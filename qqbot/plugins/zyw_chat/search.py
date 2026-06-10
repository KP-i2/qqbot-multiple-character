# DeepSeek API — 持久化客户端 + 重试
# ============================================================

_http_client: Optional[httpx.AsyncClient] = None
_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 2  # 秒

# ── 流式输出 ──
_STREAM_ENABLED = str(getattr(config, "deepseek_stream", "true")).lower() == "true"
_STREAM_FLUSH_CHARS = 60      # 累积多少字符后寻找句末断点
_STREAM_FLUSH_INTERVAL = 8.0  # 最长等待秒数（即使没到句末也发送，但需满足最低字符数+软断点）
_STREAM_FLUSH_MIN_CHARS = 80  # 时间触发刷新的最低字符数（避免太短片段被超时发出）
_STREAM_SOFT_BREAKS = set("，,；;：:、\n ")  # 软断点字符（time_flush 在最近20字内需命中其中一个）
_STREAM_MAX_FLUSH_SIZE = 300  # 单段最大字符数（强制断句）

# ── 并发控制 ──
_API_SEMAPHORE = asyncio.Semaphore(30)       # 全局最多 30 个 API 请求同时进行
_user_processing: dict[str, asyncio.Lock] = {}  # 每个用户一把锁，防止同一用户连发消息重复调 API


def _get_user_lock(uid: str) -> asyncio.Lock:
    """获取/创建用户级别的锁（惰性初始化）"""
    if uid not in _user_processing:
        _user_processing[uid] = asyncio.Lock()
    return _user_processing[uid]


# ── 情绪表情系统 ──

_EMOJI_DIR = Path(os.environ.get("EMOJI_DIR", r"D:\agent_function\skill_communication\emoji"))
_EMOJI_PROBABILITY = 0.50  # 50% 概率发送表情
_EMOJI_COOLDOWN = {}       # per-user cooldown to avoid spamming
_EMOJI_IMG_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

# 内置默认关键词（当文件夹没有 keywords.txt 时使用）
_EMOJI_DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "angry": [
        "生气", "气死", "烦死", "讨厌", "无语", "受不了", "怒", "滚", "去死",
        "混蛋", "靠", "恼火", "火大", "发火", "暴躁", "气炸", "不爽", "烦人",
        "找打", "想打人", "想锤", "想揍", "拳头", "拳头硬了",
    ],
    "sad": [
        "难过", "伤心", "呜呜", "哭", "委屈", "可怜", "心疼", "遗憾", "可惜",
        "唉", "惨", "悲伤", "郁闷", "失落", "泪", "心酸", "emo", "破防",
        "想哭", "哭了", "好惨", "太惨", "悲惨",
    ],
    "happy": [
        "开心", "高兴", "太好了", "哈哈", "嘻嘻", "嘿嘿", "好耶", "耶",
        "喜欢", "爱了", "甜", "暖", "完美", "厉害", "太棒了", "赞",
        "幸福", "快乐", "好喜欢", "超爱", "可爱", "贴贴", "mua",
    ],
    "joker": [
        "笑死", "离谱", "绝了", "抽象", "牛", "666", "乐子", "整活",
        "乐", "哈哈哈哈哈", "笑喷", "绷不住", "搞笑", "太搞笑了", "草",
        "逆天", "人才", "鬼才", "秀", "整挺好", "会整活",
    ],
}

# 运行时动态数据（由 _load_emoji_files 填充）
_EMOJI_FILES: dict[str, list[Path]] = {}       # emotion_name -> [image_paths]
_EMOJI_EMOTIONS: dict[str, list[str]] = {}     # emotion_name -> [keywords]


def _load_emoji_files():
    """扫描表情目录，自动发现所有子文件夹并加载图片和关键词。
    
    每个子文件夹：
    - 文件夹名 = 情绪类别
    - keywords.txt = 关键词文件（每行一个关键词，# 开头为注释）
    - 图片文件 = jpg/jpeg/png/gif/webp
    """
    global _EMOJI_FILES, _EMOJI_EMOTIONS
    _EMOJI_FILES = {}
    _EMOJI_EMOTIONS = {}

    if not _EMOJI_DIR.exists():
        logger.warning(f"[EMOJI] 表情目录不存在: {_EMOJI_DIR}")
        return

    for folder in sorted(_EMOJI_DIR.iterdir()):
        if not folder.is_dir():
            continue
        emotion = folder.name

        # 加载图片
        imgs = [f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() in _EMOJI_IMG_EXTS]
        if not imgs:
            continue
        _EMOJI_FILES[emotion] = imgs

        # 加载关键词：优先 keywords.txt，否则用内置默认
        kw_file = folder / "keywords.txt"
        if kw_file.exists():
            try:
                keywords = []
                for line in kw_file.read_text(encoding='utf-8').splitlines():
                    line = line.strip()
                    if line and not line.startswith('#'):
                        keywords.append(line)
                if keywords:
                    _EMOJI_EMOTIONS[emotion] = keywords
                    logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, {len(keywords)} 关键词 (from keywords.txt)")
                else:
                    _EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
                    logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, keywords.txt 为空，使用默认关键词")
            except Exception as e:
                logger.warning(f"[EMOJI] 读取 {emotion}/keywords.txt 失败: {e}")
                _EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
        else:
            _EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
            logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, 使用默认关键词")

    total_imgs = sum(len(v) for v in _EMOJI_FILES.values())
    emotions = list(_EMOJI_FILES.keys())
    logger.info(f"[EMOJI] 加载完成: {total_imgs} 个表情, {len(emotions)} 种情绪: {emotions}")


def _detect_emotion(user_text: str, bot_reply: str) -> str | None:
    """根据用户消息和 bot 回复检测情绪，多个命中时随机选一个。"""
    import random as _rand
    combined = (user_text + " " + bot_reply).lower()
    matched = []
    for emotion, keywords in _EMOJI_EMOTIONS.items():
        for kw in keywords:
            if kw.lower() in combined:
                matched.append(emotion)
                break  # 同一情绪只计一次
    return _rand.choice(matched) if matched else None


async def _maybe_send_emoji(user_text: str, bot_reply: str, uid: str):
    """根据对话情绪概率发送表情图片。"""
    import random

    # 冷却检查（同一用户 60 秒内最多发一次表情）
    now = time.time()
    last_sent = _EMOJI_COOLDOWN.get(uid, 0)
    if now - last_sent < 60:
        return

    # 概率判断
    if random.random() > _EMOJI_PROBABILITY:
        return

    # 情绪检测
    emotion = _detect_emotion(user_text, bot_reply)
    if not emotion:
        return

    # 获取对应情绪的表情文件
    files = _EMOJI_FILES.get(emotion, [])
    if not files:
        return

    # 随机选一张
    img_path = random.choice(files)
    try:
        # GIF 用 base64 发送以保证动画效果，其他格式用 file URI
        if img_path.suffix.lower() == '.gif':
            img_bytes = img_path.read_bytes()
            b64 = base64.b64encode(img_bytes).decode('ascii')
            await zyw_chat.send(MessageSegment.image(file=f"base64://{b64}"))
        else:
            file_uri = f"file:///{img_path.as_posix()}"
            await zyw_chat.send(MessageSegment.image(file=file_uri))
        _EMOJI_COOLDOWN[uid] = now
        chat_logger.info(f"[EMOJI] 发送表情: emotion={emotion}, file={img_path.name}, user={uid}")
    except Exception as e:
        logger.warning(f"[EMOJI] 发送失败: {e}")

