"""
QQ 头像/昵称管理
"""

from pathlib import Path
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot

from . import config as cfg
from . import skill_manager


def _get_avatar_path(skill_name: str) -> Optional[Path]:
    """从 photo/{skill_name}/ 目录找到原始图片路径（不处理格式，直接用原图）"""
    skill_photo_dir = cfg.PHOTO_DIR / skill_name
    if not skill_photo_dir.exists():
        return None
    for ext in (".jpg", ".jpeg", ".png"):
        for f in skill_photo_dir.iterdir():
            if f.suffix.lower() == ext and not f.name.startswith("_avatar"):
                logger.info(f"Avatar found: {skill_name} -> {f.name} ({f.stat().st_size / 1024:.0f}KB)")
                return f
    return None


async def set_profile(bot: Bot, skill_name: str):
    """尝试切换 QQ 昵称和头像（NTQQ 可能不支持，优雅降级）"""
    skill = skill_manager.ALL_SKILLS.get(skill_name)
    nickname = skill.display_name if skill else skill_name

    logger.info(f"[_set_profile] Attempting: {skill_name} -> {nickname}")

    # 昵称
    try:
        result = await bot.call_api("set_qq_profile", nickname=nickname)
        if isinstance(result, dict) and result.get("result", -1) == 0:
            logger.info(f"[_set_profile] Nickname OK: {nickname}")
        else:
            logger.warning(f"[_set_profile] Nickname rejected (NTQQ limitation): {result}")
    except Exception as e:
        logger.warning(f"[_set_profile] Nickname failed (NTQQ limitation): {e}")

    # 头像
    avatar_path = _get_avatar_path(skill_name)
    if avatar_path:
        try:
            import base64 as b64mod
            raw = avatar_path.read_bytes()
            b64 = b64mod.b64encode(raw).decode()
            result = await bot.call_api("set_qq_avatar", file=f"base64://{b64}")
            logger.info(f"[_set_profile] Avatar OK")
        except Exception as e:
            logger.warning(f"[_set_profile] Avatar failed (NTQQ limitation): {e}")
    else:
        logger.warning(f"[_set_profile] No avatar found for {skill_name}")
