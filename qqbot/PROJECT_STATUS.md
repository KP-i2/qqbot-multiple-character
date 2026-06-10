# QQ Bot 项目维护手册（截至 2026-06-09）

## 项目概述

基于 **NoneBot2 + OneBot V11** 的 QQ 聊天机器人，使用 **DeepSeek API** 实现多角色对话，支持联网搜索、语料库搜索、流式输出。通过 **NapCat (NapCatQQ-Desktop)** 桥接 QQ 协议。

---

## 目录结构

```
D:\agent_function\skill_communication\qqbot\
├── bot.py                          # NoneBot2 启动入口
├── dashboard.py                    # Web 仪表盘 (port 8501)
├── .env                            # 所有配置（API Key、端口等）
├── pyproject.toml                  # NoneBot2 项目配置
├── PROJECT_STATUS.md               # 项目维护手册（本文件）
├── data/
│   └── conversation_histories.json # 持久化对话历史
├── logs/
│   └── chat.log                    # 聊天专用日志
├── plugins/
│   └── zyw_chat/
│       └── __init__.py             # 主插件（~3100 行，全部逻辑在此）
├── skills/                         # 角色 Skill 目录（10 个角色）
│   ├── 761/   aoyi/   guuo/   iteru/   jinmao/
│   ├── mika/  nana/   skingfd/  ytj/   zyw/
│   └── (每个 skill 含 SKILL.md + 可选 corpus/ + avatar)
└── dashboard/
    ├── main.py                     # FastAPI 仪表盘主逻辑
    ├── monitor.py                  # 进程监控 + watchdog
    ├── skill_manager.py            # Skill CRUD
    ├── weibo_fetcher.py            # 微博数据抓取
    └── static/index.html           # 仪表盘前端

D:\agent_function\skill_communication\emoji\   # 情绪表情目录
├── angry/   happy/   joker/   sad/             # 情绪子文件夹
└── (每个文件夹含 keywords.txt + 表情图片)

D:\agent_function\skill_communication\skill_qqbot\   # Python venv
D:\agent_function\skill_communication\skill_qqbot\Scripts\python.exe  # venv Python
```

---

## 启动与管理

### 启动命令

```powershell
# 启动 Bot (port 8080)
cd D:\agent_function\skill_communication\qqbot
D:\agent_function\skill_communication\skill_qqbot\Scripts\python.exe bot.py

# 启动 Dashboard (port 8501)
cd D:\agent_function\skill_communication\qqbot
D:\agent_function\skill_communication\skill_qqbot\Scripts\python.exe dashboard.py
```

### 进程架构

- `skill_qqbot\python.exe bot.py` → 启动器（父进程）
  - `anaconda3\python.exe bot.py` → uvicorn 工作进程（子进程，正常现象）
- `skill_qqbot\python.exe dashboard.py` → 启动器（父进程）
  - `anaconda3\python.exe dashboard.py` → uvicorn 工作进程（子进程）

> **注意**：venv 基于 Anaconda 创建，所以子进程显示为 Anaconda Python，这是正常行为。

### 重启流程

```powershell
# 杀掉所有 Python 进程
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
# 然后重新启动 bot.py 和 dashboard.py
```

### NapCat 连接

- NapCatQQ-Desktop 外部管理（不由 bot 启动）
- 连接方式：OneBot V11 反向 WebSocket
- Bot 监听 `ws://0.0.0.0:8080/onebot/v11/ws`
- NapCat 主动连入 bot

---

## 当前配置（.env）

| 配置项 | 当前值 | 说明 |
|--------|--------|------|
| `PORT` | 8080 | Bot 监听端口 |
| `DEEPSEEK_API_KEY` | 已配置 | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API 地址 |
| `DEEPSEEK_MODEL` | `deepseek-v4-pro` | 主模型 |
| `OPENAI_ENABLED` | `false` | OpenAI GPT-5.5（当前禁用） |
| `OPENAI_API_KEY` | 已配置 | OpenAI 代理 Key |
| `OPENAI_BASE_URL` | 已配置 | OpenAI 代理地址 |
| `OPENAI_MODEL` | `gpt-5.5` | OpenAI 模型名 |
| `WEB_SEARCH_ENABLED` | `true` | 联网搜索开关 |
| `ACTIVE_HOURS_START` | 0 | 活跃时间起始 |
| `ACTIVE_HOURS_END` | 23 | 活跃时间结束 |
| `ADMIN_QQ` | 已配置 | 管理员 QQ 号 |
| `DASHBOARD_TOKEN` | 已配置 | 仪表盘访问令牌 |

