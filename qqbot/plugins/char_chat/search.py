"""
联网搜索（百度）+ 微博增强（单帖正文 / 热搜，均免 cookie）+ 页面抓取 + 语料库搜索

2026-09-10 精简：删掉四个接不通的源（Bing 投毒 / 搜狗反爬 / DDG 不可达 / 微博关键词搜索需登录态）。
它们只会拖慢响应，而模型侧本来也选不了源 —— 模型只有"要不要联网"这一个开关。
"""

import asyncio
import html as html_lib
import json
import math
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urlencode

import httpx
from nonebot import logger

from . import config as cfg


# ── 共享 HTTP 客户端（避免每次搜索都新建连接） ──
_search_client: Optional[httpx.AsyncClient] = None


def _get_search_client() -> httpx.AsyncClient:
    """获取或创建搜索专用的持久化 HTTP 客户端"""
    global _search_client
    if _search_client is None or _search_client.is_closed:
        _search_client = httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _search_client


# ── Function Calling 工具定义 ──
SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "【联网搜索 - 兜底工具】在网络上搜索实时信息。系统会用百度搜索并抓取正文，涉及微博时直接取单帖内容，"
                "并自动抓取百科类页面详细内容。"
                "使用场景（仅在以下情况使用本工具）："
                "1. 用户询问的话题明显超出角色知识范围（最新新闻、未来事件、特定圈外人物）；"
                "2. search_corpus 已调用但未找到结果；"
                "3. 用户明确要求查实时信息（'最新'、'今天'、'最近'等）。"
                "对于角色自身、所属圈子、过往经历等话题，**必须先调 search_corpus**，"
                "不要直接用联网搜索代替本地语料。"
                "搜索结果包含可点击链接，可以提供给用户参考。"
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
                "【本地语料库搜索 - 优先工具】搜索角色的语料库、知识库、共享资料库"
                "（包括角色人设、背景故事、工作经历、所属圈子的微博/推特原文、圈子通用知识等所有本地资料）。"
                "【使用规则】"
                "1. 涉及角色自身、所属圈子、过往经历、工作内容的话题，**必须**先调本工具；"
                "2. 用户问'我是谁''你喜欢什么''你的工作是什么'之类问题，必须先调本工具以保持人设一致；"
                "3. 用户提起的圈外话题（如某条微博、某个演出）也可先调本工具看是否有相关记录；"
                "4. 本工具无结果或不确定时，再决定是否调 web_search。"
                "关键词支持中文+拼音首字母 fuzzy 匹配（如搜「宋雨琦」也能命中含「syq」拼音首字母的段落）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "string",
                        "description": "搜索关键词，可以是空格分隔的多个关键词；中文尽量用核心 2~6 字",
                    },
                },
                "required": ["keywords"],
            },
        },
    },
]
_MAX_TOOL_ROUNDS = 3  # 增加到 3 轮，支持复杂查询的多步搜索


# ── 页面正文抓取 ──
_HIGH_VALUE_DOMAINS = [
    "fandom.com", "baike.baidu.com", "wiki", "zh.wikipedia.org",
    # 2026-09-10: 微博单帖走访客身份 API 能拿到正文（免人工 cookie），值得当作正文源
    "weibo.com",
]
_PAGE_FETCH_TIMEOUT = 8
_PAGE_CONTENT_MAX_CHARS = 1500


# ── 结果摘要清洗（2026-09-10 新增）──
# 背景：百度/搜狗的摘要经常夹带 SSR 的 JSON/JS 碎片，例如
#   …"}],"clamp":3},"isSingleLine":false,…,"$style":{"rate":"rate_r0rep"…
# 这类噪音既浪费上下文、又会误导模型（曾出现"搜出来全是景区门票/停车费"）。
_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>[\s\S]*?</\1>", re.I)
_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_JSON_TAIL_RE = re.compile(r'\{"|"\}|"\],|\}\],|,\s*"[A-Za-z_$][\w$]*"\s*:')
_WS_RE = re.compile(r"\s+")


def _clean_snippet(text: str, max_chars: int = 220) -> str:
    """把 HTML／带 SSR JSON 的摘要清洗成可读纯文本"""
    if not text:
        return ""
    t = html_lib.unescape(text)
    t = _SCRIPT_STYLE_RE.sub(" ", t)
    t = _COMMENT_RE.sub(" ", t)
    t = _ANY_TAG_RE.sub("", t)
    t = _JSON_TAIL_RE.split(t)[0]
    t = re.sub(r"[\x00-\x1f]+", " ", t)
    t = _WS_RE.sub(" ", t).strip(" :;,.、，。·-")
    if len(t) > max_chars:
        t = t[:max_chars].rstrip(" ,，。.") + "…"
    return t


# ── 排序权重（2026-09-10 新增：域名权威度 + 日期新鲜度参与重排）──
_AUTHORITY_DOMAINS = {
    "baike.baidu.com": 6, "zh.wikipedia.org": 6, "wikipedia.org": 5,
    "weibo.com": 5, "bilibili.com": 4, "zhihu.com": 3, "douban.com": 3,
    "github.com": 3, "thepaper.cn": 3, "36kr.com": 2, "ifeng.com": 2,
    "sina.com.cn": 2, "163.com": 2, "qq.com": 2, "sohu.com": 1,
}

_DATE_RE = re.compile(r"(20\d\d)[-/年.](\d{1,2})(?:[-/月.](\d{1,2}))?")


