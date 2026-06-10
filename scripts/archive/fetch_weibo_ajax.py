import requests, json, re, sys, time, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'.'
UID = '1234567890'

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
})

# Get visitor cookies for .weibo.com domain
r1 = session.post('https://visitor.passport.weibo.cn/visitor/genvisitor2', 
    data={
        'cb': 'visitor_gray_callback',
        'ver': '20250916',
        'request_id': 'ajax_test',
        'from': 'weibo',
        'webdriver': '0',
        'rid': str(int(time.time()*1000)),
        'return_url': 'https://weibo.com/u/' + UID
    }, timeout=15)

json_str = re.search(r'visitor_gray_callback\((.*)\)', r1.text, re.DOTALL)
vdata = json.loads(json_str.group(1))['data']
session.cookies.set('SUB', vdata['sub'], domain='.weibo.com')
session.cookies.set('SUBP', vdata['subp'], domain='.weibo.com')
print("Desktop visitor cookies obtained.")

# Try desktop weibo AJAX API
session.headers['Referer'] = 'https://weibo.com/'
session.headers['X-Requested-With'] = 'XMLHttpRequest'

all_posts = []

for page in range(1, 11):
    url = 'https://weibo.com/ajax/statuses/mymblog?uid={}&page={}&feature=0'.format(UID, page)
    r = session.get(url, timeout=15)
    
    if r.status_code != 200:
        print("Page {}: HTTP {}".format(page, r.status_code))
        break
    
    if not r.text:
        print("Page {}: empty response".format(page))
        break
    
    try:
        d = r.json()
    except:
        print("Page {}: not JSON".format(page))
        break
    
    ok = d.get('ok')
    posts = d.get('data', {}).get('list', [])
    
    if not posts:
        print("Page {}: no posts (ok={})".format(page, ok))
        break
    
    for p in posts:
        text = p.get('text_raw', '')
        if not text:
            text = re.sub(r'<[^>]+>', '', p.get('text', ''))
        
        all_posts.append({
            'created_at': p.get('created_at', ''),
            'text': text,
            'reposts_count': p.get('reposts_count', 0),
            'comments_count': p.get('comments_count', 0),
            'attitudes_count': p.get('attitudes_count', 0),
            'source': p.get('source', ''),
            'pic_ids': p.get('pic_ids', []),
            'is_long_text': p.get('isLongText', False),
            'mid': p.get('mid', ''),
        })
    
    print("Page {}: {} posts (total: {})".format(page, len(posts), len(all_posts)))
    
    # Save raw page
    with open(os.path.join(OUTPUT_DIR, 'weibo_ajax_page{}.json'.format(page)), 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    
    time.sleep(2)

# Fetch long text for posts that are truncated
print("\nFetching long text for truncated posts...")
for post in all_posts:
    if post['is_long_text'] and post['mid']:
        try:
            url = 'https://weibo.com/ajax/statuses/longtext?id={}'.format(post['mid'])
            r = session.get(url, timeout=10)
            if r.status_code == 200:
                ld = r.json()
                long_text = ld.get('data', {}).get('longTextContent', '')
                if long_text:
                    post['text'] = re.sub(r'<[^>]+>', '', long_text)
                    print("  Long text fetched for mid={}".format(post['mid']))
            time.sleep(1)
        except:
            pass

# Compile corpus
corpus_path = os.path.join(OUTPUT_DIR, 'weibo_corpus.txt')
with open(corpus_path, 'w', encoding='utf-8') as f:
    f.write("# Weibo Corpus\n")
    f.write("# Username: {}\n".format('if_known'))
    f.write("# UID: {}\n".format(UID))
    f.write("# Collected: {}\n".format(time.strftime('%Y-%m-%d %H:%M')))
    f.write("# Total posts: {}\n".format(len(all_posts)))
    f.write("=" * 60 + "\n\n")
    
    for i, post in enumerate(all_posts, 1):
        f.write("--- Post {} [{}] ---\n".format(i, post['created_at']))
        f.write("Reposts:{} Comments:{} Likes:{}\n".format(
            post['reposts_count'], post['comments_count'], post['attitudes_count']))
        f.write("{}\n".format(post['text']))
        if post['pic_ids']:
            f.write("[Images: {}]\n".format(len(post['pic_ids'])))
        f.write("Source: {}\n\n".format(post['source']))

print("\nTotal posts collected: {}".format(len(all_posts)))
print("Corpus saved to: {}".format(corpus_path))
