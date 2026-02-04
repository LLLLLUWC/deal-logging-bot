"""Base class for content extractors."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..models.types import FetchedDeck


class BaseExtractor(ABC):
    """Abstract base class for content extractors.

    All extractors must implement the extract() method which takes a URL
    and returns a FetchedDeck containing the extracted content.
    """

    @abstractmethod
    async def extract(
        self, url: str, password: Optional[str] = None
    ) -> FetchedDeck:
        """Extract content from a URL.

        Args:
            url: The URL to extract content from.
            password: Optional password for protected content.

        Returns:
            FetchedDeck with extracted content or error information.
        """
        pass

    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize a URL to canonical form.

        Args:
            url: URL to normalize.

        Returns:
            Normalized URL.
        """
        url = url.rstrip("/")
        url = url.split("?")[0].split("#")[0]
        if not url.startswith("http"):
            url = "https://" + url
        return url