def _domain_of(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url or "")
    host = (m.group(1) if m else "").lower()
    # 注意：不能用 lstrip("www.") —— 它按字符集剥离，会把 weibo.com 变成 eibo.com
    return re.sub(r"^www\.", "", host)


def _extract_date_ts(text: str) -> float:
    """从标题/摘要/URL 里抠出日期，返回时间戳（找不到返回 0）"""
    m = _DATE_RE.search(text or "")
    if not m:
        return 0.0
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return 0.0
    try:
        return time.mktime((y, mo, d, 0, 0, 0, 0, 0, -1))
    except Exception:
        return 0.0


def _query_keywords(query: str) -> list[str]:
    """把查询拆成用于相关性判断的关键词

    中文没有空格：整句当一个关键词会导致相关性判定全灭
    （实测"最近有什么热点"把百度 6 条全部误杀）。所以长中文词再补 2 字滑窗二元组 ——
    既能匹配上，又能继续拦住 Bing 那种整批跑题的诱饵结果。
    """
    q = (query or "").lower()
    kws = [k for k in re.split(r"[\s，,。、/|!?！？]+", q) if len(k) >= 2]
    for k in list(kws):
        if len(k) >= 5 and re.search(r"[\u4e00-\u9fff]", k):
            for i in range(len(k) - 1):
                bg = k[i:i + 2]
                if bg not in kws:
                    kws.append(bg)
    return kws


def _item_relevant(kws: list[str], r: dict) -> bool:
    """标题/摘要/URL 里至少命中一个查询关键词才算相关"""
    if not kws:
        return True
    blob = (
        f"{r.get('title', '')} {r.get('body') or r.get('snippet', '')} "
        f"{r.get('href') or r.get('url', '')}"
    ).lower()
    return any(k in blob for k in kws)


def _freshness_bonus(ts: float) -> int:
    if ts <= 0:
        return 0
    age_days = (time.time() - ts) / 86400
    if age_days < 0:
        return 3
    if age_days <= 30:
        return 5
    if age_days <= 180:
        return 3
    if age_days <= 365:
        return 1
    if age_days > 730:
        return -3
    return 0


