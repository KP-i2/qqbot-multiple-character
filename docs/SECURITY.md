# 🔒 隐私 + 安全

本项目会把「代码 / 文档 / 模板」发布到公开仓库，而把「真实角色 / 语料 / 配置 / 运行时数据」全部留在本地。本文说明哪些内容绝不能进仓库、发布前怎么自检，以及用户数据的处理方式。

## ⚠️ 绝对不能进仓库的内容

`.gitignore` 已经在拦，但 PR review 时仍要人工核对：

| 类型 | 例子 | 为什么 |
|---|---|---|
| **API keys** | `sk-xxx` / `sk-cp-xxx` | 别人能用你的额度 |
| **真实 QQ 号 / 手机号** | 自己的 QQ、好友 QQ、群号、手机号 | 隐私 + 防止骚扰 |
| **真实 OneBot 号** | SnowLuma 配置里的机器人 QQ 号 | 同上 |
| **真实角色目录 / 昵称** | `qqbot/characters/<你的角色>/`（公开仓库只有 `makise-kurisu` / `amadeus-kurisu` 两个动漫示例） | 圈子隐私 / 人物分析泄露 |
| **`private/` 整个目录** | `private/.env`、`private/cookies.json`、`private/twitter_cookies.json` | API Key + 登录态 |
| **`corpus/`** | `corpus/<UID>/`、`corpus/twitter_<name>/` 等真人文库 | 真人隐私 |
| **`normal-paper/` 私有部分** | 只公开 `README.md` + `examples/`；其余共享背景 / 人物分析只留本地 | 真人隐私 |
| **`photo/`** | 角色头像源目录 | 真人照片 |
| **`emoji/` 图片** | 只提交 `keywords.txt` 结构；表情图（`*.gif` / `*.jpg` / `*.png` 等）忽略 | 真人素材 / 版权风险 |
| **`qqbot/data/`** | `conversation_histories.json`、`user_profiles/`、`runtime_settings.json` | 用户数据 |
| **`qqbot/logs/`** | `chat.log`、`nonebot2.log`、`turns_stream.jsonl`、`turns/` | 含对话原文 / 用户 QQ |
| **`qqbot/snowluma_onebot_config.json` 真值** | 只提交 `qqbot/snowluma_onebot_config.example.json` 模板 | 含 QQ 号 / accessToken |
| **`snowluma/`** | QQ 协议端运行时（`node.exe` / `config/` / `data/`） | 登录态 + 消息库 |
| **`archive/` / `.work/` / `.pids/` / `.runtime/` / `skill/` / 顶层 `logs/`** | 历史归档、临时改写稿、PID、第三方 Skill 参考、旧日志 | 本地隐私 / 临时内容 |
| **QQ 群聊记录** | 任何导出的聊天记录（含真人头像 / 昵称 / QQ 号） | 真人隐私 |

> ✅ 安全示例：动漫 / 游戏角色的同人语料 —— 公开可见，可入库
> ⚠️ 危险示例：真人博主 / 偶像 / 同事的微博 / 推特 —— **绝对不要入库**，即使脱敏也不行（容易反向关联）

### 公开仓库的边界

发布版仓库只包含：

- 代码、脚本、文档、模板
- `qqbot/characters/README.md` + 2 个 anime 示例角色（`makise-kurisu/`、`amadeus-kurisu/`）
- `normal-paper/README.md` + `normal-paper/examples/`
- `emoji/*/keywords.txt`（不含图片）
- 配置模板 `qqbot/.env.example` / `qqbot/snowluma_onebot_config.example.json`

其余全部由 `.gitignore` 排除，只留本地。

---

## 发布前自检：`scripts/sanitize.py`

仓库自带一个本地脱敏辅助脚本：

```bash
# 只跑自检：确认当前工作区没有命中 CONTENT_RULES 的残留
python scripts/sanitize.py --check-only

# 预览：内容替换 + 文件黑名单删除（不实际改动）
python scripts/sanitize.py --dry-run

# 真跑：改文件 + 删除黑名单目录/文件 + 自检
python scripts/sanitize.py --apply
```

**注意**：

- `CONTENT_RULES` 与 `BLACKLIST_DIRS` 是示例规则，发布前按自己的敏感清单维护；不要把真实 QQ 号 / 社交 handle 写回仓库。
- `--apply` 会**真实删除**黑名单目录（`private/`、`corpus/`、`photo/`、`snowluma/`、`archive/` 等），请只在发布分支或干净副本上执行，不要直接在含私有数据的主工作区跑。
- 脚本只做兜底。最终以 `git status` + `git diff --cached` + 人工 review 为准。

### 推荐发布检查清单

1. `git status` —— 只包含想发布的文件
2. `git ls-files` —— 不应出现 `private/`、`corpus/`、`photo/`、`snowluma/`、`qqbot/data/`、`qqbot/logs/`、真实角色目录
3. 全文搜索真实 QQ 号 / 昵称 / 社交 handle / 本机绝对路径
4. `python scripts/sanitize.py --check-only` 复核
5. 确认 `.env`、Cookie、`onebot_<QQ>.json` 真值没有出现在 diff 里

---

## 隐私目录设计

本项目刻意把隐私配置放在 `private/` 而不是项目根 `qqbot/`，三个原因：

1. **bot 代码读取路径一致** —— `qqbot/bot.py` 显式从 `private/.env` 读，发布版和本地版本对齐
2. **整目录 gitignore** —— `private/` 整个目录都被忽略，漏一个文件就漏
3. **模板单独放** —— `qqbot/.env.example`、`qqbot/snowluma_onebot_config.example.json` 是公开模板，真值配置永远不进仓库

---

## Cookie 管理

### 微博 Cookie

