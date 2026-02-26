"""Deal Extractor - Independent module for extracting deal information.

This module provides tools to:
1. Detect and classify URLs in text (deck links, social links, etc.)
2. Extract content from various deck sources (DocSend, PDF, Google Slides)
3. Use LLM to analyze and structure deal information

Example:
    from deal_extractor import DealExtractor

    extractor = DealExtractor(
        llm_api_key="sk-xxx",
        llm_model="kimi-k2.5",
        docsend_email="your@email.com",
    )

    result = await extractor.extract(
        text="Check this deal: https://docsend.com/view/xxx",
        sender="John",
    )

    if result.success:
        for deal in result.deals:
            print(f"Company: {deal.company_name}")
            print(f"Tags: {deal.tags}")
"""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional

from .extractors import DocSendExtractor, GenericWebExtractor, GoogleSlidesExtractor, PapermarkExtractor, PDFExtractor
from .links import DetectedLink, LinkDetector, LinkType
from .llm import LLMExtractor
from .models import Deal, ExtractionResult, FetchedDeck, RouterDecision

__version__ = "0.1.0"

__all__ = [
    # Main class
    "DealExtractor",
    # Models
    "Deal",
    "DetectedLink",
    "ExtractionResult",
    "FetchedDeck",
    "LinkType",
    "RouterDecision",
    # Extractors (for standalone use)
    "DocSendExtractor",
    "GenericWebExtractor",
    "GoogleSlidesExtractor",
    "LinkDetector",
    "LLMExtractor",
    "PapermarkExtractor",
    "PDFExtractor",
]

logger = logging.getLogger(__name__)

# Files to never delete during cleanup
PROTECTED_FILES = {
    "docsend_cookies.json",
    ".gitkeep",
}


