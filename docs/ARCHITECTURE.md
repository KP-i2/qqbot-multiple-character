# 🏗️ 架构与实现细节

本文件承接 README 中的架构、Provider、Dashboard、目录结构、运行环境与技术栈细节。
两张图：[架构总览](architecture-overview.png) · [消息处理链路](message-flow.png)

## 架构

三个独立进程 + 一个业务插件的整体结构：

```
QQ 客户端 ←→ SnowLuma (OneBot v11) ←WebSocket→ NoneBot2 (port 8080)
                                                  ↕
                                               char_chat 插件
                                               ├─ DeepSeek V4-Pro (默认主对话,处理所有对话 / 工具 / 流式 / 用户画像)
                                               ├─ deepseek-vision (默认图片描述 Provider,deepseek-v4-flash-vision-exp)
                                               ├─ SiliconFlow Qwen3.5-9B (可选图片描述 Provider,视觉转文字)
                                               ├─ MiniMax M3 (视觉备用 Provider)
                                               ├─ 百度 + 微博(单帖/热搜) 联网搜索
                                               └─ 流式输出 + Function Calling

Dashboard (port 8501) ←→ 进程管理 / 微博+推特语料采集 / Skill 蒸馏 / 头像管理 / 热重载 / 实时对话流水
SnowLuma WebUI (port 5099) ←→ 扫码登录 QQ / 连接状态
manage.bat             ←→ 统一入口（启动 Dashboard + 浏览器，端口自动清理）
```


## LLM Provider 四轨架构（2026-08-30 最新重构）

**四个 Provider 永远同时在线，分工明确、独立运行，绝不互相兜底（避免"幽灵调用"）**：

| Provider | 模型 | 角色 | 适用场景 | 状态 |
|---|---|---|---|---|
| **DeepSeek**（主对话，唯一） | deepseek-v4-pro | 处理**所有对话 / 工具调用 / 流式 / 用户画像** | 成本低、JSON 模式稳定、人设语气好 | ✅ 默认启用 |
| **deepseek-vision**（默认视觉） | deepseek-v4-flash-vision-exp | **图片描述专用**：把图片转成纯文字描述 | 复用 DeepSeek 同平台 Key，少维护一套凭证 | ✅ 默认启用 |
| **SiliconFlow 硅基流动**（可选视觉） | Qwen/Qwen3.5-9B | **图片描述专用**：把图片转成纯文字描述 | 多模态好，性价比高，无敏感词误伤 | ⚡ 可选 |
| **MiniMax**（备用视觉） | MiniMax-M3 | 图片描述 Provider **备用方案**（前两家都不可用时切） | 兼容保留 | ⚡ 可选备用 |

### 调用流程

```
用户发消息（含/不含图片）
   │
   ├─ 不含图 ───────────────────────→ 直接 DeepSeek 处理回复
   │
   └─ 含图 ──┬─ [默认] deepseek-vision 看图 → 输出 [图片描述: ...]
            ├─ [可选] SiliconFlow (Qwen3.5-9B) 看图（deepseek-vision 关了）
            └─ [备用] MiniMax M3 看图（前两家都关了）
                              ↓
                    DeepSeek 拿纯文本（含描述）处理回复
```

**关键设计**：视觉 Provider（deepseek-vision / SiliconFlow / MiniMax）**永远只接收图片 + 用户文字提示，不接触对话历史 / persona / thinking**，彻底避免它们自带的敏感词过滤因为上下文中的某句话被意外触发。

### 配置 Provider

**主对话 Provider**（Dashboard 可热切换，默认 deepseek）：
- Dashboard「参数设置」→「主对话 Provider」下拉框
- 切换后自动写入 `qqbot/data/runtime_settings.json`，bot 热加载
- 选项：`deepseek` / `minimax`（minimax 做对话不推荐，自带敏感词易 422）

**图片描述 Provider 选择（2026-08-30 更新）**：
- 优先级：若 `DEEPSEEK_VISION_ENABLED=true` 且 DeepSeek key 有效 → 用 deepseek-vision
- 否则若 `SILICONFLOW_ENABLED=true` 且 key 有效 → 用 SiliconFlow
- 否则若 `MINIMAX_ENABLED=true` 且 key 有效 → 用 MiniMax
- 都关 → `image_handler_enabled` 运行时开关直接跳过失图，回复为"（图片一张，暂无法识别）"

