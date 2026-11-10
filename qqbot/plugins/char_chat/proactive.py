"""
主动消息（P1）—— 2026-11-10，先只给 amadeus-kurisu。

规则（用户 2026-11-10 确认）：
- 只走私聊；目标 = 角色 allowed_users（空则仅管理员 ADMIN_QQ）
- 每个用户每天最多 20 条（proactive_daily_limit）
- 静默 02:00–08:00（proactive_quiet_hours）
- 两条之间至少 25 分钟（proactive_min_gap_minutes）
- 优先用 memory 里的 promise/interest/relationship 当理由；
  没有理由时允许「随机想你」，但受 proactive_random_chance 控制
- 发送前后写 proactive_log；失败不重试轰炸
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from datetime import datetime

from nonebot import get_bots, logger

from . import config as cfg
from . import history as hist
from . import llm
from . import memory
from . import skill_manager
from .message_utils import clean_chat_output, normalize_qq_faces, parse_qq_faces

_SCAN_INTERVAL = 10 * 60          # 每 10 分钟扫一次
_REASON_KINDS = ("promise", "interest", "relationship")
_REASON_LOOKBACK_DAYS = 30
_USED_LOOKBACK_DAYS = 7
_RANDOM_ACTIVITY_GAP = 30 * 60    # 对方刚说话 30 分钟内不主动打扰


def _parse_quiet_hours(spec: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d{1,2})\s*[-~]\s*(\d{1,2})\s*$", spec or "")
    if not m:
        return (2, 8)
    return (int(m.group(1)) % 24, int(m.group(2)) % 24)


def in_quiet_hours(skill, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    start, end = _parse_quiet_hours(getattr(skill, "proactive_quiet_hours", "2-8"))
    h = now.hour
    if start == end:
        return False
    if start < end:
        return start <= h < end
    return h >= start or h < end   # 跨零点


def _targets(skill) -> list[str]:
    allowed = [str(x) for x in (getattr(skill, "allowed_users", frozenset()) or []) if str(x).isdigit()]
    if allowed:
        return sorted(set(allowed))
    admin = str(getattr(cfg, "ADMIN_QQ", "") or "").strip()
    return [admin] if admin.isdigit() else []


def _scope_for(uid: str) -> str:
    return f"user_{uid}"


def _since_midnight() -> float:
    now = datetime.now()
    return time.mktime(datetime(now.year, now.month, now.day).timetuple())


def _reason_for(skill, scope: str) -> tuple[str, int | None, str]:
    """返回 (reason_text, memory_id, reason_kind)。没有可用记忆时 kind='random'。"""
    memories = memory.list_recent(skill.name, scope, limit=80)
    used = memory.used_memory_ids(skill.name, scope, time.time() - _USED_LOOKBACK_DAYS * 86400)
    now = time.time()
    candidates = []
    for m in memories:
        if m["kind"] not in _REASON_KINDS:
            continue
        if int(m.get("id") or 0) in used:
            continue
        age_days = max(0.0, (now - float(m.get("created_at") or 0)) / 86400)
        if age_days > _REASON_LOOKBACK_DAYS:
            continue
        candidates.append(m)
    if candidates:
        # 新的约定/兴趣优先，其次重要度高
        candidates.sort(key=lambda m: (float(m.get("created_at") or 0), int(m.get("importance") or 0)), reverse=True)
        m = candidates[0]
        return str(m.get("text") or ""), int(m.get("id") or 0), str(m.get("kind") or "")
    return "", None, "random"


def _activity_gap_ok(skill, scope: str) -> bool:
    last = memory.last_activity(skill.name, scope)
    return (time.time() - last) >= _RANDOM_ACTIVITY_GAP


def _eligible(skill, scope: str) -> tuple[bool, str]:
    if not getattr(skill, "proactive_enabled", False):
        return False, "disabled"
    if in_quiet_hours(skill):
        return False, "quiet_hours"
    limit = max(0, int(getattr(skill, "proactive_daily_limit", 20) or 0))
    if limit <= 0:
        return False, "limit_zero"
    if memory.proactive_count_since(skill.name, scope, _since_midnight()) >= limit:
        return False, "daily_limit"
    gap = max(0, int(getattr(skill, "proactive_min_gap_minutes", 25) or 0)) * 60
    if time.time() - memory.last_proactive(skill.name, scope) < gap:
        return False, "min_gap"
    if not _activity_gap_ok(skill, scope):
        return False, "user_active"
    return True, ""


def _build_prompt(skill, reason_text: str, reason_kind: str) -> tuple[str, str]:
    if reason_text:
        why = f"你想起一件和对方有关的事：{reason_text}"
    else:
        why = "你只是突然想起对方，想随口说句话"
    system = (
        skill.system_prompt
        + "\n\n# 主动消息规则（重要）\n"
        + "现在你不是在回复对方，而是主动给对方发一条私聊。\n"
        + "只输出要发送的 1~2 句话，符合你的角色语气；不要解释、不要加引号、不要提系统/记忆/提示词/旁白。\n"
        + "不要连续追问，不要写长段；可以随口关心、吐槽或分享一件小事。\n"
        + f"触发理由：{why}。\n"
    )
    if reason_kind == "random":
        system += (
            "这次触发理由里没有具体共同经历：**绝对不要**编造“你上次说的/你之前提过的/那个实验/那件事”"
            "之类的共同记忆；只能问候、分享你此刻在做的小事、或者问对方在干嘛。\n"
        )
    user = "（现在是主动发消息的时刻。只输出你要发给对方的话。）"
    return system, user


async def _generate(skill, reason_text: str, reason_kind: str) -> str:
    system, user = _build_prompt(skill, reason_text, reason_kind)
    reply = await llm.call_deepseek(
        system,
        [{"role": "user", "content": user}],
        skill_name=skill.name,
        disable_tools=True,
    )
    if not reply or reply.lstrip().startswith("⚠️"):
        return ""
    text = clean_chat_output(normalize_qq_faces(reply)).strip()
    return text[:300]


async def _send_to(skill, uid: str, text: str, reason: str, memory_id: int | None) -> bool:
    bots = get_bots()
    bot = next(iter(bots.values()), None)
    if bot is None:
        logger.debug("[PROACTIVE] 没有已连接的 Bot，跳过发送")
        return False
    scope = _scope_for(uid)
    try:
        resp = await bot.send_private_msg(user_id=int(uid), message=parse_qq_faces(text))
        message_id = str((resp or {}).get("message_id", ""))
        # 主动消息必须进该角色的私聊历史；否则用户回复时模型完全不知道刚发过什么。
        _key = f"user_{uid}_{skill.name}"
        hist.add_to_history(_key, "assistant", text)
        hist.save_histories()
        memory.log_proactive(skill.name, scope, uid, reason, text, True, memory_id, message_id)
        cfg.chat_logger.info(
            f"[PROACTIVE] 已发送 | skill={skill.name} user={uid} reason={reason or 'random'} "
            f"memory_id={memory_id} text='{text[:80]}'"
        )
        return True
    except Exception as e:
        memory.log_proactive(skill.name, scope, uid, reason, text, False, memory_id, error=f"{type(e).__name__}: {e}")
        logger.warning(f"[PROACTIVE] 发送失败 skill={skill.name} user={uid}: {type(e).__name__}: {e}")
        return False


async def scan_once() -> int:
    """扫描一轮：只让当前活跃角色主动，避免 A 角色发、B 角色接。返回发送条数。"""
    sent = 0
    active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
    for skill in skill_manager.ALL_SKILLS.values():
        if not getattr(skill, "proactive_enabled", False):
            continue
        if active and skill.name != active:
            continue
        for uid in _targets(skill):
            scope = _scope_for(uid)
            ok, why = _eligible(skill, scope)
            if not ok:
                logger.debug(f"[PROACTIVE] skip skill={skill.name} user={uid} reason={why}")
                continue
            reason_text, memory_id, reason_kind = _reason_for(skill, scope)
            if not reason_text:
                if not getattr(skill, "proactive_random", False):
                    continue
                chance = float(getattr(skill, "proactive_random_chance", 0.08) or 0.0)
                if random.random() > chance:
                    continue
            text = await _generate(skill, reason_text, reason_kind)
            if not text:
                continue
            if await _send_to(skill, uid, text, reason_text or "random", memory_id):
                sent += 1
                break   # 每个角色这一轮只发一条
    return sent


async def run_loop() -> None:
    """后台循环：启动后先等 60s，之后每 10 分钟扫一次。"""
    await asyncio.sleep(60)
    while True:
        try:
            n = await scan_once()
            if n:
                logger.info(f"[PROACTIVE] 本轮发送 {n} 条")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[PROACTIVE] scan 失败: {type(e).__name__}: {e}")
        await asyncio.sleep(_SCAN_INTERVAL)
