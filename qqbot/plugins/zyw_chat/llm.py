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
from .dsml_cleaner import clean_llm_reply, strip_dsml_markup


async def probe_tool_usage(system_prompt: str, messages: list[dict], skill_name: str = "") -> bool:
    """快速探测模型是否需要调用工具。
    返回 True 表示模型想调用工具，False 表示直接回复。
    """
    use_tools = cfg.WEB_SEARCH_ENABLED and cfg.SEARCH_AVAILABLE
    if not use_tools and not skill_name:
        return False

    active_tools = []
    if use_tools:
        active_tools.append(search.SEARCH_TOOLS[0])   # web_search
    if skill_name:
        active_tools.append(search.SEARCH_TOOLS[1])   # search_corpus

    now = datetime.now()
    date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
    probe_system = system_prompt + date_hint

    payload = {
        "model": cfg.DEEPSEEK_SEARCH_MODEL,
        "reasoning_effort": "low",
        "temperature": 0.3,
        "top_p": 0.9,
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

    try:
        data = await api_client.api_request(payload, prov.get_provider("deepseek"))
    finally:
        api_client.API_SEMAPHORE.release()

    if data is None:
        return True

    tool_calls = data["choices"][0]["message"].get("tool_calls")
    wants_tools = bool(tool_calls)
    cfg.chat_logger.info(f"[PROBE] wants_tools={wants_tools}")
    return wants_tools


async def call_deepseek(system_prompt: str, messages: list[dict], skill_name: str = "") -> Optional[str]:
    """调用 DeepSeek API，支持 Function Calling 联网搜索 + 语料库搜索"""
    try:
        await asyncio.wait_for(api_client.API_SEMAPHORE.acquire(), timeout=30)
    except asyncio.TimeoutError:
        cfg.chat_logger.warning("[LLM] API 并发已满，排队超时")
        return "等一下下哦！窝的小脑袋瓜正在疯狂运转中 [捂脸] 再过几秒来戳窝叭～"

    try:
        return await asyncio.wait_for(
            _call_deepseek_inner(system_prompt, messages, skill_name),
            timeout=120,
        )
    except asyncio.TimeoutError:
        cfg.chat_logger.warning("[LLM] 整体调用超时 (180s)，强制返回")
        return "网络君跑不动惹！好慢好慢 [流汗] 过一会儿再戳窝叭拜托拜托 🙏💦"
    finally:
        api_client.API_SEMAPHORE.release()


async def _call_deepseek_inner(system_prompt: str, messages: list[dict], skill_name: str = "") -> Optional[str]:
    """实际的 DeepSeek API 调用逻辑"""
    use_tools = cfg.WEB_SEARCH_ENABLED and cfg.SEARCH_AVAILABLE
    tools_available = use_tools or bool(skill_name)

    if tools_available:
        provider = prov.get_provider("deepseek")
        active_model = cfg.DEEPSEEK_SEARCH_MODEL
    else:
        provider = prov.get_active_provider()
        active_model = provider["model"]

    reasoning_effort = "low" if tools_available else "high"
    cfg.chat_logger.info(f"[LLM] 调用 {provider['name']} | model={active_model} | reasoning={reasoning_effort} | 联网={'开' if use_tools else '关'} | 语料库={'开' if skill_name else '关'} | msgs={len(messages)}")

    if use_tools:
        now = datetime.now()
        date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
        system_prompt = system_prompt + date_hint

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    base_payload = {
        "model": active_model,
        "reasoning_effort": reasoning_effort,
        "temperature": 0.95,
        "top_p": 0.9,
        "max_tokens": 2048,
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
    for round_num in range(search._MAX_TOOL_ROUNDS + 1):
        payload = {
            **base_payload,
            "messages": full_messages,
            "tools": active_tools,
            "tool_choice": "auto",
        }

        data = await api_client.api_request(payload, provider)
        if data is None:
            return None

        choice = data["choices"][0]
        message = choice["message"]

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            raw_content = message.get("content", "").strip()
            cfg.chat_logger.info(f"[LLM] round={round_num} | 模型直接回复（未调用工具）")
            return clean_llm_reply(raw_content)

        cfg.chat_logger.info(f"[LLM] round={round_num} | 模型调用工具: {[tc['function']['name'] for tc in tool_calls]}")
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

    payload_final = {**base_payload, "messages": full_messages}
    data = await api_client.api_request(payload_final, provider)
    if data is None:
        return None
    raw_content = data["choices"][0]["message"]["content"].strip()
    reply = clean_llm_reply(raw_content)

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
