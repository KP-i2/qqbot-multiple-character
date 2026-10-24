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
    "siliconflow": {"healthy": True, "fail_count": 0, "last_fail": 0},  # 2026-07-23 新增: 视觉读图
    "deepseek-vision": {"healthy": True, "fail_count": 0, "last_fail": 0},  # 2026-08-30 新增: 官方多模态读图
}
_PROVIDER_COOLDOWN = 120  # 标记不健康后的冷却秒数（仅 dashboard 展示用）

# 旧版回退链保留但不再被使用（防止 dashboard/旧调用残留报错）
FALLBACK_CHAIN: dict[str, str | None] = {
    "minimax": None,
    "deepseek": None,
    "siliconflow": None,  # 2026-07-23 新增
    "deepseek-vision": None,  # 2026-08-30 新增
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
    if name == "siliconflow":  # 2026-07-23 新增
        return {
            "name": "siliconflow",
            "base_url": cfg.SILICONFLOW_BASE_URL,
            "api_key": cfg.SILICONFLOW_API_KEY,
            "model": cfg.SILICONFLOW_MODEL,
        }
    if name == "deepseek-vision":  # 2026-08-30 新增: 复用 deepseek 的 key/base_url
        return {
            "name": "deepseek-vision",
            "base_url": cfg.DEEPSEEK_BASE_URL,
            "api_key": cfg.DEEPSEEK_API_KEY,
            "model": cfg.DEEPSEEK_VISION_MODEL,
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
    if name == "siliconflow":  # 2026-07-23 新增
        return bool(
            cfg.SILICONFLOW_ENABLED
            and cfg.SILICONFLOW_API_KEY
            and cfg.SILICONFLOW_BASE_URL
            and not cfg.SILICONFLOW_API_KEY.startswith("sk-your")
        )
    if name == "deepseek-vision":  # 2026-08-30 新增
        # 复用 deepseek 的 key/base_url，只要求 key 有效且开关打开
        return bool(
            cfg.DEEPSEEK_VISION_ENABLED
            and cfg.DEEPSEEK_API_KEY
            and cfg.DEEPSEEK_BASE_URL
            and not cfg.DEEPSEEK_API_KEY.startswith("sk-your")
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
      - 消息含图片(image_url)：先由 get_vision_provider() 转文字描述,再走 active_provider
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
    """获取识图专用 provider（优先级链）

    2026-08-30 改: 首选 DeepSeek 官方多模态 deepseek-v4-flash-vision-exp
    （跟主对话同平台同 key，少一个要维护的第三方凭证）。
    优先级: deepseek-vision > siliconflow(Qwen3.5-9B) > minimax(占位)。
    每一家都由 .env 开关独立控制，关掉开关即自动降级到下一家。
    返回 None 表示 vision 全部不可用，调用方应回退到无图模式(丢图)。
    """
    if _is_provider_available("deepseek-vision"):
        return get_provider("deepseek-vision")
    if _is_provider_available("siliconflow"):
        return get_provider("siliconflow")
    if _is_provider_available("minimax"):
        return get_provider("minimax")
    return None


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
            raise RuntimeError("vision provider 不可用")
        return provider
    # 默认 active
    return get_active_provider()


# ── 视觉描述（vision provider 专用，2026-06-28；2026-08-30 扩展为三家优先级链）──
# 2026-07-23 改: OCR 文字以自然语言嵌入描述, 不带 "OCR" 字眼或元标签
# (教训: 下游 LLM 看到 "OCR/图片描述" 标签会复述成"从OCR结果看", 角色破功)
VISION_DESCRIPTION_PROMPT = """你是图片描述助手。看到图就像平常人看到图一样说出来,不做任何角色扮演或闲聊。

要求:
1. 图中所有可见文字完整抄出,自然嵌入描述里(例如"画面顶部写着'xxx',中间标签是'yyy'"),不要单独标"OCR"模块或带"OCR"字眼
2. 客观描述人物外貌/物体/场景/构图
3. 重点描述与用户提问相关的内容（用户文字会附在下方）
4. 不要输出观点、情感、角色扮演、表情
5. 用中文描述"""


def wrap_image_description(desc: str) -> str:
    """把 vision 描述包装成注入 prompt 的自然语言块（唯一约定）。

    2026-08-30 统一收敛：此前同一件事有两套写法 —
      - chat_handler 用 `[图片描述: ...]` 标签（实际生效的那条路径）
      - describe_images_in_payload 用 `【图片内容】` + "不要复述" 指令（防御分支）
    2026-07-23 已经判定标签式写法不好，但那次只改到了防御分支，
    真正跑的主路径一直没改。现在两条路径共用本函数，措辞只需在一处调。

    Args:
        desc: vision provider 返回的图片描述（可含多张图，用换行分隔）

    Returns:
        可直接追加进 user message 的文本块。
    """
    return (
        "【图片内容】\n"
        f"{desc}\n\n"
        "请理解以上图片内容并直接回答用户，不要复述你看到图片的过程。"
    )


async def describe_image(image_url: str, user_text: str = "") -> str | None:
    """调 vision provider 看图 → 返回纯文本描述

    用途:用户发图时,把图片转成文字,然后走 active provider 处理对话。
    vision provider 只接触图片 + 用户文字提示,不接触对话上下文 / persona / thinking,
    这样它自带的敏感词过滤不会被上下文里的某句话意外触发。

    2026-08-30 改名 + 改造: 原 describe_image_via_minimax。
    vision provider 现有三家(deepseek-vision / siliconflow / minimax)，
    其中 DeepSeek vision 对远程 URL 的服务端抓取**不可靠**（见下），
    命中 deepseek-vision 时一律先下载图片转 base64 data URI。

    Args:
        image_url: QQ 图片 URL (http) 或 base64 data url
        user_text: 用户附带的文字（帮助 vision 模型知道重点描述什么）

    Returns:
        描述文字 / None（失败）
    """
    vision = get_vision_provider()
    if vision is None:
        cfg.chat_logger.warning("[VISION] vision 不可用,跳过图片描述")
        return None

    # 2026-08-30: DeepSeek vision 的服务端抓图不稳定 —— 实测同一轮测试里
    # https://www.python.org/... 抓取成功，而 upload.wikimedia.org 直接 400
    # "Failed to download image from ..."。QQ CDN 是临时签名链接，更不可赌。
    # 所以走 deepseek-vision 时统一转 base64 data URI（实测 1.7MB base64 也能正常识别）。
    # siliconflow / minimax 保留直链策略：它们各自的 QQ CDN 黑名单已在 image_handler 处理。
    if vision["name"] == "deepseek-vision" and image_url.startswith(("http://", "https://")):
        from . import image_handler
        data_uri = await image_handler.download_image_to_data_uri(image_url)
        if data_uri:
            cfg.chat_logger.info(f"[VISION] deepseek-vision 远程图转 base64 成功 (len={len(data_uri)})")
            image_url = data_uri
        else:
            cfg.chat_logger.warning(
                f"[VISION] deepseek-vision 图片下载失败,退回直链(大概率 400): {image_url[:80]}"
            )

    # 构造 vision 调用 payload
    payload = {
        "model": vision["model"],
        "messages": [
            {"role": "system", "content": VISION_DESCRIPTION_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"用户问题: {user_text or '(无)'}\n\n请描述下图:"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]},
        ],
        "max_tokens": 3000,  # 2026-07-23: 500 → 1500 → 3000, 避免文字多的图被截断 (实测 1527 字就触顶)
        "temperature": 0.3,
    }
    # 2026-07-23: 硅基流动 Qwen3.5-9B 默认 thinking 模式开启, 识图时需显式关掉
    # 实测: enable_thinking=False 省 2/3 token 且输出一样 (409 vs 695 chars for 同图)
    if vision["name"] == "siliconflow":
        payload["enable_thinking"] = False
    # 2026-08-30: DeepSeek vision (deepseek-v4-flash-vision-exp) 同样默认开思考，
    # 且思考会把 max_tokens 吃光导致 content 返回空字符串（小 max_tokens 时必现）。
    # 必须用 thinking 字段关（enable_thinking 对它无效，实测传了照样思考）。
    # 同图实测: disabled → 253 tokens / 423 字；enabled → 725 tokens / 322 字。
    elif vision["name"] == "deepseek-vision":
        payload["thinking"] = {"type": "disabled"}

    # 直接调 api_request，provider 显式传 vision（不走 route_payload）
    from . import api_client
    data = await api_client.api_request(payload, provider=vision)
    if data is None:
        return None
    try:
        content = (data["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError):
        return None
    if not content:
        # 兜底: 思考没关干净时 content 会是空串，但 reasoning_content 里其实有内容。
        # 宁可把思考当描述用，也别让用户这条消息彻底丢掉图片信息。
        try:
            content = (data["choices"][0]["message"].get("reasoning_content") or "").strip()
        except (KeyError, IndexError):
            content = ""
        if content:
            cfg.chat_logger.warning("[VISION] content 为空,回退使用 reasoning_content")
    return content or None


async def describe_images_in_payload(payload: dict) -> dict:
    """检测 payload 里所有 image_url,逐个转文字描述,替换进 messages

    用法:chat_handler 在发给 deepseek 之前调一下,含图的消息就先把图转文字

    Args:
        payload: 完整 LLM 调用 payload (含 messages)

    Returns:
        处理后的 payload（image_url 段被替换为 wrap_image_description() 生成的文字块）
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
    vision_name = (get_vision_provider() or {}).get("name", "unknown")
    cfg.chat_logger.info(f"[VISION] 检测到 {len(tasks)} 张图片,准备用 vision provider ({vision_name}) 转文字描述")
    coros = [describe_image(url, hint) for _, _, url, hint in tasks]
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
                    new_content.append({"type": "text", "text": "[图片: vision 描述失败]"})
                    cfg.chat_logger.warning(f"[VISION] 图片描述失败: {desc}")
                else:
                    # 2026-07-23: 不用 [图片描述: ...] 标签
                    # 2026-08-30: 包装措辞收敛到 wrap_image_description，与 chat_handler 共用
                    new_content.append({"type": "text", "text": wrap_image_description(desc)})
                    cfg.chat_logger.info(f"[VISION] 图片描述完成 (len={len(desc)})")
                next_task = next(task_iter, None)
            else:
                new_content.append(seg)
        new_messages.append({**msg, "content": new_content})

    return {**payload, "messages": new_messages}


def get_fast_provider() -> dict | None:
    """高速 provider 接口（2026-07-23 已废弃 — minimax 高速模型字段被删）

    保留为 stub 以防 dashboard/旧调用残留报错, 永远返回 None。
    探测 / 低延迟场景现在直接走 get_active_provider()。
    """
    return None


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
      - 不支持 reasoning_effort、frequency_penalty、presence_penalty、thinking
      - tool_choice 仅 auto/none/required
    deepseek / deepseek-vision 兼容 OpenAI 全字段，全部保留。
    siliconflow 支持 enable_thinking（专有字段）。
    """
    p = {**payload}
    if provider_name == "minimax":
        # minimax 不支持的字段全部剥掉
        p.pop("reasoning_effort", None)
        p.pop("frequency_penalty", None)
        p.pop("presence_penalty", None)
        # 2026-08-30: thinking 是 deepseek 系字段，minimax 不认。
        # build_sampling_params 现在无条件带 thinking，主选切 minimax 时必须剥掉，
        # 否则可能被上游按 unknown field 拒掉。
        p.pop("thinking", None)
        p.pop("enable_thinking", None)
        # tool_choice 处理:仅 auto/none/required 是 OpenAI 规范值
        if "tool_choice" in p and p["tool_choice"] not in ("auto", "none", "required"):
            p.pop("tool_choice", None)
    elif provider_name != "siliconflow":
        # enable_thinking 是硅基流动专有字段，deepseek 系不认
        p.pop("enable_thinking", None)
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