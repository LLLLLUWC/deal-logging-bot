"""Configuration management for the Deal Logging Bot."""

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv


class DocSendExtractionMode(str, Enum):
    """DocSend extraction mode options."""

    AUTO = "auto"  # Use Playwright with cookie persistence
    PLAYWRIGHT = "playwright"  # Playwright browser automation


@dataclass
class Config:
    """Bot configuration loaded from environment variables."""

    # Telegram
    telegram_bot_token: str
    telegram_group_id: int

    # Notion
    notion_api_key: str
    notion_database_id: str

    # Kimi (Moonshot AI)
    kimi_api_key: str
    kimi_model: str

    # DocSend
    docsend_email: str
    docsend_password: Optional[str]

    # DocSend Extraction Settings
    docsend_extraction_mode: DocSendExtractionMode

    # Settings
    message_grouping_timeout: int
    ocr_language: str
    telegram_proxy: Optional[str]  # Proxy for Telegram API

    # Cleanup settings (for cloud deployment)
    cleanup_after_extract: bool  # Clean up PDFs immediately after extraction
    cleanup_max_age_minutes: int  # Delete files older than this
    cleanup_interval_minutes: int  # How often to run periodic cleanup

    # Paths
    project_root: Path
    pdf_extractor_path: Path
    temp_dir: Path

    @classmethod
    def load(cls, env_path: Optional[Path] = None) -> "Config":
        """Load configuration from environment variables.

        Args:
            env_path: Optional path to .env file. If not provided, looks for .env
                      in the project root directory.

        Returns:
            Config instance with all settings loaded.

        Raises:
            ValueError: If required environment variables are missing.
        """
        # Determine project root
        project_root = Path(__file__).parent.parent.resolve()

        # Load .env file
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv(project_root / ".env")

        # Required variables
        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")

        telegram_group_id_str = os.getenv("TELEGRAM_GROUP_ID")
        if not telegram_group_id_str:
            raise ValueError("TELEGRAM_GROUP_ID is required")
        telegram_group_id = int(telegram_group_id_str)

        notion_api_key = os.getenv("NOTION_API_KEY")
        if not notion_api_key:
            raise ValueError("NOTION_API_KEY is required")

        notion_database_id = os.getenv("NOTION_DATABASE_ID")
        if not notion_database_id:
            raise ValueError("NOTION_DATABASE_ID is required")

        kimi_api_key = os.getenv("KIMI_API_KEY")
        if not kimi_api_key:
            raise ValueError("KIMI_API_KEY is required")

        kimi_model = os.getenv("KIMI_MODEL", "moonshot-v1-8k")

        docsend_email = os.getenv("DOCSEND_EMAIL", "")
        docsend_password = os.getenv("DOCSEND_PASSWORD")

        # DocSend extraction settings
        extraction_mode_str = os.getenv("DOCSEND_EXTRACTION_MODE", "auto")
        try:
            docsend_extraction_mode = DocSendExtractionMode(extraction_mode_str)
        except ValueError:
            docsend_extraction_mode = DocSendExtractionMode.AUTO

        # Optional settings with defaults
        message_grouping_timeout = int(os.getenv("MESSAGE_GROUPING_TIMEOUT", "30"))
        ocr_language = os.getenv("OCR_LANGUAGE", "chi_sim+eng")
        telegram_proxy = os.getenv("TELEGRAM_PROXY")  # None if not set

        # Cleanup settings (important for cloud deployment)
        cleanup_after_extract = os.getenv("CLEANUP_AFTER_EXTRACT", "false").lower() in ("true", "1", "yes")
        cleanup_max_age_minutes = int(os.getenv("CLEANUP_MAX_AGE_MINUTES", "1440"))  # 24 hours
        cleanup_interval_minutes = int(os.getenv("CLEANUP_INTERVAL_MINUTES", "1440"))  # 24 hours

        # Paths
        pdf_extractor_path = project_root / "PDF_Extractor 2" / "pdf2llm.py"
        temp_dir = project_root / "temp"
        temp_dir.mkdir(exist_ok=True)

        return cls(
            telegram_bot_token=telegram_bot_token,
            telegram_group_id=telegram_group_id,
            notion_api_key=notion_api_key,
            notion_database_id=notion_database_id,
            kimi_api_key=kimi_api_key,
            kimi_model=kimi_model,
            docsend_email=docsend_email,
            docsend_password=docsend_password,
            docsend_extraction_mode=docsend_extraction_mode,
            message_grouping_timeout=message_grouping_timeout,
            ocr_language=ocr_language,
            telegram_proxy=telegram_proxy,
            cleanup_after_extract=cleanup_after_extract,
            cleanup_max_age_minutes=cleanup_max_age_minutes,
            cleanup_interval_minutes=cleanup_interval_minutes,
            project_root=project_root,
            pdf_extractor_path=pdf_extractor_path,
            temp_dir=temp_dir,
        )

    def validate(self) -> list[str]:
        """Validate the configuration and return any warnings.

        Returns:
            List of warning messages for optional but recommended settings.
        """
        warnings = []

        if not self.docsend_email:
            warnings.append(
                "DOCSEND_EMAIL not set - DocSend extraction will be limited"
            )

        if not self.pdf_extractor_path.exists():
            warnings.append(
                f"PDF extractor not found at {self.pdf_extractor_path} - "
                "PDF processing will fail"
            )

        return warnings


# Notion field mappings (based on actual database schema)
# - Name: title (page title)
# - Tags: rich_text (stores comma-separated tags)
# - OP Source: multi_select
# - External Source: rich_text
# - Deck: rich_text (stores URL as text)
# - Memo: rich_text (stores introduction)
NOTION_FIELDS = {
    "title": "Name",  # Page title (title property)
    "tags": "Tags",  # rich_text - comma-separated
    "op_source": "OP Source",  # multi_select
    "external_source": "External Source",  # rich_text
    "deck": "Deck",  # rich_text (URL as text)
    "introduction": "Memo",  # rich_text
}

# Default tag options for deals
DEFAULT_TAGS = [
    "DeFi",
    "AI",
    "Gaming",
    "Infrastructure",
    "SocialFi",
    "NFT",
    "DAO",
    "L1/L2",
    "Privacy",
    "Data",
    "Payments",
    "Enterprise",
    "Consumer",
    "Developer Tools",
    "Research",
]
