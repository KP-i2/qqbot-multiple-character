import requests, json, re, sys, time, os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'D:\Agent+LLM_results\skill_communication'
UID = '7382396909'

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
})

# Step 1: Get visitor pass
r1 = session.post('https://visitor.passport.weibo.cn/visitor/genvisitor2', 
    data={
        'cb': 'visitor_gray_callback',
        'ver': '20250916',
        'request_id': 'test456',
        'from': 'weibo',
        'webdriver': '0',
        'rid': str(int(time.time()*1000)),
        'return_url': f'https://m.weibo.cn/u/{UID}'
    }, timeout=15)

json_str = re.search(r'visitor_gray_callback\((.*)\)', r1.text, re.DOTALL)
data = json.loads(json_str.group(1))
d = data['data']
session.cookies.set('SUB', d['sub'], domain='.weibo.cn')
session.cookies.set('SUBP', d['subp'], domain='.weibo.cn')
session.cookies.set('tid', d['tid'], domain='.weibo.cn')
print("Visitor cookies obtained.")

# Step 2: Get user profile
r2 = session.get(f'https://m.weibo.cn/api/container/getIndex?type=uid&value={UID}', timeout=15)
profile_data = r2.json()

with open(os.path.join(OUTPUT_DIR, 'weibo_profile.json'), 'w', encoding='utf-8') as f:
    json.dump(profile_data, f, ensure_ascii=False, indent=2)

info = profile_data.get('data', {}).get('userInfo', {})
username = info.get('screen_name', 'N/A')
desc = info.get('description', 'N/A')
gender = info.get('gender', 'N/A')
followers = info.get('followers_count', 'N/A')
following = info.get('follow_count', 'N/A')
statuses = info.get('statuses_count', 'N/A')
verified = info.get('verified_reason', 'N/A')
location = info.get('location', 'N/A')

print(f"Username: {username}")
print(f"Description: {desc}")
print(f"Gender: {gender}")
print(f"Followers: {followers}")
print(f"Following: {following}")
print(f"Statuses: {statuses}")
print(f"Verified: {verified}")
print(f"Location: {location}")

# Get containerid for weibo tab
tabs = profile_data.get('data', {}).get('tabsInfo', {}).get('tabs', [])
weibo_tab_id = None
for tab in tabs:
    if tab.get('tab_type') == 'weibo':
        weibo_tab_id = tab.get('containerid')
        break
print(f"\nWeibo tab containerid: {weibo_tab_id}")

# Step 3: Fetch multiple pages of weibo posts
all_posts = []
container_id = weibo_tab_id

for page in range(1, 11):  # up to 10 pages
    if not container_id:
        break
    
    url = f'https://m.weibo.cn/api/container/getIndex?type=uid&value={UID}&containerid={container_id}&page={page}'
    r = session.get(url, timeout=15)
    
    if r.status_code != 200:
        print(f"Page {page}: HTTP {r.status_code}")
        break
    
    page_data = r.json()
    ok = page_data.get('ok')
    if ok != 1:
        print(f"Page {page}: ok={ok}, stopping.")
        break
    
    cards = page_data.get('data', {}).get('cards', [])
    if not cards:
        print(f"Page {page}: no cards, stopping.")
        break
    
    page_posts = []
    for card in cards:
        if card.get('card_type') == 9:
            mblog = card.get('mblog', {})
            text = re.sub(r'<[^>]+>', '', mblog.get('text', ''))
            page_posts.append({
                'created_at': mblog.get('created_at', ''),
                'text': text,
                'reposts_count': mblog.get('reposts_count', 0),
                'comments_count': mblog.get('comments_count', 0),
                'attitudes_count': mblog.get('attitudes_count', 0),
                'pics': [p.get('url', '') for p in mblog.get('pics', [])],
                'source': re.sub(r'<[^>]+>', '', mblog.get('source', '')),
            })
    
    all_posts.extend(page_posts)
    print(f"Page {page}: {len(page_posts)} posts fetched (total: {len(all_posts)})")
    
    # Save raw page
    with open(os.path.join(OUTPUT_DIR, f'weibo_posts_page{page}.json'), 'w', encoding='utf-8') as f:
        json.dump(page_data, f, ensure_ascii=False, indent=2)
    
    time.sleep(1.5)  # be polite

# Step 4: Compile into a readable text corpus
corpus_path = os.path.join(OUTPUT_DIR, 'weibo_corpus.txt')
with open(corpus_path, 'w', encoding='utf-8') as f:
    f.write(f"# 微博用户语料库\n")
    f.write(f"# 用户名: {username}\n")
    f.write(f"# UID: {UID}\n")
    f.write(f"# 简介: {desc}\n")
    f.write(f"# 性别: {gender}\n")
    f.write(f"# 粉丝数: {followers}\n")
    f.write(f"# 关注数: {following}\n")
    f.write(f"# 微博数: {statuses}\n")
    f.write(f"# 认证: {verified}\n")
    f.write(f"# 地区: {location}\n")
    f.write(f"# 采集时间: {time.strftime('%Y-%m-%d %H:%M')}\n")
    f.write(f"# 采集条数: {len(all_posts)}\n")
    f.write("=" * 60 + "\n\n")
    
    for i, post in enumerate(all_posts, 1):
        f.write(f"--- 第{i}条微博 [{post['created_at']}] ---\n")
        f.write(f"转发:{post['reposts_count']} 评论:{post['comments_count']} 点赞:{post['attitudes_count']}\n")
        f.write(f"{post['text']}\n")
        if post['pics']:
            f.write(f"[图片: {len(post['pics'])}张]\n")
        f.write(f"来源: {post['source']}\n\n")

print(f"\nDone! Total posts: {len(all_posts)}")
print(f"Corpus saved to: {corpus_path}")
