# QQ Bot Dashboard - 多角色 Skill 管理面板

基于 NoneBot2 + DeepSeek API 的角色扮演 QQ 机器人，配套 Web Dashboard 管理进程、技能、语料和参数。

## 架构

```
QQ 客户端 ←→ NapCat Desktop (OneBot v11) ←WebSocket→ NoneBot2 (port 8080)
                                                          ↕
                                                    zyw_chat 插件
                                                    ↕         ↕
                                              DeepSeek API   搜狗/微博搜索
                                                          
Dashboard (FastAPI, port 8501) ←REST/WebSocket→ 浏览器管理面板
```

## 项目结构

```
qqbot/
├── bot.py                          # NoneBot2 入口
├── dashboard.py                    # Dashboard 入口 (FastAPI, port 8501)
├── .env                            # 环境配置 (API Key, 端口等)
├── .env.example                    # 配置模板
├── pyproject.toml                  # NoneBot2 项目配置
├── requirements.txt                # Python 依赖
├── start.bat                       # 命令行启动 Bot
├── napcat_onebot_config.json       # NapCat WebSocket 连接配置
│
├── plugins/zyw_chat/
│   └── __init__.py                 # 核心插件 (消息处理/DeepSeek调用/搜索/Skill加载)
│
├── dashboard/
│   ├── main.py                     # FastAPI 应用 (REST API + WebSocket + 静态文件)
│   ├── monitor.py                  # 进程监控 + 看门狗 (自动重启)
│   ├── skill_manager.py            # Skill CRUD 操作
│   ├── weibo_fetcher.py            # 微博语料采集 + Skill蒸馏引擎
│   └── static/
│       ├── index.html              # Dashboard 单页应用 (6标签页 + 4主题)
│       └── photo/{name}/           # 角色头像目录
│
├── skills/                         # 角色 Skill 目录 (热加载)
│   └── {name}/
│       ├── SKILL.md                #   元数据 (YAML frontmatter)
│       ├── persona.md              #   人格模型 (7层结构)
│       ├── work.md                 #   工作能力/方法论
│       └── corpus_ref/             #   关联语料 (微博/QQ/文本)
│
├── data/                           # 运行时数据 (历史对话等)
├── logs/                           # 日志目录
└── napcat/                         # NapCat QQ 协议端
```

## 快速部署

1. 安装依赖: `pip install -r requirements.txt`
2. 编辑 `.env` 填入 DeepSeek API Key
3. 安装 NapCat Desktop 并扫码登录 Bot QQ 号
4. 启动 Bot: `python bot.py` (或 `start.bat`)
5. 启动 Dashboard: `python dashboard.py`
6. 浏览器访问 `http://localhost:8501`

## 配置说明

`.env` 主要配置项:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | (必填) |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型选择 | `deepseek-chat` |
| `PORT` | NoneBot2 监听端口 | `8080` |
| `ADMIN_QQ` | 管理员 QQ 号 | (选填) |
| `DASHBOARD_TOKEN` | Dashboard 访问令牌 | (空=免认证) |
| `WEB_SEARCH_ENABLED` | 联网搜索开关 | `true` |
| `ACTIVE_HOURS_START/END` | Bot 活跃时段 | `0` / `23` |

模型选择: `deepseek-chat` 响应快成本低，`deepseek-v4-pro` 质量更高。

## QQ 端使用

**私聊**: 直接发消息给 Bot，自动回复

**群聊**: @Bot 触发回复

**指令**:

| 命令 | 说明 |
|------|------|
| `/skills` | 查看可用角色列表 |
| `/switch <名字>` | 切换角色并清空记忆 |
| `/current` | 查看当前角色信息 |
| `/reset` | 清空当前对话记忆 |

## 联网搜索

Bot 收到无法回答的问题时自动触发联网搜索，搜索流程:

**第一轮 (并行)**:
- 微博全局搜索 (`statuses/search` API, 需 Cookie)
- 搜狗站点搜索: `chinaidols.fandom.com` (中国地下偶像 Wiki)
- 搜狗站点搜索: `cmks.top`

**第二轮 (补充)**:
- 搜狗通用搜索 (无站点限制)

搜索参数可在 Dashboard 参数设置中开关，超时 12 秒/次，关键词自动简化。

## Dashboard 功能

### 进程管理
- NoneBot2 启停/重启，看门狗自动守护
- NapCat Desktop 状态监控 (只读)
- 角色花名册 + 头像上传
- WebSocket 实时状态推送

### Cookie 管理
- 微博 Cookie JSON 上传/状态检查
- 用于微博全局搜索和语料采集

### 语料库管理
- **微博抓取**: 输入 UID 自动采集微博帖子
- **QQ 聊天记录**: 上传 QQChatExporter JSON，按发送者筛选导入
- **文本导入**: 支持 .md/.txt/.log 文件
- 语料预览，从语料一键生成 Skill

### 技能管理
- Skill 列表 (卡片视图)
- 在线编辑 `persona.md` / `work.md` / `SKILL.md`
- AI 智能特征整合 (输入特征描述，DeepSeek 自动融入人设)
- Skill 蒸馏 (2阶段标准 / 4阶段深度，从语料自动生成人格模型)
- 新建/删除 Skill，头像管理

### 参数设置 (热更新)
- Bot 行为: 活跃时段、联网搜索、多轮对话
- 流式输出: 断句阈值、等待间隔、单段上限
- 历史管理: 最大轮数、过期时间、保存间隔
- 响应时间: 等待提示延迟

### 主题 (4套)
- 霓虹终端 (默认，青色赛博朋克)
- 午夜银河 (深紫色调)
- 极地霜白 (亮色方案)
- 绯红机甲 (红黑机甲风 + 专属背景图)

## Skill 文件格式

每个 Skill 目录包含:

**SKILL.md** (YAML frontmatter):
```yaml
---
name: your-character
display_name: 角色显示名
description: 一行描述
version: 1.0.0
---
```

**persona.md**: 7层人格模型 (身份/表达DNA/心智模型/决策启发/反模式/知识谱系/代理协议)

**work.md**: 工作能力、方法论、专业知识

Skill 支持 `corpus_ref/` 子目录存放关联语料，蒸馏时作为输入素材。

## 依赖

| 包 | 用途 |
|---|------|
| `nonebot2[fastapi]` | Bot 框架 |
| `nonebot-adapter-onebot` | OneBot v11 协议适配 |
| `httpx` | 异步 HTTP (DeepSeek/搜索) |
| `uvicorn` | Dashboard ASGI 服务器 |
| `psutil` | 进程监控 |
| `Pillow` | 头像处理 |
| `python-dotenv` | 环境变量 |
| `python-multipart` | 文件上传 |

## NapCat 安装

1. 安装 QQNT 桌面客户端
2. 下载 [NapCatQQ](https://github.com/NapNeko/NapCatQQ/releases)
3. 解压到 `napcat/` 目录
4. 运行 `NapCatWinBootMain.exe` 扫码登录
5. 在 NapCat Desktop 中配置 WebSocket 客户端连接 `ws://127.0.0.1:8080/onebot/v11/ws`