# ── Function Calling 工具定义 ──
_SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "在网络上搜索实时信息。系统会从百度、微博、DuckDuckGo 等多个来源并行搜索，"
                "并自动抓取百科类页面的详细内容。"
                "当用户询问偶像、艺人、演出活动、最新新闻等话题时应主动使用。"
                "遇到不确定的话题应主动搜索，宁可多搜也不要编造。"
                "搜索结果包含可点击的链接，可以提供给用户参考。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "搜索关键词。要求：1.尽量简短，只写核心名称（如'阵雨电台'而非'阵雨电台 地下偶像'）"
                            "2.不要加修饰词（如'官方''是谁''介绍'等）3.微博搜索对短关键词效果更好，"
                            "关键词越长越容易搜不到结果"
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果数量，默认 8。需要详细信息时建议设为 10",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_corpus",
            "description": (
                "搜索角色的语料库/知识库（包括角色设定、背景故事、工作经历等所有资料文件）。"
                "当用户询问与角色自身相关的问题时使用此工具，确保回答与角色设定一致。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "string",
                        "description": "搜索关键词，可以是空格分隔的多个关键词",
                    },
                },
                "required": ["keywords"],
            },
        },
    },
]
_MAX_TOOL_ROUNDS = 3  # 增加到 3 轮，支持复杂查询的多步搜索


# ── 联网搜索优先站点（搜狗 site: 搜索） ──
# 注：chinaidols.fandom.com 被 Cloudflare 403 封锁，cmks.top SSL 证书失效，均已移除
_PRIORITY_SITES = [
    "weibo.com",
    "baike.baidu.com",
]

# ── 微博直搜（使用 cookie 调微博 API，不走 DuckDuckGo） ──
_WEIBO_COOKIES_FILE = Path(r"D:\agent_function\skill_communication\cookies.json")
_weibo_cookies_cache: Optional[dict] = None  # {"cookie_str": ..., "xsrf": ...}
_weibo_cookies_mtime: float = 0


def _load_weibo_cookies() -> Optional[dict]:
    """加载微博 cookie，返回 {"cookie_str": ..., "xsrf": ...}，带文件变更缓存"""
    global _weibo_cookies_cache, _weibo_cookies_mtime
    if not _WEIBO_COOKIES_FILE.exists():
        return None
    try:
        mtime = _WEIBO_COOKIES_FILE.stat().st_mtime
        if _weibo_cookies_cache and mtime == _weibo_cookies_mtime:
            return _weibo_cookies_cache
        with open(_WEIBO_COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies_list = json.load(f)
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies_list)
        xsrf = ""
        for c in cookies_list:
            if c["name"] == "XSRF-TOKEN":
                xsrf = c["value"]
                break
        _weibo_cookies_cache = {"cookie_str": cookie_str, "xsrf": xsrf}
        _weibo_cookies_mtime = mtime
        return _weibo_cookies_cache
    except Exception as e:
        logger.warning(f"[weibo] 加载 cookies.json 失败: {e}")
        return None


async def _search_weibo_direct(query: str, max_results: int = 5, timeout_s: float = 30) -> list:
    """用 cookie 调微博全局搜索 API (statuses/search)，返回统一格式的结果列表"""
    cookie_data = _load_weibo_cookies()
    if not cookie_data:
        logger.warning("[weibo] cookies.json 不存在或加载失败，跳过微博直搜")
        return []

    from urllib.parse import urlencode
    params = {"q": query, "count": max_results, "page": 1}
    url = f"https://weibo.com/ajax/statuses/search?{urlencode(params)}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Cookie": cookie_data["cookie_str"],
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://weibo.com/",
    }
    if cookie_data.get("xsrf"):
        headers["X-XSRF-TOKEN"] = cookie_data["xsrf"]

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code in (301, 302, 303):
            logger.warning(f"[weibo] cookie 已过期 (HTTP {resp.status_code})，需要更新 cookies.json")
            return []

        resp.raise_for_status()
        data = resp.json()

        # 检测 cookie 过期：ok=-100 表示未登录
        if data.get("ok") == -100:
            logger.warning("[weibo] cookie 已过期 (ok=-100)，需要更新 cookies.json")
            return []

        if data.get("ok") != 1:
            raw = json.dumps(data, ensure_ascii=False)[:300]
            logger.warning(f"[weibo] API 返回异常: {raw}")
            return []

        results = []
        items = data.get("statuses", [])
        total = data.get("total_number", 0)
        for item in items:
            if len(results) >= max_results:
                break
            _extract_weibo_item(item, results, max_results)

        logger.info(f"[weibo] 直搜完成: query='{query[:40]}' total={total} items={len(items)} results={len(results)}")
        return results

    except httpx.TimeoutException:
        logger.warning(f"[weibo] 直搜超时 ({timeout_s}s): {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[weibo] 直搜失败: {e}")
        return []


def _extract_weibo_item(item: dict, results: list, max_results: int):
    """从微博桌面端搜索结果 item 提取搜索条目"""
    if len(results) >= max_results:
        return
    # 桌面端 API 提供 text_raw（纯文本）和 text（含 HTML），优先用 text_raw
    text = item.get("text_raw", "")
    if not text:
        import re as _re
        text = _re.sub(r'<[^>]+>', '', item.get("text", "")).strip()
    user_info = item.get("user", {})
    username = user_info.get("screen_name", "未知用户")
    mid = item.get("mid") or item.get("id", "")
    link = f"https://weibo.com/{user_info.get('id', '')}/{mid}" if mid and user_info.get("id") else ""
    snippet = text[:200] + ("..." if len(text) > 200 else "")
    results.append({
        "title": f"@{username}: {text[:50]}...",
        "href": link,
        "url": link,
        "body": snippet,
    })


# ── 微博用户搜索（已废弃，endpoint 返回 404）──
async def _search_weibo_user(query: str, max_results: int = 3, timeout_s: float = 20) -> list:
    """搜索微博用户 — endpoint /ajax/side/cards/searchUser 已下线，始终返回空"""
    logger.debug(f"[weibo] 用户搜索已废弃，跳过: {query[:40]}")
    return []


# ── 关键词简化 ──
_GENERIC_SUFFIXES = {"官方", "是谁", "介绍", "资料", "简介", "哪里人", "怎么样", "什么"}


def _extract_core_keywords(query: str) -> str:
    """从搜索查询中提取核心关键词，用于重试简化。
    '阵雨电台 地下偶像' → '阵雨电台'
    'XXX 是谁' → 'XXX'
    """
    parts = query.strip().split()
    if len(parts) <= 1:
        return query

    # 去除空格分隔的通用修饰词
    filtered = [p for p in parts if p not in _GENERIC_SUFFIXES]
    if not filtered:
        return parts[0]

    # 返回第一个核心词（通常是主体名称）
    return filtered[0]


async def _search_sogou(query: str, max_results: int = 10, timeout_s: float = 12) -> list:
    """搜索搜狗网页，解析 HTML 返回结构化结果列表"""
    from urllib.parse import quote_plus

    url = f"https://www.sogou.com/web?query={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text
    except httpx.TimeoutException:
        logger.warning(f"[sogou] 搜索超时: {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[sogou] 搜索失败: {e}")
        return []

    # 轻量级 HTML 解析
    _TAG_RE = re.compile(r'<[^>]+>')
    _ENTITY_RE = re.compile(r'&(?:nbsp|amp|lt|gt|quot|#\d+|#x[\da-fA-F]+);')

    def _clean(text: str) -> str:
        text = _TAG_RE.sub('', text)
        text = _ENTITY_RE.sub(' ', text)
        return ' '.join(text.split())

    results = []
    # 搜狗搜索结果以 class 含 vrwrap 或 rb 的容器标记
    containers = re.split(r'<div[^>]*class="[^"]*(?:vrwrap|rb)[^"]*"', html)
    for block in containers[1:]:  # 第一段是页面头部，跳过
        # 提取标题（<h3> 标签内容）
        h3 = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        if not h3:
            continue
        title = _clean(h3.group(1))
        if not title:
            continue
        # 提取链接（h3 内的 <a> href）
        link = ""
        href_match = re.search(r'<a[^>]*href="([^"]*)"', h3.group(0), re.DOTALL)
        if href_match:
            link = href_match.group(1)
            # 搜狗链接可能是跳转格式，需要 follow
            if link.startswith("/link?url="):
                link = f"https://www.sogou.com{link}"
        # 提取摘要
        snippet = ""
        # 尝试匹配摘要容器
        for sm in re.finditer(r'<(?:p|span|div)[^>]*class="[^"]*(?:str-text|str_info|text-layout|space-txt)[^"]*"[^>]*>(.*?)</(?:p|span|div)>', block, re.DOTALL):
            t = _clean(sm.group(1))
            if len(t) > 15:
                snippet = t
                break
        if not snippet:
            # 备用：取第一个较长的文本段
            for sm in re.finditer(r'<(?:span|p|div)[^>]*>(.*?)</(?:span|p|div)>', block, re.DOTALL):
                t = _clean(sm.group(1))
                if len(t) > 25 and t != title:
                    snippet = t
                    break
        results.append({
            "title": title,
            "href": link,
            "url": link,
            "body": snippet[:300],
        })
        if len(results) >= max_results:
            break

    logger.info(f"[sogou] query='{query[:40]}' results={len(results)}")
    return results


