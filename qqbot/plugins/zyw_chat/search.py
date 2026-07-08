"""
搜索引擎（搜狗/百度/微博/DDG）、页面抓取、语料库搜索
"""

import asyncio
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
                "【联网搜索 - 兜底工具】在网络上搜索实时信息。系统会从百度、微博、DuckDuckGo 等多源并行搜索，"
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


# ── 联网搜索优先站点（搜狗 site: 搜索） ──
_PRIORITY_SITES = [
    "weibo.com",
    "baike.baidu.com",
]


# ── 页面正文抓取 ──
_HIGH_VALUE_DOMAINS = [
    "fandom.com", "baike.baidu.com", "wiki", "zh.wikipedia.org",
]
_PAGE_FETCH_TIMEOUT = 8
_PAGE_CONTENT_MAX_CHARS = 1500


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


async def fetch_page_content(url: str, timeout_s: int = _PAGE_FETCH_TIMEOUT) -> tuple[str, str]:
    """抓取 URL 页面并提取正文文本。返回 (final_url, content)
    
    对于 fandom.com 域名，优先使用 MediaWiki API 绕过 Cloudflare 反爬。
    """
    if not url or url.startswith("/sf/") or url.startswith("javascript:"):
        return ("", "")
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


def is_high_value_url(url: str) -> bool:
    """判断 URL 是否属于值得抓取全文的高价值域名"""
    if not url:
        return False
    lower = url.lower()
    return any(domain in lower for domain in _HIGH_VALUE_DOMAINS)


# ── 微博直搜 ──
_WEIBO_COOKIES_FILE = cfg.QQBOT_DIR.parent / "cookies.json"
_weibo_cookies_cache: Optional[dict] = None
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


def _extract_weibo_item(item: dict, results: list, max_results: int):
    """从微博桌面端搜索结果 item 提取搜索条目"""
    if len(results) >= max_results:
        return
    text = item.get("text_raw", "")
    if not text:
        text = re.sub(r'<[^>]+>', '', item.get("text", "")).strip()
    user_info = item.get("user", {})
    username = user_info.get("screen_name", "未知用户")
    user_id = user_info.get("id", "")
    mid = item.get("mid") or item.get("id", "")
    link = f"https://weibo.com/{user_id}/{mid}" if mid and user_id else ""
    user_profile_url = f"https://weibo.com/u/{user_id}" if user_id else ""
    snippet = text[:200] + ("..." if len(text) > 200 else "")
    results.append({
        "title": f"@{username}: {text[:50]}...",
        "href": link,
        "url": link,
        "user_profile_url": user_profile_url,
        "username": username,
        "body": snippet,
    })


async def search_weibo_direct(query: str, max_results: int = 5, timeout_s: float = 30) -> list:
    """用 cookie 调微博全局搜索 API"""
    cookie_data = _load_weibo_cookies()
    if not cookie_data:
        logger.warning("[weibo] cookies.json 不存在或加载失败，跳过微博直搜")
        return []

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
        client = _get_search_client()
        resp = await client.get(url, headers=headers, follow_redirects=False)

        if resp.status_code in (301, 302, 303):
            logger.warning(f"[weibo] cookie 已过期 (HTTP {resp.status_code})，需要更新 cookies.json")
            return []

        resp.raise_for_status()
        data = resp.json()

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


# ── 微博用户搜索（已废弃） ──
async def search_weibo_user(query: str, max_results: int = 3, timeout_s: float = 20) -> list:
    """搜索微博用户 — endpoint 已下线，始终返回空"""
    logger.debug(f"[weibo] 用户搜索已废弃，跳过: {query[:40]}")
    return []


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


