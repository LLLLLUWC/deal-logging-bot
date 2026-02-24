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

        # Include forward origin in text so LLM knows who forwarded
        llm_text = combined_text
        if external_source:
            llm_text = f"[Forwarded from {external_source}]\n\n{combined_text}"

        # Download and extract PDF attachment if present
        pdf_content = await self._extract_pdf_content(group)

        # Run DealExtractor
        result = await self.deal_extractor.extract(
            text=llm_text,
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
            # Scenario 2: Router skipped but deck links were detected
            deck_links = [
                lnk for lnk in result.detected_links if getattr(lnk, 'is_deck', False)
            ]
            if deck_links:
                await self._send_review_skipped_with_decks(
                    group, result.skipped_reason, deck_links
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

        # Scenario 4: Low confidence
        low_confidence = (
            result.router_confidence > 0 and result.router_confidence < 0.6
        )

        # Create Notion entries for each extracted deal
        created_deals = []
        failed_deals = []

        for deal in result.deals:
            try:
                # Forward metadata is authoritative for forwarded messages;
                # for non-forwarded messages, use LLM's per-deal extraction
                if external_source:
                    deal_external_source = external_source
                else:
                    deal_external_source = deal.external_source

                # Scenario 5: Missing company name or tags (for Telegram report only)
                missing_info = (
                    deal.company_name == "Unknown" or not deal.tags
                )

                # Determine review status (Telegram report only, NOT written to Notion)
                review_status = None
                if low_confidence:
                    review_status = "Low Confidence"
                elif missing_info or result.needs_review:
                    review_status = "Needs Review"

                entry = DealEntry(
                    title=deal.company_name,
                    tags=deal.tags,
                    intro=deal.intro,
                    detailed_content=deal.detailed_content,
                    op_source=sender,
                    external_source=deal_external_source,
                    deck_url=deal.deck_url,
                    raise_amount=deal.raise_amount,
                    valuation=deal.valuation,
                )

                notion_result = self.notion_client.create_deal_with_retry(entry)

                if notion_result.success:
                    logger.info(
                        f"Created Notion entry: {deal.company_name} -> "
                        f"{notion_result.page_url}"
                    )

                    # Add intro + original pitch text as comments
                    if notion_result.page_id:
                        try:
                            # Write intro as first comment (appears in Notion comments column)
                            if deal.intro:
                                self.notion_client.add_comment(
                                    notion_result.page_id, deal.intro
                                )
                                logger.info(
                                    f"Added intro as comment to {deal.company_name}"
                                )

                            # Write original pitch text as second comment
                            if combined_text:
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

                    # Per-deal deck extraction status
                    deal_deck_extracted = False
                    if deal.deck_url:
                        for fd in result.fetched_decks:
                            if fd.url == deal.deck_url and fd.success:
                                deal_deck_extracted = True
                                break

                    created_deals.append({
                        "company_name": deal.company_name,
                        "intro": deal.intro,
                        "tags": deal.tags,
                        "deck_url": deal.deck_url,
                        "page_url": notion_result.page_url,
                        "page_id": notion_result.page_id,
                        "deck_extracted": deal_deck_extracted,
                        "external_source": deal_external_source,
                        "status": review_status,
                        "raise_amount": deal.raise_amount,
                        "valuation": deal.valuation,
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
                group, created_deals, result, low_confidence
            )

            # Scenario 3: Some decks failed to extract
            if failed_deals:
                failed_names = [d["company_name"] for d in failed_deals]
                await self._send_partial_failure_warning(group, failed_names, result)

        elif failed_deals:
            # Scenario 6: All Notion entries failed
            await self._send_notion_failure_with_context(
                group, failed_deals, combined_text, sender
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
        result,
        low_confidence: bool = False,
    ) -> None:
        """Send enhanced confirmation with pipeline details.

        Args:
            group: The processed MessageGroup.
            deals: List of created deal dicts.
            result: Full ExtractionResult.
            low_confidence: Whether router confidence is low.
        """
        if not group.messages or not deals:
            return

        try:
            first_msg = group.messages[0].message

            if len(deals) == 1:
                confirmation = self._format_single_deal_report(
                    deals[0], result, low_confidence
                )
            else:
                confirmation = self._format_multi_deal_report(
                    deals, result, low_confidence
                )

            # Cap at 4000 chars for Telegram limit
            if len(confirmation) > 4000:
                confirmation = confirmation[:3997] + "..."

            await self._send_telegram_message_with_retry(
                first_msg, confirmation, max_retries=3
            )

        except Exception as e:
            logger.error(f"Failed to send confirmation: {e}")

    def _format_single_deal_report(
        self,
        deal: dict,
        result,
        low_confidence: bool,
    ) -> str:
        """Format simplified report for a single deal.

        Args:
            deal: Created deal dict.
            result: Full ExtractionResult.
            low_confidence: Whether router confidence is low.

        Returns:
            Formatted report string.
        """
        # Header with name and raise/valuation
        name_line = f"Name: {deal['company_name']}"
        funding_parts = []
        if deal.get("raise_amount"):
            funding_parts.append(f"Raise {deal['raise_amount']}")
        if deal.get("valuation"):
            funding_parts.append(f"Val {deal['valuation']}")
        if funding_parts:
            name_line += f" | {' / '.join(funding_parts)}"

        lines = ["Deal Logged", name_line]

        if deal.get("intro"):
            lines.append(deal["intro"])

        if deal.get("tags"):
            lines.append("")
            lines.append(f"Tags: {', '.join(deal['tags'])}")

        if deal.get("deck_extracted"):
            lines.append("Deck: Extracted")
        elif deal.get("deck_url"):
            lines.append("Deck: Link saved (content not extracted)")

        # Failed deck warnings
        failed_decks = [d for d in result.fetched_decks if not d.success]
        if failed_decks:
            lines.append("")
            for fd in failed_decks[:3]:
                url_short = fd.url[:50] + "..." if len(fd.url) > 50 else fd.url
                error_msg = fd.error or "unknown error"
                lines.append(f"<b>DECK FAILED:</b> {url_short}")
                lines.append(f"  {error_msg}")

        # Status section
        status_parts = []
        if result.decks_detected > 0:
            status_parts.append(
                f"Deck: {result.decks_fetched}/{result.decks_detected} extracted"
            )
        if low_confidence:
            status_parts.append("<b>Low Confidence</b>")
        if deal.get("status") == "Needs Review":
            status_parts.append("<b>Needs Review</b>")

        if status_parts:
            lines.append("")
            lines.append("--Status--")
            lines.extend(status_parts)

        # Notion link
        if deal.get("page_url"):
            lines.append("")
            lines.append(deal["page_url"])

        return "\n".join(lines)

    def _format_multi_deal_report(
        self,
        deals: list[dict],
        result,
        low_confidence: bool,
    ) -> str:
        """Format simplified report for multiple deals.

        Args:
            deals: List of created deal dicts.
            result: Full ExtractionResult.
            low_confidence: Whether router confidence is low.

        Returns:
            Formatted report string.
        """
        lines = [f"{len(deals)} Deals Logged"]

        for i, deal in enumerate(deals, 1):
            # Name with raise/valuation
            name_part = deal['company_name']
            funding_parts = []
            if deal.get("raise_amount"):
                funding_parts.append(f"Raise {deal['raise_amount']}")
            if deal.get("valuation"):
                funding_parts.append(f"Val {deal['valuation']}")
            if funding_parts:
                name_part += f" | {' / '.join(funding_parts)}"

            tags_str = ""
            if deal.get("tags"):
                tags_str = f" ({', '.join(deal['tags'][:2])})"

            lines.append(f"\n{i}. {name_part}{tags_str}")

            if deal.get("deck_extracted"):
                lines.append("   Deck: Extracted")
            elif deal.get("deck_url"):
                lines.append("   Deck: Link saved")

            if deal.get("page_url"):
                lines.append(f"   {deal['page_url']}")

        # Failed deck warnings
        failed_decks = [d for d in result.fetched_decks if not d.success]
        if failed_decks:
            lines.append("")
            for fd in failed_decks[:3]:
                url_short = fd.url[:50] + "..." if len(fd.url) > 50 else fd.url
                error_msg = fd.error or "unknown error"
                lines.append(f"<b>DECK FAILED:</b> {url_short}")
                lines.append(f"  {error_msg}")
            if len(failed_decks) > 3:
                lines.append(f"  +{len(failed_decks) - 3} more failed")

        # Status section
        status_parts = []
        if result.decks_detected > 0:
            status_parts.append(
                f"Deck: {result.decks_fetched}/{result.decks_detected} extracted"
            )
        if low_confidence:
            status_parts.append("<b>Low Confidence</b>")

        if status_parts:
            lines.append("")
            lines.append("--Status--")
            lines.extend(status_parts)

        return "\n".join(lines)

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
                    parse_mode="HTML",
                    disable_web_page_preview=True,
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
            await first_msg.reply_text(
                f"<b>Failed to log deal:</b> {error_short}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Failed to send error notification: {e}")

    async def _send_partial_failure_warning(
        self,
        group: MessageGroup,
        failed_company_names: list[str],
        result=None,
    ) -> None:
        """Send a warning about partially failed deal logging.

        Args:
            group: The MessageGroup being processed.
            failed_company_names: List of company names that failed.
            result: Optional ExtractionResult for deck failure details.
        """
        if not group.messages or not failed_company_names:
            return

        try:
            first_msg = group.messages[0].message
            names_str = ", ".join(failed_company_names[:5])
            if len(failed_company_names) > 5:
                names_str += f" (+{len(failed_company_names) - 5} more)"

            lines = [f"<b>Some deals failed to log:</b> {names_str}"]

            # Add deck failure details if available
            if result and result.fetched_decks:
                failed_decks = [d for d in result.fetched_decks if not d.success]
                for deck in failed_decks[:3]:
                    url_short = deck.url[:40] + "..." if len(deck.url) > 40 else deck.url
                    lines.append(f"  Deck failed: {url_short} ({deck.error or 'unknown'})")

            await first_msg.reply_text(
                "\n".join(lines), parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to send partial failure warning: {e}")

    async def _send_review_skipped_with_decks(
        self,
        group: MessageGroup,
        reason: str,
        deck_links: list,
    ) -> None:
        """Send notification when router skipped but deck links were detected.

        Scenario 2: Message classified as non-deal but contains deck links,
        suggesting it might actually be a deal worth reviewing.

        Args:
            group: The MessageGroup being processed.
            reason: Router's skip reason.
            deck_links: List of DetectedLink objects that are decks.
        """
        if not group.messages:
            return

        try:
            first_msg = group.messages[0].message
            lines = [
                "<b>Skipped (may need review)</b>",
                f"Reason: {reason}",
                "",
                "Deck link(s) detected:",
            ]

            for link in deck_links[:3]:
                lt = getattr(link, 'link_type', None)
                type_label = lt.value if lt and hasattr(lt, 'value') else "link"
                url_display = link.url
                if len(url_display) > 50:
                    url_display = url_display[:47] + "..."
                lines.append(f"  {type_label}: {url_display}")

            if len(deck_links) > 3:
                lines.append(f"  +{len(deck_links) - 3} more")

            await first_msg.reply_text(
                "\n".join(lines), parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to send skipped-with-decks notification: {e}")

    async def _send_notion_failure_with_context(
        self,
        group: MessageGroup,
        failed_deals: list[dict],
        original_text: str,
        sender: str,
    ) -> None:
        """Send notification when all Notion entries failed, with original message context.

        Scenario 6: Deals were extracted successfully but all Notion API calls failed.

        Args:
            group: The MessageGroup being processed.
            failed_deals: List of failed deal dicts with company_name and error.
            original_text: Original combined message text.
            sender: Original message sender.
        """
        if not group.messages:
            return

        try:
            first_msg = group.messages[0].message
            lines = ["<b>Failed to create Notion entries</b>"]

            for d in failed_deals[:3]:
                lines.append(f"  {d['company_name']}: {d.get('error', 'unknown')[:100]}")

            if len(failed_deals) > 3:
                lines.append(f"  +{len(failed_deals) - 3} more")

            # Append original text excerpt for manual logging
            lines.append("")
            lines.append(f"From: {sender}")
            text_preview = original_text[:500] if len(original_text) > 500 else original_text
            lines.append(f"Message: {text_preview}")

            msg = "\n".join(lines)
            if len(msg) > 4000:
                msg = msg[:3997] + "..."

            await first_msg.reply_text(
                msg, parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to send notion-failure notification: {e}")

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
            text = "<b>Needs manual review</b>\nDeck link(s) detected but content could not be extracted."
            if reasons:
                text += f"\nReason: {reasons[:300]}"
            await first_msg.reply_text(
                text, parse_mode="HTML",
                disable_web_page_preview=True,
            )
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
