# 拟人化说话（调研 + 落地）

> 2026-11-10。目标：角色说话更像真人，不像客服/写作助手；回复允许慢，模拟“想得多、回得慢”。

## 大肥鱼生态里有什么能用的

| 项目 | 是什么 | 能不能直接用 |
|---|---|---|
| [dafeiyu-qq-companion](https://github.com/ychenfen/dafeiyu-qq-companion) | QQ 电子恋人提示词工程：L0/L1/L2 三层热插拔、`packs/huorengan.md` 去 AI 味、短句/双态句法 | **提示词可直接借**；工程是 AstrBot/NapCat，不是 NoneBot |
| [whale_persona_plugin](https://github.com/a251231/whale_persona_plugin) | LangBot 的“活人感全家桶”：单/双气泡、打字停顿、防句首复读、情绪/记忆 | 规则可借；LangBot 插件不能装进 NoneBot |
| [dsh-humanizer](https://github.com/DEEP-IOS/dsh-humanizer) | DSH 写作插件：动笔前“成为作者”，去 AI 味 | **写文章**用的，不适合聊天；不装 |
| [dsh-tool-humanize](https://www.npmjs.com/package/dsh-tool-humanize) | DSH 的 `humanize_text` 改写工具 | 同上，聊天不适用 |
| [dsh-fish-soul](https://github.com/Britneycode/dsh-fish-soul) | DSH 单角色鱼：人格/记忆/主动开口/群聊插话/表情包 | DSH 插件，不能直接装 NoneBot；行为可参考 |

结论：**没有能直接装进 NoneBot2 的“拟人插件”**。最值得抄的是 `dafeiyu-qq-companion` 的 `huorengan.md` 和 `whale_persona_plugin` 的气泡/防复读规则。

## 已落地

1. **共享提示词** `normal-paper/basic/humanlike.md`（本地 gitignore；模板在 `normal-paper/examples/humanlike.example.md`）
   - 说话不是写台词；禁止 `（...）`/`*...*` 舞台旁白
   - 不逐项回应；只抓一个细节；允许答偏/停顿/没说完
   - 不先“理解用户”、不命名情绪、不客服收尾
   - 情绪从语言节奏漏出来；可以累/烦/走神
   - 五种 AI 味出现即重写：镜像、全知、永远温柔、即时深刻、元评论
   - 日常短句、少句号；1–2 条；口癖/自称别复读
   - 日程/事实只用工具明确结果，不编
2. **回复预算放宽**
   - `LLM_TOTAL_TIMEOUT` 120 → **240s**
   - `LLM_REQUEST_TIMEOUT` 45 → **90s**
   - 不再追求“秒回”；思考和工具调用有时间，外层预算只做安全阀
3. 不用额外装 DSH 插件；现有 `chat_handler` 的分段 + 段间延迟保留，作为“人打字”的节奏。

## 怎么调

- 想让某个角色更冷/更碎：改它自己的 `persona.md`，不要改共享页
- 想全局更短：把 `humanlike.md` 的“1–2 条”改成“1 条”，并收紧句长
- 想恢复严格速度：在 `private/.env` 设 `LLM_TOTAL_TIMEOUT=120`、`LLM_REQUEST_TIMEOUT=45`
