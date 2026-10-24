"""
对话流水：历史读取 + SSE 实时推流
（2026-08-30 拆分自 main.py；_read_turns_history 改为只读文件尾部）
"""
import asyncio
import json
import os

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ._shared import QQBOT_DIR, logger

router = APIRouter()

_TURNS_STREAM_PATH = QQBOT_DIR / "logs" / "turns_stream.jsonl"
_TURNS_HISTORY_MAX = 200


def _tail_lines(path, n: int) -> list[str]:
    """从文件尾部高效读取最近 n 行（不读全文件，适合大 jsonl）"""
    result: list[str] = []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 8192
            buf = b""
            while size > 0 and len(result) < n:
                read_size = min(block, size)
                size -= read_size
                f.seek(size)
                buf = f.read(read_size) + buf
                if buf.count(b"\n") >= n:
                    # 截断：只保留最后 n 行 + 可能的不完整首行
                    lines = buf.split(b"\n")
                    buf = b"\n".join(lines[-(n + 1):])
            result = buf.decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    return result[-n:]


def _read_turns_history(limit: int = _TURNS_HISTORY_MAX) -> list[dict]:
    """读 turns_stream.jsonl 尾部（约 limit*3 行），聚合成 turns 数组（从最新往最旧）。

    2026-08-30: 从"每次 readlines 全文件"改为尾部读取——文件可能上万行，
    每次全读 + 逐行 json.loads 在 30s 轮询下是浪费。
    """
    if not _TURNS_STREAM_PATH.exists():
        return []
    # 每个 turn 通常 2-4 个事件行，读 3 倍缓冲足够覆盖最近 limit 个 turn
    lines = _tail_lines(_TURNS_STREAM_PATH, limit * 3 + 10)
    # 倒序聚合:每个 turn 第一个事件是 turn_started
    turns: list[dict] = []
    current: dict | None = None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "turn_started":
            current = ev.get("turn", {})
            current["steps"] = current.get("steps", [])
            turns.append(current)
            current = None  # 与原实现一致：倒序中 turn_started 之上的 step_added 已随 turn 数据内置
            if len(turns) >= limit:
                break
        elif t == "step_added" and current is not None:
            current["steps"].append(ev.get("step"))
        elif t == "turn_ended" and current is not None:
            current["status"] = ev.get("status")
            current["duration"] = ev.get("duration")
    # turns 是倒序的,steps 也是倒序的,翻回来
    for t in turns:
        t["steps"].reverse()
    return turns


@router.get("/api/turns/history")
async def api_turns_history(limit: int = _TURNS_HISTORY_MAX):
    """返回最近的 N 个 turn(默认 200,内存 ring 大小)"""
    return {"turns": _read_turns_history(min(limit, _TURNS_HISTORY_MAX))}


@router.get("/api/turns/stream")
async def api_turns_stream():
    """SSE 推流:监听 turns_stream.jsonl 的新增行,实时推到前端"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    # 拿到当前文件大小,只推之后的
    try:
        last_pos = _TURNS_STREAM_PATH.stat().st_size if _TURNS_STREAM_PATH.exists() else 0
    except Exception:
        last_pos = 0

    async def reader():
        nonlocal last_pos
        while True:
            try:
                if _TURNS_STREAM_PATH.exists():
                    with _TURNS_STREAM_PATH.open("r", encoding="utf-8") as f:
                        f.seek(last_pos)
                        new = f.read()
                        if new:
                            last_pos = f.tell()
                            for line in new.splitlines():
                                if line.strip():
                                    try:
                                        await queue.put(line)
                                    except asyncio.QueueFull:
                                        pass
            except Exception:
                pass
            await asyncio.sleep(0.5)

    reader_task = asyncio.create_task(reader())

    async def event_stream():
        try:
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {line.replace(chr(10), chr(10) + 'data: ')}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            reader_task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
