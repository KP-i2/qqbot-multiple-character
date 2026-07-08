# 🔒 隐私 + 安全

## ⚠️ 绝对不能进仓库的内容

`.gitignore` 已经在防，但 PR review 时仍要人工核对：

| 类型 | 例子 | 为什么 |
|---|---|---|
| **API keys** | `sk-xxx` / `sk-cp-xxx` / `AQ.Axxx` | 别人能用你的额度 |
| **真实 QQ 号** | 你自己的 QQ / 好友 QQ / 群号 | 隐私 + 防止骚扰 |
| **真实 OneBot 号** | SnowLuma 配置文件里的 QQ 号 | 同上 |
| **你的真实角色目录** | `qqbot/skills/{aoyi,dot_yuu,...,zyw}/` | 圈子隐私 / 人物分析泄露 |
| **`private/.env`** | 真实 API Key + 管理员 QQ | 上面两类一起 |
| **`private/cookies.json`** | 微博登录 Cookie | 别人能用你账号发微博 |
| **`private/twitter_cookies.json`** | 推特登录 Cookie | 别人能用你账号发推 |
| **`corpus/*/tweets_raw.json`** | 推特原文（含真人用户名 / 头像 URL） | 真人隐私 |
| **`corpus/*/corpus.txt`** | 微博 / 推特合并后的语料文本 | 真人隐私 |
| **QQ 群聊记录** | 任何导出的聊天记录（含真人头像 / 昵称 / QQ 号） | 真人隐私 |

> ✅ 安全示例：动漫 / 游戏角色（牧瀬紅莉栖 / 桐人 / 初音 等）的同人语料 —— 公开可见，可入库  
> ⚠️ 危险示例：真人博主 / 偶像 / 同事的微博 / 推特 —— **绝对不要入库**，即使脱敏也不行（容易反向关联）

---

## 发布前自检：`scripts/sanitize.py`

仓库自带 `python scripts/sanitize.py --check-only` 脚本，发布前跑一遍：

```bash
python scripts/sanitize.py --check-only
```

**会扫描**：

- 整个仓库里所有 `sk-` 开头的字符串（API key 模式）
- `private/.env` 是否存在
- `qqbot/skills/` 下除了 `makise-kurisu/` 外的目录名（提示你可能漏本地角色）
- `corpus/` 下典型真人 ID 模式（数字 QQ 号 / 推特 handle 长度异常）
- `*.log` 文件是否被提交
- `*.pid` 文件是否被提交

发现任何 issue 立即报错并退出非 0。

---

## 隐私目录设计

本项目刻意把隐私配置放在 `private/` 而不是项目根 `qqbot/`，三个原因：

1. **bot 代码读取路径一致** —— `qqbot/bot.py` 显式从 `private/.env` 读，发布版和本地版本对齐
2. **整目录 gitignore** —— `private/` 整个目录都被忽略（除 `.env.example` 和 `.gitkeep`），漏一个文件就漏
3. **未来扩展方便** —— 后续加 `private/database/` / `private/secrets/` 同样安全

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

### 必填项

| Key | 用途 | 申请 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 主对话 Provider | https://platform.deepseek.com |
| `MINIMAX_API_KEY` | 图片描述 Provider（强烈建议） | https://api.minimaxi.com |

### 安全实践

1. **不要 commit** —— `private/.env` 整体 gitignore
2. **不要贴 Issue** —— 复制 `sk-xxx` 时手动改 `sk-your-key-here`
3. **定期轮换** —— API 平台被泄露 / 离职 / 项目交接时主动 revoke + 重发
4. **不要用主账号** —— 给 bot 单独建一个 API key（部分平台支持子账号 / 项目隔离）
5. **余额监控** —— 订阅 API 提供商的用量告警，避免被刷爆

---

## 用户数据隐私

bot 运行时会生成以下数据，**全部自动 gitignore**（在 `qqbot/data/`）：

| 文件 | 内容 | 处理 |
|---|---|---|
| `conversation_histories.json` | 对话历史（含用户 QQ / 群 ID / 内容） | gitignore，按 `history_ttl_hours` 自动清理 |
| `user_profiles/<QQ>.json` | 用户画像（称呼偏好 / 兴趣话题） | gitignore |
| `turns_stream.jsonl` | turn 流日志（含完整 prompt / response） | gitignore，可手动删 |
| `runtime_settings.json` | Dashboard 参数（不含敏感信息） | gitignore |
| `search_health.json` | 搜索引擎健康状态 | gitignore |

### 用户撤回请求

如果用户要求"删除我的对话历史"，运行：

```python
# 在 skill_qqbot\Scripts\python.exe 解释器下
import json
from pathlib import Path

HISTORY = Path("qqbot/data/conversation_histories.json")
data = json.loads(HISTORY.read_text(encoding="utf-8"))

# 删除指定 QQ 的全部历史(把 <QQ 号> 替换为目标用户 QQ)
target_qq = "<QQ 号>"
data = {k: v for k, v in data.items() if target_qq not in k}

HISTORY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

用户画像同理（`qqbot/data/user_profiles/<QQ>.json`）。

---

## 常见问题

**Q: 我 commit 时忘了 .gitignore，怎么办？**  
A:

1. 立即从 git 历史里删（`git rm --cached <path>` + `git commit --amend` 如果没 push）
2. 如果已 push：考虑 `git filter-branch` 或 `BFG Repo-Cleaner` 重写历史
3. **轮换所有已泄露的 API key / cookies**

**Q: .gitignore 拦不住的怎么办？**  
A: `python scripts/sanitize.py --check-only` 是兜底 —— 即使 .gitignore 配错了，sanitize 也能扫到。

**Q: 我的真实 QQ 号不小心 commit 了？**  
A: QQ 号不像 API key 可轮换 —— 但可以 `git filter-branch` 重写历史 + force push。重要数据外泄预防为主。

**Q: 我想给朋友分享我的角色怎么办？**  
A: 不要直接发 `qqbot/skills/<角色名>/` 整个目录 —— 里面可能含用户画像引用 + 上下文。先脱敏：

1. 删除 persona.md 里 Layer 1 / 5 里"用户叫 XX"这类具体人名引用
2. 删 `corpus_ref/`（可能含你圈子聊天记录引用）
3. 删 `work.md` 里跟特定用户绑定的功能

或者让朋友自己从语料蒸馏（10 分钟造新角色，比分享你的快）。

---

## 报告安全问题

发现安全漏洞 / 隐私泄露风险：

- **GitHub Issues**：[https://github.com/KP-i2/qqbot-multiple-character/issues](https://github.com/KP-i2/qqbot-multiple-character/issues)（公开报告）
- 或直接联系维护者（见仓库主页）

**请不要在 Issue 里贴 API key / 真实 QQ 号** —— 任何"我删了前缀 sk-" 的也算泄露。