"""
X(Twitter) 用户推文抓取脚本 — twikit (纯 HTTP，无需 API Key，无需浏览器)
用法:
    # 自动登录（含 KEY_BYTE 补丁）
    python scripts/fetch_twitter.py elonmusk
    python scripts/fetch_twitter.py realDonaldTrump
    python scripts/fetch_twitter.py elonmusk --count 200     # 指定抓取条数
    python scripts/fetch_twitter.py elonmusk --type Replies  # 抓回复而不是推文

    # 如自动登录失败，可手动获取 cookies 后抓取:
    python scripts/twitter_login.py --auto   # 浏览器登录保存 cookies
    python scripts/fetch_twitter.py elonmusk  # 自动用 cookies 免登

输出到 corpus/twitter_{screen_name}/ 目录:
    corpus.txt      — 抓取的推文语料

前置条件:
    pip install twikit playwright
    需要一个 X(Twitter) 账号 + twitter_account.json 凭据文件
"""

import asyncio, json, os, sys, time, argparse
from pathlib import Path
from urllib.parse import unquote

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ============================================================
# twikit KEY_BYTE 补丁 (from plume-tui)
# X 改了 JS bundle 结构，twikit 的 ClientTransaction.init 会失败
# 用空 stub 替代，跳过 X-Client-Transaction-Id 头
# ============================================================

class _StubClientTransaction:
    """替换 twikit 的 ClientTransaction，跳过 KEY_BYTE 索引提取"""
    home_page_response = True

    def generate_transaction_id(self, method: str, path: str) -> str:
        return ""

    async def init(self, session, headers):
        return None


def _apply_twikit_patch(client):
    """给 twikit client 打补丁"""
    client.client_transaction = _StubClientTransaction()
    return client


# ============================================================
# 路径常量
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRIVATE_DIR = PROJECT_ROOT / 'private'
CORPUS_DIR = PROJECT_ROOT / 'corpus'
COOKIES_FILE = PRIVATE_DIR / 'twitter_cookies.json'


def _load_env() -> dict:
    """从 .env 文件加载环境变量（兼容 private/.env 和根 .env 兜底）"""
    env_path = PRIVATE_DIR / '.env'
    if not env_path.exists():
        env_path = PROJECT_ROOT / '.env'
    env_vars = {}
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    env_vars[key.strip()] = value.strip()
    return env_vars


def parse_args():
    parser = argparse.ArgumentParser(description='X(Twitter) 用户推文抓取')
    parser.add_argument('screen_name', help='目标用户 screen name（如 elonmusk）')
    parser.add_argument('--count', type=int, default=200, help='抓取条数（默认 200，配合 --all 无效）')
    parser.add_argument('--all', action='store_true', help='抓取全部推文（忽略 --count）')
    parser.add_argument('--replies', action='store_true', help='同时抓取本人的回复（与推文合并）')
    parser.add_argument('--type', dest='tweet_type', default='Tweets',
                        choices=['Tweets', 'Replies', 'Media', 'Likes'],
                        help='推文类型（默认 Tweets）')
    return parser.parse_args()


# ============================================================
# 写入语料
# ============================================================

