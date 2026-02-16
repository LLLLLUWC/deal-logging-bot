# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Environment

**IMPORTANT**: This project uses `uv` for Python environment management. Always use `uv run` to execute Python commands:

```bash
# Correct
uv run python -m bot.main
uv run python script.py
uv run pytest

# Incorrect (don't use these)
python script.py
python3 script.py
```

## Project Overview

This repository contains three related components:

1. **Deal Logging Bot** (`bot/`): A Telegram bot that monitors group messages and automatically logs VC deals to Notion
2. **Deal Extractor Module** (`deal_extractor/`): An **independent, reusable library** for extracting deal information from messages and decks (v0.1.0)
3. **PDF Extractor** (`PDF_Extractor 2/`): A CLI tool that converts image-only PDFs into LLM-friendly outputs (used by the bot)

---

## Deal Extractor Module (Independent Library)

### Overview

`deal_extractor/` is a **standalone, reusable module** that can be extracted and used in other projects. It provides:

- URL detection and classification (zero external dependencies)
- Content extraction from DocSend, Google Slides/Docs, PDF URLs
- Two-stage LLM analysis (Router + Extractor)
- Structured deal information output with token usage tracking

### Architecture

```
deal_extractor/
├── __init__.py              # Main DealExtractor class
├── models/
│   └── types.py             # Data types (Deal, ExtractionResult, FetchedDeck)
├── links/
│   └── detector.py          # URL detection (ZERO external deps)
├── extractors/
│   ├── base.py              # Abstract base class
│   ├── docsend.py           # DocSend extraction (API + Playwright)
│   ├── google_slides.py     # Google Slides/Docs export
│   └── pdf.py               # PDF text extraction
└── llm/
    ├── extractor.py         # Two-stage LLM extractor
    └── prompts.py           # Prompt templates & tags
```

### Usage

```python
from deal_extractor import DealExtractor

extractor = DealExtractor(
    llm_api_key="sk-xxx",
    llm_model="kimi-k2.5",
    llm_base_url="https://api.moonshot.cn/v1",  # Optional, defaults to Kimi
    docsend_email="your@email.com",
    temp_dir=Path("./temp"),
)

result = await extractor.extract(
    text="Check this deal: https://docsend.com/view/xxx",
    sender="John",
)

if result.success:
    for deal in result.deals:
        print(f"Company: {deal.company_name}")
        print(f"Tags: {deal.tags}")
        print(f"Intro: {deal.intro}")
        print(f"Deck: {deal.deck_url}")
```

### Dependencies (for standalone use)

```
openai          # LLM API client (OpenAI-compatible)
aiohttp         # DocSend API calls
httpx           # Google Slides/PDF downloads
Pillow          # Image processing
pypdf           # PDF text extraction

# Optional
img2pdf         # Image to PDF conversion
playwright      # Browser automation (DocSend fallback)
playwright-stealth  # Anti-detection
```

### Moving to Another Project

To use `deal_extractor/` independently:

1. Copy the entire `deal_extractor/` directory
2. Install required dependencies (see above)
3. Optionally copy `PDF_Extractor 2/pdf2llm.py` if you need OCR capabilities
4. The `pdf2llm_path` parameter is optional - without it, uses `pypdf` for direct text extraction

---

## Deal Logging Bot

### Purpose

Automatically monitor a Telegram group for deal-related messages, extract relevant information (company name, tags, deck content), and create entries in a Notion database.

### Architecture

The bot uses a clean, modular architecture that delegates all extraction logic to `deal_extractor/`:

```
bot/
├── main.py                    # Bot entry point, initializes DealExtractor
├── config.py                  # Configuration management
├── handlers/
│   └── message_handler.py     # Telegram message processing
├── notion/
│   └── client.py              # Notion API integration
├── utils/
│   └── grouping.py            # Message grouping logic
└── analysis/                  # Analysis tools (optional)
    ├── telegram_analyzer.py   # Deep analysis of message patterns
    └── replay_test.py         # Replay messages through bot logic
```

### Processing Flow

```
Telegram Message
    ↓
MessageHandler.handle_message()
    ↓
MessageGrouper (groups related messages)
    ↓
_on_group_ready() callback
    ↓
DealExtractor.extract()
    ├→ LinkDetector.get_all_deck_links()
    ├→ Parallel fetch: DocSendExtractor, PDFExtractor, GoogleExtractor
    ├→ LLMExtractor._run_router() [Stage 1: Is this a deal?]
    └→ LLMExtractor._run_extractor() [Stage 2: Extract info]
    ↓
NotionClient.create_deal_with_retry()
    ↓
Telegram reply with confirmation
```

### Running the Bot

```bash
# Install dependencies
uv pip install -r requirements.txt

# Install Playwright browsers (for DocSend fallback)
uv run playwright install chromium

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys

# Run the bot
uv run python -m bot.main
```

### Environment Variables