**识图总开关（运行时）**：Dashboard「参数设置」→ `image_handler_enabled`，临时关了不会报错，直接跳过图片处理。

**切换策略（不自动兜底，出问题明确报错）**：
- 主对话 Provider 不可用 → 抛 `RuntimeError`，Bot 回复"找主人修一下下"类型文案；需 Dashboard 手动切或修 `.env`
- 识图 Provider 不可用 → 本次 turn 跳过识图，下次消息仍尝试（不会永久禁用）
- **绝不**出现"Dashboard 显示 A，实际悄悄跑 B"的幽灵调用（2026-06-28 之前有，已移除）

启用/禁用：在 `private/.env` 配置：
- `DEEPSEEK_API_KEY`（主对话；只要填了就生效，没有专门的 enable 开关）
- `DEEPSEEK_VISION_ENABLED=true|false` + `DEEPSEEK_VISION_MODEL`（默认视觉，复用 `DEEPSEEK_API_KEY`）
- `MINIMAX_ENABLED=true|false` + `MINIMAX_API_KEY`
- `SILICONFLOW_ENABLED=true|false` + `SILICONFLOW_API_KEY`

通用 LLM 运行时参数（Dashboard 可调，无需重启，实际作用于 active Provider）：

| 参数 | 说明 | 默认 | 影响路径 |
|---|---|---|---|
| `llm_temperature` | 采样温度（2026-07-23 重命名，之前 `minimax_temperature`） | `0.95` | 对话 & 视觉 |
| `llm_top_p` | 核采样 | `0.9` | 对话 & 视觉 |
| `llm_max_tokens` | 单次最大输出 | `16384` | 对话 |
| `llm_reasoning_effort_with_tools` | 带工具调用时的思考强度 | `high` | 对话探测调用 |
| `llm_reasoning_effort_no_tools` | 纯文本时的思考强度 | `high` | 对话探测调用 |
| `image_handler_enabled` | 识图功能总开关（2026-07-23 新增） | `true` | 含图消息 |

### 错误分类与 fallback 文案

API 失败按 HTTP 错误分类，dashboard/chat.log 显示具体类型，回复给用户的 fallback 文案也不同：

| 错误类型 | HTTP | 用户看到的回复 |
|---|---|---|
| `sensitive` | 422 输入敏感词 | 呜呜泥说的话触发了窝的 safety filter…换个话题好不好嘛🥺 |
| `model` | 400 unknown model | 哎呀当前模型配置有点问题…等窝修一下下🙏 |
| `auth` | 401/403 | 呜…窝的 API key 好像出问题了，找主人修一下下叭 |
| `disallowed` | 400 disallowed url | 呜呜这张图好像有点问题，窝看不粗来… |
| `rate` | 429 | 太多人跟窝说话了呜呜 排队一下下嘛🙏 |
| `server` | 5xx | 上游模型服务在打盹儿…稍等一下下再来戳窝叭💤 |
| `unknown` | 其它 | 啊啊窝的脑袋当机惹 [抓狂] 让窝休息一下下… |

**敏感类错误不盲目重试**：sensitive / model / disallowed / auth 这 4 类遇到立即停止，避免浪费 3 次重试 + 触发连续错误。

## Dashboard 架构

- **后端**：FastAPI + Uvicorn，`main.py` 只做 app 组装 + 鉴权 + SSR，**45+ 路由按业务拆分到 `routers/` 包**（process/turns/corpus/skills/settings/docs/logs/ws），`monitor.py` 负责 psutil 进程监控
- **前端**：原生 HTML/CSS/JS，**模块化拆分**（17 个 js 文件按 tab/工具分层）+ 6 个 CSS 文件（tweaks 覆盖层已收口并入 2026-08-30）
- **首屏**：SSR-first 注入 `window._INIT_DATA`，首屏立即有数据，避开 `/api/health` 慢查询
- **主题**：8 套主题切换（桜日和 / 蜜桃气泡 / 霓虹终端 / 午夜银河 / 极地霜白 / 绯红机甲 / 琥珀暗夜 / 翡翠霜白），通过 CSS 变量热切换
- **API 鉴权**：中间件白名单（首页/静态资源/`/api/health`/`/api/ws-health`/snowluma SSE 放行），其余 `/api/*` 需 `X-Dashboard-Token` header 或 `?token=` query

## Dashboard 新增功能