def write_corpus(dir_path: Path, screen_name: str, user_info: dict, tweets: list[dict], replies: list[dict] = None):
    """输出语料文件（仅保留角色提炼有用的信息：正文 + 回复对象 + 转推来源）"""
    if replies is None:
        replies = []

    # ---- JSON 备份（完整结构化数据）----
    json_path = dir_path / 'tweets_raw.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'user': user_info,
            'tweets': tweets,
            'replies': replies,
            'collected_at': time.strftime('%Y-%m-%d %H:%M'),
            'tweet_count': len(tweets),
            'reply_count': len(replies),
        }, f, ensure_ascii=False, indent=2)
    print(f"已保存 JSON: {json_path}")

    # ---- 可读语料（角色提炼专用格式）----
    corpus_path = dir_path / 'corpus.txt'
    with open(corpus_path, 'w', encoding='utf-8') as f:
        f.write(f"# X(Twitter) 用户发言语料\n")
        f.write(f"# 用户名: @{screen_name}\n")
        f.write(f"# 昵称: {user_info.get('name', 'N/A')}\n")
        f.write(f"# 简介: {user_info.get('description', 'N/A')}\n")
        f.write(f"# 位置: {user_info.get('location', 'N/A')}\n")
        f.write(f"# 粉丝: {user_info.get('followers_count', 0):,}\n")
        f.write(f"# 关注: {user_info.get('following_count', 0):,}\n")
        f.write(f"# 总推文数: {user_info.get('tweets_count', 0):,}\n")
        f.write(f"# 采集时间: {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# 采集条数: 推文 {len(tweets)} + 回复 {len(replies)} = {len(tweets) + len(replies)}\n")
        f.write("=" * 60 + "\n\n")

        def write_one(f, tweet, index, label='推文'):
            f.write(f"--- 第{index}条{label} [{tweet['created_at']}] ---\n")
            # 互动数据（精简）
            f.write(f"❤️{tweet['favorite_count']} 🔄{tweet['retweet_count']} 💬{tweet['reply_count']}\n")
            # 正文
            f.write(f"{tweet['text']}\n")
            # 转推
            if tweet.get('is_retweet') and tweet.get('retweet_text'):
                f.write(f"  [转推自 @{tweet['is_retweet']}]: {tweet['retweet_text'][:200]}...\n" if len(tweet['retweet_text']) > 200 else
                       f"  [转推自 @{tweet['is_retweet']}]: {tweet['retweet_text']}\n")
            elif tweet.get('is_retweet'):
                f.write(f"  [转推自 @{tweet['is_retweet']}]\n")
            # 回复
            if tweet.get('in_reply_to'):
                f.write(f"  [回复 @{tweet['in_reply_to']}]\n")
            # 链接
            f.write(f"  https://x.com/{screen_name}/status/{tweet['id']}\n")
            f.write("\n")

        # 推文
        idx = 0
        for tweet in tweets:
            idx += 1
            write_one(f, tweet, idx, '推文')

        # 本人回复
        if replies:
            f.write("\n" + "=" * 60 + "\n")
            f.write("# 以下为本人回复他人的内容\n")
            f.write("=" * 60 + "\n\n")
            for reply in replies:
                idx += 1
                write_one(f, reply, idx, '回复')

    print(f"已保存: {corpus_path} (推文 {len(tweets)} + 回复 {len(replies)} = {len(tweets) + len(replies)} 条)")


# ============================================================
# 抓取逻辑
# ============================================================

