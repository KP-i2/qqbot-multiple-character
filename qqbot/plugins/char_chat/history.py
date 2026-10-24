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
    # 2026-06-28 fix:防御性 coerce,content 必须是 str
    # 早期版本可能误存了 multimodal content 数组,这里强制转字符串防 deepseek 400
    if not isinstance(content, str):
        if isinstance(content, list):
            # OpenAI multimodal content 数组 → 拼接文本段,图片用 placeholder
            text_parts = []
            for seg in content:
                if isinstance(seg, dict):
                    if seg.get("type") == "text":
                        text_parts.append(seg.get("text", ""))
                    elif seg.get("type") == "image_url":
                        text_parts.append("[图片]")
                elif isinstance(seg, str):
                    text_parts.append(seg)
            content = "\n".join(text_parts) if text_parts else "[消息]"
        else:
            content = str(content)
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


def _sanitize_msg_content(m: dict) -> dict | None:
    """过滤单条 history 消息,只保留纯文本 content,丢弃 multimodal 残留

    2026-06-28 fix:早期版本 / 加载时的脏数据可能导致 history 里某条 content 是
    OpenAI multimodal 数组(包含 image_url 段)。这种 history 发给 deepseek 会触发
    "unknown variant image_url" 400。这里强制 coerce 成纯文本,image 段替换为 placeholder。
    """
    if not isinstance(m, dict) or "role" not in m or "content" not in m:
        return None
    content = m.get("content")
    if isinstance(content, str):
        return m
    if isinstance(content, list):
        text_parts = []
        had_image = False
        for seg in content:
            if isinstance(seg, dict):
                seg_type = seg.get("type")
                if seg_type == "text":
                    text_parts.append(seg.get("text", ""))
                elif seg_type == "image_url":
                    had_image = True
                else:
                    text_parts.append(str(seg))
            elif isinstance(seg, str):
                text_parts.append(seg)
        if had_image and not text_parts:
            return {"role": m["role"], "content": "[图片]"}
        if had_image:
            return {"role": m["role"], "content": "[图片] " + "\n".join(text_parts)}
        # 全是 text 段但意外传了 list
        return {"role": m["role"], "content": "\n".join(text_parts) or "[消息]"}
    # 兜底
    return {"role": m["role"], "content": str(content)}


def load_histories():
    """启动时从磁盘恢复对话历史"""
    global _history_last_save
    if not _HISTORY_FILE.exists():
        return
    try:
        data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        dirty = False
        for key, entry in data.items():
            if isinstance(entry, dict) and "messages" in entry:
                msgs = entry["messages"]
                if isinstance(msgs, list):
                    # 2026-06-28 fix:加载时 sanitize,过滤 multimodal 残留
                    valid_msgs = []
                    for m in msgs:
                        clean = _sanitize_msg_content(m)
                        if clean is not None:
                            valid_msgs.append(clean)
                    if valid_msgs:
                        conversation_histories[key] = valid_msgs
                        _history_timestamps[key] = entry.get("timestamp", 0)
                        # 检测是否发生过 sanitize(被替换了)
                        if any(_msg_had_multimodal(m) for m in msgs):
                            entry["messages"] = valid_msgs
                            dirty = True
        if dirty:
            # 立即写回磁盘,避免下次再触发同样问题
            try:
                _HISTORY_FILE.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.warning("[history] sanitize: multimodal 残留已从磁盘 history 清理")
            except Exception as e:
                logger.warning(f"[history] sanitize write-back failed: {e}")
        _history_last_save = time.time()
        logger.info(f"[history] Restored {len(data)} conversations from disk")
    except Exception as e:
        logger.warning(f"[history] Failed to load histories: {e}")


def _msg_had_multimodal(m: dict) -> bool:
    """单条消息的 content 是否是 OpenAI multimodal 数组(用于检测 sanitize 是否触发)"""
    if not isinstance(m, dict):
        return False
    content = m.get("content")
    return isinstance(content, list)


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
