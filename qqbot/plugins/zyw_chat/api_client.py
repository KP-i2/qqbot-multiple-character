"""
HTTP 客户端、API 请求（普通 + 流式）、重试、并发控制
"""

import asyncio
import json
from typing import Optional

import httpx
from nonebot import logger

from . import config as cfg
from . import provider as prov


# ── 并发控制 ──
API_SEMAPHORE = asyncio.Semaphore(30)       # 全局最多 30 个 API 请求同时进行
_user_processing: dict[str, asyncio.Lock] = {}  # 每个用户一把锁


def get_user_lock(uid: str) -> asyncio.Lock:
    """获取/创建用户级别的锁（惰性初始化）"""
    if uid not in _user_processing:
        _user_processing[uid] = asyncio.Lock()
    return _user_processing[uid]


def release_user_lock(uid: str):
    """释放用户锁（无竞争时清理，防止内存泄漏）"""
    lock = _user_processing.get(uid)
    if lock and not lock.locked():
        _user_processing.pop(uid, None)


# ── 持久化 HTTP 客户端 ──
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """获取或创建持久化 HTTP 客户端"""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.REQUEST_TIMEOUT, connect=15),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


def close_http_client():
    """关闭 HTTP 客户端（供 shutdown 调用），返回原客户端以便外部 aclose"""
    global _http_client
    old = _http_client
    _http_client = None
    return old


_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 2  # 秒


async def api_request(payload: dict, provider: dict = None) -> Optional[dict]:
    """底层 API 请求，带重试和 provider 回退。返回 JSON dict 或 None"""
    if provider is None:
        provider = prov.get_active_provider()

    result = await _api_request_inner(payload, provider)
    if result is not None:
        prov.mark_provider_healthy(provider["name"])
        return result

    # 主 provider 失败，尝试回退
    fallback_name = "deepseek" if provider["name"] == "openai" else None
    if fallback_name:
        prov.mark_provider_failed(provider["name"])
        fallback = prov.get_provider(fallback_name)
        cfg.chat_logger.info(f"[LLM] {provider['name']} 失败，回退到 {fallback_name}")
        result = await _api_request_inner(payload, fallback)
        if result is not None:
            prov.mark_provider_healthy(fallback["name"])
    return result


async def _api_request_inner(payload: dict, provider: dict) -> Optional[dict]:
    """对指定 provider 发送 API 请求，带重试。返回 JSON dict 或 None"""
    url = f"{provider['base_url']}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }
    adapted = prov.adapt_payload_for_provider(payload, provider["name"])
    client = get_http_client()
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
    adapted = prov.adapt_payload_for_provider(payload, provider["name"])
    adapted = {**adapted, "stream": True}
    client = get_http_client()
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


async def api_request_stream(payload: dict):
    """流式 API 请求，带 provider 回退。优先使用活跃 provider，失败后回退 DeepSeek。"""
    provider = prov.get_active_provider()
    sent_any = False
    result = None

    async for chunk in _api_request_stream_inner(payload, provider):
        if chunk is True:
            prov.mark_provider_healthy(provider["name"])
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
        prov.mark_provider_failed(provider["name"])
        fallback = prov.get_provider(fallback_name)
        cfg.chat_logger.info(f"[LLM] stream: {provider['name']} 失败，回退到 {fallback_name}")
        async for chunk in _api_request_stream_inner(payload, fallback):
            if chunk is True:
                prov.mark_provider_healthy(fallback["name"])
                return
            elif chunk is False:
                return
            else:
                yield chunk
