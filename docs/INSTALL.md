# 📦 详细安装指南

> **Windows 专属**。Mac / Linux 用户需自行替换 SnowLuma 版本、调整启动脚本和路径。

## 一、前置条件

### 1. Anaconda / Miniconda（Python 3.12）

项目依赖装在独立的 Conda 环境 `botenv`（Python 3.12）里，不直接使用系统 Python。

- [下载 Anaconda](https://www.anaconda.com/download) 或 [Miniconda](https://docs.conda.io/en/latest/miniconda.html)
- 默认按 `D:\Work\anaconda3\` 安装；若装在别处，需改这几处硬编码路径：`scripts/bat/setup.bat`、`scripts/bat/launch_dashboard.ps1`、`qqbot/dashboard/monitor.py`（见 [ARCHITECTURE.md 目录结构](ARCHITECTURE.md#目录结构)）

验证 conda 可用：

```bat
D:\Work\anaconda3\Scripts\conda.exe --version
```

### 2. QQ 桌面版 (QQNT)

[下载最新版 QQ](https://im.qq.com/)。SnowLuma 基于 QQNT 实现 OneBot 协议层。

> 建议给 bot 单独准备一个 QQ 号，不要和日常主力号混用。

### 3. SnowLuma（v2.2.0）

[下载 SnowLuma](https://github.com/MaaYYDS/SnowLuma/releases) 的 `SnowLuma-v*-win-x64.zip`（带 `node.exe` 的版本）。

v2.2.0 的 zip 解压后是**根级文件**（`node.exe` / `index.mjs` / `client/` / `native/`），不再自带版本号目录，所以需要先手动建目录再解压：

```bat
mkdir snowluma\SnowLuma-v2.2.0-win-x64
:: 把 zip 里的 node.exe / index.mjs / client / native 等解压进上面这个目录
```

最终目录结构：

```
snowluma/
└── SnowLuma-v2.2.0-win-x64/
    ├── node.exe
    ├── index.mjs
    ├── client/          # WebUI 静态资源
    ├── config/          # 首次启动后生成（onebot_<QQ>.json 等）
    └── data/            # 登录态与消息库（首次登录后生成）
```

> 目录名保持 `SnowLuma-<版本>-win-x64` 格式；`snowluma/` 下有多个版本目录时，Dashboard 按字典序取第一个。
> v2.2.0 首次打开 WebUI（http://localhost:5099）需要重新同意一次 EULA / 隐私政策（协议文件有更新，旧同意记录会失效；只影响网页面板，不影响 QQ 收发）。

### 4. LLM API Key

| Provider | 用途 | 申请 |
|---|---|---|
| **DeepSeek**（必填） | 主对话、工具调用、流式、用户画像；默认视觉 `deepseek-vision` 复用同一个 Key | https://platform.deepseek.com |
| **SiliconFlow**（可选） | 备用视觉 Provider（Qwen3.5-9B），默认关闭 | https://cloud.siliconflow.cn |
| **MiniMax M3**（可选备用） | 视觉兜底 Provider，默认关闭 | https://api.minimaxi.com |

> 图片描述默认走 DeepSeek 官方 `deepseek-vision`，只填 `DEEPSEEK_API_KEY` 即可；SiliconFlow / MiniMax 仅在前者不可用时手动启用。

### 5. Git

[下载 Git](https://git-scm.com/)（任意版本）。

---

## 二、克隆 + 安装

### 步骤 1：克隆仓库

在任意目录下打开终端（CMD 或 PowerShell），执行：

```bat
git clone https://github.com/KP-i2/qqbot-multiple-character.git
cd qqbot-multiple-character
```

> 以下所有操作都在 `qqbot-multiple-character` 目录内进行。

### 步骤 2：运行安装脚本

双击 `scripts\bat\setup.bat`（或在终端执行）：

```bat
scripts\bat\setup.bat
```

脚本会自动完成：

- ✅ 检测 conda 是否可用
- ✅ 创建 Conda 环境 `botenv`（Python 3.12）
- ✅ 在 `botenv` 里安装 `requirements.txt` 的全部依赖
- ✅ 安装 Playwright Chromium（微博语料抓取用）
- ✅ 从 `qqbot\.env.example` 生成配置文件 `private\.env`
- ✅ 检查 `snowluma\SnowLuma-*` 是否存在

如果看到 `Setup complete.` 字样，说明安装成功。

### 步骤 3：编辑配置文件

用任意文本编辑器打开 `private\.env`，**必填项**：

```ini
# 必填 —— 主对话 Provider
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥

# 强烈建议 —— 默认视觉 Provider（复用上面的 DeepSeek Key）
DEEPSEEK_VISION_ENABLED=true
DEEPSEEK_VISION_MODEL=deepseek-v4-flash-vision-exp

# 必填 —— 你的 QQ 号（管理员；当前命令校验按单个 QQ 号匹配）
# 不填的话所有管理员专属命令都会被拒绝
ADMIN_QQ=123456789

# 建议修改 —— Dashboard 登录密码（留空会随机生成并打印到日志）
DASHBOARD_TOKEN=自定义一个密码

# 默认角色（公开仓库自带 makise-kurisu / amadeus-kurisu 两个示例，可不改）
DEFAULT_SKILL=makise-kurisu

# 可选视觉 Provider（默认关闭，需要时再开）
# SILICONFLOW_ENABLED=true
# SILICONFLOW_API_KEY=sk-你的SiliconFlow密钥
# MINIMAX_ENABLED=true
# MINIMAX_API_KEY=sk-cp-你的MiniMax密钥
```

> 其余配置项保持默认即可，后续可在 Dashboard「参数设置」中动态调整。

### 步骤 4：写 SnowLuma 反向 WebSocket 配置

让 SnowLuma 自动连 NoneBot2（端口 8080）：

```bat
scripts\bat\setup_snowluma.bat
```

脚本会：

1. 自动检测 `snowluma\SnowLuma-*\` 目录
2. 若 `config\onebot_*.json` 已存在则跳过（不覆盖真值配置）
3. 否则提示输入 QQ 号，把 `qqbot\snowluma_onebot_config.example.json` 复制为 `snowluma\SnowLuma-<版本>\config\onebot_<QQ号>.json`
4. 配置反向 WebSocket：`ws://127.0.0.1:8080/onebot/v11/ws`

> SnowLuma 配置说明（一般不用手动改）：
>
> | 参数 | 值 |
> |---|---|
> | 启用 | 开（`enabled: true`） |
> | 名称 | `qqbot`（随意） |
> | URL | `ws://127.0.0.1:8080/onebot/v11/ws` |
> | 消息格式 | `array` |
> | Token | 留空（NoneBot2 未配置 token 时不校验） |
> | 重连间隔 | `5000` ms（配置字段 `reconnectIntervalMs`） |
> | 上报自身消息 | 关 |
>
> v2.2.0 的 `wsClients` 不再有独立的「心跳间隔」字段（旧版 `heartInterval` 已废弃，会被兼容解析器忽略）。

### 步骤 5：首次扫码登录 QQ

**方式 A（推荐）**：Dashboard 启动 SnowLuma

1. 双击 `manage.bat`，浏览器访问 `http://localhost:8501`
2. 用 `.env` 里的 `DASHBOARD_TOKEN` 登录
3. 进程管理 → SnowLuma 卡片点「启动」
4. 打开 SnowLuma WebUI：`http://localhost:5099`，输入机器人 QQ 号 → 生成二维码 → 手机 QQ 扫码确认
5. 看到「在线」状态即可；之后 Dashboard 启动 SnowLuma 会自动登录，无需再扫码

**方式 B（命令行）**：

```bat
cd snowluma\SnowLuma-v2.2.0-win-x64
node.exe 你的QQ号
```

扫码后看到 `SnowLuma.Core Version: x.x.x` 输出，按 `Ctrl+C` 或关闭窗口即可（首次登录只需一次）。

> ⚠️ 机器人号不要同时在手机 QQ / 电脑 QQ 客户端登录，否则会被腾讯挤下线。

### 步骤 6：启动 Bot

**方式一（推荐）**：用 manage.bat 一键拉起 Dashboard

```bat
manage.bat
```

脚本会在后台启动 Dashboard（无黑框），然后手动打开 `http://localhost:8501`，输入 `.env` 中设置的 `DASHBOARD_TOKEN` 进入。

在 Dashboard → 进程管理 里依次点：

1. NoneBot2 卡片 →「启动」（等 pill 变绿、8080 监听）
2. SnowLuma 卡片 →「启动」（等 WebSocket 显示已连接）

**方式二（调试用）**：命令行前台启动 Dashboard

```bat
D:\Work\anaconda3\envs\botenv\python.exe qqbot\dashboard.py
```

> **桌面快捷方式**：运行 `scripts\bat\create_shortcut.bat` 可在桌面创建「QQ Bot」快捷方式，以后双击即可打开 Dashboard。

---

## 三、验证安装

Dashboard 启动后，检查：

| 检查项 | 怎么看 |
|---|---|
| Dashboard 进程正常 | 顶栏 pill 显示绿色 |
| NoneBot2 启动成功 | 进程管理 tab → NoneBot2 卡片显示「运行中」+ 端口 8080 监听 |
| SnowLuma 启动成功 | 进程管理 tab → SnowLuma 卡片显示「运行中」+ WebSocket 已连接 |
| LLM Provider 在线 | KPI 卡 → 主对话 LLM + 图片描述 LLM 都显示绿色 |

测试 bot 回复：

1. 私聊给 bot 发任意消息，应该立即收到回复
2. 群聊里 `@bot 消息` 触发回复

> ⚠️ **群里其他人 @ 了不回**：先检查 Dashboard「参数设置」里的 `test_mode_enabled`。默认是开启的，**非管理员**的群聊消息会被静默拦截（管理员自己的号不受影响，私聊也不受影响），取消勾选保存即可。

如果 QQ 收不到消息，按 [docs/SOP.md 故障排查](SOP.md#sop-07-故障排查清单按最常见最不常见排序) 或 [docs/SECURITY.md 常见问题](SECURITY.md#常见问题) 排查。

---

## 四、卸载 / 重置

清理所有运行时数据 + 重装：

```bat
:: 1. 先在 Dashboard 进程管理里停止 NoneBot2 / SnowLuma / Dashboard
::    （或用页面上的 full-restart 按钮）

:: 2. 删除运行时残留（不会动 private\.env 和角色/语料）
rmdir /s /q qqbot\logs
rmdir /s /q qqbot\data
rmdir /s /q .runtime
rmdir /s /q .pids

:: 3. 重新跑安装
scripts\bat\setup.bat
```

> ⚠️ 以上命令只删运行日志与缓存，不会删除：
>
> - `private\.env`（API Key / 管理员 QQ）
> - `qqbot\characters\<你的角色>\`（自建角色；公开仓库只有 2 个示例角色，其余不会被覆盖）
> - `emoji\` + `photo\` + `corpus\` + `normal-paper\`（本地的表情、头像、语料与共享背景）
>
> 这些都是 `.gitignore` 保护的内容，重置前建议先备份。
