import sqlite3
import os
import sys
import json
import shutil
import requests
import re
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUTPUT_DIR = r'D:\Agent+LLM_results\skill_communication'
UID = '7382396909'

# Try to decrypt Edge cookies on Windows
def get_edge_cookies():
    """Extract Weibo cookies from Edge's cookie database."""
    cookies_db = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies')
    if not os.path.exists(cookies_db):
        # Try alternate location
        cookies_db = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cookies')
    
    if not os.path.exists(cookies_db):
        print("Cookies DB not found!")
        return {}
    
    # Copy to temp to avoid lock issues
    tmp_db = os.path.join(OUTPUT_DIR, 'cookies_tmp.db')
    shutil.copy2(cookies_db, tmp_db)
    
    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()
    
    # Query weibo-related cookies
    cursor.execute("SELECT host_key, name, value, encrypted_value FROM cookies WHERE host_key LIKE '%weibo%'")
    rows = cursor.fetchall()
    conn.close()
    os.remove(tmp_db)
    
    print(f"Found {len(rows)} Weibo cookies")
    
    cookies = {}
    for host, name, value, encrypted_value in rows:
        if value:
            cookies[name] = value
        elif encrypted_value:
            # Try DPAPI decryption on Windows
            try:
                import win32crypt
                decrypted = win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)
                cookies[name] = decrypted[1].decode('utf-8', errors='replace')
            except ImportError:
                # Try with cryptography + local state key
                try:
                    local_state_path = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Local State')
                    if os.path.exists(local_state_path):
                        with open(local_state_path, 'r', encoding='utf-8') as f:
                            local_state = json.load(f)
                        
                        import base64
                        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                        
                        encrypted_key = base64.b64decode(local_state['os_crypt']['encrypted_key'])
                        encrypted_key = encrypted_key[5:]  # Remove 'DPAPI' prefix
                        
                        import win32crypt
                        key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
                        
                        # Decrypt cookie value (v10/v20 format)
                        nonce = encrypted_value[3:15]  # 'v10' or 'v20' prefix (3 bytes) + 12 byte nonce
                        ciphertext = encrypted_value[15:-16]
                        tag = encrypted_value[-16:]
                        
                        aesgcm = AESGCM(key)
                        decrypted = aesgcm.decrypt(nonce, ciphertext + tag, None)
                        cookies[name] = decrypted.decode('utf-8', errors='replace')
                except Exception as e2:
                    print(f"  Decrypt failed for {name}: {e2}")
            except Exception as e:
                print(f"  DPAPI decrypt failed for {name}: {e}")
    
    return cookies

# Main flow
print("Extracting Weibo cookies from Edge...")
cookies = get_edge_cookies()

if cookies:
    print(f"Decrypted {len(cookies)} cookies")
    for k in sorted(cookies.keys()):
        v = cookies[k]
        print(f"  {k}: {v[:50]}{'...' if len(v) > 50 else ''}")
    
    # Use cookies to fetch weibo via API
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://weibo.com/',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json',
    })
    for k, v in cookies.items():
        session.cookies.set(k, v, domain='.weibo.com')
    
    all_posts = []
    for page_num in range(1, 11):
        url = f'https://weibo.com/ajax/statuses/mymblog?uid={UID}&page={page_num}&feature=0'
        r = session.get(url, timeout=15)
        
        if r.status_code != 200:
            print(f"Page {page_num}: HTTP {r.status_code}")
            break
        
        try:
            data = r.json()
        except:
            print(f"Page {page_num}: not JSON")
            break
        
        posts = data.get('data', {}).get('list', [])
        if not posts:
            print(f"Page {page_num}: no posts (ok={data.get('ok')})")
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
    
    print(f"\nDone! Total: {len(all_posts)} posts")
    print(f"Corpus: {corpus_path}")
else:
    print("No Weibo cookies found. You may not be logged into Weibo in Edge.")
