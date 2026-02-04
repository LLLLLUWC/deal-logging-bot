"""URL extraction and classification utilities.

This module has ZERO external dependencies - it only uses Python standard library.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse


class LinkType(Enum):
    """Types of links that can be detected in messages."""

    DOCSEND = "docsend"
    PAPERMARK = "papermark"
    PITCH_COM = "pitch_com"
    NOTION = "notion"
    GOOGLE_DRIVE = "google_drive"
    DROPBOX = "dropbox"
    PDF_DIRECT = "pdf_direct"
    LOOM = "loom"
    YOUTUBE = "youtube"
    DUNE = "dune"
    CALENDAR = "calendar"
    WEBSITE = "website"
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    UNKNOWN = "unknown"


@dataclass
class DetectedLink:
    """A detected link with its classification."""

    url: str
    link_type: LinkType
    is_deck: bool
    priority: int

    def __lt__(self, other: "DetectedLink") -> bool:
        """Sort by priority (higher first)."""
        return self.priority > other.priority


class LinkDetector:
    """Detects and classifies URLs in message text.

    This class is designed to be used standalone with no external dependencies.

    Example:
        detector = LinkDetector()
        links = detector.detect_links("Check this: https://docsend.com/view/xxx")
        for link in links:
            print(f"{link.url} - {link.link_type.value} - deck: {link.is_deck}")
    """

    # URL regex pattern
    URL_PATTERN = re.compile(
        r"https?://[^\s<>\"')\]]+",
        re.IGNORECASE,
    )

    # Domain patterns for classification
    DOMAIN_PATTERNS = {
        LinkType.DOCSEND: [r"docsend\.com"],
        LinkType.PAPERMARK: [r"papermark\.io", r"papermark\.com"],
        LinkType.PITCH_COM: [r"pitch\.com"],
        LinkType.NOTION: [r"notion\.so", r"notion\.site"],
        LinkType.GOOGLE_DRIVE: [r"drive\.google\.com", r"docs\.google\.com"],
        LinkType.DROPBOX: [r"dropbox\.com"],
        LinkType.LOOM: [r"loom\.com"],
        LinkType.YOUTUBE: [r"youtube\.com", r"youtu\.be"],
        LinkType.DUNE: [r"dune\.com"],
        LinkType.CALENDAR: [r"cal\.com", r"calendly\.com"],
        LinkType.LINKEDIN: [r"linkedin\.com"],
        LinkType.TWITTER: [r"twitter\.com", r"x\.com"],
    }

    # Deck-related path patterns
    DECK_PATH_PATTERNS = [
        r"/deck",
        r"/pitch",
        r"/presentation",
        r"/investor",
        r"/fundrais",
    ]

    # Priority mapping (higher = more important for deck extraction)
    PRIORITY_MAP = {
        LinkType.DOCSEND: 100,
        LinkType.PAPERMARK: 90,
        LinkType.PITCH_COM: 88,
        LinkType.PDF_DIRECT: 85,
        LinkType.GOOGLE_DRIVE: 70,
        LinkType.DROPBOX: 60,
        LinkType.NOTION: 50,
        LinkType.LOOM: 40,
        LinkType.YOUTUBE: 35,
        LinkType.DUNE: 15,
        LinkType.WEBSITE: 10,
        LinkType.CALENDAR: 3,
        LinkType.LINKEDIN: 5,
        LinkType.TWITTER: 5,
        LinkType.UNKNOWN: 1,
    }

    # Known redirect/tracking domains
    REDIRECT_DOMAINS = [
        r"getcabal\.com",
        r"click\.",
        r"track\.",
        r"redirect\.",
        r"link\.",
        r"go\.",
        r"mailchimp\.com",
        r"hubspot\.com",
        r"sendgrid\.net",
    ]

    def extract_urls(self, text: str) -> list[str]:
        """Extract all URLs from text.

        Args:
            text: Message text to search for URLs.

        Returns:
            List of URLs found in the text.
        """
        if not text:
            return []
        return self.URL_PATTERN.findall(text)

    def extract_url_from_redirect(self, url: str) -> Optional[str]:
        """Extract the actual target URL from a redirect/tracking URL.

        Common patterns:
        - https://getcabal.com/...?url=https%3A%2F%2Fdocsend.com%2F...
        - https://click.mailchimp.com/...?url=...

        Args:
            url: A potential redirect URL.

        Returns:
            The extracted target URL, or None if not a redirect.
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Check if this is a known redirect domain
            is_redirect = any(
                re.search(pattern, domain) for pattern in self.REDIRECT_DOMAINS
            )

            if not is_redirect:
                return None

            # Try to extract URL from query parameters
            query_params = parse_qs(parsed.query)
            url_params = [
                "url",
                "redirect",
                "target",
                "dest",
                "destination",
                "link",
                "goto",
            ]

            for param in url_params:
                if param in query_params:
                    target_url = query_params[param][0]
                    target_url = unquote(target_url)
                    if target_url.startswith("http"):
                        return target_url

            return None

        except Exception:
            return None

    def classify_url(self, url: str) -> LinkType:
        """Classify a URL by its domain and path.

        Args:
            url: URL to classify.

        Returns:
            LinkType classification.
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            path = parsed.path.lower()

            # Check domain patterns
            for link_type, patterns in self.DOMAIN_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, domain):
                        return link_type

            # Check for direct PDF links
            if path.endswith(".pdf"):
                return LinkType.PDF_DIRECT

            return LinkType.WEBSITE

        except Exception:
            return LinkType.UNKNOWN

    def is_deck_link(self, url: str, link_type: LinkType) -> bool:
        """Determine if a link is likely a pitch deck.

        Args:
            url: The URL to check.
            link_type: Pre-classified link type.

        Returns:
            True if the link is likely a pitch deck.
        """
        # These are always deck links
        if link_type in (LinkType.DOCSEND, LinkType.PAPERMARK, LinkType.PITCH_COM):
            return True

        # Direct PDF links are likely decks
        if link_type == LinkType.PDF_DIRECT:
            return True

        # Loom videos are often pitch demos
        if link_type == LinkType.LOOM:
            return True

        # Google Slides presentations are deck links
        if link_type == LinkType.GOOGLE_DRIVE:
            if "/presentation/" in url:
                return True
            if "/document/" in url:
                return True

        # Calendar links are never decks
        if link_type == LinkType.CALENDAR:
            return False

        # Check path for deck-related keywords
        try:
            parsed = urlparse(url)
            path = parsed.path.lower()
            for pattern in self.DECK_PATH_PATTERNS:
                if re.search(pattern, path):
                    return True
        except Exception:
            pass

        return False

    def detect_links(self, text: str) -> list[DetectedLink]:
        """Extract and classify all links from text.

        Args:
            text: Message text to analyze.

        Returns:
            List of DetectedLink objects, sorted by priority (highest first).
        """
        urls = self.extract_urls(text)
        links = []
        seen_urls = set()

        for url in urls:
            # Clean URL
            url = url.rstrip(".,;:!?")

            # Check if this is a redirect URL
            target_url = self.extract_url_from_redirect(url)
            if target_url:
                url = target_url

            # Skip duplicates
            if url in seen_urls:
                continue
            seen_urls.add(url)

            link_type = self.classify_url(url)
            is_deck = self.is_deck_link(url, link_type)
            priority = self.PRIORITY_MAP.get(link_type, 1)

            # Boost priority if it's a deck link
            if is_deck:
                priority += 50

            links.append(
                DetectedLink(
                    url=url,
                    link_type=link_type,
                    is_deck=is_deck,
                    priority=priority,
                )
            )

        return sorted(links)

    def get_best_deck_link(self, text: str) -> Optional[DetectedLink]:
        """Get the highest priority deck link from text.

        Args:
            text: Message text to analyze.

        Returns:
            The best deck link, or None if no deck links found.
        """
        links = self.detect_links(text)
        deck_links = [link for link in links if link.is_deck]
        return deck_links[0] if deck_links else None

    def get_all_deck_links(self, text: str) -> list[DetectedLink]:
        """Get all deck links from text, sorted by priority.

        Args:
            text: Message text to analyze.

        Returns:
            List of deck links, sorted by priority (highest first).
        """
        links = self.detect_links(text)
        return [link for link in links if link.is_deck]

    def has_multiple_decks(self, text: str) -> bool:
        """Check if text contains multiple deck links.

        Args:
            text: Message text to analyze.

        Returns:
            True if there are 2+ deck links.
        """
        return len(self.get_all_deck_links(text)) > 1

    def get_non_deck_links(self, text: str) -> list[DetectedLink]:
        """Get all non-deck links from text.

        Args:
            text: Message text to analyze.

        Returns:
            List of non-deck links.
        """
        links = self.detect_links(text)
        return [link for link in links if not link.is_deck]
