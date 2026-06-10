# QQ Bot 多角色扮演系统

基于 NoneBot2 + DeepSeek API + NapCat 的多角色扮演 QQ 机器人，支持语料采集 → Skill 蒸馏 → 实时切换，内置联网搜索和中文 Web 管理面板。

## 架构

```
QQ 客户端 ←→ NapCat (OneBot v11) ←WebSocket→ NoneBot2 (port 8080)
                                                      ↕
                                                zyw_chat 插件
                                                ├─ DeepSeek / OpenAI API
                                                └─ 百度+微博+搜狗+DDG 联网搜索

Dashboard (port 8501) ←→ 进程管理 / 语料采集 / Skill 蒸馏 / 头像管理 / 热重载
```

## 新手上路

### 前置条件

| 软件 | 要求 | 下载 |
|------|------|------|
| Python | 3.10+，安装时勾选 "Add to PATH" | [python.org](https://www.python.org/) |
| QQ 桌面版 (QQNT) | 最新版 | [im.qq.com](https://im.qq.com/) |
| NapCat | 最新版 | [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) |
| DeepSeek API Key | 注册获取 | [platform.deepseek.com](https://platform.deepseek.com) |

### 安装步骤

**1. 克隆仓库**

```bash
git clone <你的仓库地址>
cd skill_communication
```

**2. 运行安装脚本**

双击 `setup.bat`，会自动：
- 检测 Python 环境
- 创建虚拟环境 `skill_qqbot/`
- 安装所有依赖

**3. 配置**

```bash
# 复制配置模板并编辑
cp qqbot/.env.example qqbot/.env
```

编辑 `qqbot/.env`，填入以下信息：

```bash
DEEPSEEK_API_KEY=sk-你的deepseek-api-key    # 必填，从 platform.deepseek.com 获取
ADMIN_QQ=你的QQ号                            # 必填
DASHBOARD_TOKEN=自定义密码                    # Dashboard 访问密码
DEFAULT_SKILL=761                           # 启动时的默认角色（示例为 761）
```

**4. 安装 NapCat 并配置**

- 从 [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 `NapCat.Shell.Windows.OneKey.zip`
- 解压到 `qqbot/napcat/` 目录下，确保 `NapCatWinBootMain.exe` 在 `qqbot/napcat/NapCat.44498.Shell/` 内
- 运行 `setup_napcat.bat` 写入连接配置

**5. 首次登录 QQ**

```bash
# 启动 NapCat 扫码登录
cd qqbot\napcat\NapCat.44498.Shell
.\NapCatWinBootMain.exe 你的QQ号
```

用手机 QQ 扫码。看到 "NapCat.Core Version: x.x.x" 后关闭窗口。

**6. 启动**

```bat
:: 启动 Bot + NapCat
start_all.bat

:: 启动管理面板（自动打开浏览器）
dashboard_silent.bat
```

打开浏览器访问 `http://localhost:8501`，输入 Dashboard 密码。

## QQ Bot 命令

| 命令 | 说明 | 权限 |
|------|------|------|
| `/skills` | 查看所有可用角色 | 所有人 |
| `/switch <角色名>` | 切换角色 | 仅管理员 |
| `/current` | 查看当前角色和模型 | 所有人 |
| `/reset` | 清空对话记忆 | 所有人 |
| `/reloademoji` | 热重载表情文件 | 仅管理员 |

私聊直接发消息，群聊 @Bot 触发回复。

## Dashboard 管理面板

访问 `http://localhost:8501`

| 页面 | 功能 |
|------|------|
| 进程管理 | KPI 总览、服务启停、看门狗、角色花名册 |
| Cookie | 微博 Cookie 状态查看与上传 |
| 语料库 | 微博抓取、QQ 群聊导入、语料 → Skill 生成 |
| 技能管理 | 角色 Skill 列表/编辑/蒸馏/创建 |
| 参数设置 | 运行时参数动态调整 |

## 配置参考

完整配置项见 `qqbot/.env.example`：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（必填） | — |
| `DEEPSEEK_MODEL` | 模型名称 | `deepseek-v4-pro` |
| `OPENAI_ENABLED` | 启用 OpenAI 主选 | `false` |
| `ADMIN_QQ` | 管理员 QQ 号 | — |
| `DASHBOARD_TOKEN` | Dashboard 访问密码 | — |
| `DEFAULT_SKILL` | 启动默认角色 | `761` |
| `WEB_SEARCH_ENABLED` | 联网搜索开关 | `true` |
| `ACTIVE_HOURS_START/END` | 活跃时段 | `0 / 23`（全天） |

## 目录结构

```
skill_communication/
├── qqbot/                     # QQ Bot 应用
│   ├── bot.py                 # NoneBot2 入口
│   ├── dashboard.py           # Dashboard 入口
│   ├── plugins/zyw_chat/      # 核心聊天插件
│   ├── dashboard/             # Web 管理面板
│   │   ├── main.py            # FastAPI 路由
│   │   ├── monitor.py         # 进程监控 + 看门狗
│   │   ├── skill_manager.py   # Skill CRUD
│   │   ├── weibo_fetcher.py   # 语料采集 + Skill 蒸馏
│   │   └── static/            # 前端界面
│   ├── skills/                # 角色 Skill 目录
│   ├── .env.example           # 配置模板
│   └── pyproject.toml         # NoneBot2 配置
├── scripts/                   # 工具脚本
│   └── weibo_pw_cookies.py    # 微博语料抓取
├── emoji/                     # 情绪表情资源
├── requirements.txt           # Python 依赖
├── .gitignore
└── README.md
```

## 运行环境说明

本项目设计为 Windows 本地运行：

- `.bat` 脚本均在 Windows 下测试
- NapCat 使用 Windows 版本（`NapCatWinBootMain.exe`）
- 虚拟环境路径硬编码在 `skill_qqbot/` 目录下

Mac/Linux 用户需自行替换 NapCat 版本、调整启动脚本和路径。

## 添加新角色 Skill

每个角色是一个 `qqbot/skills/角色名/` 目录，最少需要两个文件：

### 文件结构

```
qqbot/skills/新角色/
├── SKILL.md       # 元数据（角色名、描述、版本）
├── persona.md     # 人设（七层结构：核心规则→关系→表达DNA→情感→冲突→记忆）
└── work.md        # 工作方式（可选）
```

### SKILL.md 模板

```markdown
---
display_name: 角色显示名
description: 一行描述
version: 1.0.0
---
```

### persona.md 结构

```markdown
# 角色名 — Persona

## Layer 0: Core Rules（行为底线）
- 你不做什么 / 永远怎么做

## Layer 1: Context（身份和关系）
- 你是谁，和用户什么关系

## Layer 2: Expression DNA（说话方式）
- 口头禅、节奏、语言特征

## Layer 3: Emotional Logic（情感模式）
- 什么时候开心/沉默/防御

## Layer 4: Conflict and Repair（冲突处理）
- 如何回避冲突、如何修复关系

## Layer 5: Memory Signature（核心记忆）
- 最重要的几个记忆锚点
```

### 创建方式

**方式一：Dashboard 语料蒸馏（推荐）**

1. 在语料库页面抓取微博或导入 QQ 聊天记录
2. 点击语料旁的「生成技能」
3. 选择角色类型（名人/同事/亲密关系），系统自动调用 DeepSeek 生成

**方式二：手动创建**

1. 在 `qqbot/skills/` 下新建目录
2. 创建 `SKILL.md`、`persona.md`、`work.md`
3. 在 Dashboard 技能管理页点击「重载技能」或 Bot 下次收到消息时自动加载

**方式三：Dashboard 创建**

在 Dashboard 技能管理页点击「创建」，填写角色信息后系统自动生成模板文件。

### 头像

在 `photo/角色名/` 下放入 `avatar.jpg` 或 `avatar.png`，Dashboard 角色花名册会自动展示。

## 技术栈

| 组件 | 技术 |
|------|------|
| Bot 框架 | NoneBot2 + OneBot V11 Adapter |
| QQ 协议 | NapCat |
| AI 模型 | DeepSeek / OpenAI（双 Provider 回退） |
| 联网搜索 | 百度 + 微博 + 搜狗 + DuckDuckGo（多源并行，健康检查，自动降级） |
| Dashboard | FastAPI + Uvicorn + WebSocket |
| 前端 | 原生 HTML/CSS/JS, 6 套主题 |
| 语料蒸馏 | DeepSeek API |
| 进程监控 | psutil + asyncio 看门狗 |

## License

MIT
