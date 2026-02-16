"""Telegram message handling with DealExtractor integration."""

import logging
from pathlib import Path
from typing import Optional

from telegram import Bot, Message, Update
from telegram.ext import ContextTypes

from deal_extractor import DealExtractor

from ..notion.client import DealEntry, NotionClient
from ..utils.grouping import MessageGroup, MessageGrouper


logger = logging.getLogger(__name__)


class MessageHandler:
    """Handles incoming Telegram messages and processes deals."""

    def __init__(
        self,
        deal_extractor: DealExtractor,
        notion_client: NotionClient,
        grouper: MessageGrouper,
        target_group_id: int,
        bot: Bot,
        temp_dir: Path,
    ):
        """Initialize the message handler.

        Args:
            deal_extractor: DealExtractor for extracting deal information.
            notion_client: NotionClient for creating Notion entries.
            grouper: MessageGrouper for grouping related messages.
            target_group_id: Telegram group ID to monitor.
            bot: Telegram Bot instance.
            temp_dir: Temporary directory for file downloads.
        """
        self.deal_extractor = deal_extractor
        self.notion_client = notion_client
        self.grouper = grouper
        self.target_group_id = target_group_id
        self.bot = bot
        self.temp_dir = temp_dir

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle an incoming message.

        Args:
            update: Telegram Update object.
            context: Callback context.
        """
        message = update.effective_message
        if not message:
            return

        chat_id = message.chat_id
        sender = self._get_sender_name(message)
        text_preview = (message.text or message.caption or "")[:50]
        logger.info(f"[Chat {chat_id}] Message from {sender}: {text_preview}...")

        if chat_id != self.target_group_id:
            logger.debug(f"Skipping: chat {chat_id} != target {self.target_group_id}")
            return

        should_process, reason = self._should_process_with_reason(message)
        if not should_process:
            logger.info(f"Message filtered: {reason}")
            return

        logger.info("Message accepted for processing")

        await self.grouper.add_message(
            message,
            on_group_ready=self._on_group_ready,
        )

    async def _on_group_ready(self, group: MessageGroup) -> None:
        """Callback when a message group is ready for processing.

        Args:
            group: Finalized MessageGroup.
        """
        logger.info(f"Processing group with {len(group.messages)} message(s)")

        processing_msg = await self._send_processing_notification(group)

        try:
            await self._process_group(group, processing_msg)
        except Exception as e:
            logger.exception(f"Error processing message group: {e}")
            if processing_msg:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
            await self._send_error_notification(group, str(e))

    async def _process_group(self, group: MessageGroup, processing_msg) -> None:
        """Process message group using DealExtractor.

        Args:
            group: MessageGroup to process.
            processing_msg: Processing notification message.
        """
        # Extract message content
        combined_text = group.get_combined_text()
        sender = group.primary_sender
        external_source = self._extract_external_source_from_group(group)

        # Download and extract PDF attachment if present
        pdf_content = await self._extract_pdf_content(group)

        # Run DealExtractor
        result = await self.deal_extractor.extract(
            text=combined_text,
            sender=sender,
            pdf_content=pdf_content,
        )

        # Delete processing message
        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception:
                pass

        # Handle skipped (not a deal)
        if result.skipped_reason:
            logger.info(
                f"Skipped - {result.skipped_reason} "
                f"(router tokens: {result.router_tokens})"
            )
            return

        # Handle error
        if result.error:
            await self._send_error_notification(group, result.error)
            return

        # Handle no deals found
        if not result.deals:
            logger.info(
                f"No deals extracted "
                f"(tokens: router={result.router_tokens}, "
                f"extractor={result.extractor_tokens})"
            )
            if result.needs_review:
                reasons = "; ".join(result.review_reasons[:3])
                await self._send_needs_review_notification(group, reasons)
            return

        # Create Notion entries for each extracted deal
        created_deals = []
        failed_deals = []

        for deal in result.deals:
            try:
                # Use per-deal external source from LLM, fallback to group-level
                deal_external_source = deal.external_source or external_source

                entry = DealEntry(
                    title=deal.company_name,
                    tags=deal.tags,
                    intro=deal.intro,
                    detailed_content=deal.detailed_content,
                    op_source=sender,
                    external_source=deal_external_source,
                    deck_url=deal.deck_url,
                    status="Needs Review" if result.needs_review else None,
                )

                notion_result = self.notion_client.create_deal_with_retry(entry)

                if notion_result.success:
                    logger.info(
                        f"Created Notion entry: {deal.company_name} -> "
                        f"{notion_result.page_url}"
                    )

                    # Add original pitch text as comment
                    if notion_result.page_id and combined_text:
                        try:
                            prefix = f"Original message from {sender}:\n\n"
                            pitch_comment = f"{prefix}{combined_text}"
                            comments_added = self.notion_client.add_comment_multipart(
                                notion_result.page_id, pitch_comment
                            )
                            if comments_added > 0:
                                logger.info(
                                    f"Added pitch text as {comments_added} comment(s) "
                                    f"to {deal.company_name}"
                                )
                        except Exception as comment_error:
                            logger.warning(
                                f"Comment failed for {deal.company_name}: "
                                f"{comment_error}"
                            )

                    created_deals.append({
                        "company_name": deal.company_name,
                        "intro": deal.intro,
                        "tags": deal.tags,
                        "deck_url": deal.deck_url,
                        "page_url": notion_result.page_url,
                        "page_id": notion_result.page_id,
                        "deck_extracted": result.decks_fetched > 0,
                    })
                else:
                    logger.error(
                        f"Failed to create Notion entry for {deal.company_name}: "
                        f"{notion_result.error}"
                    )
                    failed_deals.append({
                        "company_name": deal.company_name,
                        "error": notion_result.error,
                    })

            except Exception as e:
                logger.exception(
                    f"Error creating Notion entry for {deal.company_name}: {e}"
                )
                failed_deals.append({
                    "company_name": deal.company_name,
                    "error": str(e),
                })

        # Send confirmation
        if created_deals:
            await self._send_confirmation(
                group,
                created_deals,
                result.router_tokens,
                result.extractor_tokens,
                needs_review=result.needs_review,
            )

            if failed_deals:
                failed_names = [d["company_name"] for d in failed_deals]
                await self._send_partial_failure_warning(group, failed_names)

        elif failed_deals:
            error_details = "; ".join(
                f"{d['company_name']}: {d['error']}" for d in failed_deals
            )
            await self._send_error_notification(
                group, f"Failed to create Notion entries: {error_details[:300]}"
            )

    async def _extract_pdf_content(self, group: MessageGroup) -> Optional[str]:
        """Extract PDF attachment content from message group.

        Args:
            group: MessageGroup to check for PDF attachments.

        Returns:
            Extracted PDF text content, or None if no PDF.
        """
        doc_message = group.get_document_message()
        if not doc_message or not doc_message.document_name:
            return None

        if not doc_message.document_name.lower().endswith(".pdf"):
            return None

        try:
            # Download PDF from Telegram
            document = doc_message.message.document
            file = await self.bot.get_file(document.file_id)

            # Generate unique path
            import hashlib
            file_hash = hashlib.md5(
                f"{document.file_id}_{doc_message.message_id}".encode()
            ).hexdigest()[:12]

            pdf_path = self.temp_dir / "pdf_downloads" / f"{file_hash}.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)

            await file.download_to_drive(pdf_path)
            logger.info(f"Downloaded PDF: {doc_message.document_name} -> {pdf_path}")

            # Extract text using DealExtractor's PDF extractor
            result = self.deal_extractor.pdf_extractor.extract(pdf_path)

            # Clean up downloaded file
            try:
                pdf_path.unlink()
            except Exception:
                pass

            if result.success and result.text_content:
                logger.info(f"Extracted {len(result.text_content)} chars from PDF")
                return f"File: {doc_message.document_name}\n\n{result.text_content}"

        except Exception as e:
            logger.error(f"Error extracting PDF: {e}")

        return None

    def _extract_external_source_from_group(
        self, group: MessageGroup
    ) -> Optional[str]:
        """Extract external source from message group.

        Args:
            group: MessageGroup to analyze.

        Returns:
            External source name or None.
        """
        for msg in group.messages:
            telegram_msg = msg.message

            # Check for forward_origin (python-telegram-bot v20+)
            forward_origin = getattr(telegram_msg, 'forward_origin', None)
            if forward_origin:
                origin_type = getattr(forward_origin, 'type', None)

                if origin_type == 'user':
                    sender_user = getattr(forward_origin, 'sender_user', None)
                    if sender_user:
                        return sender_user.full_name or sender_user.username

                elif origin_type == 'hidden_user':
                    sender_name = getattr(forward_origin, 'sender_user_name', None)
                    if sender_name:
                        return sender_name

                elif origin_type == 'chat':
                    sender_chat = getattr(forward_origin, 'sender_chat', None)
                    if sender_chat:
                        return sender_chat.title

                elif origin_type == 'channel':
                    chat = getattr(forward_origin, 'chat', None)
                    if chat:
                        return chat.title

            # Fallback: check legacy attributes
            if hasattr(telegram_msg, 'forward_from') and telegram_msg.forward_from:
                return (
                    telegram_msg.forward_from.full_name
                    or telegram_msg.forward_from.username
                )

            if (
                hasattr(telegram_msg, 'forward_sender_name')
                and telegram_msg.forward_sender_name
            ):
                return telegram_msg.forward_sender_name

            if (
                hasattr(telegram_msg, 'forward_from_chat')
                and telegram_msg.forward_from_chat
            ):
                return telegram_msg.forward_from_chat.title

        return None

    async def _send_processing_notification(self, group: MessageGroup):
        """Send a processing notification to the Telegram group.

        Args:
            group: The MessageGroup being processed.

        Returns:
            The sent message object, or None.
        """
        if not group.messages:
            return None

        try:
            first_msg = group.messages[0].message
            return await first_msg.reply_text("Processing deal...")
        except Exception as e:
            logger.error(f"Failed to send processing notification: {e}")
            return None

    async def _send_confirmation(
        self,
        group: MessageGroup,
        deals: list[dict],
        router_tokens: int,
        extractor_tokens: int,
        needs_review: bool = False,
    ) -> None:
        """Send confirmation for extraction results.

        Args:
            group: The processed MessageGroup.
            deals: List of created deal dicts.
            router_tokens: Tokens used by router.
            extractor_tokens: Tokens used by extractor.
            needs_review: Whether deck extraction failed and needs manual review.
        """
        if not group.messages or not deals:
            return

        try:
            first_msg = group.messages[0].message
            total_tokens = router_tokens + extractor_tokens

            if len(deals) == 1:
                deal = deals[0]
                lines = [f"**{deal['company_name']}**"]

                if deal.get("intro"):
                    lines.append(deal['intro'])

                if deal.get("tags"):
                    lines.append(f"Tags: {', '.join(deal['tags'])}")

                if deal.get("deck_extracted"):
                    lines.append("Deck: Extracted")
                elif deal.get("deck_url"):
                    lines.append("Deck: Link saved")

                if needs_review:
                    lines.append("Deck content could not be extracted, may need manual review")

                lines.append(
                    f"Tokens: {router_tokens} + {extractor_tokens} = {total_tokens}"
                )

                if deal.get("page_url"):
                    lines.append(f"\n{deal['page_url']}")

                confirmation = "\n".join(lines)
            else:
                lines = [f"Logged {len(deals)} deals:"]

                for deal in deals:
                    deal_line = f"\n- **{deal['company_name']}**"
                    if deal.get("tags"):
                        deal_line += f" ({', '.join(deal['tags'][:2])})"
                    if deal.get("page_url"):
                        deal_line += f"\n  {deal['page_url']}"
                    lines.append(deal_line)

                if needs_review:
                    lines.append(
                        "\nDeck content could not be extracted, may need manual review"
                    )

                lines.append(
                    f"\nTokens: {router_tokens} + {extractor_tokens} = {total_tokens}"
                )

                confirmation = "\n".join(lines)

            await self._send_telegram_message_with_retry(
                first_msg, confirmation, max_retries=3
            )

        except Exception as e:
            logger.error(f"Failed to send confirmation: {e}")

    async def _send_telegram_message_with_retry(
        self,
        message,
        text: str,
        max_retries: int = 3,
    ) -> bool:
        """Send a Telegram message with retry on timeout.

        Args:
            message: Message to reply to.
            text: Text to send.
            max_retries: Maximum retry attempts.

        Returns:
            True if sent successfully, False otherwise.
        """
        import asyncio

        for attempt in range(max_retries):
            try:
                await message.reply_text(
                    text,
                    disable_web_page_preview=True,
                    parse_mode="Markdown",
                )
                return True
            except Exception as e:
                error_str = str(e).lower()
                if "timed out" in error_str or "timeout" in error_str:
                    logger.warning(
                        f"Telegram send timeout (attempt {attempt + 1}/{max_retries})"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                logger.error(f"Failed to send Telegram message: {e}")
                return False
        return False

    async def _send_error_notification(
        self,
        group: MessageGroup,
        error: str,
    ) -> None:
        """Send an error notification to the Telegram group.

        Args:
            group: The MessageGroup that failed.
            error: Error message.
        """
        if not group.messages:
            return

        try:
            first_msg = group.messages[0].message
            error_short = error[:200] if len(error) > 200 else error
            await first_msg.reply_text(f"Failed to log deal: {error_short}")
        except Exception as e:
            logger.error(f"Failed to send error notification: {e}")

    async def _send_partial_failure_warning(
        self,
        group: MessageGroup,
        failed_company_names: list[str],
    ) -> None:
        """Send a warning about partially failed deal logging.

        Args:
            group: The MessageGroup being processed.
            failed_company_names: List of company names that failed.
        """
        if not group.messages or not failed_company_names:
            return

        try:
            first_msg = group.messages[0].message
            names_str = ", ".join(failed_company_names[:5])
            if len(failed_company_names) > 5:
                names_str += f" (+{len(failed_company_names) - 5} more)"

            await first_msg.reply_text(f"Some deals failed to log: {names_str}")
        except Exception as e:
            logger.error(f"Failed to send partial failure warning: {e}")

    async def _send_needs_review_notification(
        self,
        group: MessageGroup,
        reasons: str,
    ) -> None:
        """Send notification when deck links were detected but extraction failed.

        Args:
            group: The MessageGroup being processed.
            reasons: Summary of extraction failure reasons.
        """
        if not group.messages:
            return

        try:
            first_msg = group.messages[0].message
            text = "Deck link(s) detected but content could not be extracted, may need manual review."
            if reasons:
                text += f"\nReason: {reasons[:300]}"
            await first_msg.reply_text(text)
        except Exception as e:
            logger.error(f"Failed to send needs-review notification: {e}")

    def _should_process_with_reason(self, message: Message) -> tuple[bool, str]:
        """Determine if a message should be processed, with reason.

        Args:
            message: Telegram Message to check.

        Returns:
            Tuple of (should_process, reason).
        """
        # Skip messages from bots
        if message.from_user and message.from_user.is_bot:
            return False, "from bot"

        has_text = bool(message.text or message.caption)
        has_document = message.document is not None
        has_photo = bool(message.photo)

        if not has_text and not has_document and not has_photo:
            return False, "no content"

        if has_document or has_photo:
            return True, "has attachment"

        text = message.text or message.caption or ""

        if len(text) < 5:
            return False, f"too short ({len(text)} chars)"

        # Accept forwarded messages
        if hasattr(message, 'forward_origin') and message.forward_origin:
            return True, "forwarded message"
        if hasattr(message, 'forward_from') and message.forward_from:
            return True, "forwarded message"
        if hasattr(message, 'forward_sender_name') and message.forward_sender_name:
            return True, "forwarded message"

        if self._looks_like_deal(message):
            return True, "looks like deal"

        if len(text) >= 50:
            return True, "long message"

        return False, "no deal keywords found"

    def _looks_like_deal(self, message: Message) -> bool:
        """Check if a message looks like a deal.

        Args:
            message: Telegram Message to check.

        Returns:
            True if the message might be a deal.
        """
        if message.document:
            file_name = message.document.file_name or ""
            if file_name.lower().endswith(".pdf"):
                return True

        text = (message.text or message.caption or "").lower()

        deal_keywords = [
            "docsend",
            "pitch",
            "deck",
            "investment",
            "funding",
            "series",
            "seed",
            "pre-seed",
            "raise",
            "round",
            "valuation",
            "cap",
            "safe",
            "equity",
            "tokenomics",
            "whitepaper",
            "intro",
            "meet",
            "connect",
            "founder",
            "startup",
            "project",
            "protocol",
            "platform",
        ]

        for keyword in deal_keywords:
            if keyword in text:
                return True

        url_patterns = [
            "docsend.com",
            "papermark.io",
            "papermark.com",
            ".pdf",
            "notion.so",
            "pitch.com",
            "docs.google.com",
            "loom.com",
        ]

        for pattern in url_patterns:
            if pattern in text:
                return True

        is_forwarded = (
            (hasattr(message, 'forward_origin') and message.forward_origin)
            or (hasattr(message, 'forward_from') and message.forward_from)
            or (hasattr(message, 'forward_sender_name') and message.forward_sender_name)
        )

        if is_forwarded:
            if len(text) >= 100:
                return True
            if "http" in text:
                return True

        return False

    def _get_sender_name(self, message: Message) -> str:
        """Get the sender name from a message.

        Args:
            message: Telegram Message.

        Returns:
            Sender name string.
        """
        if message.from_user:
            return (
                message.from_user.full_name
                or message.from_user.username
                or str(message.from_user.id)
            )
        return "Unknown"

    async def handle_callback_query(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle callback queries (button presses).

        Args:
            update: Telegram Update object.
            context: Callback context.
        """
        query = update.callback_query
        if not query:
            return

        await query.answer()

        data = query.data or ""

        if data.startswith("delete:"):
            page_id = data.replace("delete:", "")
            await self._handle_delete_request(query, page_id)

    async def _handle_delete_request(self, query, page_id: str) -> None:
        """Handle a request to delete a Notion entry.

        Args:
            query: Telegram CallbackQuery.
            page_id: Notion page ID to delete.
        """
        try:
            result = self.notion_client.archive_deal(page_id)

            if result.success:
                await query.edit_message_text("Deal entry deleted.")
            else:
                await query.edit_message_text(f"Failed to delete: {result.error}")

        except Exception as e:
            logger.error(f"Error handling delete request: {e}")
            await query.edit_message_text(f"Error: {str(e)}")