**进程管理增强**
- 启动/重启前自动清理占用端口的旧进程（不再出现 Errno 10048 端口冲突）
- Dashboard 自身状态卡片（PID、内存、运行时间），支持网页一键重启
- 顶栏 pill **SSR 兜底**（不等 `/api/health`，首屏立即正确显示）

**技能管理增强**
- **头像自动加载**（用 SSR `init.avatars` 填充 `_PHOTOS`，渲染即可见）
- 排序：按名称 / 创建时间 / 版本号 / 大小
- 实时搜索筛选（名称、描述）
- 批量操作：全选/单选，一键批量删除

**日志查看**
- 内置日志查看器，无需命令行
- 自动清理 ANSI 颜色代码
- 支持自动滚动、选择不同日志文件

**对话流水实时监控**
- SSE 推流（`/api/turns/stream`）
- turn 步骤可视化（每步带类型/耗时/内容）
- 聚合统计 + 多维度筛选

**状态持久化**
- 自动记住上次访问的标签页
- 创建技能表单自动保存草稿
- SSR 兜底 + safeFetch 默认 30s timeout（避开 monitor 慢查询）



**进程管理增强**
- 启动/重启前自动清理占用端口的旧进程（不再出现 Errno 10048 端口冲突）
- Dashboard 自身状态卡片（PID、内存、运行时间），支持网页一键重启
- 顶栏 pill **SSR 兜底**（不等 `/api/health`，首屏立即正确显示）

**技能管理增强**
- **头像自动加载**（用 SSR `init.avatars` 填充 `_PHOTOS`，渲染即可见）
- 排序：按名称 / 创建时间 / 版本号 / 大小
- 实时搜索筛选（名称、描述）
- 批量操作：全选/单选，一键批量删除

**日志查看**
- 内置日志查看器，无需命令行
- 自动清理 ANSI 颜色代码
- 支持自动滚动、选择不同日志文件

**对话流水实时监控**
- SSE 推流（`/api/turns/stream`）
- turn 步骤可视化（每步带类型/耗时/内容）
- 聚合统计 + 多维度筛选

**状态持久化**
- 自动记住上次访问的标签页
- 创建技能表单自动保存草稿
- SSR 兜底 + safeFetch 默认 30s timeout（避开 monitor 慢查询）


## 目录结构

