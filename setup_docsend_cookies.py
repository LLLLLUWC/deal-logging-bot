#!/usr/bin/env python3
"""
Manual DocSend cookie setup utility.

Run this script to manually solve CAPTCHA once and save cookies.
The bot will then use these cookies for subsequent requests.

Usage:
    python setup_docsend_cookies.py

This will:
1. Open a browser window to DocSend
2. Let you manually solve the CAPTCHA and enter email
3. Save the cookies for the bot to use
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


async def setup_cookies():
    from playwright.async_api import async_playwright

    email = os.getenv("DOCSEND_EMAIL", "")

    # Use project temp directory to match DocSendExtractor's path in main.py
    project_root = Path(__file__).parent
    output_dir = project_root / "temp" / "docsend_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = output_dir / "docsend_cookies.json"

    print("=" * 60)
    print("DocSend Cookie Setup")
    print("=" * 60)
    print(f"Email: {email}")
    print(f"Cookie file: {cookie_file}")
    print()
    print("Instructions:")
    print("1. A browser window will open")
    print("2. Solve the CAPTCHA if prompted")
    print("3. Enter your email if prompted")
    print("4. Wait for the deck to load")
    print("5. Press Enter in this terminal to save cookies")
    print("=" * 60)
    print()

    test_url = input("Enter a DocSend URL to test (or press Enter for default): ").strip()
    if not test_url:
        test_url = "https://docsend.com/view/4yu3kq4cziqa3bbb"

    print(f"\nOpening: {test_url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # Show browser for manual interaction
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        page = await context.new_page()
        await page.goto(test_url)

        print("\n" + "=" * 60)
        print("BROWSER OPENED")
        print("=" * 60)
        print("Please:")
        print("1. Solve any CAPTCHA shown")
        print("2. Enter your email if prompted")
        print("3. Wait for the deck content to load")
        print()
        input("Press Enter when you can see the deck content...")

        # Save cookies
        await context.storage_state(path=str(cookie_file))
        print(f"\n✅ Cookies saved to: {cookie_file}")

        # Verify we can see content
        page_count = 0
        for selector in [
            ".page-thumbnail",
            ".slide-thumbnail",
            '[data-testid="thumbnail"]',
        ]:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    page_count = count
                    break
            except Exception:
                continue

        if page_count > 0:
            print(f"✅ Detected {page_count} pages - cookies should work!")
        else:
            print("⚠️  Could not detect page count - cookies might not work")

        await browser.close()

    print()
    print("=" * 60)
    print("Setup complete!")
    print("The bot should now be able to access DocSend without CAPTCHA.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(setup_cookies())