# ── 搜狗搜索 ──
async def search_sogou(query: str, max_results: int = 10, timeout_s: float = 12) -> list:
    """搜索搜狗网页，解析 HTML 返回结构化结果列表"""
    url = f"https://www.sogou.com/web?query={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        client = _get_search_client()
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text
    except httpx.TimeoutException:
        logger.warning(f"[sogou] 搜索超时: {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[sogou] 搜索失败: {e}")
        return []

    _TAG_RE = re.compile(r'<[^>]+>')
    _ENTITY_RE = re.compile(r'&(?:nbsp|amp|lt|gt|quot|#\d+|#x[\da-fA-F]+);')

    def _clean(text: str) -> str:
        text = _TAG_RE.sub('', text)
        text = _ENTITY_RE.sub(' ', text)
        return ' '.join(text.split())

    results = []
    containers = re.split(r'<div[^>]*class="[^"]*(?:vrwrap|rb)[^"]*"', html)
    for block in containers[1:]:
        h3 = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        if not h3:
            continue
        title = _clean(h3.group(1))
        if not title:
            continue
        link = ""
        href_match = re.search(r'<a[^>]*href="([^"]*)"', h3.group(0), re.DOTALL)
        if href_match:
            link = href_match.group(1)
            if link.startswith("/link?url="):
                link = f"https://www.sogou.com{link}"
        snippet = ""
        for sm in re.finditer(r'<(?:p|span|div)[^>]*class="[^"]*(?:str-text|str_info|text-layout|space-txt)[^"]*"[^>]*>(.*?)</(?:p|span|div)>', block, re.DOTALL):
            t = _clean(sm.group(1))
            if len(t) > 15:
                snippet = t
                break
        if not snippet:
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
        html = resp.text
    except httpx.TimeoutException:
        logger.warning(f"[baidu] 搜索超时: {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[baidu] 搜索失败: {e}")
        return []

    _TAG_RE = re.compile(r'<[^>]+>')
    _ENTITY_RE = re.compile(r'&(?:nbsp|amp|lt|gt|quot|#\d+|#x[\da-fA-F]+);')

    def _clean(text: str) -> str:
        text = _TAG_RE.sub('', text)
        text = _ENTITY_RE.sub(' ', text)
        return ' '.join(text.split())

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


# ── DuckDuckGo Instant Answer API ──
async def search_ddg_instant(query: str, timeout_s: float = 8) -> list:
    """DuckDuckGo Instant Answer API，返回实体摘要和相关主题。"""
    url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        client = _get_search_client()
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