# ── 百度搜索（替代搜狗作为主力搜索引擎） ──
async def _search_baidu(query: str, max_results: int = 10, timeout_s: float = 15) -> list:
    """搜索百度网页，解析 HTML 返回结构化结果列表。
    过滤掉百度图片、百度视频等非网页搜索结果。
    """
    from urllib.parse import quote_plus

    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={max_results}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text
    except httpx.TimeoutException:
        logger.warning(f"[baidu] 搜索超时: {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[baidu] 搜索失败: {e}")
        return []

    # 轻量级 HTML 解析
    _TAG_RE = re.compile(r'<[^>]+>')
    _ENTITY_RE = re.compile(r'&(?:nbsp|amp|lt|gt|quot|#\d+|#x[\da-fA-F]+);')

    def _clean(text: str) -> str:
        text = _TAG_RE.sub('', text)
        text = _ENTITY_RE.sub(' ', text)
        return ' '.join(text.split())

    # 过滤非网页搜索结果（图片搜索、视频搜索等）
    _BAIDU_NOISE_RE = re.compile(r'image\.baidu\.com|/sf/vsearch\?|tn=baiduimage')

    results = []
    # 百度搜索结果以 class="result c-container" 或 class="c-container" 分隔
    blocks = re.split(
        r'<div[^>]*class="[^"]*(?:result\s+c-container|c-container)[^"]*"[^>]*>',
        html,
    )

    for block in blocks[1:]:  # 第一段是页面头部，跳过
        # 提取标题（<h3 class="t">）
        h3_match = re.search(
            r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>(.*?)</h3>', block, re.DOTALL
        )
        if not h3_match:
            h3_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        if not h3_match:
            continue
        title = _clean(h3_match.group(1))
        if not title:
            continue

        # 提取链接
        link = ""
        href_match = re.search(r'<a[^>]*href="([^"]*)"', h3_match.group(0))
        if href_match:
            link = href_match.group(1)

        # 过滤噪音结果（图片/视频聚合等）
        if _BAIDU_NOISE_RE.search(link) or _BAIDU_NOISE_RE.search(title):
            continue

        # 提取摘要：依次尝试多种百度摘要容器
        snippet = ""
        _snippet_patterns = [
            r'<span[^>]*class="[^"]*content-right_[^"]*"[^>]*>(.*?)</span>',
            r'<div[^>]*class="[^"]*c-abstract[^"]*"[^>]*>(.*?)</div>',
        ]
        for pat in _snippet_patterns:
            for sm in re.finditer(pat, block, re.DOTALL):
                t = _clean(sm.group(1))
                if len(t) > 30 and t != title:
                    snippet = t
                    break
            if snippet:
                break

        if not snippet:
            # 备用：取 block 中较长的纯文本段
            for sm in re.finditer(r'>([^<]{40,})<', block):
                t = sm.group(1).strip()
                if t and t != title and not t.startswith('{') and not t.startswith('var '):
                    snippet = t
                    break

        results.append({
            "title": title,
            "href": link,
            "url": link,
            "body": snippet[:300] if snippet else "",
        })
        if len(results) >= max_results:
            break

    logger.info(f"[baidu] query='{query[:40]}' results={len(results)}")
    return results


# ── 页面正文抓取（从搜索结果 URL 提取全文，用于高价值页面） ──
_HIGH_VALUE_DOMAINS = [
    "fandom.com", "baike.baidu.com", "wiki", "zh.wikipedia.org",
]
_PAGE_FETCH_TIMEOUT = 8        # 单页抓取超时（秒）
_PAGE_CONTENT_MAX_CHARS = 1500  # 单页正文最大字符数


async def _fetch_page_content(url: str, timeout_s: int = _PAGE_FETCH_TIMEOUT) -> tuple[str, str]:
    """抓取 URL 页面并提取正文文本。返回 (final_url, content)"""
    if not url or url.startswith("/sf/") or url.startswith("javascript:"):
        return ("", "")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return ("", "")
        final_url = str(resp.url)
        html = resp.text

        # 去 <script> <style>
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)

        # 标题
        title = ""
        for h_match in re.finditer(r'<h[12][^>]*>(.*?)</h[12]>', html, re.DOTALL):
            t = re.sub(r'<[^>]+>', '', h_match.group(1))
            t = re.sub(r'&\w+;', ' ', t).strip()
            if len(t) > 3:
                title = t
                break

        # 正文
        body_parts = []
        for p_match in re.finditer(r'<p[^>]*>(.*?)</p>', html, re.DOTALL):
            t = re.sub(r'<[^>]+>', '', p_match.group(1))
            t = re.sub(r'&\w+;', ' ', t).strip()
            t = ' '.join(t.split())
            if len(t) > 20 and t != title:
                body_parts.append(t)

        if len(''.join(body_parts)) < 80:
            for div_match in re.finditer(r'<div[^>]*>(.*?)</div>', html, re.DOTALL):
                t = re.sub(r'<[^>]+>', '', div_match.group(1))
                t = re.sub(r'&\w+;', ' ', t).strip()
                t = ' '.join(t.split())
                if len(t) > 40 and t not in body_parts:
                    body_parts.append(t)
                    if len(''.join(body_parts)) > _PAGE_CONTENT_MAX_CHARS:
                        break

        content = '\n'.join(body_parts)
        if title:
            content = f"【{title}】\n{content}"
        if len(content) > _PAGE_CONTENT_MAX_CHARS:
            content = content[:_PAGE_CONTENT_MAX_CHARS]
            last_period = max(content.rfind('。'), content.rfind('\n'))
            if last_period > _PAGE_CONTENT_MAX_CHARS * 0.6:
                content = content[: last_period + 1]
        return (final_url, content)
    except Exception as e:
        logger.debug(f"[fetch_page] 抓取失败 {url[:60]}: {e}")
        return ("", "")


def _is_high_value_url(url: str) -> bool:
    """判断 URL 是否属于值得抓取全文的高价值域名"""
    if not url:
        return False
    lower = url.lower()
    return any(domain in lower for domain in _HIGH_VALUE_DOMAINS)


# ── URL 提取与内容抓取 ──

_URL_PATTERN = re.compile(r'https?://[^\s<>]+')
_URL_MAX_FETCH = 2          # 每条消息最多抓取 URL 数
_URL_OVERALL_TIMEOUT = 10   # URL 处理总超时（秒）
_URL_CONTENT_MAX_CHARS = 2000  # URL 内容注入最大字符

_BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def _extract_urls(text: str) -> list[str]:
    """从文本中提取 URL，去重，最多返回 _URL_MAX_FETCH 个。"""
    urls = _URL_PATTERN.findall(text)
    seen = set()
    result = []
    for u in urls:
        u = u.rstrip('.,;:)!?\u3002\uff0c\uff01\uff1f\u3001\uff09')  # 去掉尾部标点
        if u not in seen:
            seen.add(u)
            result.append(u)
            if len(result) >= _URL_MAX_FETCH:
                break
    return result


def _parse_bvid(url: str) -> str | None:
    """从 B 站 URL 中提取 BV 号。"""
    m = re.search(r'(BV[a-zA-Z0-9]{10})', url)
    return m.group(1) if m else None


async def _fetch_bilibili_info(bvid: str) -> str | None:
    """通过 B 站 API 获取视频信息（标题、简介、标签、热评）。"""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            # 第一步：获取视频信息
            info_resp = await client.get(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers=_BILIBILI_HEADERS,
            )

            if info_resp.status_code != 200:
                return None
            data = info_resp.json()
            if data.get("code") != 0:
                return None

            video_info = data["data"]
            aid = video_info.get("aid")

            title = video_info.get("title", "")
            desc = video_info.get("desc", "")
            owner = video_info.get("owner", {}).get("name", "")
            stat = video_info.get("stat", {})
            view = stat.get("view", 0)
            like = stat.get("like", 0)
            coin = stat.get("coin", 0)
            danmaku = stat.get("danmaku", 0)
            reply_count = stat.get("reply", 0)

            # 格式化播放量
            if view >= 10000:
                view_str = f"{view / 10000:.1f}万"
            else:
                view_str = str(view)

            parts = [f"【B站视频】{title}"]
            parts.append(f"UP主：{owner}")
            parts.append(f"播放 {view_str} · 点赞 {like} · 投币 {coin} · 弹幕 {danmaku} · 评论 {reply_count}")

            if desc and desc.strip():
                desc_clean = desc.strip()
                if len(desc_clean) > 400:
                    desc_clean = desc_clean[:400] + "..."
                parts.append(f"\n简介：{desc_clean}")

            # 第二步：并行获取评论 + 标签
            comments = []
            comment_task = None
            tag_task = None

            if aid:
                comment_task = asyncio.create_task(
                    client.get(
                        f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=1&ps=5",
                        headers=_BILIBILI_HEADERS,
                    )
                )
            tag_task = asyncio.create_task(
                client.get(
                    f"https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}",
                    headers=_BILIBILI_HEADERS,
                )
            )

            # 等待评论和标签结果
            if comment_task:
                try:
                    cr = await comment_task
                    if cr.status_code == 200:
                        cdata = cr.json()
                        if cdata.get("code") == 0:
                            for r in (cdata.get("data", {}).get("replies", None) or [])[:5]:
                                msg = r.get("content", {}).get("message", "")
                                if msg and len(msg) > 5:
                                    uname = r.get("member", {}).get("uname", "")
                                    comments.append(f"  {uname}：{msg[:100]}")
                except Exception:
                    pass

            if comments:
                parts.append("\n热门评论：")
                parts.extend(comments)

            # 标签
            try:
                tag_resp = await tag_task
                if tag_resp.status_code == 200:
                    tag_data = tag_resp.json()
                    if tag_data.get("code") == 0:
                        tags = [t["tag_name"] for t in tag_data.get("data", [])[:8]]
                        if tags:
                            parts.append(f"\n标签：{', '.join(tags)}")
            except Exception:
                pass

            return "\n".join(parts)

    except Exception as e:
        logger.warning(f"[URL] B站 API 失败 {bvid}: {e}")
        return None


