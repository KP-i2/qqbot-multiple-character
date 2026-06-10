import requests, json, re, sys, time, os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'D:\Agent+LLM_results\skill_communication'
COOKIES_FILE = os.path.join(OUTPUT_DIR, 'cookies.json')
UID = '7382396909'

with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
    cookie_list = json.load(f)

session = requests.Session()

# Set very browser-like headers
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
})

# Build cookie header manually for precise control
cookie_str_parts = []
xsrf_token = ''
for c in cookie_list:
    cookie_str_parts.append(f"{c['name']}={c['value']}")
    if c['name'] == 'XSRF-TOKEN':
        xsrf_token = c['value']
    # Also set via session cookies with exact domain
    domain = c.get('domain', '.weibo.com')
    path = c.get('path', '/')
    secure = c.get('secure', False)
    
    # Use http.cookiejar for precise domain matching
    from http.cookiejar import Cookie
    import http.cookiejar
    
    cj_cookie = Cookie(
        version=0,
        name=c['name'],
        value=c['value'],
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=not c.get('hostOnly', False),
        domain_initial_dot=domain.startswith('.'),
        path=path,
        path_specified=True,
        secure=secure,
        expires=c.get('expirationDate', None),
        discard=c.get('session', True),
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )
    session.cookies.set_cookie(cj_cookie)

if xsrf_token:
    session.headers['X-XSRF-TOKEN'] = xsrf_token

# First visit the main page to establish session
print("Warming up session...")
session.headers['Referer'] = 'https://weibo.com/'
r0 = session.get('https://weibo.com/', timeout=15)
print(f"Main page: HTTP {r0.status_code}, URL: {r0.url}")

# Check cookies after warmup
print(f"\nSession cookies ({len(session.cookies)}):")
for c in session.cookies:
    print(f"  {c.name} @ {c.domain} = {str(c.value)[:40]}...")

# Now try the API
session.headers['Referer'] = f'https://weibo.com/u/{UID}'

# Test profile detail endpoint
print("\n=== Profile detail ===")
r = session.get(f'https://weibo.com/ajax/profile/detail?uid={UID}', timeout=15)
print(f"HTTP {r.status_code}")
if r.status_code == 200:
    try:
        d = r.json()
        print(f"ok={d.get('ok')}")
        if d.get('ok') == 1:
            user = d.get('data', {})
            print(json.dumps(user, ensure_ascii=False, indent=2)[:1000])
        else:
            print(json.dumps(d, ensure_ascii=False)[:500])
    except:
        print(r.text[:500])

# Test mymblog endpoint
print("\n=== Posts ===")
r = session.get(f'https://weibo.com/ajax/statuses/mymblog?uid={UID}&page=1&feature=0', timeout=15)
print(f"HTTP {r.status_code}")
if r.status_code == 200:
    try:
        d = r.json()
        print(f"ok={d.get('ok')}")
        if d.get('ok') == 1:
            posts = d.get('data', {}).get('list', [])
            print(f"Posts on page 1: {len(posts)}")
            if posts:
                p = posts[0]
                text = p.get('text_raw', '') or re.sub(r'<[^>]+>', '', p.get('text', ''))
                print(f"\nFirst post [{p.get('created_at','')}]:")
                print(text[:500])
        else:
            print(json.dumps(d, ensure_ascii=False)[:500])
    except:
        print(r.text[:500])

# Also try mobile API with cookies
print("\n=== Mobile API ===")
mobile_session = requests.Session()
mobile_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': f'https://m.weibo.cn/u/{UID}',
})
for c in cookie_list:
    # Set cookies for .weibo.cn domain (mobile)
    mobile_session.cookies.set(c['name'], c['value'], domain='.weibo.cn')
    mobile_session.cookies.set(c['name'], c['value'], domain='.weibo.com')

r = mobile_session.get(f'https://m.weibo.cn/api/container/getIndex?type=uid&value={UID}&containerid=1076037382396909', timeout=15)
print(f"HTTP {r.status_code}")
if r.status_code == 200:
    try:
        d = r.json()
        print(f"ok={d.get('ok')}")
        cards = d.get('data', {}).get('cards', [])
        print(f"Cards: {len(cards)}")
        for card in cards[:5]:
            if card.get('card_type') == 9:
                mblog = card.get('mblog', {})
                text = re.sub(r'<[^>]+>', '', mblog.get('text', ''))
                print(f"\n[{mblog.get('created_at','')}] {text[:300]}")
    except:
        print(r.text[:500])