# ── 主搜索编排 ──
async def execute_web_search(query: str, max_results: int = 5) -> str:
    """异步执行联网搜索：百度主力 + 微博补充 + 搜狗兜底 + DDG 即时答案 + 页面内容抓取。"""

    cfg.record_metric("web_search", "calls")

    _TIMEOUT = 15

    async def _baidu_with_timeout(q: str, max_r: int) -> tuple[str, list]:
        try:
            res = await asyncio.wait_for(
                search_baidu(q, max_results=max_r, timeout_s=_TIMEOUT),
                timeout=_TIMEOUT + 3,
            )
            return (q, res)
        except asyncio.TimeoutError:
            logger.warning(f"[web_search] 百度搜索超时 ({_TIMEOUT}s): {q[:60]}")
            return (q, [])
        except Exception as e:
            logger.warning(f"[web_search] 百度搜索失败: {e}")
            return (q, [])

    async def _sogou_with_timeout(q: str, max_r: int) -> tuple[str, list]:
        try:
            res = await asyncio.wait_for(
                search_sogou(q, max_results=max_r, timeout_s=12),
                timeout=14,
            )
            return (q, res)
        except asyncio.TimeoutError:
            logger.warning(f"[web_search] 搜狗搜索超时: {q[:60]}")
            return (q, [])
        except Exception as e:
            logger.warning(f"[web_search] 搜狗搜索失败: {e}")
            return (q, [])

    async def _ddg_with_timeout(q: str) -> tuple[str, list]:
        try:
            res = await asyncio.wait_for(
                search_ddg_instant(q, timeout_s=6),
                timeout=9,
            )
            return ("ddg", res)
        except asyncio.TimeoutError:
            logger.warning(f"[web_search] DDG 搜索超时: {q[:60]}")
            return ("ddg", [])
        except Exception as e:
            logger.warning(f"[web_search] DDG 搜索失败: {e}")
            return ("ddg", [])

    _weibo_cookie_expired = False
    _weibo_alert_written = False

    async def _weibo_wrapper() -> tuple[str, list]:
        nonlocal _weibo_cookie_expired, _weibo_alert_written
        cookie_data = _load_weibo_cookies()
        if not cookie_data:
            _weibo_cookie_expired = True
            if not _weibo_alert_written:
                _set_weibo_cookie_alert(True, "cookies.json 不存在")
                _weibo_alert_written = True
            return ("weibo_direct", [])

        res = await search_weibo_direct(query, max_results=8, timeout_s=_TIMEOUT)
        if res:
            _weibo_cookie_expired = False
            if _weibo_alert_written:
                _set_weibo_cookie_alert(False, "")
                _weibo_alert_written = False
            return ("weibo_direct", res)
        if cookie_data and not res:
            _weibo_cookie_expired = True
            if not _weibo_alert_written:
                _set_weibo_cookie_alert(True, "微博 API 返回 0 结果（cookie 可能已过期）")
                _weibo_alert_written = True
        simplified = extract_core_keywords(query)
        if simplified != query:
            res = await search_weibo_direct(simplified, max_results=8, timeout_s=_TIMEOUT)
            if res:
                _weibo_cookie_expired = False
                if _weibo_alert_written:
                    _set_weibo_cookie_alert(False, "")
                    _weibo_alert_written = False
                logger.info(f"[weibo] 简化关键词命中: '{query}' → '{simplified}'")
                return ("weibo_direct", res)
        logger.info(f"[weibo] 所有搜索均未命中: query='{query}'")
        return ("weibo_direct", [])

    # 检查健康状态
    baidu_ok = cfg.is_search_healthy("baidu")
    sogou_ok = cfg.is_search_healthy("sogou")
    weibo_ok = cfg.is_search_healthy("weibo")
    ddg_ok = cfg.is_search_healthy("ddg")
    if not baidu_ok:
        logger.info("[web_search] 百度不健康，跳过")
    if not sogou_ok:
        logger.info("[web_search] 搜狗不健康，跳过")
    if not weibo_ok:
        logger.info("[web_search] 微博不健康，跳过")
    if not ddg_ok:
        logger.info("[web_search] DDG 不健康，跳过")

    # ── 第一轮：百度 + 微博 + DDG 并行 ──
    round1_tasks = []
    if baidu_ok:
        round1_tasks.append(_baidu_with_timeout(query, max_results + 5))
    if weibo_ok:
        round1_tasks.append(_weibo_wrapper())
    if ddg_ok:
        round1_tasks.append(_ddg_with_timeout(query))
    round1_results = await asyncio.gather(*round1_tasks) if round1_tasks else []

    results = []
    seen_urls = set()
    baidu_total = 0
    weibo_total = 0
    ddg_total = 0

    for _q, items in round1_results:
        if _q == "weibo_direct":
            weibo_total = len(items)
            if weibo_total > 0:
                cfg.record_metric("by_source", "weibo", weibo_total)
            for r in items:
                url = r.get("href") or r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(("微博", r))
        elif _q == "ddg":
            ddg_total = len(items)
            if ddg_total > 0:
                cfg.record_metric("by_source", "ddg", ddg_total)
                cfg.record_metric("web_search", "ddg_hits", ddg_total)
            for r in items:
                url = r.get("href") or r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(("DDG", r))
                elif not url and r.get("body"):
                    # DDG 摘要经常没有 URL 也保留
                    results.append(("DDG", r))
        else:
            baidu_total = len(items)
            if baidu_total > 0:
                cfg.record_metric("by_source", "baidu", baidu_total)
            for r in items:
                url = r.get("href") or r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(("百度", r))

    # 更新健康状态
    if baidu_ok:
        cfg.update_search_health("baidu", baidu_total > 0)
    if weibo_ok:
        cfg.update_search_health("weibo", weibo_total > 0)
    if ddg_ok:
        cfg.update_search_health("ddg", ddg_total > 0)

    # ── 第二轮：搜狗兜底（仅百度无结果时） ──
    sogou_total = 0
    if baidu_total == 0 and sogou_ok:
        logger.info("[web_search] 百度无结果，启用搜狗兜底")
        cfg.record_metric("web_search", "fallback_rounds")
        _, sogou_items = await _sogou_with_timeout(query, max_results + 3)
        sogou_total = len(sogou_items)
        if sogou_total > 0:
            cfg.record_metric("by_source", "sogou", sogou_total)
        for r in sogou_items:
            url = r.get("href") or r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(("搜狗", r))
        cfg.update_search_health("sogou", sogou_total > 0)

    if not results:
        return "未找到相关结果。"

    cfg.record_metric("web_search", "hits")

    # ── 页面内容增强 ──
    _ENRICH_TITLE_KEYWORDS = [
        "wiki", "百科", "fandom", "维基百科", "百度百",
        "官方", "官网", "简介", "资料", "profile",
    ]

    def _should_enrich(r: dict) -> bool:
        title = (r.get("title") or "").lower()
        url = (r.get("href") or r.get("url") or "").lower()
        if is_high_value_url(url):
            return True
        return any(kw in title for kw in _ENRICH_TITLE_KEYWORDS)

    fetch_targets = []
    for idx, (source, r) in enumerate(results):
        url = r.get("href") or r.get("url", "")
        if url and _should_enrich(r) and len(fetch_targets) < 3:
            fetch_targets.append((idx, url))

    page_contents = {}
    if fetch_targets:
        fetch_tasks = [fetch_page_content(url) for _, url in fetch_targets]
        fetch_results = await asyncio.gather(*fetch_tasks)
        for (idx, url), (final_url, content) in zip(fetch_targets, fetch_results):
            if content and len(content) > 80:
                if is_high_value_url(final_url) or len(content) > 200:
                    page_contents[idx] = content
                if final_url:
                    seen_urls.add(final_url)
        logger.info(
            f"[web_search] 页面增强: 抓取 {len(fetch_targets)} 页, "
            f"有效 {len(page_contents)} 页"
        )

    # ── 结果去重与排序 ──
    seen_final = set()
    deduped_results = []
    for source, r in results:
        url = (r.get("href") or r.get("url", "")).rstrip('/')
        if url and url not in seen_final:
            seen_final.add(url)
            deduped_results.append((source, r))
    results = deduped_results

    query_lower = query.lower()
    def _relevance_score(item: tuple) -> int:
        source, r = item
        score = 0
        title = (r.get("title") or "").lower()
        body = (r.get("body") or r.get("snippet", "")).lower()
        for kw in query_lower.split():
            if kw in title:
                score += 3
        for kw in query_lower.split():
            if kw in body:
                score += 1
        if any(kw in title for kw in ["百科", "wiki", "维基"]):
            score += 2
        return score
    results.sort(key=_relevance_score, reverse=True)
    results = results[:max_results * 2]

    # ── 解析百度重定向URL ──
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
                # 检查是否是微博用户ID格式（https://weibo.com/数字ID），转换为用户主页URL
                weibo_user_match = re.match(r'^https?://weibo\.com/(\d+)/?$', resolved_url)
                if weibo_user_match:
                    user_id = weibo_user_match.group(1)
                    resolved_url = f"https://weibo.com/u/{user_id}"
                    logger.info(f"[search] 转换微博用户主页URL: {user_id} -> {resolved_url}")
                r["href"] = resolved_url
                r["url"] = resolved_url
                logger.info(f"[search] 解析百度重定向: 原URL -> {resolved_url[:80]}")

    # ── 格式化输出 ──
    source_summary = f"百度 {baidu_total} + 微博 {weibo_total} + DDG {ddg_total}"
    if sogou_total > 0:
        source_summary += f" + 搜狗 {sogou_total}"
    lines = [f"## 网络搜索结果（{source_summary}）\n"]

    if _weibo_cookie_expired and weibo_total == 0:
        lines.append("⚠️ 微博搜索不可用（Cookie 可能已过期，请在 Dashboard 上传新的 cookies.json）\n")

    # ── 收集微博用户主页URL ──
    weibo_user_profiles = {}
    for source, r in results:
        if source == "微博":
            user_profile_url = r.get("user_profile_url", "")
            username = r.get("username", "")
            if user_profile_url and username:
                weibo_user_profiles[username] = user_profile_url

    for i, (source, r) in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("href") or r.get("url", "")
        body = r.get("body") or r.get("snippet", "")
        tag = f" [{source}]" if source != "百度" else ""

        # 记录URL信息用于调试
        if url:
            logger.info(f"[search] 结果[{i}] source={source} url={url[:80]}")

        page_text = page_contents.get(i - 1, "")
        if page_text:
            lines.append(
                f"[{i}] {title}{tag}\n"
                f"    链接: {url}\n"
                f"    【页面内容】:\n{page_text}\n"
            )
        else:
            lines.append(f"[{i}] {title}{tag}\n    链接: {url}\n    摘要: {body}\n")

    # ── 添加微博用户主页链接汇总 ──
    if weibo_user_profiles:
        lines.append("\n## 微博用户主页链接\n")
        for username, profile_url in weibo_user_profiles.items():
            lines.append(f"- @{username}: {profile_url}")

    return "\n".join(lines)


