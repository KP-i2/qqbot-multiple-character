"""
LLM Provider 管理（minimax / DeepSeek 平级，不做自动回退）

按用户 2026-06-27 决策：
  - 两个 Provider 完全独立，不互相兜底
  - 通过运行时参数 `active_llm_provider` 切换主选
  - Provider 失败时不自动回退另一家，直接报错
  - Dashboard 网页可按钮切换
"""
import time

from nonebot import logger

from . import config as cfg


# Provider 健康状态（用于 dashboard 展示，但不参与回退决策）
_provider_health: dict[str, dict] = {
    "minimax": {"healthy": True, "fail_count": 0, "last_fail": 0},
    "deepseek": {"healthy": True, "fail_count": 0, "last_fail": 0},
}
_PROVIDER_COOLDOWN = 120  # 标记不健康后的冷却秒数（仅 dashboard 展示用）

# 旧版回退链保留但不再被使用（防止 dashboard/旧调用残留报错）
FALLBACK_CHAIN: dict[str, str | None] = {
    "minimax": None,
    "deepseek": None,
}


def get_provider(name: str) -> dict:
    """获取指定 provider 的配置信息"""
    if name == "minimax":
        return {
            "name": "minimax",
            "base_url": cfg.MINIMAX_BASE_URL,
            "api_key": cfg.MINIMAX_API_KEY,
            "model": cfg.MINIMAX_MODEL,
        }
    return {
        "name": "deepseek",
        "base_url": cfg.DEEPSEEK_BASE_URL,
        "api_key": cfg.DEEPSEEK_API_KEY,
        "model": cfg.DEEPSEEK_MODEL,
    }


def _is_provider_available(name: str) -> bool:
    """检查 provider 是否配置了可用凭证（不是空、不是占位）"""
    if name == "minimax":
        return bool(
            cfg.MINIMAX_ENABLED
            and cfg.MINIMAX_API_KEY
            and cfg.MINIMAX_BASE_URL
            and not cfg.MINIMAX_API_KEY.startswith("sk-your")
            and not cfg.MINIMAX_API_KEY.startswith("sk-cp-your")
        )
    # deepseek 总是兜底,但 key 缺失就不算"可用"
    return bool(
        cfg.DEEPSEEK_API_KEY
        and not cfg.DEEPSEEK_API_KEY.startswith("sk-your")
    )


def _is_provider_healthy(name: str) -> bool:
    """检查 provider 当前是否在冷却期内"""
    h = _provider_health.get(name)
    if not h:
        return True
    if h["healthy"]:
        return True
    # 冷却期已过,自动恢复
    if time.time() - h["last_fail"] > _PROVIDER_COOLDOWN:
        h["healthy"] = True
        h["fail_count"] = 0
        logger.info(f"[provider] '{name}' 冷却期已过,重新启用")
        return True
    return False


def get_active_provider() -> dict:
    """返回当前可用的主 provider。

    选择规则（2026-06-28 重写）：
      1. 读 cfg.ACTIVE_LLM_PROVIDER（运行时参数，dashboard 可改）
      2. 如果指定的是 'minimax' 且配置可用 → 用 minimax
      3. 如果指定的是 'deepseek' 且配置可用 → 用 deepseek
      4. 否则 → 抛 RuntimeError（不允许静默回退）

    路由说明（route_payload）：
      - 普通对话/工具调用/流式：走 active_provider（用户主选,默认 deepseek）
      - 消息含图片(image_url)：自动路由到 minimax（多模态强）
      - 两个 Provider 独立,无自动 fallback
    """
    active = getattr(cfg, "ACTIVE_LLM_PROVIDER", "deepseek")
    if active in ("minimax", "deepseek") and _is_provider_available(active):
        return get_provider(active)
    raise RuntimeError(
        f"主选 LLM provider '{active}' 不可用（API key 缺失/格式错/服务异常）."
        f"请到 Dashboard 切换主选,或在 .env 修对应 key."
        f"两个 Provider 独立,不自动回退."
    )


# ── 路由（2026-06-28 重构 LLM 调用策略）──

def get_vision_provider() -> dict | None:
    """获取识图专用 provider：minimax M3（多模态强）

    返回 None 表示 minimax 不可用,调用方应回退到无图模式（丢图）
    """
    if not _is_provider_available("minimax"):
        return None
    return get_provider("minimax")


def has_image_content(payload: dict) -> bool:
    """检测 payload 里 messages 是否含 image_url content（用户发了图）"""
    messages = payload.get("messages") or []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for seg in content:
                if isinstance(seg, dict) and seg.get("type") == "image_url":
                    return True
    return False