async def _resolve_short_url(url: str) -> str:
    """解析短链接，返回最终 URL。"""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            resp = await client.head(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            return str(resp.url)
    except Exception:
        return url


async def _fetch_url_content(url: str) -> str | None:
    """抓取 URL 内容：B 站走专用 API，其他走通用页面抓取。"""
    lower = url.lower()

    # B 站短链先解析
    if "b23.tv" in lower:
        resolved = await _resolve_short_url(url)
        if "bilibili" in resolved.lower():
            bvid = _parse_bvid(resolved)
            if bvid:
                return await _fetch_bilibili_info(bvid)
        # 短链解析后不是 B 站，走通用抓取
        _, content = await _fetch_page_content(resolved)
        return content if content else None

    # B 站长链
    if "bilibili.com" in lower:
        bvid = _parse_bvid(url)
        if bvid:
            return await _fetch_bilibili_info(bvid)

    # 通用页面抓取
    _, content = await _fetch_page_content(url)
    return content if content else None


async def _process_message_urls(urls: list[str]) -> str | None:
    """处理消息中的所有 URL，返回拼接后的上下文文本。"""
    if not urls:
        return None

    tasks = [_fetch_url_content(u) for u in urls[:_URL_MAX_FETCH]]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_URL_OVERALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[URL] 处理超时 ({_URL_OVERALL_TIMEOUT}s)")
        return None

    parts = []
    for url, result in zip(urls, results):
        if isinstance(result, str) and result:
            parts.append(f"[用户分享链接 {url} 的内容：]\n{result}")
        else:
            parts.append(f"[用户分享了一个链接 {url}，但未能获取内容]")

    combined = "\n\n".join(parts)
    if len(combined) > _URL_CONTENT_MAX_CHARS:
        combined = combined[:_URL_CONTENT_MAX_CHARS] + "\n...(内容过长已截断)"
    return combined


# ── DuckDuckGo Instant Answer API（免费，无需爬虫） ──
async def _search_ddg_instant(query: str, timeout_s: float = 8) -> list:
    """DuckDuckGo Instant Answer API，返回实体摘要和相关主题。"""
    from urllib.parse import quote_plus
    url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []

        abstract = data.get("AbstractText", "")
        if abstract and len(abstract) > 30:
            results.append({
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "body": abstract[:600],
                "source": "DDG",
            })

        for topic in data.get("RelatedTopics", [])[:5]:
            if "Text" in topic and len(topic["Text"]) > 20:
                results.append({
                    "title": topic["Text"][:60],
                    "url": topic.get("FirstURL", ""),
                    "body": topic["Text"][:400],
                    "source": "DDG",
                })
            elif "Topics" in topic:
                for sub in topic["Topics"][:3]:
                    if "Text" in sub and len(sub["Text"]) > 20:
                        results.append({
                            "title": sub["Text"][:60],
                            "url": sub.get("FirstURL", ""),
                            "body": sub["Text"][:400],
                            "source": "DDG",
                        })
        logger.info(f"[ddg] query='{query[:40]}' results={len(results)}")
        return results
    except Exception as e:
        logger.warning(f"[ddg] 搜索失败: {e}")
        return []


async def _execute_web_search(query: str, max_results: int = 5) -> str:
    """异步执行联网搜索：百度主力 + 微博补充 + 搜狗兜底 + 页面内容抓取。

    搜索策略：
    1. 第一轮（并行）：百度 + 微博
    2. 页面增强：对疑似百科类页面并行抓取正文
    3. 兜底：若百度无结果，启用搜狗
    """

    _TIMEOUT = 15  # 搜索超时（秒）

    # ── 带超时的百度搜索 ──
    async def _baidu_with_timeout(q: str, max_r: int) -> tuple[str, list]:
        try:
            res = await asyncio.wait_for(
                _search_baidu(q, max_results=max_r, timeout_s=_TIMEOUT),
                timeout=_TIMEOUT + 3,
            )
            return (q, res)
        except asyncio.TimeoutError:
            logger.warning(f"[web_search] 百度搜索超时 ({_TIMEOUT}s): {q[:60]}")
            return (q, [])
        except Exception as e:
            logger.warning(f"[web_search] 百度搜索失败: {e}")
            return (q, [])

    # ── 带超时的搜狗搜索（兜底用） ──
    async def _sogou_with_timeout(q: str, max_r: int) -> tuple[str, list]:
        try:
            res = await asyncio.wait_for(
                _search_sogou(q, max_results=max_r, timeout_s=12),
                timeout=14,
            )
            return (q, res)
        except asyncio.TimeoutError:
            logger.warning(f"[web_search] 搜狗搜索超时: {q[:60]}")
            return (q, [])
        except Exception as e:
            logger.warning(f"[web_search] 搜狗搜索失败: {e}")
            return (q, [])

    # ── 微博搜索降级链 ──
    _weibo_cookie_expired = False  # 追踪 cookie 状态

    async def _weibo_wrapper() -> tuple[str, list]:
        nonlocal _weibo_cookie_expired
        # 检查 cookie 是否存在
        cookie_data = _load_weibo_cookies()
        if not cookie_data:
            _weibo_cookie_expired = True
            return ("weibo_direct", [])

        res = await _search_weibo_direct(query, max_results=8, timeout_s=_TIMEOUT)
        if res:
            return ("weibo_direct", res)
        # 检查是否因 cookie 过期导致无结果
        if cookie_data and not res:
            _weibo_cookie_expired = True  # 标记可能过期
        simplified = _extract_core_keywords(query)
        if simplified != query:
            res = await _search_weibo_direct(simplified, max_results=8, timeout_s=_TIMEOUT)
            if res:
                _weibo_cookie_expired = False  # 简化词有结果，cookie 正常
                logger.info(f"[weibo] 简化关键词命中: '{query}' → '{simplified}'")
                return ("weibo_direct", res)
        logger.info(f"[weibo] 所有搜索均未命中: query='{query}'")
        return ("weibo_direct", [])

    # 检查健康状态
    baidu_ok = _is_search_healthy("baidu")
    sogou_ok = _is_search_healthy("sogou")
    weibo_ok = _is_search_healthy("weibo")
    if not baidu_ok:
        logger.info("[web_search] 百度不健康，跳过")
    if not sogou_ok:
        logger.info("[web_search] 搜狗不健康，跳过")
    if not weibo_ok:
        logger.info("[web_search] 微博不健康，跳过")

    # ── 第一轮：百度 + 微博 并行 ──
    round1_tasks = []
    if baidu_ok:
        round1_tasks.append(_baidu_with_timeout(query, max_results + 5))
    if weibo_ok:
        round1_tasks.append(_weibo_wrapper())
    round1_results = await asyncio.gather(*round1_tasks) if round1_tasks else []

    results = []
    seen_urls = set()
    baidu_total = 0
    weibo_total = 0

    for _q, items in round1_results:
        if _q == "weibo_direct":
            weibo_total = len(items)
            for r in items:
                url = r.get("href") or r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(("微博", r))
        else:
            baidu_total = len(items)
            for r in items:
                url = r.get("href") or r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(("百度", r))

    # 更新健康状态
    if baidu_ok:
        _update_search_health("baidu", baidu_total > 0)
    if weibo_ok:
        _update_search_health("weibo", weibo_total > 0)

    # ── 第二轮：搜狗兜底（仅百度无结果时） ──
    sogou_total = 0
    if baidu_total == 0 and sogou_ok:
        logger.info("[web_search] 百度无结果，启用搜狗兜底")
        _, sogou_items = await _sogou_with_timeout(query, max_results + 3)
        sogou_total = len(sogou_items)
        for r in sogou_items:
            url = r.get("href") or r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(("搜狗", r))
        _update_search_health("sogou", sogou_total > 0)

    if not results:
        return "未找到相关结果。"

    # ── 页面内容增强 ──
    # 选取疑似百科/资料页的结果进行全文抓取。
    # 百度链接是重定向链接，通过标题关键词判断是否值得抓取。
    _ENRICH_TITLE_KEYWORDS = [
        "wiki", "百科", "fandom", "维基百科", "百度百",
        "官方", "官网", "简介", "资料", "profile",
    ]

    def _should_enrich(r: dict) -> bool:
        """判断搜索结果是否值得抓取全文"""
        title = (r.get("title") or "").lower()
        url = (r.get("href") or r.get("url") or "").lower()
        if _is_high_value_url(url):
            return True
        return any(kw in title for kw in _ENRICH_TITLE_KEYWORDS)

    fetch_targets = []  # [(index_in_results, url)]
    for idx, (source, r) in enumerate(results):
        url = r.get("href") or r.get("url", "")
        if url and _should_enrich(r) and len(fetch_targets) < 3:
            fetch_targets.append((idx, url))

    page_contents = {}  # index → page_text
    if fetch_targets:
        fetch_tasks = [_fetch_page_content(url) for _, url in fetch_targets]
        fetch_results = await asyncio.gather(*fetch_tasks)
        for (idx, url), (final_url, content) in zip(fetch_targets, fetch_results):
            if content and len(content) > 80:
                # 二次过滤：抓取后检查最终 URL 是否真的高价值
                if _is_high_value_url(final_url) or len(content) > 200:
                    page_contents[idx] = content
                if final_url:
                    seen_urls.add(final_url)
        logger.info(
            f"[web_search] 页面增强: 抓取 {len(fetch_targets)} 页, "
            f"有效 {len(page_contents)} 页"
        )

    # ── 结果去重与排序 ──
    # 按 URL 去重（保留第一个出现的）
    seen_final = set()
    deduped_results = []
    for source, r in results:
        url = (r.get("href") or r.get("url", "")).rstrip('/')
        if url and url not in seen_final:
            seen_final.add(url)
            deduped_results.append((source, r))
    results = deduped_results

    # 按相关性排序：标题包含查询关键词的排前面
    query_lower = query.lower()
    def _relevance_score(item: tuple) -> int:
        source, r = item
        score = 0
        title = (r.get("title") or "").lower()
        body = (r.get("body") or r.get("snippet", "")).lower()
        # 标题命中关键词 +3
        for kw in query_lower.split():
            if kw in title:
                score += 3
        # 摘要命中关键词 +1
        for kw in query_lower.split():
            if kw in body:
                score += 1
        # 百科类来源 +2
        if any(kw in title for kw in ["百科", "wiki", "维基"]):
            score += 2
        return score
    results.sort(key=_relevance_score, reverse=True)

    # 截取到合理数量
    results = results[:max_results * 2]

    # ── 格式化输出 ──
    lines = [f"## 网络搜索结果（百度 {baidu_total} + 微博 {weibo_total}）\n"]

    # 微博 cookie 过期提示
    if _weibo_cookie_expired and weibo_total == 0:
        lines.append("⚠️ 微博搜索不可用（Cookie 可能已过期，请在 Dashboard 上传新的 cookies.json）\n")

    for i, (source, r) in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("href") or r.get("url", "")
        body = r.get("body") or r.get("snippet", "")
        tag = f" [{source}]" if source != "百度" else ""

        # 如果有页面正文，优先展示正文（更丰富）
        page_text = page_contents.get(i - 1, "")
        if page_text:
            lines.append(
                f"[{i}] {title}{tag}\n"
                f"    链接: {url}\n"
                f"    【页面内容】:\n{page_text}\n"
            )
        else:
            lines.append(f"[{i}] {title}{tag}\n    链接: {url}\n    摘要: {body}\n")

    return "\n".join(lines)


def _execute_corpus_search(skill_name: str, keywords: str) -> str:
    """在指定 skill 目录下搜索 .md 和 corpus_ref/ 下的文本文件，返回匹配的上下文片段。
    搜索范围：
      - 根目录 *.md（排除 backup_* 目录）
      - corpus_ref/*.txt（微博语料等，按关键词命中数打分排序）
      - corpus_ref/weibo_profile_detail.json（自动提取账号简介）
    """
    skill_dir = SKILLS_DIR / skill_name
    if not skill_dir.exists():
        return f"角色 '{skill_name}' 的语料库不存在"

    # 拆分关键词，按长度降序排列（长关键词更具体，权重更高）
    kw_list = [k.strip().lower() for k in keywords.split() if k.strip()]
    if not kw_list:
        return "未提供有效关键词"
    kw_list.sort(key=len, reverse=True)

    # 计算关键词 IDF 权重：越短的关键词越常见，权重越低
    kw_weight: dict[str, float] = {}
    for kw in kw_list:
        if len(kw) <= 2:
            kw_weight[kw] = 0.5
        elif len(kw) <= 4:
            kw_weight[kw] = 1.0
        else:
            kw_weight[kw] = 2.0

    _MAX_SNIPPETS = 12
    results: list[str] = []

    def _match_line(line_lower: str) -> bool:
        return any(kw in line_lower for kw in kw_list)

    def _score_line(line_lower: str) -> float:
        """计算一行文本的关键词匹配得分。命中越多/越具体的关键词，得分越高。"""
        score = 0.0
        for kw in kw_list:
            if kw in line_lower:
                score += kw_weight.get(kw, 1.0)
        return score

    # ── 1) 根目录 *.md 文件（跳过 backup_* 目录） ──
    for md_file in sorted(skill_dir.glob("*.md")):
        if len(results) >= _MAX_SNIPPETS:
            break
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if _match_line(line.lower()):
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                snippet = "\n".join(lines[start:end]).strip()
                results.append(f"[{md_file.name} 第{i+1}行]\n{snippet}")
                if len(results) >= _MAX_SNIPPETS:
                    break

    # ── 2) corpus_ref/*.txt 文件（微博语料等）──
    # 使用 TF-IDF 风格打分：语料中出现越少的关键词权重越高
    # 对上下文窗口整体打分，排序后取 top-N，相邻行去重
    corpus_ref_dir = skill_dir / "corpus_ref"
    txt_hits: list[tuple[float, int, str]] = []   # (score, line_idx, formatted_snippet)
    if corpus_ref_dir.is_dir():
        for txt_file in sorted(corpus_ref_dir.glob("*.txt")):
            try:
                content = txt_file.read_text(encoding="utf-8")
            except Exception:
                continue
            lines = content.split("\n")
            total_lines = max(len(lines), 1)

            # 统计每个关键词在 txt 中的文档频率（多少行包含该关键词）
            _kw_df: dict[str, int] = {}
            for kw in kw_list:
                df = sum(1 for ln in lines if kw in ln.lower())
                _kw_df[kw] = df

            # IDF 权重：df 越小（越稀有）权重越高
            _kw_idf: dict[str, float] = {}
            for kw, df in _kw_df.items():
                if df > 0:
                    _kw_idf[kw] = math.log(total_lines / df)
                else:
                    _kw_idf[kw] = 0.0  # 语料中不存在，不计分

            def _score_line_idf(context_lower: str) -> float:
                """基于 IDF 的上下文打分"""
                return sum(_kw_idf.get(kw, 0.0) for kw in kw_list if kw in context_lower)

            for i, line in enumerate(lines):
                line_lower = line.lower()
                if _match_line(line_lower):
                    start = max(0, i - 4)
                    end = min(len(lines), i + 5)
                    context_lower = "\n".join(lines[start:end]).lower()
                    score = _score_line_idf(context_lower)
                    snippet = "\n".join(lines[start:end]).strip()
                    txt_hits.append((score, i, f"[{txt_file.name} 第{i+1}行]\n{snippet}"))

    # 按得分降序排序，取前 _MAX_TXT_SNIPPETS 条，相邻行去重
    _MAX_TXT_SNIPPETS = 6
    txt_hits.sort(key=lambda x: x[0], reverse=True)
    _seen_line_idxs: list[int] = []
    _txt_added = 0
    for score, line_idx, snippet in txt_hits:
        if _txt_added >= _MAX_TXT_SNIPPETS or len(results) >= _MAX_SNIPPETS:
            break
        # 去重：跳过与已选结果行号距离 < 6 的条目
        if any(abs(line_idx - s) < 6 for s in _seen_line_idxs):
            continue
        _seen_line_idxs.append(line_idx)
        results.append(snippet)
        _txt_added += 1

    # ── 3) 微博账号简介（自动附加，不需要关键词命中） ──
    profile_file = corpus_ref_dir / "weibo_profile_detail.json" if corpus_ref_dir.is_dir() else None
    if profile_file and profile_file.exists():
        try:
            pdata = json.loads(profile_file.read_text(encoding="utf-8"))
            user_info = pdata.get("data", {}).get("user", {})
            profile_parts = []
            if user_info.get("screen_name"):
                profile_parts.append(f"微博名: {user_info['screen_name']}")
            if user_info.get("verified_reason"):
                profile_parts.append(f"认证: {user_info['verified_reason']}")
            if user_info.get("description"):
                profile_parts.append(f"简介: {user_info['description']}")
            if user_info.get("location"):
                profile_parts.append(f"地区: {user_info['location']}")
            if user_info.get("gender"):
                g = {"m": "男", "f": "女"}.get(user_info["gender"], user_info["gender"])
                profile_parts.append(f"性别: {g}")
            if profile_parts:
                profile_text = "\n".join(profile_parts)
                results.insert(0, f"[微博档案]\n{profile_text}")
        except Exception:
            pass

    if not results:
        return f"在角色 '{skill_name}' 的语料库中未找到与「{keywords}」相关的内容"

    return f"## 语料库搜索结果（{skill_name}）\n\n" + "\n\n---\n\n".join(results)


def _get_http_client() -> httpx.AsyncClient:
    """获取或创建持久化 HTTP 客户端"""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=15),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


