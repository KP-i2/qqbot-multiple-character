import requests, json, re, sys, time, os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'D:\Agent+LLM_results\skill_communication'
COOKIES_FILE = os.path.join(OUTPUT_DIR, 'cookies.json')
UID = '7382396909'

# Load cookies
with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
    cookie_list = json.load(f)

print(f"Loaded {len(cookie_list)} cookies")

# Set up session
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://weibo.com/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json, text/plain, */*',
})

# Set XSRF-TOKEN header
for c in cookie_list:
    if c['name'] == 'XSRF-TOKEN':
        session.headers['X-XSRF-TOKEN'] = c['value']
        print(f"Set XSRF-TOKEN: {c['value'][:20]}...")

# Set cookies on session
for c in cookie_list:
    domain = c.get('domain', '.weibo.com')
    session.cookies.set(c['name'], c['value'], domain=domain)

print("Cookies applied to session.\n")

# First, get profile info
print("=== Fetching profile ===")
profile_url = f'https://weibo.com/ajax/profile/info?uid={UID}'
r = session.get(profile_url, timeout=15)
print(f"Profile API: HTTP {r.status_code}")

user_info = {}
if r.status_code == 200:
    try:
        pdata = r.json()
        user_info = pdata.get('data', {}).get('user', {})
        username = user_info.get('screen_name', 'N/A')
        desc = user_info.get('description', 'N/A')
        gender = user_info.get('gender', 'N/A')
        followers = user_info.get('followers_count', 'N/A')
        following = user_info.get('friends_count', 'N/A')
        statuses = user_info.get('statuses_count', 'N/A')
        verified = user_info.get('verified_reason', '')
        location = user_info.get('location', 'N/A')
        
        print(f"Username: {username}")
        print(f"Description: {desc}")
        print(f"Gender: {gender}")
        print(f"Followers: {followers}")
        print(f"Following: {following}")
        print(f"Statuses: {statuses}")
        print(f"Verified: {verified}")
        print(f"Location: {location}")
        
        with open(os.path.join(OUTPUT_DIR, 'weibo_profile_detail.json'), 'w', encoding='utf-8') as f:
            json.dump(pdata, f, ensure_ascii=False, indent=2)
    except:
        print(f"Not JSON: {r.text[:300]}")
else:
    print(f"Response: {r.text[:300]}")

# Fetch posts
print("\n=== Fetching posts ===")
all_posts = []

for page_num in range(1, 21):  # up to 20 pages
    url = f'https://weibo.com/ajax/statuses/mymblog?uid={UID}&page={page_num}&feature=0'
    r = session.get(url, timeout=15)
    
    if r.status_code != 200:
        print(f"Page {page_num}: HTTP {r.status_code}")
        if r.status_code == 403:
            print("  Access denied. Cookie may have expired.")
        break
    
    try:
        data = r.json()
    except:
        print(f"Page {page_num}: not JSON")
        break
    
    ok = data.get('ok')
    posts = data.get('data', {}).get('list', [])
    
    if not posts:
        print(f"Page {page_num}: no more posts (ok={ok})")
        break
    
    for p in posts:
        text = p.get('text_raw', '') or re.sub(r'<[^>]+>', '', p.get('text', ''))
        all_posts.append({
            'created_at': p.get('created_at', ''),
            'text': text,
            'reposts_count': p.get('reposts_count', 0),
            'comments_count': p.get('comments_count', 0),
            'attitudes_count': p.get('attitudes_count', 0),
            'source': p.get('source', ''),
            'pic_ids': p.get('pic_ids', []),
            'is_long_text': p.get('isLongText', False),
            'mid': str(p.get('mid', '')),
            'retweeted': bool(p.get('retweeted_status')),
        })
    
    print(f"Page {page_num}: {len(posts)} posts (total: {len(all_posts)})")
    
    # Save raw page
    with open(os.path.join(OUTPUT_DIR, f'weibo_ajax_page{page_num}.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    time.sleep(2)

# Fetch long text for truncated posts
long_posts = [p for p in all_posts if p['is_long_text'] and p['mid']]
if long_posts:
    print(f"\n=== Fetching long text for {len(long_posts)} posts ===")
    for post in long_posts:
        try:
            lt_url = f'https://weibo.com/ajax/statuses/longtext?id={post["mid"]}'
            r = session.get(lt_url, timeout=10)
            if r.status_code == 200:
                ld = r.json()
                long_text = ld.get('data', {}).get('longTextContent', '')
                if long_text:
                    post['text'] = re.sub(r'<[^>]+>', '', long_text)
                    print(f"  OK: mid={post['mid']}")
            time.sleep(1)
        except Exception as e:
            print(f"  Error: {e}")

# Compile corpus
corpus_path = os.path.join(OUTPUT_DIR, 'weibo_corpus.txt')
with open(corpus_path, 'w', encoding='utf-8') as f:
    f.write("# 微博用户语料库\n")
    f.write(f"# 用户名: {user_info.get('screen_name', 'N/A')}\n")
    f.write(f"# UID: {UID}\n")
    f.write(f"# 简介: {user_info.get('description', 'N/A')}\n")
    f.write(f"# 性别: {user_info.get('gender', 'N/A')}\n")
    f.write(f"# 粉丝数: {user_info.get('followers_count', 'N/A')}\n")
    f.write(f"# 关注数: {user_info.get('friends_count', 'N/A')}\n")
    f.write(f"# 微博数: {user_info.get('statuses_count', 'N/A')}\n")
    f.write(f"# 认证: {user_info.get('verified_reason', 'N/A')}\n")
    f.write(f"# 采集时间: {time.strftime('%Y-%m-%d %H:%M')}\n")
    f.write(f"# 采集条数: {len(all_posts)}\n")
    f.write("=" * 60 + "\n\n")
    
    for i, post in enumerate(all_posts, 1):
        f.write(f"--- 第{i}条微博 [{post['created_at']}] ---\n")
        f.write(f"转发:{post['reposts_count']} 评论:{post['comments_count']} 点赞:{post['attitudes_count']}\n")
        if post['retweeted']:
            f.write("[转发微博]\n")
        f.write(f"{post['text']}\n")
        if post['pic_ids']:
            f.write(f"[图片: {len(post['pic_ids'])}张]\n")
        f.write(f"来源: {post['source']}\n\n")

print(f"\n=== Done! ===")
print(f"Total posts: {len(all_posts)}")
print(f"Original posts: {sum(1 for p in all_posts if not p['retweeted'])}")
print(f"Retweets: {sum(1 for p in all_posts if p['retweeted'])}")
print(f"Long text fetched: {sum(1 for p in all_posts if not p['is_long_text'] or p.get('text'))}")
print(f"Corpus saved to: {corpus_path}")
