"""Google Slides/Docs extraction by exporting to PDF."""

import logging
import re
from pathlib import Path
from typing import Optional

import httpx

from .base import BaseExtractor
from ..models.types import FetchedDeck

logger = logging.getLogger(__name__)


class GoogleSlidesExtractor(BaseExtractor):
    """Extracts content from Google Slides/Docs by exporting to PDF.

    Works for publicly shared presentations. Export URL formats:
    - Slides: https://docs.google.com/presentation/d/{id}/export/pdf
    - Docs: https://docs.google.com/document/d/{id}/export?format=pdf
    - Drive: https://drive.google.com/uc?export=download&id={id}

    Example:
        extractor = GoogleSlidesExtractor(temp_dir=Path("./temp"))
        result = await extractor.extract(
            "https://docs.google.com/presentation/d/xxx/edit"
        )
        if result.success:
            print(result.content)
    """

    def __init__(
        self,
        temp_dir: Path,
        pdf_extractor=None,
    ):
        """Initialize Google Slides extractor.

        Args:
            temp_dir: Directory for temporary files.
            pdf_extractor: Optional PDFExtractor instance for text extraction.
        """
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_extractor = pdf_extractor

    async def extract(
        self, url: str, password: Optional[str] = None
    ) -> FetchedDeck:
        """Extract content from a Google Slides/Docs URL.

        Args:
            url: Google Slides/Docs/Drive URL.
            password: Not used (Google docs don't use passwords).

        Returns:
            FetchedDeck with content or error.
        """
        # Extract document ID and type
        doc_id, doc_type = self._parse_url(url)

        if not doc_id:
            return FetchedDeck(
                url=url,
                success=False,
                error="Could not extract Google document ID from URL",
            )

        # Construct export URL
        export_url = self._get_export_url(doc_id, doc_type)

        if not export_url:
            return FetchedDeck(
                url=url,
                success=False,
                error=f"Unsupported Google document type: {doc_type}",
            )

        logger.info(f"Exporting Google doc as PDF: {export_url}")

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=60.0
            ) as client:
                response = await client.get(export_url)

                # Check for access issues
                if response.status_code in (401, 403):
                    return FetchedDeck(
                        url=url,
                        success=False,
                        error="Google doc requires login or is not publicly shared",
                    )

                # Check for HTML response (usually access error)
                content_type = response.headers.get("content-type", "")
                if "text/html" in content_type:
                    return FetchedDeck(
                        url=url,
                        success=False,
                        error="Google doc is not publicly accessible",
                    )

                response.raise_for_status()

                # Save PDF
                pdf_path = self.temp_dir / f"google_{doc_id}.pdf"
                pdf_path.write_bytes(response.content)

                logger.info(f"Downloaded Google doc: {len(response.content)} bytes")

                # Extract text if PDF extractor available
                text_content = None
                title = None

                if self.pdf_extractor:
                    pdf_result = self.pdf_extractor.extract(pdf_path)
                    if pdf_result.success:
                        text_content = pdf_result.text_content
                        title = pdf_result.title

                # Clean up
                try:
                    pdf_path.unlink()
                except Exception:
                    pass

                return FetchedDeck(
                    url=url,
                    success=True,
                    content=text_content,
                    title=title,
                    pdf_path=pdf_path if pdf_path.exists() else None,
                )

        except httpx.HTTPStatusError as e:
            return FetchedDeck(
                url=url,
                success=False,
                error=f"Failed to download: HTTP {e.response.status_code}",
            )
        except Exception as e:
            return FetchedDeck(
                url=url,
                success=False,
                error=f"Export failed: {str(e)}",
            )

    def _parse_url(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Parse Google URL to extract document ID and type.

        Args:
            url: Google URL.

        Returns:
            Tuple of (doc_id, doc_type) or (None, None) if invalid.
        """
        if "/presentation/d/" in url:
            match = re.search(r"/presentation/d/([a-zA-Z0-9_-]+)", url)
            if match:
                return match.group(1), "slides"

        elif "/document/d/" in url:
            match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
            if match:
                return match.group(1), "docs"

        elif "/file/d/" in url:
            match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
            if match:
                return match.group(1), "drive"

        return None, None

    def _get_export_url(
        self, doc_id: str, doc_type: str
    ) -> Optional[str]:
        """Get the export URL for a Google document.

        Args:
            doc_id: Document ID.
            doc_type: Document type (slides, docs, drive).

        Returns:
            Export URL or None if unsupported.
        """
        if doc_type == "slides":
            return f"https://docs.google.com/presentation/d/{doc_id}/export/pdf"
        elif doc_type == "docs":
            return f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
        elif doc_type == "drive":
            return f"https://drive.google.com/uc?export=download&id={doc_id}"
        return None