def route_payload(payload: dict, force: str | None = None) -> tuple[dict, str]:
    """根据 payload 决定走哪个 provider（2026-06-28 改）

    新策略（按用户决定）：
    - **所有对话/工具/流式都走 active_provider（默认 deepseek）**
    - 含图片的对话 → 先调用 minimax vision 转文本描述 → 替换 image_url → 再走 deepseek
    - minimax 永远只做"看图说话",不参与对话

    Returns:
        (payload_with_correct_model, provider_name_used)
        永远返回 active_provider，minimax 只用于图片描述预处理
    """
    return payload, get_active_provider()["name"]


def get_provider_for_route(routed_name: str) -> dict:
    """根据路由名返回 provider dict"""
    if routed_name == "minimax":
        provider = get_vision_provider()
        if provider is None:
            raise RuntimeError("minimax 不可用")
        return provider
    # 默认 active
    return get_active_provider()


# ── 视觉描述（minimax 专用，2026-06-28）──
VISION_DESCRIPTION_PROMPT = """你是图片描述助手。只描述图片的客观可见内容,不要做任何角色扮演或闲聊。

要求:
1. 客观描述图中人物/物体/场景,3-5 句话
2. 重点描述与用户提问相关的内容（用户文字会附在下方）
3. 不要输出任何观点、情感、角色扮演内容
4. 不要输出表情/颜文字
5. 用中文描述"""


async def describe_image_via_minimax(image_url: str, user_text: str = "") -> str | None:
    """调 minimax 看图 → 返回纯文本描述

    用途:用户发图时,把图片转成文字,然后走 deepseek 处理对话
    这样 minimax 只接触图片,不接触对话上下文,不会被 422 sensitive 卡

    Args:
        image_url: QQ 图片 URL (http) 或 base64 data url
        user_text: 用户附带的文字（帮助 minimax 知道重点描述什么）

    Returns:
        描述文字 / None（失败）
    """
    vision = get_vision_provider()
    if vision is None:
        cfg.chat_logger.warning("[VISION] minimax 不可用,跳过图片描述")
        return None

    # 构造 minimax vision 调用 payload
    payload = {
        "model": vision["model"],
        "messages": [
            {"role": "system", "content": VISION_DESCRIPTION_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"用户问题: {user_text or '(无)'}\n\n请描述下图:"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]},
        ],
        "max_tokens": 500,
        "temperature": 0.3,
    }

    # 直接调 api_request，provider 显式传 minimax（不走 route_payload）
    from . import api_client
    data = await api_client.api_request(payload, provider=vision)
    if data is None:
        return None
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        return None


async def describe_images_in_payload(payload: dict) -> dict:
    """检测 payload 里所有 image_url,逐个转文字描述,替换进 messages

    用法:chat_handler 在发给 deepseek 之前调一下,含图的消息就先把图转文字

    Args:
        payload: 完整 LLM 调用 payload (含 messages)

    Returns:
        处理后的 payload（image_url 段被替换为 [图片描述: ...] 文字）
    """
    import asyncio
    messages = payload.get("messages") or []
    if not messages:
        return payload

    # 收集所有需要描述的 (msg_idx, seg_idx, url, user_text_hint)
    tasks: list[tuple[int, int, str, str]] = []
    for msg_idx, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        # 收集 user 文字 hint（同一消息里的 text 段）
        user_text_hint = ""
        for seg in content:
            if isinstance(seg, dict) and seg.get("type") == "text":
                user_text_hint += seg.get("text", "")
        for seg_idx, seg in enumerate(content):
            if isinstance(seg, dict) and seg.get("type") == "image_url":
                url_obj = seg.get("image_url", {})
                url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
                if isinstance(url, str) and url:
                    tasks.append((msg_idx, seg_idx, url, user_text_hint))

    if not tasks:
        return payload

    # 并发描述所有图片
    cfg.chat_logger.info(f"[VISION] 检测到 {len(tasks)} 张图片,准备用 minimax 转文字描述")
    coros = [describe_image_via_minimax(url, hint) for _, _, url, hint in tasks]
    descriptions = await asyncio.gather(*coros, return_exceptions=True)

    # 替换回 messages（按收集顺序）
    new_messages = []
    for msg_idx, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            new_messages.append(msg)
            continue
        new_content = []
        task_iter = iter([(t_idx, t) for t_idx, t in enumerate(tasks) if t[0] == msg_idx])
        next_task = next(task_iter, None)
        for seg_idx, seg in enumerate(content):
            if next_task and next_task[1][1] == seg_idx:
                # 这是要替换的 image_url 段
                desc = descriptions[next_task[0]]
                if isinstance(desc, Exception) or desc is None:
                    new_content.append({"type": "text", "text": "[图片: minimax 描述失败]"})
                    cfg.chat_logger.warning(f"[VISION] 图片描述失败: {desc}")
                else:
                    new_content.append({"type": "text", "text": f"[图片描述: {desc}]"})
                    cfg.chat_logger.info(f"[VISION] 图片描述完成 (len={len(desc)})")
                next_task = next(task_iter, None)
            else:
                new_content.append(seg)
        new_messages.append({**msg, "content": new_content})

    return {**payload, "messages": new_messages}


