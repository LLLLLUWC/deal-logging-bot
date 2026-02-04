# Deal Logging Bot

An automatic Telegram-to-Notion deal logging system for VC firms. The bot monitors Telegram group messages, extracts deal information (including pitch decks), and creates entries in a Notion database.

## Features

- **Automatic monitoring**: Process all messages in a Telegram group
- **Smart message grouping**: Combine multi-message deals using thread detection and LLM analysis
- **Deck extraction**: Support for DocSend links and direct PDF files
- **LLM-powered analysis**: Extract company names, tags, and sources using Claude
- **Notion integration**: Automatically create deal entries with all extracted information

## Quick Start

### 1. Install Dependencies

```bash
# Python dependencies
pip install -r requirements.txt

# Playwright browsers (for DocSend automation)
playwright install chromium

# System dependencies (macOS)
brew install uv ocrmypdf poppler tesseract tesseract-lang
```

### 2. Create Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the bot token
4. Add your bot to the target Telegram group
5. Get the group ID (you can use [@raw_data_bot](https://t.me/raw_data_bot))

### 3. Set Up Notion Integration

1. Go to [Notion Integrations](https://www.notion.so/my-integrations)
2. Create a new integration
3. Copy the API key
4. Share your target database with the integration
5. Copy the database ID from the database URL

### 4. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_GROUP_ID=-1001234567890
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=your_database_id
ANTHROPIC_API_KEY=sk-ant-xxx
DOCSEND_EMAIL=team@company.com
```

### 5. Run the Bot

```bash
python -m bot.main
```

## Notion Database Schema

Create a Notion database with these properties:

| Property | Type | Description |
|----------|------|-------------|
| Title | Title | Company/project name |
| Tags | Multi-select | Categories (DeFi, AI, Gaming, etc.) |
| OP Source | Select/Text | Team member who posted the deal |
| External Source | Text | Person who referred the deal |
| Deck | URL | Link to pitch deck |
| Introduction | Rich Text | Original message content |

## How It Works

### Message Flow

```
Telegram Message
       ↓
Message Handler (filter & buffer)
       ↓
Message Grouper (combine related messages)
       ↓
Deal Processor
  ├── Link Detection (DocSend, PDF, etc.)
  ├── Content Extraction
  │   ├── PDF: Download → OCR → Extract text
  │   └── DocSend: Browser automation → Screenshot → OCR
  └── LLM Analysis (company name, tags, source)
       ↓
Notion Client (create entry)
       ↓
Confirmation Reply
```

### DocSend Extraction

For DocSend links, the bot uses Playwright to:
1. Navigate to the DocSend URL
2. Handle email/password gates automatically
3. Screenshot each page of the deck
4. Combine screenshots into a PDF
5. OCR the PDF to extract text

### Message Grouping

Related messages are grouped together using:
- Thread/reply detection
- Same sender within 60 seconds
- 30-second timeout before processing

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `MESSAGE_GROUPING_TIMEOUT` | 30 | Seconds to wait before processing a message group |
| `OCR_LANGUAGE` | chi_sim+eng | Tesseract language code for OCR |

## Project Structure

```
Deal_logging_bot/
├── bot/
│   ├── main.py              # Entry point
│   ├── config.py            # Configuration
│   ├── handlers/            # Telegram handlers
│   ├── extractors/          # Content extractors
│   ├── notion/              # Notion integration
│   └── utils/               # Utilities
├── PDF_Extractor 2/
│   └── pdf2llm.py           # PDF processing tool
├── requirements.txt
├── .env.example
└── README.md
```

## Development

### Running in Development

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
python -m bot.main
```

### Testing Individual Components

```python
# Test PDF extraction
from bot.extractors.pdf_extractor import PDFExtractor
extractor = PDFExtractor(pdf2llm_path, output_dir)
result = extractor.extract(pdf_path)

# Test Notion client
from bot.notion.client import NotionClient, DealEntry
client = NotionClient(api_key, database_id)
client.create_deal(DealEntry(title="Test", tags=["AI"]))
```

## Troubleshooting

### Bot not receiving messages

- Ensure the bot is added to the group as a member
- Check that `TELEGRAM_GROUP_ID` is correct (should be negative for groups)
- Verify the bot has permission to read messages

### DocSend extraction fails

- DocSend may have updated their UI - selectors may need updating
- Some decks require passwords - ensure `DOCSEND_PASSWORD` is set if needed
- Rate limiting may occur - the bot waits 2 seconds between pages

### Notion entry not created

- Verify the database is shared with your integration
- Check that property names in the database match the expected schema
- Review logs for API error messages

## License

MIT
