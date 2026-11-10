"""
QQ 群只读学习（P1 学习线）—— 2026-11-10，测试角色 iteru。

范围：
- 只采集「角色 SKILL 里 learning_enabled: true 且群号在 learning_groups 里」的群
- 只读，不回复、不 @、不把群消息发到别处
- 原始消息在本地缓冲区最多留 7 天；摘要进 memory，scope=group_<gid>（群里共享）
- 摘要只提取群体话题/梗/活动/称呼偏好；明确禁止个人隐私和可识别信息

流程：
  on_message(priority=1, block=False) 采集 → learning_messages
  每 10 分钟扫一次 → 攒够 learning_min_messages 且距上次摘要超过间隔
  → 小模型摘要成 1~3 条 memory_events → 标记 processed → 7 天后删原始消息
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent

from . import config as cfg
from . import memory
from . import skill_manager

_SCAN_INTERVAL = 10 * 60
_SCAN_START_DELAY = 90
_MAX_SUMMARY_MESSAGES = 120
_KEEP_RAW_DAYS = 7

# 优先级 1：只记录，不 block；@ 的消息仍会继续走到 char_chat（priority 50）
collector = on_message(priority=1, block=False)


def _learning_skills_for_group(group_id: str):
    for skill in skill_manager.ALL_SKILLS.values():
        if not getattr(skill, "learning_enabled", False):
            continue
        groups = {str(g) for g in (getattr(skill, "learning_groups", frozenset()) or [])}
        if str(group_id) in groups:
            yield skill


@collector.handle()
async def _collect_group_message(bot: Bot, event: Event):
    """只读采集：不发送、不回复、不调用模型。"""
    try:
        if not isinstance(event, GroupMessageEvent):
            return
        if str(event.get_user_id()) == str(bot.self_id):
            return
        text = event.get_plaintext().strip()
        if len(text) < 2 or text.startswith(("/", "／")):
            return
        gid = str(event.group_id)
        skills = list(_learning_skills_for_group(gid))
        if not skills:
            return
        sender = getattr(event.sender, "card", "") or getattr(event.sender, "nickname", "") or ""
        msg_id = str(getattr(event, "message_id", "") or "")
        for skill in skills:
            if memory.record_learning_message(
                skill.name, gid, str(event.get_user_id()), sender, text, msg_id
            ):
                logger.debug(f"[LEARN] 采集 {skill.name} group={gid} user={event.get_user_id()}")
    except Exception as e:
        logger.warning(f"[LEARN] 采集失败: {type(e).__name__}: {e}")


_SUMMARY_PROMPT = """你是群聊观察员。下面是一段 QQ 群里最近的真实发言。
请只提取「适合角色长期记住的群体级信息」：
- 群里最近在聊什么、反复出现的梗/黑话/称呼
- 群友对角色、圈子、活动的偏好或共识
- 可验证的活动/作品/时间信息

严格规则：
1. 不要提取任何人的隐私、身份信息、联系方式、住址、工作单位、健康/情感等敏感内容。
2. 不要记录可识别到具体个人的私事；只写群体层面的观察。
3. 输出 JSON 数组，最多 3 条，每条：
   {"kind":"interest|fact","text":"群体层面的简洁记忆（<=80字）","quote":"群里逐字支持这条的短句","importance":1-5}
