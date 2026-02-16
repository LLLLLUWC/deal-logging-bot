"""Data types for deal extraction."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Deal:
    """A single extracted deal."""

    company_name: str
    tags: list[str] = field(default_factory=list)
    intro: str = ""
    detailed_content: str = ""
    deck_url: Optional[str] = None
    external_source: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "company_name": self.company_name,
            "tags": self.tags,
            "intro": self.intro,
            "detailed_content": self.detailed_content,
            "deck_url": self.deck_url,
            "external_source": self.external_source,
        }


@dataclass
class ExtractionResult:
    """Result from DealExtractor.extract()."""

    success: bool
    deals: list[Deal] = field(default_factory=list)
    error: Optional[str] = None
    skipped_reason: Optional[str] = None

    # Token usage tracking
    router_tokens: int = 0
    extractor_tokens: int = 0
    total_tokens: int = 0

    # Stats
    decks_detected: int = 0
    decks_fetched: int = 0

    # Review flag
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


@dataclass
class FetchedDeck:
    """Content fetched from a deck URL."""

    url: str
    success: bool
    content: Optional[str] = None
    title: Optional[str] = None
    error: Optional[str] = None
    pdf_path: Optional[Path] = None


@dataclass
class RouterDecision:
    """Decision from the Router Agent (Stage 1)."""

    is_deal: bool
    confidence: float = 0.0
    reason: str = ""
    company_hints: list[str] = field(default_factory=list)
    is_multi_deal: bool = False


@dataclass
class PDFExtractionResult:
    """Result of PDF extraction."""

    success: bool
    title: Optional[str] = None
    text_content: str = ""
    markdown_path: Optional[Path] = None
    searchable_pdf_path: Optional[Path] = None
    output_dir: Optional[Path] = None
    error: Optional[str] = None


@dataclass
class DocSendExtractionResult:
    """Result of DocSend extraction."""

    success: bool
    pdf_path: Optional[Path] = None
    page_count: int = 0
    title: Optional[str] = None
    error: Optional[str] = None
    image_paths: list[Path] = field(default_factory=list)
