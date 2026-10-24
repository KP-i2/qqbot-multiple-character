# ⚙️ 配置参考

全部配置项与运行时参数。最简可用配置见 [INSTALL.md](INSTALL.md) 步骤 3；运行时参数也可在 Dashboard「参数设置」页热改（写入 `qqbot/data/runtime_settings.json`）。


完整配置项写在 `private/.env`（NoneBot2 自身需要的基础变量 + 项目扩展变量）。NoneBot2 官方还会从 `qqbot/.env.example` 找模板，实际生效值以 `private/.env` 为准。

## private/.env 完整参考

| 参数 | 说明 | 默认值 | 必填 |
|------|------|--------|------|
| **基础** | | | |
| `HOST` | NoneBot2 监听地址 | `0.0.0.0` | ✅ |
| `PORT` | NoneBot2 监听端口（SnowLuma 连到此） | `8080` | ✅ |
| `COMMAND_START` | 命令前缀 | `["/"]` | ✅ |
| **主对话 LLM（必填二选一以上，默认 deepseek）** | | | |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | — | ⭕（主对话默认） |
| `DEEPSEEK_BASE_URL` | DeepSeek Base URL | `https://api.deepseek.com` | |
| `DEEPSEEK_MODEL` | DeepSeek 模型名 | `deepseek-v4-pro` | |
| `DEEPSEEK_VISION_ENABLED` | DeepSeek 官方视觉开关（默认视觉） | `false` | 推荐启用 |
| `DEEPSEEK_VISION_MODEL` | DeepSeek 视觉模型名（复用 `DEEPSEEK_API_KEY`） | `deepseek-v4-flash-vision-exp` | |
| `MINIMAX_ENABLED` | MiniMax 开关（视觉备用） | `false` | |
| `MINIMAX_API_KEY` | MiniMax API 密钥 | — | 视觉备用时 |
| `MINIMAX_BASE_URL` | MiniMax Base URL | `https://api.minimaxi.com/v1` | |
| `MINIMAX_MODEL` | MiniMax 模型名 | `MiniMax-M3` | |
| `MINIMAX_FAST_MODEL` | MiniMax 高速模型 | `MiniMax-M2.7-highspeed` | |
| `SILICONFLOW_ENABLED` | 硅基流动开关（可选视觉） | `false` | 可选 |
| `SILICONFLOW_API_KEY` | SiliconFlow API 密钥 | — | 可选视觉时 |
| `SILICONFLOW_BASE_URL` | SiliconFlow Base URL | `https://api.siliconflow.cn/v1` | |
| `SILICONFLOW_MODEL` | SiliconFlow 视觉模型 | `Qwen/Qwen3.5-9B` | |
| **权限 & 默认值** | | | |
| `ADMIN_QQ` | 管理员 QQ 号（多个用中英文逗号分隔） | — | ⭕ |
| `DASHBOARD_TOKEN` | Dashboard 访问密码（空则自动生成随机 token） | — | 推荐 |
| `DEFAULT_SKILL` | 启动默认角色 Skill 名 | `makise-kurisu` | |
| `ACTIVE_LLM_PROVIDER` | 启动主选 Provider（`deepseek` / `minimax`） | `deepseek` | |
| **行为** | | | |
| `ACTIVE_HOURS_START` / `ACTIVE_HOURS_END` | Bot 活跃时段（小时 0-23） | `0 / 23`（全天） | |
| `WEB_SEARCH_ENABLED` | 联网搜索开关 | `true` | |
| `AUTO_REPLY_GROUP_IDS` | **已移除（历史项）**：白名单群号（多群逗号分隔）；代码不再读取，旧 `.env` 留着不影响启动 | 空 | 历史项 |
| `AUTO_REPLY_GROUP_PROBABILITY` | **已移除（历史项）**：白名单群自动回复概率（0~1）；2026-09 起群聊必须 @ 或 `/` 命令 | `0.0`（关） | 历史项 |
| **推特抓取（可选）** | | | |
| `TWITTER_USERNAME` | X 账号 | — | 不用 Cookie 时 |
| `TWITTER_PASSWORD` | X 密码 | — | 不用 Cookie 时 |
| `TWITTER_EMAIL` | X 邮箱 | — | 不用 Cookie 时 |

## 运行时参数（Dashboard「参数设置」可调，无需重启，写入 `qqbot/data/runtime_settings.json`）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `active_llm_provider` | 当前主选对话 Provider（`deepseek` / `minimax`） | `deepseek` |
| `stream_enabled` | 流式输出开关 | `true` |
| `stream_flush_chars` | 流式累积字符数阈值（分段触发） | `60` |
| `stream_flush_interval` | 流式最长发送间隔（秒，到时间即使不满字符也发） | `8.0` |
| `stream_flush_min_chars` | 流式最小发送字符数（低于此再等下一批） | `80` |
| `stream_time_grace_seconds` | 流式尾部宽容等待秒数（最后一个 flush 前延后） | `1.5` |
| `stream_max_flush_size` | 单次最大发送字符数（超长在此截断分段） | `300` |
| `max_history_rounds` | 最大对话轮数（超过从尾部丢弃） | `100` |
| `history_ttl_hours` | 对话历史过期时间（小时，超出后失效清空） | `36` |
| `history_save_interval` | 历史持久化到磁盘间隔（秒） | `60` |
| `thinking_timer_seconds` | "思考中…"提示延迟发送秒数（避免很快回复还显示思考中） | `120` |
| `multi_turn_enabled` | 多轮对话 / 记忆开关（关了每条消息都当新会话） | `true` |
| `test_mode_enabled` | 测试模式（仅私聊触发回复，群里静默拦截不响应） | `true` |
| `image_handler_enabled` | 多模态识图开关（关则所有含图消息跳过失图） | `true` |
| `active_hours_start` / `active_hours_end` | 活跃时段 | `0 / 23` |
| `web_search_enabled` | 联网搜索开关 | `true` |
| `llm_temperature` | 采样温度 | `0.95` |
| `llm_top_p` | 核采样 p | `0.9` |
| `llm_max_tokens` | 单次最大输出 tokens | `16384` |
| `llm_reasoning_effort_with_tools` | 带工具调用时的思考强度（`low` / `medium` / `high`） | `high` |
| `llm_reasoning_effort_no_tools` | 纯文本回复的思考强度 | `high` |
