# QQ Bot 多角色扮演系统 — 运行维护 SOP

> **标准操作程序（Standard Operating Procedure）**
> 适用：Windows 本地部署的 `skill_communication` 项目（NoneBot2 + SnowLuma + 多 LLM Provider）
> 配套文档：[README.md](../README.md)（安装 / 架构 / 配置全量说明）
> 本文件整理于：2026-08-30（从 README 独立成文）

---

## 0. 环境速查

| 项 | 值 |
|---|---|
| 项目根目录 | `D:\agent_function\skill_communication` |
| Python 环境 | `D:\Work\anaconda3\envs\botenv\`（Conda，Python 3.12，硬编码路径） |
| NoneBot2 端口 | `8080` |
| Dashboard 端口 | `8501`（浏览器访问 http://localhost:8501） |
| SnowLuma WebUI | `http://localhost:5099`（扫码登录 QQ） |
| 主配置文件 | `private/.env`（唯一生效位置） |
| 运行时参数 | `qqbot/data/runtime_settings.json`（Dashboard「参数设置」页可改） |
| 日志目录 | `qqbot/logs/`：nonebot2.log / chat.log / snowluma.log / dashboard.out / dashboard.err / `turns_stream.jsonl` / `turns/<date>/` |
| 角色 Skill 目录 | `qqbot/characters/`（当前 17 个角色） |
| 语料目录 | `corpus/`（微博 / 推特 / QQ 群聊 / 二次元原作） |
| 共享背景知识 | `normal-paper/`（所有角色自动注入 system prompt） |
| 入口 | `manage.bat`（桌面快捷方式「QQ Bot」也指向它） |

---

## 1. SOP 索引

