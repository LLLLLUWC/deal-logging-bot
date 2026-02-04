"""Message grouping logic for multi-message deals."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from telegram import Message


logger = logging.getLogger(__name__)


@dataclass
class BufferedMessage:
    """A message waiting in the buffer for grouping."""

    message: Message
    message_id: int
    chat_id: int
    sender_name: str
    text: str
    timestamp: datetime
    reply_to_message_id: Optional[int] = None
    has_document: bool = False
    document_name: Optional[str] = None
    is_forwarded: bool = False

    @classmethod
    def from_telegram_message(cls, message: Message) -> "BufferedMessage":
        """Create a BufferedMessage from a Telegram message."""
        sender_name = ""
        if message.from_user:
            sender_name = message.from_user.full_name or message.from_user.username or ""

        text = message.text or message.caption or ""

        # Extract URLs from hyperlink entities (text_link type)
        # This is CRITICAL - Telegram rich text stores URLs in entities, not in text!
        entities = message.entities or message.caption_entities or []
        hyperlink_urls = []
        for entity in entities:
            if entity.type == "text_link" and entity.url:
                hyperlink_urls.append(entity.url)

        # Append hyperlink URLs to text so link detector can find them
        if hyperlink_urls:
            text = text + "\n\n" + "\n".join(hyperlink_urls)
            logger.info(f"Extracted {len(hyperlink_urls)} hyperlink URL(s) from rich text entities")

        reply_to_id = None
        if message.reply_to_message:
            reply_to_id = message.reply_to_message.message_id

        has_document = message.document is not None
        document_name = message.document.file_name if message.document else None

        # Check if forwarded
        is_forwarded = (
            hasattr(message, 'forward_origin') and message.forward_origin is not None
        ) or (
            hasattr(message, 'forward_from') and message.forward_from is not None
        ) or (
            hasattr(message, 'forward_sender_name') and message.forward_sender_name is not None
        )

        return cls(
            message=message,
            message_id=message.message_id,
            chat_id=message.chat_id,
            sender_name=sender_name,
            text=text,
            timestamp=message.date or datetime.now(),
            reply_to_message_id=reply_to_id,
            has_document=has_document,
            document_name=document_name,
            is_forwarded=is_forwarded,
        )


@dataclass
class MessageGroup:
    """A group of related messages representing a single deal."""

    messages: list[BufferedMessage] = field(default_factory=list)
    primary_sender: str = ""
    created_at: datetime = field(default_factory=datetime.now)

    def add_message(self, msg: BufferedMessage) -> None:
        """Add a message to the group."""
        self.messages.append(msg)
        if not self.primary_sender:
            self.primary_sender = msg.sender_name

    def get_combined_text(self) -> str:
        """Get combined text from all messages."""
        texts = [msg.text for msg in self.messages if msg.text]
        return "\n\n".join(texts)

    def get_all_message_ids(self) -> list[int]:
        """Get all message IDs in the group."""
        return [msg.message_id for msg in self.messages]

    def has_document(self) -> bool:
        """Check if any message in the group has a document."""
        return any(msg.has_document for msg in self.messages)

    def get_document_message(self) -> Optional[BufferedMessage]:
        """Get the first message with a document."""
        for msg in self.messages:
            if msg.has_document:
                return msg
        return None


class MessageGrouper:
    """Groups related messages together before processing.

    Optimized for handling bulk forwarded messages efficiently.
    Uses parallel processing - race conditions are avoided via unique identifiers
    in file paths throughout the processing pipeline.
    """

    def __init__(
        self,
        timeout_seconds: int = 30,
        max_group_size: int = 10,
        quick_timeout: float = 3.0,
    ):
        """Initialize the message grouper.

        Args:
            timeout_seconds: Maximum time to wait before finalizing a group.
            max_group_size: Maximum messages per group before auto-finalize.
            quick_timeout: Short timeout for rapid message sequences.
        """
        self.timeout_seconds = timeout_seconds
        self.max_group_size = max_group_size
        self.quick_timeout = quick_timeout
        self._pending_groups: dict[int, MessageGroup] = {}  # chat_id -> current group
        self._lock = asyncio.Lock()
        self._timers: dict[int, asyncio.Task] = {}
        self._last_message_time: dict[int, datetime] = {}
        self._active_tasks: set[asyncio.Task] = set()  # Track active processing tasks

    async def add_message(
        self,
        message: Message,
        on_group_ready: callable,
    ) -> None:
        """Add a message to the buffer and potentially trigger grouping."""
        async with self._lock:
            buffered = BufferedMessage.from_telegram_message(message)
            chat_id = buffered.chat_id
            now = datetime.now()

            current_group = self._pending_groups.get(chat_id)
            last_time = self._last_message_time.get(chat_id)

            # Decide if we should start a new group
            should_new_group = self._should_start_new_group(
                buffered, current_group, last_time, now
            )

            if should_new_group:
                # Finalize existing group first
                if current_group and current_group.messages:
                    logger.info(f"Finalizing group with {len(current_group.messages)} messages (new group starting)")
                    await self._finalize_group_unlocked(chat_id, on_group_ready)

                # Create new group
                self._pending_groups[chat_id] = MessageGroup()
                current_group = self._pending_groups[chat_id]

            # Add message to group
            current_group.add_message(buffered)
            self._last_message_time[chat_id] = now

            logger.info(f"Added message to group (now {len(current_group.messages)} messages)")

            # Check if group should be immediately processed
            if self._should_process_immediately(current_group, buffered):
                logger.info("Processing group immediately (has document/max size)")
                await self._finalize_group_unlocked(chat_id, on_group_ready)
            else:
                # Start/reset timer with dynamic timeout
                timeout = self._calculate_timeout(current_group)
                await self._reset_timer(chat_id, on_group_ready, timeout)

    def _should_start_new_group(
        self,
        msg: BufferedMessage,
        current_group: Optional[MessageGroup],
        last_time: Optional[datetime],
        now: datetime,
    ) -> bool:
        """Determine if a new group should be started."""
        # No existing group
        if not current_group or not current_group.messages:
            return True

        # Different sender (not a reply)
        if msg.sender_name != current_group.primary_sender:
            # Unless it's a reply to the group
            if msg.reply_to_message_id not in current_group.get_all_message_ids():
                return True

        # Long gap since last message (more than 2 minutes)
        if last_time:
            gap = (now - last_time).total_seconds()
            if gap > 120:
                return True

        # Max group size reached
        if len(current_group.messages) >= self.max_group_size:
            return True

        return False

    def _should_process_immediately(
        self, group: MessageGroup, latest_msg: BufferedMessage
    ) -> bool:
        """Check if group should be processed immediately without waiting."""
        # Process immediately if we hit max size
        if len(group.messages) >= self.max_group_size:
            return True

        # Process immediately if there's a PDF (standalone deal)
        if latest_msg.has_document and latest_msg.document_name:
            if latest_msg.document_name.lower().endswith('.pdf'):
                # Only if it's the first/only message or single message group
                if len(group.messages) == 1:
                    return True

        return False

    def _calculate_timeout(self, group: MessageGroup) -> float:
        """Calculate appropriate timeout based on group state."""
        # Shorter timeout for groups with content
        if group.has_document():
            return self.quick_timeout

        # Use quick timeout for single messages (likely standalone)
        if len(group.messages) == 1:
            return self.quick_timeout

        # Standard timeout for multi-message groups
        return min(self.quick_timeout * 2, self.timeout_seconds)

    async def _reset_timer(
        self, chat_id: int, on_group_ready: callable, timeout: float
    ) -> None:
        """Reset the grouping timer for a chat."""
        # Cancel existing timer
        if chat_id in self._timers:
            self._timers[chat_id].cancel()
            try:
                await self._timers[chat_id]
            except asyncio.CancelledError:
                pass

        # Start new timer
        self._timers[chat_id] = asyncio.create_task(
            self._timer_callback(chat_id, on_group_ready, timeout)
        )

    async def _timer_callback(
        self, chat_id: int, on_group_ready: callable, timeout: float
    ) -> None:
        """Timer callback that finalizes the group after timeout."""
        await asyncio.sleep(timeout)

        async with self._lock:
            group = self._pending_groups.get(chat_id)
            if group and group.messages:
                logger.info(f"Timer expired, finalizing group with {len(group.messages)} messages")
            await self._finalize_group_unlocked(chat_id, on_group_ready)

    async def _finalize_group_unlocked(
        self, chat_id: int, on_group_ready: callable
    ) -> None:
        """Finalize a message group (must hold lock).

        Groups are processed in parallel - race conditions are avoided via
        unique identifiers in file paths throughout the processing pipeline.
        """
        group = self._pending_groups.pop(chat_id, None)
        self._last_message_time.pop(chat_id, None)

        # Cancel any pending timer
        if chat_id in self._timers:
            self._timers[chat_id].cancel()
            self._timers.pop(chat_id, None)

        if group and group.messages:
            # Process in parallel - unique IDs prevent file collisions
            task = asyncio.create_task(self._process_group_wrapper(group, on_group_ready))
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
            logger.info(f"Started parallel processing for group (active tasks: {len(self._active_tasks)})")

    async def _process_group_wrapper(self, group: MessageGroup, callback: callable) -> None:
        """Wrapper to handle exceptions in parallel group processing."""
        try:
            await callback(group)
        except Exception as e:
            logger.exception(f"Error processing group in parallel: {e}")

    async def flush_all(self, on_group_ready: callable) -> None:
        """Flush all pending groups immediately."""
        async with self._lock:
            chat_ids = list(self._pending_groups.keys())
            for chat_id in chat_ids:
                await self._finalize_group_unlocked(chat_id, on_group_ready)

    async def stop(self) -> None:
        """Stop all processing and clean up resources."""
        # Cancel any pending timers
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()

        # Cancel active processing tasks
        for task in self._active_tasks:
            task.cancel()

        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
            logger.info(f"Cancelled {len(self._active_tasks)} active tasks")

        self._active_tasks.clear()

    async def wait_for_completion(self) -> None:
        """Wait for all active processing tasks to finish."""
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
            logger.info("All parallel tasks completed")
