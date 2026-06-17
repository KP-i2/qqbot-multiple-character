"""
zyw 人格 QQ 聊天插件 (多 Skill 版)
支持多个角色 Skill 目录，可通过命令切换

拆分后的模块结构：
  config.py          - 全局配置、路径、运行时设置
  provider.py        - LLM Provider 管理（OpenAI/DeepSeek）
  skill_manager.py   - Skill 加载、热重载
  avatar.py          - QQ 头像/昵称切换
  history.py         - 对话历史管理、持久化
  user_profile.py    - 用户画像提取
  emoji_system.py    - 情绪表情系统
  search.py          - 搜索引擎（搜狗/百度/微博/DDG/语料库）
  url_fetcher.py     - URL 提取与内容抓取（B站API）
  api_client.py      - HTTP 客户端、API 请求、流式
  llm.py             - LLM 调用逻辑（探测+Function Calling）
  dsml_cleaner.py    - DSML/XML 标记清洗
  message_utils.py   - 消息分段、QQ 表情解析
  rich_message.py    - QQ 富媒体消息解析
  rules.py           - 消息匹配规则
  commands.py        - 命令注册与处理
  chat_handler.py    - 主聊天处理器
  lifecycle.py       - 启动/关闭生命周期
"""

# 按依赖顺序导入所有模块，确保 NoneBot 发现并注册所有处理器

# 基础层
from . import config                          # noqa: F401  全局配置
from . import provider                        # noqa: F401  Provider 管理

# Skill 管理（注册 event_preprocessor 用于热重载检测）
from . import skill_manager                   # noqa: F401

# 对话状态
from . import history                         # noqa: F401
from . import user_profile                    # noqa: F401

# API 与搜索
from . import api_client                      # noqa: F401
from . import search                          # noqa: F401
from . import url_fetcher                     # noqa: F401

# 工具模块
from . import dsml_cleaner                    # noqa: F401
from . import message_utils                   # noqa: F401
from . import rich_message                    # noqa: F401

# 表情系统
from . import emoji_system                    # noqa: F401

# LLM 调用
from . import llm                             # noqa: F401

# 消息规则
from . import rules                           # noqa: F401

# 命令注册（on_command handlers）
from . import commands                        # noqa: F401

# 主聊天处理器（on_message handler）
from . import chat_handler                    # noqa: F401

# 生命周期（on_startup / on_shutdown）
from . import lifecycle                       # noqa: F401
