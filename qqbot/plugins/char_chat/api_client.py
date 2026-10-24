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


# ── 思考过程（thinking mode）收集 ──
REASONING_LOG_CHARS = 1200


def _note_reasoning(text: str, *, source: str = "stream.reasoning_content") -> None:
    """把模型的思考过程落进 chat.log，并记一条 turn step

    2026-09-10 新增：流式分支此前只取 delta.content，delta.reasoning_content 被整个丢掉，
    导致主对话路径上完全看不到推理（chat.log 和 dashboard 面板都是空的）。
    这里用局部 import 引 turn_recorder，避免模块级循环依赖。
    """
    if not text:
        return
    snippet = " ⏎ ".join(text.strip().splitlines())
    if len(snippet) > REASONING_LOG_CHARS:
        snippet = snippet[:REASONING_LOG_CHARS] + "…[截断]"
    cfg.chat_logger.info(f"[REASONING] src={source} len={len(text)} | {snippet}")
    try:
        from . import turn_recorder

        turn_recorder.add_step({
            "kind": "thinking",
            "title": f"模型思考过程({source})",
            "data": {"text": text, "source": source},
        })
    except Exception as e:
        logger.debug(f"[REASONING] add_step failed: {e}")


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


# ── 错误分类（2026-06-28） ──
# 上次失败的错误类型,business 层可以根据它给不同的 fallback 文案
# 取值: 'sensitive'(422 输入敏感词)|'auth'(401/403)|'rate'(429)|'disallowed'(400 disallowed url)|
#       'model'(400 unknown model)|'server'(5xx)|'timeout'|None(成功)
_LAST_ERROR_TYPE: dict[str, str] = {}


def get_last_error(provider_name: str) -> str | None:
    """获取指定 provider 上次调用的错误类型(供 chat_handler 给友好 fallback)"""
    return _LAST_ERROR_TYPE.get(provider_name)


def _set_last_error(provider_name: str, err_type: str | None):
    _LAST_ERROR_TYPE[provider_name] = err_type


def _classify_error(status: int, body: str) -> str:
    """根据 HTTP 状态码 + body 分类错误类型"""
    if status == 422 and "sensitive" in body:
        return "sensitive"
    if status == 400 and "disallowed url" in body:
        return "disallowed"
    if status == 400 and ("unknown model" in body or "invalid params" in body):
        return "model"
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate"
    if status in (500, 502, 503, 529):
        return "server"
    return "unknown"


async def api_request(payload: dict, provider: dict = None) -> Optional[dict]:
    """底层 API 请求，带重试。返回 JSON dict 或 None。

    路由策略（2026-06-28 重构）：
    - 如果 caller 显式传 provider → 用 caller 指定
    - 如果没传 → 自动路由：
      - 含图片 → vision provider（minimax 多模态）
      - 无图片 → active provider（默认 deepseek）

    注：两个 Provider 完全独立,失败不互回退。
    """
    if provider is None:
        # 自动路由
        routed_payload, routed_name = prov.route_payload(payload)
        provider = prov.get_provider_for_route(routed_name)
        payload = routed_payload

    result, err_type = await _api_request_inner(payload, provider)
    if result is not None:
        prov.mark_provider_healthy(provider["name"])
        _set_last_error(provider["name"], None)
        return result

    # 标记当前 provider 不健康，但不切换到另一家
    prov.mark_provider_failed(provider["name"])
    _set_last_error(provider["name"], err_type)
    cfg.chat_logger.error(
        f"[LLM] {provider['name']} 调用失败({err_type or 'unknown'})，不自动回退。请在 Dashboard 切换主选或检查配置。"
    )
    return None


async def _api_request_inner(payload: dict, provider: dict) -> tuple[Optional[dict], str | None]:
    """对指定 provider 发送 API 请求，带重试。返回 (JSON dict 或 None, error_type)"""
    base = provider["base_url"].rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }
    adapted = prov.adapt_payload_for_provider(payload, provider["name"])
    client = get_http_client()
    last_error = None
    last_err_type: str | None = None
    pname = provider["name"]

    cfg.chat_logger.debug(
        f"[{pname}] POST {url} | model={adapted.get('model')} | "
        f"keys={sorted(adapted.keys())}"
    )

    for attempt in range(1, _API_MAX_RETRIES + 1):
        try:
            resp = await client.post(url, headers=headers, json=adapted)
            resp.raise_for_status()
            return resp.json(), None
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            body = e.response.text[:200]
            last_error = f"HTTP {status}: {body}"
            last_err_type = _classify_error(status, body)
            cfg.chat_logger.error(
                f"[{pname}] HTTP {status} (attempt {attempt}/{_API_MAX_RETRIES}, type={last_err_type}) | url={url} | body={body}"
            )
            # 422 sensitive / 400 model / 400 disallowed / 401 auth 这些重试无意义
            if last_err_type in ("sensitive", "model", "disallowed", "auth"):
                return None, last_err_type
            if status in (429, 500, 502, 503, 529) and attempt < _API_MAX_RETRIES:
                delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"[{pname}] API {status} (attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s: {body}")
                await asyncio.sleep(delay)
                continue
            logger.error(f"[{pname}] API HTTP error (attempt {attempt}/{_API_MAX_RETRIES}): {last_error}")
        except httpx.TimeoutException as e:
            last_error = f"Timeout: {e}"
            last_err_type = "timeout"
            if attempt < _API_MAX_RETRIES:
                delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"[{pname}] API timeout (attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s")
                await asyncio.sleep(delay)
                continue
            logger.error(f"[{pname}] API timeout (attempt {attempt}/{_API_MAX_RETRIES}): {e}")
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            last_err_type = "unknown"
            logger.error(f"[{pname}] API unexpected error (attempt {attempt}/{_API_MAX_RETRIES}): {last_error}")
            if attempt < _API_MAX_RETRIES:
                await asyncio.sleep(_API_RETRY_BASE_DELAY)
                continue

    logger.error(f"[{pname}] API failed after {_API_MAX_RETRIES} attempts: {last_error}")
    return None, last_err_type


async def _api_request_stream_inner(payload: dict, provider: dict):
    """对指定 provider 的流式 API 请求。成功 yield True，全部重试耗尽 yield False。"""
    base = provider["base_url"].rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"
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
                _reasoning_parts: list[str] = []
                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            _note_reasoning("".join(_reasoning_parts))
                            yield True
                            return
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {})
                            # 2026-09-10: 思考过程单独收集，不混进正文
                            _r = delta.get("reasoning_content") or delta.get("reasoning")
                            if _r:
                                _reasoning_parts.append(_r)
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                _note_reasoning("".join(_reasoning_parts))
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


async def api_request_stream(payload: dict, provider: dict | None = None):
    """流式 API 请求。失败时直接报错，不自动回退另一家 Provider。

    路由（2026-06-28）：
    - 如果 caller 显式传 provider（chat_handler 已 route 过）→ 用 caller 的
    - 否则自动路由：含图 → vision provider（minimax），无图 → active（默认 deepseek）
    """
    if provider is None:
        routed_payload, routed_name = prov.route_payload(payload)
        provider = prov.get_provider_for_route(routed_name)
        payload = routed_payload

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

    # 已发送部分内容则不再做其他动作
    if sent_any:
        return

    # 主 provider 失败且未产出任何内容 → 标记不健康，不回退
    if result is False:
        prov.mark_provider_failed(provider["name"])
        cfg.chat_logger.error(
            f"[LLM] stream: {provider['name']} 调用失败，不自动回退。请在 Dashboard 切换主选。"
        )
