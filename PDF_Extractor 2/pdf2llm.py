#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf"]
# ///

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from pypdf import PdfReader

TITLE_BLACKLIST = {
    "confidential",
    "disclaimer",
    "copyright",
    "all rights reserved",
    "table of contents",
    "contents",
    "目录",
    "免责声明",
    "保密",
    "版权",
}


def require_tools() -> None:
    required = ["ocrmypdf", "tesseract", "pdftoppm"]
    missing = [tool for tool in required if shutil.which(tool) is None]
    if missing:
        print("Missing required tools: " + ", ".join(missing))
        print("Install with: brew install ocrmypdf tesseract poppler")
        sys.exit(1)


def sanitize_name(name: str) -> str:
    name = re.sub(r"[\s\u00a0]+", " ", name.strip())
    name = re.sub(r"[\\/:*?\"<>|]", "", name)
    name = re.sub(r"[\s]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def ensure_unique_dir(base_dir: Path) -> Path:
    if not base_dir.exists():
        return base_dir
    for i in range(2, 1000):
        candidate = Path(f"{base_dir}-{i}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Too many duplicate directories")


def read_pdf_title(pdf_path: Path) -> Optional[str]:
    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return None
    meta = reader.metadata or {}
    title = getattr(meta, "title", None) if not isinstance(meta, dict) else None
    if not title and isinstance(meta, dict):
        title = meta.get("/Title")
    if not title:
        return None
    title = sanitize_name(str(title))
    if len(title) < 2:
        return None
    return title


def pdf_has_text(pdf_path: Path) -> bool:
    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return False
    total_pages = len(reader.pages)
    if total_pages == 0:
        return False
    text_pages = 0
    total_chars = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        text = text.strip()
        total_chars += len(text)
        if len(text) >= 20:
            text_pages += 1
    avg_chars = total_chars / total_pages
    return text_pages / total_pages >= 0.3 or avg_chars >= 30


def run_command(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("Command failed: " + " ".join(command))


def run_ocr(source_pdf: Path, target_pdf: Path, lang: str) -> None:
    command = [
        "ocrmypdf",
        "--language",
        lang,
        "--skip-text",
        "--rotate-pages",
        "--deskew",
        str(source_pdf),
        str(target_pdf),
    ]
    run_command(command)


def extract_images(pdf_path: Path, pages_dir: Path) -> list[Path]:
    pages_dir.mkdir(parents=True, exist_ok=True)
    prefix = pages_dir / "page"
    command = [
        "pdftoppm",
        "-png",
        "-r",
        "200",
        str(pdf_path),
        str(prefix),
    ]
    run_command(command)
    generated = sorted(pages_dir.glob("page-*.png"))
    renamed = []
    for index, path in enumerate(generated, start=1):
        new_name = pages_dir / f"{index:03d}.png"
        path.rename(new_name)
        renamed.append(new_name)
    return renamed


def extract_text_by_page(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    texts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = re.sub(r"\s+", " ", text).strip()
        texts.append(text)
    return texts


def ocr_image_text(image_path: Path, lang: str) -> str:
    command = [
        "tesseract",
        str(image_path),
        "stdout",
        "-l",
        lang,
        "--psm",
        "6",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def choose_title_from_lines(lines: List[str]) -> Optional[str]:
    best_line = None
    best_score = 0
    for line in lines:
        cleaned = re.sub(r"\s+", " ", line).strip()
        if len(cleaned) < 2:
            continue
        lowered = cleaned.lower()
        if any(bad in lowered for bad in TITLE_BLACKLIST):
            continue
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
        alpha_count = len(re.findall(r"[A-Za-z]", cleaned))
        score = len(cleaned) + cjk_count * 4 + alpha_count * 2
        if score > best_score:
            best_score = score
            best_line = cleaned
    return best_line


def derive_title_from_first_page(image_path: Path, lang: str) -> Optional[str]:
    text = ocr_image_text(image_path, lang)
    if not text:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    candidate = choose_title_from_lines(lines[:12])
    if not candidate:
        candidate = choose_title_from_lines(lines)
    if not candidate:
        return None
    candidate = sanitize_name(candidate)
    if len(candidate) < 2:
        return None
    return candidate


def write_markdown(
    markdown_path: Path, page_texts: list[str], images: list[Path]
) -> None:
    lines = []
    for index, text in enumerate(page_texts, start=1):
        lines.append(f"## Slide {index}")
        if text:
            lines.append(text)
        else:
            lines.append("(No text detected)")
        if index - 1 < len(images):
            image_rel = images[index - 1].relative_to(markdown_path.parent)
            lines.append(f"![Slide {index}]({image_rel.as_posix()})")
        lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert PDF to LLM-friendly bundle")
    parser.add_argument("pdf", type=Path, help="Path to PDF file")
    parser.add_argument(
        "--output", type=Path, default=Path("output"), help="Output directory"
    )
    parser.add_argument("--lang", default="chi_sim+eng", help="OCR language")
    args = parser.parse_args()

    require_tools()

    pdf_path = args.pdf.expanduser().resolve()
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        sys.exit(1)

    output_root = args.output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    base_name = sanitize_name(pdf_path.stem) or f"deck-{int(time.time())}"
    output_dir = ensure_unique_dir(output_root / base_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_pdf = output_dir / "source.pdf"
    shutil.copy2(pdf_path, source_pdf)

    searchable_pdf = output_dir / "searchable.pdf"
    if pdf_has_text(source_pdf):
        shutil.copy2(source_pdf, searchable_pdf)
    else:
        run_ocr(source_pdf, searchable_pdf, args.lang)

    pages_dir = output_dir / "pages"
    images = extract_images(searchable_pdf, pages_dir)

    title = read_pdf_title(source_pdf)
    if not title and images:
        title = derive_title_from_first_page(images[0], args.lang)

    if title:
        new_dir = ensure_unique_dir(output_root / title)
        if new_dir != output_dir:
            output_dir.rename(new_dir)
            output_dir = new_dir
            pages_dir = output_dir / "pages"
            searchable_pdf = output_dir / "searchable.pdf"
            source_pdf = output_dir / "source.pdf"
            images = [pages_dir / image.name for image in images]

    page_texts = extract_text_by_page(searchable_pdf)
    markdown_path = output_dir / "deck.md"
    write_markdown(markdown_path, page_texts, images)

    print(f"Done: {output_dir}")
    print(f"- {source_pdf}")
    print(f"- {searchable_pdf}")
    print(f"- {markdown_path}")


if __name__ == "__main__":
    main()