async def _api_request(payload: dict, provider: dict = None) -> Optional[dict]:
    """底层 API 请求，带重试和 provider 回退。返回 JSON dict 或 None"""
    if provider is None:
        provider = _get_active_provider()

    result = await _api_request_inner(payload, provider)
    if result is not None:
        _mark_provider_healthy(provider["name"])
        return result

    # 主 provider 失败，尝试回退
    fallback_name = "deepseek" if provider["name"] == "openai" else None
    if fallback_name:
        _mark_provider_failed(provider["name"])
        fallback = _get_provider(fallback_name)
        chat_logger.info(f"[LLM] {provider['name']} 失败，回退到 {fallback_name}")
        result = await _api_request_inner(payload, fallback)
        if result is not None:
            _mark_provider_healthy(fallback["name"])
    return result


async def _api_request_inner(payload: dict, provider: dict) -> Optional[dict]:
    """对指定 provider 发送 API 请求，带重试。返回 JSON dict 或 None"""
    url = f"{provider['base_url']}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }
    adapted = _adapt_payload_for_provider(payload, provider["name"])
    client = _get_http_client()
    last_error = None
    pname = provider["name"]

    for attempt in range(1, _API_MAX_RETRIES + 1):
        try:
            resp = await client.post(url, headers=headers, json=adapted)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            body = e.response.text[:200]
            last_error = f"HTTP {status}: {body}"
            if status in (429, 500, 502, 503, 529) and attempt < _API_MAX_RETRIES:
                delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"[{pname}] API {status} (attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s: {body}")
                await asyncio.sleep(delay)
                continue
            logger.error(f"[{pname}] API HTTP error (attempt {attempt}/{_API_MAX_RETRIES}): {last_error}")
        except httpx.TimeoutException as e:
            last_error = f"Timeout: {e}"
            if attempt < _API_MAX_RETRIES:
                delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"[{pname}] API timeout (attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s")
                await asyncio.sleep(delay)
                continue
            logger.error(f"[{pname}] API timeout (attempt {attempt}/{_API_MAX_RETRIES}): {e}")
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.error(f"[{pname}] API unexpected error (attempt {attempt}/{_API_MAX_RETRIES}): {last_error}")
            if attempt < _API_MAX_RETRIES:
                await asyncio.sleep(_API_RETRY_BASE_DELAY)
                continue

    logger.error(f"[{pname}] API failed after {_API_MAX_RETRIES} attempts: {last_error}")
    return None