```
skill_communication/
├── qqbot/                          # QQ Bot 应用（NoneBot2 主程序）
│   ├── bot.py                      # NoneBot2 入口（读取 private/.env，注册 OneBot v11 adapter）
│   ├── dashboard.py                # Dashboard 前台启动入口（前台模式、有日志）
│   ├── .env.example                # 配置模板（参考用，真实生效值在 private/.env）
│   ├── pyproject.toml              # NoneBot2 配置：注册 OneBot v11 adapter；plugin_dirs=["plugins"]
│   ├── plugins/char_chat/          # ⭐ 核心聊天插件（22 个模块化文件，见下）
│   │   ├── __init__.py             # 插件入口，按依赖顺序导入所有模块
│   │   ├── config.py               # 全局配置（API Key、模型、路径、运行时参数、搜索健康、metrics）
│   │   ├── provider.py             # LLM Provider 管理（deepseek / deepseek-vision / siliconflow / minimax，四轨独立 + 健康）
│   │   ├── api_client.py           # HTTP API 请求（并发控制、重试、错误分类）
│   │   ├── skill_manager.py        # Skill 加载、热重载、角色切换、测试模式
│   │   ├── history.py              # 对话历史管理（持久化到 JSON、TTL 清理、轮数上限）
│   │   ├── user_profile.py         # 用户画像提取（DeepSeek 抽取称呼偏好/兴趣 → 注入上下文）
│   │   ├── avatar.py               # QQ 昵称与头像设置（SnowLuma API）
│   │   ├── search.py               # 联网搜索（百度 + 微博单帖/热搜，健康感知 + 自动降级）
│   │   ├── url_fetcher.py          # URL 提取与内容抓取（BV 号解析、短链展开、正文提取）
│   │   ├── dsml_cleaner.py         # DSML / XML / 思考标记清理
│   │   ├── message_utils.py        # 消息分段、QQ 表情解析、反垃圾
│   │   ├── rich_message.py         # QQ 富消息解析（小程序、XML、卡片分享）
│   │   ├── emoji_system.py         # 情绪表情系统（10 种情绪 + 关键词检测 + 概率 + 冷却）
│   │   ├── llm.py                  # LLM 调用（探测式 + Function Calling + 流式输出）
│   │   ├── rules.py                # 消息匹配规则（@我、命令识别、bot self-id 过滤；白名单群自动回复已移除）
│   │   ├── initiate.py             # 主动暗示概率掷骰（角色级、私聊白名单、默认关闭，2026-09-13）
│   │   ├── commands.py             # 命令注册与处理（reset / skills / switch / current / reloademoji）
│   │   ├── chat_handler.py         # 主消息处理流程（情绪→搜索→LLM→表情→流式分段）
│   │   ├── image_handler.py        # 多模态识图（image_url → 视觉Provider → 文字描述 → 替换注入）
│   │   ├── turn_recorder.py        # turn 流记录（turns_stream.jsonl + 按日 turn JSON 落盘）
│   │   └── lifecycle.py            # 生命周期（启动加载技能/停止保存历史）
│   ├── dashboard/                  # ⭐ Web 管理面板（FastAPI + 原生 HTML/CSS/JS）
│   │   ├── main.py                 # app 组装 + token 鉴权中间件 + SSR 首屏（路由已拆分到 routers/）
│   │   ├── routers/                # ⭐ 路由模块（2026-08-30 拆分）
│   │   │   ├── _shared.py          #   共享常量/路径/安全校验/token 常量时间比较
│   │   │   ├── process.py          #   进程管理 + /api/health(复用状态缓存) + Dashboard 自管理 + snowluma 日志 SSE
│   │   │   ├── turns.py            #   对话流水历史(尾部读取) + SSE 推流
│   │   │   ├── corpus.py           #   Cookie + 微博/QQ/文本语料 + 推特抓取 + 语料蒸馏
│   │   │   ├── skills.py           #   Skill CRUD + 编辑 + 蒸馏(并发保护) + 头像 + AI 特征整合
│   │   │   ├── settings.py         #   运行时参数读写 + LLM Provider 状态(mtime 缓存)
│   │   │   ├── docs.py / logs.py / ws.py   # 帮助文档 / 日志查看 / WebSocket 状态推送
│   │   ├── monitor.py              # 进程监控（端口清理 + psutil + SSE 探活 + 日志流）
│   │   ├── skill_manager.py        # Skill CRUD + 头像管理 + 蒸馏流程
│   │   ├── weibo_fetcher.py        # 微博 + 推特语料采集 + 语料→Skill 蒸馏（调用 DeepSeek）
│   │   └── static/                 # 前端界面（原生，无框架）
│   │       ├── index.html          # 主页面（SSR 注入 window._INIT_DATA + 8 套主题变量）
│   │       ├── photo/              # 头像静态资源（跟根目录 photo/ 同步，Dashboard SSR 用）
│   │       ├── css/ (6 文件)       # themes.css / base.css / layout.css / components.css
│   │       │                       #   editor.css / help.css（tweaks.css 已收口并入 2026-08-30）
│   │       └── js/ (17 文件)       # init.js / core_shared.js / themes.js / kpi.js / wshealth.js
│   │                               #   roster.js / logs.js / corpus.js / dashboard_mgmt.js / twitter.js
│   │                               #   turns.js / help.js / editor.js / snowluma_log.js
│   │                               #   skills_filter.js / form_draft.js / skills_batch.js
│   ├── characters/                 # ⭐ 角色目录（公开仓库只带 2 个 anime 示例）
│   │   ├── README.md              #   目录结构 + SKILL.md front-matter 字段说明
│   │   ├── makise-kurisu/         #   示例：牧瀬红莉栖（命运石之门）
│   │   ├── amadeus-kurisu/        #   示例：Amadeus 红莉栖
│   │   └── <其他角色>/             #   作者本地维护（真人系/自建），不进公开仓库
│   │       = SKILL.md + persona.md + work.md + corpus_ref/
│   ├── logs/                       # 运行日志（自动轮转 5MB×3 备份）
│   │   ├── nonebot2.log            # NoneBot2 全部输出（启动/报错/API栈）
│   │   ├── chat.log                # 对话事件日志（用户消息→Bot回复→搜索→流式分段）
│   │   ├── dashboard.out / .err    # Dashboard 后台启动 stdout/stderr
│   │   ├── snowluma.log            # SnowLuma 协议层日志（QQ 收发连接状态）
│   │   ├── turns_stream.jsonl      # turn 实时事件流（追加写，Dashboard SSE tail 它）
│   │   └── turns/                  # 单个 turn 的步骤快照（按日期分目录，*.json）
│   ├── data/                       # 持久化数据（bot 自己读写）
│   │   ├── runtime_settings.json   # Dashboard 运行时参数保存点
│   │   ├── conversation_histories.json  # 对话历史持久化
│   │   ├── user_profiles/          # 用户画像 JSON（每个 QQ 号一个文件）
│   │   └── search_health.json      # 搜索引擎健康状态（持久化，重启恢复）
│   └── _diag_*.py                  # 临时诊断脚本（2026-07-23 排查 conda env 子进程 fork 问题）
├── .work/                          # 临时工作目录（角色改写稿 / 模拟输出，不参与运行）
├── .pids/                          # 旧版 PID 目录（与 .runtime/ 重复，可忽略）
├── skill/                          # 第三方 Skill 参考（titanwings/colleague-skill 副本）
├── scripts/                        # 工具脚本
│   ├── bat/                        # Windows 批处理 & PS1（主运维入口）
│   │   ├── setup.bat               # ⭐ 一键环境部署（conda botenv + pip deps + Playwright Chromium）
│   │   ├── setup_snowluma.bat      # SnowLuma 反向 WebSocket 配置写入
│   │   ├── start_all.bat           # ❌ 已弃用 2026-07-01（保留作迁移提示）
│   │   ├── dashboard.bat           # Dashboard 前台启动（有日志框）
│   │   ├── dashboard_silent.bat    # Dashboard 后台启动（无窗口）
│   │   ├── create_shortcut.bat     # 桌面快捷方式创建（QQ Bot.lnk → manage.bat）
│   │   └── launch_dashboard.ps1    # ⭐ manage.bat 实际调的 PS1（后台 pythonw 启动 + 端口检测）
│   ├── fetch_twitter.py            # 推特语料抓取（twikit + Cookie，无需 API Key）
│   └── weibo_pw_cookies.py         # 微博抓取（Playwright + Cookie 注入）
├── private/                        # ⭐ 隐私配置目录（.gitignore 保护，不外传）
│   ├── .env                        # 主配置文件（API Key / 管理员 QQ / Token）
│   ├── cookies.json                # 微博登录 Cookie（Dashboard 上传）
│   └── twitter_cookies.json        # 推特登录 Cookie（Dashboard 上传或自动保存）
├── snowluma/                       # SnowLuma（QQ 协议层，需手动下载解压）
│   └── SnowLuma-v*-win-x64/
│       └── node.exe                # SnowLuma 启动入口（Dashboard → monitor.py 调用它）
├── corpus/                         # 语料库（微博/推特/QQ/二次元原作摘录）
│   ├── <uid>/                      # 真人微博用户语料（weibo_corpus.txt + weibo_ajax_pageN.json）
│   ├── <twitter_name>/             # 真人推特语料（corpus.txt + tweets_raw.json）
│   └── <二次元名>/                 # 二次元角色语料（corpus.txt + knowledge.md + source/）
│       └── source/                 #   原作摘录 / 萌娘百科参考 / 集数核对 / 中文粉丝向
├── photo/                          # 角色头像源目录（每个 Skill 同名子目录）
│   └── <skill名>/avatar.jpg|png    # Dashboard 同步到 qqbot/dashboard/static/photo/<同名>/
├── emoji/                          # 情绪表情资源（Bot 触发情绪时随机挑一张发出）
│   ├── angry/ curious/ happy/ laugh/ proud/ sad/ shy/ surprised/ thinking/ tired/
│   └── <每类>/ = keywords.txt（触发词） + *.png/jpg（表情图，需自行添加）
├── normal-paper/                   # ⭐ 通用共享背景知识
│   ├── README.md                   #   目录说明（公开仓库只带示例）
│   ├── examples/                   #   示例：basic/ 下文件的写法
│   └── basic/                      #   本地目录（⚠️ 只有这一层会被自动注入所有角色 system prompt）
│   # 说明：basic/ 之外的文件不进 prompt，只在角色调用 search_corpus 时被检索（rglob 全目录）
├── assets/                         # 静态资源
│   ├── icon/ icon.ico · qqbot_bot_icon.ico  # 桌面快捷方式 ICO
│   └── images/                     # 本地配图（已忽略，不进公开库）
├── docs/                           # 文档
│   ├── architecture-overview.png   # 架构总览图
│   ├── message-flow.png            # 单条消息处理链路图
│   ├── SOP.md                      # ⭐ 运行维护 SOP（SOP-01~09 + 索引 + 快速故障定位表）
│   ├── RELEASE-2026-08-30.md       # 2026-08-30 Dashboard 优化发布说明（更新日志 + 部署/回滚）
│   └── plan-himeragi-yukina.md     # 姬柊雪菜角色引入计划（参考二次元 Skill 流程）
├── archive/                        # 历史归档（旧环境/旧协议）
│   └── 2026-07-23-botenv-migrate/  # 2026-07-23 迁移前的 venv 备份
├── .runtime/                       # 运行时 PID（manage.bat / launch_dashboard.ps1 写入）
│   └── dashboard.pid
├── manage.bat                       # ⭐ 主入口（桌面快捷方式"QQ Bot.lnk"也指向它）
├── requirements.txt                # Python 依赖清单（setup.bat pip install -r 它）
├── LICENSE                         # MIT
├── .gitignore
└── README.md
```