4. quote 必须逐字出现在给出的发言里；找不到就丢弃该条。
5. 没有值得记的就输出 []。"""


def _parse_items(raw: str) -> list[dict]:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = data.get("items") or data.get("events") or []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


async def _summarize(skill, group_id: str, messages: list[dict]) -> int:
    from . import api_client
    from . import provider as prov

    if not messages:
        return 0
    try:
        provider = prov.get_active_provider()
    except RuntimeError:
        return 0
    if prov.get_provider_cooldown_remaining(provider["name"]) > 0:
        return 0

    lines = []
    for m in messages[-_MAX_SUMMARY_MESSAGES:]:
        sender = (m.get("sender_name") or "群友").strip()
        lines.append(f"{sender}: {m.get('text', '')}")
    body = "\n".join(lines)
    payload = {
        "model": cfg.DEEPSEEK_SEARCH_MODEL,
        "temperature": 0.2,
        "max_tokens": 1200,
        "thinking": {"type": "disabled"},
        "messages": [
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": f"群号：{group_id}\n\n发言：\n{body}"},
        ],
    }
    try:
        data = await api_client.api_request(
            payload, provider,
            timeout=min(30.0, float(cfg.LLM_REQUEST_TIMEOUT)),
            max_retries=1,
        )
    except Exception as e:
        logger.warning(f"[LEARN] 摘要请求失败 skill={skill.name}: {type(e).__name__}: {e}")
        return 0
    if not data:
        logger.warning(f"[LEARN] 摘要无返回 skill={skill.name} group={group_id}")
        return 0
    raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    items = _parse_items(raw)
    logger.info(f"[LEARN] 摘要 raw={len(raw)} items={len(items)} skill={skill.name} group={group_id}")

    norm_body = re.sub(r"\s+", "", body)
    valid = []
    for it in items[:3]:
        quote = re.sub(r"\s+", " ", str(it.get("quote") or "")).strip()
        text = re.sub(r"\s+", " ", str(it.get("text") or "")).strip()
        if not quote or not text or re.sub(r"\s+", "", quote) not in norm_body:
            logger.info(f"[LEARN] 丢弃无效 quote: {quote[:60]!r}")
            continue
        kind = str(it.get("kind") or "fact").lower()
        if kind not in ("interest", "fact"):
            kind = "fact"
        valid.append({
            "kind": kind,
            "text": text[:160],
            "source_quote": quote[:200],
            "importance": it.get("importance", 3),
            "confidence": 0.7,
        })
    if not valid:
        return 0
    n = memory.record_events(
        skill.name,
        scope=f"group_{group_id}",
        channel="group",
        user_id="",
        events=valid,
        source_msg_id="",
    )
    if n:
        logger.info(f"[LEARN] 摘要入库 {n} 条 | skill={skill.name} group={group_id}")
    return n


async def scan_once() -> int:
    """每个角色/群，到间隔且攒够消息就摘要一次。"""
    now = time.time()
    total = 0
    for skill in skill_manager.ALL_SKILLS.values():
        if not getattr(skill, "learning_enabled", False):
            continue
        if "qq" not in (getattr(skill, "learning_sources", frozenset()) or frozenset()):
            continue
        interval = max(5, int(getattr(skill, "learning_summary_interval_minutes", 120) or 120)) * 60
        min_msgs = max(1, int(getattr(skill, "learning_min_messages", 8) or 8))
        for gid in sorted({str(g) for g in (getattr(skill, "learning_groups", frozenset()) or [])}):
            if now - memory.last_learning_summary(skill.name, gid) < interval:
                continue
            msgs = memory.unprocessed_learning_messages(skill.name, gid, _MAX_SUMMARY_MESSAGES)
            if len(msgs) < min_msgs:
                continue
            n = await _summarize(skill, gid, msgs)
            # 无论成功与否都推进摘要时间，避免失败时每 10 分钟重试烧钱；
            # 失败的消息保留，下一轮和新消息一起再试。
            memory.set_learning_summary(skill.name, gid, now)
            if n:
                memory.mark_learning_processed([int(m["id"]) for m in msgs])
            total += n
    memory.prune_learning_messages(_KEEP_RAW_DAYS)
    return total


async def run_loop() -> None:
    await asyncio.sleep(_SCAN_START_DELAY)
    while True:
        try:
            n = await scan_once()
            if n:
                logger.info(f"[LEARN] 本轮新增记忆 {n} 条")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[LEARN] scan 失败: {type(e).__name__}: {e}")
        await asyncio.sleep(_SCAN_INTERVAL)