async def resolve_baidu_redirect(url: str, timeout_s: int = 5) -> str:
    """解析百度重定向URL，返回实际目标URL。如果不是百度重定向则返回原URL。"""
    if not url or "baidu.com/link" not in url:
        return url
    try:
        client = _get_search_client()
        resp = await client.head(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if location and "baidu.com" not in location:
                return location
        # 如果HEAD请求失败，尝试GET
        client = _get_search_client()
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, follow_redirects=False, allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if location and "baidu.com" not in location:
                return location
    except Exception as e:
        logger.debug(f"[baidu_redirect] 解析失败 {url[:60]}: {e}")
    return url


# ── 微博：免登录访客身份（2026-09-10 新增）──
# 实测结论（2026-09-10）：
#   ✅ 单帖正文 API（ajax/statuses/show）用访客身份可读，ok=1，含正文/作者/时间/互动数
#   ❌ 关键词搜索（m.weibo.cn 容器、s.weibo.com）强制要求登录态，访客身份 ok=-100
# 所以"微博搜索不需要 cookie"做不到，但"拿到某条微博的正文"可以 ——
# 配合百度索引（能搜到 weibo.com 链接）就完全不用人工导出 cookie。
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_WEIBO_POST_URL_RE = re.compile(r"weibo\.com/(?:u/)?\d*?/?([A-Za-z0-9]{6,})")
_VISITOR_COOKIES: str = ""
_VISITOR_TS: float = 0.0
_VISITOR_TTL = 12 * 3600
_VISITOR_LOCK = asyncio.Lock()


async def _get_visitor_cookie() -> str:
    """免登录换取微博访客身份（genvisitor → incarnate），12 小时缓存

    用独立的 httpx 客户端跑，避免把微博 cookie 污染进共享搜索客户端。
    """
    global _VISITOR_COOKIES, _VISITOR_TS
    if _VISITOR_COOKIES and time.time() - _VISITOR_TS < _VISITOR_TTL:
        return _VISITOR_COOKIES
    async with _VISITOR_LOCK:
        if _VISITOR_COOKIES and time.time() - _VISITOR_TS < _VISITOR_TTL:
            return _VISITOR_COOKIES
        try:
            fp = ('{"os":"1","browser":"Chrome125,0,0,0","fonts":"undefined",'
                  '"screenInfo":"1920*1080*24","plugins":""}')
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as cl:
                r1 = await cl.post(
                    "https://passport.weibo.com/visitor/genvisitor",
                    data={"cb": "gen_callback", "fp": fp},
                    headers={"User-Agent": _BROWSER_UA,
                             "Referer": "https://passport.weibo.com/visitor/visitor"},
                )
                m = re.search(r'"tid":"([^"]+)"', r1.text)
                if not m:
                    logger.warning("[weibo] 访客身份: genvisitor 未返回 tid")
                    return ""
                await cl.get(
                    "https://passport.weibo.com/visitor/visitor",
                    params={"a": "incarnate", "t": m.group(1), "w": "2", "c": "095",
                            "gc": "", "cb": "cross_domain", "from": "weibo",
                            "_rand": f"{time.time():.4f}"},
                    headers={"User-Agent": _BROWSER_UA},
                )
                jar = {c.name: c.value for c in cl.cookies.jar
                       if c.name in ("SUB", "SUBP", "SRF", "SRT", "SVB")}
            if "SUB" not in jar:
                logger.warning("[weibo] 访客身份: 未拿到 SUB cookie")
                return ""
            _VISITOR_COOKIES = "; ".join(f"{k}={v}" for k, v in jar.items())
            _VISITOR_TS = time.time()
            logger.info(f"[weibo] 已获取访客身份（{len(jar)} 个 cookie，{_VISITOR_TTL // 3600}h 有效）")
            return _VISITOR_COOKIES
        except Exception as e:
            logger.warning(f"[weibo] 获取访客身份失败: {type(e).__name__}: {e}")
            return ""


def _weibo_mid_from_url(url: str) -> str:
    """从微博链接里抠出 mid（末段 base62 或数字 id）"""
    if not url or "weibo.com" not in url:
        return ""
    m = re.search(r"weibo\.com/(?:u/)?\d*/([A-Za-z0-9]{6,})", url)
    if m:
        return m.group(1)
    m2 = re.search(r"weibo\.com/(?:status|detail)/([A-Za-z0-9]{6,})", url)
    return m2.group(1) if m2 else ""


async def fetch_weibo_post(mid: str) -> tuple[str, str]:
    """用访客身份读单帖正文，返回 (final_url, text)"""
    if not mid:
        return ("", "")
    cookie = await _get_visitor_cookie()
    if not cookie:
        return ("", "")
    try:
        client = _get_search_client()
        resp = await client.get(
            "https://weibo.com/ajax/statuses/show",
            params={"id": mid},
            headers={"User-Agent": _BROWSER_UA, "Referer": "https://weibo.com/",
                     "Accept": "application/json", "Cookie": cookie},
        )
        j = resp.json()
        if not isinstance(j, dict) or j.get("ok") != 1:
            return ("", "")
        user = (j.get("user") or {}).get("screen_name", "")
        uid = (j.get("user") or {}).get("id", "")
        text = (j.get("text_raw") or j.get("text") or "").strip()
        created = j.get("created_at", "")
        stats = (f"转{j.get('reposts_count')} 评{j.get('comments_count')} "
                 f"赞{j.get('attitudes_count')}")
        final_url = f"https://weibo.com/{uid}/{mid}" if uid else f"https://weibo.com/{mid}"
        body = f"[微博] @{user} · {created} · {stats}\n{text}"
        logger.info(f"[weibo] 单帖正文已取: {mid} ({len(text)} 字)")
        return (final_url, body[:_PAGE_CONTENT_MAX_CHARS])
    except Exception as e:
        logger.debug(f"[weibo] 单帖正文失败 {mid}: {type(e).__name__}")
        return ("", "")


# ── 微博实时热搜（2026-09-10 新增：零 cookie 可读，实测 52 条）──
_WEIBO_HOT_CACHE: list[dict] = []
_WEIBO_HOT_TS: float = 0.0
_WEIBO_HOT_TTL = 300  # 热搜更新快，但也没必要每次搜索都拉

# 判定"在问最近的热点"的意图词
_TREND_INTENT_KEYWORDS = (
    "热搜", "热门", "热点", "话题榜", "大家都在聊", "最近在聊", "流行什么",
    "最近有什么", "最近发生", "有什么新闻", "最近的事",
)


def _is_trend_query(query: str) -> bool:
    q = (query or "").lower()
    return any(k in q for k in _TREND_INTENT_KEYWORDS)


def _fmt_heat(num) -> str:
    try:
        n = int(num)
    except Exception:
        return ""
    return f"{n / 10000:.1f}万" if n >= 10000 else str(n)


async def fetch_weibo_hot(limit: int = 10) -> list[dict]:
    """拉微博实时热搜榜（免 cookie，5 分钟缓存）。返回 [{word, rank, heat, label}]"""
    global _WEIBO_HOT_CACHE, _WEIBO_HOT_TS
    if _WEIBO_HOT_CACHE and time.time() - _WEIBO_HOT_TS < _WEIBO_HOT_TTL:
        return _WEIBO_HOT_CACHE[:limit]
    try:
        client = _get_search_client()
        resp = await client.get(
            "https://weibo.com/ajax/side/hotSearch",
            headers={"User-Agent": _BROWSER_UA, "Referer": "https://weibo.com/"},
        )
        data = (resp.json() or {}).get("data") or {}
        items = []
        for it in (data.get("realtime") or []):
            w = (it.get("word") or "").strip()
            if not w:
                continue
            items.append({
                "word": w,
                "rank": it.get("rank"),
                "heat": it.get("num"),
                "label": it.get("label_name") or "",
            })
        if items:
            _WEIBO_HOT_CACHE = items
            _WEIBO_HOT_TS = time.time()
            logger.info(f"[weibo] 热搜榜已更新: {len(items)} 条")
        return _WEIBO_HOT_CACHE[:limit]
    except Exception as e:
        logger.debug(f"[weibo] 热搜榜获取失败: {type(e).__name__}: {e}")
        return _WEIBO_HOT_CACHE[:limit]


def _format_hot_block(items: list[dict], title: str, note: str = "") -> str:
    if not items:
        return ""
    out = [f"## {title}"]
    if note:
        out.append(note)
    for i, it in enumerate(items, 1):
        heat = _fmt_heat(it.get("heat"))
        label = f"（{it['label']}）" if it.get("label") else ""
        tail = f" · 热度 {heat}" if heat else ""
        out.append(f"{i}. {it['word']}{label}{tail}")
    return "\n".join(out) + "\n"


def _decode_html(resp) -> str:
    """稳健解码 HTML：国内不少新闻站是 GBK/GB2312，直接用 resp.text 会整段乱码

    2026-09-10 新增：实测 news.youth.cn 抓回来是 "Ʊ����115��…"，乱码正文进了 prompt
    比没有正文更糟。优先按响应声明解码，出现替换字符时再按 gb18030 等重试。
    """
    text = resp.text
    if "\ufffd" not in text[:3000]:
        return text
    for enc in ("gb18030", "gbk", "big5", "utf-8"):
        try:
            cand = resp.content.decode(enc)
        except Exception:
            continue
        if "\ufffd" not in cand[:3000]:
            logger.debug(f"[fetch] 页面按 {enc} 重解码成功: {str(resp.url)[:60]}")
            return cand
    return text


async def fetch_page_content(url: str, timeout_s: int = _PAGE_FETCH_TIMEOUT) -> tuple[str, str]:
    """抓取 URL 页面并提取正文文本。返回 (final_url, content)
    
    对于 fandom.com 域名，优先使用 MediaWiki API 绕过 Cloudflare 反爬。
    """
    if not url or url.startswith("/sf/") or url.startswith("javascript:"):
        return ("", "")

    # 2026-09-10: 微博单帖走访客身份 API（网页是 JS 壳，直接抓取拿不到正文）
    _mid = _weibo_mid_from_url(url)
    if _mid:
        _final, _text = await fetch_weibo_post(_mid)
        if _text:
            return (_final or url, _text)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        client = _get_search_client()
        resp = await client.get(url, headers=headers)

        # ── fandom.com Cloudflare 反爬绕过：使用 MediaWiki API ──
        if "fandom.com" in url and (
            resp.status_code == 403
            or "Just a moment" in resp.text[:500]
        ):
            m = re.search(r'/([a-z0-9-]+)\.fandom\.com/wiki/(.+)', url)
            if m:
                wiki_subdomain = m.group(1)
                page_name = m.group(2).split("#")[0].split("?")[0]
                api_url = (
                    f"https://{wiki_subdomain}.fandom.com/api.php"
                    f"?action=parse&page={quote_plus(page_name)}"
                    f"&prop=text&format=json"
                )
                try:
                    api_client = _get_search_client()
                    api_resp = await api_client.get(
                        api_url,
                        headers={"User-Agent": headers["User-Agent"], "Accept": "application/json"},
                    )
                    if api_resp.status_code == 200:
                        data = api_resp.json()
                        title = data.get("parse", {}).get("title", "")
                        wiki_text = data.get("parse", {}).get("text", {}).get("*", "")
                        # 清洗 HTML → 纯文本
                        clean = re.sub(r'<[^>]+>', ' ', wiki_text)
                        clean = re.sub(r'&[a-z]+;', ' ', clean)
                        clean = re.sub(r'\s+', ' ', clean).strip()
                        if clean:
                            content = f"【{title}】(来自 Fandom Wiki API)\n{clean[:_PAGE_CONTENT_MAX_CHARS]}"
                            return (str(api_resp.url), content)
                except Exception:
                    pass
        # ── /fandom API 回退结束 ──

        if resp.status_code != 200:
            return ("", "")
        final_url = str(resp.url)
        html = _decode_html(resp)

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


def is_high_value_url(url: str) -> bool:
    """判断 URL 是否属于值得抓取全文的高价值域名"""
    if not url:
        return False
    lower = url.lower()
    return any(domain in lower for domain in _HIGH_VALUE_DOMAINS)


# ── 关键词简化 ──
_GENERIC_SUFFIXES = {"官方", "是谁", "介绍", "资料", "简介", "哪里人", "怎么样", "什么"}


def extract_core_keywords(query: str) -> str:
    """从搜索查询中提取核心关键词，用于重试简化。"""
    parts = query.strip().split()
    if len(parts) <= 1:
        return query
    filtered = [p for p in parts if p not in _GENERIC_SUFFIXES]
    if not filtered:
        return parts[0]
    return filtered[0]


# ── 百度搜索 ──
async def search_baidu(query: str, max_results: int = 10, timeout_s: float = 15) -> list:
    """搜索百度网页，解析 HTML 返回结构化结果列表。"""
    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={max_results}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        client = _get_search_client()
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = _decode_html(resp)
    except httpx.TimeoutException:
        logger.warning(f"[baidu] 搜索超时: {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[baidu] 搜索失败: {e}")
        return []

    def _clean(text: str) -> str:
        # 2026-09-10: 改用公共清洗器（去 script/style/注释 + 砍掉 SSR JSON 碎片）
        return _clean_snippet(text, max_chars=400)

    _BAIDU_NOISE_RE = re.compile(r'image\.baidu\.com|/sf/vsearch\?|tn=baiduimage')

    results = []
    blocks = re.split(
        r'<div[^>]*class="[^"]*(?:result\s+c-container|c-container)[^"]*"[^>]*>',
        html,
    )

    for block in blocks[1:]:
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

        link = ""
        href_match = re.search(r'<a[^>]*href="([^"]*)"', h3_match.group(0))
        if href_match:
            link = href_match.group(1)

        if _BAIDU_NOISE_RE.search(link) or _BAIDU_NOISE_RE.search(title):
            continue

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
            for sm in re.finditer(r'>([^<]{40,})<', block):
                raw = sm.group(1)
                if raw.strip().startswith(('{', 'var ', 'window.', 'function')):
                    continue
                # 2026-09-10: 兜底分支此前直接用裸文本 —— SSR JSON 就是从这条路漏进 prompt 的
                t = _clean_snippet(raw, max_chars=400)
                if len(t) > 30 and t != title:
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


# ── 主搜索编排 ──
async def execute_web_search(query: str, max_results: int = 5) -> str:
    """联网搜索：百度（主力）+ 微博增强 + 相关性闸门 + 重排 + 正文抓取。

    2026-09-10 精简：删掉四个接不通的源 ——
      - Bing：对非浏览器客户端返回诱饵结果（HTML/RSS 都试过）
      - 搜狗：反爬验证页，恒为 0 结果
      - DDG：国内不可达，每次白等 6-9 秒
      - 微博关键词搜索：强制登录态，访客身份也不行
    它们只会拖慢响应，而且模型侧本来也选不了源（模型只有"要不要联网"这一个开关）。
    保留并强化的：百度搜索、微博单帖正文（访客身份，免 cookie）、微博热搜（免 cookie）。
    """
    cfg.record_metric("web_search", "calls")

    _TIMEOUT = 15

    async def _baidu_with_timeout(q: str, max_r: int) -> list:
        """百度是当前唯一可用的通用引擎，给它一次重试"""
        for attempt in (1, 2):
            try:
                res = await asyncio.wait_for(
                    search_baidu(q, max_results=max_r, timeout_s=_TIMEOUT),
                    timeout=_TIMEOUT + 3,
                )
                if res or attempt == 2:
                    return res
                logger.warning(f"[web_search] 百度首次 0 结果，重试一次: {q[:40]}")
                await asyncio.sleep(1.0)
            except asyncio.TimeoutError:
                if attempt == 2:
                    logger.warning(f"[web_search] 百度搜索超时 ({_TIMEOUT}s): {q[:60]}")
                    return []
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"[web_search] 百度搜索失败: {e}")
                    return []
        return []

    _kws_all = _query_keywords(query)

    # ── 搜索（唯一引擎）──
    _items = await _baidu_with_timeout(query, max_results + 5)
    baidu_total = len(_items)
    if baidu_total:
        cfg.record_metric("by_source", "baidu", baidu_total)
    cfg.update_search_health("baidu", baidu_total > 0)

    # 引擎级相关性闸门：整批都不含查询关键词 → 判为跑题/投毒，整批丢弃
    if _items and _kws_all and len(_items) >= 3:
        _hit = sum(1 for r in _items if _item_relevant(_kws_all, r))
        if _hit == 0:
            logger.warning(f"[web_search] 百度返回 {len(_items)} 条但全部与查询无关，已丢弃")
            _items = []
            baidu_total = 0

    results: list[tuple[str, dict]] = []
    seen_urls = set()
    for r in _items:
        url = r.get("href") or r.get("url", "")
        if url:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        results.append(("百度", r))

    # 单条级宽松过滤：过滤后仍有结果才生效，避免把搜索整个清空
    if _kws_all and results:
        _filtered = [(s, r) for (s, r) in results if _item_relevant(_kws_all, r)]
        if _filtered:
            _lost = len(results) - len(_filtered)
            if _lost:
                logger.info(f"[web_search] 单条相关性过滤: 丢弃 {_lost} 条无关结果")
            results = _filtered

    if not results:
        # 全网都没搜到 → 给一份微博热搜当背景（明确标注"不一定是答案"）
        _hot = await fetch_weibo_hot(5)
        if _hot:
            return (
                "网络搜索没有找到相关结果。\n\n"
                + _format_hot_block(
                    _hot,
                    "当前微博热搜（仅供参考）",
                    "注：这些是全网热搜，**不一定是用户问的东西**；除非问题本身就是在问最近的热点，"
                    "否则不要拿它当答案，直接说没查到即可。",
                )
            )
        return "未找到相关结果。（可以换个说法再搜一次，或者直接告诉对方没查到）"

    cfg.record_metric("web_search", "hits")

    # ── 排序：关键词命中 + 域名权威度 + 日期新鲜度 ──
    def _score(item: tuple) -> int:
        _source, r = item
        title = (r.get("title") or "").lower()
        body = (r.get("body") or r.get("snippet", "") or "").lower()
        url = r.get("href") or r.get("url") or ""
        s = 0
        for kw in _kws_all:
            if kw in title:
                s += 3
            if kw in body:
                s += 1
        if any(k in title for k in ("百科", "wiki", "维基")):
            s += 2
        s += _AUTHORITY_DOMAINS.get(_domain_of(url), 0)
        s += _freshness_bonus(_extract_date_ts(f"{title} {body} {url}"))
        return s

    results.sort(key=_score, reverse=True)

    # ── 近重复标题折叠 + 每域名最多 2 条 ──
    def _norm_title(t: str) -> str:
        return re.sub(r"[\s\W_]+", "", (t or "").lower())

    _deduped: list[tuple[str, dict]] = []
    _kept_titles: list[str] = []
    _per_domain: dict[str, int] = {}
    for item in results:
        _source, r = item
        nt = _norm_title(r.get("title", ""))
        if len(nt) < 4:
            continue
        if any((nt in kt or kt in nt) for kt in _kept_titles if len(kt) > 6 and len(nt) > 6):
            continue
        dom = _domain_of(r.get("href") or r.get("url") or "")
        if dom and _per_domain.get(dom, 0) >= 2:
            continue
        _kept_titles.append(nt)
        if dom:
            _per_domain[dom] = _per_domain.get(dom, 0) + 1
        _deduped.append(item)
    results = _deduped[: max_results * 2]

    # ── 先解析百度重定向，再抓正文 ──
    # 顺序很重要：不先还原真实 URL，抓取时看到的还是 baidu.com/link?...，
    # 认不出目标站点（微博单帖这类按域名分流的正文源就会漏掉）。
    baidu_redirect_tasks = []
    baidu_redirect_indices = []
    for i, (source, r) in enumerate(results):
        if source == "百度":
            url = r.get("href") or r.get("url", "")
            if url and "baidu.com/link" in url:
                baidu_redirect_indices.append(i)
                baidu_redirect_tasks.append(resolve_baidu_redirect(url))

    if baidu_redirect_tasks:
        resolved_urls = await asyncio.gather(*baidu_redirect_tasks)
        for idx, resolved_url in zip(baidu_redirect_indices, resolved_urls):
            if resolved_url and "baidu.com/link" not in resolved_url:
                source, r = results[idx]
                weibo_user_match = re.match(r'^https?://weibo\.com/(\d+)/?$', resolved_url)
                if weibo_user_match:
                    user_id = weibo_user_match.group(1)
                    resolved_url = f"https://weibo.com/u/{user_id}"
                    logger.info(f"[search] 转换微博用户主页URL: {user_id} -> {resolved_url}")
                r["href"] = resolved_url
                r["url"] = resolved_url
                logger.info(f"[search] 解析百度重定向: 原URL -> {resolved_url[:80]}")

    # ── 页面内容增强：前 2 条无条件 + wiki/官方/微博，最多 3 条 ──
    _ENRICH_TITLE_KEYWORDS = [
        "wiki", "百科", "fandom", "维基百科", "百度百",
        "官方", "官网", "简介", "资料", "profile",
    ]

    def _should_enrich(idx: int, r: dict) -> bool:
        url = (r.get("href") or r.get("url") or "").lower()
        title = (r.get("title") or "").lower()
        if idx < 2:
            return True
        if is_high_value_url(url):
            return True
        return any(kw in title for kw in _ENRICH_TITLE_KEYWORDS)

    fetch_targets = []
    for idx, (source, r) in enumerate(results):
        url = r.get("href") or r.get("url", "")
        if url and _should_enrich(idx, r) and len(fetch_targets) < 3:
            fetch_targets.append((idx, url))

    page_contents = {}
    if fetch_targets:
        fetch_results = await asyncio.gather(*[fetch_page_content(u) for _, u in fetch_targets])
        for (idx, url), (final_url, content) in zip(fetch_targets, fetch_results):
            if content and len(content) > 80:
                if is_high_value_url(final_url) or len(content) > 200:
                    page_contents[idx] = content
                if final_url:
                    seen_urls.add(final_url)
        logger.info(
            f"[web_search] 页面增强: 抓取 {len(fetch_targets)} 页, 有效 {len(page_contents)} 页"
        )

    # ── 格式化输出（给模型看：紧凑、带来源与日期，不含运维告警）──
    lines = [f"## 网络搜索结果（百度 {baidu_total}）\n"]

    # 问题本身在问"最近的热点" → 微博热搜直接放最前面
    if _is_trend_query(query):
        _hot = await fetch_weibo_hot(10)
        if _hot:
            lines.insert(0, _format_hot_block(_hot, "微博实时热搜（前 10）"))

    for i, (source, r) in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("href") or r.get("url", "")
        body = r.get("body") or r.get("snippet", "")

        if url:
            logger.info(f"[search] 结果[{i}] source={source} url={url[:80]}")

        _ts = _extract_date_ts(f"{title} {body} {url}")
        _date_str = time.strftime("%Y-%m-%d", time.localtime(_ts)) if _ts else ""
        _dom = _domain_of(url)
        _meta = " · ".join(x for x in (_dom, _date_str) if x)
        head = f"[{i}] {title}"
        if _meta:
            head += f"\n    来源: {_meta}"

        page_text = page_contents.get(i - 1, "")
        if page_text:
            lines.append(f"{head}\n    链接: {url}\n    【页面正文】:\n{page_text}\n")
        elif body:
            lines.append(f"{head}\n    链接: {url}\n    摘要: {body}\n")
        else:
            lines.append(f"{head}\n    链接: {url}\n")

    lines.append(
        "\n（以上是搜索结果原文。若与问题无关，就直接说没查到，不要凭记忆硬答；"
        "引用时带上来源域名，不要编造链接。）"
    )
    return "\n".join(lines)