**运行时自动创建的目录**（无需手动创建）：
- `D:\Work\anaconda3\envs\botenv\` - Conda 环境（Python 3.12，setup.bat 创建，所有脚本里硬编码路径）
- `qqbot/data/` - 对话历史 / 用户画像 / 搜索健康持久化
- `qqbot/logs/` - 运行日志（自动轮转）+ turn 流（`turns_stream.jsonl` / `turns/<date>/`）
- `corpus/` - 语料目录（Dashboard 抓取微博/推特时自动创建子目录）
- `.runtime/` - Dashboard PID 文件

**需要自行添加的内容**：
- `emoji/*/` - 表情图片（GIF/JPG/PNG，keywords.txt 已在）
- `photo/<角色名>/avatar.*` - 每个 Skill 的头像（可选，Dashboard 花名册展示）
- `snowluma/SnowLuma-*/node.exe` - 需从 GitHub 下载 SnowLuma 解压

**重要硬编码路径（迁移/换机器时必改）**：

| 文件 | 常量 | 值 |
|---|---|---|
| scripts/bat/setup.bat | CONDA_EXE | `D:\Work\anaconda3\Scripts\conda.exe` |
| scripts/bat/setup.bat | BOTENV_PY | `D:\Work\anaconda3\envs\botenv\python.exe` |
| scripts/bat/launch_dashboard.ps1 | `$venv` | `D:\Work\anaconda3\envs\botenv\pythonw.exe` |
| qqbot/dashboard/monitor.py | VENV_PYTHON | `D:\Work\anaconda3\envs\botenv\python.exe` |

> 这 4 处必须匹配你机器的 Anaconda 安装路径，否则所有启动/安装脚本会报"找不到 python"。


## 运行环境说明

本项目设计为 **Windows 本地单机运行**，所有脚本和路径均已在此环境下验证：

| 环境项 | 要求 / 说明 |
|---|---|
| 操作系统 | Windows 10 / Windows 11（64 位） |
| Python 环境 | **Conda env `botenv`**，路径 `D:\Work\anaconda3\envs\botenv\`（Python 3.12） |
| Conda 根 | `D:\Work\anaconda3\`（必须在此位置，否则改 4 处硬编码路径） |
| 启动脚本 | `.bat` / `.ps1`（PowerShell 5.1，Windows 自带） |
| QQ 协议端 | SnowLuma（Node.exe），需放项目根目录 `snowluma/SnowLuma-*/node.exe` |
| 占用端口 | `8080` NoneBot2 / `8501` Dashboard / `5099` SnowLuma WebUI |

**迁移历史记录（避免踩老坑）**：
- **2026-07-23：Python 运行环境从 venv → Conda env `botenv`**
  - 旧方式：`D:\path\to\skill_qqbot\Scripts\python.exe`（虚拟 venv）
  - 问题：venv launcher 是 Stub，实际转发到 base python，导致 `uvicorn` / `multiprocessing` fork 出的 worker 进了 Conda base 而非 venv，报 ImportError
  - 新方式：直接用 Conda 建真独立的 `botenv`，目录下的 `python.exe` 是真正的解释器副本，无转发问题
  - 影响：脚本路径硬编码均已改到 Conda 路径；bot.py / dashboard.py 开头仍保留 `sys._base_executable = sys.executable` 保险补丁（无害，保留）
  - bot.py 内 2026-07-23 注入的 `[DIAG Popen]` / `[DIAG uvicorn.run]` 临时诊断打印 **已清理（2026-08-30）**，bot.py 从 92 行精简到 29 行，仅保留 `sys._base_executable` 保险补丁

- **2026-07-01：QQ 协议层从 NapCat Desktop → SnowLuma（MaaYYDS 版）**
  - 旧方式：NapCat Desktop（QQNT 桌面插件版），启动需先开 QQ 再开 NapCat 启动器
  - 新方式：SnowLuma（纯 Node.exe，自带 OneBot v11 协议和反向 WebSocket）
  - 安装位置：`qqbot/snowluma/` → 已改到**项目根目录** `snowluma/`（跟 monitor.py 匹配）
  - 登录方式：SnowLuma WebUI（http://localhost:5099）扫码，无需手动开 QQ 客户端（但建议 QQ 客户端不要同时登同号，避免挤下线）

**Mac/Linux 用户**：本项目暂不支持，需要自行：
1. 替换 SnowLuma 为对应平台版本（或使用 Lagrange.OneBot 等其他 OneBot v11 实现）
2. 将 `.bat` / `.ps1` 启动脚本改写为 `.sh`
3. 修改 4 处 `D:\Work\anaconda3\...` 硬编码路径为 macOS/Linux 的 Conda 路径


## 日志与调试

所有日志存放在 `qqbot/logs/` 目录下（自动创建），超过 5MB 时自动轮转，保留 3 份备份：

| 文件 | 内容 | 查看时机 |
|------|------|----------|
| `nonebot2.log` | Bot 全部输出（启动、Skill 加载、API 调用、错误堆栈） | Bot 启动失败、API 报错、功能异常 |
| `chat.log` | 对话事件（用户消息、Bot 回复、搜索触发、流式分段） | 查看聊天记录、确认搜索是否触发、调试回复内容 |
| `snowluma.log` | snowluma QQ 协议层日志（WebSocket 连接、消息收发） | QQ 消息未收到/未发出、连接断开 |
| `dashboard.log` | Dashboard FastAPI 请求日志 | API 401/500 排查、token 鉴权问题 |

turn 流日志落地在 `qqbot/logs/turns_stream.jsonl`（单个 turn 的步骤快照在 `qqbot/logs/turns/<date>/*.json`），Dashboard 对话流水 tab 实时展示。

快速排查命令：

```bash
# 查看最近的错误
tail -50 qqbot/logs/nonebot2.log | grep -i error

# 查看最近对话
tail -20 qqbot/logs/chat.log

# 实时监控 Bot 输出
tail -f qqbot/logs/nonebot2.log

# 看 turn 流最后几条
tail -5 qqbot/logs/turns_stream.jsonl
```

---


## 技术栈

| 组件 | 技术 |
|------|------|
| Bot 框架 | NoneBot2 + OneBot V11 Adapter |
| QQ 协议 | SnowLuma（MaaYYDS 版，node.exe OneBot v11 实现） |
| 主对话 LLM | DeepSeek V4-Pro（默认，唯一主对话） |
| 视觉理解（默认） | **deepseek-vision - deepseek-v4-flash-vision-exp**（先下载转 base64，复用 DeepSeek Key） |
| 视觉理解（可选/备用） | SiliconFlow Qwen/Qwen3.5-9B（可选） / MiniMax M3（备用） |
| 联网搜索 | 百度（唯一通用引擎）+ 微博单帖正文/热搜（健康状态持久化 + 自动降级） |
| 推特抓取 | twikit（纯 HTTP，内置 KEY_BYTE 补丁，Dashboard 一键调用） |
| 微博抓取 | Playwright Chromium + Cookie 注入 + 浏览器内 AJAX 翻页 |
| Dashboard | FastAPI + Uvicorn + SSE（对话流）+ SSR（首屏注入） |
| 前端 | 原生 HTML/CSS/JS（模块化：17 js + 6 css），8 套 CSS 变量主题 |
| 语料蒸馏 | DeepSeek API（真人系/二次元系两套模板） |
| 进程监控 | psutil + asyncio（内置于 Dashboard，含端口自动清理） |
| 服务管理 | manage.bat + launch_dashboard.ps1（pythonw 后台启动 + PID 跟踪 + 端口冲突自愈） |
| Python 环境 | Conda env `botenv`（Python 3.12，杜绝 venv launcher 转发问题） |
