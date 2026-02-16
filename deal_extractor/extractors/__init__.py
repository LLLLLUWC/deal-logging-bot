"""Content extractors for various deck sources."""

from .base import BaseExtractor
from .docsend import DocSendExtractor
from .generic_web import GenericWebExtractor
from .google_slides import GoogleSlidesExtractor
from .pdf import PDFExtractor

__all__ = [
    "BaseExtractor",
    "DocSendExtractor",
    "GenericWebExtractor",
    "GoogleSlidesExtractor",
    "PDFExtractor",
]
