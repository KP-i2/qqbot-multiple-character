"""
进程管理 + 系统健康 + Dashboard 自管理 + WS 探活 + snowluma 日志流
（2026-08-30 拆分自 main.py）
"""
import asyncio
import os
import signal
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .. import monitor
from ._shared import _safe_name, logger, token_ok

router = APIRouter()


# ── 进程监控 ──
@router.get("/api/status")
async def api_status():
    return await asyncio.get_event_loop().run_in_executor(None, monitor.get_all_status)


@router.post("/api/process/start/{name}")
async def api_start_process(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    if name == "nonebot2":
        # 加锁防止与 full_restart 冲突
        if monitor._restart_lock.locked():
            return {"ok": False, "msg": "重启操作进行中，请稍后再试"}
        async with monitor._restart_lock:
            result = await asyncio.get_event_loop().run_in_executor(None, monitor.start_nonebot2)
            monitor.invalidate_status_cache()
        if result.get("ok"):
            logger.info("NoneBot2 process started")
        return result
    if name == "snowluma":
        if monitor._restart_lock.locked():
            return {"ok": False, "msg": "重启操作进行中，请稍后再试"}
        async with monitor._restart_lock:
            result = await asyncio.get_event_loop().run_in_executor(None, monitor.start_snowluma)
            monitor.invalidate_status_cache()
        if result.get("ok"):
            logger.info("snowluma started")
        return result
    return {"ok": False, "msg": f"Unknown process: {name} (supported: nonebot2, snowluma)"}


@router.post("/api/process/stop/{name}")
async def api_stop_process(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    if name == "nonebot2":
        # 加锁防止与 full_restart 冲突
        if monitor._restart_lock.locked():
            return {"ok": False, "msg": "重启操作进行中，请稍后再试"}
        async with monitor._restart_lock:
            result = await asyncio.get_event_loop().run_in_executor(None, monitor.stop_nonebot2)
            monitor.invalidate_status_cache()
        if result.get("ok"):
            logger.info("NoneBot2 process stopped")
        return result
    if name == "snowluma":
        if monitor._restart_lock.locked():
            return {"ok": False, "msg": "重启操作进行中，请稍后再试"}
        async with monitor._restart_lock:
            result = await asyncio.get_event_loop().run_in_executor(None, monitor.stop_snowluma)
            monitor.invalidate_status_cache()
        if result.get("ok"):
            logger.info("snowluma stopped")
        return result
    return {"ok": False, "msg": f"Unknown process: {name} (supported: nonebot2, snowluma)"}


@router.post("/api/process/full-restart")
async def api_full_restart():
    result = await monitor.full_restart()
    monitor.invalidate_status_cache()
    return result


@router.get("/api/health")
async def api_health():
    """系统健康检查（复用 get_all_status 的 5s 缓存，避免高频全进程遍历）"""
    loop = asyncio.get_event_loop()
    all_status = await loop.run_in_executor(None, monitor.get_all_status)
    nb = all_status.get("nonebot2") or {}
    snowluma = all_status.get("napcat") or {}

    bot_running = bool(nb.get("running"))
    desktop_running = bool(snowluma.get("running"))
    qq_running = bool(snowluma.get("qq_running"))
    ws_connected = bool(snowluma.get("ws_connected", False))

    # 判断状态
    if bot_running and desktop_running and qq_running and ws_connected:
        status = "ok"
    elif bot_running and (desktop_running or qq_running):
        status = "warn"  # 部分服务运行，但 WS 可能未连接
    else:
        status = "error"

    return {
        "status": status,
        "bot": bot_running,
        "snowluma": desktop_running,   # 2026-06-27 字段名统一:snowluma
        "napcat": desktop_running,     # 兼容旧前端
        "qq": qq_running,
        "ws_connected": ws_connected,
        "bot_port_open": bool(nb.get("port_open")),
        "timestamp": time.strftime("%H:%M:%S"),
    }


# ── Dashboard 自身管理 ──
@router.get("/api/dashboard/status")
async def api_dashboard_status():
    return await asyncio.get_event_loop().run_in_executor(None, monitor.get_dashboard_status)


@router.post("/api/dashboard/restart")
async def api_dashboard_restart():
    """重启 Dashboard（spawn 独立进程，当前进程随后退出）"""
    result = await asyncio.get_event_loop().run_in_executor(None, monitor.restart_dashboard)
    if result.get("ok"):
        logger.info("Dashboard restart initiated, shutting down current instance...")
        # 延迟关闭当前实例，让 response 先返回
        asyncio.get_event_loop().call_later(1, lambda: asyncio.create_task(_shutdown_self()))
    return result


async def _shutdown_self():
    """优雅关闭当前 Dashboard 进程"""
    logger.info("Dashboard self-shutdown...")
    os.kill(os.getpid(), signal.SIGTERM)


# ── WS 健康状态 API（前端轮询，3s 一次） ──
@router.get("/api/ws-health")
async def api_ws_health():
    h = monitor.get_ws_health()
    last_ok = h.get("last_ok_at", 0.0)
    last_disc = h.get("last_disconnect_at", 0.0)
    now = time.time()
    h["disconnected_for_sec"] = int(now - last_disc) if last_disc and not h.get("ws_upgrade_ok") else 0
    h["last_ok_at"] = last_ok
    return h


# ── snowluma 日志：最近 64KB（用于面板首次加载） ──
@router.get("/api/snowluma-log/tail")
async def api_snowluma_log_tail():
    return {"content": monitor._read_snowluma_log_tail()}


# ── snowluma 日志实时流（SSE，前端 EventSource 订阅） ──
@router.get("/api/snowluma-log/sse")
async def api_snowluma_log_sse():
    """Server-Sent Events：snowluma 日志新行实时推流到前端"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    monitor._snowluma_log_subscribers.add(queue)
    # 首次连接立刻补一份 tail（最多 8KB）让前端有内容
    tail = monitor._read_snowluma_log_tail(8 * 1024)
    if tail:
        try:
            queue.put_nowait(tail)
        except asyncio.QueueFull:
            pass

    async def event_stream():
        try:
            while True:
                # 心跳防止代理超时
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=15.0)
                    # SSE data 字段：多行用\n分隔，注释里 \n 不会断事件
                    yield f"data: {chunk.replace(chr(10), chr(10) + 'data: ')}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            monitor._snowluma_log_subscribers.discard(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
