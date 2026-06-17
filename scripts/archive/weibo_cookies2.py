import os, sys, json, sqlite3, shutil, subprocess, re, time, requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'.'
UID = '1234567890'

# Edge cookies paths
EDGE_COOKIES_PATHS = [
    os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies'),
    os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cookies'),
]

def copy_locked_file(src):
    """Copy a file that's locked by another process using robocopy or VSS."""
    dst = os.path.join(OUTPUT_DIR, 'cookies_copy.db')
    if os.path.exists(dst):
        os.remove(dst)
    
    src_dir = os.path.dirname(src)
    src_name = os.path.basename(src)
    
    # Try robocopy (can copy locked files)
    result = subprocess.run(
        ['robocopy', src_dir, OUTPUT_DIR, src_name, '/B'],
        capture_output=True, text=True, timeout=10
    )
    # robocopy returns 0-7 for success
    copied = os.path.join(OUTPUT_DIR, src_name)
    if os.path.exists(copied) and copied != dst:
        os.rename(copied, dst)
        return dst
    
    # Fallback: try esentutl (Volume Shadow Copy)
    result = subprocess.run(
        ['esentutl', '/y', src, '/d', dst, '/o'],
        capture_output=True, text=True, timeout=10
    )
    if os.path.exists(dst):
        return dst
    
    return None

def get_weibo_cookies():
    for cookies_path in EDGE_COOKIES_PATHS:
        if not os.path.exists(cookies_path):
            continue
        
        print(f"Found cookies DB: {cookies_path}")
        
        # Try direct copy first
        tmp_db = os.path.join(OUTPUT_DIR, 'cookies_tmp.db')
        try:
            shutil.copy2(cookies_path, tmp_db)
        except PermissionError:
            print("File locked, trying robocopy...")
            tmp_db = copy_locked_file(cookies_path)
            if not tmp_db:
                print("All copy methods failed!")
                continue
        
        # Read cookies
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT host_key, name, value, encrypted_value FROM cookies WHERE host_key LIKE '%weibo%'")
        rows = cursor.fetchall()
        conn.close()
        
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        
        print(f"Found {len(rows)} Weibo cookie entries")
        
        cookies = {}
        for host, name, value, enc_value in rows:
            if value:
                cookies[name] = value
                continue
            if enc_value:
                # Try DPAPI
                try:
                    import win32crypt
                    decrypted = win32crypt.CryptUnprotectData(enc_value, None, None, None, 0)
                    cookies[name] = decrypted[1].decode('utf-8', errors='replace')
                    continue
                except:
                    pass
                
                # Try AES-GCM with Local State key
                try:
                    import win32crypt
                    import base64
                    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                    
                    ls_path = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Local State')
                    with open(ls_path, 'r') as f:
                        ls = json.load(f)
                    
                    enc_key = base64.b64decode(ls['os_crypt']['encrypted_key'])
                    enc_key = enc_key[5:]
                    key = win32crypt.CryptUnprotectData(enc_key, None, None, None, 0)[1]
                    
                    version = enc_value[:3]
                    if version in (b'v10', b'v20'):
                        nonce = enc_value[3:15]
                        ciphertext_with_tag = enc_value[15:]
                        
                        aesgcm = AESGCM(key)
                        dec = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
                        cookies[name] = dec.decode('utf-8', errors='replace')
                except Exception as e:
                    print(f"  Failed to decrypt {name}: {e}")
        
        return cookies
    
    return {}

# === Main ===
print("=== Extracting Weibo cookies from Edge ===")
cookies = get_weibo_cookies()

if not cookies:
    print("\nNo Weibo cookies found. You may not be logged into Weibo in Edge.")
    print("Please log into weibo.com in Edge first, then retry.")
    sys.exit(0)

print(f"\nDecrypted {len(cookies)} cookies:")
for k in sorted(cookies.keys()):
    v = cookies[k]
    print(f"  {k} = {v[:40]}{'...' if len(str(v)) > 40 else ''}")

# Fetch weibo posts
print("\n=== Fetching Weibo posts ===")
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://weibo.com/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json, text/plain, */*',
})
for k, v in cookies.items():
    session.cookies.set(k, str(v), domain='.weibo.com')

all_posts = []
for page_num in range(1, 11):
    url = f'https://weibo.com/ajax/statuses/mymblog?uid={UID}&page={page_num}&feature=0'
    r = session.get(url, timeout=15)
    
    if r.status_code != 200:
        print(f"Page {page_num}: HTTP {r.status_code}")
        if r.status_code == 403:
            print("  Session may be invalid. Try logging into weibo.com in Edge.")
        break
    
    try:
        data = r.json()
    except:
        print(f"Page {page_num}: not JSON: {r.text[:200]}")
        break
    
    posts = data.get('data', {}).get('list', [])
    if not posts:
        print(f"Page {page_num}: no posts (ok={data.get('ok')})")
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
        })
    
    print(f"Page {page_num}: {len(posts)} posts (total: {len(all_posts)})")
    with open(os.path.join(OUTPUT_DIR, f'weibo_ajax_page{page_num}.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    time.sleep(2)

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

print(f"\nTotal: {len(all_posts)} posts")
print(f"Corpus: {corpus_path}")