---

## 核心架构（__init__.py）

### LLM Provider 系统

```
_get_active_provider()  →  优先 OpenAI（如果 ENABLED），否则 DeepSeek
_get_provider("deepseek")  →  强制 DeepSeek
_mark_provider_failed()  →  连续失败 2 次进入 120s 冷却
```

**当前状态**：`OPENAI_ENABLED=false`，所有请求走 DeepSeek。

**Provider 健康追踪**：`_provider_health` 字典记录每个 provider 的 `healthy`/`fail_count`/`last_fail`。

### API 请求分层

| 函数 | 用途 |
|------|------|
| `_api_request(payload, provider)` | 非流式请求，带 provider 回退 |
| `_api_request_inner(payload, provider)` | 对指定 provider 发请求，带重试 |
| `_api_request_stream(payload)` | 流式请求，带 provider 回退 |
| `_api_request_stream_inner(payload, provider)` | 对指定 provider 流式请求 |
| `_adapt_payload_for_provider(payload, name)` | 按 provider 调整参数（去 reasoning_effort 等） |

### 对话处理流程

```
用户消息
  → 命令分发（/reset, /skills, /switch, /current）
  → 活跃时间检查
  → 身份提取（QQ 昵称/群名片）
  → 风格自适应（消息长度、提问检测、群聊/私聊）
  → 流式决策：
      ├─ 搜索意图关键词命中 → 直接走工具调用路径
      ├─ probe（DeepSeek, max_tokens=1）判断需不需要工具
      │   ├─ 需要工具 → call_deepseek()（非流式，带 tools）
      │   └─ 不需要 → 流式输出（_api_request_stream）
      └─ 无 skill 激活 → 按 WEB_SEARCH_ENABLED 决定
  → 回复发送 + 历史保存
```

### 流式输出

| 参数 | 值 | 说明 |
|------|-----|------|
| `_STREAM_FLUSH_CHARS` | 60 | 累积多少字符后找句末断点 |
| `_STREAM_MAX_FLUSH_SIZE` | 300 | 单段最大字符（强制断句） |
| `_STREAM_FLUSH_INTERVAL` | 8.0 | 保留但未使用（time_flush 已移除） |

**断句策略**（仅两种）：
1. `sentence_end`：≥60 字符 + 末尾是句末标点（`。！？\n!?`）
2. `max_size`：≥300 字符强制切

### 并发控制

| 机制 | 值 | 说明 |
|------|-----|------|
| `_API_SEMAPHORE` | 30 | 全局最多同时 30 个 API 请求 |
| `_user_processing` | per-user Lock | 同一用户串行处理 |
| 排队等待超时 | 30s（Semaphore） | 等不到信号量就返回忙 |
| 整体调用超时 | 120s | 含工具循环 |
| `REQUEST_TIMEOUT` | 60s | HTTP 请求超时 |

### 对话记忆

| 参数 | 值 |
|------|-----|
| `MAX_HISTORY_ROUNDS` | 15 轮 |
| `MAX_HISTORY_MESSAGES` | 30 条（15×2） |
| `_HISTORY_TTL` | 6 小时无活动自动清理 |
| 持久化文件 | `data/conversation_histories.json` |
| 保存策略 | 节流写磁盘，最多每 60 秒写一次 |
| 清理任务 | 每 30 分钟扫描过期 key |

**Key 格式**：`group_{群号}_{用户QQ}_{角色名}` 或 `user_{用户QQ}_{角色名}`

### 工具调用（Function Calling）

**工具定义**：`_SEARCH_TOOLS` 数组
- `web_search`：联网搜索（搜狗/百度/微博/DuckDuckGo 多源并行）
- `search_corpus`：TF-IDF 语料库搜索（在 skill 的 corpus/ 目录中搜索）

**工具调用循环**（`_call_deepseek_inner`）：
- 最多 `_MAX_TOOL_ROUNDS = 2` 轮
- 工具场景强制 DeepSeek（`deepseek-v4-flash` 模型）
- probe 也强制 DeepSeek（速度快）
- 所有 `_api_request` 调用都传入了正确的 `provider` 参数

### DSML / 工具标签清洗