```bash
# Required
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_GROUP_ID=-1001234567890
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=your_database_id
KIMI_API_KEY=sk-xxx                  # Moonshot AI API key

# Optional
KIMI_MODEL=kimi-k2.5                 # LLM model (default: kimi-k2.5)
DOCSEND_EMAIL=team@company.com       # For DocSend gates
DOCSEND_PASSWORD=xxx                 # For password-protected decks
DOCSEND_EXTRACTION_MODE=auto         # "auto" (API first, Playwright fallback) or "playwright"
MESSAGE_GROUPING_TIMEOUT=30          # Seconds before processing
OCR_LANGUAGE=chi_sim+eng             # Tesseract language
TELEGRAM_PROXY=socks5://127.0.0.1:1080  # Proxy for China

# Cleanup (important for cloud deployment)
CLEANUP_AFTER_EXTRACT=false          # Keep files until periodic cleanup
CLEANUP_MAX_AGE_MINUTES=1440         # Delete files older than 24 hours
CLEANUP_INTERVAL_MINUTES=1440        # Run periodic cleanup every 24 hours
```

### Notion Database Schema

The bot expects these properties:

| Property | Type | Description |
|----------|------|-------------|
| Name | title | Company/project name |
| Tags | rich_text | Comma-separated tags |
| OP Source | multi_select | Team member who posted |
| External Source | rich_text | Person who referred the deal |
| Deck | files | Pitch deck link/file |
| Intro | rich_text | Short intro (140 chars) |
| Memo | rich_text | Additional notes |

### DocSend Extraction

**Primary Method: docsend2pdf.com API** (handles CAPTCHA internally)

```
1. POST to https://docsend2pdf.com/api/convert
2. Returns PDF bytes directly
3. Rate limited: 5 req/s
```

**Fallback: Playwright Browser Automation**

- Used when API fails
- Supports cookie persistence for session reuse
- Run `python setup_docsend_cookies.py` to manually solve CAPTCHA once

### Supported Link Types

| Type | Domains | Deck? | Priority |
|------|---------|-------|----------|
| DocSend | docsend.com | Yes | 100 |
| Papermark | papermark.io, papermark.com | Yes | 90 |
| Pitch.com | pitch.com | Yes | 88 |
| PDF Direct | *.pdf | Yes | 85 |
| Google Drive | drive.google.com, docs.google.com | Yes | 70 |
| Dropbox | dropbox.com | Yes | 60 |
| Notion | notion.so, notion.site | No | 50 |
| Loom | loom.com | Yes | 40 |
| YouTube | youtube.com, youtu.be | No | 35 |
| Dune | dune.com | No | 15 |
| Website | (general URLs) | No | 10 |
| LinkedIn | linkedin.com | No | 5 |
| Twitter | twitter.com, x.com | No | 5 |
| Calendar | cal.com, calendly.com | No | 3 |
| Unknown | (unrecognized) | No | 1 |

LinkDetector also automatically unwraps redirect/tracking URLs from services like getcabal.com, Mailchimp, HubSpot, SendGrid, etc.

### Message Grouping

Related messages are grouped together using:
- Thread/reply detection
- Same sender within 2 minutes
- 3-second quick timeout for single messages
- 30-second max timeout before processing

### Multi-Deal Handling

**Scenario 1: Multiple deck links for ONE deal (pitch + memo)**
- Primary deck link used for Notion entry
- Additional URLs listed in Introduction field

**Scenario 2: Multiple deals in one message (numbered list)**
- LLM detects numbered patterns
- Creates separate Notion entries
- Each deal associated with respective deck URL

### Temporary File Cleanup (Cloud-Ready)

The bot includes automatic cleanup of temporary files, essential for cloud deployment:

**Periodic Cleanup** (default, background task)
- Runs every 24 hours (`CLEANUP_INTERVAL_MINUTES=1440`)
- Deletes files older than 24 hours (`CLEANUP_MAX_AGE_MINUTES=1440`)
- Catches orphaned files from crashes
- Reports temp directory size in logs
- Keeps files around for debugging if needed

**Optional Immediate Cleanup** (`CLEANUP_AFTER_EXTRACT=true`)
- PDFs are deleted right after text extraction
- For memory-constrained environments

**Protected Files:**
- `docsend_cookies.json` - Session cookies, never deleted
- Empty directories are automatically removed

**Programmatic Cleanup:**
```python
# Manual cleanup
extractor.cleanup_old_files(max_age_minutes=60)

# Check temp directory size
total_bytes, file_count = extractor.get_temp_dir_size()
```

---

## Docker Deployment

### Files

- `Dockerfile` - Python 3.11 slim image with Playwright + OCR dependencies
- `docker-compose.yml` - Container orchestration with volume mounts
- `.dockerignore` - Excludes .env, __pycache__, .venv, etc.

### VPS Deployment

