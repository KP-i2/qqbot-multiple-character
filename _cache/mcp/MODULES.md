# MCP 目录功能介绍

本目录为 QQ Bot 提供联网搜索能力，基于 DuckDuckGo 搜索引擎，免费且无需 API Key。

## 文件总览

```
mcp/
├── web_search.py            ← 底层搜索模块
├── search_integration.py    ← DeepSeek function calling 集成层
├── requirements.txt         ← Python 依赖声明
└── MODULES.md               ← 本文件
```

---

## web_search.py — 底层搜索模块

封装 DuckDuckGo 搜索能力，是整个联网功能的基座。不依赖项目其他模块，可以独立使用和测试。

### 提供的函数

**`web_search(query, max_results=5, region="cn-zh") -> list[dict]`**

执行通用网页搜索。返回结果列表，每条包含 `title`、`href`（链接）、`body`（摘要）三个字段。默认搜索中文内容（region=cn-zh），可通过参数切换为 `"us-en"` 等。搜索失败时返回空列表并打印错误，不会抛异常。

**`web_news(query, max_results=5) -> list[dict]`**

执行新闻搜索，返回最新的新闻报道。每条包含 `title`、`url`、`body`、`date` 字段。适合用户询问时事、最新动态等场景。

**`format_results(results) -> str`**

将搜索结果列表格式化成结构化文本，输出编号 + 标题 + 链接 + 摘要的形式。设计目的是让搜索结果可以直接作为文本塞进 LLM 的 prompt 中。如果传入空列表，返回 "未找到相关结果。"

### 独立测试

```bash
venv\Scripts\activate
python mcp/web_search.py
# 输入关键词，直接输出格式化搜索结果
```

---

## search_integration.py — DeepSeek Function Calling 集成层

将 `web_search.py` 的搜索能力通过 DeepSeek 的 function calling 机制接入 Bot 对话流程，让模型自主决定何时需要联网。

### 核心机制

DeepSeek API 支持在请求中声明可用工具（tools）。当模型判断当前对话需要实时信息时，会返回一个 `tool_call` 而不是直接回复文本。本模块拦截这个 tool_call，调用 `web_search.py` 执行实际搜索，把结果喂回模型，模型再基于搜索结果生成最终回复。

整个过程对对话双方透明——用户正常聊天，模型自己判断要不要搜、搜什么。

### 提供的函数

**`call_deepseek_with_search(api_key, base_url, model, system_prompt, messages, max_tool_rounds=2, timeout=60, temperature=0.95) -> Optional[str]`**

这是 `zyw_chat/__init__.py` 中原有 `call_deepseek()` 的替代品。函数签名接收相同的 API 配置和对话参数，内部增加了工具调用循环：

1. 发送消息 + `SEARCH_TOOLS` 工具定义给 DeepSeek
2. 如果返回 `tool_calls`，解析参数并调用 `web_search()`
3. 将搜索结果以 `role: "tool"` 消息追加到对话历史
4. 再次调用 DeepSeek，重复直到模型不再请求工具（或达到 `max_tool_rounds` 上限）
5. 超过上限时发送一次不带 tools 的请求，强制模型生成回复

### 工具定义（SEARCH_TOOLS）

模块顶部定义了注册给 DeepSeek 的工具 schema：

```json
{
  "name": "web_search",
  "description": "在网络上搜索实时信息。当用户询问最新新闻、实时数据、你不确定的事实、或需要查证的信息时使用此工具。",
  "parameters": {
    "query": "搜索关键词，应该简洁精准",
    "max_results": "返回结果数量，默认 5"
  }
}
```

工具描述决定了模型在什么场景下触发搜索。如果需要调整触发行为（比如更激进或更保守地搜索），修改这段 description 即可。

### 安全设计

- **最大轮次限制**：`max_tool_rounds=2`，防止模型反复调用搜索导致死循环
- **超时兜底**：超过轮次后发送不带 tools 的请求，强制生成回复
- **异常容错**：搜索失败时返回空结果，模型仍能基于已有信息回复

---

## requirements.txt — 依赖声明

```
ddgs>=8.0.0      ← DuckDuckGo 搜索引擎 Python 封装
httpx>=0.27.0    ← 异步 HTTP 客户端（与 Bot 主项目共用）
```

`ddgs` 是 DuckDuckGo 的官方 Python 库（原 `duckduckgo-search` 包的升级版），无需注册、无需 API Key，开箱即用。`httpx` 已经在 Bot 主项目的依赖中，这里声明是为了 mcp 目录独立使用时也能安装。

安装命令：

```bash
venv\Scripts\activate
pip install -r mcp/requirements.txt
```

---

## 调用链路

```
用户发消息 "今天北京天气怎么样"
        │
        ▼
zyw_chat 插件接收消息
        │
        ▼
call_deepseek_with_search()
  发送 system_prompt + 对话历史 + tools 定义
        │
        ▼
DeepSeek 判断需要搜索
  返回 tool_call: web_search(query="北京今天天气")
        │
        ▼
search_integration.py 拦截 tool_call
  调用 web_search.web_search("北京今天天气")
        │
        ▼
web_search.py → DuckDuckGo API
  返回搜索结果（标题 + 链接 + 摘要）
        │
        ▼
format_results() 格式化为文本
  以 role: "tool" 追加到对话历史
        │
        ▼
再次调用 DeepSeek（带搜索结果）
        │
        ▼
模型基于搜索结果 + 角色人格生成回复
  "北京今天 32 度，热得一批，出门记得防晒..."
        │
        ▼
返回给用户
```

## 接入 Bot 的改动量

只需修改 `qqbot/plugins/zyw_chat/__init__.py` 两处：

1. 文件顶部加 import（2 行）
2. `handle_chat` 函数里把 `call_deepseek()` 换成 `call_deepseek_with_search()`（1 处调用）

改完重启 Bot 即可，无需修改角色 Skill 文件或 DeepSeek 配置。
