# ✨ 功能特性详解

各功能的完整行为、参数与边界条件。快速了解见 README「功能亮点」。


## 联网搜索

Bot 内置联网搜索，当用户消息包含搜索意图关键词（如"查一下"、"最新"、"是谁"）或 URL 时自动触发，无需手动开启。联网搜索**只走 DeepSeek 官方 Search API**（自研搜索引擎，2026-10-30 起）：返回带来源站点、发布日期、摘要的结果，按相关性/权威性/时效性重排；实测比旧版百度抓取更快更稳（0.2~0.4s，无正文污染）。搜索失败或 0 结果时模型直接说没查到，不再用微博热搜兜底、也不凭记忆硬编。搜索结果经 LLM 整合后以角色语气回复。

LLM 的 Function Calling 机制让模型自主决定是否搜索、搜什么，最多执行 3 轮工具调用。可通过 `.env` 中 `WEB_SEARCH_ENABLED=false` 关闭；`DEEPSEEK_SEARCH_COUNT`（默认 5）、`DEEPSEEK_SEARCH_TIMEOUT`（默认 10s）、`DEEPSEEK_SEARCH_SUMMARY_MAX`（默认 600）可调。

## URL 内容抓取

用户发送的消息中包含 URL 时，Bot 会自动抓取页面内容并注入上下文。支持的特殊链接包括：

- **Bilibili**：解析 BV 号，通过 API 获取视频标题、简介、播放量等信息
- **短链接**：自动解析 302 重定向获取真实地址
- **普通网页**：提取正文内容（去 HTML 标签，截断超长内容）

## 情绪表情系统

Bot 会根据对话内容的情绪自动发送对应的表情图片（GIF/JPG/PNG）。表情文件按情绪分类存放在 `emoji/` 目录下，每个子目录包含一个 `keywords.txt` 定义触发词。

当前支持 10 种情绪分类：angry、curious、happy、laugh、proud、sad、shy、surprised、thinking、tired。

触发规则：50% 概率触发，同一用户 60 秒冷却，避免刷屏。管理员可通过 `/reloademoji` 命令热重载表情文件。

## 流式输出

Bot 默认启用流式回复，先通过一次非流式"探测"判断是否需要工具调用，若为普通文本回复则切换为流式输出，分段发送。流式参数（累积字符数、间隔、最小字符数、最大单次大小）可通过 Dashboard「参数设置」页动态调整，无需重启。

## 多模态识图（默认 deepseek-vision）

用户给 bot 发图片时，bot 会**自动识别图内容**并自然回应（不区分表情包/截图/自拍）。

数据流：

```
QQ 图片消息 → snowluma (OneBot v11 image segment, 含 cdn url)
           → bot 提取 url → 先下载转 base64 → deepseek-vision 视觉理解
           → 输出 [图片描述: ...] → 注入上下文 → DeepSeek 处理回复
```

技术细节：

- **协议**：snowluma 推送的 image segment 通常自带 `url` 字段（QQ 临时签名 CDN）。视觉 Provider 优先级为 `deepseek-vision` → `SiliconFlow` → `MiniMax`，默认走 `deepseek-vision`（`deepseek-v4-flash-vision-exp`，复用 `DEEPSEEK_API_KEY`）。
- **默认转 base64**：DeepSeek 官方视觉模型让服务端 fetch 远程图不稳定（常见 400），所以命中 `deepseek-vision` 时一律先下载图片转 base64 data URI；`SiliconFlow` / `MiniMax` 保留直链策略。
- **历史策略**：多轮对话历史里**只存文字占位符**（如 `[用户上一条消息里发了一张图片]`），不带图，省 token。
- **降级兜底**：若 image segment 没有 url 字段（罕见），回退到本地下载 + base64；若全部视觉 Provider 都不可用，本次 turn 跳过失图。
- **不依赖浏览器**：纯 HTTP 协议，不需要 headless Chrome / playwright。

> LLM Provider 四轨架构、调用流程与错误分类见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 用户画像

Bot 会在对话过程中自动提取用户的简要画像（称呼偏好、兴趣话题等），作为上下文注入后续回复，让角色"记住"对方的特点。画像由 DeepSeek 抽取，定期更新，不影响对话性能。

## 通用背景知识

`normal-paper/` 目录下的 `.md` 文件会作为所有角色共享的领域背景知识自动注入 system prompt（**只注入 `basic/*.md`**）。适合存放圈子通用术语、礼仪规则、文化背景等不需要写进每个角色人设的参考资料，支持热重载。公开仓库只带 `normal-paper/README.md` 与 `examples/` 示例，真实内容留在本地。

## 对话流水（turn 流）

Dashboard 新增「对话流水」tab，通过 SSE 实时推送每个 turn 的完整步骤：

- **状态流转**：排队 → 探测 → LLM 调用 → 思考 → 工具调用 → 工具结果 → 流式分块 → 回复
- **元信息**：用户 QQ、群 ID、角色、Provider/Model、工具调用数、回复字符数、耗时
- **聚合统计**：总 turn 数 / 成功率 / 空回复率 / 进行中数 + Provider + Skill + 高频用户分布
- **筛选搜索**：按状态 / 关键字（用户文本、UID、群 ID、角色名）过滤

文件落地在 `qqbot/logs/turns_stream.jsonl`（追加写），单个 turn 的步骤快照在 `qqbot/logs/turns/<date>/*.json`；Dashboard 历史接口从 `turns_stream.jsonl` 尾部聚合并返回。
