FROM python:3.11-slim

# Install system dependencies for Playwright and OCR
RUN apt-get update && apt-get install -y \
    # Playwright dependencies
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    # OCR dependencies (optional, for pdf2llm)
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    ocrmypdf poppler-utils \
    # General utilities
    git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application code
COPY . .

# Create temp directory
RUN mkdir -p /app/temp

# Run the bot
CMD ["python", "-m", "bot.main"]
