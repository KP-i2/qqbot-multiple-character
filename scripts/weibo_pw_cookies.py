"""
微博语料抓取脚本 — Playwright + Cookie 注入 + 浏览器内 AJAX
用法:
    python weibo_pw_cookies.py                    # 使用默认 UID
    python weibo_pw_cookies.py 7382396909         # 指定 UID
    python weibo_pw_cookies.py 7382396909 --all   # 抓取全部（含扩展页）

输出到 corpus/{UID}/ 目录:
    corpus.txt           — 初始抓取（约 20 页）
    corpus_extended.txt  — 扩展抓取（21 页起，仅 --all 模式）
    corpus_full.txt      — 合并（仅 --all 模式）
"""

import asyncio, json, re, os, sys, time, argparse
from pathlib import Path
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ============================================================
# 配置
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COOKIES_FILE = PROJECT_ROOT / 'cookies.json'
DEFAULT_UID = '7382396909'


def parse_args():
    parser = argparse.ArgumentParser(description='微博语料抓取')
    parser.add_argument('uid', nargs='?', default=DEFAULT_UID, help='微博用户 UID')
    parser.add_argument('--all', action='store_true', help='抓取全部页面（含 21 页以后的历史）')
    parser.add_argument('--start-page', type=int, default=21, help='扩展抓取起始页（默认 21）')
    return parser.parse_args()


# ============================================================
# Cookie 加载
# ============================================================

def load_cookies(path: Path) -> list[dict]:
    with open(path, 'r', encoding='utf-8') as f:
        cookie_list = json.load(f)

    pw_cookies = []
    for c in cookie_list:
        cookie = {
            'name': c['name'],
            'value': c['value'],
            'domain': c.get('domain', '.weibo.com'),
            'path': c.get('path', '/'),
            'secure': c.get('secure', False),
            'httpOnly': c.get('httpOnly', False),
        }
        same_site = c.get('sameSite', '')
        if same_site and same_site != 'no_restriction':
            cookie['sameSite'] = same_site
        elif same_site == 'no_restriction':
            cookie['sameSite'] = 'None'
        pw_cookies.append(cookie)

    print(f"已加载 {len(pw_cookies)} 条 cookie")
    return pw_cookies


# ============================================================
# 单页抓取
# ============================================================

async def fetch_page(page, uid: str, page_num: int) -> list[dict]:
    """在浏览器上下文中调用微博 AJAX 接口获取一页微博"""
    result = await page.evaluate(f'''async () => {{
        try {{
            const resp = await fetch('/ajax/statuses/mymblog?uid={uid}&page={page_num}&feature=0');
            return await resp.json();
        }} catch(e) {{
            return {{error: e.message}};
        }}
    }}''')

    if result.get('ok') != 1:
        return []

    posts = []
    for item in result.get('data', {}).get('list', []):
        text = item.get('text_raw', '') or re.sub(r'<[^>]+>', '', item.get('text', ''))

        # 长微博补全
        if item.get('isLongText'):
            mid = item.get('mid', '')
            try:
                long_result = await page.evaluate(f'''async () => {{
                    try {{
                        const resp = await fetch('/ajax/statuses/longtext?id={mid}');
                        return await resp.json();
                    }} catch(e) {{ return null; }}
                }}''')
                if long_result and long_result.get('ok') == 1:
                    long_text = long_result.get('data', {}).get('longTextContent', '')
                    if long_text:
                        text = long_text
            except Exception:
                pass

        posts.append({
            'created_at': item.get('created_at', ''),
            'text': text,
            'reposts_count': item.get('reposts_count', 0),
            'comments_count': item.get('comments_count', 0),
            'attitudes_count': item.get('attitudes_count', 0),
            'source': item.get('source', ''),
            'pic_ids': item.get('pic_ids', []),
        })

    return posts


# ============================================================
# 写入语料文件
# ============================================================

def write_corpus(path: Path, uid: str, posts: list[dict], label: str = ''):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"# 微博用户语料库{f' ({label})' if label else ''}\n")
        f.write(f"# UID: {uid}\n")
        f.write(f"# 采集时间: {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# 采集条数: {len(posts)}\n")
        f.write(f"{'='*60}\n\n")

        for i, post in enumerate(posts, 1):
            f.write(f"--- 第{i}条微博 [{post['created_at']}] ---\n")
            f.write(f"转发:{post['reposts_count']} 评论:{post['comments_count']} 点赞:{post['attitudes_count']}\n")
            f.write(f"{post['text']}\n")
            if post['pic_ids']:
                f.write(f"[图片: {len(post['pic_ids'])}张]\n")
            f.write(f"来源: {post['source']}\n\n")

    print(f"已保存: {path} ({len(posts)} 条)")


# ============================================================
# 主流程
# ============================================================

async def main():
    args = parse_args()
    uid = args.uid

    # 输出目录
    corpus_dir = PROJECT_ROOT / 'corpus' / uid
    corpus_dir.mkdir(parents=True, exist_ok=True)
    print(f"UID: {uid}")
    print(f"输出目录: {corpus_dir}")

    cookies = load_cookies(COOKIES_FILE)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 900},
        )
        await context.add_cookies(cookies)

        page = await context.new_page()
        await page.goto(f'https://weibo.com/u/{uid}', wait_until='domcontentloaded', timeout=60000)
        await asyncio.sleep(6)

        # ---- 初始抓取（1-20 页）----
        print("\n=== 初始抓取 (page 1-20) ===")
        all_posts = []
        for page_num in range(1, 21):
            posts = await fetch_page(page, uid, page_num)
            if not posts:
                print(f"Page {page_num}: 无更多数据，停止")
                break
            all_posts.extend(posts)
            print(f"Page {page_num}: {len(posts)} 条 (累计 {len(all_posts)})")
            await asyncio.sleep(2)

        corpus_path = corpus_dir / 'corpus.txt'
        write_corpus(corpus_path, uid, all_posts, '初始')

        # ---- 扩展抓取（21+ 页，仅 --all）----
        if args.all:
            print(f"\n=== 扩展抓取 (page {args.start_page}+) ===")
            extended_posts = []
            for page_num in range(args.start_page, 200):
                posts = await fetch_page(page, uid, page_num)
                if not posts:
                    print(f"Page {page_num}: 无更多数据，停止")
                    break
                extended_posts.extend(posts)
                print(f"Page {page_num}: {len(posts)} 条 (累计 {len(extended_posts)})")
                await asyncio.sleep(2)

            if extended_posts:
                ext_path = corpus_dir / 'corpus_extended.txt'
                write_corpus(ext_path, uid, extended_posts, '扩展')

                # 合并：老的在前，新的在后
                full_posts = extended_posts + all_posts
                full_path = corpus_dir / 'corpus_full.txt'
                write_corpus(full_path, uid, full_posts, '完整合并')
            else:
                print("扩展抓取无新数据")

        # ---- 汇总 ----
        print(f"\n{'='*40}")
        print(f"初始: {len(all_posts)} 条 → {corpus_path}")
        if args.all and 'extended_posts' in dir():
            print(f"扩展: {len(extended_posts)} 条")
            print(f"合并: {len(all_posts) + len(extended_posts)} 条")
        print(f"目录: {corpus_dir}")

        await browser.close()


asyncio.run(main())
