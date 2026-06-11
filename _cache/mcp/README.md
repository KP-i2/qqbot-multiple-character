# Web Search 集成

为 QQ Bot 添加联网搜索能力。基于 DuckDuckGo，免费无需 API Key。

## 文件说明

| 文件 | 作用 |
|------|------|
| `web_search.py` | 独立搜索模块，封装 DuckDuckGo 搜索 |
| `search_integration.py` | DeepSeek function calling 集成模块 |
| `requirements.txt` | 本目录的 Python 依赖 |

## 集成方式

### 方案：DeepSeek Function Calling（推荐）

DeepSeek API 原生支持 function calling，让模型自己决定什么时候需要联网搜索。

#### 第一步：安装依赖

```bash
venv\Scripts\activate
pip install -r mcp/requirements.txt
```

#### 第二步：修改插件代码

打开 `qqbot/plugins/zyw_chat/__init__.py`，做以下修改：

**1. 添加 import**（文件顶部）：

```python
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "mcp"))
from search_integration import call_deepseek_with_search
```

**2. 替换 call_deepseek 调用**（在 `handle_chat` 函数中）：

把原来的：
```python
reply = await call_deepseek(skill.system_prompt, conversation_histories[history_key])
```

改为：
```python
reply = await call_deepseek_with_search(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    model=DEEPSEEK_MODEL,
    system_prompt=skill.system_prompt,
    messages=conversation_histories[history_key],
)
```

#### 第三步：重启 Bot

```bash
start_all.bat
```

#### 效果

- 用户问"今天天气怎么样" → DeepSeek 自动调用 web_search → 拿到实时结果 → 用角色语气回复
- 用户问"你觉得xxx怎么样"（不需要搜索的问题） → DeepSeek 直接回复，不调用搜索
- 模型自己判断，不需要改任何 prompt

## 独立测试搜索模块

```bash
venv\Scripts\activate
python mcp/web_search.py
# 输入关键词即可看到搜索结果
```

## 工作原理

```
用户消息
  ↓
DeepSeek API（带 tools 定义）
  ↓
模型判断需要搜索？
  ├── 是 → 返回 tool_call: web_search("xxx")
  │         ↓
  │       Bot 执行 DuckDuckGo 搜索
  │         ↓
  │       搜索结果追加到对话历史
  │         ↓
  │       再次调用 DeepSeek（带搜索结果）
  │         ↓
  │       模型用角色语气生成回复
  │
  └── 否 → 直接返回回复
```

## 注意事项

- DuckDuckGo 在部分网络环境下可能不稳定，如遇超时会自动跳过搜索
- 每次对话最多触发 2 轮搜索（`max_tool_rounds=2`），防止死循环
- 搜索结果会以 "tool" 角色消息追加到历史中，不占用用户对话记忆
- 如需更好的搜索质量，可换成 Tavily API（需注册获取免费 key，每月 1000 次）