**获取方式**（不要 commit）：

1. 浏览器登录 [weibo.com](https://weibo.com)
2. F12 → Network → 任意请求 → 复制 `Cookie:` 整行
3. Dashboard → Cookie 页粘贴 → 保存（自动写 `private/cookies.json`）

**安全提醒**：

- 不要把 cookies 贴到群聊 / Issue / PR 评论
- 定期更换 Cookie（微博风控会过期）
- 不要用同一个 Cookie 同时跑 bot + 自己浏览器（容易被风控踢下线）

### 推特 Cookie

**获取方式**：

1. 浏览器登录 [x.com](https://x.com)
2. 安装 [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm) 扩展
3. 扩展 → Export → JSON → 保存
4. Dashboard → 语料库 → 推特抓取 → 上传（自动写 `private/twitter_cookies.json`）

或方式 B：`private/.env` 加 `TWITTER_USERNAME/PASSWORD/EMAIL`，首次抓取自动登录保存。

---

## API Key 管理

### 必填 / 可选

| Key | 用途 | 申请 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 主对话 Provider；默认视觉 `deepseek-vision` 复用同一个 Key | https://platform.deepseek.com |
| `SILICONFLOW_API_KEY` | 可选视觉 Provider（Qwen3.5-9B） | https://cloud.siliconflow.cn |
| `MINIMAX_API_KEY` | 视觉备用 Provider | https://api.minimaxi.com |

> 默认配置只要求 `DEEPSEEK_API_KEY`；SiliconFlow / MiniMax 是可选兜底，默认关闭。

### 安全实践

1. **不要 commit** —— `private/.env` 整体 gitignore
2. **不要贴 Issue** —— 复制 `sk-xxx` 时手动改 `sk-your-key-here`
3. **定期轮换** —— API 平台被泄露 / 离职 / 项目交接时主动 revoke + 重发
4. **不要用主账号** —— 给 bot 单独建一个 API key（部分平台支持子账号 / 项目隔离）
5. **余额监控** —— 订阅 API 提供商的用量告警，避免被刷爆

---

## 用户数据隐私

bot 运行时会生成以下数据，**全部自动 gitignore**：

| 文件 | 内容 | 处理 |
|---|---|---|
| `qqbot/data/conversation_histories.json` | 对话历史（含用户 QQ / 群 ID / 内容） | gitignore，按 `history_ttl_hours` 自动清理 |
| `qqbot/data/user_profiles/<QQ>.json` | 用户画像（称呼偏好 / 兴趣话题） | gitignore |
| `qqbot/data/runtime_settings.json` | Dashboard 参数（不含敏感信息） | gitignore |
| `qqbot/data/search_health.json` | 搜索引擎健康状态 | gitignore |
| `qqbot/logs/turns_stream.jsonl` | turn 流日志（含完整 prompt / response） | gitignore，可手动删 |
| `qqbot/logs/turns/<date>/*.json` | 单个 turn 的步骤快照 | gitignore，可手动删 |
| `qqbot/logs/chat.log` | 对话事件日志（用户消息 / 回复） | gitignore，自动轮转 |

### 用户撤回请求

如果用户要求「删除我的对话历史」，在项目根目录用 `botenv` 的 `python.exe` 执行：

```python
import json
from pathlib import Path

HISTORY = Path("qqbot/data/conversation_histories.json")
data = json.loads(HISTORY.read_text(encoding="utf-8"))

# 删除指定 QQ 的全部历史（把 123456789 替换为目标用户 QQ）
target_qq = "123456789"
data = {k: v for k, v in data.items() if target_qq not in k}

HISTORY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

用户画像同理：删除 `qqbot/data/user_profiles/<QQ>.json`。turn 流属于追加日志，如需彻底清除，可停 bot 后删除 `qqbot/logs/turns_stream.jsonl` 与 `qqbot/logs/turns/`（或按 turn ID 定向删除）。

---

## 常见问题

**Q: 我 commit 时忘了 .gitignore，怎么办？**
A:

1. 立即从 git 历史里删（`git rm --cached <path>` + `git commit --amend` 如果没 push）
2. 如果已 push：考虑 `git filter-repo` / `git filter-branch` 或 `BFG Repo-Cleaner` 重写历史
3. **轮换所有已泄露的 API key / cookies**

**Q: .gitignore 拦不住的怎么办？**
A: 用 `python scripts/sanitize.py --check-only` + `git ls-files` 做兜底复核；发现敏感文件先 `git rm --cached`，再补 `.gitignore` 规则。

**Q: 我的真实 QQ 号不小心 commit 了？**
A: QQ 号不像 API key 可轮换 —— 但可以 `git filter-repo` 重写历史 + force push。重要数据外泄预防为主。

**Q: 我想给朋友分享我的角色怎么办？**
A: 不要直接发 `qqbot/characters/<角色名>/` 整个目录 —— 里面可能含用户画像引用 + 上下文。先脱敏：

1. 删除 `persona.md` 里 Layer 1 / 5 中「用户叫 XX」这类具体人名引用
2. 删 `corpus_ref/`（可能含你圈子聊天记录引用）
3. 删 `work.md` 里跟特定用户绑定的功能

或者让朋友自己从语料蒸馏（10 分钟造新角色，比分享你的快）。

---

## 报告安全问题

发现安全漏洞 / 隐私泄露风险：

- **GitHub Issues**：[https://github.com/KP-i2/qqbot-multiple-character/issues](https://github.com/KP-i2/qqbot-multiple-character/issues)（公开报告）
- 或直接联系维护者（见仓库主页）

**请不要在 Issue 里贴 API key / 真实 QQ 号** —— 任何「我删了前缀 sk-」的也算泄露。
