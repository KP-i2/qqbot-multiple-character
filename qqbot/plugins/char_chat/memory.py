"""
长期记忆（Life Memory）—— 2026-11-10 起，先给 amadeus-kurisu 灰度。

设计目标（对齐大肥鱼生态，但适配 QQ 多角色 bot）：
- 事件化：每轮对话抽取 0~5 条稳定记忆（事实/偏好/约定/关系/兴趣/边界），
  必须带「原文引用」，引不到原文就不入库（防模型编记忆）。
- 作用域隔离：skill + scope（私聊 user_<uid> / 群聊 group_<gid>_<uid>），
  不同角色、不同用户、私聊/群聊之间不串记忆。
- 可检索、可遗忘：按关键词 + 重要度 + 新近度取少量注入 prompt；
  `/memory` 查看，`/forget` 软删除。
- 不阻塞回复：抽取是回复发送后的后台任务。

数据库：qqbot/data/memory.db（WAL），可用环境变量 MEMORY_DB_PATH 覆盖（测试用）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from nonebot import logger

from . import config as cfg

DB_PATH = Path(os.getenv("MEMORY_DB_PATH", "") or (cfg.QQBOT_DIR / "data" / "memory.db"))
SCHEMA_VERSION = 2

_KINDS = {"fact", "preference", "promise", "relationship", "interest", "boundary", "event"}
_KIND_CN = {
    "fact": "事实",
    "preference": "偏好",
    "promise": "约定",
    "relationship": "关系",
    "interest": "兴趣",
    "boundary": "边界",
    "event": "事件",
}
_WRITE_LOCK = threading.Lock()
_EXTRACT_SEMAPHORE = asyncio.Semaphore(2)
_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def _db():
    """每次操作独立连接，确保 close（sqlite3 的 with 只 commit 不 close）。"""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """建表（幂等）。启动时调用一次；测试换 DB_PATH 后可再调。"""
    global _initialized
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill TEXT NOT NULL,
                scope TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'fact',
                text TEXT NOT NULL,
                source_quote TEXT NOT NULL DEFAULT '',
                source_msg_id TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.7,
                importance INTEGER NOT NULL DEFAULT 3,
                visibility TEXT NOT NULL DEFAULT 'scope',
                expires_at REAL NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mem_lookup
                ON memory_events(skill, scope, deleted, created_at);
            CREATE INDEX IF NOT EXISTS idx_mem_user
                ON memory_events(skill, user_id, deleted, created_at);
            CREATE TABLE IF NOT EXISTS memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_activity (
                skill TEXT NOT NULL,
                scope TEXT NOT NULL,
                last_seen REAL NOT NULL,
                PRIMARY KEY (skill, scope)
            );
            CREATE TABLE IF NOT EXISTS proactive_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill TEXT NOT NULL,
                scope TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                memory_id INTEGER,
                text TEXT NOT NULL DEFAULT '',
                ok INTEGER NOT NULL DEFAULT 0,
                message_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                sent_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_proactive_lookup
                ON proactive_log(skill, scope, sent_at);
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO memory_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    _initialized = True


def _ensure_init() -> None:
    if not _initialized:
        init_db()


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _clean_text(s: str, limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"').strip("'")
    return s[:limit]


def _clean_quote(s: str, limit: int = 200) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"').strip("'")
    return s[:limit]


def record_events(
    skill: str,
    scope: str,
    channel: str,
    user_id: str,
    events: list[dict],
    source_msg_id: str = "",
) -> int:
    """写入一批已校验的记忆事件，返回实际新增条数。"""
    _ensure_init()
    if not events:
        return 0
    now = time.time()
    inserted = 0
    with _WRITE_LOCK, _db() as conn:
        for ev in events:
            kind = str(ev.get("kind") or "fact").strip().lower()
            if kind not in _KINDS:
                kind = "fact"
            text = _clean_text(ev.get("text") or "")
            if len(text) < 2:
                continue
            quote = _clean_quote(ev.get("source_quote") or ev.get("quote") or "")
            try:
                importance = max(1, min(5, int(ev.get("importance") or 3)))
            except (TypeError, ValueError):
                importance = 3
            try:
                confidence = max(0.0, min(1.0, float(ev.get("confidence") or 0.7)))
            except (TypeError, ValueError):
                confidence = 0.7
            exists = conn.execute(
                "SELECT 1 FROM memory_events "
                "WHERE skill=? AND scope=? AND deleted=0 AND text=? LIMIT 1",
                (skill, scope, text),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO memory_events"
                "(skill, scope, channel, user_id, kind, text, source_quote, "
                " source_msg_id, confidence, importance, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (skill, scope, channel, user_id, kind, text, quote,
                 source_msg_id, confidence, importance, now),
            )
            inserted += 1
    if inserted:
        cfg.chat_logger.info(
            f"[MEMORY] 写入 {inserted} 条 | skill={skill} scope={scope} kind={[e.get('kind') for e in events]}"
        )
    return inserted


def _query_tokens(query: str) -> list[str]:
    q = (query or "").lower()
    tokens = [t for t in re.split(r"[\s，,。、/|!?！？;；:：]+", q) if len(t) >= 2]
    for t in list(tokens):
        if len(t) >= 5 and re.search(r"[\u4e00-\u9fff]", t):
            tokens.extend(t[i:i + 2] for i in range(len(t) - 1))
    return list(dict.fromkeys(tokens))[:24]


def retrieve(skill: str, scope: str, query: str, limit: int = 8) -> list[dict]:
    """按关键词 + 重要度 + 新近度取相关记忆。"""
    _ensure_init()
    now = time.time()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM memory_events "
            "WHERE skill=? AND scope=? AND deleted=0 "
            "  AND (expires_at=0 OR expires_at>?) "
            "ORDER BY created_at DESC LIMIT 300",
            (skill, scope, now),
        ).fetchall()
    if not rows:
        return []
    tokens = _query_tokens(query)
    scored_hit = []
    scored_all = []
    for r in rows:
        blob = f"{r['text']} {r['source_quote']}".lower()
        hits = sum(1 for t in tokens if t in blob)
        age_days = max(0.0, (now - float(r["created_at"])) / 86400.0)
        recency = 1.0 / (1.0 + age_days / 14.0)
        kind_boost = {"promise": 0.8, "relationship": 0.8, "boundary": 0.6}.get(r["kind"], 0.0)
        score = (
            hits * 3.0
            + float(r["importance"]) * 0.5
            + float(r["confidence"]) * 0.4
            + recency * 1.2
            + kind_boost
        )
        item = (score, dict(r))
        if hits > 0:
            scored_hit.append(item)
        # 完全无关的旧记忆不硬塞；但最近/重要的保留一点背景
        if not (hits == 0 and recency < 0.15 and r["importance"] < 4):
            scored_all.append(item)
    # 有相关记忆时只用相关的，避免「聊咖啡却提买票」；一条都没命中才退回最近/重要背景
    scored = scored_hit if (tokens and scored_hit) else scored_all
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:max(1, limit)]]


def format_for_prompt(memories: list[dict], max_chars: int = 1200) -> str:
    """把检索到的记忆格式化成短提示块；没有就返回空串。"""
    if not memories:
        return ""
    lines = ["# 长期记忆（你与对方过去真实发生过、值得记住的事）"]
    for m in memories:
        try:
            day = time.strftime("%Y-%m-%d", time.localtime(float(m.get("created_at") or 0)))
        except (TypeError, ValueError):
            day = "—"
        kind = _KIND_CN.get(m.get("kind"), m.get("kind") or "事件")
        text = _clean_text(m.get("text") or "", 120)
        if text:
            lines.append(f"- [{day} · {kind}] {text}")
    if len(lines) == 1:
        return ""
    lines.append("（这些是过去的事；不要逐条复述，只在合适时自然提起。不确定或与当前对话无关就别提。）")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "…"
    return out


def list_recent(skill: str, scope: str, limit: int = 20) -> list[dict]:
    _ensure_init()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM memory_events WHERE skill=? AND scope=? AND deleted=0 "
            "ORDER BY created_at DESC LIMIT ?",
            (skill, scope, max(1, min(limit, 100))),
        ).fetchall()
    return [dict(r) for r in rows]


def forget(
    skill: str,
    scope: str,
    *,
    ids: list[int] | None = None,
    all: bool = False,
    query: str = "",
) -> int:
    """软删除记忆，返回影响条数。"""
    _ensure_init()
    now = time.time()
    with _WRITE_LOCK, _db() as conn:
        if all:
            cur = conn.execute(
                "UPDATE memory_events SET deleted=1, expires_at=? "
                "WHERE skill=? AND scope=? AND deleted=0",
                (now, skill, scope),
            )
        elif ids:
            ph = ",".join("?" for _ in ids)
            cur = conn.execute(
                f"UPDATE memory_events SET deleted=1, expires_at=? "
                f"WHERE skill=? AND scope=? AND deleted=0 AND id IN ({ph})",
                [now, skill, scope] + [int(i) for i in ids],
            )
        elif query:
            like = f"%{query.strip()}%"
            cur = conn.execute(
                "UPDATE memory_events SET deleted=1, expires_at=? "
                "WHERE skill=? AND scope=? AND deleted=0 "
                "  AND (text LIKE ? OR source_quote LIKE ?)",
                (now, skill, scope, like, like),
            )
        else:
            return 0
    n = int(cur.rowcount or 0)
    if n:
        cfg.chat_logger.info(f"[MEMORY] 遗忘 {n} 条 | skill={skill} scope={scope}")
    return n


def stats(skill: str, scope: str) -> dict:
    _ensure_init()
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(created_at) AS last FROM memory_events "
            "WHERE skill=? AND scope=? AND deleted=0",
            (skill, scope),
        ).fetchone()
    return {"count": int(row["n"] or 0), "last": float(row["last"] or 0)}


# ── 活跃时间 / 主动消息日志（P1）──────────────────────────────────

def touch(skill: str, scope: str, ts: float | None = None) -> None:
    """记录「这个作用域刚有对话」，主动消息据此避免打扰。"""
    _ensure_init()
    now = float(ts or time.time())
    with _WRITE_LOCK, _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO memory_activity(skill, scope, last_seen) VALUES(?,?,?)",
            (skill, scope, now),
        )


def last_activity(skill: str, scope: str) -> float:
    _ensure_init()
    with _db() as conn:
        row = conn.execute(
            "SELECT last_seen FROM memory_activity WHERE skill=? AND scope=?",
            (skill, scope),
        ).fetchone()
    return float(row["last_seen"] or 0) if row else 0.0


def log_proactive(
    skill: str,
    scope: str,
    user_id: str,
    reason: str,
    text: str,
    ok: bool,
    memory_id: int | None = None,
    message_id: str = "",
    error: str = "",
) -> None:
    _ensure_init()
    now = time.time()
    with _WRITE_LOCK, _db() as conn:
        conn.execute(
            "INSERT INTO proactive_log"
            "(skill, scope, user_id, reason, memory_id, text, ok, message_id, error, sent_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (skill, scope, user_id, reason, memory_id, text[:500],
             1 if ok else 0, message_id, error[:300], now),
        )


def proactive_count_since(skill: str, scope: str, since_ts: float) -> int:
    _ensure_init()
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM proactive_log "
            "WHERE skill=? AND scope=? AND ok=1 AND sent_at>=?",
            (skill, scope, since_ts),
        ).fetchone()
    return int(row["n"] or 0)


def last_proactive(skill: str, scope: str) -> float:
    _ensure_init()
    with _db() as conn:
        row = conn.execute(
            "SELECT MAX(sent_at) AS ts FROM proactive_log WHERE skill=? AND scope=? AND ok=1",
            (skill, scope),
        ).fetchone()
    return float(row["ts"] or 0) if row else 0.0


def used_memory_ids(skill: str, scope: str, since_ts: float) -> set[int]:
    _ensure_init()
    with _db() as conn:
        rows = conn.execute(
            "SELECT memory_id FROM proactive_log "
            "WHERE skill=? AND scope=? AND ok=1 AND memory_id IS NOT NULL AND sent_at>=?",
            (skill, scope, since_ts),
        ).fetchall()
    return {int(r["memory_id"]) for r in rows if r["memory_id"] is not None}


# ── 抽取 ──────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """你是角色记忆抽取器。根据一轮 QQ 对话，提取「值得长期记住」的稳定信息。