async def _api_request_stream_inner(payload: dict, provider: dict):
    """对指定 provider 的流式 API 请求。成功 yield True，全部重试耗尽 yield False。"""
    url = f"{provider['base_url']}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }
    adapted = _adapt_payload_for_provider(payload, provider["name"])
    adapted = {**adapted, "stream": True}
    client = _get_http_client()
    pname = provider["name"]

    for attempt in range(1, _API_MAX_RETRIES + 1):
        try:
            async with client.stream("POST", url, headers=headers, json=adapted) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    body_text = body.decode("utf-8", errors="replace")[:200]
                    if resp.status_code in (429, 500, 502, 503, 529) and attempt < _API_MAX_RETRIES:
                        delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                        logger.warning(
                            f"[{pname}] stream API {resp.status_code} "
                            f"(attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.error(f"[{pname}] stream API HTTP {resp.status_code}: {body_text}")
                    yield False
                    return

                buffer = ""
                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            yield True
                            return
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
            yield True  # stream ended normally
            return

        except httpx.TimeoutException as e:
            if attempt < _API_MAX_RETRIES:
                delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"[{pname}] stream timeout "
                    f"(attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s"
                )
                await asyncio.sleep(delay)
                continue
            logger.error(f"[{pname}] stream timeout after {_API_MAX_RETRIES} attempts: {e}")
            yield False
            return
        except Exception as e:
            if attempt < _API_MAX_RETRIES:
                await asyncio.sleep(_API_RETRY_BASE_DELAY)
                continue
            logger.error(f"[{pname}] stream unexpected error: {e}")
            yield False
            return


async def _api_request_stream(payload: dict):
    """流式 API 请求，带 provider 回退。优先使用活跃 provider，失败后回退 DeepSeek。"""
    provider = _get_active_provider()
    sent_any = False
    result = None

    async for chunk in _api_request_stream_inner(payload, provider):
        if chunk is True:
            _mark_provider_healthy(provider["name"])
            return
        elif chunk is False:
            result = False
            break
        else:
            sent_any = True
            yield chunk

    # 如果已经发送了内容给调用方，不再尝试回退
    if sent_any:
        return

    # 主 provider 失败且未产出任何内容，尝试回退
    fallback_name = "deepseek" if provider["name"] == "openai" else None
    if fallback_name and result is False:
        _mark_provider_failed(provider["name"])
        fallback = _get_provider(fallback_name)
        chat_logger.info(f"[LLM] stream: {provider['name']} 失败，回退到 {fallback_name}")
        async for chunk in _api_request_stream_inner(payload, fallback):
            if chunk is True:
                _mark_provider_healthy(fallback["name"])
                return
            elif chunk is False:
                return
            else:
                yield chunk


# ── DeepSeek DSML 标记清洗 ──
# DeepSeek 有时会在 content 中嵌入工具调用的 XML 标记（｜｜DSML｜｜tool_calls...），
# 需要在使用 tools 时清洗掉这些原始标记，避免泄露到最终回复。
_DSML_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*tool_calls\s*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*tool_calls\s*>',
    re.DOTALL
)
_DSML_INVOKE_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*invoke[^>]*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*invoke\s*>',
    re.DOTALL
)
_DSML_PARAM_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*parameter[^>]*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*parameter\s*>',
    re.DOTALL
)
# 通用兜底：匹配所有包含 DSML 的尖括号标签
_DSML_ANY_TAG = re.compile(r'<[^>]*DSML[^>]*/?>',  re.IGNORECASE)
# 截断兜底：匹配从 DSML 开标签到字符串末尾（处理模型输出被截断的情况）
_DSML_TRUNCATED = re.compile(
    r'<[\uff5c|]*\s*DSML\s*[\uff5c|].*',
    re.DOTALL
)
# ── 原生 tool_calls XML 清洗 ──
# 流式路径不带 tools 定义，但模型有时会模仿历史中的 function calling 格式，
# 输出原生 <tool_calls>...</tool_calls> XML 片段，必须清除以避免泄露给用户。
_TOOL_CALLS_FULL = re.compile(
    r'<tool_calls>.*?</tool_calls>',
    re.DOTALL
)
# 截断的 tool_call 开头：<tool_call... （模型输出被截断）
_TOOL_CALL_TRUNCATED = re.compile(
    r'<tool_call[^>]*>.*',
    re.DOTALL
)
# 最终兜底：从 <tool_calls> 开标签到字符串末尾（处理工具调用中被截断的情况）
_TOOL_CALLS_OPEN_TRUNC = re.compile(
    r'<tool_calls>.*',
    re.DOTALL
)
# ── 通用工具调用标签清洗 ──
# 模型在流式路径（无 tools 定义）中可能模仿 function calling 格式，
# 输出 <web_search query="...">、</web_search>、<search_corpus ... /> 等标签。
_TOOL_LIKE_TAG = re.compile(
    r'</?\s*(?:web_search|search_corpus|search)\b[^>]*/?>',
    re.IGNORECASE
)


