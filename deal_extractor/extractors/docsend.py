"""DocSend extraction with multiple fallback strategies.

Extraction methods (in order):
1. docsend2pdf.com API - Free API that handles CAPTCHA internally
2. Playwright browser automation with cookie persistence
"""

import asyncio
import hashlib
import io
import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import ClassVar, Optional
from urllib.parse import urlparse

import aiohttp
from PIL import Image

from .base import BaseExtractor
from ..models.types import DocSendExtractionResult, FetchedDeck

try:
    import img2pdf
except ImportError:
    img2pdf = None

logger = logging.getLogger(__name__)


class ExtractionMode(str, Enum):
    """DocSend extraction mode options."""

    AUTO = "auto"  # Try API first, fallback to Playwright
    PLAYWRIGHT = "playwright"  # Only use Playwright


# Browser-like headers
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class DocSendExtractor(BaseExtractor):
    """Extracts deck content from DocSend links.

    Primary method: docsend2pdf.com API (handles CAPTCHA internally)
    Fallback: Playwright browser automation with cookie persistence

    Example:
        extractor = DocSendExtractor(
            email="your@email.com",
            output_dir=Path("./temp/docsend"),
        )
        result = await extractor.extract("https://docsend.com/view/xxx")
        if result.success:
            print(f"PDF saved to: {result.pdf_path}")
    """

    # Class-level rate limiting for API (5 req/s limit)
    _last_api_call: ClassVar[Optional[datetime]] = None
    _min_interval: ClassVar[timedelta] = timedelta(seconds=0.25)

    def __init__(
        self,
        email: str,
        password: Optional[str] = None,
        output_dir: Optional[Path] = None,
        extraction_mode: ExtractionMode = ExtractionMode.AUTO,
        cookie_file: Optional[Path] = None,
    ):
        """Initialize DocSend extractor.

        Args:
            email: Email address for DocSend authentication.
            password: Optional password for protected documents.
            output_dir: Directory to save extracted files.
            extraction_mode: Which extraction method to use.
            cookie_file: Path to cookie file for session persistence.
        """
        self.email = email
        self.password = password
        self.output_dir = Path(output_dir or tempfile.gettempdir()) / "docsend"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.extraction_mode = extraction_mode
        self.cookie_file = cookie_file or self._find_cookie_file()

    def _find_cookie_file(self) -> Path:
        """Find cookie file from possible locations."""
        primary = self.output_dir / "docsend_cookies.json"
        if primary.exists():
            return primary

        for base in [Path.cwd() / "temp" / "docsend"]:
            fallback = base / "docsend_cookies.json"
            if fallback.exists():
                return fallback

        return primary

    async def extract(
        self, url: str, password: Optional[str] = None
    ) -> FetchedDeck:
        """Extract content from a DocSend URL.

        Args:
            url: DocSend URL to extract.
            password: Optional document password.

        Returns:
            FetchedDeck with content or error.
        """
        if not self.email:
            return FetchedDeck(
                url=url,
                success=False,
                error="DocSend email not configured",
            )

        url = self.normalize_url(url)
        effective_password = password or self.password

        result = await self._extract_internal(url, effective_password)

        if not result.success:
            return FetchedDeck(
                url=url,
                success=False,
                error=result.error,
                title=result.title,
            )

        return FetchedDeck(
            url=url,
            success=True,
            title=result.title,
            pdf_path=result.pdf_path,
        )

    async def extract_full(
        self, url: str, password: Optional[str] = None
    ) -> DocSendExtractionResult:
        """Extract with full result details.

        Args:
            url: DocSend URL to extract.
            password: Optional document password.

        Returns:
            DocSendExtractionResult with all details.
        """
        if not self.email:
            return DocSendExtractionResult(
                success=False,
                error="DocSend email not configured",
            )

        url = self.normalize_url(url)
        effective_password = password or self.password
        return await self._extract_internal(url, effective_password)

    async def _extract_internal(
        self, url: str, password: Optional[str]
    ) -> DocSendExtractionResult:
        """Internal extraction logic.

        Args:
            url: Normalized DocSend URL.
            password: Optional password.

        Returns:
            DocSendExtractionResult with results.
        """
        if self.extraction_mode == ExtractionMode.PLAYWRIGHT:
            return await self._extract_via_browser(url, password)

        # AUTO mode: try API first
        logger.info(f"Attempting docsend2pdf.com API: {url}")
        result = await self._extract_via_api(url, password)

        if result.success:
            logger.info(f"API extraction succeeded: {result.page_count} pages")
            return result

        # Check for password requirement
        if result.error and "passcode" in result.error.lower():
            logger.warning(f"Document requires password: {url}")
            return DocSendExtractionResult(
                success=False,
                error="Document requires password",
                title=self._extract_title_from_url(url),
            )

        # Fallback to Playwright
        logger.info(f"API failed ({result.error}), trying Playwright")
        return await self._extract_via_browser(url, password)

    @staticmethod
    def _extract_title_from_url(url: str) -> Optional[str]:
        """Extract title from DocSend URL slug."""
        try:
            parsed = urlparse(url)
            path = parsed.path
            m = re.search(r"/(?:v|view)/[a-z0-9]+/([a-z0-9_-]+)", path, re.I)
            if m:
                slug = m.group(1)
                title = slug.replace("_", " ").replace("-", " ")
                title = " ".join(word.capitalize() for word in title.split())
                if len(title) > 2:
                    return title
        except Exception:
            pass
        return None

    # ── API Extraction ───────────────────────────────────────────────────

    async def _rate_limit(self) -> None:
        """Enforce rate limiting for API."""
        if DocSendExtractor._last_api_call is not None:
            elapsed = datetime.now() - DocSendExtractor._last_api_call
            if elapsed < DocSendExtractor._min_interval:
                wait_time = (
                    DocSendExtractor._min_interval - elapsed
                ).total_seconds()
                await asyncio.sleep(wait_time)
        DocSendExtractor._last_api_call = datetime.now()

    async def _extract_via_api(
        self, url: str, password: Optional[str] = None
    ) -> DocSendExtractionResult:
        """Extract using docsend2pdf.com API."""
        try:
            await self._rate_limit()

            payload = {"url": url, "email": self.email}
            if password:
                payload["passcode"] = password

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://docsend2pdf.com/api/convert",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status == 200:
                        pdf_bytes = await resp.read()
                        if not pdf_bytes or len(pdf_bytes) < 100:
                            return DocSendExtractionResult(
                                success=False,
                                error="API returned empty response",
                            )

                        # Save PDF
                        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
                        title = self._extract_title_from_url(url)
                        safe_title = "".join(
                            c if c.isalnum() or c in " -_" else "_"
                            for c in (title or "docsend")
                        ).strip()[:30]
                        pdf_path = self.output_dir / f"{safe_title}_{url_hash}.pdf"
                        pdf_path.write_bytes(pdf_bytes)

                        page_count = self._count_pdf_pages(pdf_path)

                        return DocSendExtractionResult(
                            success=True,
                            pdf_path=pdf_path,
                            page_count=page_count,
                            title=title,
                        )

                    elif resp.status == 429:
                        return DocSendExtractionResult(
                            success=False,
                            error="API rate limited",
                        )
                    else:
                        try:
                            error_data = await resp.json()
                            error_msg = error_data.get("error", f"HTTP {resp.status}")
                        except Exception:
                            error_msg = f"HTTP {resp.status}"
                        return DocSendExtractionResult(
                            success=False,
                            error=f"API error: {error_msg}",
                        )

        except asyncio.TimeoutError:
            return DocSendExtractionResult(
                success=False,
                error="API timeout (120s)",
            )
        except aiohttp.ClientError as e:
            return DocSendExtractionResult(
                success=False,
                error=f"API connection error: {e}",
            )
        except Exception as e:
            return DocSendExtractionResult(
                success=False,
                error=f"API error: {e}",
            )

    @staticmethod
    def _count_pdf_pages(pdf_path: Path) -> int:
        """Count pages in a PDF file."""
        try:
            from pypdf import PdfReader

            reader = PdfReader(pdf_path)
            return len(reader.pages)
        except Exception:
            return 0

    # ── Browser Extraction ───────────────────────────────────────────────

    async def _extract_via_browser(
        self, url: str, password: Optional[str] = None
    ) -> DocSendExtractionResult:
        """Extract using Playwright browser automation."""
        try:
            from playwright.async_api import (
                async_playwright,
                TimeoutError as PlaywrightTimeout,
            )
        except ImportError:
            return DocSendExtractionResult(
                success=False,
                error="Playwright not installed. Run: pip install playwright && playwright install chromium",
            )

        # Try stealth mode
        try:
            from playwright_stealth import stealth_async

            use_stealth = True
        except ImportError:
            use_stealth = False

        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                )

                # Load cookies if available
                storage_state = None
                if self.cookie_file.exists():
                    storage_state = str(self.cookie_file)

                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=_HEADERS["User-Agent"],
                    storage_state=storage_state,
                )

                page = await context.new_page()

                if use_stealth:
                    await stealth_async(page)

                page.set_default_timeout(60000)

                # Navigate
                try:
                    await page.goto(url, wait_until="load", timeout=60000)
                except PlaywrightTimeout:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)

                await asyncio.sleep(3)

                # Check for unavailability
                page_content = await page.content()
                unavailable = self._check_document_unavailable(page_content)
                if unavailable:
                    return DocSendExtractionResult(
                        success=False,
                        error=unavailable,
                    )

                # Handle email gate
                try:
                    email_input = page.locator(
                        'input[type="email"], input[name="email"]'
                    )
                    if await email_input.is_visible(timeout=3000):
                        await email_input.fill(self.email)
                        submit = page.locator('button[type="submit"]')
                        if await submit.first.is_visible(timeout=2000):
                            await submit.first.click()
                            await page.wait_for_load_state("load", timeout=30000)
                            await asyncio.sleep(3)
                except Exception:
                    pass

                # Handle password gate
                if password:
                    try:
                        pwd_input = page.locator('input[type="password"]')
                        if await pwd_input.is_visible(timeout=2000):
                            await pwd_input.fill(password)
                            submit = page.locator('button[type="submit"]')
                            if await submit.first.is_visible(timeout=2000):
                                await submit.first.click()
                                await page.wait_for_load_state("load", timeout=30000)
                                await asyncio.sleep(3)
                    except Exception:
                        pass

                # Check for CAPTCHA
                page_content = await page.content()
                if self._check_captcha_required(page_content):
                    return DocSendExtractionResult(
                        success=False,
                        error="CAPTCHA required - manual setup needed",
                    )

                await asyncio.sleep(2)

                title = await self._browser_extract_title(page)
                page_count = await self._browser_get_page_count(page)

                if page_count == 0:
                    page_count = 1

                # Capture screenshots
                screenshots = []
                image_paths = []
                url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

                for i in range(min(page_count, 30)):
                    screenshot = await self._browser_capture_page(page)
                    if screenshot:
                        screenshots.append(screenshot)
                        img_path = self.output_dir / f"page_{url_hash}_{i+1:03d}.png"
                        img_path.write_bytes(screenshot)
                        image_paths.append(img_path)

                    if i < page_count - 1:
                        await self._browser_next_page(page)
                        await asyncio.sleep(1.5)

                # Save cookies
                if screenshots:
                    try:
                        await context.storage_state(path=str(self.cookie_file))
                    except Exception:
                        pass

                await browser.close()
                browser = None

                if not screenshots:
                    return DocSendExtractionResult(
                        success=False,
                        error="Failed to capture any screenshots",
                    )

                pdf_path = self._save_pdf(screenshots, title or "docsend", url)

                return DocSendExtractionResult(
                    success=True,
                    pdf_path=pdf_path,
                    page_count=len(screenshots),
                    title=title,
                    image_paths=image_paths,
                )

        except Exception as e:
            return DocSendExtractionResult(
                success=False,
                error=f"Browser extraction failed: {e}",
            )
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

    @staticmethod
    def _check_document_unavailable(html: str) -> Optional[str]:
        """Check if document is unavailable."""
        if "dead-link--disabled" in html:
            return "Document disabled"
        if "This document is not available" in html:
            return "Document not available"
        if "This link has expired" in html:
            return "Link expired"
        if "access denied" in html.lower():
            return "Access denied"
        return None

    @staticmethod
    def _check_captcha_required(html: str) -> bool:
        """Check if CAPTCHA is required."""
        indicators = [
            '"CAPTCHA_ENABLED":true',
            "arkose",
            "funcaptcha",
            "captcha-container",
        ]
        html_lower = html.lower()
        return any(ind.lower() in html_lower for ind in indicators)

    async def _browser_extract_title(self, page) -> Optional[str]:
        """Extract title from page."""
        try:
            for selector in [
                '[data-testid="document-title"]',
                ".document-title",
                "h1",
            ]:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        text = await element.text_content()
                        if text and len(text.strip()) > 2:
                            return text.strip()
                except Exception:
                    continue

            title = await page.title()
            if title and "docsend" not in title.lower():
                for suffix in [" | DocSend", " - DocSend"]:
                    if suffix in title:
                        title = title.split(suffix)[0]
                return title.strip() if len(title.strip()) > 2 else None
        except Exception:
            pass
        return None

    async def _browser_get_page_count(self, page) -> int:
        """Get page count from browser."""
        try:
            for selector in [
                '[data-testid="page-count"]',
                ".page-count",
                '[class*="pageCount"]',
            ]:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        text = await element.text_content()
                        if text:
                            m = re.search(r"(\d+)\s*[/of]\s*(\d+)", text)
                            if m:
                                return int(m.group(2))
                except Exception:
                    continue

            for selector in [".page-thumbnail", ".slide-thumbnail"]:
                try:
                    count = await page.locator(selector).count()
                    if count > 0:
                        return count
                except Exception:
                    continue
        except Exception:
            pass
        return 0

    async def _browser_capture_page(self, page) -> Optional[bytes]:
        """Capture current page screenshot."""
        try:
            for selector in [
                '[data-testid="slide-container"]',
                ".slide-container",
                ".viewer-content",
                "#viewer",
                "main",
            ]:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        return await element.screenshot()
                except Exception:
                    continue
            return await page.screenshot(full_page=False)
        except Exception:
            return None

    async def _browser_next_page(self, page) -> bool:
        """Navigate to next page."""
        try:
            for selector in [
                '[data-testid="next-page"]',
                ".next-page",
                'button[aria-label="Next"]',
            ]:
                try:
                    button = page.locator(selector).first
                    if await button.is_visible(timeout=500):
                        await button.click()
                        await asyncio.sleep(0.5)
                        return True
                except Exception:
                    continue
            await page.keyboard.press("ArrowRight")
            await asyncio.sleep(0.5)
            return True
        except Exception:
            return False

    def _save_pdf(
        self, images: list[bytes], title: str, url: Optional[str] = None
    ) -> Path:
        """Assemble images into a PDF file."""
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in title
        )
        safe_title = safe_title.strip()[:30] or "docsend"

        if url:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            pdf_path = self.output_dir / f"{safe_title}_{url_hash}.pdf"
        else:
            pdf_path = self.output_dir / f"{safe_title}.pdf"

        if img2pdf:
            with open(pdf_path, "wb") as f:
                f.write(img2pdf.convert(images))
        else:
            pil_images = []
            for raw in images:
                img = Image.open(io.BytesIO(raw))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                pil_images.append(img)
            if pil_images:
                pil_images[0].save(
                    pdf_path, "PDF", save_all=True, append_images=pil_images[1:]
                )

        return pdf_path
