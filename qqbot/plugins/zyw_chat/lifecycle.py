"""
Bot 生命周期管理（启动/关闭）
"""

import asyncio
from typing import Optional

from nonebot import logger

from . import config as cfg
from . import api_client
from . import emoji_system
from . import history as hist
from . import skill_manager


_cleanup_task: Optional[asyncio.Task] = None


def _safe_create_task(coro, name: str = ""):
    """创建带错误日志的后台任务"""
    task = asyncio.create_task(coro, name=name)
    def _on_done(t):
        if not t.cancelled() and t.exception():
            logger.error(f"Background task '{name}' failed: {t.exception()}")
    task.add_done_callback(_on_done)
    return task


@cfg.driver.on_startup
async def on_startup():
    global _cleanup_task
    logger.info(f"zyw QQ Bot 启动成功")
    logger.info(f"已加载 {len(skill_manager.ALL_SKILLS)} 个角色 Skill")
    if skill_manager.DEFAULT_SKILL_NAME:
        logger.info(f"默认角色：{skill_manager.DEFAULT_SKILL_NAME}")
    if not cfg.DEEPSEEK_API_KEY or cfg.DEEPSEEK_API_KEY.startswith("sk-your"):
        logger.warning("DEEPSEEK_API_KEY 未配置！请在 .env 文件中设置。")
    # 显示 LLM 配置（2026-06-28 重构：双 provider 分工）
    # 主对话 Provider (active_llm_provider)：所有对话 / 工具 / 流式 / 用户画像
    active = getattr(cfg, 'ACTIVE_LLM_PROVIDER', 'deepseek')
    if active == 'minimax':
        logger.info(f"主对话 Provider: minimax | model={cfg.MINIMAX_MODEL}")
    else:
        logger.info(f"主对话 Provider: deepseek | model={cfg.DEEPSEEK_MODEL}")
    # 图片描述 Provider：minimax 专用 (看图说话,不参与对话)
    if cfg.MINIMAX_ENABLED and cfg.MINIMAX_API_KEY and cfg.MINIMAX_BASE_URL:
        logger.info(f"图片描述 Provider: minimax | model={cfg.MINIMAX_MODEL} (只处理 image_url)")
    else:
        logger.warning("minimax 不可用 → 含图消息将走主对话 Provider,可能触发 deepseek image_url 400")
    # 列出可用 provider（dashboard 显示用）
    available = []
    if cfg.DEEPSEEK_API_KEY and not cfg.DEEPSEEK_API_KEY.startswith("sk-your"):
        available.append("deepseek")
    if cfg.MINIMAX_ENABLED and cfg.MINIMAX_API_KEY and cfg.MINIMAX_BASE_URL:
        available.append("minimax")
    logger.info(f"可用 LLM Providers: {available}")
    # 初始化持久化 HTTP 客户端
    api_client.get_http_client()
    # 加载情绪表情文件
    emoji_system.load_emoji_files()
    # 启动定期对话历史清理
    _cleanup_task = _safe_create_task(hist.periodic_history_cleanup(), "history-cleanup")
    logger.info("HTTP client initialized, history cleanup task started")


@cfg.driver.on_shutdown
async def on_shutdown():
    global _cleanup_task
    # 关闭 HTTP 客户端
    client = api_client.close_http_client()
    if client and not client.is_closed:
        await client.aclose()
    # 取消清理任务
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
    logger.info("zyw QQ Bot 已关闭（HTTP client closed, cleanup task cancelled）")