def _corpus_dirs_for(name: str) -> list[Path]:
    """找某个名字**自己的**语料目录：corpus/{name}_* + characters/{name}/corpus_ref/"""
    dirs: list[Path] = []
    if cfg.CORPUS_DIR.is_dir():
        # 按目录名字典序倒序（假设新版名字里有更高版本号或更晚日期）
        for d in sorted(cfg.CORPUS_DIR.iterdir(), reverse=True):
            if d.is_dir() and d.name.startswith(name + "_"):
                dirs.append(d)
    fallback = cfg.SKILLS_DIR / name / "corpus_ref"
    if fallback.is_dir():
        dirs.append(fallback)
    return dirs


def _resolve_corpus_dirs(skill_name: str) -> list[Path]:
    """解析所有匹配 skill 名的 corpus 目录，按名称排序（新的在前）。

    例: ytj → [corpus/ytj_v3/, corpus/ytj_v2/, corpus/ytj_7841140689/]
    同时回退到 characters/{name}/corpus_ref/（如果存在）。

    2026-09-10: 若该角色自己完全没有语料，再按 config.CORPUS_ALIASES 借用别名角色的语料
    （当前只有 dot_yuu → ytj）。角色一旦有了自己的语料目录就优先用自己的，别名自动失效。
    """
    dirs = _corpus_dirs_for(skill_name)
    if dirs:
        return dirs
    alias = cfg.CORPUS_ALIASES.get(skill_name)
    if alias:
        borrowed = _corpus_dirs_for(alias)
        if borrowed:
            logger.info(
                f"[corpus] 角色 '{skill_name}' 无自有语料，借用 '{alias}' 的：{borrowed[0].name}"
            )
        return borrowed
    return dirs


