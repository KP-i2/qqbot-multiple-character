# 用量账本 & 学习报告（P2）

> 2026-11-10。目标：能回答「钱花在哪、学得有没有用」，不再只看服务商总账单。

## 用量账本

表：`qqbot/data/memory.db` → `usage_events`

| 字段 | 含义 |
|---|---|
| `kind` | `llm` / `search` |
| `purpose` | `chat`、`chat_stream`、`chat_tool`、`probe`、`memory_extract`、`profile`、`learning_summary`、`vision`、`web_search`、`search_events` |
| `skill` | 角色名（后台任务没有就空） |
| `provider` / `model` | deepseek / deepseek-v4-flash 等 |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | 非流式取 API 返回的 `usage` |
| `calls` | LLM 调用 1 次；Search 一次调用计 1 |
| `estimated` | 1 = 流式按字符数估算（没有精确 usage） |

覆盖的调用：

- 主对话（流式/非流式/工具轮/收尾）
- probe 工具判断
- 记忆抽取
- 用户画像
- 群学习摘要
- 识图
- DeepSeek Search（含 `search_events` 的 4–5 次补搜）

## 学习报告

表：`learning_runs`

每次群学习摘要记一条：消息数、accepted、rejected、是否成功、错误。

- accepted：最终写入 `memory_events` 的条数
- rejected：模型给了但 quote 校验没过/格式无效的条数
- 失败（无返回/冷却）也记录，方便看“为什么没学到”

## Dashboard

重启 Dashboard 后，「记忆」页新增：

- **用量账本（近 7 天）**：调用数、tokens、输入/输出、估算次数，按用途/角色/模型分行
- **学习报告（近 7 天）**：摘要次数、接收/丢弃、覆盖消息数、最近入库的群体记忆

API：

```
GET /api/usage/summary?days=7&skill=&purpose=
GET /api/usage/recent?limit=100
GET /api/learning/report?days=7&skill=
```

## 限制

- 流式回复的 token 是**按字符数估算**（`estimated=1`），不是精确计费
- Search 只记调用次数，token/费用在 DeepSeek 账单里
- 本地不存单价，不显示人民币金额；按服务商价格 × tokens 自己换算
- B站学习还没有接入，所以学习报告目前只覆盖 QQ 群
