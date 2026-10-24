# 📝 更新日志

按时间倒序记录功能演进与重要修复。


### 2026-10-24（SnowLuma v1.10.3 → v2.2.0 升级）
- **QQ 协议层升级到 SnowLuma v2.2.0**（bundled Node v24.22.1）：
  - 旧版 `snowluma/SnowLuma-v1.10.3-win-x64/` 完整归档到 `archive/2026-10-24-snowluma-v2.2.0-upgrade/`（本地目录，`.gitignore` 已排除），含 `config-backup/`、`data-backup/` 与 `MIGRATION-NOTES.md`（回滚步骤）
  - `config/`、`data/` 直接沿用：QQ 登录态保留（无需重新扫码），启动日志确认 `Identity schema migrated` / `MessageMigration pending=970` / `WS-Client connected`
  - v2.2.0 的 zip 不再自带版本号目录，安装时需手动建 `snowluma/SnowLuma-v2.2.0-win-x64/`；`config/` 路径仍是 `config/onebot_<QQ>.json`
  - OneBot `wsClients` 字段变化：`heartInterval` / `reconnectInterval` 废弃，改为 `reconnectIntervalMs`（旧配置可被兼容解析器读取）
  - ⚠️ WebUI（:5099）登录后需重新同意 EULA / 隐私政策（协议文件更新导致旧 consent 失效，仅影响面板，不影响 QQ 收发）
- **同步配置模板**：`qqbot/snowluma_onebot_config.example.json` 升级为 v2.2.0 字段（真值配置文件 `qqbot/snowluma_onebot_config.json` 按 `.gitignore` 只留本地，`.example.json` 才入库）；`qqbot/.env.example` 补齐 `DEEPSEEK_VISION_*` / `SILICONFLOW_*` / `ACTIVE_LLM_PROVIDER` 等当前配置项（旧模板还残留 NapCat 与 minimax 回退链说明）
- **文档同步**：README / SOP 修正角色目录（`qqbot/characters/`）、角色数 17、四轨 Provider、搜索源精简、白名单群机制已移除、turn 流路径等滞后描述

### 2026-08-30（代码清理 + 运维脚本修复 + SOP 独立成文）
- **清理 bot.py 临时诊断代码**：
  - 移除 2026-07-23 排查 conda fork 问题时注入的 Popen / uvicorn.run / multiprocessing 三层 `[DIAG]` PATCH 打印
  - bot.py 从 92 行精简到 29 行，启动日志不再刷 DIAG 噪音
  - 保留 `sys._base_executable = sys.executable` 保险补丁（防御解释器转发，无害）
- **修复 setup.bat 两处路径问题**：
  - 第 4 步：配置模板改为写入 `private/.env`（原来写 `qqbot/.env`，实际代码不读取该路径）
  - 第 5 步：SnowLuma 检查路径修正为项目根目录 `%~dp0..\..\snowluma\SnowLuma-*`（原来 `%~dp0snowluma` 会错误解析到 scripts/bat 下）
- **运行维护 SOP 独立成文**：README 中 SOP 章节抽取为 [SOP.md](SOP.md)，新增环境速查 + SOP 索引 + 快速故障定位表；README 保留链接 + 最常用 3 条速记
- **Dashboard 前端改进**（已浏览器实测无回归）：
  - **统一轮询调度器**：3 个散落的 `setInterval`（health 30s / dashboard 30s / ws-health 5s）合并为 `schedulePoll` 单一调度器；**页面隐藏自动暂停全部轮询**，切回前台立即补刷（省请求、省电量）
  - **可访问性**：蒸馏 Modal 加 focus trap + Escape 关闭 + 关闭后焦点恢复；主题下拉加 Escape 关闭 + `aria-expanded` 管理；状态 pills / WS badge 加 `role="status" aria-live="polite"`（读屏播报）；Modal 补 `role="dialog" aria-modal`
  - **CSS 收口**：删除独立覆盖层 `tweaks.css`（7→6 个 CSS），其增量规则按主题/布局/动效/组件分别并入 base.css / layout.css / components.css / help.css，最终视觉不变
