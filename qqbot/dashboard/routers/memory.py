"""
Dashboard 记忆面板 API —— 2026-11-10 P2。

Dashboard 是独立进程（uvicorn），不初始化 NoneBot；这里直接只读/写
qqbot/data/memory.db，避免 import char_chat.config（它依赖 nonebot driver）。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Body, Query

from ._shared import QQBOT_DIR, logger

router = APIRouter()
DB_PATH = QQBOT_DIR / "data" / "memory.db"


def _ro_conn():
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.warning(f"[memory] open ro failed: {e}")
        return None


def _rw_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _table_exists(conn, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
        ).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


@router.get("/api/usage/summary")
def usage_summary(days: int = Query(7, ge=1, le=365), skill: str = Query(""), purpose: str = Query("")):
    conn = _ro_conn()
    if conn is None or not _table_exists(conn, "usage_events"):
        return {"ok": True, "days": days, "totals": {"calls": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0, "estimated_calls": 0}, "rows": []}
    try:
        since = time.time() - days * 86400
        where = ["ts>=?"]
        args: list = [since]
        if skill:
            where.append("skill=?"); args.append(skill)
        if purpose:
            where.append("purpose=?"); args.append(purpose)
        w = " AND ".join(where)
        rows = _dicts(conn.execute(
            "SELECT kind, purpose, provider, model, skill, "
            "SUM(calls) AS calls, SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens, SUM(total_tokens) AS total_tokens, "
            "SUM(cached_tokens) AS cached_tokens, SUM(estimated) AS estimated_calls "
            f"FROM usage_events WHERE {w} "
            "GROUP BY kind, purpose, provider, model, skill "
            "ORDER BY total_tokens DESC, calls DESC",
            args,
        ).fetchall())
        totals = {
            "calls": sum(int(r["calls"] or 0) for r in rows),
            "prompt_tokens": sum(int(r["prompt_tokens"] or 0) for r in rows),
            "completion_tokens": sum(int(r["completion_tokens"] or 0) for r in rows),
            "total_tokens": sum(int(r["total_tokens"] or 0) for r in rows),
            "estimated_calls": sum(int(r["estimated_calls"] or 0) for r in rows),
        }
        return {"ok": True, "days": days, "totals": totals, "rows": rows}
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"db error: {e}", "rows": []}
    finally:
        conn.close()


@router.get("/api/usage/recent")
def usage_recent(limit: int = Query(100, ge=1, le=300)):
    conn = _ro_conn()
    if conn is None or not _table_exists(conn, "usage_events"):
        return {"ok": True, "items": []}
    try:
        rows = _dicts(conn.execute(
            "SELECT id, ts, kind, purpose, provider, model, skill, total_tokens, calls, estimated "
            "FROM usage_events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall())
        return {"ok": True, "items": rows}
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"db error: {e}", "items": []}
    finally:
        conn.close()


@router.get("/api/learning/report")
def learning_report(days: int = Query(7, ge=1, le=365), skill: str = Query("")):
    conn = _ro_conn()
    if conn is None:
        return {"ok": True, "days": days, "totals": {"runs": 0, "messages": 0,
                "accepted": 0, "rejected": 0}, "runs": [], "recent": []}
    try:
        since = time.time() - days * 86400
        runs: list = []
        if _table_exists(conn, "learning_runs"):
            sql = "SELECT * FROM learning_runs WHERE finished_at>=?"
            args: list = [since]
            if skill:
                sql += " AND skill=?"; args.append(skill)
            sql += " ORDER BY finished_at DESC LIMIT 50"
            runs = _dicts(conn.execute(sql, args).fetchall())
        totals = {
            "runs": len(runs),
            "messages": sum(int(r["messages"] or 0) for r in runs),
            "accepted": sum(int(r["accepted"] or 0) for r in runs),
            "rejected": sum(int(r["rejected"] or 0) for r in runs),
        }
        recent: list = []
        if _table_exists(conn, "memory_events"):
            sql2 = ("SELECT id, skill, scope, kind, text, source_quote, created_at "
                    "FROM memory_events WHERE deleted=0 AND scope LIKE 'group_%' AND created_at>=?")
            args2: list = [since]
            if skill:
                sql2 += " AND skill=?"; args2.append(skill)
            sql2 += " ORDER BY created_at DESC LIMIT 30"
            recent = _dicts(conn.execute(sql2, args2).fetchall())
        return {"ok": True, "days": days, "totals": totals, "runs": runs, "recent": recent}
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"db error: {e}", "runs": [], "recent": []}
    finally:
        conn.close()


@router.get("/api/memory/overview")
def memory_overview():
    conn = _ro_conn()
    if conn is None:
        return {"ok": True, "db": str(DB_PATH), "exists": False, "skills": [], "total": 0,
                "proactive": 0, "recent": []}
    try:
        skills = _dicts(conn.execute(
            "SELECT skill, COUNT(*) AS n FROM memory_events "
            "WHERE deleted=0 GROUP BY skill ORDER BY n DESC"
        ).fetchall())
        total = conn.execute("SELECT COUNT(*) FROM memory_events WHERE deleted=0").fetchone()[0]
        proactive = 0
        if _table_exists(conn, "proactive_log"):
            proactive = conn.execute("SELECT COUNT(*) FROM proactive_log").fetchone()[0]
        recent = _dicts(conn.execute(
            "SELECT id, skill, scope, channel, kind, text, created_at "
            "FROM memory_events WHERE deleted=0 ORDER BY created_at DESC LIMIT 10"
        ).fetchall())
        return {"ok": True, "db": str(DB_PATH), "exists": True, "skills": skills,
                "total": int(total or 0), "proactive": int(proactive or 0), "recent": recent}
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"db error: {e}", "skills": [], "total": 0, "recent": []}
    finally:
        conn.close()


@router.get("/api/memory/scopes")
def memory_scopes(skill: str = Query("")):
    if not skill:
        return {"ok": True, "scopes": []}
    conn = _ro_conn()
    if conn is None:
        return {"ok": True, "scopes": []}
    try:
        rows = _dicts(conn.execute(
            "SELECT scope, COUNT(*) AS n FROM memory_events "
            "WHERE skill=? AND deleted=0 GROUP BY scope ORDER BY n DESC",
            (skill,),
        ).fetchall())
        return {"ok": True, "scopes": rows}
    finally:
        conn.close()


@router.get("/api/memory/list")
def memory_list(
    skill: str = Query(""),
    scope: str = Query(""),
    q: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
):
    conn = _ro_conn()
    if conn is None:
        return {"ok": True, "items": []}
    try:
        sql = ("SELECT id, skill, scope, channel, user_id, kind, text, source_quote, "
               "importance, created_at FROM memory_events WHERE deleted=0")
        args: list = []
        if skill:
            sql += " AND skill=?"; args.append(skill)
        if scope:
            sql += " AND scope=?"; args.append(scope)
        if q:
            sql += " AND (text LIKE ? OR source_quote LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
        return {"ok": True, "items": _dicts(conn.execute(sql, args).fetchall())}
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"db error: {e}", "items": []}
    finally:
        conn.close()


@router.post("/api/memory/delete")
def memory_delete(payload: dict = Body(...)):
    ids = payload.get("ids") or []
    skill = str(payload.get("skill") or "")
    scope = str(payload.get("scope") or "")
    all_flag = bool(payload.get("all"))
    if not ids and not (all_flag and skill and scope):
        return {"ok": False, "msg": "需要 ids，或 all=true 且带 skill+scope"}
    conn = _rw_conn()
    try:
        now = time.time()
        if ids:
            clean = []
            for x in ids:
                try:
                    clean.append(int(x))
                except (TypeError, ValueError):
                    continue
            if not clean:
                return {"ok": False, "msg": "ids 非法"}
            ph = ",".join("?" for _ in clean)
            cur = conn.execute(
                f"UPDATE memory_events SET deleted=1, expires_at=? WHERE id IN ({ph})",
                [now] + clean,
            )
        else:
            cur = conn.execute(
                "UPDATE memory_events SET deleted=1, expires_at=? "
                "WHERE skill=? AND scope=? AND deleted=0",
                (now, skill, scope),
            )
        conn.commit()
        return {"ok": True, "deleted": int(cur.rowcount or 0)}
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"db error: {e}"}
    finally:
        conn.close()


@router.get("/api/memory/proactive")
def memory_proactive(skill: str = Query(""), limit: int = Query(50, ge=1, le=200)):
    conn = _ro_conn()
    if conn is None:
        return {"ok": True, "items": []}
    try:
        sql = ("SELECT id, skill, scope, user_id, reason, memory_id, text, ok, "
               "message_id, error, sent_at FROM proactive_log")
        args: list = []
        if skill:
            sql += " WHERE skill=?"; args.append(skill)
        sql += " ORDER BY sent_at DESC LIMIT ?"; args.append(limit)
        return {"ok": True, "items": _dicts(conn.execute(sql, args).fetchall())}
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"db error: {e}", "items": []}
    finally:
        conn.close()


@router.get("/api/memory/learning")
def memory_learning(skill: str = Query("")):
    conn = _ro_conn()
    if conn is None:
        return {"ok": True, "groups": []}
    try:
        if not _table_exists(conn, "learning_state"):
            return {"ok": True, "groups": []}
        sql = ("SELECT skill, group_id, last_summary_at FROM learning_state")
        args: list = []
        if skill:
            sql += " WHERE skill=?"; args.append(skill)
        sql += " ORDER BY last_summary_at DESC"
        rows = _dicts(conn.execute(sql, args).fetchall())
        if _table_exists(conn, "learning_messages"):
            for r in rows:
                row = conn.execute(
                    "SELECT COUNT(*) FROM learning_messages "
                    "WHERE skill=? AND group_id=? AND processed=0",
                    (r["skill"], r["group_id"]),
                ).fetchone()
                r["pending"] = int(row[0] or 0)
        return {"ok": True, "groups": rows}
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"db error: {e}", "groups": []}
    finally:
        conn.close()