class DealExtractor:
    """Main class for extracting deal information from messages.

    Combines link detection, content extraction, and LLM analysis into
    a simple, unified API. Includes automatic cleanup of temporary files.

    Example:
        extractor = DealExtractor(
            llm_api_key="sk-xxx",
            llm_model="kimi-k2.5",
            llm_base_url="https://api.moonshot.cn/v1",
            docsend_email="your@email.com",
            temp_dir=Path("./temp"),
            cleanup_after_extract=False,  # Keep files until periodic cleanup
            cleanup_max_age_minutes=1440,  # Clean files older than 24 hours
        )

        result = await extractor.extract(
            text="New project intro: https://docsend.com/view/xxx",
            sender="John",
            pdf_content=None,
        )

        if result.success:
            for deal in result.deals:
                print(f"Company: {deal.company_name}")
                print(f"Tags: {deal.tags}")
                print(f"Intro: {deal.intro}")
                print(f"Deck: {deal.deck_url}")
    """

    def __init__(
        self,
        llm_api_key: str,
        llm_model: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        docsend_email: Optional[str] = None,
        docsend_password: Optional[str] = None,
        pdf2llm_path: Optional[Path] = None,
        temp_dir: Optional[Path] = None,
        cleanup_after_extract: bool = False,
        cleanup_max_age_minutes: int = 1440,  # 24 hours
        browser_agent_enabled: bool = False,
    ):
        """Initialize the DealExtractor.

        Args:
            llm_api_key: API key for the LLM provider.
            llm_model: Model name (default: kimi-k2.5).
            llm_base_url: API base URL (default: Kimi/Moonshot).
            docsend_email: Email for DocSend authentication.
            docsend_password: Password for protected DocSend documents.
            pdf2llm_path: Path to pdf2llm.py for OCR extraction.
            temp_dir: Directory for temporary files.
            cleanup_after_extract: If True, clean up PDFs immediately after extraction.
            cleanup_max_age_minutes: Delete files older than this many minutes.
        """
        self.temp_dir = Path(temp_dir or "./temp/deal_extractor")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Cleanup settings
        self.cleanup_after_extract = cleanup_after_extract
        self.cleanup_max_age_minutes = cleanup_max_age_minutes

        # Track files created during current extraction
        self._pending_cleanup: list[Path] = []

        # Initialize components
        self.link_detector = LinkDetector()

        self.llm_extractor = LLMExtractor(
            api_key=llm_api_key,
            model=llm_model,
            base_url=llm_base_url,
        )

        # PDF extractor
        self.pdf_extractor = PDFExtractor(
            output_dir=self.temp_dir / "pdf",
            pdf2llm_path=pdf2llm_path,
        )

        # DocSend extractor (optional)
        self.docsend_extractor = None
        if docsend_email:
            self.docsend_extractor = DocSendExtractor(
                email=docsend_email,
                password=docsend_password,
                output_dir=self.temp_dir,  # DocSendExtractor internally appends /docsend
            )

        # Google Slides extractor
        self.google_extractor = GoogleSlidesExtractor(
            temp_dir=self.temp_dir / "google",
            pdf_extractor=self.pdf_extractor,
        )

        # Generic web extractor (fallback for unsupported link types)
        self.generic_extractor = GenericWebExtractor(
            temp_dir=self.temp_dir / "web",
            pdf_extractor=self.pdf_extractor,
        )

        # Papermark extractor (DeckExtract API + generic fallback)
        self.papermark_extractor = PapermarkExtractor(
            email=docsend_email or "",
            output_dir=self.temp_dir / "papermark",
            generic_extractor=self.generic_extractor,
        )

        # Browser agent extractor (opt-in, last-resort fallback)
        self.browser_agent_extractor = None
        if browser_agent_enabled:
            try:
                from .extractors.browser_agent import BrowserAgentExtractor
                self.browser_agent_extractor = BrowserAgentExtractor(
                    api_key=llm_api_key,
                    model=llm_model or "kimi-k2.5",
                    base_url=llm_base_url,
                    email=docsend_email,
                    password=docsend_password,
                    temp_dir=self.temp_dir / "browser_agent",
                )
                logger.info("Browser agent extractor enabled")
            except ImportError:
                logger.warning("browser-use not installed, browser agent disabled")

        logger.info(
            f"DealExtractor initialized: "
            f"model={llm_model or 'kimi-k2.5'}, "
            f"docsend={'enabled' if docsend_email else 'disabled'}, "
            f"cleanup={'immediate' if cleanup_after_extract else 'manual'}"
        )

    async def extract(
        self,
        text: str,
        sender: str,
        pdf_content: Optional[str] = None,
    ) -> ExtractionResult:
        """Extract deals from a message.

        Args:
            text: The message content.
            sender: Message sender (OP Source).
            pdf_content: Pre-extracted PDF content (optional).

        Returns:
            ExtractionResult with extracted deals.
        """
        logger.info(f"Processing message from {sender}")

        # Reset pending cleanup list
        self._pending_cleanup = []

        try:
            # Step 1: Detect deck links
            detected_links = self.link_detector.get_all_deck_links(text)
            password = self._extract_password(text)

            logger.info(f"Found {len(detected_links)} deck link(s)")

            # Step 2: Fetch deck contents
            fetched_decks: list[FetchedDeck] = []

            for link in detected_links:
                deck = await self._fetch_deck(link, password)
                fetched_decks.append(deck)

                # Track PDF path for cleanup
                if deck.pdf_path:
                    self._pending_cleanup.append(deck.pdf_path)

            decks_fetched = len([d for d in fetched_decks if d.success])
            logger.info(f"Fetched {decks_fetched}/{len(detected_links)} deck(s)")

            # Step 3: LLM extraction
            result = await self.llm_extractor.extract(
                message_text=text,
                sender=sender,
                fetched_decks=fetched_decks,
                pdf_content=pdf_content,
            )

            # Step 4: Set deck stats, pipeline data, and needs_review flag
            result.decks_detected = len(detected_links)
            result.decks_fetched = decks_fetched  # Override LLM layer value
            result.detected_links = detected_links
            result.fetched_decks = fetched_decks

            if result.decks_detected > 0 and decks_fetched == 0:
                result.needs_review = True
                result.review_reasons = [
                    f"{d.url}: {d.error}" for d in fetched_decks if not d.success
                ]

            # Flag thin content — deck "succeeded" but extracted too little
            # (likely hit a login/verification page)
            thin_decks = [
                d for d in fetched_decks
                if d.success and d.content and len(d.content) < 500
            ]
            if thin_decks:
                result.needs_review = True
                if not result.review_reasons:
                    result.review_reasons = []
                for d in thin_decks:
                    result.review_reasons.append(
                        f"{d.url}: thin content ({len(d.content)} chars, may be login page)"
                    )

            return result

        finally:
            # Cleanup after extraction
            if self.cleanup_after_extract:
                self._cleanup_pending_files()

    def _cleanup_pending_files(self) -> int:
        """Clean up files created during the current extraction.

        Returns:
            Number of files deleted.
        """
        deleted = 0
        for path in self._pending_cleanup:
            try:
                if path.exists() and path.is_file():
                    path.unlink()
                    deleted += 1
                    logger.debug(f"Cleaned up: {path}")
            except Exception as e:
                logger.warning(f"Failed to clean up {path}: {e}")

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} temporary file(s)")

        self._pending_cleanup = []
        return deleted

    def cleanup_old_files(self, max_age_minutes: Optional[int] = None) -> int:
        """Clean up files older than the specified age.

        Args:
            max_age_minutes: Maximum file age in minutes. Uses instance default if None.

        Returns:
            Number of files deleted.
        """
        max_age = max_age_minutes or self.cleanup_max_age_minutes
        cutoff_time = time.time() - (max_age * 60)
        deleted = 0

        for subdir in ["pdf", "docsend", "google", "web", "papermark", "pdf_downloads", "browser_agent"]:
            dir_path = self.temp_dir / subdir
            if not dir_path.exists():
                continue

            deleted += self._cleanup_directory(dir_path, cutoff_time)

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old file(s) (older than {max_age} min)")

        return deleted

    def _cleanup_directory(self, directory: Path, cutoff_time: float) -> int:
        """Clean up old files in a directory.

        Args:
            directory: Directory to clean.
            cutoff_time: Delete files older than this timestamp.

        Returns:
            Number of files deleted.
        """
        deleted = 0

        try:
            for path in directory.iterdir():
                # Skip protected files
                if path.name in PROTECTED_FILES:
                    continue

                # Recursively clean subdirectories
                if path.is_dir():
                    deleted += self._cleanup_directory(path, cutoff_time)
                    # Remove empty directories
                    try:
                        if not any(path.iterdir()):
                            path.rmdir()
                            logger.debug(f"Removed empty dir: {path}")
                    except Exception:
                        pass
                    continue

                # Check file age
                try:
                    mtime = path.stat().st_mtime
                    if mtime < cutoff_time:
                        path.unlink()
                        deleted += 1
                        logger.debug(f"Deleted old file: {path}")
                except Exception as e:
                    logger.warning(f"Failed to delete {path}: {e}")

        except Exception as e:
            logger.warning(f"Error cleaning directory {directory}: {e}")

        return deleted

    def get_temp_dir_size(self) -> tuple[int, int]:
        """Get the size of the temp directory.

        Returns:
            Tuple of (total_bytes, file_count).
        """
        total_size = 0
        file_count = 0

        try:
            for path in self.temp_dir.rglob("*"):
                if path.is_file() and path.name not in PROTECTED_FILES:
                    total_size += path.stat().st_size
                    file_count += 1
        except Exception as e:
            logger.warning(f"Error calculating temp dir size: {e}")

        return total_size, file_count

    async def _fetch_deck(
        self, link: DetectedLink, password: Optional[str]
    ) -> FetchedDeck:
        """Fetch content from a deck link.

        Args:
            link: Detected link to fetch.
            password: Optional password.

        Returns:
            FetchedDeck with content or error.
        """
        logger.info(f"Fetching: {link.url} (type: {link.link_type.value})")

        try:
            if link.link_type == LinkType.DOCSEND:
                if not self.docsend_extractor:
                    return FetchedDeck(
                        url=link.url,
                        success=False,
                        error="DocSend extraction not configured (no email)",
                    )
                result = await self.docsend_extractor.extract(link.url, password)

                # If we got a PDF, extract text via OCR
                if result.success and result.pdf_path:
                    pdf_result = self.pdf_extractor.extract(result.pdf_path)
                    if pdf_result.success and pdf_result.text_content:
                        return FetchedDeck(
                            url=link.url,
                            success=True,
                            content=pdf_result.text_content,
                            title=result.title or pdf_result.title,
                            pdf_path=result.pdf_path,
                        )
                    else:
                        # PDF downloaded but text extraction failed (e.g. OCR not installed)
                        error_detail = pdf_result.error or "empty text (image-only PDF, OCR may be needed)"
                        logger.warning(f"DocSend PDF text extraction failed: {error_detail}")
                        return FetchedDeck(
                            url=link.url,
                            success=False,
                            error=f"PDF downloaded but text extraction failed: {error_detail}",
                            title=result.title,
                            pdf_path=result.pdf_path,
                        )
                # Browser agent fallback for DocSend failures
                if not result.success and self.browser_agent_extractor:
                    logger.info(f"DocSend extractors failed, trying browser agent: {link.url}")
                    result = await self.browser_agent_extractor.extract(link.url, password)

                return result

            elif link.link_type == LinkType.PDF_DIRECT:
                return await self._fetch_pdf_url(link.url)

            elif link.link_type == LinkType.GOOGLE_DRIVE:
                return await self.google_extractor.extract(link.url)

            elif link.link_type == LinkType.PAPERMARK:
                result = await self.papermark_extractor.extract(link.url, password)
                # If API returned a PDF, extract text from it
                if result.success and result.pdf_path:
                    pdf_result = self.pdf_extractor.extract(result.pdf_path)
                    if pdf_result.success and pdf_result.text_content:
                        return FetchedDeck(
                            url=link.url,
                            success=True,
                            content=pdf_result.text_content,
                            title=result.title or pdf_result.title,
                            pdf_path=result.pdf_path,
                        )
                    else:
                        error_detail = pdf_result.error or "empty text (image-only PDF, OCR may be needed)"
                        logger.warning(f"Papermark PDF text extraction failed: {error_detail}")
                        return FetchedDeck(
                            url=link.url,
                            success=False,
                            error=f"PDF downloaded but text extraction failed: {error_detail}",
                            title=result.title,
                            pdf_path=result.pdf_path,
                        )
                return result

            elif link.link_type == LinkType.NOTION:
                resolved_url = self._resolve_notion_url(link.url)
                result = await self.generic_extractor.extract(resolved_url, password)
                if not result.success and self.browser_agent_extractor:
                    logger.info(f"Generic extractor failed for Notion, trying browser agent: {link.url}")
                    result = await self.browser_agent_extractor.extract(link.url, password)
                return result

            else:
                # Fallback: use generic web extractor for all other deck types
                # (Pitch.com, Loom, Dropbox, etc.)
                result = await self.generic_extractor.extract(link.url, password)
                if not result.success and self.browser_agent_extractor:
                    logger.info(f"Generic extractor failed, trying browser agent: {link.url}")
                    result = await self.browser_agent_extractor.extract(link.url, password)
                return result

        except Exception as e:
            logger.exception(f"Fetch error: {e}")
            return FetchedDeck(url=link.url, success=False, error=str(e))

    async def _fetch_pdf_url(self, url: str) -> FetchedDeck:
        """Fetch and extract content from a direct PDF URL.

        Args:
            url: Direct PDF URL.

        Returns:
            FetchedDeck with content or error.
        """
        import hashlib

        import httpx

        pdf_path = None
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=60.0
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

                # Save PDF
                url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
                pdf_path = self.temp_dir / "pdf" / f"downloaded_{url_hash}.pdf"
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                pdf_path.write_bytes(response.content)

            # Track for cleanup
            self._pending_cleanup.append(pdf_path)

            # Extract text
            pdf_result = self.pdf_extractor.extract(pdf_path)

            if pdf_result.success:
                return FetchedDeck(
                    url=url,
                    success=True,
                    content=pdf_result.text_content,
                    title=pdf_result.title,
                    pdf_path=pdf_path,
                )
            else:
                return FetchedDeck(
                    url=url,
                    success=False,
                    error=pdf_result.error or "PDF extraction failed",
                )

        except Exception as e:
            return FetchedDeck(
                url=url,
                success=False,
                error=f"PDF download failed: {e}",
            )

    @staticmethod
    def _resolve_notion_url(url: str) -> str:
        """Rewrite notion.so/{workspace}/{slug} to {workspace}.notion.site/{slug}.

        Notion migrated from notion.so to notion.site. The old domain shows a
        JS interstitial redirect that httpx and Jina Reader can't follow.
        Rewriting skips the interstitial entirely.
        """
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.hostname in ("notion.so", "www.notion.so"):
            parts = parsed.path.strip("/").split("/", 1)
            if len(parts) == 2:
                workspace, rest = parts
                return f"https://{workspace}.notion.site/{rest}"
        return url

    def _extract_password(self, text: str) -> Optional[str]:
        """Extract password from message text.

        Args:
            text: Message text.

        Returns:
            Extracted password or None.
        """
        patterns = [
            r"(?:password|passcode|pwd|pw|密码)[:\s]*([^\s\n]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                password = match.group(1).strip().rstrip(".,;:")
                return password

        return None