def get_fast_provider() -> dict | None:
    """获取高速版 provider（用于探测/低延迟场景）。

    仅当 minimax 可用且 cfg.MINIMAX_USE_HIGH_SPEED=True 时返回；
    否则返回 None,调用方应继续走普通 provider。
    """
    if not (cfg.MINIMAX_USE_HIGH_SPEED and _is_provider_available("minimax")):
        return None
    return {
        "name": "minimax_fast",
        "base_url": cfg.MINIMAX_BASE_URL,
        "api_key": cfg.MINIMAX_API_KEY,
        "model": cfg.MINIMAX_FAST_MODEL,
    }


def mark_provider_failed(name: str):
    """标记 provider 失败,连续失败则进入冷却"""
    h = _provider_health.get(name)
    if not h:
        return
    h["fail_count"] += 1
    h["last_fail"] = time.time()
    if h["fail_count"] >= 2:
        h["healthy"] = False
        logger.warning(f"Provider '{name}' 连续失败 {h['fail_count']} 次,进入 {_PROVIDER_COOLDOWN}s 冷却")


def mark_provider_healthy(name: str):
    """标记 provider 恢复健康"""
    h = _provider_health.get(name)
    if h:
        h["healthy"] = True
        h["fail_count"] = 0


def get_fallback_name(current_name: str) -> str | None:
    """获取指定 provider 的回退目标;若已无下一跳则返回 None"""
    return FALLBACK_CHAIN.get(current_name)


def adapt_payload_for_provider(payload: dict, provider_name: str) -> dict:
    """根据 provider 调整 payload 参数（剥离非通用字段）

    minimax (OpenAI 兼容但不完全支持 OpenAI 全字段)：
      - 不支持 reasoning_effort、frequency_penalty、presence_penalty
      - tool_choice 仅 auto/none/required
    deepseek 兼容 OpenAI 全字段，全部保留。
    """
    p = {**payload}
    if provider_name == "minimax":
        # minimax 不支持的字段全部剥掉
        p.pop("reasoning_effort", None)
        p.pop("frequency_penalty", None)
        p.pop("presence_penalty", None)
        # tool_choice 处理:仅 auto/none/required 是 OpenAI 规范值
        if "tool_choice" in p and p["tool_choice"] not in ("auto", "none", "required"):
            p.pop("tool_choice", None)
    # 2026-06-28 fix deepseek 400 image_url:
    # 历史消息里 user 偶尔带图片,content 是 multimodal 数组 [{"type":"text"...},{"type":"image_url"...}]
    # DeepSeek 兼容 OpenAI 多模态,但对老 content 格式要求更严
    # 处理:把 image_url 段的 url 提到 content.image_url 字段(deepseek 期望的 schema)
    if provider_name == "deepseek" and "messages" in p:
        for msg in p["messages"]:
            content = msg.get("content")
            if isinstance(content, list):
                # 检查是否有多模态段
                image_url = None
                text_parts = []
                for seg in content:
                    if not isinstance(seg, dict):
                        text_parts.append(str(seg))
                        continue
                    seg_type = seg.get("type")
                    if seg_type == "image_url":
                        # 提取 url,只保留第一张（deepseek 单图限制）
                        url_obj = seg.get("image_url", {})
                        if isinstance(url_obj, dict):
                            image_url = url_obj.get("url")
                        elif isinstance(url_obj, str):
                            image_url = url_obj
                        # 其它 image_url 段丢弃
                    elif seg_type == "text":
                        text_parts.append(seg.get("text", ""))
                    else:
                        text_parts.append(str(seg))
                # 重组 content
                if image_url:
                    # OpenAI 多模态格式:content 是 [{type:text},{type:image_url,...}]
                    # deepseek 期望:content 是字符串 OR content.image_url(单图)
                    # 兼容做法:把 image 留在数组里,只确保字段是 deepseek 期望的
                    new_content = [{"type": "text", "text": "".join(text_parts)}]
                    if image_url:
                        new_content.append({"type": "image_url", "image_url": {"url": image_url}})
                    msg["content"] = new_content
                else:
                    msg["content"] = "".join(text_parts)
    return p