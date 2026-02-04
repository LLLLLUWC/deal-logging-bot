#!/usr/bin/env python3
"""Test DocSend extraction with a real URL."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from bot.extractors.docsend_extractor import DocSendExtractor


async def test_docsend(url: str):
    """Test DocSend extraction."""
    email = os.getenv("DOCSEND_EMAIL", "")
    password = os.getenv("DOCSEND_PASSWORD", "")

    print(f"\n{'='*60}")
    print(f"Testing DocSend extraction")
    print(f"{'='*60}")
    print(f"URL: {url}")
    print(f"Email: {email[:5]}...{email[-10:] if email else 'NOT SET'}")
    print(f"Password: {'SET' if password else 'NOT SET'}")
    print(f"{'='*60}\n")

    if not email:
        print("ERROR: DOCSEND_EMAIL not set in .env")
        return

    output_dir = Path("temp/docsend_test")
    output_dir.mkdir(parents=True, exist_ok=True)

    extractor = DocSendExtractor(
        email=email,
        password=password,
        output_dir=output_dir,
    )

    print("Starting extraction...")
    result = await extractor.extract(url)

    print(f"\n{'='*60}")
    print("EXTRACTION RESULT")
    print(f"{'='*60}")
    print(f"Success: {result.success}")
    print(f"Title: {result.title}")
    print(f"Page count: {result.page_count}")
    print(f"PDF path: {result.pdf_path}")
    print(f"Image paths: {len(result.image_paths)} images")

    if result.error:
        print(f"ERROR: {result.error}")

    if result.image_paths:
        print(f"\nImages saved to:")
        for p in result.image_paths[:3]:
            print(f"  - {p}")
        if len(result.image_paths) > 3:
            print(f"  ... and {len(result.image_paths) - 3} more")

    print(f"{'='*60}\n")

    return result


async def test_http_api_debug(url: str):
    """Debug HTTP API extraction step by step."""
    import aiohttp
    import re

    email = os.getenv("DOCSEND_EMAIL", "")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Normalize URL
    url = url.rstrip("/").split("?")[0].split("#")[0]
    if not url.startswith("http"):
        url = "https://" + url

    print(f"\n{'='*60}")
    print("HTTP API DEBUG")
    print(f"{'='*60}")
    print(f"URL: {url}")

    async with aiohttp.ClientSession(headers=headers) as session:
        # Step 1: GET the page
        print("\n[Step 1] GET page HTML...")
        async with session.get(url) as resp:
            print(f"  Status: {resp.status}")
            if resp.status != 200:
                print(f"  ERROR: Unexpected status")
                return

            html = await resp.text()
            print(f"  HTML length: {len(html)} chars")

            # Save HTML for inspection
            debug_path = Path("temp/docsend_debug.html")
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(html)
            print(f"  Saved to: {debug_path}")

        # Parse authenticity_token
        print("\n[Step 2] Parse authenticity_token...")
        token = None
        patterns = [
            r'name="authenticity_token"\s+value="([^"]+)"',
            r'name="authenticity_token"\s+content="([^"]+)"',
            r'"authenticity_token":\s*"([^"]+)"',
            r'<meta[^>]*name="csrf-token"[^>]*content="([^"]+)"',
        ]
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                token = m.group(1)
                print(f"  Found with pattern: {pattern[:40]}...")
                print(f"  Token: {token[:20]}...")
                break

        if not token:
            print("  ERROR: No token found!")
            print("  Checking HTML for clues...")
            if "email" in html.lower():
                print("  - 'email' found in HTML (email gate likely)")
            if "password" in html.lower():
                print("  - 'password' found in HTML")
            if "captcha" in html.lower():
                print("  - 'captcha' found in HTML (BLOCKED!)")
            if "access denied" in html.lower():
                print("  - 'access denied' found in HTML (BLOCKED!)")

        # Parse page count
        print("\n[Step 3] Parse page count...")
        page_count = 0

        # Method 1: page-label
        labels = re.findall(r'class="page-label"[^>]*>(\d+)<', html)
        if labels:
            page_count = max(int(x) for x in labels)
            print(f"  Found via page-label: {page_count}")

        # Method 2: total_pages JSON
        if page_count == 0:
            m = re.search(r'"total_pages":\s*(\d+)', html)
            if m:
                page_count = int(m.group(1))
                print(f"  Found via total_pages: {page_count}")

        # Method 3: num_pages JSON
        if page_count == 0:
            m = re.search(r'"num_pages":\s*(\d+)', html)
            if m:
                page_count = int(m.group(1))
                print(f"  Found via num_pages: {page_count}")

        # Method 4: "X of Y" pattern
        if page_count == 0:
            m = re.search(r'(\d+)\s*(?:of|/)\s*(\d+)\s*(?:pages?)?', html, re.IGNORECASE)
            if m:
                page_count = int(m.group(2))
                print(f"  Found via 'X of Y': {page_count}")

        if page_count == 0:
            print("  ERROR: Could not determine page count!")

        # Parse title
        print("\n[Step 4] Parse title...")
        m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
            print(f"  Raw title: {title}")
            for suffix in [" | DocSend", " - DocSend", " on DocSend"]:
                if suffix in title:
                    title = title.split(suffix)[0].strip()
            print(f"  Clean title: {title}")
        else:
            print("  No title found")

        # Check if we can proceed
        if token and page_count > 0:
            print("\n[Step 5] Try authentication...")
            form_data = {
                "utf8": "✓",
                "authenticity_token": token,
                "link_auth[email]": email,
            }

            async with session.post(
                url,
                data=form_data,
                headers={
                    **headers,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": url,
                },
                allow_redirects=True,
            ) as resp:
                print(f"  Auth POST status: {resp.status}")

            print("\n[Step 6] Try fetching page 1...")
            page_data_url = f"{url}/page_data/1"
            async with session.get(page_data_url) as resp:
                print(f"  page_data/1 status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    print(f"  Response keys: {list(data.keys())}")
                    image_url = data.get("imageUrl") or data.get("image_url")
                    if image_url:
                        print(f"  imageUrl: {image_url[:60]}...")
                    else:
                        print(f"  ERROR: No imageUrl in response!")
                        print(f"  Full response: {data}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    # Test URL from user
    test_url = "https://docsend.com/view/5sms9zynhuhnaivx/d/33kt6ypwsu8tf7v9"

    # Run debug first
    asyncio.run(test_http_api_debug(test_url))

    # Then run full extraction
    asyncio.run(test_docsend(test_url))