- **Dashboard 后端优化**（已实测 19 个 API 全部 200 + 401 鉴权正常）：
  - **路由拆模块（P3）**：main.py 从 1384 行精简到 139 行，45+ 路由按业务拆分到 `dashboard/routers/` 包（process / turns / corpus / skills / settings / docs / logs / ws + `_shared.py` 共享常量与安全校验）
  - **/api/health 复用状态缓存（P1）**：不再每次全进程遍历 psutil，改用 `get_all_status` 的 5s 缓存
  - **turns history 改尾部读取（P1）**：`_read_turns_history` 从"每次 readlines 全文件"改为 `_tail_lines` 只读文件尾部最近 N 行
  - **llm-providers mtime 缓存（P2）**：前端 30s 轮询不再每次重解析 .env，配置未变直接返回缓存
  - **token 常量时间比较（P2）**：HTTP 中间件 + WebSocket 校验改用 `secrets.compare_digest`
  - **搜索健康写盘限频（P2，bot 侧）**：`config.py` 搜索源连续失败时写盘从"每次"降为"5s 一次"
  - **顺手修复**：twitter 抓取子进程不再引用已失效的旧 venv 路径 `skill_qqbot/Scripts/python.exe`，直接用当前解释器
- **全栈检查补充修复**（2026-08-30 第二轮，均已实测）：
  - **monitor.py 性能**：`check_port_open` 改 psutil LISTEN 检测（原 socket 连未监听端口要等满 1s 超时 → 毫秒级）；`get_all_status` 里 `get_napcat_status` 只调一次（原调两次）→ 冷启动 **7.5s → 3.9s**（缓存命中 0s）
  - **kpi.js 鉴权修复**：LLM 状态卡改用 `safeFetch`（自动带 `X-Dashboard-Token`）；原裸 `fetch('/api/llm-providers')` 在开启鉴权后**必 401**，LLM KPI 卡显示 "-"
  - **静态资源缓存策略**：`/static/*` 加 `Cache-Control: no-cache`（保留 ETag 校验）——之前前端改版后浏览器可能一直用旧 JS/CSS
  - **setup_snowluma.bat 路径修复**：2 处 `%ROOT%` 路径回退两级到项目根（原指向 `scripts/bat/` 下不存在的目录，导致脚本检测失败）
- **README 同步**：Roadmap 勾掉 DIAG 清理项；setup.bat 注意事项更新为已修复