async def fetch_tweets(
    client,
    screen_name: str,
    tweet_type: str = 'Tweets',
    count: int = None,
) -> tuple[dict, list[dict]]:
    """抓取指定用户推文。count=None 表示抓全部（翻到无数据为止）"""
    from twikit import Client

    # 1. 获取用户信息
    print(f"\n🔍 正在查找用户 @{screen_name} ...")
    user = await client.get_user_by_screen_name(screen_name)
    user_info = {
        'id': user.id,
        'name': user.name,
        'screen_name': user.screen_name,
        'created_at': str(user.created_at_datetime) if hasattr(user, 'created_at_datetime') and user.created_at_datetime else getattr(user, 'created_at', ''),
        'description': getattr(user, 'description', ''),
        'location': getattr(user, 'location', ''),
        'url': getattr(user, 'url', ''),
        'followers_count': getattr(user, 'followers_count', 0),
        'following_count': getattr(user, 'following_count', 0),
        'tweets_count': getattr(user, 'statuses_count', 0),
        'likes_count': getattr(user, 'favourites_count', 0),
        'listed_count': getattr(user, 'listed_count', 0),
        'media_count': getattr(user, 'media_count', 0),
        'verified': getattr(user, 'verified', False),
        'is_blue_verified': getattr(user, 'is_blue_verified', False),
        'protected': getattr(user, 'protected', False),
        'profile_image_url': getattr(user, 'profile_image_url', ''),
        'profile_banner_url': getattr(user, 'profile_banner_url', ''),
        'pinned_tweet_ids': getattr(user, 'pinned_tweet_ids', []),
    }
    print(f"  用户: {user.name} (@{screen_name})")
    print(f"  ID: {user.id}")
    print(f"  粉丝: {user_info['followers_count']:,}  推文: {user_info['tweets_count']:,}")

    # 2. 分页抓取推文
    target_desc = f"目标: {count} 条" if count else "目标: 全部"
    print(f"\n📥 开始抓取推文 (类型: {tweet_type}, {target_desc})...")
    all_tweets = []
    cursor = None
    page = 0
    fail_count = 0  # 限流退避计数器

    while count is None or len(all_tweets) < count:
        page += 1

        if count is not None:
            remaining = count - len(all_tweets)
            batch_size = min(40, remaining)
        else:
            batch_size = 40

        try:
            result = await client.get_user_tweets(
                user.id, tweet_type, count=batch_size, cursor=cursor
            )
        except Exception as e:
            err_msg = str(e)
            print(f"  ⚠️ 第{page}页请求失败: {err_msg[:120]}")
            # 429 限流 — 指数退避
            if '429' in err_msg or 'Rate limit' in err_msg:
                wait = min(60 * (2 ** min(fail_count, 3)), 300)  # 60→120→240→300s
                print(f"    限流等待 {wait} 秒...")
                await asyncio.sleep(wait)
                fail_count += 1
            else:
                await asyncio.sleep(10)
            continue

        if not result or len(result) == 0:
            print(f"  第{page}页: 无数据，抓取结束")
            break

        page_tweets = []
        for tweet in result:
            # 提取数据
            data = {
                'id': tweet.id,
                'text': tweet.full_text or tweet.text,
                'created_at': str(tweet.created_at_datetime) if tweet.created_at_datetime else tweet.created_at,
                'favorite_count': getattr(tweet, 'favorite_count', 0),
                'retweet_count': getattr(tweet, 'retweet_count', 0),
                'reply_count': getattr(tweet, 'reply_count', 0),
                'quote_count': getattr(tweet, 'quote_count', 0),
                'view_count': getattr(tweet, 'view_count', 0),
                'bookmark_count': getattr(tweet, 'bookmark_count', 0),
                'lang': getattr(tweet, 'lang', ''),
                'possibly_sensitive': getattr(tweet, 'possibly_sensitive', False),
                'hashtags': list(tweet.hashtags) if hasattr(tweet, 'hashtags') and tweet.hashtags else [],
                'urls': [u.get('url', u) if isinstance(u, dict) else str(u)
                         for u in tweet.urls] if hasattr(tweet, 'urls') and tweet.urls else [],
                'is_retweet': None,
                'retweet_text': None,
                'in_reply_to': None,
                'media_urls': [],
                'place': None,
                'poll': None,
                'community_note': None,
            }

            # 转推
            if hasattr(tweet, 'retweeted_tweet') and tweet.retweeted_tweet:
                try:
                    rt = tweet.retweeted_tweet
                    data['is_retweet'] = rt.user.screen_name
                    data['retweet_text'] = rt.full_text or rt.text
                except Exception:
                    data['is_retweet'] = 'unknown'

            # 回复
            if hasattr(tweet, 'in_reply_to') and tweet.in_reply_to:
                try:
                    data['in_reply_to'] = tweet.in_reply_to.screen_name
                except Exception:
                    pass

            # 媒体
            if hasattr(tweet, 'media') and tweet.media:
                for m in tweet.media:
                    try:
                        url = m.get('media_url_https') or m.get('media_url') or str(m)
                        data['media_urls'].append(url)
                    except Exception:
                        pass

            # 地点
            if hasattr(tweet, 'place') and tweet.place:
                try:
                    data['place'] = tweet.place.get('full_name') or tweet.place.get('name') or str(tweet.place)
                except Exception:
                    pass

            # 投票
            if hasattr(tweet, 'poll') and tweet.poll:
                try:
                    choices = tweet.poll.get('choices', [])
                    data['poll'] = ' | '.join(c.get('label', '?') for c in choices)
                except Exception:
                    pass

            # 社区笔记
            if hasattr(tweet, 'community_note') and tweet.community_note:
                try:
                    data['community_note'] = tweet.community_note.get('text', str(tweet.community_note))[:200]
                except Exception:
                    pass

            page_tweets.append(data)

        if not page_tweets:
            print(f"  第{page}页: 无数据，抓取结束")
            break

        all_tweets.extend(page_tweets)
        fail_count = 0  # 请求成功，重置限流退避
        progress = f"累计 {len(all_tweets)}" if count is None else f"累计 {len(all_tweets)}/{count}"
        print(f"  第{page}页: +{len(page_tweets)} 条 ({progress})")

        # 检查是否还有下一页
        cursor = result.next_cursor
        if not cursor:
            print("  已到末尾，无更多推文")
            break

        await asyncio.sleep(8)  # 请求间隔，避免限流

    return user_info, all_tweets


