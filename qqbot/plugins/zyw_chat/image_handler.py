"""
图片消息处理：把 QQ 消息里的 image segment 抽出，转成 OpenAI 多模态 content 块。

设计要点（2026-06-27 用户决策 A1B2C1D1）:
  B2 — 优先用 image segment 里的 url 字段（snowluma / OneBot v11 通常会带 cdn url），
       让 minimax 服务端自己去 fetch。零带宽,零 base64 编解码,响应最快。
       当 url 不存在时(罕见),回退到下载原图转 base64。
  C1 — 用户的图都看,不区分表情包/截图/自拍。Bot 看图后给自然反应。
  D1 — 历史记录里只存文字描述(由 minimax 自己生成或我们自己加占位符)，
       这样历史回放时不携带图片 token,省成本。

2026-06-29 补丁:
  minimax 中转对 QQ CDN 域名（multimedia.nt.qq.com.cn / gchat.qpic.cn）报
  "disallowed url"。在 _resolve_image_url 里加一个白名单判断：
  命中"minimax 中转不认"的域名时,主动下载原图转 base64 data URI,
  这样 minimax 只看到 data: URL（永远在 allowlist 里）,稳定可用。

返回结构（兼容 OpenAI Chat Completion 多模态格式）:
  [
    {"type": "text", "text": "<原文本>"},
    {"type": "image_url", "image_url": {"url": "<http url 或 data:image/...;base64,...>"}},
    ...
  ]
"""
import asyncio
import base64
import logging
from typing import Optional

import httpx

from nonebot import logger

from . import config as cfg


# 单张图下载超时（秒）—— B2 路径实际不下载,但 base64 兜底用得到
_DOWNLOAD_TIMEOUT = 15
# 单张图最大字节数(防止 OOM)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
# 允许的图片 content-type 前缀
_IMAGE_CT_PREFIX = ("image/",)

# 2026-06-29: minimax 中转拒绝的 URL 域名(主要是 QQ 自家 CDN)。
# 命中这些域名时,主动下载转 data URI 兜底,避免 400 disallowed。
# 已知坏域名:multimedia.nt.qq.com.cn (私聊/群聊图),gchat.qpic.cn (旧版群图)。
_DISALLOWED_DOMAINS = (
    "multimedia.nt.qq.com.cn",
    "gchat.qpic.cn",
    "c2cpicdw.qpic.cn",
)


def extract_image_segments(event) -> list[dict]:
    """从 OneBot v11 消息事件里抽出所有 image segment 的 data。

    返回 list[dict],每个 dict 是 seg.data 原始内容(含 url/file/cache/type 等字段)。
    """
    images = []
    msg = getattr(event, "message", None)
    if msg is None:
        return images
    try:
        for seg in msg:
            if getattr(seg, "type", "") == "image":
                images.append(dict(seg.data or {}))
    except Exception as e:
        logger.warning(f"[image] 抽取 image segment 失败: {e}")
    return images


def build_multimodal_user_content(text: str, images: list[dict], image_url_resolver=None) -> list[dict]:
    """把用户文本 + 图片列表组装成 OpenAI 多模态 content 数组。

    参数:
      text: 用户原始文本(可能为空)
      images: extract_image_segments() 的返回
      image_url_resolver: async (seg_data) -> Optional[str] 的函数。
        给 None 时,只用 seg.data["url"] 同步解析;给 resolver 时
        (例如 bot.call_api("get_image")),会先尝试 resolver 拿真 url。

    返回:
      list[dict] — OpenAI Chat Completion 标准的 content 数组。
      若没有图片,返回 [{"type":"text","text":text}]。
      若有图片但解析全失败,只发文本 + 在 text 前加 [用户发了 X 张图但解析失败] 提示。
    """
    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})

    resolved_count = 0
    fail_count = 0
    for seg_data in images:
        url = _resolve_image_url(seg_data, image_url_resolver)
        if url:
            content.append({"type": "image_url", "image_url": {"url": url}})
            resolved_count += 1
        else:
            fail_count += 1

    if fail_count and not resolved_count and not text:
        # 全失败且无文字 — 给个占位,避免 messages 完全空
        return [{"type": "text", "text": "[用户发送了图片,但 bot 暂时无法识别]"}]
    if fail_count:
        # 部分失败:在末尾追加提示
        content.append({
            "type": "text",
            "text": f"[提示: 用户共发了 {len(images)} 张图片,其中 {fail_count} 张解析失败]",
        })
    return content