def _resolve_corpus_dirs(skill_name: str) -> list[Path]:
    """解析所有匹配 skill 名的 corpus 目录，按名称排序（新的在前）。

    例: ytj → [corpus/ytj_v3/, corpus/ytj_v2/, corpus/ytj_7841140689/]
    同时回退到 skills/{name}/corpus_ref/（如果存在）。
    """
    dirs: list[Path] = []
    if cfg.CORPUS_DIR.is_dir():
        # 按目录名字典序倒序（假设新版名字里有更高版本号或更晚日期）
        for d in sorted(cfg.CORPUS_DIR.iterdir(), reverse=True):
            if d.is_dir() and d.name.startswith(skill_name + "_"):
                dirs.append(d)
    skill_dir = cfg.SKILLS_DIR / skill_name
    fallback = skill_dir / "corpus_ref"
    if fallback.is_dir():
        dirs.append(fallback)
    return dirs


def _resolve_corpus_dir(skill_name: str) -> Optional[Path]:
    """向后兼容：返回最新一个 corpus 目录（推荐使用 _resolve_corpus_dirs 多目录合并）"""
    dirs = _resolve_corpus_dirs(skill_name)
    return dirs[0] if dirs else None


def _set_weibo_cookie_alert(expired: bool, reason: str = ""):
    """写微博 Cookie 状态到运行时配置文件，Dashboard 可读到"""
    try:
        data = {}
        if cfg._RUNTIME_CONFIG_FILE.exists():
            data = json.loads(cfg._RUNTIME_CONFIG_FILE.read_text(encoding="utf-8"))
        data["weibo_cookie_status"] = {
            "expired": expired,
            "reason": reason,
            "updated_at": time.time(),
        }
        cfg._RUNTIME_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        cfg._RUNTIME_CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug(f"[weibo_alert] 写入失败: {e}")


def get_weibo_cookie_status() -> dict:
    """Dashboard 读这个函数拿微博 Cookie 状态"""
    try:
        if cfg._RUNTIME_CONFIG_FILE.exists():
            data = json.loads(cfg._RUNTIME_CONFIG_FILE.read_text(encoding="utf-8"))
            return data.get("weibo_cookie_status", {})
    except Exception:
        pass
    return {}


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

    # ── 4) normal-paper 根目录共享资料库（所有角色通用） ──
    normal_paper_dir = cfg.NORMAL_PAPER_DIR
    if normal_paper_dir.is_dir():
        for md_file in sorted(normal_paper_dir.glob("*.md")):
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