# ============================================================
# 主流程
# ============================================================

async def main():
    args = parse_args()
    screen_name = args.screen_name.lower().lstrip('@')

    from twikit import Client
    client = Client('en-US')
    _apply_twikit_patch(client)  # KEY_BYTE 补丁

    logged_in = False

    # ---- 优先: 已有 cookies 直接加载（跳过可能假成功的 login）----
    if COOKIES_FILE.exists():
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            saved_cookies = json.load(f)

        # 兼容两种格式：浏览器导出的数组 / twikit 保存的字典
        if isinstance(saved_cookies, list):
            auth_token = next((c['value'] for c in saved_cookies if c.get('name') == 'auth_token'), None)
        else:
            auth_token = saved_cookies.get('auth_token')

        if auth_token:
            print(f"\n🍪 加载已有 cookies (含 auth_token)")

            # 浏览器导出的数组 → 转为 twikit dict 格式
            if isinstance(saved_cookies, list):
                saved_cookies = {c['name']: c['value'] for c in saved_cookies}

            client.set_cookies(saved_cookies)

            # 从 twid cookie 提取 user_id，跳过已废弃的 v1.1/settings API
            if isinstance(saved_cookies, list):
                twid = next((c['value'] for c in saved_cookies if c.get('name') == 'twid'), '')
            else:
                twid = saved_cookies.get('twid', '')
            if twid:
                decoded = unquote(twid)  # u%3D123 → u=123
                if decoded.startswith('u='):
                    client._user_id = decoded[2:]
                    print(f"   user_id (from twid): {client._user_id}")

            try:
                # 直接用目标用户验证（不走 v1.1 API）
                await client.get_user_by_screen_name(screen_name)
                print(f"  ✅ Cookies 有效")
                logged_in = True
            except Exception as e:
                print(f"  ⚠️ Cookies 验证失败: {e}，尝试重新登录...")

    # ---- 方式2: 密码登录（仅当 cookies 无效/不存在）----
    if not logged_in:
        env = _load_env()
        username = env.get('TWITTER_USERNAME', '')
        password = env.get('TWITTER_PASSWORD', '')
        email = env.get('TWITTER_EMAIL', '')

        if username and password:
            print(f"\n🔑 密码登录: {username}")
            try:
                await client.login(
                    auth_info_1=username,
                    auth_info_2=email or None,
                    password=password,
                )
                # 保存登录后的 cookies
                raw_cookies = client.get_cookies()
                with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                    json.dump(raw_cookies, f, ensure_ascii=False, indent=2)
                print("  ✅ 登录成功，cookies 已保存")
                logged_in = True
            except Exception as e:
                print(f"  ⚠️ 登录失败: {e}")

    # ---- 都失败 ----
    if not logged_in:
        print("\n❌ 所有登录方式均失败")
        print("   请从浏览器导出 cookies (Cookie-Editor 插件) 粘贴给我")
        sys.exit(1)

    # 输出目录
    corpus_dir = CORPUS_DIR / f'twitter_{screen_name}'
    corpus_dir.mkdir(parents=True, exist_ok=True)
    print(f"输出目录: {corpus_dir}")

    # 抓取推文
    fetch_count = None if args.all else args.count
    user_info, tweets = await fetch_tweets(
        client, screen_name,
        tweet_type=args.tweet_type,
        count=fetch_count,
    )

    # 抓取本人回复（可选）
    replies = []
    if args.replies:
        print(f"\n{'='*40}")
        print(f"📥 追加抓取本人回复...")
        reply_count = None if args.all else args.count
        _, replies = await fetch_tweets(
            client, screen_name,
            tweet_type='Replies',
            count=reply_count,
        )

    # 输出
    write_corpus(corpus_dir, screen_name, user_info, tweets, replies)

    # 汇总
    print(f"\n{'='*40}")
    print(f"用户: {user_info['name']} (@{screen_name})")
    print(f"类型: {args.tweet_type}" + (" + Replies" if args.replies else ""))
    print(f"抓取: 推文 {len(tweets)} 条" + (f" + 回复 {len(replies)} 条" if args.replies else ""))
    print(f"输出: {corpus_dir / 'corpus.txt'}")


if __name__ == '__main__':
    asyncio.run(main())
