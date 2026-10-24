# 📖 使用说明

本文档涵盖：

- [Bot 命令](#bot-命令)
- [触发规则](#触发规则)
- [Dashboard 管理面板](#dashboard-管理面板)
- [流式输出调参](#流式输出调参)
- [情绪表情系统](#情绪表情系统)
- [角色切换](#角色切换)
- [联网搜索与识图](#联网搜索与识图)
- [语料采集与 Skill 蒸馏](#语料采集与-skill-蒸馏)

---

## Bot 命令

私聊直接发消息；群聊普通聊天需要 `@bot`，以 `/`、`／` 开头的命令在群里不用 @ 也能触发。

管理员由 `private\.env` 的 `ADMIN_QQ` 指定（当前聊天命令按**单个 QQ 号**匹配，建议只填一个）。未配置时，管理员专属命令会被拒绝。

| 命令 | 别名 | 说明 | 权限 |
|---|---|---|---|
| `/skills` | `/角色`、`/列表` | 查看所有可用角色 | 所有人 |
| `/switch <角色名>` | `/切换` | 切换角色（热切换，无需重启） | 仅管理员 |
| `/current` | `/当前` | 查看当前角色、Provider / Model | 所有人 |
| `/reset` | `/重置`、`/清空记忆` | 清空对话记忆（保留用户画像） | 所有人 |
| `/reloademoji` | `/重载表情` | 热重载表情文件 | 仅管理员 |
| `/testmode on\|off` | `/测试模式` | 开关测试模式（不带参数时查看状态） | 仅管理员 |

> - `/switch` 后面要写**角色目录名**（如 `/switch makise-kurisu`），不是 `SKILL.md` 里的 `display_name`。
> - 角色名区分大小写，写错会返回当前可用角色列表。
> - 非管理员发 `/switch`、`/reloademoji`、`/testmode` 只会收到拒绝提示，不会执行。

---

## 触发规则

### 群聊

- **普通聊天**：必须 `@Bot` 才会进入处理流程
- **命令**：以 `/` 或 `／` 开头可绕过 @ 限制（群聊里发命令不需要 @）
- **白名单群按概率自动接话**：已于 2026-09 移除；`AUTO_REPLY_GROUP_IDS` / `AUTO_REPLY_GROUP_PROBABILITY` 仍能解析但不再生效，旧 `.env` 留着不影响启动

### 私聊

- 规则层一律放行；命令 / 切角色 / 改设置等受限操作在命令处理层做管理员校验
- 如果当前角色在 `SKILL.md` 里声明了 `allowed_users`，只有白名单里的 QQ 能触发该角色
- 注意：`test_mode_enabled` 开启时，非管理员私聊还会被测试模式拦截（见下）

### 测试模式（test_mode）

`test_mode_enabled` **默认开启**，行为：

| 场景 | 开启时 | 关闭时 |
|---|---|---|
| 管理员（私聊 / 群聊） | 正常回复 | 正常回复 |
| 普通用户私聊 | 拦截，回复「Bot 当前在维护中」提示 | 正常回复 |
| 普通用户群聊 | 静默拦截（日志打 `group silent`，用户侧无反应） | 正常按 @ / 命令触发 |

新手刚装好时默认开着，避免群里一堆人 @ 把额度打没。给朋友用之前记得关掉：

- Dashboard → 参数设置 → 取消勾选 `test_mode_enabled` → 保存，或
- 管理员发 `/testmode off`

---

## Dashboard 管理面板

访问 `http://localhost:8501`，输入 `.env` 中设置的 `DASHBOARD_TOKEN` 登录。

> - `DASHBOARD_TOKEN` 留空时，后端会随机生成一个并打印到 Dashboard 日志（`qqbot\logs\dashboard.out` / `dashboard.err`），用该 token 登录即可。
> - 除首页 / 静态资源 / `/api/health` / `/api/ws-health` 等白名单外，`/api/*` 需要 `X-Dashboard-Token` header 或 `?token=` query。
> - EventSource（SSE）通过 `?token=...` query 传递鉴权。

Dashboard 共 8 个页面：进程管理 / Cookie / 语料库 / 技能管理 / 日志 / 帮助文档 / 参数设置 / 对话流水。

> 架构：后端 FastAPI + 8 个业务 router（process / turns / corpus / skills / settings / docs / logs / ws），前端原生 HTML/CSS/JS（17 个 js + 6 个 css，8 套 CSS 变量主题热切换）。

### 1. 进程管理

**KPI 总览**（顶栏 pill）：系统 / NoneBot2 / SnowLuma / WebSocket 实时状态。

| 功能 | 说明 |
|---|---|
| **启动 / 停止** | 启停 NoneBot2 / SnowLuma 进程，启动前自动清理占用端口的旧进程（避免 Errno 10048） |
| **Dashboard 自管理** | 显示 Dashboard 自身 PID / 内存 / 运行时间，支持网页一键重启 |
| **状态探测** | 顶栏 / KPI 定时刷新进程与 WebSocket 状态，页面隐藏时自动暂停轮询 |
| **角色花名册** | 显示当前所有角色 Skill 卡片 + 头像预览 |
| **full-restart** | 一键重启 Dashboard + Bot + SnowLuma 全部进程 |

### 2. Cookie 管理

| 项 | 说明 |
|---|---|
| 微博 Cookie | 粘贴浏览器登录后的 Cookie 字符串，页面显示有效 / 过期状态 |
| 推特 Cookie | 单独管理；支持上传浏览器导出的 `twitter_cookies.json` |

### 3. 语料库

**多源语料采集**：

| 来源 | 说明 |
|---|---|
| **微博抓取** | 输入用户 UID（如 `weibo.com/u/1234567890` 里的 `1234567890`），自动下载微博内容到 `corpus/<UID>/` |
| **QQ 群聊导入** | 上传导出的聊天记录文件，系统自动解析 |
| **推特抓取** | 输入 screen name，自动保存 `corpus/twitter_<name>/`（`tweets_raw.json` + `corpus.txt`） |
| **文本导入** | 直接粘贴文本 |

**Skill 蒸馏**：

抓取完成后点「生成技能」，选择角色类型（名人 / 同事 / 亲密关系），系统调用 DeepSeek 自动生成 `SKILL.md` + `persona.md` + `work.md`，落到 `qqbot\characters\<新角色名>/`。

### 4. 技能管理

| 操作 | 说明 |
|---|---|
| **列表 / 编辑** | 显示所有角色 Skill，支持在线编辑 |
| **蒸馏** | 从语料生成 7 层结构 persona |
| **创建** | 手动填写角色信息，自动生成模板文件 |
| **批量删除** | 多选 + 一键删除（仅删除磁盘文件，不影响运行时） |
| **排序 / 筛选** | 按名称 / 创建时间 / 版本号 / 大小排序 + 实时搜索 |
| **头像自动加载** | 用 SSR `init.avatars` 填充 `_PHOTOS`，渲染即可见 |

### 5. 日志查看

| 文件 | 内容 | 查看时机 |
|---|---|---|
| `nonebot2.log` | Bot 全部输出（启动 / Skill 加载 / API 调用 / 错误堆栈） | Bot 启动失败、API 报错、功能异常 |
| `chat.log` | 对话事件（用户消息 / Bot 回复 / 搜索触发 / 流式分段） | 查看聊天记录、确认搜索是否触发 |
| `snowluma.log` | SnowLuma QQ 协议层日志 | QQ 消息未收到 / 未发出、连接断开 |
| `dashboard.log` | Dashboard 请求日志 | API 401 / 500 排查、token 鉴权问题 |

**功能**：自动清理 ANSI 颜色代码 + 自动滚动 + 支持选择不同日志文件。

turn 流与单 turn 快照也在 `qqbot\logs\` 下（`turns_stream.jsonl` + `turns\<date>\*.json`），见「对话流水」。

### 6. 帮助文档

文档目录侧边栏 + Markdown 渲染（自动读取根目录 `README.md` + `docs/` 下的 `.md` 文件）。

### 7. 参数设置

**运行时参数**（Dashboard 可调，无需重启，写 `qqbot\data\runtime_settings.json`，bot 热加载）：

| 参数 | 说明 | 默认 |
|---|---|---|
| `active_llm_provider` | 当前主选对话 Provider（`deepseek` / `minimax`） | `deepseek` |
| `stream_enabled` | 流式输出开关 | `true` |
| `stream_flush_chars` | 流式累积字符数阈值 | `60` |
| `stream_flush_interval` | 流式最长发送间隔（秒） | `8.0` |
| `stream_flush_min_chars` | 最小累积字符数 | `80` |
| `stream_time_grace_seconds` | 流式尾部宽容等待（秒） | `1.5` |
| `stream_max_flush_size` | 单次最大发送字符数 | `300` |
| `max_history_rounds` | 最大对话轮数 | `100` |
| `history_ttl_hours` | 对话历史过期时间（小时） | `36` |
| `history_save_interval` | 历史持久化间隔（秒） | `60` |
| `thinking_timer_seconds` | 「思考中」提示延迟（秒） | `120` |
| `multi_turn_enabled` | 多轮对话开关 | `true` |
| `test_mode_enabled` | 测试模式（仅私聊触发，群里静默） | `true` |
| `image_handler_enabled` | 多模态识图总开关 | `true` |
| `active_hours_start` / `active_hours_end` | Bot 活跃时段（小时） | `0` / `23` |
| `web_search_enabled` | 联网搜索开关 | `true` |
| `llm_temperature` | 采样温度（作用于当前主选 Provider） | `0.95` |
| `llm_top_p` | 核采样 | `0.9` |
| `llm_max_tokens` | 单次最大输出 | `16384` |
| `llm_reasoning_effort_with_tools` | 带工具调用时的思考强度 | `high` |
| `llm_reasoning_effort_no_tools` | 纯文本时的思考强度 | `high` |

> 旧版 `minimax_*` 参数已通用化为 `llm_*`（2026-07-23），旧的键仍保留向后兼容读写。

### 8. 对话流水（turn 流）

通过 SSE 实时推送每个 turn 的完整步骤：

- **状态流转**：排队 → 探测 → LLM 调用 → 思考 → 工具调用 → 工具结果 → 流式分块 → 回复
- **元信息**：用户 QQ、群 ID、角色、Provider / Model、工具调用数、回复字符数、耗时
- **聚合统计**：总 turn 数 / 成功率 / 空回复率 + Provider + Skill + 高频用户分布
- **筛选搜索**：按状态 / 关键字（用户文本 / UID / 群 ID / 角色名）过滤

文件落地在 `qqbot\logs\turns_stream.jsonl`（追加写），单个 turn 的步骤快照在 `qqbot\logs\turns\<date>\*.json`。

---

## 流式输出调参

**原理**：bot 先发一次非流式「探测」判断是否需要工具调用 → 普通文本则切流式 + 分段发送。

**调参建议**：

| 场景 | 调整 |
|---|---|
| bot 回复太碎（一句话分 5+ 段） | 调大 `stream_flush_min_chars`（如 120）+ 调大 `stream_flush_chars`（如 80） |
| bot 回复累积太久才发 | 调小 `stream_flush_interval`（如 5.0） |
| 单段太长刷屏 | 调小 `stream_max_flush_size`（如 200） |
| 想关闭流式（一口气发完整回复） | `stream_enabled=false` |

---

## 情绪表情系统

Bot 根据用户消息和回复里的关键词自动匹配情绪，并发送对应表情图片。

### 触发流程

1. bot 生成回复后，用用户消息 + 回复文本匹配各情绪目录的关键词
2. 命中后在 `emoji/<情绪>/` 目录随机选一张图
3. 50% 概率发送 + 同用户 60 秒冷却（避免刷屏）

### 10 种情绪分类

`angry / curious / happy / laugh / proud / sad / shy / surprised / thinking / tired`

### 添加表情图片

```bat
emoji/
├── angry/
│   ├── keywords.txt       # 触发词（已含）
│   ├── 1.jpg              # ← 添加你的表情图
│   └── 2.gif
├── happy/
│   ├── keywords.txt
│   └── ...
```

支持 GIF / JPG / PNG / WebP。完成后运行 `/reloademoji`（仅管理员）热重载，或重启 bot。

> `.gitignore` 只提交 `keywords.txt` 结构，表情图片（`*.gif` / `*.jpg` / `*.png` 等）默认不进公开仓库。

### 触发词配置

每个情绪子目录下的 `keywords.txt` 定义了哪些词触发该情绪，格式：每行一个词或短语，`#` 开头为注释。没有 `keywords.txt` 时使用代码内置的默认关键词。修改后用 `/reloademoji` 热加载。

> 某个角色不想发表情图，可以在它的 `SKILL.md` front matter 写 `emoji_images: false`。

---

## 角色切换

### 热切换流程

1. 管理员发 `/switch <角色名>`（如 `/switch makise-kurisu`）
2. bot 立即加载新角色 persona，下次回复即生效
3. 所有用户从下一条消息起看到新角色
4. 历史对话记忆**保留**（按角色 ID 隔离）

### 多角色并存

bot 同时加载所有 `qqbot\characters\<角色名>\` 下的角色（首次启动 + 收到消息时扫描）。

> 公开仓库只附带 `makise-kurisu` / `amadeus-kurisu` 两个动漫示例角色；其余角色都在本地维护，`.gitignore` 已排除，不会被公开。

### 查看当前角色

`/current` → 显示角色名 + Provider + Model。

---

## 联网搜索与识图

### 联网搜索

当用户消息包含搜索意图关键词（如「查一下」「最新」「是谁」）或 URL 时自动触发：

- **百度**：唯一通用搜索引擎
- **微博单帖正文 / 微博热搜**：做增强补充（免 Cookie）
- 带相关性过滤 + 健康检查 + 自动降级；最多执行 3 轮工具调用

可在 `.env` 用 `WEB_SEARCH_ENABLED=false` 关闭，或在 Dashboard 参数设置动态调整。

### 多模态识图

用户发图时自动识别图内容并自然回应：

- 默认视觉 Provider：`deepseek-vision`（`deepseek-v4-flash-vision-exp`，复用 `DEEPSEEK_API_KEY`）
- 可选 / 备用：SiliconFlow Qwen3.5-9B → MiniMax M3（前一家不可用时才按优先级启用）
- 多轮历史里只存文字占位符，不重复传图
- Dashboard 参数设置里的 `image_handler_enabled` 是识图总开关，临时关掉不会报错

---

## 语料采集与 Skill 蒸馏

### 1. 抓取微博

1. **获取 Cookie**：
   - 浏览器登录 [weibo.com](https://weibo.com)
   - F12 → Network → 任意请求 → 复制 `Cookie:` 整行
2. **上传 Cookie**：Dashboard → Cookie 页粘贴 → 保存
3. **抓取**：Dashboard → 语料库 → 输入目标用户 UID（如 `weibo.com/u/1234567890` 里的 `1234567890`）→ 抓取
4. 输出：`corpus/<UID>/weibo_ajax_page*.json` + `corpus.txt`

### 2. 抓取推特

**方式 A（推荐）**：浏览器 cookies

1. 浏览器登录 [x.com](https://x.com)
2. 安装 [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm) 扩展
3. 扩展 → Export → JSON → 保存
4. Dashboard → 语料库 → 推特抓取 → 上传 cookies → 输入 screen name → 抓取

**方式 B**：账号密码自动登录（在 `private\.env` 加 `TWITTER_USERNAME/PASSWORD/EMAIL`，首次抓取时自动登录保存 cookies）

**抓取选项**：

| 选项 | 说明 |
|---|---|
| 抓取条数 | 默认 200，可设 1-5000（被 X 限流时建议 50-100） |
| 抓取全部 | 翻页到无数据为止（可能耗时数十分钟） |
| 同时抓取本人回复 | 抓 Tweets + Replies 两类，合并输出 |

**输出位置**：`corpus/twitter_<screen_name>/`

- `corpus.txt` — 可读格式（互动数据 + 正文 + 回复对象 + 转推来源）
- `tweets_raw.json` — 完整结构化数据（用户信息 / 推文元数据 / 社区笔记 / 投票）

**命令行使用**：

```bash
python scripts/fetch_twitter.py elonmusk --count 100
python scripts/fetch_twitter.py elonmusk --all
python scripts/fetch_twitter.py elonmusk --all --replies
```

### 3. 蒸馏 Skill

抓取完成后，在语料旁的「生成技能」按钮：

1. 选择角色类型（名人 / 同事 / 亲密关系）
2. 系统调用 DeepSeek 自动生成 7 层结构 persona
3. 输出到 `qqbot\characters\<新角色名>/`，含 `SKILL.md` + `persona.md` + `work.md`

详见 [docs/SKILL_AUTHORING.md](SKILL_AUTHORING.md)。

> `corpus/`、`qqbot\characters\`（示例角色除外）、`photo/` 都在 `.gitignore` 里，只保留在本地。
