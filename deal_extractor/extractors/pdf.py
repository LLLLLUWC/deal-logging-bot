"""PDF extraction using pdf2llm.py or direct text extraction."""

import hashlib
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from ..models.types import FetchedDeck, PDFExtractionResult

logger = logging.getLogger(__name__)


class PDFExtractor:
    """Extracts content from PDF files.

    Can use either:
    1. pdf2llm.py script (OCR + markdown generation) - recommended for image-heavy PDFs
    2. Direct text extraction via pypdf - faster for text-based PDFs

    Example:
        extractor = PDFExtractor(
            output_dir=Path("./temp"),
            pdf2llm_path=Path("./pdf2llm.py"),  # Optional
        )
        result = extractor.extract(Path("./deck.pdf"))
        if result.success:
            print(result.text_content)
    """

    def __init__(
        self,
        output_dir: Path,
        pdf2llm_path: Optional[Path] = None,
        ocr_language: str = "chi_sim+eng",
    ):
        """Initialize the PDF extractor.

        Args:
            output_dir: Directory for extraction output.
            pdf2llm_path: Path to pdf2llm.py script. If None, uses direct extraction.
            ocr_language: OCR language code (default: chi_sim+eng).
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.pdf2llm_path = Path(pdf2llm_path) if pdf2llm_path else None
        self.ocr_language = ocr_language

    def extract(
        self, pdf_path: Path, unique_id: Optional[str] = None
    ) -> PDFExtractionResult:
        """Extract content from a PDF file.

        Args:
            pdf_path: Path to the PDF file.
            unique_id: Optional unique identifier for parallel-safe processing.

        Returns:
            PDFExtractionResult with extraction details.
        """
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            return PDFExtractionResult(
                success=False,
                error=f"PDF file not found: {pdf_path}",
            )

        # Generate unique ID if not provided
        if not unique_id:
            path_hash = hashlib.md5(str(pdf_path).encode()).hexdigest()[:8]
            unique_id = f"{path_hash}_{uuid.uuid4().hex[:8]}"

        # Use pdf2llm.py if available, otherwise direct extraction
        if self.pdf2llm_path and self.pdf2llm_path.exists():
            return self._extract_with_pdf2llm(pdf_path, unique_id)
        else:
            return self._extract_direct(pdf_path)

    def _extract_with_pdf2llm(
        self, pdf_path: Path, unique_id: str
    ) -> PDFExtractionResult:
        """Extract using pdf2llm.py script with OCR.

        Args:
            pdf_path: Path to PDF file.
            unique_id: Unique identifier for this extraction.

        Returns:
            PDFExtractionResult with extraction details.
        """
        # Create unique output subdirectory
        unique_output_dir = self.output_dir / f"extract_{unique_id}"
        unique_output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Use "uv run" locally, fall back to "python" in Docker/CI
            import shutil
            if shutil.which("uv"):
                cmd = ["uv", "run", str(self.pdf2llm_path)]
            else:
                cmd = ["python", str(self.pdf2llm_path)]

            result = subprocess.run(
                cmd + [
                    str(pdf_path),
                    "--output",
                    str(unique_output_dir),
                    "--lang",
                    self.ocr_language,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                return PDFExtractionResult(
                    success=False,
                    error=f"pdf2llm.py failed: {result.stderr}",
                )

            # Parse output to find result directory
            result_dir = None
            output_line = result.stdout.strip().split("\n")[0]
            if output_line.startswith("Done: "):
                result_dir = Path(output_line.replace("Done: ", ""))

            # Fallback: find subdirectory
            if not result_dir or not result_dir.exists():
                result_dir = self._find_output_in_dir(unique_output_dir)

            if not result_dir or not result_dir.exists():
                return PDFExtractionResult(
                    success=False,
                    error="Could not find extraction output directory",
                )

            # Read markdown content
            markdown_path = result_dir / "deck.md"
            text_content = ""
            if markdown_path.exists():
                text_content = markdown_path.read_text(encoding="utf-8")

            # Extract title
            title = result_dir.name

            # Find searchable PDF
            searchable_pdf_path = result_dir / "searchable.pdf"
            if not searchable_pdf_path.exists():
                searchable_pdf_path = None

            return PDFExtractionResult(
                success=True,
                title=title,
                text_content=text_content,
                markdown_path=markdown_path,
                searchable_pdf_path=searchable_pdf_path,
                output_dir=result_dir,
            )

        except subprocess.TimeoutExpired:
            return PDFExtractionResult(
                success=False,
                error="PDF extraction timed out after 5 minutes",
            )
        except Exception as e:
            return PDFExtractionResult(
                success=False,
                error=f"PDF extraction failed: {str(e)}",
            )

    def _extract_direct(self, pdf_path: Path) -> PDFExtractionResult:
        """Extract text directly from PDF using pypdf.

        Args:
            pdf_path: Path to PDF file.

        Returns:
            PDFExtractionResult with extraction details.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(pdf_path))
            texts = []

            for page in reader.pages:
                text = page.extract_text() or ""
                text = " ".join(text.split())
                if text:
                    texts.append(text)

            text_content = "\n\n".join(texts)

            # Get title from metadata
            title = None
            meta = reader.metadata or {}
            if hasattr(meta, "title") and meta.title:
                title = str(meta.title).strip()
            elif isinstance(meta, dict) and meta.get("/Title"):
                title = str(meta.get("/Title")).strip()

            if not title or len(title) < 2:
                title = pdf_path.stem

            return PDFExtractionResult(
                success=True,
                title=title,
                text_content=text_content,
            )

        except Exception as e:
            return PDFExtractionResult(
                success=False,
                error=f"Direct PDF extraction failed: {str(e)}",
            )

    def _find_output_in_dir(self, parent_dir: Path) -> Optional[Path]:
        """Find extraction output directory within parent.

        Args:
            parent_dir: The unique parent directory.

        Returns:
            Path to output directory, or None.
        """
        if not parent_dir.exists():
            return None

        dirs = [d for d in parent_dir.iterdir() if d.is_dir()]
        if not dirs:
            if (parent_dir / "deck.md").exists() or (
                parent_dir / "searchable.pdf"
            ).exists():
                return parent_dir
            return None

        if len(dirs) == 1:
            return dirs[0]

        return max(dirs, key=lambda d: d.stat().st_mtime)

    def extract_title_from_pdf(self, pdf_path: Path) -> Optional[str]:
        """Extract title from PDF metadata.

        Args:
            pdf_path: Path to PDF file.

        Returns:
            Title string or None.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(pdf_path))
            meta = reader.metadata or {}

            title = None
            if hasattr(meta, "title"):
                title = meta.title
            elif isinstance(meta, dict):
                title = meta.get("/Title")

            if title and len(str(title).strip()) > 2:
                return str(title).strip()

        except Exception:
            pass

        return None

    def extract_text_preview(self, pdf_path: Path, max_pages: int = 3) -> str:
        """Extract text preview from first few pages.

        Args:
            pdf_path: Path to PDF file.
            max_pages: Maximum pages to extract.

        Returns:
            Combined text from first pages.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(pdf_path))
            texts = []

            for page in reader.pages[:max_pages]:
                text = page.extract_text() or ""
                text = " ".join(text.split())
                if text:
                    texts.append(text)

            return "\n\n".join(texts)

        except Exception:
            return ""