def _resolve_corpus_dir(skill_name: str) -> Optional[Path]:
    """向后兼容：返回最新一个 corpus 目录（推荐使用 _resolve_corpus_dirs 多目录合并）"""
    dirs = _resolve_corpus_dirs(skill_name)
    return dirs[0] if dirs else None


def _pinyin_initials(text: str) -> str:
    """提取中文文本的拼音首字母序列，用于 fuzzy 匹配。

    例: '宋雨琦' → 'syq', 'starwink' → 'starwink' (非中文保留)
    """
    try:
        import pypinyin
        out = []
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff':
                py = pypinyin.pinyin(ch, style=pypinyin.NORMAL)
                out.append(py[0][0][0] if py and py[0] else ch)
            else:
                out.append(ch)
        return ''.join(out).lower()
    except ImportError:
        # pypinyin 不可用,降级为原字符
        return text.lower()


def _fuzzy_match(line_lower: str, kw_list: list[str]) -> bool:
    """精确匹配 + 拼音首字母 fuzzy 匹配。

    fuzzy 规则：
    1. 精确包含（kw 直接出现在 line 里）→ True
    2. 拼音首字母 fuzzy：line 抽取首字母序列后，kw 的首字母在其中 → True
       例: kw='momo', line='mm 来演出' → line_initials='mmlcyl', kw_initials='momo'
           若 kw_initials 全是首字母（中文）,在 line_initials 中查找
           若 kw 含非中文字符（英文）,只在 line 里有连续首字母段匹配时算命中
    """
    # 1) 精确包含（要求 kw 长度 >= 2,空串/单字不参与匹配）
    if any(kw and len(kw) >= 2 and kw in line_lower for kw in kw_list):
        return True

    line_initials = _pinyin_initials(line_lower)

    for kw in kw_list:
        if not kw or len(kw) < 2:
            continue
        # 长度上限 12（防误命中）
        if len(kw) > 12:
            continue
        kw_initials = _pinyin_initials(kw)
        if not kw_initials:
            continue

        # 中文关键词：要求 line_initials 包含 kw_initials
        if any('\u4e00' <= c <= '\u9fff' for c in kw):
            if kw_initials in line_initials:
                return True
        # 纯英文/字母关键词：要求 line 里出现连续 kw_initials（精确字母匹配）
        else:
            # 把 line_initials 视为字母序列，要求 kw 整串在里面出现
            if kw_initials in line_initials:
                return True
    return False