`_strip_dsml_markup()` 清洗以下格式：
- DSML 格式：`<｜DSML｜tool_calls>...`
- 原生 `<tool_calls>` XML
- 通用工具标签：`<web_search query="...">`、`</web_search>`、`<search_corpus ...>`、`</search_corpus>`（正则 `_TOOL_LIKE_TAG`）
- 截断兜底：从开标签到字符串末尾

**流式空回复回退**：如果流式输出全是工具 XML（清洗后为空），自动回退到非流式 `call_deepseek`（带完整 tools 定义）。

### 非流式消息分段

| 参数 | 值 | 说明 |
|------|-----|------|
| `_SPLIT_THRESHOLD` | 120 字符 | 超过才分段 |
| `_SPLIT_MAX_SEGMENTS` | 3 段 | 最多分几段 |
| `_SPLIT_MIN_SEGMENT` | 40 字符 | 每段最少字符 |

---

## 命令列表

| 命令 | 别名 | 功能 |
|------|------|------|
| `/reset` | `/重置`, `/清空记忆` | 清空当前对话历史 |
| `/skills` | `/角色`, `/列表` | 显示可用角色列表 |
| `/switch <名字>` | `/切换` | 切换全局角色（自动换头像+昵称） |
| `/current` | `/当前` | 显示当前角色 + 模型信息 |
| `/reloademoji` | `/重载表情` | 热重载表情文件（管理员） |

---

## Dashboard（Web 仪表盘）

- 地址：`http://localhost:8501`
- 访问令牌：`qqbot2024`
- 功能：进程监控、Skill 管理、运行时参数调整、日志查看
- Watchdog：每 30 秒检测 bot 进程，挂了自动重启（使用 `VENV_PYTHON`）

### monitor.py 关键修复

`start_nonebot2()` 已改为使用 `VENV_PYTHON`（skill_qqbot venv）而非 `BASE_PYTHON`（Anaconda），避免 watchdog 重启时用错误的 Python 启动 bot。

---

## 已知问题与修复记录

### 已修复

| 问题 | 原因 | 修复 |
|------|------|------|
| tool_call XML 泄露给用户 | `_strip_dsml_markup` 没处理原生 `<tool_calls>` XML | 添加 3 个正则模式 |
| 流式输出不自然截断 | time_flush 按时间强制切分 | 移除 time_flush，只保留 sentence_end + max_size |
| GPT-5.5 `<web_search>` 标签泄露 | 正则只覆盖 `<tool_calls>` 不覆盖 `<web_search>` | 添加 `_TOOL_LIKE_TAG` 通用正则 |
| `<search_corpus></search_corpus>` 泄露 | 正则没匹配闭标签 | `_TOOL_LIKE_TAG` 加 `/?` 前缀 |
| 流式全工具 XML → 空回复发给用户 | `full_reply` 未清洗 | 清洗后为空时回退到非流式调用 |
| OpenAI 收到 `deepseek-v4-flash` 模型名 | `_api_request` 默认走 active provider | 所有调用传入正确的 provider |
| Dashboard watchdog 用 Anaconda 重启 bot | `start_nonebot2()` 用 `BASE_PYTHON` | 改为 `VENV_PYTHON` |
| `/current` 不显示模型 | 改错了处理器位置 | 修正到 `handle_current`（on_command 注册的） |

### 当前已知待优化

- Probe（DeepSeek）判断"不需要工具"→ 流式路径中模型仍可能输出工具调用 XML，目前靠清洗 + 回退处理
- 群聊场景下多人并发对话可能产生较长的排队等待

---

## 日志排查

### 日志文件

- **聊天日志**：`D:\agent_function\skill_communication\qqbot\logs\chat.log`
- **NoneBot2 日志**：`D:\agent_function\skill_communication\qqbot\logs\nonebot2.log`

### 关键日志标签

| 标签 | 含义 |
|------|------|
| `[MSG]` | 收到的用户消息 |
| `[PROBE]` | 探测结果（是否需要工具） |
| `[LLM]` | LLM 调用详情（provider、model、round） |
| `[STREAM_SEG]` | 流式分段发送（reason=len=text） |
| `[STREAM]` | 流式完成汇总 |
| `[REPLY]` | 非流式回复 |
| `[SEARCH]` | 联网搜索触发 |
| `[CORPUS]` | 语料库搜索触发 |
| `[URL]` | URL 检测与内容抓取 |
| `[EMOJI]` | 情绪表情发送 |

### 用 Python 读日志（避免终端编码问题）

```python
import sys
with open(r'D:\agent_function\skill_communication\qqbot\logs\chat.log', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for line in lines[-30:]:
    sys.stdout.buffer.write(line.encode('utf-8'))
```

