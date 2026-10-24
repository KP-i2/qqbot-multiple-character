# ❓ 常见问题（FAQ）

按现象查原因。安装/启动类问题也可以先看 [SOP-07 故障排查](SOP.md)。


**Q: setup.bat 提示找不到 conda / python？**
A: 项目硬编码 Conda 根在 `D:\Work\anaconda3\`。如果装到了 C 盘或其他位置，改这 4 处文件的路径常量：
   - `scripts/bat/setup.bat`（CONDA_EXE + BOTENV_PY）
   - `scripts/bat/launch_dashboard.ps1`（`$venv` 变量）
   - `qqbot/dashboard/monitor.py`（`VENV_PYTHON` 常量）
   然后重新跑 `scripts/bat/setup.bat` 会自动装 botenv。

**Q: manage.bat 一闪就没了 / 浏览器没打开？**
A: `manage.bat` 现在走 `scripts/bat/start_stack.ps1`：会依次启动 Dashboard → SnowLuma → NoneBot2，等 WebSocket 连通后才开浏览器（正常可能等十几秒，QQ 没登录时最多等 60s）。
- 窗口一闪就没有、浏览器也没开 → 看 `qqbot/logs/dashboard.err`（Dashboard 起不来）或直接在该窗口里看启动输出；常见是 botenv 未安装 / 端口 8501 被占。
- 想跳过自动开浏览器：设环境变量 `QQBOT_NO_BROWSER=1` 再运行。
- 日志里写 `pythonw.exe not found` → 先跑 setup.bat。

**Q: 一键启动后卡在"等待 WS 连接就绪"？**
A: 说明 SnowLuma/NoneBot2 进程起来了但 QQ 没连上。最常见原因是 **QQ 客户端没登录**（SnowLuma 靠 hook 已登录的 QQ）。脚本开始时会检查并提示；打开 QQ 登录即可，之后重跑 `manage.bat` 或到 Dashboard 点重启。超时后脚本只是提示，不会杀掉已启动的进程。

**Q: manage.bat / Dashboard 里启动找不到 SnowLuma？**
A: 2026-07-01 之后 SnowLuma 要放**项目根目录**下（不是 qqbot/ 里！）：
```
skill_communication/
└── snowluma/
    └── SnowLuma-v2.2.0-win-x64/
        ├── node.exe
        └── index.mjs
```
从 [MaaYYDS/SnowLuma Releases](https://github.com/MaaYYDS/SnowLuma/releases) 下载 win-x64 zip 解压即可。注意 v2.2.0 的 zip 是**根级文件**，要先手动新建 `snowluma\SnowLuma-v2.2.0-win-x64\` 再解压进去（旧版包自带版本目录）；`snowluma/` 下有多版本目录时 Dashboard 会按版本号取**最高版本**（v2.10 > v2.2）。

**Q: Bot 启动了但 QQ @了不回消息？**
A: 按 SOP-07 故障 3 的诊断顺序：
1. 先看顶部 **WS pill 颜色**（红=SnowLuma 和 NoneBot2 反 WS 没连上 → 跑 `setup_snowluma.bat`）
2. 再看 Dashboard → 日志 `chat.log` 是否有消息行进来（没进来=SnowLuma 侧没登录 → 去 SnowLuma WebUI localhost:5099 扫码）
3. 最容易踩的坑：**test_mode_enabled 默认是 true**！群聊会被静默拦截。去 Dashboard「参数设置」取消勾选 test_mode_enabled → 保存即可。
4. 白名单群概率接话机制**已于 2026-09 移除**（群聊改为必须 @ 或 `/` 命令）；旧 `.env` 里残留的 `AUTO_REPLY_*` 已不生效。

**Q: Dashboard 打不开 / 端口 8501 被占用？**
A: Dashboard 本身重启按钮会自动 clean_stale_port。如果连 Dashboard 都启不开，PowerShell 手动执行：
```powershell
Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue | % { Stop-Process -Id $_.OwningProcess -Force }
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | % { Stop-Process -Id $_.OwningProcess -Force }
```
然后重开 manage.bat。

**Q: Bot 重启后端口冲突 Errno 10048？**
A: 2026-06-27 后 Dashboard 的「重启」按钮都会先调用 `clean_stale_port()` 自动杀占用进程。如果还出现 → 说明有非 Dashboard 启动的残留 python 进程占用，按上面的 PowerShell 命令手动杀一轮。

**Q: API 报错 401 未授权？**
A: 检查 `private/.env` 里的**实际在用的 Provider** API Key：
   - 主对话失败 → 看 `DEEPSEEK_API_KEY`（或 minimax 主选时的 `MINIMAX_API_KEY`）
   - 识图失败 → 默认 deepseek-vision 复用 `DEEPSEEK_API_KEY`；SiliconFlow / MiniMax 兜底时看对应 Key
   别忘了查对应平台账户余额是否为 0（DeepSeek / SiliconFlow / MiniMax 任一个欠费都会 401）。

**Q: 识图不工作（发图 bot 没描述内容）？**
A: 诊断顺序：
1. Dashboard「参数设置」→ `image_handler_enabled` 必须勾选
2. private/.env 里 `DEEPSEEK_VISION_ENABLED=true`（默认视觉，复用 `DEEPSEEK_API_KEY`），或启用 `SILICONFLOW_ENABLED` / `MINIMAX_ENABLED` 作兜底
3. 去对应平台控制台查余额 / Key 是否有效（DeepSeek / SiliconFlow / MiniMax）
4. 临时不行的话：先关 `image_handler_enabled` 保存 → 再勾上 保存 → 重启 NoneBot2。

**Q: 顶栏 pill 一开始红，20+ 秒才变绿？**
A: 2026-06-27 后 SSR 兜底已修好。如果还出现：
   - token 为空时 dashboard 会生成随机 token 并打印到 dashboard.out
   - 刷新浏览器确保走正常登录流程（弹框输 DASHBOARD_TOKEN，localStorage 存下）
   - 有防火墙拦截 127.0.0.1 的 8501 自连接也可能导致 pill 红

**Q: 切换主选 Provider 后没生效？**
A: Dashboard「参数设置」→ 选择 `active_llm_provider` → 点保存。下次新 turn 立即生效（无需重启 NoneBot2）。注意选项目前只有 `deepseek` / `minimax` 两种，SiliconFlow 是视觉专用 Provider 不进对话路由。

**Q: 对话流水 Tab 一直 401？**
A: 浏览器 EventSource（SSE）API 不能带自定义 header，token 通过 `?token=xxx` query 传递。Dashboard 正常登录时前端会自动拼 query。
   - 如果是手动开 URL → 改成 `http://localhost:8501/?token=你的DASHBOARD_TOKEN`
   - 如果忘了 token 又没写 private/.env → 看 `qqbot/logs/dashboard.err`，首次启动没填 `DASHBOARD_TOKEN` 会自动生成并打印到日志。

**Q: test_mode 是什么？为什么群里@了不回？**
A: `test_mode_enabled = true` 时，只有**管理员（ADMIN_QQ）**不受影响：
   - 管理员：私聊 / 群聊都正常触发
   - 非管理员：私聊收到「维护中」提示；群聊静默拦截（用户侧无任何反应）
   默认开着是防止新用户装好就被群里一堆人@把余额打没。群里真要用 → Dashboard「参数设置」取消勾选保存即可。
