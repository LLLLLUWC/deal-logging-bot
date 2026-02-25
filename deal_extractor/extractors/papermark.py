"""Papermark deck extraction via DeckExtract API.

Extraction methods (in order):
1. DeckExtract API (https://deckextract.com/api) - returns PDF bytes directly
2. GenericWebExtractor fallback (Jina Reader)
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar, Optional

import httpx

from .base import BaseExtractor
from ..models.types import FetchedDeck

logger = logging.getLogger(__name__)

# DeckExtract API endpoint
_DECKEXTRACT_API_URL = "https://deckextract.com/api"


class PapermarkExtractor(BaseExtractor):
    """Extracts deck content from Papermark links.

    Primary method: DeckExtract API (free, returns PDF bytes)
    Fallback: GenericWebExtractor (Jina Reader)

    Example:
        extractor = PapermarkExtractor(
            email="your@email.com",
            output_dir=Path("./temp/papermark"),
            generic_extractor=generic_web_instance,
        )
        result = await extractor.extract("https://papermark.io/view/xxx")
        if result.success:
            print(f"PDF saved to: {result.pdf_path}")
    """

    # Class-level rate limiting: 5 req / 30 min / IP
    _request_timestamps: ClassVar[list[datetime]] = []
    _rate_window: ClassVar[timedelta] = timedelta(minutes=30)
    _rate_limit: ClassVar[int] = 5

    def __init__(
        self,
        email: str,
        output_dir: Path,
        generic_extractor: Optional[BaseExtractor] = None,
    ):
        """Initialize Papermark extractor.

        Args:
            email: Email address for Papermark email gates.
            output_dir: Directory to save extracted PDF files.
            generic_extractor: Fallback extractor for when API fails.
        """
        self.email = email
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.generic_extractor = generic_extractor

    async def extract(
        self, url: str, password: Optional[str] = None
    ) -> FetchedDeck:
        """Extract content from a Papermark URL.

        Tries DeckExtract API first, falls back to generic extractor.

        Args:
            url: Papermark URL to extract.
            password: Optional document password.

        Returns:
            FetchedDeck with PDF path (API success) or text content (fallback).
        """
        url = self.normalize_url(url)

        # Try DeckExtract API first
        logger.info(f"Attempting DeckExtract API for Papermark: {url}")
        api_result = await self._extract_via_api(url, password)

        if api_result.success:
            logger.info(f"DeckExtract API succeeded for {url}")
            return api_result

        # Fallback to generic extractor
        if self.generic_extractor:
            logger.info(
                f"DeckExtract API failed ({api_result.error}), "
                f"falling back to generic extractor"
            )
            return await self.generic_extractor.extract(url, password)

        return api_result

    async def _enforce_rate_limit(self) -> None:
        """Enforce rate limiting: 5 requests per 30 minutes."""
        now = datetime.now()
        cutoff = now - self._rate_window

        # Remove timestamps outside the window
        PapermarkExtractor._request_timestamps = [
            ts for ts in PapermarkExtractor._request_timestamps
            if ts > cutoff
        ]

        if len(PapermarkExtractor._request_timestamps) >= self._rate_limit:
            oldest = PapermarkExtractor._request_timestamps[0]
            wait_seconds = (oldest + self._rate_window - now).total_seconds()
            if wait_seconds > 0:
                logger.warning(
                    f"DeckExtract rate limit reached, waiting {wait_seconds:.0f}s"
                )
                await asyncio.sleep(wait_seconds)
                # Clean up again after waiting
                PapermarkExtractor._request_timestamps = [
                    ts for ts in PapermarkExtractor._request_timestamps
                    if ts > datetime.now() - self._rate_window
                ]

        PapermarkExtractor._request_timestamps.append(datetime.now())

    async def _extract_via_api(
        self, url: str, password: Optional[str] = None
    ) -> FetchedDeck:
        """Extract using DeckExtract API.

        Handles:
        - Direct PDF response (success)
        - requiresCredentials response (retry with sessionId + credentials)
        - Rate limiting and errors

        Args:
            url: Normalized Papermark URL.
            password: Optional document password.

        Returns:
            FetchedDeck with pdf_path on success.
        """
        try:
            await self._enforce_rate_limit()

            # Don't pass custom email on first attempt — DeckExtract API
            # auto-verifies with system-generated emails. Custom emails
            # trigger manual verification flow.
            payload: dict = {"url": url}
            if password:
                payload["password"] = password

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(_DECKEXTRACT_API_URL, json=payload)

                # Handle credential requirement (retry once)
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")

                    if "application/json" in content_type:
                        data = response.json()
                        if data.get("requiresCredentials"):
                            return await self._retry_with_credentials(
                                client, url, data, password
                            )
                        error_msg = data.get("error", "Unknown API error")
                        return FetchedDeck(
                            url=url, success=False,
                            error=f"DeckExtract API: {error_msg}",
                        )

                    if "application/pdf" in content_type:
                        return self._save_pdf(url, response.content)

                    return FetchedDeck(
                        url=url, success=False,
                        error=f"Unexpected content-type: {content_type}",
                    )

                if response.status_code == 429:
                    return FetchedDeck(
                        url=url, success=False,
                        error="DeckExtract API rate limited",
                    )

                return FetchedDeck(
                    url=url, success=False,
                    error=f"DeckExtract API HTTP {response.status_code}",
                )

        except httpx.TimeoutException:
            return FetchedDeck(
                url=url, success=False,
                error="DeckExtract API timeout (120s)",
            )
        except httpx.HTTPError as e:
            return FetchedDeck(
                url=url, success=False,
                error=f"DeckExtract API connection error: {e}",
            )
        except Exception as e:
            return FetchedDeck(
                url=url, success=False,
                error=f"DeckExtract API error: {e}",
            )

    async def _retry_with_credentials(
        self,
        client: httpx.AsyncClient,
        url: str,
        initial_response: dict,
        password: Optional[str],
    ) -> FetchedDeck:
        """Retry API call with session ID and credentials.

        Args:
            client: Reusable httpx client.
            url: Original URL.
            initial_response: JSON response containing sessionId.
            password: Optional document password.

        Returns:
            FetchedDeck with result.
        """
        session_id = initial_response.get("sessionId")
        if not session_id:
            return FetchedDeck(
                url=url, success=False,
                error="Credentials required but no sessionId returned",
            )

        logger.info(f"Retrying with credentials (sessionId={session_id[:8]}...)")

        await self._enforce_rate_limit()

        payload: dict = {
            "url": url,
            "sessionId": session_id,
        }
        if self.email:
            payload["email"] = self.email
        if password:
            payload["password"] = password

        try:
            response = await client.post(_DECKEXTRACT_API_URL, json=payload)

            if response.status_code == 200:
                content_type = response.headers.get("content-type", "")
                if "application/pdf" in content_type:
                    return self._save_pdf(url, response.content)

            return FetchedDeck(
                url=url, success=False,
                error="Credential retry failed",
            )
        except Exception as e:
            return FetchedDeck(
                url=url, success=False,
                error=f"Credential retry error: {e}",
            )

    def _save_pdf(self, url: str, pdf_bytes: bytes) -> FetchedDeck:
        """Save PDF bytes to disk.

        Args:
            url: Original URL (used for filename hash).
            pdf_bytes: Raw PDF content.

        Returns:
            FetchedDeck with pdf_path on success.
        """
        if not pdf_bytes or len(pdf_bytes) < 100:
            return FetchedDeck(
                url=url, success=False,
                error="API returned empty PDF",
            )

        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        pdf_path = self.output_dir / f"papermark_{url_hash}.pdf"
        pdf_path.write_bytes(pdf_bytes)

        logger.info(f"Saved Papermark PDF: {pdf_path} ({len(pdf_bytes)} bytes)")
        return FetchedDeck(
            url=url,
            success=True,
            pdf_path=pdf_path,
        )
