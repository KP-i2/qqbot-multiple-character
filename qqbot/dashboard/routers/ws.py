"""
WebSocket 实时状态推送
（2026-08-30 拆分自 main.py；token 校验改用常量时间比较）
"""
import asyncio
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import monitor
from ._shared import logger, token_ok

router = APIRouter()

WS_PUSH_INTERVAL = int(os.getenv("WS_PUSH_INTERVAL", "5"))  # WebSocket 推送间隔（秒）


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    # Token 认证（WebSocket 不走 HTTP 中间件，需单独校验）
    if not token_ok(websocket.query_params.get("token", "")):
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await websocket.accept()
    try:
        while True:
            try:
                status = await asyncio.get_event_loop().run_in_executor(None, monitor.get_all_status)
                await websocket.send_json(status)
            except WebSocketDisconnect:
                raise  # 重新抛出，由外层处理
            except ConnectionError as e:
                logger.warning(f"WebSocket connection error: {e}")
                break
            except Exception as e:
                logger.error(f"WebSocket send error: {type(e).__name__}: {e}")
                break
            await asyncio.sleep(WS_PUSH_INTERVAL)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket fatal error: {e}")
