"""
日志查看
（2026-08-30 拆分自 main.py）
"""
import re
import time
from pathlib import Path

from fastapi import APIRouter

from ._shared import QQBOT_DIR, logger

router = APIRouter()

LOG_DIR = QQBOT_DIR / "logs"


@router.get("/api/logs")
async def api_list_logs():
    """列出可用的日志文件"""
    result = []
    if LOG_DIR.exists():
        for f in sorted(LOG_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.suffix == ".log":
                result.append({
                    "name": f.name,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                })
    return {"ok": True, "logs": result}


@router.get("/api/logs/{filename}")
async def api_get_log(filename: str, tail: int = 200):
    """读取日志文件内容（默认最后200行）"""
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', filename) or '..' in filename:
        return {"ok": False, "msg": "Invalid filename"}
    log_path = LOG_DIR / filename
    if not log_path.exists():
        return {"ok": False, "msg": "Log file not found"}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return {"ok": True, "lines": lines[-tail:], "total": len(lines)}
    except Exception as e:
        return {"ok": False, "msg": f"读取日志失败: {type(e).__name__}: {e}"}