只输出 JSON 数组，不要 markdown、不要解释。每条格式：
{"kind":"fact|preference|promise|relationship|interest|boundary","text":"第三人称、简洁、<=80字","quote":"对话原文中逐字支持这条记忆的短语","importance":1-5,"confidence":0-1}

规则：
1. 只提取「对方」明确说过的稳定信息、双方约定、关系变化、边界；不提取寒暄、一次性情绪、搜索结果、角色自己的设定/台词。
2. quote 必须逐字出现在给出的「对方说」或「角色回复」里；找不到就丢弃该条。
3. 最多 5 条；没有值得记的就输出 []。
4. text 不要带「用户说」「对方说」这类前缀，直接写记忆本身，例如「对方在苏州做舞台设计，常去看地下偶像演出」。
5. 不要推测，不要补全对方没说的信息。"""


def _parse_events(raw: str) -> list[dict]:
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
        data = data.get("events") or data.get("memories") or []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


async def extract_and_store(
    skill: str,
    scope: str,
    channel: str,
    user_id: str,
    user_text: str,
    reply: str,
    sender_name: str = "",
    source_msg_id: str = "",
) -> int:
    """后台任务：从一轮对话抽取记忆并入库（必须带原文引用）。"""
    if not user_text or not reply or len(user_text.strip()) < 4:
        return 0
    if reply.lstrip().startswith("⚠️") or "LLM 调用失败" in reply[:40]:
        return 0

    from . import api_client
    from . import provider as prov

    try:
        await asyncio.wait_for(_EXTRACT_SEMAPHORE.acquire(), timeout=20)
    except asyncio.TimeoutError:
        logger.debug("[MEMORY] 抽取并发已满，跳过")
        return 0

    try:
        try:
            provider = prov.get_active_provider()
        except RuntimeError:
            return 0
        if prov.get_provider_cooldown_remaining(provider["name"]) > 0:
            return 0

        user_content = (
            f"角色：{skill}\n"
            f"对方：{sender_name or '用户'}\n"
            f"场景：{'群聊' if channel == 'group' else '私聊'}\n\n"
            f"对方说：{user_text[:1200]}\n\n"
            f"角色回复：{reply[:1600]}"
        )
        payload = {
            "model": cfg.DEEPSEEK_SEARCH_MODEL,
            "temperature": 0.1,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }
        data = await api_client.api_request(
            payload, provider,
            timeout=min(30.0, float(cfg.LLM_REQUEST_TIMEOUT)),
            max_retries=1,
        )
        if not data:
            return 0
        raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        events = _parse_events(raw)

        corpus = _normalize(user_text + "\n" + reply)
        valid = []
        for ev in events[:5]:
            quote = _clean_quote(ev.get("quote") or ev.get("source_quote") or "")
            if not quote or _normalize(quote) not in corpus:
                continue
            text = _clean_text(ev.get("text") or "")
            if len(text) < 4:
                continue
            kind = str(ev.get("kind") or "fact").lower()
            if kind not in _KINDS:
                kind = "fact"
            valid.append({
                "kind": kind,
                "text": text,
                "source_quote": quote,
                "importance": ev.get("importance", 3),
                "confidence": ev.get("confidence", 0.7),
            })
        if not valid:
            return 0
        return record_events(skill, scope, channel, user_id, valid, source_msg_id)
    except Exception as e:
        logger.warning(f"[MEMORY] 抽取失败 skill={skill} scope={scope}: {type(e).__name__}: {e}")
        return 0
    finally:
        _EXTRACT_SEMAPHORE.release()


def format_stats_text(skill: str, scope: str, limit: int = 20) -> str:
    """给 /memory 命令用的可读列表。"""
    rows = list_recent(skill, scope, limit)
    st = stats(skill, scope)
    if not rows:
        return f"📭 {skill} 在 {scope} 下还没有长期记忆。"
    lines = [f"🧠 {skill} · {scope}：{st['count']} 条（显示最近 {len(rows)} 条）"]
    for r in rows:
        day = time.strftime("%m-%d", time.localtime(float(r["created_at"])))
        kind = _KIND_CN.get(r["kind"], r["kind"])
        lines.append(f"  #{r['id']} [{day}·{kind}] {_clean_text(r['text'], 70)}")
    lines.append("用 /forget <id|all|关键词> 删除")
    return "\n".join(lines)


init_db()
