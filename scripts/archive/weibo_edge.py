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

EDGE_PATH = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
EDGE_USER_DATA = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data')

async def main():
    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=EDGE_USER_DATA,
                executable_path=EDGE_PATH,
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--disable-gpu',
                ],
                viewport={'width': 1280, 'height': 900},
                ignore_default_args=['--enable-automation'],
            )

            page = context.pages[0] if context.pages else await context.new_page()

            # Navigate to desktop weibo
            url = f'https://weibo.com/u/{UID}'
            print(f"Navigating to {url}...")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(8)

            current_url = page.url
            print(f"Current URL: {current_url}")
            title = await page.title()
            print(f"Title: {title}")

            # Fetch posts via AJAX API using browser cookies
            print("\nFetching weibo posts via in-browser AJAX...")
            all_posts = []

            for page_num in range(1, 11):
                api_url = f'https://weibo.com/ajax/statuses/mymblog?uid={UID}&page={page_num}&feature=0'

                result = await page.evaluate('''async (apiUrl) => {
                    try {
                        const resp = await fetch(apiUrl);
                        if (!resp.ok) return { error: resp.status, statusText: resp.statusText };
                        return await resp.json();
                    } catch(e) {
                        return { error: e.message };
                    }
                }''', api_url)

                if not result or 'error' in result:
                    print(f"Page {page_num}: error {result}")
                    break

                posts = result.get('data', {}).get('list', [])
                if not posts:
                    print(f"Page {page_num}: no posts (ok={result.get('ok')})")
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

                with open(os.path.join(OUTPUT_DIR, f'weibo_ajax_page{page_num}.json'), 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)

                await asyncio.sleep(2)

            # Fetch long text for truncated posts
            long_count = sum(1 for pp in all_posts if pp['is_long_text'])
            if long_count > 0:
                print(f"\nFetching long text for {long_count} posts...")
                for post in all_posts:
                    if post['is_long_text'] and post['mid']:
                        try:
                            lt_url = f'https://weibo.com/ajax/statuses/longtext?id={post["mid"]}'
                            result = await page.evaluate('''async (ltUrl) => {
                                try {
                                    const resp = await fetch(ltUrl);
                                    if (!resp.ok) return null;
                                    return await resp.json();
                                } catch(e) { return null; }
                            }''', lt_url)
                            if result:
                                long_text = result.get('data', {}).get('longTextContent', '')
                                if long_text:
                                    post['text'] = re.sub(r'<[^>]+>', '', long_text)
                                    print(f"  Got long text for mid={post['mid']}")
                            await asyncio.sleep(1)
                        except:
                            pass

            # Save corpus
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
            print(f"Corpus saved to: {corpus_path}")

            await context.close()

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

asyncio.run(main())