def _resolve_image_url(seg_data: dict, image_url_resolver=None) -> Optional[str]:
    """解析 image segment → 可用 url(优先直链,失败回退 base64)。

    B2 策略:
      1) seg_data["url"] — snowluma 通常会给 cdn url,直接给 minimax fetch
      2) 同步直接走 seg_data["url"](因为这是异步函数里调用的,resolver 留接口给将来用)
      3) 兜底:base64 download(本地浏览器 / 没有 url 字段的情况)

    2026-06-29 补丁: 第 1 步里如果 URL 域名在 _DISALLOWED_DOMAINS(minimax 中转拒绝的),
    主动下载转 data URI。httpx sync 调用,在 image_handler 调用栈内(<1s 完成)。
    """
    direct_url = (seg_data.get("url") or "").strip()
    if direct_url and direct_url.startswith(("http://", "https://")):
        if _is_disallowed_domain(direct_url):
            # minimax 中转会 400 → 主动下载转 data URI
            logger.info(f"[image] URL 命中 disallowed 域名,转 base64 data URI: {direct_url[:80]}")
            try:
                return _download_to_data_uri_sync(direct_url)
            except Exception as e:
                logger.warning(f"[image] disallowed 域名下载转 data URI 失败: {e}")
                # 下载失败也回退到原 URL(让 minimax 自己去试,虽然大概率还是 400)
                return direct_url
        return direct_url

    # 文件路径或 base64 已经在 seg_data["file"] 里(自己发的 / 缓存的)
    file_field = seg_data.get("file", "")
    if isinstance(file_field, str):
        if file_field.startswith("base64://"):
            b64 = file_field[len("base64://"):]
            return f"data:image/jpeg;base64,{b64}"
        if file_field.startswith("http://") or file_field.startswith("https://"):
            if _is_disallowed_domain(file_field):
                try:
                    return _download_to_data_uri_sync(file_field)
                except Exception as e:
                    logger.warning(f"[image] disallowed 域名(file)下载失败: {e}")
                    return file_field
            return file_field

    # 兜底: 尝试同步下载(只对绝对路径的 file 字段有意义)
    if file_field and (file_field.startswith("/") or file_field[1:3] == ":\\"):
        try:
            return _local_file_to_data_uri(file_field)
        except Exception as e:
            logger.warning(f"[image] 兜底读本地文件失败: {file_field} | {e}")

    return None


def _is_disallowed_domain(url: str) -> bool:
    """2026-06-29: 检查 URL 是否在 minimax 中转的"黑名单"域名里。"""
    from urllib.parse import urlparse
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    for d in _DISALLOWED_DOMAINS:
        if host == d or host.endswith("." + d):
            return True
    return False


def _download_to_data_uri_sync(url: str) -> str:
    """2026-06-29: 同步下载远程图 → data URI(httpx sync,适用于 sync 调用栈)。

    Raises Exception on failure,让上层决定是否 fallback 到原 URL。
    """
    with httpx.Client(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "").lower()
        if not ct.startswith(_IMAGE_CT_PREFIX):
            raise ValueError(f"non-image content-type: {ct}")
        data = resp.content
        if len(data) > _MAX_IMAGE_BYTES:
            raise ValueError(f"image too large: {len(data)} bytes")
        b64 = base64.b64encode(data).decode("ascii")
        mime = ct.split(";")[0].strip() or "image/jpeg"
        return f"data:{mime};base64,{b64}"


def _local_file_to_data_uri(path: str) -> str:
    """本地路径 → data URI(用于兜底)。文件不存在或超大时抛异常。"""
    import os
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    size = os.path.getsize(path)
    if size > _MAX_IMAGE_BYTES:
        raise ValueError(f"image too large: {size} bytes")
    # 简单按扩展名猜 mime
    ext = os.path.splitext(path)[1].lower()
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def download_image_to_data_uri(url: str) -> Optional[str]:
    """异步下载远程图片 → data URI(用于 url 解析失败 / 模型服务端 fetch 不可达时的兜底)。

    不在主路径(B2)使用,留给将来扩展。
    """
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "").lower()
            if not ct.startswith(_IMAGE_CT_PREFIX):
                logger.warning(f"[image] 非图片 content-type: {ct} | url={url[:80]}")
                return None
            data = resp.content
            if len(data) > _MAX_IMAGE_BYTES:
                logger.warning(f"[image] 图片过大: {len(data)} bytes")
                return None
            b64 = base64.b64encode(data).decode("ascii")
            mime = ct.split(";")[0].strip() or "image/jpeg"
            return f"data:{mime};base64,{b64}"
    except Exception as e:
        logger.warning(f"[image] 下载失败: {url[:80]} | {e}")
        return None


def summarize_images_for_history(images_count: int) -> str:
    """D1 — 历史里只存文字占位符,不带图。

    用户聊到一半可能说"上面那张图",bot 至少知道之前有过图。
    """
    if images_count <= 0:
        return ""
    if images_count == 1:
        return "[用户上一条消息里发了一张图片]"
    return f"[用户上一条消息里发了 {images_count} 张图片]"