def _strip_dsml_markup(text: str) -> str:
    """清洗 DeepSeek 可能嵌入 content 的工具调用标记（DSML 和原生 XML）"""
    # DSML 格式
    text = _DSML_PATTERN.sub('', text)
    text = _DSML_INVOKE_PATTERN.sub('', text)
    text = _DSML_PARAM_PATTERN.sub('', text)
    text = _DSML_ANY_TAG.sub('', text)
    # 原生 <tool_calls> XML 格式
    text = _TOOL_CALLS_FULL.sub('', text)
    text = _TOOL_CALL_TRUNCATED.sub('', text)
    # 通用工具调用标签（GPT-5.5 等模型模仿 function calling）
    text = _TOOL_LIKE_TAG.sub('', text)
    # 截断兜底：如果还有残留的 DSML / tool_calls 开标签，删除从那里到末尾
    text = _DSML_TRUNCATED.sub('', text)
    text = _TOOL_CALLS_OPEN_TRUNC.sub('', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _clean_llm_reply(raw_content: str) -> str:
    """清洗模型回复，处理截断的 DSML 标记等异常情况。

    模型在工具调用轮次后可能仍尝试生成 DSML 标记，但因 max_tokens
    截断而只残留不完整的尖括号内容（如 '<'）。此函数负责清理这些
    残留并保证返回有意义的文本。
    """
    cleaned = _strip_dsml_markup(raw_content)
    # 去除尾部残留的不完整 XML-like 标签（如 '<'、'</'、'<tag' 无闭合 '>'）
    cleaned = re.sub(r'<[^>]*$', '', cleaned).strip()
    # 如果清洗后内容过短或无实质内容，返回兜底消息
    if len(cleaned) <= 1 or not re.search(r'[\w\u4e00-\u9fff]', cleaned):
        chat_logger.warning(f"[LLM] 模型回复清洗后为空或无意义 (原始: {repr(raw_content[:200])})")
        return "呜呜对不起！刚刚脑袋打了个盹儿 [捂脸] 泥再说一遍问题好不好？🥺"
    return cleaned


async def _probe_tool_usage(system_prompt: str, messages: list[dict], skill_name: str = "") -> bool:
    """快速探测模型是否需要调用工具。
    返回 True 表示模型想调用工具，False 表示直接回复。
    """
    use_tools = WEB_SEARCH_ENABLED and _SEARCH_AVAILABLE
    if not use_tools and not skill_name:
        return False  # 没有可用工具

    active_tools = []
    if use_tools:
        active_tools.append(_SEARCH_TOOLS[0])   # web_search
    if skill_name:
        active_tools.append(_SEARCH_TOOLS[1])   # search_corpus

    # 注入日期（与正式调用一致）
    now = datetime.now()
    date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
    probe_system = system_prompt + date_hint

    payload = {
        "model": DEEPSEEK_SEARCH_MODEL,
        "reasoning_effort": "low",
        "temperature": 0.3,           # 低温度，更稳定的决策
        "top_p": 0.9,
        "max_tokens": 10,             # 增加到 10，让模型有足够空间表达意图
        "frequency_penalty": 0.3,
        "presence_penalty": 0.2,
        "stream": False,
        "messages": [{"role": "system", "content": probe_system}] + messages,
        "tools": active_tools,
        "tool_choice": "auto",
    }

    try:
        await asyncio.wait_for(_API_SEMAPHORE.acquire(), timeout=10)
    except asyncio.TimeoutError:
        chat_logger.warning("[PROBE] API 并发已满，跳过探测")
        return True  # 假设需要工具，走安全路径

    try:
        # 探测始终使用 DeepSeek（function calling 兼容性最好，且 flash 模型速度快）
        data = await _api_request(payload, _get_provider("deepseek"))
    finally:
        _API_SEMAPHORE.release()

    if data is None:
        return True  # API 失败，走安全的非流式路径

    tool_calls = data["choices"][0]["message"].get("tool_calls")
    wants_tools = bool(tool_calls)
    chat_logger.info(f"[PROBE] wants_tools={wants_tools}")
    return wants_tools


async def call_deepseek(system_prompt: str, messages: list[dict], skill_name: str = "") -> Optional[str]:
    """调用 DeepSeek API，支持 Function Calling 联网搜索 + 语料库搜索"""
    # 全局并发控制：最多 3 个请求同时进行，超出的排队等待（最多等 30 秒）
    try:
        await asyncio.wait_for(_API_SEMAPHORE.acquire(), timeout=30)
    except asyncio.TimeoutError:
        chat_logger.warning("[LLM] API 并发已满，排队超时")
        return "等一下下哦！窝的小脑袋瓜正在疯狂运转中 [捂脸] 再过几秒来戳窝叭～"

    try:
        return await asyncio.wait_for(
            _call_deepseek_inner(system_prompt, messages, skill_name),
            timeout=120,  # 整体超时 2 分钟，防止搜索循环无限卡住
        )
    except asyncio.TimeoutError:
        chat_logger.warning("[LLM] 整体调用超时 (180s)，强制返回")
        return "网络君跑不动惹！好慢好慢 [流汗] 过一会儿再戳窝叭拜托拜托 🙏💦"
    finally:
        _API_SEMAPHORE.release()


async def _call_deepseek_inner(system_prompt: str, messages: list[dict], skill_name: str = "") -> Optional[str]:
    """实际的 DeepSeek API 调用逻辑（由 call_deepseek 的 Semaphore 保护）"""
    use_tools = WEB_SEARCH_ENABLED and _SEARCH_AVAILABLE
    # 语料库搜索始终可用，联网搜索取决于配置
    tools_available = use_tools or bool(skill_name)

    # 选择 provider 和模型
    if tools_available:
        # 工具调用场景强制使用 DeepSeek（function calling 兼容性最好）
        provider = _get_provider("deepseek")
        active_model = DEEPSEEK_SEARCH_MODEL
    else:
        provider = _get_active_provider()
        active_model = provider["model"]

    reasoning_effort = "low" if tools_available else "high"
    chat_logger.info(f"[LLM] 调用 {provider['name']} | model={active_model} | reasoning={reasoning_effort} | 联网={'开' if use_tools else '关'} | 语料库={'开' if skill_name else '关'} | msgs={len(messages)}")

    # 联网搜索时注入当前日期，避免模型猜错时间
    if use_tools:
        now = datetime.now()
        date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
        system_prompt = system_prompt + date_hint

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    base_payload = {
        "model": active_model,
        "reasoning_effort": reasoning_effort,
        "temperature": 0.95,
        "top_p": 0.9,
        "max_tokens": 2048,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.2,
        "stream": False,
    }

    if not tools_available:
        # 不带 tools 的简单调用
        payload = {**base_payload, "messages": full_messages}
        data = await _api_request(payload, provider)
        if data is None:
            return None
        return _clean_llm_reply(data["choices"][0]["message"]["content"].strip())

    # 根据可用能力动态构建工具列表
    active_tools = []
    if use_tools:
        active_tools.append(_SEARCH_TOOLS[0])   # web_search
    if skill_name:
        active_tools.append(_SEARCH_TOOLS[1])   # search_corpus

    # ── Function Calling 循环 ──
    for round_num in range(_MAX_TOOL_ROUNDS + 1):
        payload = {
            **base_payload,
            "messages": full_messages,
            "tools": active_tools,
            "tool_choice": "auto",
        }

        data = await _api_request(payload, provider)
        if data is None:
            return None

        choice = data["choices"][0]
        message = choice["message"]

        # 检查是否有工具调用
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            # 无工具调用 → 直接返回（同时清洗可能残留的 DSML 标记）
            raw_content = message.get("content", "").strip()
            chat_logger.info(f"[LLM] round={round_num} | 模型直接回复（未调用工具）")
            return _clean_llm_reply(raw_content)

        # 把模型的 tool_calls 消息追加到历史
        chat_logger.info(f"[LLM] round={round_num} | 模型调用工具: {[tc['function']['name'] for tc in tool_calls]}")
        full_messages.append(message)

        # 处理每个工具调用
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            func_args = json.loads(tc["function"]["arguments"])

            if func_name == "web_search":
                query = func_args.get("query", "")
                max_results = func_args.get("max_results", 8)
                logger.info(f"[search] 联网搜索: {query} (max={max_results})")
                chat_logger.info(f"[SEARCH] 联网搜索触发: query='{query}', max_results={max_results}")
                search_text = await _execute_web_search(query, max_results)

            elif func_name == "search_corpus":
                kw = func_args.get("keywords", "")
                chat_logger.info(f"[CORPUS] 语料库搜索: skill='{skill_name}', keywords='{kw}'")
                search_text = _execute_corpus_search(skill_name, kw)

            else:
                search_text = f"未知工具: {func_name}"

            full_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": search_text,
            })

    # 超过最大轮次，强制让模型回复（不带 tools）
    # 追加一条提示，让模型基于已有搜索结果生成回复，而不是继续尝试搜索
    _has_search_results = any(
        m["role"] == "tool" and "未找到" not in m.get("content", "")
        for m in full_messages
    )
    if _has_search_results:
        full_messages.append({
            "role": "user",
            "content": "（系统提示：请根据上面搜索到的信息直接回答用户的问题，不要再调用搜索工具。如果搜索结果不够完整，也请基于已有信息给出尽可能的回答。）",
        })

    payload_final = {**base_payload, "messages": full_messages}
    data = await _api_request(payload_final, provider)
    if data is None:
        return None
    raw_content = data["choices"][0]["message"]["content"].strip()
    reply = _clean_llm_reply(raw_content)

    # 如果回复仍为空（DSML 截断），用搜索结果做最后一次尝试
    if "脑子短路" in reply and _has_search_results:
        chat_logger.info("[LLM] 回复被 DSML 截断，追加总结重试")
        full_messages.append({
            "role": "user",
            "content": "（系统：请立刻用自然语言总结上面搜索到的内容回复用户，不要使用任何工具标记。）",
        })
        payload_retry = {**base_payload, "messages": full_messages}
        data2 = await _api_request(payload_retry, provider)
        if data2:
            raw2 = data2["choices"][0]["message"]["content"].strip()
            reply2 = _clean_llm_reply(raw2)
            if "脑子短路" not in reply2:
                return reply2

    return reply


# ============================================================
