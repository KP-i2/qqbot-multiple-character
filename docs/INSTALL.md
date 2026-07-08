# 📦 详细安装指南

> **Windows 专属**。Mac / Linux 用户需自行替换 SnowLuma 版本、调整启动脚本和路径。

## 一、前置条件

### 1. Python 3.10+

[下载 Python](https://www.python.org/downloads/)（3.10 或更高版本）

**安装时务必勾选 "Add Python to PATH"**，否则后续命令找不到 `python`。

验证安装：

```bat
python --version
:: 应该输出: Python 3.10.x 或更高
```

### 2. QQ 桌面版 (QQNT)

[下载最新版 QQ](https://im.qq.com/)。SnowLuma 是基于 QQNT 实现的 OneBot 协议层。

### 3. SnowLuma

[下载 SnowLuma](https://github.com/NapNeko/snowluma/releases) **OneKey 版**（`SnowLuma.Shell.Windows.OneKey.zip`，v1.10.3 或更新）。

解压 zip 内的 `snowluma.xxxxx.Shell` 文件夹到本项目的 `snowluma/` 目录下，最终目录结构：

```
snowluma/
└── SnowLuma-v1.10.3-win-x64/
    └── node.exe (snowluma)
```

> `xxxxx` 为版本号，如 `SnowLuma-v1.10.3-win-x64`。

### 4. LLM API Key

| Provider | 用途 | 申请 |
|---|---|---|
| **DeepSeek**（必填） | 主对话、工具调用、流式、用户画像 | https://platform.deepseek.com |
| **MiniMax M3**（强烈建议） | 图片描述（用户发图时识别） | https://api.minimaxi.com |

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

- ✅ 检测 Python 版本
- ✅ 创建虚拟环境 `skill_qqbot/`（项目根）
- ✅ 安装所有 Python 依赖（NoneBot2 / playwright / twikit 等）
- ✅ 安装 Playwright Chromium（仅微博 fetcher 需要）
- ✅ 从模板生成配置文件 `private\.env`
- ✅ 检测 SnowLuma 是否已下载

如果看到 `Setup complete.` 字样，说明安装成功。

### 步骤 3：编辑配置文件

用任意文本编辑器打开 `private\.env`，**必填项**：

```ini
# 必填 —— 主对话 Provider
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥

# 强烈建议 —— 图片描述专用（不填的话用户发图会 400）
MINIMAX_ENABLED=true
MINIMAX_API_KEY=sk-cp-你的MiniMax密钥

# 必填 —— 你的 QQ 号（**唯一管理员**）
ADMIN_QQ=你的QQ号

# 建议修改 —— Dashboard 登录密码
DASHBOARD_TOKEN=自定义一个密码

# 默认角色（已预置示例 makise-kurisu，可不改）
DEFAULT_SKILL=makise-kurisu
```

> 其余配置项保持默认即可，后续可在 Dashboard 中动态调整。

### 步骤 4：写 SnowLuma 反向 WebSocket 配置

让 SnowLuma 自动连 NoneBot2（端口 8080）：

```bat
scripts\bat\setup_snowluma.bat
```

脚本会：

1. 自动检测 `snowluma\SnowLuma-*\` 目录
2. 复制 `qqbot\snowluma_onebot_config.json` 模板到 `snowluma\SnowLuma-*\config\onebot_<QQ号>.json`
3. 配置反向 WebSocket：`ws://127.0.0.1:8080/onebot/v11/ws`

> SnowLuma 配置说明（一般不用手动改）：
>
> | 参数 | 值 |
> |---|---|
> | 启用 | 开 |
> | 名称 | `zyw`（随意） |
> | URL | `ws://127.0.0.1:8080/onebot/v11/ws` |
> | 消息格式 | `array` |
> | Token | 留空 |
> | 心跳间隔 | `30000`（默认） |
> | 重连间隔 | `5000`（默认） |
> | 上报自身消息 | 关 |

### 步骤 5：首次扫码登录 QQ

启动 SnowLuma：

```bat
cd snowluma\SnowLuma-v1.10.3-win-x64
node.exe (snowluma) 你的QQ号
```

手机 QQ 扫码确认登录。看到 `SnowLuma.Core Version: x.x.x` 输出后，按 `Ctrl+C` 或关闭窗口即可（首次登录只需一次，之后 Dashboard 会自动启动）。

### 步骤 6：启动 Bot

**方式一（推荐）**：用 manage.bat 一键拉起 Dashboard

```bat
:: 回到项目根目录
cd ..\..\..

:: 启动
manage.bat
```

Dashboard 在 `http://localhost:8501` 打开，输入 `.env` 中设置的 `DASHBOARD_TOKEN` 即可进入。

**方式二**：Dashboard 页面里手动控制 bot / SnowLuma

打开 `http://localhost:8501`，在控制面板点「启动」/「停止」按钮。

> **桌面快捷方式**：运行 `scripts\bat\create_shortcut.bat` 可在桌面创建 Dashboard 快捷方式，以后双击即可打开。

---

## 三、验证安装

Dashboard 启动后，检查：

| 检查项 | 怎么看 |
|---|---|
| Dashboard 进程正常 | 顶栏 pill 显示绿色 |
| NoneBot2 启动成功 | 进程管理 tab → NoneBot2 卡片显示 "运行中" + 端口 8080 监听 |
| SnowLuma 启动成功 | 进程管理 tab → SnowLuma 卡片显示 "运行中" + WebSocket 已连 |
| LLM Provider 在线 | KPI 卡 → 主对话 LLM + 图片描述 LLM 都显示绿色 |

测试 bot 回复：

1. 私聊给 bot 发任意消息，应该立即收到回复
2. 群聊里 `@bot 消息` 触发回复

如果 QQ 收不到消息，按 [docs/SECURITY.md 常见问题](SECURITY.md#常见问题) 排查。

---

## 四、卸载 / 重置

清理所有运行时数据 + 重装：

```bat
:: 停掉所有进程（Dashboard 网页里的 full-restart 按钮也行）
scripts\bat\manage-equivalent stop  :: 或者用 Dashboard

:: 删除运行时残留
mavis-trash skill_qqbot logs qqbot\logs qqbot\data .runtime

:: 重新跑安装
scripts\bat\setup.bat
```

> ⚠️ 这不会删除你的 `private/.env`（保留 API Key）+ `qqbot/skills/<你的真实角色>/`（保留自建角色）+ `emoji/` + `photo/` + `corpus/` 下的真实语料。这些都是 `.gitignore` 保护的内容。