```bash
# 1. Clone repository (requires public repo or SSH key)
git clone https://github.com/LLLLLUWC/deal-logging-bot.git
cd deal-logging-bot

# 2. Create environment file
cp .env.example .env
nano .env  # Fill in API keys

# 3. Create cookie file (for DocSend session persistence)
touch docsend_cookies.json

# 4. Start the bot
docker-compose up -d

# 5. View logs
docker-compose logs -f
```

### Switching Environments (Test → Production)

To switch to a new Telegram group and/or Notion workspace:

1. **Get new Telegram Group ID**
   - Add bot to the new group
   - Send a message, then visit: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find `"chat":{"id":-100xxxxxxxxxx}`

2. **Create new Notion Integration** (if changing accounts)
   - Go to https://www.notion.so/my-integrations
   - Create new integration, copy the `secret_xxx` token
   - Create database with required schema (see Notion Database Schema section)
   - Connect integration to database via **···** → **Connections**

3. **Update .env on VPS**
   ```bash
   nano .env
   # Update: TELEGRAM_GROUP_ID, NOTION_API_KEY, NOTION_DATABASE_ID
   ```

4. **Restart**
   ```bash
   docker-compose down
   docker-compose up -d
   ```

### Docker Commands Reference

```bash
docker-compose up -d      # Start in background
docker-compose down       # Stop
docker-compose logs -f    # Follow logs
docker-compose restart    # Restart
docker-compose pull       # Update image (after git pull)
docker-compose up -d --build  # Rebuild after code changes
```

---

## PDF Extractor (pdf2llm.py)

### Purpose

Convert image-only PDFs (commonly from DocSend downloads) into LLM-friendly outputs with OCR.

### System Dependencies

```bash
brew install uv ocrmypdf poppler tesseract tesseract-lang
```

### Usage

```bash
# Basic usage
uv run "PDF_Extractor 2/pdf2llm.py" "/path/to/deck.pdf"

# Custom output directory
uv run "PDF_Extractor 2/pdf2llm.py" "~/Downloads/demo.pdf" --output ./output

# Custom OCR language
uv run "PDF_Extractor 2/pdf2llm.py" "/path/to/deck.pdf" --lang eng
```

### Output Bundle

```
output/<deck_name>/
├── source.pdf          # Original PDF copy
├── searchable.pdf      # OCR'd version
├── deck.md             # Markdown with per-slide text
└── pages/
    ├── 001.png         # Page images at 200 DPI
    └── ...
```

---

## Analysis Tools

Tools to analyze historical Telegram messages and measure classification accuracy.

### Architecture

```
bot/analysis/
├── telegram_analyzer.py       # Deep analysis of message patterns
├── replay_test.py             # Replay messages through bot logic
└── test_multi_deal.py         # Unit tests for multi-deck detection
```

Root-level scripts:
- `analyze_export.py` - CLI entry point for analysis
- `expected_deals_template.csv` - Template for accuracy testing (F1 score calculation)

### Usage

```bash
# Basic analysis
python analyze_export.py /path/to/ChatExport/result.json

# Export as JSON
python analyze_export.py /path/to/result.json -o analysis_output.json

# Replay test with accuracy measurement
python analyze_export.py /path/to/result.json --replay -e expected_deals.csv -o results.csv
```

---

## Key Design Decisions

1. **Modular Architecture** - `deal_extractor/` is independent and reusable; `bot/` is a thin wrapper
2. **Kimi (Moonshot) API** for LLM - Cost-effective, supports vision, accessible in China
3. **Deterministic Link Detection** - URLs extracted before LLM, no hallucination risk
4. **Two-Stage Agent Architecture** - Router filters non-deals cheaply, Extractor only runs when needed
5. **docsend2pdf.com API** - Primary DocSend extraction, handles CAPTCHA internally
6. **Parallel Processing** - Message groups processed concurrently with unique IDs preventing race conditions
7. **Per-Deal External Source** - Each deal can have its own referrer (for multi-deal messages)
8. **Cloud-Ready Cleanup** - Automatic temp file cleanup with immediate + periodic strategies

---

## Development Notes

### Git Workflow
- `PDF_Extractor 2/` has its own Git repository
- Main project files (bot/) are in parent directory
- `output/` and `temp/` directories are gitignored

### Testing
- Test by sending messages to target Telegram group
- Monitor logs for processing status
- Verify Notion entries are created correctly
- Use analysis tools for accuracy measurement

### Common Issues

| Issue | Solution |
|-------|----------|
| DocSend extraction fails | Check docsend2pdf.com API, fallback to Playwright |
| OCR quality issues | Try different `--lang` options |
| Notion API errors | Verify database schema matches expected properties |
| Telegram timeout | Configure `TELEGRAM_PROXY` for China |
| Rate limiting | docsend2pdf.com has 5 req/s limit |

---

## Available Tags

Tags are validated against this list (defined in `deal_extractor/llm/prompts.py`):

```
DeFi, AI, Gaming, Infrastructure, SocialFi, NFT, DAO, L1/L2,
Privacy, Data, Payments, Enterprise, Consumer, Developer Tools, Research
```
