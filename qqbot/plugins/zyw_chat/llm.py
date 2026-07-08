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
    active = prov.get_active_provider()
    fast = prov.get_fast_provider()
    probe_provider = fast or active
    probe_payload = {
        "model": probe_provider["model"],
        # minimax 不接 reasoning_effort（由 adapt_payload_for_provider 剥离），deepseek 兼容
        "reasoning_effort": cfg.MINIMAX_REASONING_EFFORT_WITH_TOOLS,
        "temperature": 0.3,
        "top_p": cfg.MINIMAX_TOP_P,
        "max_tokens": 10,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.2,
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

    tool_calls = data["choices"][0]["message"].get("tool_calls")
    wants_tools = bool(tool_calls)
    cfg.chat_logger.info(f"[PROBE] wants_tools={wants_tools} (provider={probe_provider['name']})")
    return wants_tools


async def call_deepseek(
    system_prompt: str,
    messages: list[dict],
    skill_name: str = "",
    current_user_content: Optional[object] = None,
) -> Optional[str]:
    """调用 DeepSeek API，支持 Function Calling 联网搜索 + 语料库搜索

    参数:
      system_prompt: 系统提示
      messages: 多轮对话历史(每条 role+content)
      skill_name: 当前角色名(用于语料库搜索)
      current_user_content: 可选,替换最后一条 user 消息的 content,
        支持字符串或 OpenAI 多模态 content 数组(用于带图消息)。
    """
    try:
        await asyncio.wait_for(api_client.API_SEMAPHORE.acquire(), timeout=30)
    except asyncio.TimeoutError:
        cfg.chat_logger.warning("[LLM] API 并发已满，排队超时")
        return "等一下下哦！窝的小脑袋瓜正在疯狂运转中 [捂脸] 再过几秒来戳窝叭～"

    try:
        return await asyncio.wait_for(
            _call_deepseek_inner(system_prompt, messages, skill_name, current_user_content),
            timeout=120,
        )
    except asyncio.TimeoutError:
        cfg.chat_logger.warning("[LLM] 整体调用超时 (180s)，强制返回")
        return "网络君跑不动惹！好慢好慢 [流汗] 过一会儿再戳窝叭拜托拜托 🙏💦"
    finally:
        api_client.API_SEMAPHORE.release()


async def _call_deepseek_inner(
    system_prompt: str,
    messages: list[dict],
    skill_name: str = "",
    current_user_content: Optional[object] = None,
) -> Optional[str]:
    """实际的 LLM 调用逻辑。

    Provider 选择规则（2026-06-28 改）：
    - 走 `prov.get_active_provider()`，由运行时参数 `active_llm_provider` 控制
    - 默认 deepseek（用户 2026-06-28 起的主选）
    - minimax 只在图片描述时使用（prov.describe_image_via_minimax）
    - 两个 Provider 独立，**不互相回退**。失败直接报错。

    current_user_content: 若提供,则替换最后一条 user 消息的 content
    (支持字符串或 OpenAI 多模态 content 列表,用于带图消息)。

    2026-06-28 fix:含图消息先调 describe_image_via_minimax 把图转文字,
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

    use_tools = cfg.WEB_SEARCH_ENABLED and cfg.SEARCH_AVAILABLE
    tools_available = use_tools or bool(skill_name)

    # 主选 provider（由运行时参数 active_llm_provider 控制，dashboard 可改）
    provider = prov.get_active_provider()
    active_model = provider["model"]

    # 思考强度：可用 Dashboard 参数热调（按 provider 分支）
    if provider["name"] == "minimax":
        reasoning_effort = (
            cfg.MINIMAX_REASONING_EFFORT_WITH_TOOLS if tools_available
            else cfg.MINIMAX_REASONING_EFFORT_NO_TOOLS
        )
    else:
        # deepseek 用 reasoning_effort 时直接给 high
        reasoning_effort = "high"
    cfg.chat_logger.info(f"[LLM] 调用 {provider['name']} | model={active_model} | reasoning={reasoning_effort} | 联网={'开' if use_tools else '关'} | 语料库={'开' if skill_name else '关'} | msgs={len(messages)}")

    if use_tools:
        now = datetime.now()
        date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
        system_prompt = system_prompt + date_hint

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    base_payload = {
        "model": active_model,
        "reasoning_effort": reasoning_effort,
        # 以下三个参数由 adapt_payload_for_provider 在 minimax 上下文中自动剥掉
        "temperature": cfg.MINIMAX_TEMPERATURE,
        "top_p": cfg.MINIMAX_TOP_P,
        "max_tokens": cfg.MINIMAX_MAX_TOKENS,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.2,
        "stream": False,
    }

    if not tools_available:
        payload = {**base_payload, "messages": full_messages}
        data = await api_client.api_request(payload, provider)
        if data is None:
            return None
        return clean_llm_reply(data["choices"][0]["message"]["content"].strip())

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
        payload = {
            **base_payload,
            "messages": full_messages,
            "tools": active_tools,
            "tool_choice": "auto",
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
                max_results = func_args.get("max_results", 8)
                logger.info(f"[search] 联网搜索: {query} (max={max_results})")
                cfg.chat_logger.info(f"[SEARCH] 联网搜索触发: query='{query}', max_results={max_results}")
                search_text = await search.execute_web_search(query, max_results)

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
