"""
QQ 头像/昵称管理

2026-11-10: 角色可以有多张头像；默认读 SKILL.md 的 avatar_file，
也可以用 /avatar 选择（写 photo/<skill>/selected.txt），旧图不删除。
"""

from pathlib import Path
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot

from . import config as cfg
from . import skill_manager

_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_SELECTED_FILE = "selected.txt"


def _photo_dir(skill_name: str) -> Path:
    return cfg.PHOTO_DIR / skill_name


def list_avatars(skill_name: str) -> list[Path]:
    """列出该角色可选头像（不含 _ 开头文件和选择记录）。"""
    d = _photo_dir(skill_name)
    if not d.exists():
        return []
    return sorted(
        (f for f in d.iterdir()
         if f.is_file() and f.suffix.lower() in _AVATAR_EXTS and not f.name.startswith("_")),
        key=lambda f: f.name.lower(),
    )


def selected_avatar_name(skill_name: str) -> str:
    p = _photo_dir(skill_name) / _SELECTED_FILE
    try:
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except OSError:
        return ""


def _get_avatar_path(skill_name: str) -> Optional[Path]:
    """选择顺序：selected.txt > SKILL.md avatar_file > 旧逻辑（目录第一张图）。"""
    skill_photo_dir = _photo_dir(skill_name)
    if not skill_photo_dir.exists():
        return None

    # 1) /avatar 选择记录
    selected = selected_avatar_name(skill_name)
    if selected:
        p = skill_photo_dir / Path(selected).name
        if p.is_file() and p.suffix.lower() in _AVATAR_EXTS:
            logger.info(f"Avatar selected: {skill_name} -> {p.name}")
            return p
        logger.warning(f"[AVATAR] selected.txt 指向的文件不存在: {skill_name}/{selected}")

    # 2) SKILL.md 的 avatar_file
    skill = skill_manager.ALL_SKILLS.get(skill_name)
    configured = str(getattr(skill, "avatar_file", "") or "").strip()
    if configured:
        p = skill_photo_dir / Path(configured).name
        if p.is_file() and p.suffix.lower() in _AVATAR_EXTS:
            logger.info(f"Avatar configured: {skill_name} -> {p.name}")
            return p
        logger.warning(f"[AVATAR] avatar_file 指向的文件不存在: {skill_name}/{configured}，回退目录扫描")

    # 3) 旧逻辑：扩展名优先级 + 跳过 _avatar*
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        for f in skill_photo_dir.iterdir():
            if f.suffix.lower() == ext and not f.name.startswith("_avatar"):
                logger.info(f"Avatar found: {skill_name} -> {f.name} ({f.stat().st_size / 1024:.0f}KB)")
                return f
    return None


def select_avatar(skill_name: str, filename: str) -> Optional[Path]:
    """把某个头像记为当前选择（持久化到 selected.txt）。"""
    name = Path(str(filename or "").strip()).name
    if not name or name != str(filename or "").strip():
        return None
    if name.startswith("_") or Path(name).suffix.lower() not in _AVATAR_EXTS:
        return None
    p = _photo_dir(skill_name) / name
    if not p.is_file():
        return None
    d = _photo_dir(skill_name)
    d.mkdir(parents=True, exist_ok=True)
    (d / _SELECTED_FILE).write_text(name, encoding="utf-8")
    logger.info(f"[AVATAR] 选择头像: {skill_name} -> {name}")
    return p


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
            logger.info(f"[_set_profile] Avatar OK: {avatar_path.name}")
        except Exception as e:
            logger.warning(f"[_set_profile] Avatar failed (NTQQ limitation): {e}")
    else:
        logger.warning(f"[_set_profile] No avatar found for {skill_name}")
