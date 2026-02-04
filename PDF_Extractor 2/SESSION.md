# Session

## Project Snapshot
- Goal: Convert DocSend-style image PDFs into LLM-friendly outputs.
- Current scope: `pdf2llm` CLI only (PDF → searchable PDF + Markdown).
- Non-goals: DocSend link automation, summarization, or content analysis.

## Problem Context
- Many DocSend downloads are image-only PDFs, so LLMs cannot extract text.
- Solution: OCR the PDF to add a text layer, then export Markdown per slide.

## How to Run
1) Install system dependencies (macOS):
   - `brew install uv ocrmypdf poppler tesseract tesseract-lang`
2) Convert a deck:
   - `uv run pdf2llm.py "/path/to/deck.pdf"`

## Output Bundle
- `output/<deck_name>/source.pdf`
- `output/<deck_name>/searchable.pdf`
- `output/<deck_name>/deck.md`
- `output/<deck_name>/pages/001.png ...`

## Current Status
- `pdf2llm.py` implemented with auto-naming (PDF metadata → first-page OCR → filename).
- `README.md` documents setup and usage.
- GitHub remote is configured and pushed.
- `.gitignore` excludes `output/` and local caches.

## Decisions & Rationale
- Use `uv` to avoid polluting system Python.
- Use `ocrmypdf` + `tesseract` + `poppler` for stable OCR and searchable PDF output.
- Prefer raw OCR text in `deck.md` (no summarization).

## Next Steps
- Add `docsend2llm` with screenshot flow (DocSend default download disabled).
- Consider hot-folder or Finder quick action for zero-typing usage.
- Improve title extraction heuristics for edge cases.

## Operational Notes
- Avoid committing `output/` to Git.
- Commit small, verifiable changes with clear messages.
- Use `git pull` before switching machines, `git push` after finishing work.
