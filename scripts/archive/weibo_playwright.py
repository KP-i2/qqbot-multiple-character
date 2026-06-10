import asyncio
import json
import re
import os
import sys
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'D:\Agent+LLM_results\skill_communication'
UID = '7382396909'
URL = f'https://m.weibo.cn/u/{UID}'

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
            viewport={'width': 390, 'height': 844},
        )
        page = await context.new_page()
        
        print("Navigating to profile page...")
        await page.goto(URL, wait_until='networkidle', timeout=60000)
        await asyncio.sleep(5)
        
        # Check current URL (might have been redirected to login)
        current_url = page.url
        print(f"Current URL: {current_url}")
        
        # Get page title and content
        title = await page.title()
        print(f"Page title: {title}")
        
        content = await page.content()
        
        # Save the full HTML
        html_path = os.path.join(OUTPUT_DIR, 'weibo_page.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"HTML saved to {html_path} ({len(content)} chars)")
        
        # Try to extract text content
        text_content = await page.inner_text('body')
        print(f"\nPage text (first 2000 chars):\n{text_content[:2000]}")
        
        # Check if we can see any weibo posts
        # Try to find weibo card elements
        cards = await page.query_selector_all('.card-wrap, .weibo-text, [class*="card"], [class*="weibo"]')
        print(f"\nFound {len(cards)} card elements")
        
        for i, card in enumerate(cards[:20]):
            text = await card.inner_text()
            if text and len(text.strip()) > 10:
                print(f"\nCard {i}: {text.strip()[:300]}")
        
        await browser.close()

asyncio.run(main())
