"""
DeepSeek Function Calling 集成模块

将 web_search 作为工具注册到 DeepSeek API 调用中，
让模型自行决定何时联网搜索。

使用方法：
    将 search_integration.py 中的 call_deepseek_with_search() 
    替换 zyw_chat 插件中原来的 call_deepseek() 即可。
"""

import json
import sys
import httpx
from pathlib import Path
from typing import Optional

# 把 mcp/ 目录加入 sys.path 以便导入 web_search
sys.path.insert(0, str(Path(__file__).parent))
from web_search import web_search, format_results


# DeepSeek Function Calling 工具定义
SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "在网络上搜索实时信息。当用户询问最新新闻、实时数据、你不确定的事实、或需要查证的信息时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，应该简洁精准",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    }
]


async def call_deepseek_with_search(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tool_rounds: int = 2,
    timeout: int = 60,
    temperature: float = 0.95,
) -> Optional[str]:
    """
    带 function calling 的 DeepSeek 调用。

    流程：
    1. 发送消息 + tools 定义给 DeepSeek
    2. 如果模型返回 tool_calls，执行搜索
    3. 把搜索结果追加到消息中，再次调用 DeepSeek
    4. 重复直到模型不再调用工具（最多 max_tool_rounds 轮）
    5. 返回最终回复

    Args:
        api_key: DeepSeek API Key
        base_url: API 基础 URL
        model: 模型名称
        system_prompt: 系统提示（角色设定）
        messages: 对话历史
        max_tool_rounds: 最大工具调用轮次
        timeout: 请求超时秒数
        temperature: 温度参数

    Returns:
        模型最终回复文本，失败返回 None
    """
    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 构建完整的消息列表
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    for round_num in range(max_tool_rounds + 1):
        payload = {
            "model": model,
            "messages": full_messages,
            "tools": SEARCH_TOOLS,
            "tool_choice": "auto",
            "temperature": temperature,
            "top_p": 0.9,
            "max_tokens": 1024,
            "frequency_penalty": 0.3,
            "presence_penalty": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            print(f"[search_integration] API 请求失败: {e}")
            return None

        choice = data["choices"][0]
        message = choice["message"]

        # 检查是否有工具调用
        tool_calls = message.get("tool_calls")
        if not tool_calls or choice.get("finish_reason") == "stop":
            # 模型不再调用工具，返回最终回复
            return message.get("content", "").strip()

        # 把模型的 tool_calls 消息追加到历史
        full_messages.append(message)

        # 处理每个工具调用
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            func_args = json.loads(tc["function"]["arguments"])

            if func_name == "web_search":
                query = func_args.get("query", "")
                max_results = func_args.get("max_results", 5)
                print(f"[search_integration] 搜索: {query} (max={max_results})")

                results = web_search(query, max_results=max_results)
                search_text = format_results(results)
            else:
                search_text = f"未知工具: {func_name}"

            # 把搜索结果作为 tool 消息追加
            full_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": search_text,
            })

    # 超过最大轮次，强制让模型回复（不带 tools）
    payload_final = {
        "model": model,
        "messages": full_messages,
        "temperature": temperature,
        "top_p": 0.9,
        "max_tokens": 1024,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload_final)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[search_integration] 最终回复失败: {e}")
        return None