def execute_corpus_search(skill_name: str, keywords: str) -> str:
    """在 skill 人设文件、corpus 语料库、normal-paper 共享资料库中搜索关键词相关内容。

    搜索来源（按优先级）：
    1. skills/{name}/*.md — 角色人设/工作能力等结构化参考
    2. corpus/{name}_{uid}/*.txt — 正式语料库（微博帖子原文）
    3. corpus/{name}_{uid}/*.md — 语料库中的附加参考文档
    4. normal-paper/*.md — 所有角色共享的领域资料库
    5. corpus/{name}_{uid}/weibo_profile_detail.json — 微博账号档案

    支持 fuzzy 匹配：中文关键词会用拼音首字母（如「宋雨琦」→「syq」）匹配；
    关键词太宽时会自动简化重试（复用 extract_core_keywords）。
    """
    skill_dir = cfg.SKILLS_DIR / skill_name
    corpus_dirs = _resolve_corpus_dirs(skill_name)
    # 取第一个作为 source_hint 显示用
    corpus_dir = corpus_dirs[0] if corpus_dirs else None

    if not skill_dir.exists() and not corpus_dirs:
        return f"角色 '{skill_name}' 的语料库不存在"

    # 1) 关键词简化重试准备
    raw_keywords = keywords
    simplified_keywords = extract_core_keywords(keywords)
    kw_candidates = [raw_keywords]
    if simplified_keywords and simplified_keywords != raw_keywords:
        kw_candidates.append(simplified_keywords)

    kw_list: list[str] = []
    for kw_phrase in kw_candidates:
        for k in kw_phrase.split():
            k = k.strip().lower()
            if k and k not in kw_list:
                kw_list.append(k)
    if not kw_list:
        return "未提供有效关键词"
    kw_list.sort(key=len, reverse=True)

    cfg.record_metric("search_corpus", "calls")

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
        return _fuzzy_match(line_lower, kw_list)

    # ── 1) skill 根目录 *.md 文件（人设/工作能力等） ──
    _MD_MAX = 4
    _md_added = 0
    if skill_dir.exists():
        for md_file in sorted(skill_dir.glob("*.md")):
            if len(results) >= _MAX_SNIPPETS or _md_added >= _MD_MAX:
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
                    _md_added += 1
                    if len(results) >= _MAX_SNIPPETS or _md_added >= _MD_MAX:
                        break

    # ── 2) corpus 语料库 *.txt 文件（微博帖子原文等，TF-IDF 评分，多目录合并） ──
    txt_hits: list[tuple[float, int, str]] = []
    if corpus_dirs:
        for corpus_dir in corpus_dirs:  # 多目录合并
            for txt_file in sorted(corpus_dir.glob("*.txt")):
                try:
                    content = txt_file.read_text(encoding="utf-8")
                except Exception:
                    continue
                if len(content) < 100:
                    continue
                lines = content.split("\n")
                total_lines = max(len(lines), 1)

                _kw_df: dict[str, int] = {}
                for kw in kw_list:
                    df = sum(1 for ln in lines if kw in ln.lower())
                    _kw_df[kw] = df

                _kw_idf: dict[str, float] = {}
                for kw, df in _kw_df.items():
                    if df > 0:
                        _kw_idf[kw] = math.log(total_lines / df)
                    else:
                        _kw_idf[kw] = 0.0

                def _score_line_idf(context_lower: str) -> float:
                    return sum(_kw_idf.get(kw, 0.0) for kw in kw_list if kw in context_lower)

                for i, line in enumerate(lines):
                    line_lower = line.lower()
                    if _match_line(line_lower):
                        start = max(0, i - 4)
                        end = min(len(lines), i + 5)
                        context_lower = "\n".join(lines[start:end]).lower()
                        score = _score_line_idf(context_lower)
                        snippet = "\n".join(lines[start:end]).strip()
                        # 标签：文件名 → 简化显示名
                        label = txt_file.stem
                        short_labels = {
                            "corpus_full": "微博语料",
                            "corpus_extended": "微博语料(扩展)",
                            "corpus": "微博语料",
                            "weibo_corpus": "微博语料",
                            "qq_group_msgs": "QQ群消息",
                            "raw_msgs": "原始消息",
                        }
                        display = short_labels.get(label, label)
                        # 多目录时标注来源目录
                        if len(corpus_dirs) > 1:
                            display = f"{display}[{corpus_dir.name}]"
                        txt_hits.append((score, i, f"[{display} 第{i+1}行]\n{snippet}"))

    _MAX_TXT_SNIPPETS = 8
    txt_hits.sort(key=lambda x: x[0], reverse=True)
    _seen_line_idxs: list[int] = []
    _txt_added = 0
    for score, line_idx, snippet in txt_hits:
        if _txt_added >= _MAX_TXT_SNIPPETS or len(results) >= _MAX_SNIPPETS:
            break
        if any(abs(line_idx - s) < 6 for s in _seen_line_idxs):
            continue
        _seen_line_idxs.append(line_idx)
        results.append(snippet)
        _txt_added += 1

    # ── 3) corpus 语料库 *.md 文件（附加参考文档，多目录合并） ──
    if corpus_dirs:
        for corpus_dir in corpus_dirs:
            for md_file in sorted(corpus_dir.glob("*.md")):
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
                        tag = md_file.name
                        if len(corpus_dirs) > 1:
                            tag = f"{md_file.name}[{corpus_dir.name}]"
                        results.append(f"[{tag} 第{i+1}行]\n{snippet}")
                        if len(results) >= _MAX_SNIPPETS:
                            break

    # ── 4) normal-paper 共享资料库（所有角色通用） ──
    # 2026-09-10: 改为递归（rglob），把 basic/ 子目录也纳入检索范围。
    # 背景：角色可以关掉 normal-paper 的常驻注入（include_normal_paper: false），
    # 关掉之后 knowledge 不该消失——留在资料库里按需检索即可。
    normal_paper_dir = cfg.NORMAL_PAPER_DIR
    if normal_paper_dir.is_dir():
        for md_file in sorted(normal_paper_dir.rglob("*.md")):
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
                    results.append(f"[资料库:{md_file.name} 第{i+1}行]\n{snippet}")
                    if len(results) >= _MAX_SNIPPETS:
                        break

    # ── 5) 微博账号档案（多目录合并，每个目录取一次） ──
    profile_files = []
    for cd in corpus_dirs:
        pf = cd / "weibo_profile_detail.json"
        if pf.exists():
            profile_files.append(pf)

    for profile_file in profile_files:
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
        cfg.record_metric("search_corpus", "zero_results")
        return f"在角色 '{skill_name}' 的语料库中未找到与「{keywords}」相关的内容"

    cfg.record_metric("search_corpus", "hits")
    # fuzzy 命中: 至少有 1 段是通过 fuzzy 命中的
    fuzzy_only = all(
        _fuzzy_match(seg.lower(), [k for k in kw_list if 2 <= len(k) <= 6 and any('\u4e00' <= c <= '\u9fff' for c in k)])
        for seg in results
    )
    if fuzzy_only:
        cfg.record_metric("search_corpus", "fuzzy_hits")

    # 添加 corpus 目录来源提示
    source_hint = ""
    if corpus_dirs:
        if len(corpus_dirs) == 1:
            source_hint = f" (来源: corpus/{corpus_dirs[0].name}/)"
        else:
            names = ", ".join(d.name for d in corpus_dirs)
            source_hint = f" (来源: {names})"

    return f"## 语料库搜索结果（{skill_name}）{source_hint}\n\n" + "\n\n---\n\n".join(results)
