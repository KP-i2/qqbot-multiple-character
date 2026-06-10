import asyncio
import json
import re
import os
import sys
import time
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'.'
UID = '1234567890'

# Try to find Chrome
CHROME_PATHS = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
]

USER_DATA_DIR = os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data')

async def main():
    chrome_path = None
    for p in CHROME_PATHS:
        if os.path.exists(p):
            chrome_path = p
            break
    
    if not chrome_path:
        print("ERROR: Chrome not found")
        return
    
    print(f"Chrome found at: {chrome_path}")
    print(f"User data dir: {USER_DATA_DIR}")
    print(f"User data exists: {os.path.exists(USER_DATA_DIR)}")
    
    async with async_playwright() as p:
        try:
            # Launch with user's Chrome profile (persistent context)
            # This reuses cookies/login state from the user's Chrome
            context = await p.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                executable_path=chrome_path,
                headless=True,
                channel='chrome',
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-first-run',
                    '--no-default-browser-check',
                ],
                viewport={'width': 1280, 'height': 900},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            )
            
            page = context.pages[0] if context.pages else await context.new_page()
            
            # Navigate to user profile
            url = f'https://weibo.com/u/{UID}'
            print(f"\nNavigating to {url}...")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(8)
            
            current_url = page.url
            print(f"Current URL: {current_url}")
            title = await page.title()
            print(f"Title: {title}")
            
            # Try to get the page content via AJAX API using browser's cookies
            print("\nFetching weibo posts via AJAX...")
            all_posts = []
            
            for page_num in range(1, 11):
                api_url = f'https://weibo.com/ajax/statuses/mymblog?uid={UID}&page={page_num}&feature=0'
                result = await page.evaluate(f'''
                    async () => {{
                        const resp = await fetch("{api_url}");
                        if (!resp.ok) return {{ error: resp.status }};
                        return await resp.json();
                    }}
                ''')
                
                if not result or 'error' in result:
                    print(f"Page {page_num}: error {result}")
                    break
                
                posts = result.get('data', {}).get('list', [])
                if not posts:
                    print(f"Page {page_num}: no posts")
                    break
                
                for post in posts:
                    text = post.get('text_raw', '')
                    if not text:
                        text = re.sub(r'<[^>]+>', '', post.get('text', ''))
                    
                    all_posts.append({
                        'created_at': post.get('created_at', ''),
                        'text': text,
                        'reposts_count': post.get('reposts_count', 0),
                        'comments_count': post.get('comments_count', 0),
                        'attitudes_count': post.get('attitudes_count', 0),
                        'source': post.get('source', ''),
                        'pic_ids': post.get('pic_ids', []),
                        'is_long_text': post.get('isLongText', False),
                        'mid': str(post.get('mid', '')),
                    })
                
                print(f"Page {page_num}: {len(posts)} posts (total: {len(all_posts)})")
                
                # Save raw page data
                with open(os.path.join(OUTPUT_DIR, f'weibo_ajax_page{page_num}.json'), 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                
                await asyncio.sleep(2)
            
            # Fetch long text posts
            print(f"\nFetching long text for {sum(1 for p in all_posts if p['is_long_text'])} posts...")
            for post in all_posts:
                if post['is_long_text'] and post['mid']:
                    try:
                        api_url = f'https://weibo.com/ajax/statuses/longtext?id={post["mid"]}'
                        result = await page.evaluate(f'''
                            async () => {{
                                const resp = await fetch("{api_url}");
                                if (!resp.ok) return null;
                                return await resp.json();
                            }}
                        ''')
                        if result:
                            long_text = result.get('data', {}).get('longTextContent', '')
                            if long_text:
                                post['text'] = re.sub(r'<[^>]+>', '', long_text)
                                print(f"  Fetched long text for mid={post['mid']}")
                        await asyncio.sleep(1)
                    except Exception as e:
                        print(f"  Error fetching long text: {e}")
            
            # Compile corpus
            corpus_path = os.path.join(OUTPUT_DIR, 'weibo_corpus.txt')
            with open(corpus_path, 'w', encoding='utf-8') as f:
                f.write("# 微博用户语料库\n")
                f.write(f"# UID: {UID}\n")
                f.write(f"# 采集时间: {time.strftime('%Y-%m-%d %H:%M')}\n")
                f.write(f"# 采集条数: {len(all_posts)}\n")
                f.write("=" * 60 + "\n\n")
                
                for i, post in enumerate(all_posts, 1):
                    f.write(f"--- 第{i}条微博 [{post['created_at']}] ---\n")
                    f.write(f"转发:{post['reposts_count']} 评论:{post['comments_count']} 点赞:{post['attitudes_count']}\n")
                    f.write(f"{post['text']}\n")
                    if post['pic_ids']:
                        f.write(f"[图片: {len(post['pic_ids'])}张]\n")
                    f.write(f"来源: {post['source']}\n\n")
            
            print(f"\nDone! Total: {len(all_posts)} posts")
            print(f"Corpus: {corpus_path}")
            
            await context.close()
            
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

asyncio.run(main())
