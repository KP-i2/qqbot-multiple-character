"""
FastAPI Dashboard 主应用
2026-08-30 重构：路由按业务拆分到 routers/ 包，本文件只负责 app 组装 + 鉴权 + SSR 首屏。
"""
import asyncio
import html
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import monitor, skill_manager
from .routers import corpus, docs, logs, process, settings, skills, turns, ws
from .routers._shared import (
    STATIC_DIR, _AUTH_ENABLED, DASHBOARD_TOKEN, token_ok,
)

logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Dashboard starting...")
    monitor.start_ws_health_probe(interval=3.0)
    logger.info("WS health probe auto-started")
    monitor.start_snowluma_log_watcher()
    logger.info("snowluma log watcher auto-started")
    yield
    logger.info("Dashboard shutting down...")
    if monitor._ws_health_task and not monitor._ws_health_task.done():
        monitor._ws_health_task.cancel()
        logger.info("WS health probe stopped")
    if monitor._snowluma_log_watcher_task and not monitor._snowluma_log_watcher_task.done():
        monitor._snowluma_log_watcher_task.cancel()
        logger.info("snowluma log watcher stopped")


app = FastAPI(title="QQ Bot Dashboard", lifespan=_lifespan)

# 缓存 index.html（避免每次请求读磁盘）
_INDEX_HTML: str | None = None


@app.middleware("http")
async def _static_no_cache(request: Request, call_next):
    """静态资源加 no-cache：保留 ETag 校验，前端改版后浏览器能立即拉到新版"""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def _api_auth_middleware(request: Request, call_next):
    """对 /api/ 路径强制 Token 认证（常量时间比较）；静态资源和页面放行"""
    path = request.url.path
    # 放行：首页、静态文件、健康检查、WS 探活、snowluma 日志流（SSE 浏览器无法带 header）、CORS 预检
    if (path == "/" or path.startswith("/static/") or path == "/api/health"
        or path == "/api/ws-health"
        or path == "/api/snowluma-log/sse" or path == "/api/snowluma-log/tail"
        or request.method == "OPTIONS"):
        return await call_next(request)
    # /api/ 路径需要 token
    if _AUTH_ENABLED and path.startswith("/api/"):
        token = request.headers.get("X-Dashboard-Token") or request.query_params.get("token")
        if not token_ok(token):
            return JSONResponse({"ok": False, "msg": "Unauthorized"}, status_code=401)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── 路由挂载（按业务拆分的模块）──
app.include_router(process.router)
app.include_router(turns.router)
app.include_router(corpus.router)
app.include_router(docs.router)
app.include_router(skills.router)
app.include_router(settings.router)
app.include_router(logs.router)
app.include_router(ws.router)


# ── 前端 ──
async def _build_init_data() -> dict:
    """构造首屏 SSR 数据（注入到 window._INIT_DATA）"""
    loop = asyncio.get_event_loop()
    # status 调用是阻塞的（psutil 扫描），丢到 executor
    try:
        status = await loop.run_in_executor(None, monitor.get_all_status)
    except Exception as e:
        logger.warning(f"SSR status failed: {e}")
        status = {}
    # skills 列表
    try:
        skills = await loop.run_in_executor(None, skill_manager.list_skills)
    except Exception as e:
        logger.warning(f"SSR skills failed: {e}")
        skills = []
    # avatars 映射（uid -> 相对 URL 路径），复用 /api/avatars 的扫描逻辑
    try:
        static_photo_dir = STATIC_DIR / "photo"
        avatars = {}
        if static_photo_dir.exists():
            for d in static_photo_dir.iterdir():
                if d.is_dir():
                    avatar_file = None
                    fallback_file = None
                    for f in d.iterdir():
                        if f.suffix.lower() in ('.jpg', '.jpeg', '.png'):
                            if f.stem.lower() == 'avatar':
                                avatar_file = f
                                break
                            elif fallback_file is None:
                                fallback_file = f
                    chosen = avatar_file or fallback_file
                    if chosen:
                        avatars[d.name] = f"/static/photo/{d.name}/{chosen.name}"
    except Exception as e:
        logger.warning(f"SSR avatars failed: {e}")
        avatars = {}
    return {
        "status": status,
        "skills": skills,
        "avatars": avatars,
        "server_time": int(time.time()),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    global _INDEX_HTML
    if _INDEX_HTML is None:
        _INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # 注入 auth token 供前端使用（转义防止 XSS）
    safe_token = html.escape(DASHBOARD_TOKEN, quote=True)
    token_script = f'<script>window._DASHBOARD_TOKEN="{safe_token}";</script>'
    # 注入首屏 SSR 数据（避免前端 fetch 等待）
    init_data = await _build_init_data()
    init_script = f'<script>window._INIT_DATA={json.dumps(init_data, ensure_ascii=False, default=str)};</script>'
    page_html = _INDEX_HTML.replace("</head>", token_script + init_script + "</head>", 1)
    return HTMLResponse(content=page_html, headers={"Cache-Control": "no-store"})
