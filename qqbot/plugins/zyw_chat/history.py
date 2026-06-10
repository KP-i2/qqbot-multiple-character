"""
对话历史状态管理、持久化、定期清理
"""

import asyncio
import json
import time
from collections import defaultdict

from nonebot import logger
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, PrivateMessageEvent

from . import config as cfg
from . import skill_manager


# 管理员的当前 skill (全局角色，所有用户共用)
user_active_skill: dict[str, str] = defaultdict(lambda: skill_manager.DEFAULT_SKILL_NAME or "")
# 对话历史 (key = 用户/群 + 角色名，每个用户在每个角色下独立对话)
conversation_histories: dict[str, list[dict]] = defaultdict(list)
_history_timestamps: dict[str, float] = {}  # key → last access time

# ── 对话历史持久化 ──
_HISTORY_DIR = cfg.QQBOT_DIR / "data"
_HISTORY_DIR.mkdir(exist_ok=True)
_HISTORY_FILE = _HISTORY_DIR / "conversation_histories.json"
_history_dirty = False
_history_last_save = 0.0

# 当前全局活跃角色 (管理员切换后影响所有用户)
global_active_skill: str = skill_manager.DEFAULT_SKILL_NAME or ""


def get_history_key(event: Event) -> str:
    skill_name = global_active_skill or skill_manager.DEFAULT_SKILL_NAME or "default"
    if isinstance(event, GroupMessageEvent):
        return f"group_{event.group_id}_{event.user_id}_{skill_name}"
    elif isinstance(event, PrivateMessageEvent):
        return f"user_{event.user_id}_{skill_name}"
    return f"unknown_{event.get_user_id()}_{skill_name}"


def add_to_history(key: str, role: str, content: str):
    global _history_dirty
    history = conversation_histories[key]
    history.append({"role": role, "content": content})
    if len(history) > cfg.MAX_HISTORY_MESSAGES:
        history[:] = history[-cfg.MAX_HISTORY_MESSAGES:]
    _history_timestamps[key] = time.time()
    _history_dirty = True


def clear_history(key: str):
    global _history_dirty
    conversation_histories.pop(key, None)
    _history_timestamps.pop(key, None)
    _history_dirty = True


def load_histories():
    """启动时从磁盘恢复对话历史"""
    global _history_last_save
    if not _HISTORY_FILE.exists():
        return
    try:
        data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        for key, entry in data.items():
            if isinstance(entry, dict) and "messages" in entry:
                conversation_histories[key] = entry["messages"]
                _history_timestamps[key] = entry.get("timestamp", 0)
        _history_last_save = time.time()
        logger.info(f"[history] Restored {len(data)} conversations from disk")
    except Exception as e:
        logger.warning(f"[history] Failed to load histories: {e}")


def save_histories(force: bool = False):
    """将对话历史写入磁盘（带节流，最多每 60 秒写一次）"""
    global _history_dirty, _history_last_save
    now = time.time()
    if not force and (not _history_dirty or now - _history_last_save < cfg.HISTORY_SAVE_INTERVAL):
        return
    try:
        data = {}
        for key in list(conversation_histories.keys()):
            msgs = conversation_histories[key]
            if msgs:  # 只保存非空历史
                data[key] = {
                    "messages": msgs,
                    "timestamp": _history_timestamps.get(key, 0),
                }
        _HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _history_dirty = False
        _history_last_save = now
    except Exception as e:
        logger.warning(f"[history] Failed to save histories: {e}")


# 启动时恢复历史
load_histories()


async def periodic_history_cleanup():
    """每30分钟清理一次过期对话历史"""
    while True:
        await asyncio.sleep(1800)  # 30分钟
        now = time.time()
        expired = [k for k, t in _history_timestamps.items() if now - t > cfg.HISTORY_TTL]
        for k in expired:
            conversation_histories.pop(k, None)
            _history_timestamps.pop(k, None)
        if expired:
            logger.info(f"[cleanup] Removed {len(expired)} expired conversation histories")
            save_histories(force=True)