---

## 角色 Skill 系统

10 个已加载角色，每个 skill 目录包含：
- `SKILL.md`：角色定义（system prompt、描述、版本等）
- `corpus/`（可选）：语料库文本，用于 TF-IDF 搜索
- `avatar.jpg`（可选）：角色头像

切换角色时自动更换 QQ 头像和昵称（`_set_profile` 异步任务）。

---

## 联网搜索引擎

优先级：搜狗 → 百度 → 微博 → DuckDGo（多源并行，带健康检查和冷却机制）

搜索结果处理：
- 搜狗/百度返回摘要 + URL
- 高价值页面自动抓取全文（`_fetch_page_content`）
- 结果拼接后作为 tool response 返回给模型

---

## URL 内容抓取（2026-06-10 新增）

用户消息中包含 URL 时，bot 自动抓取内容并注入到 LLM 上下文。

### 流程

1. 消息到达时，正则 `_URL_PATTERN` 提取 http/https 链接（每条消息最多 2 个）
2. 在等待用户锁期间启动异步抓取任务（不阻塞主流程）
3. B站链接走专用 API，其他链接走通用页面抓取
4. 抓取结果注入 `effective_prompt` 的 `context_parts`
5. 同时添加风格提示"对方分享了链接，请根据内容给出有内容的回应"

### B站专用处理（`_fetch_bilibili_info`）

- 识别 `bilibili.com/video/BVxxx` 和 `b23.tv` 短链
- 通过 B站公开 API 获取：标题、UP主、播放/点赞/投币/弹幕/评论数、简介、热门评论（前5条）、视频标签
- 评论和标签并行请求，总超时 8 秒

### 通用 URL 处理（`_fetch_page_content`）

- HTML → 去 script/style → 提取 p/div 正文（≤2000 字符）
- 短链先解析重定向后抓取

### 相关常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `_URL_MAX_FETCH` | 2 | 每条消息最多抓取 URL 数 |
| `_URL_OVERALL_TIMEOUT` | 10s | URL 处理总超时 |
| `_URL_CONTENT_MAX_CHARS` | 2000 | URL 内容注入最大字符 |

---

## QQ 富媒体消息解析（2026-06-10 新增）

`_parse_rich_message()` 函数处理非纯文本消息：

- **QQ小程序（json 类型）**：解析 JSON 数据，提取 `prompt` 字段或 `meta` 中的描述
- **分享卡片（share 类型）**：提取标题、描述和链接
- **XML 消息**：提取 title、brief、source 标签内容

当 `get_plaintext()` 返回空时（纯小程序/卡片消息），自动切换到 `_parse_rich_message()` 解析。

---

## 情绪表情系统（2026-06-10 新增）

bot 回复后根据对话情绪概率发送表情图片。

### 目录结构

```
D:\agent_function\skill_communication\emoji\
├── angry/
│   ├── keywords.txt       # 关键词文件（每行一个，# 为注释）
│   └── img-xxx.jpg        # 表情图片
├── happy/
│   ├── keywords.txt
│   └── Image_xxx.png
├── joker/
│   ├── keywords.txt
│   └── 1781012281746.jpeg
└── sad/
    ├── keywords.txt
    └── img-xxx.jpg
```

### 工作流程

1. bot 生成回复后，分析用户消息 + bot 回复的组合文本
2. 遍历各情绪的 `keywords.txt` 关键词，匹配第一个命中的情绪
3. 35% 概率触发（`_EMOJI_PROBABILITY = 0.35`）
4. 同一用户 60 秒冷却，避免刷屏
5. 从对应情绪文件夹随机选一张图片，通过 `MessageSegment.image()` 发送

### 热加载

- 命令：`/reloademoji` 或 `/重载表情`（仅管理员）
- 自动发现所有子文件夹（文件夹名 = 情绪类别，可任意新增）
- 每个文件夹的 `keywords.txt` 定义触发关键词
- 没有 `keywords.txt` 时使用内置默认关键词（仅限 angry/happy/joker/sad）
- 图片格式支持：jpg/jpeg/png/gif/webp

### 新增情绪步骤

1. 在 `emoji/` 下新建文件夹（如 `shy`、`love`、`confused`）
2. 放入表情图片
3. 创建 `keywords.txt`，每行一个关键词
4. 给 bot 发 `/reloademoji`，立即生效

---

*文档更新时间：2026-06-10 10:25*
