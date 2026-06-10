import requests, json, re, sys, time, os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'D:\Agent+LLM_results\skill_communication'
COOKIES_FILE = os.path.join(OUTPUT_DIR, 'cookies.json')
UID = '7382396909'

with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
    cookie_list = json.load(f)

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': f'https://weibo.com/u/{UID}',
    'Accept': 'application/json, text/plain, */*',
})

for c in cookie_list:
    if c['name'] == 'XSRF-TOKEN':
        session.headers['X-XSRF-TOKEN'] = c['value']
    domain = c.get('domain', '.weibo.com')
    session.cookies.set(c['name'], c['value'], domain=domain)

# Test: check who we are logged in as
print("=== Testing session ===")
r = session.get('https://weibo.com/ajax/profile/info', timeout=15)
print(f"Own profile: HTTP {r.status_code}")
if r.status_code == 200:
    try:
        d = r.json()
        user = d.get('data', {}).get('user', {})
        print(f"Logged in as: {user.get('screen_name', 'unknown')} (uid={user.get('id', 'unknown')})")
    except:
        print(f"Response: {r.text[:300]}")

# Try multiple API endpoints
endpoints = [
    ('mymblog', f'https://weibo.com/ajax/statuses/mymblog?uid={UID}&page=1&feature=0'),
    ('mymblog_v2', f'https://weibo.com/ajax/statuses/mymblog?uid={UID}&page=1'),
    ('detail', f'https://weibo.com/ajax/profile/detail?uid={UID}'),
]

for name, url in endpoints:
    print(f"\n=== Testing {name} ===")
    r = session.get(url, timeout=15)
    print(f"HTTP {r.status_code}")
    if r.status_code == 200 and r.text:
        try:
            d = r.json()
            print(f"ok={d.get('ok')}")
            print(f"Keys: {list(d.get('data', {}).keys()) if isinstance(d.get('data'), dict) else 'N/A'}")
            # Print a snippet
            text = json.dumps(d, ensure_ascii=False, indent=2)
            print(text[:800])
        except:
            print(f"Not JSON: {r.text[:300]}")
    else:
        print(f"Response: {r.text[:300] if r.text else 'empty'}")
