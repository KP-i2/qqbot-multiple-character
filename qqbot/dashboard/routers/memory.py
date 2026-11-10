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
