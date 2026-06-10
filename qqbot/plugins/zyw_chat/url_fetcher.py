"""
URL 提取与内容抓取（B站 API、通用页面）
"""

import asyncio
import logging
import re

import httpx
from nonebot import logger

from . import config as cfg
from .search import fetch_page_content


_log = logging.getLogger("zyw_chat.events")

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


def extract_urls(text: str) -> list[str]:
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


def parse_bvid(url: str) -> str | None:
    """从 B 站 URL 中提取 BV 号。"""
    m = re.search(r'(BV[a-zA-Z0-9]{10})', url)
    return m.group(1) if m else None


async def fetch_bilibili_info(bvid: str) -> str | None:
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


async def resolve_short_url(url: str) -> str:
    """解析短链接，返回最终 URL。"""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            resp = await client.head(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            return str(resp.url)
    except Exception:
        return url


async def fetch_url_content(url: str) -> str | None:
    """抓取 URL 内容：B 站走专用 API，其他走通用页面抓取。"""
    lower = url.lower()

    # B 站短链先解析
    if "b23.tv" in lower:
        resolved = await resolve_short_url(url)
        if "bilibili" in resolved.lower():
            bvid = parse_bvid(resolved)
            if bvid:
                return await fetch_bilibili_info(bvid)
        # 短链解析后不是 B 站，走通用抓取
        _, content = await fetch_page_content(resolved)
        return content if content else None

    # B 站长链
    if "bilibili.com" in lower:
        bvid = parse_bvid(url)
        if bvid:
            return await fetch_bilibili_info(bvid)

    # 通用页面抓取
    _, content = await fetch_page_content(url)
    return content if content else None


async def process_message_urls(urls: list[str]) -> str | None:
    """处理消息中的所有 URL，返回拼接后的上下文文本。"""
    if not urls:
        return None

    tasks = [fetch_url_content(u) for u in urls[:_URL_MAX_FETCH]]
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