### 2026-07-23（环境 + Provider + 配置大重构）
- **Python 运行环境迁移**（根治 venv 坑）：
  - 从项目自建 venv `skill_qqbot/` 迁移到 Anaconda Conda env `botenv`（Python 3.12，真独立解释器）
  - 解决了 venv launcher 转发到 base python 导致 uvicorn watchfiles / multiprocessing worker 拿不到依赖的问题
  - setup.bat / launch_dashboard.ps1 / monitor.py 4 处路径统一更新为 `D:\Work\anaconda3\envs\botenv\`
  - bot.py 开头加入 `sys._base_executable = sys.executable` 保险补丁（双保险，无害）
  - bot.py 内注入了 Popen/uvicorn/multiprocessing PATCH 级 DIAG 日志（排查用，后续可删）
- **三轨 Provider 架构上线**（新增 SiliconFlow；2026-08-30 已扩展为 deepseek-vision 四轨）：
  - 新增 **SiliconFlow Qwen3.5-9B** 作为视觉默认 Provider（比 MiniMax 更稳定、无敏感词误杀）
  - 当时视觉路由优先级：SiliconFlow → MiniMax → 跳过失图（2026-08-30 起为 deepseek-vision → SiliconFlow → MiniMax）
  - 新增 `image_handler_enabled` 运行时开关（Dashboard 可热关，识图故障时一键降级）
  - Provider 健康检查扩展到 3 家（deepseek / minimax / siliconflow；2026-08-30 起为 4 家，新增 deepseek-vision）
  - `active_llm_provider` 依然只有 deepseek / minimax 两选项（SiliconFlow 仅视觉不进对话）
- **运行时参数通用化重命名**：
  - `minimax_temperature` → `llm_temperature` 等 5 个参数（不再误导使用者以为只影响 MiniMax）
  - 旧的 `minimax_*` schema 保留向后兼容读写
- **新增功能**：
  - **白名单群自动回复**（`AUTO_REPLY_GROUP_IDS` + `AUTO_REPLY_GROUP_PROBABILITY`，群内概率接话不@；**2026-09 已移除**）
  - **搜索意图关键词扩充**（补"新闻/动态/近况/成员名单/科普/百科"等 40+ 高频搜索信号词，避免"kimi k3有什么新闻"被当普通闲聊）
  - **MAX_HISTORY_ROUNDS 默认 40 → 100，HISTORY_TTL_HOURS 6 → 36**（更长记忆）
  - **THINKING_TIMER_SECONDS 5 → 120**（减少"思考中…还没发出来就回复"尴尬）
  - **STREAM_TIME_GRACE_SECONDS = 1.5** 新参数（流式尾部宽容等待）
- **配置路径一致性**：private/.env 明确作为唯一生效配置位置；qqbot/.env.example 仅作模板保留

### 2026-07-01（QQ 协议层迁移 SnowLuma + 启动流程重构）
- **QQ 协议层从 NapCat Desktop → SnowLuma（MaaYYDS 版）**：
  - 旧：QQ 客户端 + NapCat 插件启动器（依赖图形界面开 QQ）
  - 新：纯 Node.exe（SnowLuma），自带 WebUI（localhost:5099）扫码登录，不依赖 QQ 客户端 GUI
  - 安装目录从 `qqbot/snowluma/` 改到项目根 `snowluma/SnowLuma-*/node.exe`（跟 monitor.py 对齐）
  - setup_snowluma.bat 重新适配新的 onebot11 配置 JSON 路径
- **启动流程统一**：
  - `start_all.bat` 标记 **DEPRECATED**（不再使用）
  - 唯一入口：`manage.bat` → 调用 `scripts/bat/launch_dashboard.ps1` → 后台 pythonw 启动 Dashboard → 自动开浏览器
  - NoneBot2 / SnowLuma 都在 Dashboard 网页启停（不再需要命令行开）
- **端口冲突自愈**：Dashboard「启动」/「重启」按钮前自动 `clean_stale_port()` 清理 8080/8501 占用，不再需要手动杀进程

### 2026-06-28
- **LLM 双轨架构重构**（核心变更）：
  - 主对话 Provider 默认改回 **deepseek**（minimax 自带敏感词过滤导致 422 影响正常聊天）
  - minimax 重新定位为**图片描述专用 Provider**：只看图说话，不参与对话
  - 含图消息流：minimax vision 转文字描述 → 替换 image_url 段 → deepseek 处理回复
  - **minimax 永远不接触对话上下文**，避开敏感词触发
- **错误分类细化**：API 错误按 sensitive/model/auth/disallowed/rate/server 分类型；sensitive/model/disallowed/auth **立即停止重试**；7 种 fallback 文案
- **新增 Dashboard KPI 卡**：主对话 LLM + 图片描述 LLM 两个独立状态展示
- **新增 `/api/llm-providers` endpoint**：返回双 provider 状态 + routing strategy
- **Dashboard settings 修复**：5 个 minimax 参数（max_tokens/temperature/top_p/reasoning_effort）之前 schema 没登记，dashboard 改完静默被吞；现在真正可调
- **移除兜底**：minimax 失败不再自动切 deepseek（避免"幽灵调用"）；主选不可用直接抛错
- **bot 启动日志改进**：分「主对话 Provider」/「图片描述 Provider」/「可用 Providers」三行展示
- **流式 thinking buf 残留修复**：流结束时 thinking buffer 残留的最多 6 字符被吞 → 现在并入正文
- **截断补救判断收紧**：避免短回复 + 末尾标点误判触发补救导致双发
- **用户画像提取** 改为跟随主选（之前硬编码 deepseek，但 minimax 主选时会"幽灵"用 deepseek）

### 2026-06-27
- **默认主选切换到 MiniMax M3**（minimax）：自带多模态识图、400k 上下文、思考模式可调
- **彻底移除 OpenAI Provider**：之前保留的兼容层已删除，README/配置同步更新
- **Dashboard 视觉优化 ABDE**：tweaks.css 增量层 + 6+2 主题增强 + 模块化拆分（17 个 js 文件）+ SSR 兜底
- **新增 Dashboard「对话流水」tab**：SSE 实时 + 历史聚合 + 多维度筛选
- **新增 minimax 全参数运行时可调**：temperature / top_p / max_tokens / reasoning_effort（含/不含工具）
- **修复**：checkHealth SSR 兜底（避开 `/api/health` 慢查询 25s 卡顿）/ EventSource 加 token query / safeFetch 默认 timeout 30s
- **角色 Skill 框架完整就绪**：示例角色 + Dashboard 蒸馏流程

### 早期
- v4.x — 联网搜索 / 流式输出 / 多模态识图 / 微博+推特语料
- v3.x — 角色 Skill 体系 + 蒸馏流程
- v2.x — Dashboard Web 管理面板
- v1.x — 基础 NoneBot2 + snowluma + DeepSeek