| # | SOP | 何时用 |
|---|---|---|
| [01](#sop-01-日常启动开机后第一件事) | 日常启动 | 开机后要把 bot 跑起来 |
| [02](#sop-02-日常关闭关机前) | 日常关闭 | 关机前优雅关停 |
| [03](#sop-03-新增角色真人系--已有微博账号) | 新增角色（真人·微博） | 给真人做角色 |
| [04](#sop-04-新增角色二次元系--原作萌娘百科) | 新增角色（二次元） | 给动漫角色做 persona |
| [05](#sop-05-群聊触发规则白名单概率接话已移除) | 群聊触发规则 | 群聊为什么必须 @ 或发命令 |
| [06](#sop-06-角色语气不好--回答不符合人设--怎么调) | 角色语气调优 | persona 不对味 |
| [07](#sop-07-故障排查清单按最常见最不常见排序) | 故障排查清单 | 任何"出问题了"先看这里 |
| [08](#sop-08-升级依赖--python-包需要时) | 升级依赖 | 要装 / 升 Python 包 |
| [09](#sop-09-备份防止数据丢失每月必做) | 备份 | 每月 / 改角色后 |

---

## 2. 快速故障定位

| 症状 | 直接跳转 |
|---|---|
| 双击 manage.bat 没反应 / 浏览器没开 | [SOP-07 → 故障 1](#-1-双击-managebat-一闪就没了--浏览器没打开) |
| Dashboard 能开但 NoneBot2 启不动（pill 一直红） | [SOP-07 → 故障 2](#-2-dashboard-能打开但-nonebot2-启不动pill-一直红) |
| **群聊 @Bot 不回**（九成是 test_mode） | [SOP-07 → 故障 3](#-3-nonebot2-绿snowluma-绿但-qq-群--bot-不回) |
| 回复报错"上游模型服务在打盹儿…" | [SOP-07 → 故障 4](#-4-回复里出现上游模型服务在打盹儿或-5xx) |
| 发图 bot 没描述内容 | [SOP-07 → 故障 5](#-5-识图失败发图不回描述) |
| Dashboard / 对话流水 Tab 401 | [SOP-07 → 故障 6](#-6-dashboard--对话流水-tab-401-unauthorized) |
| bot 忘事快 / 刚聊过的不记得 | [SOP-07 → 故障 7](#-7-对话记忆忘事太快--刚聊过的就不记得) |
| QQ 号被挤下线 / SnowLuma 离线 | [SOP-07 → 故障 8](#-8-qq-号被挤下线--snowluma-显示离线) |
| 角色说话不像角色 / 人设不对 | [SOP-06](#sop-06-角色语气不好--回答不符合人设--怎么调) |
| 怎么加新角色 | [SOP-03](#sop-03-新增角色真人系--已有微博账号) / [SOP-04](#sop-04-新增角色二次元系--原作萌娘百科) |
| 群聊不 @ Bot 就不回复（2026-09 起设计如此） | [SOP-05](#sop-05-群聊触发规则白名单概率接话已移除) |
| 升级 Python 依赖 | [SOP-08](#sop-08-升级依赖--python-包需要时) |
| 定期备份 | [SOP-09](#sop-09-备份防止数据丢失每月必做) |

---

## SOP-01 日常启动（开机后第一件事）

```
1. 双击桌面「QQ Bot」快捷方式 → （或直接双击 manage.bat）
   └─ 背后操作：powershell 后台启动 pythonw uvicorn，写 .runtime/dashboard.pid
   └─ 自动打开浏览器 → http://localhost:8501

2. Dashboard 登录（输入 DASHBOARD_TOKEN，浏览器会记到 localStorage）

3. 进程管理 Tab，按顺序启动：
   [1] NoneBot2 卡片 → 点「启动」
       └─ 等待 pill 从红 → 绿（显示"监听 8080"）≈ 5-15s
   [2] SnowLuma 卡片 → 点「启动」
       └─ 如果弹出 SnowLuma WebUI → 确认显示"机器人在线"即可
       └─ 首次或掉线需扫码 → http://localhost:5099 用手机 QQ 扫

4. 顶部状态 pill 最终颜色：
   ✅ 全部变绿 = System OK / NoneBot2 OK / SnowLuma OK / WebSocket 已连接
   ❌ 有红色 = 见 SOP-07 故障排查
```

---

## SOP-02 日常关闭（关机前）

推荐顺序：**先 Dashboard 页面优雅关停，再关机**。不优雅关机问题也不大（Bot 数据持久化是原子写），但能避免下次启动端口残留。

```
方式 A（优雅，推荐）：
1. Dashboard → 进程管理
2. 先点「停止 SnowLuma」（避免 QQ 协议层硬断开被腾讯判定异常）
3. 再点「停止 NoneBot2」
4. 最后点「停止 Dashboard」
   └─ Dashboard 自管理卡片会杀死自己（pythonw 进程）

方式 B（快速，直接关机）：
- 直接 Windows 关机即可。bot 不会损坏，下次启动 manage.bat 会自动清理 8080 / 8501 残留进程
```

---

## SOP-03 新增角色（真人系 — 已有微博账号）

```
1. 拿目标微博 UID：打开用户主页 → URL 里 weibo.com/u/1234567890 的数字
2. Dashboard → Cookie → 检查微博 Cookie 是否绿（过期就重新粘贴一份）
3. Dashboard → 语料库 → 微博抓取：
   [1] 填入 UID → 点「抓取」（Playwright 模拟浏览器，约 1-2 分钟）
   [2] 抓完会落到 corpus/<UID>/weibo_corpus.txt + weibo_ajax_page*.json
4. 语料库 → 刚抓取的条目 → 点「生成技能」（蒸馏）：
   [1] 选角色类型：名人 / 同事 / 亲密关系
   [2] 填技能名（英文/数字，如 "jinmao"、"aoyi"）+ 显示名 + 描述
   [3] 点「生成」 → DeepSeek 跑 30~60s → 自动落到 qqbot/characters/<name>/
5. 角色头像：
   把 avatar.jpg/png 放到 photo/<name>/avatar.jpg
6. Dashboard → 技能管理 → 点「重载技能」→ 确认新角色已出现在列表
7. 测试：私聊 bot → /switch <name> → 说两句看语气
```

---

## SOP-04 新增角色（二次元系 — 原作+萌娘百科）

参考 [docs/plan-himeragi-yukina.md](plan-himeragi-yukina.md) 完整流程（雪菜引入计划），简写版：

```
1. 打开萌娘百科对应词条 → 全文复制存为 corpus/<name>/source/moegirl_ref.md（UTF-8）
2. 手动整理原作语料：动画台词 / 小说摘录 → corpus/<name>/source/jp_scripts.md（标集数）
3. 粉丝向语料（贴吧/微博/b站）→ corpus/<name>/source/cn_fanwork.md（标来源）
4. 写 corpus/<name>/knowledge.md（设定 / 关系 / 武器 / 关键剧情锚点）
5. 写 corpus/<name>/corpus.txt（蒸馏过的短句 + 上下文 + 口癖 DNA 样本，≥500 行）
6. 建 qqbot/characters/<name>/：
   - SKILL.md（frontmatter + 启用规则 + 硬限制，参考 yukina/）
   - persona.md（5 层：Core Rules → Context → Expression DNA → Emotional Logic → Conflict/Repair）
   - work.md（日常参与策略 / 战斗场景 / 关系边界）
   - corpus_ref/ （软链或直接复制 ../../corpus/<name>/ 进来）
7. photo/<name>/avatar.jpg → 放入头像
8. Dashboard → 技能管理 → 「重载技能」→ 测试：/switch <name>
```

---

## SOP-05 群聊触发规则（白名单概率接话已移除）

> **2026-09 起**，`rules.py` 已移除"白名单群按概率自动回复"。现在的触发规则：
> - **群聊**：必须 **@Bot**，或以 `/`、`／` 开头的命令才会进入处理流程
> - **私聊**：一律放行（命令权限由 handler 内的 admin 校验决定）
> - `AUTO_REPLY_GROUP_IDS` / `AUTO_REPLY_GROUP_PROBABILITY` **已不再生效**：`config.py` 仍保留解析以兼容旧 `.env`，但没有任何调用方；留着不影响启动，可以删除

当年配置方式（历史说明，勿再照做）：

```
1. 打开 private/.env，加两行：
   AUTO_REPLY_GROUP_IDS=123456789,987654321     ← 群号，中英逗号都行
   AUTO_REPLY_GROUP_PROBABILITY=0.15             ← 每条消息 15% 概率接话
2. 重启 NoneBot2 后，群里不用 @ 也会按概率接话
3. 现在要 bot 在群里说话，请直接 @ 它，或使用 / 命令
```

---

## SOP-06 角色语气不好 / 回答不符合人设 → 怎么调

**不要直接改代码**，99% 情况改 `persona.md` 就能解决：

| 问题 | 调哪里 | 改法 |
|---|---|---|
| 说话像通用助手，没角色感 | persona.md Layer 2（Expression DNA） | 加口头禅/禁止词/句末语气样本 5~10 条 |
| 说错了设定（把角色 A 的设定说出来） | persona.md Layer 0（Core Rules） | 加硬限制"绝对不要… / 你只说 <原作> 内的事" |
| 太主动/太冷漠，关系错位 | persona.md Layer 1（Context）+ Layer 3（Emotional Logic） | 明确"你和用户是 XX 关系"/"不要主动发暧昧"/"不要自称老婆" |
| 被稍微带偏就聊到成人话题 | persona.md Layer 0 + 4 冲突处理 | 加边界红线 + 指定回避/修复话术 |
| 人设还原度还不够 | corpus/<name>/corpus.txt | 加更多原作口癖样本（原句摘录），越具体越好 |
| 全都是中文，二次元浓度不够 | persona.md Layer 2 | 加混语规则："关键情绪节点可插入日文短句，用「」包起来，占比 ≤ 30%" |

**每次改完后的生效路径（最快）**：
```
1. 改 qqbot/characters/<name>/persona.md 保存
2. Dashboard → 技能管理 → 「重载技能」（不重启 bot，0.5s 生效）
3. 私聊 Bot → /reset → 清空之前记忆 → 开始测试
```

---

## SOP-07 故障排查清单（按最常见→最不常见排序）

### ❓ 1. 双击 manage.bat 一闪就没了 / 浏览器没打开

```
→ 看日志：qqbot/logs/dashboard.err
常见原因：
  a. botenv 没装 → 先跑 scripts/bat/setup.bat
  b. D:\Work\anaconda3 路径不对（装到了 C 盘？）→ 改 4 处硬编码（见 README「目录结构」末表）
  c. 端口 8501 被别的东西占了且没清掉 → 打开 PowerShell 执行：
     Get-NetTCPConnection -LocalPort 8501 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }
     然后再开 manage.bat
```

### ❓ 2. Dashboard 能打开，但 NoneBot2 启不动（pill 一直红）

```
→ Dashboard → 日志 Tab → 选 nonebot2.log，看最后 30 行
常见原因：
  a. private/.env 里 DEEPSEEK_API_KEY 还是 sk-your-xxx 占位 → 换成真 key
  b. Port 8080 被残留旧进程占 → 点「重启」（重启会自动 clean_stale_port）
  c. pip 依赖没装全 → 打开 botenv 终端：
     D:\Work\anaconda3\envs\botenv\python.exe -m pip install -r requirements.txt
  d. 模块 SyntaxError / ImportError → 最近改了代码文件，回退或修复
```

### ❓ 3. NoneBot2 绿、SnowLuma 绿，但 QQ 群 @ Bot 不回

```
→ 诊断顺序：
  [1] 先看顶部 WS pill：是不是绿色"已连接"？
      红色 = SnowLuma 跟 NoneBot2 的反 WS 没通 → 跑 scripts/bat/setup_snowluma.bat 重写配置
  [2] 再看聊天日志 chat.log：有没有 "@机器人 ..." 这行进来？
      没有行进来 = SnowLuma 没把消息推上来 → SnowLuma 侧问题（WebUI 登出再扫码）
  [3] 有消息进来但输出 "test_mode_enabled: group silent" → 开了测试模式！
      解决：Dashboard → 参数设置 → test_mode_enabled 取消勾选 → 保存
  [4] 有输出 Error: 401 auth → 修 private/.env 里 DEEPSEEK_API_KEY
  [5] 群里没 @ 就不回复 → 2026-09 起设计如此，不是故障；白名单群概率接话已移除
```

### ❓ 4. 回复里出现"上游模型服务在打盹儿…"或 5xx

```
→ 临时故障，DeepSeek/SiliconFlow 侧问题，不是你项目的问题
处理：
  1. 等 1-2 分钟再发一次，正常会好
  2. 频繁出现 → Dashboard → 参数设置 → 切主对话 Provider 试（如果 minimax 还有额度的话）
  3. 看 Provider 官网状态（DeepSeek status / SiliconFlow status）
  4. 不会自动切兜底，报错是有意为之的 — 避免"幽灵调用"计费不清楚
```

### ❓ 5. 识图失败（发图不回描述）

```
→ Dashboard → 参数设置：
  a. image_handler_enabled 是否勾选（必须勾）
  b. private/.env 里 DEEPSEEK_VISION_ENABLED=true（默认视觉，复用 DEEPSEEK_API_KEY）；
     或 SILICONFLOW_ENABLED=true / MINIMAX_ENABLED=true 作兜底（优先级 deepseek-vision → SiliconFlow → MiniMax）
  c. 如果默认视觉一直报 auth → 切 SiliconFlow / MiniMax 试试，或 Dashboard 先关 image_handler_enabled
     下次重启 NoneBot2 再开
```

### ❓ 6. Dashboard / 对话流水 Tab 401 Unauthorized

```
原因：EventSource（SSE）浏览器 API 不能带自定义 Header，token 通过 ?token=xxx query 传
解决：
  1. 确认你是通过正常登录流程进入（首次弹框输 DASHBOARD_TOKEN，不是直接打开 URL）
  2. 直接开的话 → URL 后加 ?token=你的DASHBOARD_TOKEN
  3. 如果忘了 DASHBOARD_TOKEN → 看 private/.env，或 qqbot/logs/dashboard.err（启动时会打印生成的随机 token）
```

### ❓ 7. 对话记忆"忘事"太快 / 刚聊过的就不记得

```
原因：
  - 历史轮数 MAX_HISTORY_ROUNDS 到顶了，最旧的被挤掉
  - 或 HISTORY_TTL_HOURS 过期了（默认 36 小时过期清空）
处理：
  Dashboard → 参数设置：
  max_history_rounds: 100 → 改到 200
  history_ttl_hours:  36  → 改到 72（3 天）
```

### ❓ 8. QQ 号被挤下线 / SnowLuma 显示离线

```
最常见：你手机 QQ / 电脑 QQ 客户端又登录了同一个机器人号 → 腾讯踢掉 SnowLuma
处理（推荐）：
  1. 机器人号用**新号或小号**，跟你私人号完全分开
  2. 不在手机 QQ / PC QQ 客户端上登机器人号
  3. 重新扫码：SnowLuma WebUI（localhost:5099）→ 下线 → 重新生成二维码扫
```

---

## SOP-08 升级依赖 / Python 包（需要时）

```bat
:: 打开 CMD，进入项目目录
cd D:\agent_function\skill_communication

:: 1. 先看 botenv 里当前有啥
D:\Work\anaconda3\envs\botenv\python.exe -m pip list

:: 2. 升级所有包（保守做法：只升级 nonebot2 / psutil / twikit 等直接依赖）
D:\Work\anaconda3\envs\botenv\python.exe -m pip install -r requirements.txt --upgrade

:: 3. 若 requirements.txt 改了，重新装一遍
D:\Work\anaconda3\envs\botenv\python.exe -m pip install -r requirements.txt

:: 4. Playwright 浏览器内核升级（微博抓取用）
D:\Work\anaconda3\envs\botenv\Scripts\playwright.exe install chromium

:: 5. 升级后必须重启 NoneBot2
Dashboard → 进程管理 → NoneBot2 重启
```

---

## SOP-09 备份（防止数据丢失，每月必做）

```
必备份（含隐私，不要上传 GitHub / 公网）：
  1. private/              整个目录 → 云盘 / NAS / 移动硬盘加密备份
  2. qqbot/characters/         所有角色（17 个，纯手写的无价）
  3. qqbot/data/           conversation_histories.json / user_profiles/ / runtime_settings.json
  4. corpus/               所有语料（抓微博/整理原作非常花时间）

可选备份：
  - photo/ （头像，没了可以再找）
  - normal-paper/（通用共享知识，跟项目一起已在 git 里的话就不用额外备份）

建议备份频率：
  - 改了角色 persona.md / 新增 Skill → 当天备份
  - 平日 → 每周自动同步
  - 重要升级（环境变动/大改代码）→ 前后各备份一次

⚠️ 绝对不要备份到公开仓库（GitHub）：.gitignore 已拦，但再确认：
     private/*  /  corpus/*  /  qqbot/characters/*（除 761 示例）  /  qqbot/data/*
```
