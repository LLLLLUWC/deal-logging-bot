"""Generic web content extractor for unsupported deck link types.

Two-phase extraction:
1. httpx GET with browser headers (fast, handles static pages)
2. Jina Reader fallback for JS-rendered SPAs (Notion, Papermark, etc.)
"""

import hashlib
import logging
import re
from pathlib import Path
from typing import Optional

import html2text
import httpx

from .base import BaseExtractor
from ..models.types import FetchedDeck

logger = logging.getLogger(__name__)

# Browser-like headers for httpx requests
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Jina Reader endpoint
_JINA_READER_URL = "https://r.jina.ai/"

# Minimum text length to consider extraction successful without fallback
_MIN_TEXT_LENGTH = 200

# Maximum content length to return (avoid huge pages overwhelming LLM)
_MAX_CONTENT_LENGTH = 8000


class GenericWebExtractor(BaseExtractor):
    """Extracts content from arbitrary web pages.

    Tries httpx first for speed, falls back to Jina Reader for
    JS-rendered pages (e.g. Notion, Papermark).

    If the response is a PDF (Content-Type: application/pdf),
    saves it and delegates to PDFExtractor.

    Example:
        extractor = GenericWebExtractor(
            temp_dir=Path("./temp/web"),
            pdf_extractor=pdf_extractor_instance,
        )
        result = await extractor.extract("https://notion.so/some-page")
        if result.success:
            print(result.content[:500])
    """

    def __init__(
        self,
        temp_dir: Path,
        pdf_extractor=None,
    ):
        """Initialize the generic web extractor.

        Args:
            temp_dir: Directory for temporary files.
            pdf_extractor: Optional PDFExtractor for PDF responses.
        """
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_extractor = pdf_extractor

        # Configure html2text
        self._h2t = html2text.HTML2Text()
        self._h2t.ignore_links = False
        self._h2t.ignore_images = True
        self._h2t.body_width = 0  # No wrapping

    async def extract(
        self, url: str, password: Optional[str] = None
    ) -> FetchedDeck:
        """Extract content from a web URL.

        Args:
            url: The URL to extract content from.
            password: Not used for generic extraction.

        Returns:
            FetchedDeck with extracted text content or error.
        """
        logger.info(f"GenericWebExtractor: fetching {url}")

        # Phase 1: Try httpx GET (fast)
        try:
            result = await self._fetch_with_httpx(url)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"httpx fetch failed for {url}: {e}")

        # Phase 2: Jina Reader fallback for JS-rendered pages
        try:
            result = await self._fetch_with_jina(url)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"Jina Reader failed for {url}: {e}")

        return FetchedDeck(
            url=url,
            success=False,
            error="Failed to extract content (both httpx and Jina Reader failed)",
        )

    async def _fetch_with_httpx(self, url: str) -> Optional[FetchedDeck]:
        """Attempt to fetch and extract content using httpx.

        Returns:
            FetchedDeck if extraction yielded enough text, None to signal
            that Jina Reader should be tried instead.
        """
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers=_HEADERS,
        ) as client:
            response = await client.get(url)

            # Handle auth errors
            if response.status_code in (401, 403):
                return FetchedDeck(
                    url=url,
                    success=False,
                    error=f"Access denied (HTTP {response.status_code})",
                )

            response.raise_for_status()

            content_type = response.headers.get("content-type", "")

            # PDF response: delegate to PDFExtractor
            if "application/pdf" in content_type:
                return self._handle_pdf_response(url, response.content)

            # HTML response: convert to text
            if "text/html" in content_type or "text/plain" in content_type:
                html = response.text
                text = self._html_to_text(html)

                if len(text) >= _MIN_TEXT_LENGTH:
                    logger.info(
                        f"httpx extracted {len(text)} chars from {url}"
                    )
                    return FetchedDeck(
                        url=url,
                        success=True,
                        content=text[:_MAX_CONTENT_LENGTH],
                        title=self._extract_title(html),
                    )

                # Too little text — likely JS-rendered, fall through to Jina
                logger.info(
                    f"httpx got only {len(text)} chars from {url}, "
                    f"trying Jina Reader"
                )
                return None

            # Unknown content type
            logger.warning(f"Unexpected content-type for {url}: {content_type}")
            return None

    async def _fetch_with_jina(self, url: str) -> Optional[FetchedDeck]:
        """Fetch page content via Jina Reader (renders JS, returns Markdown).

        Returns:
            FetchedDeck with content or None on failure.
        """
        jina_url = f"{_JINA_READER_URL}{url}"
        headers = {"Accept": "text/markdown"}

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            response = await client.get(jina_url, headers=headers)

            if response.status_code != 200:
                logger.warning(
                    f"Jina Reader returned HTTP {response.status_code} for {url}"
                )
                return None

            text = response.text.strip()

            # Parse title from Jina's "Title: ..." header
            title = self._parse_jina_title(text)

            # Strip Jina metadata headers (Title:, URL Source:, Markdown Content:)
            text = self._strip_jina_headers(text)

            if len(text) < 50:
                logger.warning(
                    f"Jina Reader got only {len(text)} chars from {url}"
                )
                return FetchedDeck(
                    url=url,
                    success=False,
                    error="Page content too short after Jina Reader extraction",
                )

            logger.info(f"Jina Reader extracted {len(text)} chars from {url}")
            return FetchedDeck(
                url=url,
                success=True,
                content=text[:_MAX_CONTENT_LENGTH],
                title=title,
            )

    def _handle_pdf_response(
        self, url: str, pdf_bytes: bytes
    ) -> FetchedDeck:
        """Handle a PDF response by saving and extracting text.

        Args:
            url: Original URL.
            pdf_bytes: Raw PDF content.

        Returns:
            FetchedDeck with extracted text or error.
        """
        if not self.pdf_extractor:
            return FetchedDeck(
                url=url,
                success=False,
                error="PDF response but no PDF extractor configured",
            )

        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        pdf_path = self.temp_dir / f"web_{url_hash}.pdf"
        pdf_path.write_bytes(pdf_bytes)

        try:
            pdf_result = self.pdf_extractor.extract(pdf_path)
            if pdf_result.success:
                return FetchedDeck(
                    url=url,
                    success=True,
                    content=pdf_result.text_content,
                    title=pdf_result.title,
                    pdf_path=pdf_path,
                )
            return FetchedDeck(
                url=url,
                success=False,
                error=pdf_result.error or "PDF extraction failed",
            )
        except Exception as e:
            return FetchedDeck(
                url=url,
                success=False,
                error=f"PDF extraction error: {e}",
            )

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to clean plain text.

        Args:
            html: Raw HTML string.

        Returns:
            Cleaned plain text.
        """
        text = self._h2t.handle(html)
        # Collapse multiple blank lines
        lines = text.split("\n")
        cleaned = []
        blank_count = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                blank_count += 1
                if blank_count <= 1:
                    cleaned.append("")
            else:
                blank_count = 0
                cleaned.append(stripped)
        return "\n".join(cleaned).strip()

    @staticmethod
    def _extract_title(html: str) -> Optional[str]:
        """Extract <title> from HTML.

        Args:
            html: Raw HTML string.

        Returns:
            Title text or None.
        """
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            title = match.group(1).strip()
            if title:
                return title
        return None

    @staticmethod
    def _parse_jina_title(text: str) -> Optional[str]:
        """Parse title from Jina Reader response header.

        Jina Reader returns content with metadata like:
            Title: Page Title Here
            URL Source: https://...
            Markdown Content:
            ...

        Args:
            text: Raw Jina Reader response.

        Returns:
            Extracted title or None.
        """
        match = re.match(r"Title:\s*(.+)", text)
        if match:
            title = match.group(1).strip()
            if title:
                return title
        return None

    @staticmethod
    def _strip_jina_headers(text: str) -> str:
        """Strip Jina Reader metadata headers from response.

        Removes all leading metadata lines (Title:, URL Source:,
        Published Time:, Markdown Content:) and blank lines before
        the actual content begins.

        Args:
            text: Raw Jina Reader response.

        Returns:
            Content without metadata headers.
        """
        _JINA_HEADER_PREFIXES = (
            "Title:",
            "URL Source:",
            "Markdown Content:",
            "Published Time:",
        )
        lines = text.split("\n")
        content_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or any(stripped.startswith(p) for p in _JINA_HEADER_PREFIXES):
                content_start = i + 1
            else:
                break
        return "\n".join(lines[content_start:]).strip()
