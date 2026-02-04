# docsend2pdf

Convert DocSend decks into LLM-friendly outputs (PDF → searchable PDF + Markdown).

## Quick Start (New Mac)

1) Install Homebrew
2) Install dependencies

```bash
brew install uv ocrmypdf poppler tesseract tesseract-lang
```

3) Run the converter

```bash
uv run pdf2llm.py "/path/to/deck.pdf"
```

## Output

The tool creates a bundle under `./output/<deck_name>/`:

- `source.pdf`
- `searchable.pdf`
- `deck.md`
- `pages/001.png ...`

## Notes

- Auto-naming priority: PDF metadata title → first-page OCR → filename.
- Default OCR language: `chi_sim+eng` (override with `--lang`).
- Custom output root: `--output /path/to/output`.

## Example

```bash
uv run pdf2llm.py "~/Downloads/demo.pdf" --output ./output
```
