"""
LLM 调用逻辑：探测工具需求、Function Calling 循环、流式/非流式
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

from nonebot import logger

from . import config as cfg
from . import provider as prov
from . import api_client
from . import search
from . import turn_recorder
from .dsml_cleaner import clean_llm_reply, strip_dsml_markup


def build_sampling_params(
    provider_name: str,
    tools_available: bool = False,
    force_tools: bool = False,
) -> dict:
    """统一构造 LLM 采样参数（probe / 流式 / 非流式三处共用）。

    2026-08-30 修复：此前流式分支（chat_handler）把 temperature / top_p /
    max_tokens / reasoning_effort 全部硬编码，导致 Dashboard「参数设置」里的
    llm_temperature / llm_top_p / llm_max_tokens 对流式输出完全无效
    （只有非流式跟随），且流式 max_tokens 被砍半成 8192。
    现在三条路径共用本函数，Dashboard 改参后流式与非流式行为一致。

    Args:
        provider_name: active provider 名（"deepseek" / "minimax" ...）
        tools_available: 本轮是否带工具（决定 minimax 用哪档思考强度）
        force_tools: 是否强制首轮调工具（决定 deepseek 的 thinking 开关）

    Returns:
        可直接展开进 payload 的采样参数字典。
    """
    # reasoning_effort：minimax 走 Dashboard 配置；deepseek 系当前只接受 high
    # （Dashboard 的 llm_reasoning_effort_* 实际仅对 minimax 生效）
    if provider_name == "minimax":
        reasoning_effort = (
            cfg.LLM_REASONING_EFFORT_WITH_TOOLS if tools_available
            else cfg.LLM_REASONING_EFFORT_NO_TOOLS
        )
    else:
        reasoning_effort = "high"

    return {
        "reasoning_effort": reasoning_effort,
        "temperature": cfg.LLM_TEMPERATURE,
        "top_p": cfg.LLM_TOP_P,
        "max_tokens": cfg.LLM_MAX_TOKENS,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.2,
        # deepseek 系支持 thinking 开关（实测 enabled/disabled 均被接受）。
        # minimax 不支持该字段，adapt_payload_for_provider 会自动剥掉。
        # force_tools 时要关思考：deepseek 在 thinking mode 下会无视 tool_choice。
        "thinking": {"type": "disabled"} if force_tools else {"type": "enabled"},
    }


async def probe_tool_usage(
    system_prompt: str,
    messages: list[dict],
    skill_name: str = "",
    current_user_content: Optional[object] = None,
) -> bool:
    """快速探测模型是否需要调用工具。
    返回 True 表示模型想调用工具，False 表示直接回复。

    current_user_content: 可选,替换最后一条 user 消息的 content(支持 multimodal)。
    """
    use_tools = cfg.WEB_SEARCH_ENABLED and cfg.SEARCH_AVAILABLE
    if not use_tools and not skill_name:
        return False

    # 多模态替换最后一条 user content
    if current_user_content is not None and messages:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                messages[i] = {**messages[i], "content": current_user_content}
                break

    active_tools = []
    if use_tools:
        active_tools.append(search.SEARCH_TOOLS[0])   # web_search
    if skill_name:
        active_tools.append(search.SEARCH_TOOLS[1])   # search_corpus

    now = datetime.now()
    date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
    probe_system = system_prompt + date_hint

    # 探测调用:跟随主选 provider(避免每次白打 deepseek 400)
    # 2026-06-28 改:之前固定 fast or deepseek,主选 minimax 时每次探测都打 deepseek
    # 又因为历史里有 image_url content,deepseek 直接 400,造成 PROBE 显示 provider=deepseek 但
    # 实际主调用走 minimax,日志混乱也浪费调用
    # 2026-07-23: 删 get_fast_provider 引用 — minimax 高速模型已废弃, 探测统一走 active
    active = prov.get_active_provider()
    probe_provider = active
    # 采样参数走公共构造函数；探测只需要"要不要工具"这一个判断，故覆盖 temperature / max_tokens。
    #
    # 2026-09-12 修复（重要）：probe 从写出来那天起就是坏的 —— chat.log 里
    # wants_tools=True 出现 0 次 / False 出现 735 次，不是模型不想搜，而是：
    #   1) max_tokens=10 太小：工具调用的 JSON（含 query 参数）本身就要 40+ token，
    #      额度不够时返回 finish=length 且 tool_calls 为空 → 永远判成"不需要工具"。
    #      实测：关思考下 max_tokens 需 ≥48 才能干净拿到 finish=tool_calls。
    #   2) thinking 默认开启会先吃掉额度（实测 10 token 全被 reasoning 占用）。
    #      探测是路由决策，不需要深度推理 —— 关掉它省一半 token 且更快。
    probe_sampling = build_sampling_params(probe_provider["name"], tools_available=True)
    # 2026-09-13 二次修复：96 仍然贴边。实测（同一问题跑 10 次）completion_tokens 在
    # 86~96 之间浮动，偶尔正好撞上 96 → finish_reason=length，此时被截断的 tool_calls
    # 可能被 provider 丢掉 → 判成"不需要工具" → 模型只能凭记忆编。
    # 线上案例：问"你们这周在哪演出"，probe=False，回复编了"周六在育音堂"。
    # 现在提到 256（探测本身很便宜），并在下面加截断兜底。
    probe_sampling.update({"temperature": 0.3, "max_tokens": 256})
    probe_sampling["thinking"] = {"type": "disabled"}
    probe_payload = {
        "model": probe_provider["model"],
        **probe_sampling,
        "stream": False,
        "messages": [{"role": "system", "content": probe_system}] + messages,
        "tools": active_tools,
        "tool_choice": "auto",
    }

    try:
        await asyncio.wait_for(api_client.API_SEMAPHORE.acquire(), timeout=10)
    except asyncio.TimeoutError:
        cfg.chat_logger.warning("[PROBE] API 并发已满，跳过探测")
        return True

    # 2026-06-28 fix:探测前先调 describe_images_in_payload 把图片转文字
    # 否则含图消息探测会发 multimodal 给 deepseek,触发 400 image_url unknown variant
    if prov.has_image_content(probe_payload):
        cfg.chat_logger.info("[VISION] 探测阶段检测到图片,先用 minimax 转文字")
        probe_payload = await prov.describe_images_in_payload(probe_payload)

    try:
        data = await api_client.api_request(probe_payload, probe_provider)
    finally:
        api_client.API_SEMAPHORE.release()

    if data is None:
        return True

    choice = data["choices"][0]
    tool_calls = choice["message"].get("tool_calls")
    truncated = choice.get("finish_reason") == "length"
    # 截断且没拿到 tool_calls = "没判断出来"，保守当作需要工具：
    # 多搜一次的代价远小于"该搜没搜、凭记忆编一个行程"。
    wants_tools = bool(tool_calls) or truncated
    if truncated and not tool_calls:
        cfg.chat_logger.warning("[PROBE] 探测被 max_tokens 截断且无 tool_calls → 保守判为需要工具")
    cfg.chat_logger.info(f"[PROBE] wants_tools={wants_tools} (provider={probe_provider['name']})")
    return wants_tools


async def call_deepseek(
    system_prompt: str,
    messages: list[dict],
    skill_name: str = "",
    current_user_content: Optional[object] = None,
    force_tools: bool = False,
    disable_tools: bool = False,
) -> Optional[str]:
    """调用 DeepSeek API，支持 Function Calling 联网搜索 + 语料库搜索

    参数:
      system_prompt: 系统提示
      messages: 多轮对话历史(每条 role+content)
      skill_name: 当前角色名(用于语料库搜索)
      current_user_content: 可选,替换最后一条 user 消息的 content,
        支持字符串或 OpenAI 多模态 content 数组(用于带图消息)。
      force_tools: 2026-07-22 新增 — 强制第一轮 tool_choice="required",
        逼 LLM 必须调工具 (而不是 auto). 解决"minimax 凭知识回答不知道的人/事"
        的问题 — 7/21 10:29 念张师 case minimax 决定不调搜索, 直接回"没听过".
      disable_tools: 2026-07-23 新增 — 完全禁用 tool_call 路径(不发 tools 字段).
        适用: chat_handler 已经自己调了 web_search 拿到结果塞进 user message,
        此时再给 LLM 加 tools 会触发 deepseek v4 思考模式瞎答.
        强制 LLM 只看 user message (含搜索结果) 总结.
    """
    try:
        await asyncio.wait_for(api_client.API_SEMAPHORE.acquire(), timeout=30)
    except asyncio.TimeoutError:
        cfg.chat_logger.warning("[LLM] API 并发已满，排队超时")
        return "⚠️ 调用排队超时（API 并发已满），请稍后重试。"

    try:
        return await asyncio.wait_for(
            _call_deepseek_inner(
                system_prompt, messages, skill_name, current_user_content,
                force_tools=force_tools, disable_tools=disable_tools,
            ),
            timeout=120,
        )
    except asyncio.TimeoutError:
        cfg.chat_logger.warning("[LLM] 整体调用超时 (180s)，强制返回")
        return "⚠️ LLM 调用超时（180s），请稍后重试或检查网络。"
    finally:
        api_client.API_SEMAPHORE.release()


# 2026-09-10: 思考过程写进 chat.log 的长度上限（reasoning_content 可能几千字）
REASONING_LOG_CHARS = 1200


def _log_reasoning(text: str, *, round_num: int = 0, source: str = "reasoning_content") -> None:
    """把模型思考过程落进 chat.log（截断+单行化），便于事后 grep 复盘

    背景：此前 reasoning_content 只在「带工具」分支被提取、且只喂给 turn_recorder，
    而 recorder 因没人 begin_turn 实际是停摆的 → 思考过程等于被丢弃。
    """
    if not text:
        return
    snippet = " ⏎ ".join(text.strip().splitlines())
    if len(snippet) > REASONING_LOG_CHARS:
        snippet = snippet[:REASONING_LOG_CHARS] + "…[截断]"
    cfg.chat_logger.info(f"[REASONING] round={round_num} src={source} len={len(text)} | {snippet}")


async def _call_deepseek_inner(
    system_prompt: str,
    messages: list[dict],
    skill_name: str = "",
    current_user_content: Optional[object] = None,
    force_tools: bool = False,
    disable_tools: bool = False,
) -> Optional[str]:
    """实际的 LLM 调用逻辑。

    Provider 选择规则（2026-06-28 改）：
    - 走 `prov.get_active_provider()`，由运行时参数 `active_llm_provider` 控制
    - 默认 deepseek（用户 2026-06-28 起的主选）
    - vision provider（deepseek-vision / siliconflow / minimax）只在图片描述时使用
      （prov.describe_image），不参与对话
    - 两个 Provider 独立，**不互相回退**。失败直接报错。

    current_user_content: 若提供,则替换最后一条 user 消息的 content
    (支持字符串或 OpenAI 多模态 content 列表,用于带图消息)。

    2026-06-28 fix:含图消息先调 prov.describe_image 把图转文字,
    再走 active provider (deepseek)。避免 deepseek 不认识 image_url 报 400。
    """
    # 若有当前用户 multimodal content,替换最后一条 user 消息的 content
    if current_user_content is not None and messages:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                messages[i] = {**messages[i], "content": current_user_content}
                break

    # 2026-06-28:如果替换后内容含 image_url,先调 minimax 转文字描述
    # 这样 minimax 只看图不接触对话上下文,deepseek 不会 400
    if current_user_content is not None and prov.has_image_content({"messages": messages}):
        from . import provider as _prov_for_desc
        payload_with_imgs = {"messages": messages}
        processed = await _prov_for_desc.describe_images_in_payload(payload_with_imgs)
        messages = processed["messages"]
        cfg.chat_logger.info(f"[VISION] 非流式分支图片已转文字 (messages={len(messages)})")

    # 2026-07-23: disable_tools 强制跳过 tool_call 路径(不发 tools 字段)
    # chat_handler 预搜索场景下, user message 已含搜索结果, 不应再让 LLM 调工具
    if disable_tools:
        use_tools = False
        skill_name = ""  # 不调语料库
    else:
        use_tools = cfg.WEB_SEARCH_ENABLED and cfg.SEARCH_AVAILABLE
    tools_available = use_tools or bool(skill_name)

    # 主选 provider（由运行时参数 active_llm_provider 控制，dashboard 可改）
    provider = prov.get_active_provider()
    active_model = provider["model"]

    # 采样参数统一构造（2026-08-30: probe / 流式 / 非流式三处共用同一份逻辑）
    _sampling = build_sampling_params(provider["name"], tools_available, force_tools)
    reasoning_effort = _sampling["reasoning_effort"]
    cfg.chat_logger.info(f"[LLM] 调用 {provider['name']} | model={active_model} | reasoning={reasoning_effort} | 联网={'开' if use_tools else '关'} | 语料库={'开' if skill_name else '关'} | msgs={len(messages)}")

    if use_tools:
        now = datetime.now()
        date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
        system_prompt = system_prompt + date_hint

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    base_payload = {
        "model": active_model,
        # 采样参数全部来自 build_sampling_params（Dashboard 可热调）。
        # 其中 reasoning_effort / frequency_penalty / presence_penalty / thinking
        # 由 adapt_payload_for_provider 在 minimax 上下文中自动剥掉。
        **_sampling,
        "stream": False,
    }

    if not tools_available:
        # 2026-09-10 修复：这条分支此前「只取 content 直接 return」——
        # 既丢掉了 reasoning_content（思考过程），也完全没记 turn step，
        # 导致无工具调用时 dashboard 面板空白、日志里查不到推理。
        payload = {**base_payload, "messages": full_messages}
        turn_recorder.add_step({
            "kind": "llm_call",
            "title": f"非流式调用 {provider['name']} ({active_model}, tools=off)",
            "data": {
                "provider": provider["name"],
                "model": active_model,
                "messages_count": len(full_messages),
                "tools_count": 0,
                "reasoning_effort": reasoning_effort,
            },
        })
        data = await api_client.api_request(payload, provider)
        if data is None:
            turn_recorder.add_step({"kind": "error", "title": "LLM 请求失败", "data": {}})
            return None
        _msg = data["choices"][0]["message"]
        _reasoning = _msg.get("reasoning_content") or _msg.get("reasoning") or ""
        if _reasoning:
            _log_reasoning(_reasoning)
            turn_recorder.add_step({
                "kind": "thinking",
                "title": "模型思考过程(round=0, reasoning_content)",
                "data": {"text": _reasoning, "round": 0, "source": "reasoning_content"},
            })
        return clean_llm_reply((_msg.get("content") or "").strip())

    active_tools = []
    if use_tools:
        active_tools.append(search.SEARCH_TOOLS[0])
    if skill_name:
        active_tools.append(search.SEARCH_TOOLS[1])

    # ── Function Calling 循环 ──
    turn_recorder.add_step({
        "kind": "llm_call",
        "title": f"非流式调用 {provider['name']} ({active_model}, tools={'on' if active_tools else 'off'})",
        "data": {
            "provider": provider["name"],
            "model": active_model,
            "messages_count": len(full_messages),
            "tools_count": len(active_tools),
            "reasoning_effort": reasoning_effort,
        },
    })

    for round_num in range(search._MAX_TOOL_ROUNDS + 1):
        # 2026-07-22: force_tools=True 时第一轮强制 LLM 调工具, 后续轮次回到 auto 让 LLM 决定
        # 解决"minimax 凭知识答非所问"的问题 (7/21 念张师 case)
        # 2026-07-23: 升级为 tool_choice={"type":"function","function":{"name":...}} 强指定函数.
        #   deepseek v4 在 thinking mode 下 "required" 强制失效, 仍会凭知识瞎答
        #   (实测: "claude fable5 新闻" → bot 返回"呜哦脑袋当场当机"而非调工具)
        #   function-specific 是 OpenAI 协议里最强的强制, 大多数 provider 都遵守
        if force_tools and round_num == 0 and active_tools:
            _first_tool = active_tools[0].get("function", {}).get("name", "")
            if _first_tool:
                tool_choice_value = {"type": "function", "function": {"name": _first_tool}}
            else:
                tool_choice_value = "required"
        else:
            tool_choice_value = "auto"
        payload = {
            **base_payload,
            "messages": full_messages,
            "tools": active_tools,
            "tool_choice": tool_choice_value,
        }

        data = await api_client.api_request(payload, provider)
        if data is None:
            turn_recorder.add_step({
                "kind": "error",
                "title": f"LLM API 调用失败 (round={round_num})",
                "data": {"round": round_num},
            })
            return None

        choice = data["choices"][0]
        message = choice["message"]

        # 提取 thinking(M3 会把 reasoning_content 放进 message.reasoning_content 字段,
        # 也可能塞到 content 前缀的 <think> 块里)
        _reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        if _reasoning:
            _log_reasoning(_reasoning, round_num=round_num)
            turn_recorder.add_step({
                "kind": "thinking",
                "title": f"模型思考过程(round={round_num}, reasoning_content)",
                "data": {"text": _reasoning, "round": round_num, "source": "reasoning_content"},
            })

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            raw_content = message.get("content", "").strip()
            # 即使 model 说不用工具, content 里也可能有 <think> 块,先提取
            _think_text, raw_content_no_think = _extract_think(raw_content)
            if _think_text:
                _log_reasoning(_think_text, round_num=round_num, source="content<think>")
                turn_recorder.add_step({
                    "kind": "thinking",
                    "title": f"模型思考过程(round={round_num}, content<think>)",
                    "data": {"text": _think_text, "round": round_num, "source": "content<think>"},
                })
            cfg.chat_logger.info(f"[LLM] round={round_num} | 模型直接回复（未调用工具）")
            return clean_llm_reply(raw_content_no_think)

        cfg.chat_logger.info(f"[LLM] round={round_num} | 模型调用工具: {[tc['function']['name'] for tc in tool_calls]}")
        turn_recorder.add_step({
            "kind": "tool_call",
            "title": f"模型调用工具(round={round_num}): {[tc['function']['name'] for tc in tool_calls]}",
            "data": {"round": round_num, "tools": [
                {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
                for tc in tool_calls
            ]},
        })
        full_messages.append(message)

        for tc in tool_calls:
            func_name = tc["function"]["name"]
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"[LLM] 工具参数解析失败: {func_name} | error={e}")
                func_args = {}

            if func_name == "web_search":
                query = func_args.get("query", "")
                max_results = func_args.get("max_results", 5)
                # 2026-10-30: 把用户本轮原话一并传给 DeepSeek Search（ori_question），
                # 帮助搜索引擎理解意图（模型给的 query 可能过短/丢语境）
                _ori_question = ""
                for _m in reversed(full_messages):
                    if _m.get("role") == "user":
                        _c = _m.get("content")
                        if isinstance(_c, str):
                            _ori_question = _c[:128]
                        elif isinstance(_c, list):
                            _ori_question = "".join(
                                str(p.get("text", "")) for p in _c if isinstance(p, dict)
                            )[:128]
                        break
                logger.info(f"[search] 联网搜索: {query} (max={max_results})")
                cfg.chat_logger.info(f"[SEARCH] 联网搜索触发: query='{query}', max_results={max_results}")
                search_text = await search.execute_web_search(
                    query, max_results, ori_question=_ori_question
                )

            elif func_name == "search_corpus":
                kw = func_args.get("keywords", "")
                cfg.chat_logger.info(f"[CORPUS] 语料库搜索: skill='{skill_name}', keywords='{kw}'")
                search_text = await asyncio.get_event_loop().run_in_executor(
                    None, search.execute_corpus_search, skill_name, kw
                )

            else:
                search_text = f"未知工具: {func_name}"

            full_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": search_text,
            })

            turn_recorder.add_step({
                "kind": "tool_result",
                "title": f"{func_name} 返回 ({len(search_text)} 字符)",
                "data": {
                    "tool": func_name,
                    "args": func_args,
                    "result_preview": search_text[:500] + ("…" if len(search_text) > 500 else ""),
                    "result_len": len(search_text),
                },
            })

    # 超过最大轮次，强制让模型回复
    _has_search_results = any(
        m["role"] == "tool" and "未找到" not in m.get("content", "")
        for m in full_messages
    )
    if _has_search_results:
        full_messages.append({
            "role": "user",
            "content": "（系统提示：请根据上面搜索到的信息直接回答用户的问题，不要再调用搜索工具。如果搜索结果不够完整，也请基于已有信息给出尽可能的回答。）",
        })

    turn_recorder.add_step({
        "kind": "llm_call",
        "title": f"强制总结轮(round={search._MAX_TOOL_ROUNDS + 1})",
        "data": {"forced": True, "round": search._MAX_TOOL_ROUNDS + 1, "has_results": _has_search_results},
    })

    payload_final = {**base_payload, "messages": full_messages}
    data = await api_client.api_request(payload_final, provider)
    if data is None:
        return None
    raw_content = data["choices"][0]["message"].get("content", "").strip()
    _think_text, raw_content_no_think = _extract_think(raw_content)
    if _think_text:
        turn_recorder.add_step({
            "kind": "thinking",
            "title": "强制总结轮的思考",
            "data": {"text": _think_text, "source": "content<think>"},
        })
    reply = clean_llm_reply(raw_content_no_think)

    # 如果回复仍为空，用搜索结果做最后一次尝试
    if "脑子短路" in reply and _has_search_results:
        cfg.chat_logger.info("[LLM] 回复被 DSML 截断，追加总结重试")
        full_messages.append({
            "role": "user",
            "content": "（系统：请立刻用自然语言总结上面搜索到的内容回复用户，不要使用任何工具标记。）",
        })
        payload_retry = {**base_payload, "messages": full_messages}
        data2 = await api_client.api_request(payload_retry, provider)
        if data2:
            raw2 = data2["choices"][0]["message"]["content"].strip()
            reply2 = clean_llm_reply(raw2)
            if "脑子短路" not in reply2:
                return reply2

    return reply


def _extract_think(text: str) -> tuple[str, str]:
    """从 text 里抽出 <think>...</think> / <thinking>...</thinking> 块。

    返回 (think_text, text_without_think)。
    若 text 里没有 think 块,think_text 为空,text 原样返回。
    """
    import re
    patterns = [
        (re.compile(r'<think>(.*?)</think>', re.DOTALL), '<think>'),
        (re.compile(r'<thinking>(.*?)</thinking>', re.DOTALL), '<thinking>'),
    ]
    think_parts = []
    rest = text
    for pat, label in patterns:
        for m in pat.finditer(rest):
            think_parts.append(m.group(1))
        rest = pat.sub('', rest)
    # 截断兜底
    for open_tag in ('<think>', '<thinking>'):
        idx = rest.find(open_tag)
        if idx != -1:
            think_parts.append(rest[idx + len(open_tag):])
            rest = rest[:idx]
    think_text = '\n'.join(p.strip() for p in think_parts if p and p.strip())
    return think_text, rest.strip()
