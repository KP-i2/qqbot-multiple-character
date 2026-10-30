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
    logger.info(f"char_chat QQ Bot 启动成功")
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
    # 联网搜索（2026-10-30 起只走 DeepSeek 官方 Search API）
    if cfg.DEEPSEEK_SEARCH_ENABLED:
        logger.info(
            f"联网搜索: DeepSeek Search API | count={cfg.DEEPSEEK_SEARCH_COUNT} "
            f"| timeout={cfg.DEEPSEEK_SEARCH_TIMEOUT}s | summary_max={cfg.DEEPSEEK_SEARCH_SUMMARY_MAX}"
        )
    else:
        logger.warning("联网搜索已关闭 (DEEPSEEK_SEARCH_ENABLED=false)")
    logger.info(
        f"LLM 超时预算: total={cfg.LLM_TOTAL_TIMEOUT:.0f}s "
        f"| request={cfg.LLM_REQUEST_TIMEOUT:.0f}s x{cfg.LLM_MAX_RETRIES}"
    )
    # 图片描述 Provider：优先硅基流动 Qwen3.5-9B, minimax 兜底 (2026-07-23 接入)
    # 2026-07-23 优化: warning 条件改为"用户开了识图但 vision 不可用"时才报,
    # 避免 image_handler_enabled=False 时出现误报 warning
    if cfg.IMAGE_HANDLER_ENABLED and cfg.DEEPSEEK_VISION_ENABLED and cfg.DEEPSEEK_API_KEY and cfg.DEEPSEEK_BASE_URL:
        logger.info(f"图片描述 Provider: deepseek-vision | model={cfg.DEEPSEEK_VISION_MODEL} (只处理 image_url)")
    elif cfg.IMAGE_HANDLER_ENABLED and cfg.SILICONFLOW_ENABLED and cfg.SILICONFLOW_API_KEY and cfg.SILICONFLOW_BASE_URL:
        logger.info(f"图片描述 Provider: siliconflow | model={cfg.SILICONFLOW_MODEL} (只处理 image_url)")
    elif cfg.IMAGE_HANDLER_ENABLED and cfg.MINIMAX_ENABLED and cfg.MINIMAX_API_KEY and cfg.MINIMAX_BASE_URL:
        logger.info(f"图片描述 Provider: minimax (fallback) | model={cfg.MINIMAX_MODEL} (只处理 image_url)")
    elif cfg.IMAGE_HANDLER_ENABLED:
        logger.warning("识图已开启但 deepseek-vision/siliconflow/minimax 都不可用 → 含图消息会走降级占位")
    else:
        logger.info("图片描述: 已关闭 (image_handler_enabled=False)")
    # 列出可用 provider（dashboard 显示用）
    available = []
    if cfg.DEEPSEEK_API_KEY and not cfg.DEEPSEEK_API_KEY.startswith("sk-your"):
        available.append("deepseek")
    if cfg.DEEPSEEK_VISION_ENABLED and cfg.DEEPSEEK_API_KEY and cfg.DEEPSEEK_BASE_URL:
        available.append("deepseek-vision")
    if cfg.SILICONFLOW_ENABLED and cfg.SILICONFLOW_API_KEY and cfg.SILICONFLOW_BASE_URL:
        available.append("siliconflow")
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
    logger.info("char_chat QQ Bot 已关闭（HTTP client closed, cleanup task cancelled）")
