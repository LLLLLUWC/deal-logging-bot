#!/usr/bin/env python3
"""Deal Logging Bot - Telegram to Notion automation.

This bot monitors Telegram group messages and automatically logs deals
to a Notion database.

Usage:
    python -m bot.main
    # or
    python bot/main.py
"""

import asyncio
import logging
import signal
import sys

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler as TelegramMessageHandler,
    filters,
)

from deal_extractor import DealExtractor

from .config import Config
from .handlers.message_handler import MessageHandler
from .notion.client import NotionClient
from .utils.grouping import MessageGrouper


# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class DealLoggingBot:
    """Main bot application."""

    def __init__(self, config: Config):
        """Initialize the bot.

        Args:
            config: Bot configuration.
        """
        self.config = config
        self.application = None
        self.deal_extractor = None
        self._shutdown_event = asyncio.Event()
        self._cleanup_task = None

    def setup(self) -> None:
        """Set up bot components."""
        logger.info("Setting up bot components...")

        # Initialize DealExtractor (replaces all individual extractors)
        self.deal_extractor = DealExtractor(
            llm_api_key=self.config.kimi_api_key,
            llm_model=self.config.kimi_model,
            docsend_email=self.config.docsend_email,
            docsend_password=self.config.docsend_password,
            pdf2llm_path=self.config.pdf_extractor_path,
            temp_dir=self.config.temp_dir / "deal_extractor",
            cleanup_after_extract=self.config.cleanup_after_extract,
            cleanup_max_age_minutes=self.config.cleanup_max_age_minutes,
        )
        logger.info(f"DealExtractor initialized: model={self.config.kimi_model}")

        # Initialize Notion client
        notion_client = NotionClient(
            api_key=self.config.notion_api_key,
            database_id=self.config.notion_database_id,
        )

        # Validate Notion connection
        success, message = notion_client.validate_connection()
        if success:
            logger.info(f"Notion: {message}")
        else:
            logger.error(f"Notion connection failed: {message}")
            sys.exit(1)

        # Initialize Telegram application
        builder = Application.builder().token(self.config.telegram_bot_token)

        # Add proxy if configured (required in China)
        if self.config.telegram_proxy:
            from telegram.request import HTTPXRequest
            logger.info(f"Using Telegram proxy: {self.config.telegram_proxy}")
            request = HTTPXRequest(proxy=self.config.telegram_proxy)
            builder = builder.request(request)

        self.application = builder.build()

        # Initialize message grouper
        grouper = MessageGrouper(
            timeout_seconds=self.config.message_grouping_timeout,
            max_group_size=10,
            quick_timeout=3.0,
        )

        # Initialize message handler
        message_handler = MessageHandler(
            deal_extractor=self.deal_extractor,
            notion_client=notion_client,
            grouper=grouper,
            target_group_id=self.config.telegram_group_id,
            bot=self.application.bot,
            temp_dir=self.config.temp_dir,
        )

        # Register handlers
        self.application.add_handler(
            TelegramMessageHandler(
                filters.ALL & ~filters.COMMAND,
                message_handler.handle_message,
            )
        )

        self.application.add_handler(
            CallbackQueryHandler(message_handler.handle_callback_query)
        )

        logger.info("Bot setup complete")
        logger.info(f"Monitoring Telegram group ID: {self.config.telegram_group_id}")
        logger.info(f"Message grouping timeout: {self.config.message_grouping_timeout}s")
        logger.info(
            f"Cleanup: immediate={self.config.cleanup_after_extract}, "
            f"max_age={self.config.cleanup_max_age_minutes}min, "
            f"interval={self.config.cleanup_interval_minutes}min"
        )

    async def run(self) -> None:
        """Run the bot."""
        logger.info("Starting bot...")

        await self.application.initialize()
        await self.application.start()

        await self.application.updater.start_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )

        # Start periodic cleanup task
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        logger.info("Bot is running. Press Ctrl+C to stop.")

        await self._shutdown_event.wait()

        # Cleanup
        logger.info("Shutting down...")

        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

    async def _periodic_cleanup(self) -> None:
        """Periodically clean up old temporary files."""
        interval_seconds = self.config.cleanup_interval_minutes * 60

        # Wait a bit before first cleanup
        await asyncio.sleep(60)

        while True:
            try:
                # Run cleanup
                deleted = self.deal_extractor.cleanup_old_files()

                # Log temp directory stats
                total_bytes, file_count = self.deal_extractor.get_temp_dir_size()
                total_mb = total_bytes / (1024 * 1024)

                if deleted > 0 or file_count > 0:
                    logger.info(
                        f"Periodic cleanup: deleted {deleted} files, "
                        f"temp dir: {file_count} files, {total_mb:.1f} MB"
                    )

            except Exception as e:
                logger.error(f"Periodic cleanup error: {e}")

            # Wait for next interval
            await asyncio.sleep(interval_seconds)

    def request_shutdown(self) -> None:
        """Request bot shutdown."""
        self._shutdown_event.set()


def setup_signal_handlers(bot: DealLoggingBot) -> None:
    """Set up signal handlers for graceful shutdown.

    Args:
        bot: Bot instance to shut down.
    """
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        bot.request_shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def main() -> None:
    """Main entry point."""
    try:
        config = Config.load()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        logger.error("Please check your .env file")
        sys.exit(1)

    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)

    bot = DealLoggingBot(config)
    bot.setup()

    setup_signal_handlers(bot)

    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
