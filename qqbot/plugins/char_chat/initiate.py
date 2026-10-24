"""「主动暗示」掷骰（角色级，默认关闭）

设计要点
--------
- 以"一段对话"为单位掷一次骰子：同一个 history key 上，相邻两条消息的间隔
  超过 SESSION_GAP_SECONDS 就算新的一段对话，重新掷；一段对话内结果保持不变。
- 概率来自角色 SKILL.md front-matter 的 initiate_probability（默认 0 = 永不触发）。
- 命中时把该角色的 initiate_hint 注入 system prompt；没命中不注入任何东西。
- **作用范围（2026-09-13 收紧）**：只在私聊、且发送者在该角色的 allowed_users 白名单里时才掷。
  也就是说这个机制天生是"某个角色的某个人"专用——角色没写白名单就不掷（宁可不触发，也不乱触发）。

为什么在代码里掷，而不是让模型"自己按 7% 概率"
--------------------------------------------
模型对概率没有可靠直觉：写进 prompt 的"7%"要么几乎不触发，要么每轮都触发，
还会随上下文漂移。掷骰放在代码里才是真的 7%，而且可复现、可观测（日志有 [initiate] 行）。

角色接入方式（SKILL.md front-matter）：
    allowed_users: 2972494435
    initiate_probability: 0.07
    initiate_hint: "本轮：主动暗示命中（见 persona.md 的「主动暗示」一节）"
"""
from __future__ import annotations

import random
import time

from nonebot import logger


SESSION_GAP_SECONDS = 30 * 60      # 超过 30 分钟没说话 = 新的一段对话
STATE_TTL_SECONDS = 24 * 3600      # 状态最多留一天，防止内存里越堆越多
_MAX_STATE = 4096                  # 超过这个数量就清理过期项

_state: dict[str, dict] = {}


def _prune(now: float) -> None:
    if len(_state) <= _MAX_STATE:
        return
    expired = [k for k, v in _state.items() if now - v.get("last_seen", 0) > STATE_TTL_SECONDS]
    for k in expired:
        _state.pop(k, None)


def hint_for(key: str, skill, is_group: bool = False, uid: str = "") -> str:
    """返回本轮要注入 system prompt 的提示；不需要注入时返回空串。

    key:   一段对话的标识（用 history key 即可）
    skill: skill_manager.Skill（读 initiate_probability / initiate_hint / allowed_users）
    uid:   发送者 QQ 号；必须落在该角色的白名单内才会掷骰
    """
    prob = float(getattr(skill, "initiate_probability", 0.0) or 0.0)
    if prob <= 0.0 or is_group:
        return ""

    # 只对"这个角色的这个人"生效：没写白名单就不掷
    allowed = getattr(skill, "allowed_users", None) or frozenset()
    if not allowed or str(uid) not in {str(x) for x in allowed}:
        return ""

    now = time.time()
    _prune(now)
    st = _state.get(key)
    if st is None or now - st.get("last_seen", 0.0) > SESSION_GAP_SECONDS:
        hit = random.random() < prob
        st = {"last_seen": now, "hit": hit, "rolled_at": now}
        _state[key] = st
        logger.info(
            f"[initiate] 新对话掷骰 p={prob:.2f} → {'命中' if hit else '未命中'}（{key}）"
        )
    else:
        st["last_seen"] = now

    if not st.get("hit"):
        return ""
    return getattr(skill, "initiate_hint", "") or "[本轮：主动暗示命中]"


def reset(key: str | None = None) -> None:
    """清掉掷骰状态（测试用；传 None 清全部）。"""
    if key is None:
        _state.clear()
    else:
        _state.pop(key, None)
