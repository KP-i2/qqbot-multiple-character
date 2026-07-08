"""FastAPI Dashboard 主应用（修复版）"""
import asyncio, html, json, logging, os, re, secrets, signal, subprocess, sys, tempfile, time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import monitor, weibo_fetcher, skill_manager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Dashboard starting...")
    monitor.start_watchdog(interval=30)
    logger.info("Process watchdog auto-started")
    monitor.start_ws_health_probe(interval=3.0)
    logger.info("WS health probe auto-started")
    monitor.start_snowluma_log_watcher()
    logger.info("snowluma log watcher auto-started")
    yield
    logger.info("Dashboard shutting down...")
    monitor.stop_watchdog()
    logger.info("Process watchdog stopped")
    if monitor._ws_health_task and not monitor._ws_health_task.done():
        monitor._ws_health_task.cancel()
        logger.info("WS health probe stopped")
    if monitor._snowluma_log_watcher_task and not monitor._snowluma_log_watcher_task.done():
        monitor._snowluma_log_watcher_task.cancel()
        logger.info("snowluma log watcher stopped")


app = FastAPI(title="QQ Bot Dashboard", lifespan=_lifespan)
STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).parent.parent.parent
PRIVATE_DIR = PROJECT_ROOT / "private"
COOKIES_FILE = PRIVATE_DIR / "cookies.json"
TWITTER_COOKIES_FILE = PRIVATE_DIR / "twitter_cookies.json"
TWITTER_SCRIPT = PROJECT_ROOT / "scripts" / "fetch_twitter.py"
TWITTER_CORPUS_DIR = PROJECT_ROOT / "corpus"
DOCS_DIR = PROJECT_ROOT / "docs"
README_FILE = PROJECT_ROOT / "README.md"

# ── 安全：Token 认证 ──
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
if not DASHBOARD_TOKEN:
    DASHBOARD_TOKEN = secrets.token_urlsafe(16)
    logger.warning("DASHBOARD_TOKEN not set in .env — generated random token: %s", DASHBOARD_TOKEN)
_AUTH_ENABLED = DASHBOARD_TOKEN and DASHBOARD_TOKEN.strip()

# 缓存 index.html（避免每次请求读磁盘）
_INDEX_HTML: str | None = None


@app.middleware("http")
async def _api_auth_middleware(request: Request, call_next):
    """对 /api/ 路径强制 Token 认证；静态资源和页面放行"""
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
        if token != DASHBOARD_TOKEN:
            return JSONResponse({"ok": False, "msg": "Unauthorized"}, status_code=401)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _safe_name(name: str) -> bool:
    """校验名称是否安全（只允许字母数字下划线横线）"""
    return bool(re.match(r'^[a-zA-Z0-9_\-]+$', name))


def _safe_path_component(s: str) -> bool:
    """校验路径组件是否安全（无路径遍历）"""
    return '..' not in s and '/' not in s and '\\' not in s and s.strip()


def _check_upload_size(content: bytes, max_mb: int = 10):
    """检查上传文件大小"""
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large (max {max_mb}MB)")


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


# ── WS 健康状态 API（前端轮询，3s 一次） ──
@app.get("/api/ws-health")
async def api_ws_health():
    h = monitor.get_ws_health()
    last_ok = h.get("last_ok_at", 0.0)
    last_disc = h.get("last_disconnect_at", 0.0)
    now = time.time()
    h["disconnected_for_sec"] = int(now - last_disc) if last_disc and not h.get("ws_upgrade_ok") else 0
    h["last_ok_at"] = last_ok
    return h


# ── snowluma 日志：最近 64KB（用于面板首次加载） ──
@app.get("/api/snowluma-log/tail")
async def api_snowluma_log_tail():
    return {"content": monitor._read_snowluma_log_tail()}


# ── snowluma 日志实时流（SSE，前端 EventSource 订阅） ──
@app.get("/api/snowluma-log/sse")
async def api_snowluma_log_sse():
    """Server-Sent Events：snowluma 日志新行实时推流到前端"""
    from fastapi.responses import StreamingResponse
    import asyncio as _asyncio

    queue: _asyncio.Queue = _asyncio.Queue(maxsize=200)
    monitor._snowluma_log_subscribers.add(queue)
    # 首次连接立刻补一份 tail（最多 8KB）让前端有内容
    tail = monitor._read_snowluma_log_tail(8 * 1024)
    if tail:
        try:
            queue.put_nowait(tail)
        except _asyncio.QueueFull:
            pass

    async def event_stream():
        try:
            while True:
                # 心跳防止代理超时
                try:
                    chunk = await _asyncio.wait_for(queue.get(), timeout=15.0)
                    # SSE data 字段：多行用\n分隔，注释里 \n 不会断事件
                    yield f"data: {chunk.replace(chr(10), chr(10) + 'data: ')}\n\n"
                except _asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            monitor._snowluma_log_subscribers.discard(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 对话流水(turn_recorder 写的 turns_stream.jsonl)──
_TURNS_STREAM_PATH = PROJECT_ROOT / "qqbot" / "logs" / "turns_stream.jsonl"
_TURNS_HISTORY_MAX = 200


def _read_turns_history(limit: int = _TURNS_HISTORY_MAX) -> list[dict]:
    """读 turns_stream.jsonl,聚合成 turns 数组(从最新往最旧)。"""
    if not _TURNS_STREAM_PATH.exists():
        return []
    # 一次性读到内存(2k-10k 行 jsonl 不到 1MB),简单够用
    try:
        with _TURNS_STREAM_PATH.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
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
            current = None
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


@app.get("/api/turns/history")
async def api_turns_history(limit: int = _TURNS_HISTORY_MAX):
    """返回最近的 N 个 turn(默认 200,内存 ring 大小)"""
    return {"turns": _read_turns_history(min(limit, _TURNS_HISTORY_MAX))}


@app.get("/api/turns/stream")
async def api_turns_stream():
    """SSE 推流:监听 turns_stream.jsonl 的新增行,实时推到前端"""
    from fastapi.responses import StreamingResponse
    import asyncio as _asyncio

    queue: _asyncio.Queue = _asyncio.Queue(maxsize=200)

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
                                    except _asyncio.QueueFull:
                                        pass
            except Exception:
                pass
            await _asyncio.sleep(0.5)

    reader_task = _asyncio.create_task(reader())

    async def event_stream():
        try:
            while True:
                try:
                    line = await _asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {line.replace(chr(10), chr(10) + 'data: ')}\n\n"
                except _asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            reader_task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 进程监控 ──
@app.get("/api/status")
async def api_status():
    return await asyncio.get_event_loop().run_in_executor(None, monitor.get_all_status)


@app.post("/api/process/start/{name}")
async def api_start_process(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    if name == "nonebot2":
        # 加锁防止与 full_restart / 看门狗冲突
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


@app.post("/api/process/stop/{name}")
async def api_stop_process(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    if name == "nonebot2":
        # 加锁防止与 full_restart / 看门狗冲突
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


@app.post("/api/process/full-restart")
async def api_full_restart():
    result = await monitor.full_restart()
    monitor.invalidate_status_cache()
    return result


@app.get("/api/health")
async def api_health():
    """系统健康检查（snowluma Desktop 外部管理，检测进程+WS连接状态）"""
    loop = asyncio.get_event_loop()
    snowluma = await loop.run_in_executor(None, monitor.get_napcat_status)
    nb = await loop.run_in_executor(None, monitor.get_nonebot2_status)
    watchdog = monitor.get_watchdog_status()

    bot_running = nb["running"]
    desktop_running = snowluma["running"]
    qq_running = snowluma["qq_running"]
    ws_connected = snowluma.get("ws_connected", False)

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
        "bot_port_open": nb["port_open"],
        "watchdog": watchdog,
        "timestamp": time.strftime("%H:%M:%S"),
    }


# ── 看门狗控制 ──
@app.post("/api/watchdog/start")
async def api_watchdog_start():
    task = monitor.start_watchdog()
    if task:
        return {"ok": True, "msg": "进程看门狗已启动（每30秒检查，自动重启崩溃的进程）"}
    return {"ok": False, "msg": "看门狗已在运行"}


@app.post("/api/watchdog/stop")
async def api_watchdog_stop():
    monitor.stop_watchdog()
    return {"ok": True, "msg": "进程看门狗已停止"}


@app.get("/api/watchdog/status")
async def api_watchdog_status():
    return monitor.get_watchdog_status()


# ── Dashboard 自身管理 ──
@app.get("/api/dashboard/status")
async def api_dashboard_status():
    return await asyncio.get_event_loop().run_in_executor(None, monitor.get_dashboard_status)


@app.post("/api/dashboard/restart")
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
    import signal
    logger.info("Dashboard self-shutdown...")
    monitor.stop_watchdog()
    os.kill(os.getpid(), signal.SIGTERM)


# ── Cookie 管理 ──
@app.get("/api/cookies")
async def api_cookie_status():
    return await asyncio.get_event_loop().run_in_executor(None, weibo_fetcher.get_cookie_status)


@app.post("/api/cookies/upload")
async def api_upload_cookie(file: UploadFile = File(...)):
    content = await file.read()
    _check_upload_size(content, 5)
    try:
        json.loads(content)
    except json.JSONDecodeError:
        return {"ok": False, "msg": "Invalid JSON file"}
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_FILE.write_bytes(content)
    logger.info(f"Cookie file uploaded: {file.filename} ({len(content)} bytes)")
    return {"ok": True, "msg": f"Cookie saved ({len(content)} bytes)"}


# ── 语料管理 ──
@app.get("/api/corpus")
async def api_list_corpora():
    return await asyncio.get_event_loop().run_in_executor(None, weibo_fetcher.list_corpora)


@app.get("/api/corpus/{uid}")
async def api_corpus_detail(uid: str):
    if not _safe_path_component(uid):
        return {"ok": False, "msg": "Invalid UID"}
    corpus_dir = (PROJECT_ROOT / "corpus" / uid).resolve()
    if not str(corpus_dir).startswith(str((PROJECT_ROOT / "corpus").resolve())):
        return {"ok": False, "msg": "Invalid path"}
    if not corpus_dir.exists():
        return {"ok": False, "msg": "Not found"}
    files = []
    for f in corpus_dir.iterdir():
        if f.suffix == ".txt":
            with open(f, encoding="utf-8", errors="ignore") as fh:
                preview = fh.read(500)
            files.append({
                "name": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "preview": preview,
            })
    return {"ok": True, "files": files}


@app.post("/api/corpus/fetch")
async def api_fetch_weibo(uid: str = Form(...)):
    if not _safe_path_component(uid):
        return {"ok": False, "msg": "Invalid UID"}
    result = await weibo_fetcher.fetch_weibo(uid)
    if result.get("ok"):
        logger.info(f"Weibo corpus fetched: uid={uid}")
    return result


@app.post("/api/corpus/qq-import")
async def api_qq_import(
    file: UploadFile = File(...),
    qq_uid: str = Form(""),
    dir_uid: str = Form(""),
    uid: str = Form(""),  # 兼容旧版
    name: str = Form(""),
):
    if not qq_uid:
        qq_uid = uid
    if not dir_uid:
        dir_uid = uid or qq_uid
    if not _safe_path_component(dir_uid):
        return {"ok": False, "msg": "Invalid directory UID"}
    content = await file.read()
    _check_upload_size(content, 20)
    suffix = Path(file.filename or "data.json").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, weibo_fetcher.extract_qq_messages, tmp_path, qq_uid, dir_uid, name
        )
        if result.get("ok"):
            logger.info(f"QQ corpus imported: qq_uid={qq_uid}, dir_uid={dir_uid}")
        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/corpus/qq-senders")
async def api_qq_senders(file: UploadFile = File(...)):
    content = await file.read()
    _check_upload_size(content, 20)
    suffix = Path(file.filename or "data.json").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return await asyncio.get_event_loop().run_in_executor(
            None, weibo_fetcher.list_senders, tmp_path
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/corpus/import-text")
async def api_import_text(
    file: UploadFile = File(...),
    uid: str = Form(""),
    name: str = Form(""),
):
    if uid and not _safe_path_component(uid):
        return {"ok": False, "msg": "Invalid UID"}
    content = await file.read()
    _check_upload_size(content, 10)
    filename = Path(file.filename or "import.txt").name  # 去掉目录组件，防路径穿越
    if not _safe_path_component(filename):
        return {"ok": False, "msg": "Invalid filename"}
    result = await asyncio.get_event_loop().run_in_executor(
        None, weibo_fetcher.import_text_file, content, filename, uid, name
    )
    if result.get("ok"):
        logger.info(f"Text corpus imported: {filename}")
    return result


@app.post("/api/corpus/generate-skill")
async def api_generate_skill(
    uid: str = Form(...),
    skill_name: str = Form(...),
    display_name: str = Form(""),
    description: str = Form(""),
    version: str = Form("1.0.0"),
    character: str = Form("celebrity"),
):
    if not _safe_name(skill_name):
        return {"ok": False, "msg": "Invalid skill name"}
    if not display_name:
        display_name = skill_name
    result = await asyncio.get_event_loop().run_in_executor(
        None, weibo_fetcher.generate_skill_from_corpus, uid, skill_name, display_name, description, version, character
    )
    if result.get("ok"):
        trigger = PROJECT_ROOT / ".reload_skills_trigger"
        trigger.write_text(f"generate from dashboard at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Skill generated from corpus: {uid} (uid={uid}, character={character})")
    return result


# ── Twitter 抓取 ──
def _get_twitter_cookie_status() -> dict:
    """检查 twitter_cookies.json 状态（项目根目录）"""
    if not TWITTER_COOKIES_FILE.exists():
        return {"exists": False, "count": 0, "size_kb": 0, "modified": ""}
    try:
        data = json.loads(TWITTER_COOKIES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            count = len(data)
        elif isinstance(data, dict):
            count = len(data)
        else:
            count = 0
        stat = TWITTER_COOKIES_FILE.stat()
        return {
            "exists": True,
            "count": count,
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        }
    except Exception as e:
        return {"exists": False, "error": str(e)}


def _run_twitter_fetch_subprocess(screen_name: str, count: int | None, all_tweets: bool, replies: bool) -> dict:
    """调用 scripts/fetch_twitter.py 抓取（同步，subprocess 阻塞）"""
    if not TWITTER_SCRIPT.exists():
        return {"ok": False, "msg": f"脚本不存在: {TWITTER_SCRIPT}"}
    venv_py = PROJECT_ROOT / "skill_qqbot" / "Scripts" / "python.exe"
    py = str(venv_py) if venv_py.exists() else sys.executable

    args = [py, str(TWITTER_SCRIPT), screen_name]
    if all_tweets:
        args.append("--all")
    elif count is not None:
        args.extend(["--count", str(count)])
    if replies:
        args.append("--replies")

    try:
        proc = subprocess.run(
            args,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,  # 30 分钟上限
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0:
            corpus_path = TWITTER_CORPUS_DIR / f"twitter_{screen_name.lower()}"
            return {
                "ok": True,
                "msg": f"抓取完成，输出: {corpus_path}",
                "stdout_tail": out[-2000:] if len(out) > 2000 else out,
                "corpus_dir": str(corpus_path),
            }
        return {
            "ok": False,
            "msg": f"抓取失败 (exit={proc.returncode})",
            "stdout_tail": out[-2000:] if len(out) > 2000 else out,
            "stderr_tail": err[-1000:] if len(err) > 1000 else err,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "抓取超时（>30 分钟），建议先用 --count 50 测试"}
    except Exception as e:
        return {"ok": False, "msg": f"执行异常: {e}"}


def _list_twitter_corpora() -> list[dict]:
    """列出 corpus/twitter_*/ 下的语料"""
    if not TWITTER_CORPUS_DIR.exists():
        return []
    results = []
    for d in sorted(TWITTER_CORPUS_DIR.iterdir()):
        if not d.is_dir() or not d.name.startswith("twitter_"):
            continue
        screen_name = d.name[len("twitter_"):]
        corpus_txt = d / "corpus.txt"
        tweets_raw = d / "tweets_raw.json"
        info = {
            "screen_name": screen_name,
            "dir": d.name,
            "corpus_exists": corpus_txt.exists(),
            "raw_exists": tweets_raw.exists(),
            "size_kb": round(sum(f.stat().st_size for f in d.iterdir() if f.is_file()) / 1024, 1),
        }
        if corpus_txt.exists():
            stat = corpus_txt.stat()
            info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        results.append(info)
    return results


@app.get("/api/twitter/cookie")
async def api_twitter_cookie_status():
    return await asyncio.get_event_loop().run_in_executor(None, _get_twitter_cookie_status)


@app.post("/api/twitter/cookie/upload")
async def api_twitter_cookie_upload(file: UploadFile = File(...)):
    content = await file.read()
    _check_upload_size(content, 5)
    try:
        data = json.loads(content)
        if not isinstance(data, (list, dict)):
            return {"ok": False, "msg": "Cookie 格式错误（需要 JSON 数组或对象）"}
    except json.JSONDecodeError:
        return {"ok": False, "msg": "Invalid JSON file"}
    TWITTER_COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    TWITTER_COOKIES_FILE.write_bytes(content)
    logger.info(f"Twitter cookie uploaded: {file.filename} ({len(content)} bytes)")
    return {"ok": True, "msg": f"Twitter Cookie saved ({len(content)} bytes)"}


@app.get("/api/twitter/corpora")
async def api_twitter_corpora():
    return await asyncio.get_event_loop().run_in_executor(None, _list_twitter_corpora)


@app.post("/api/twitter/fetch")
async def api_twitter_fetch(
    screen_name: str = Form(...),
    count: int = Form(200),
    all_tweets: bool = Form(False),
    replies: bool = Form(False),
):
    """抓取推特用户推文（同步阻塞，最长 30 分钟）"""
    screen_name = screen_name.strip().lstrip("@").lower()
    if not screen_name or not screen_name.replace("_", "").isalnum():
        return {"ok": False, "msg": "无效的 screen_name（只允许字母数字下划线）"}
    if not all_tweets and (count < 1 or count > 5000):
        return {"ok": False, "msg": "count 必须在 1-5000 之间"}

    logger.info(f"Twitter fetch start: @{screen_name} (count={count}, all={all_tweets}, replies={replies})")
    result = await asyncio.get_event_loop().run_in_executor(
        None, _run_twitter_fetch_subprocess, screen_name, count, all_tweets, replies
    )
    if result.get("ok"):
        logger.info(f"Twitter fetch done: @{screen_name}")
    return result


# ── 文档查看（实时读取 README.md + docs/*.md）──
_DOCS_TITLE_OVERRIDES = {
    "README.md": "项目说明",
}


def _list_docs() -> list[dict]:
    """列出可用文档：根 README.md + docs/*.md"""
    docs: list[dict] = []
    if README_FILE.exists():
        st = README_FILE.stat()
        docs.append({
            "name": "README.md",
            "title": _DOCS_TITLE_OVERRIDES.get("README.md", "README"),
            "path_kind": "readme",
            "size_kb": round(st.st_size / 1024, 1),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        })
    if DOCS_DIR.exists():
        for f in sorted(DOCS_DIR.glob("*.md")):
            if not f.is_file():
                continue
            st = f.stat()
            docs.append({
                "name": f"docs/{f.name}",
                "title": f.stem,
                "path_kind": "docs_md",
                "size_kb": round(st.st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            })
    return docs


def _resolve_doc_path(name: str) -> Path | None:
    """将 API 传入的 name 解析为绝对路径；不允许路径遍历"""
    if not name or ".." in name or name.startswith(("/", "\\")):
        return None
    if name in ("README.md", "README"):
        return README_FILE
    if name.startswith("docs/"):
        return PROJECT_ROOT / name
    return None


def _read_doc(name: str) -> dict:
    """读取单个文档内容"""
    path = _resolve_doc_path(name)
    if path is None:
        return {"ok": False, "msg": "Invalid doc name（仅支持 README.md 和 docs/*.md）"}
    try:
        resolved = path.resolve()
        readme_root = README_FILE.resolve()
        docs_root = DOCS_DIR.resolve()
        in_readme = (resolved == readme_root)
        in_docs = (resolved.parent == docs_root)
        if not (in_readme or in_docs):
            return {"ok": False, "msg": "Path traversal blocked"}
    except Exception:
        return {"ok": False, "msg": "Invalid path"}
    if not resolved.exists() or not resolved.is_file():
        return {"ok": False, "msg": f"文档不存在: {name}"}
    if resolved.suffix.lower() != ".md":
        return {"ok": False, "msg": "仅支持 .md 文件"}
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = resolved.read_text(encoding="gbk")
        except Exception as e:
            return {"ok": False, "msg": f"读取失败: {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"读取失败: {e}"}
    st = resolved.stat()
    return {
        "ok": True,
        "name": name,
        "title": _DOCS_TITLE_OVERRIDES.get(name, resolved.stem),
        "content": content,
        "size_kb": round(st.st_size / 1024, 1),
        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
    }


@app.get("/api/docs")
async def api_list_docs():
    return await asyncio.get_event_loop().run_in_executor(None, _list_docs)


@app.get("/api/docs/{name:path}")
async def api_get_doc(name: str):
    return await asyncio.get_event_loop().run_in_executor(None, _read_doc, name)


# ── Skill 管理 ──
@app.get("/api/skills")
async def api_list_skills():
    return skill_manager.list_skills()


@app.get("/api/skills/{name}")
async def api_skill_detail(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    return skill_manager.get_skill_content(name)


@app.post("/api/skills/create")
async def api_create_skill(
    name: str = Form(...),
    display_name: str = Form(""),
    description: str = Form(""),
    version: str = Form("1.0.0"),
    persona: str = Form(""),
    work: str = Form(""),
):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    if not display_name:
        display_name = name
    result = skill_manager.create_skill(name, display_name, description, persona, work, version)
    if result.get("ok"):
        logger.info(f"Skill created: {name} ({display_name})")
    return result


# ── 蒸馏管理（带并发保护）──
_running_retrains: set[str] = set()
_retrain_tasks: dict[str, asyncio.Task] = {}  # 保持强引用，防止 GC 销毁


@app.post("/api/skills/{name}/retrain")
async def api_retrain_skill(name: str, character: str = "celebrity", research_profile: str = "budget-friendly"):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    # 并发保护：同一 skill 不允许同时蒸馏
    if name in _running_retrains:
        return {"ok": False, "msg": f"蒸馏正在进行中，请等待完成后再试"}
    _running_retrains.add(name)
    task = asyncio.create_task(_run_retrain(name, character, research_profile))
    _retrain_tasks[name] = task
    task.add_done_callback(lambda t, n=name: _retrain_tasks.pop(n, None))
    logger.info(f"Retrain started: {name} ({character}/{research_profile})")
    return {"ok": True, "msg": f"蒸馏已启动 ({character}/{research_profile})，请在进度面板查看"}


async def _run_retrain(name, character, research_profile):
    """Background wrapper for retrain_skill"""
    try:
        result = await weibo_fetcher.retrain_skill(name, character, research_profile)
        if result.get("ok"):
            trigger = PROJECT_ROOT / ".reload_skills_trigger"
            trigger.write_text(f"reload from dashboard at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            # retrain_skill 返回了错误字典（非异常），需要同步到进度追踪
            err_msg = result.get("msg", "未知错误")
            logger.error(f"Retrain '{name}' returned error: {err_msg}")
            weibo_fetcher._update_progress(name, "error", err_msg, done=True, ok=False)
    except Exception as e:
        logger.error(f"Retrain '{name}' failed: {e}")
        weibo_fetcher._update_progress(name, "error", str(e), done=True, ok=False)
    finally:
        _running_retrains.discard(name)


@app.get("/api/skills/{name}/retrain-progress")
async def api_retrain_progress(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    progress = weibo_fetcher.get_retrain_progress(name)
    progress["is_running"] = name in _running_retrains
    return progress


@app.post("/api/bot/reload")
async def api_bot_reload():
    """触发 bot 重新加载 skill"""
    trigger = PROJECT_ROOT / ".reload_skills_trigger"
    trigger.write_text(f"manual reload at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("Manual bot reload triggered")
    return {"ok": True, "msg": "Reload trigger sent. Bot will reload skills on next message."}


@app.put("/api/skills/{name}/{filename}")
async def api_update_skill_file(name: str, filename: str, request: Request):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid skill name"}
    # 文件名允许包含点（如 persona.md）
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', filename) or '..' in filename:
        return {"ok": False, "msg": "Invalid filename"}
    # 路径包含检查：确保解析后仍在 skill 目录内
    skill_dir = (PROJECT_ROOT / "qqbot" / "skills" / name).resolve()
    target = (skill_dir / filename).resolve()
    if not str(target).startswith(str(skill_dir)):
        return {"ok": False, "msg": "Invalid file path"}
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "msg": "Invalid JSON body"}
    result = skill_manager.update_skill_file(name, filename, body.get("content", ""))
    if result.get("ok"):
        # 保存即重载：写入 trigger 文件让 bot 热加载
        trigger = PROJECT_ROOT / ".reload_skills_trigger"
        trigger.write_text(f"auto reload after {name}/{filename} saved at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        result["reloaded"] = True
        logger.info(f"Skill file updated: {name}/{filename}")
    return result


@app.post("/api/skills/{name}/integrate-trait")
async def api_integrate_trait(name: str, request: Request):
    """AI 智能整合：输入特征描述，调用 DeepSeek 合并到 persona.md"""
    if not _safe_name(name):
        return {"ok": False, "msg": "无效的技能名称"}
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "msg": "无效的 JSON 请求"}
    trait = (body.get("trait") or "").strip()
    target_file = body.get("target", "persona.md")
    if target_file not in ("persona.md", "work.md"):
        target_file = "persona.md"
    if not trait:
        return {"ok": False, "msg": "请输入特征描述"}
    # 检查技能是否存在
    skill_dir = PROJECT_ROOT / "qqbot" / "skills" / name
    if not skill_dir.exists():
        return {"ok": False, "msg": f"技能 '{name}' 不存在"}
    # 读取当前文件内容
    target_path = skill_dir / target_file
    if not target_path.exists():
        current_content = ""
    else:
        current_content = target_path.read_text(encoding="utf-8")
    # 调用 DeepSeek 智能整合
    ok, result_text = await weibo_fetcher._deepseek_call(
        messages=[
            {"role": "system", "content": (
                "你是一个角色人设文档编辑专家。你的任务是将用户提供的新特征描述，"
                "智能整合到现有的角色人设文档（persona.md）中。\n\n"
                "规则：\n"
                "1. 保持现有文档的结构和风格不变\n"
                "2. 将新特征融入最合适的章节，如果没有合适的章节则追加到末尾\n"
                "3. 如果新特征与已有内容有重叠，合并而非重复\n"
                "4. 如果文档中存在「Correction Log」章节，必须在该章节中追加一条变更记录，格式为：\n"
                "   - YYYY-MM-DD: 整合了「新特征的简要摘要」，影响章节：Layer X/章节名\n"
                "   如果 Correction Log 为 (empty) 或不存在，则替换 (empty) 或新增该章节（放在文档最后）\n"
                "5. 用中文输出完整的修改后文档（不要输出 diff，输出完整内容）\n"
                "6. 如果现有文档为空，根据输入创建一份结构化的 persona 文档\n"
                "7. 只输出文档内容，不要加任何解释或前言"
            )},
            {"role": "user", "content": (
                f"当前 {target_file} 内容：\n"
                f"```\n{current_content}\n```\n\n"
                f"新特征描述：\n{trait}\n\n"
                f"请输出整合后的完整 {target_file} 内容："
            )},
        ],
        temperature=0.6,
        max_tokens=6000,
        timeout=180,
    )
    if not ok:
        return {"ok": False, "msg": f"AI 整合失败: {result_text}"}
    # 清理返回内容（去除可能的 markdown 代码块包裹）
    merged = result_text.strip()
    if merged.startswith("```"):
        first_nl = merged.index("\n") if "\n" in merged else 3
        merged = merged[first_nl + 1:]
    if merged.endswith("```"):
        merged = merged[:-3].rstrip()
    # 写入文件
    target_path.write_text(merged, encoding="utf-8")
    # 自动重载
    trigger = PROJECT_ROOT / ".reload_skills_trigger"
    trigger.write_text(f"auto reload after trait integrate on {name}/{target_file} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Trait integrated into {name}/{target_file}")
    return {
        "ok": True,
        "msg": f"特征已整合到 {name}/{target_file}，bot 已自动重载",
        "content": merged,
        "reloaded": True,
    }


@app.get("/api/avatars")
async def api_list_avatars():
    """列出所有角色头像路径"""
    static_photo_dir = STATIC_DIR / "photo"
    result = {}
    if static_photo_dir.exists():
        for d in static_photo_dir.iterdir():
            if d.is_dir():
                # 优先使用 avatar.* 文件（新上传的），否则使用目录下第一个图片
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
                    result[d.name] = f"/static/photo/{d.name}/{chosen.name}"
    return result


@app.post("/api/skills/{name}/avatar")
async def api_upload_avatar(name: str, file: UploadFile = File(...)):
    """上传角色头像（JPG/PNG）"""
    if not _safe_name(name):
        return {"ok": False, "msg": "无效的技能名称"}
    # 检查文件类型
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ('.jpg', '.jpeg', '.png'):
        return {"ok": False, "msg": "仅支持 JPG / PNG 格式"}
    content = await file.read()
    _check_upload_size(content, 5)
    # 保存到 photo 目录（先清理旧文件）
    photo_dir = PROJECT_ROOT / "photo" / name
    photo_dir.mkdir(parents=True, exist_ok=True)
    for old in photo_dir.iterdir():
        if old.is_file() and old.suffix.lower() in ('.jpg', '.jpeg', '.png'):
            old.unlink()
    photo_path = photo_dir / f"avatar{suffix}"
    photo_path.write_bytes(content)
    # 同步到 dashboard 静态目录（同样清理旧文件）
    static_photo_dir = STATIC_DIR / "photo" / name
    static_photo_dir.mkdir(parents=True, exist_ok=True)
    for old in static_photo_dir.iterdir():
        if old.is_file() and old.suffix.lower() in ('.jpg', '.jpeg', '.png'):
            old.unlink()
    (static_photo_dir / f"avatar{suffix}").write_bytes(content)
    logger.info(f"Avatar uploaded for skill: {name} ({len(content)} bytes)")
    return {"ok": True, "msg": f"头像上传成功 ({len(content)} bytes)"}


@app.delete("/api/skills/{name}")
async def api_delete_skill(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    result = await asyncio.get_event_loop().run_in_executor(None, skill_manager.delete_skill, name)
    if result.get("ok"):
        logger.info(f"Skill deleted: {name}")
    return result


# ── 参数设置 ──
_RUNTIME_SETTINGS_FILE = PROJECT_ROOT / "qqbot" / "data" / "runtime_settings.json"
_SETTINGS_RELOAD_TRIGGER = PROJECT_ROOT / "qqbot" / ".reload_settings_trigger"

_SETTINGS_SCHEMA = {
    "active_hours_start":    {"type": "int",   "label": "活跃时段起始 (0-23)",    "default": 0,    "category": "bot"},
    "active_hours_end":      {"type": "int",   "label": "活跃时段结束 (0-23)",    "default": 23,   "category": "bot"},
    "web_search_enabled":    {"type": "bool",  "label": "联网搜索",                "default": True,  "category": "bot"},
    "stream_enabled":        {"type": "bool",  "label": "流式输出",                "default": True,  "category": "stream"},
    "stream_flush_chars":    {"type": "int",   "label": "断句字符阈值",            "default": 60,    "category": "stream", "min": 20, "max": 300},
    "stream_flush_interval": {"type": "float", "label": "断句等待秒数",            "default": 8.0,   "category": "stream", "min": 1, "max": 15},
    "stream_flush_min_chars":{"type": "int",   "label": "最小累积字符数",          "default": 80,    "category": "stream", "min": 20, "max": 300},
    "stream_max_flush_size": {"type": "int",   "label": "单段最大字符",            "default": 180,   "category": "stream", "min": 50, "max": 1000},
    "max_history_rounds":    {"type": "int",   "label": "最大对话轮数",            "default": 100,   "category": "history", "min": 1, "max": 200},
    "history_ttl_hours":     {"type": "int",   "label": "历史过期时间 (小时)",     "default": 36,    "category": "history", "min": 1, "max": 168},
    "history_save_interval": {"type": "int",   "label": "保存间隔 (秒)",           "default": 60,    "category": "history", "min": 10, "max": 600},
    "thinking_timer_seconds":{"type": "int",   "label": "等待提示秒数",            "default": 120,   "category": "timing", "min": 1, "max": 300},
    "multi_turn_enabled":    {"type": "bool",  "label": "多轮对话",                "default": True,  "category": "bot"},
    "test_mode_enabled":     {"type": "bool",  "label": "测试模式 (仅管理员)",     "default": True,  "category": "bot"},
    "active_llm_provider":   {"type": "select","label": "主对话 Provider (含图消息走 vision,其它都走这个)",  "default": "deepseek",
                              "options": ["minimax", "deepseek"],
                              "category": "llm"},
    # ── minimax 专属参数（运行时可调，2026-06-28 补齐 schema）
    # 之前只有前端 input + config.py 默认值，后端 schema 没登记导致 dashboard save 被静默吞掉
    "minimax_max_tokens":                  {"type": "int",  "label": "minimax Max Tokens",                    "default": 16384, "category": "llm", "min": 100,  "max": 32000},
    "minimax_temperature":                 {"type": "float","label": "minimax Temperature",                  "default": 0.95,  "category": "llm", "min": 0.0,  "max": 2.0},
    "minimax_top_p":                       {"type": "float","label": "minimax Top P",                        "default": 0.9,   "category": "llm", "min": 0.0,  "max": 1.0},
    "minimax_reasoning_effort_with_tools": {"type": "select","label": "minimax 思考强度(带工具)",            "default": "high",
                                            "options": ["low", "medium", "high"], "category": "llm"},
    "minimax_reasoning_effort_no_tools":   {"type": "select","label": "minimax 思考强度(无工具)",            "default": "high",
                                            "options": ["low", "medium", "high"], "category": "llm"},
}


@app.get("/api/settings")
async def api_get_settings():
    """读取当前运行时参数"""
    current = {}
    if _RUNTIME_SETTINGS_FILE.exists():
        try:
            current = json.loads(_RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 合并：以 schema 为准，已保存的值覆盖默认值
    result = {}
    for key, meta in _SETTINGS_SCHEMA.items():
        result[key] = {
            "value": current.get(key, meta["default"]),
            "type": meta["type"],
            "label": meta["label"],
            "category": meta["category"],
            "default": meta["default"],
        }
        if "min" in meta:
            result[key]["min"] = meta["min"]
        if "max" in meta:
            result[key]["max"] = meta["max"]
    return {"ok": True, "settings": result}

# ── LLM Provider 双轨状态（2026-06-28 重构） ──

@app.get("/api/llm-providers")
async def api_llm_providers():
    """返回主对话 + 图片描述 两个 provider 的实时状态（供 dashboard 展示）

    字段:
      chat_provider: 主对话 provider (active_llm_provider)
        - name: 'deepseek' 或 'minimax'
        - model: 模型名
        - available: 是否配置可用
      vision_provider: 图片描述 provider (永远走 minimax)
        - name: 'minimax'
        - model: 模型名
        - available: minimax 是否配置可用
      routing_strategy: "vision_then_chat"
    """
    # dashboard 进程独立,不能 import zyw_chat.config(会触发 get_driver)
    # 直接读 .env 文件 + runtime_settings.json
    import os as _os
    from pathlib import Path as _Path

    _qqbot_env = _Path(__file__).resolve().parent.parent / ".env"
    _private_env = _Path(__file__).resolve().parents[2] / "private" / ".env"
    _runtime_settings = _Path(__file__).resolve().parent.parent / "data" / "runtime_settings.json"
    # 优先 private/.env (用户真实配置),然后 qqbot/.env
    _env_vars: dict = {}
    for _env_path in [_private_env, _qqbot_env]:
        if not _env_path.exists():
            continue
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            _env_vars[k.strip()] = v.strip()
        break  # 第一个存在的用完即止
    _runtime: dict = {}
    if _runtime_settings.exists():
        try:
            _runtime = json.loads(_runtime_settings.read_text(encoding="utf-8"))
        except Exception:
            pass

    class zyw_cfg:  # 替身,只在 dashboard 用
        ACTIVE_LLM_PROVIDER = _runtime.get("active_llm_provider", "deepseek")
        MINIMAX_ENABLED = _env_vars.get("MINIMAX_ENABLED", "false").lower() == "true"
        MINIMAX_API_KEY = _env_vars.get("MINIMAX_API_KEY", "")
        MINIMAX_BASE_URL = _env_vars.get("MINIMAX_BASE_URL", "")
        MINIMAX_MODEL = _env_vars.get("MINIMAX_MODEL", "MiniMax-M3")
        DEEPSEEK_API_KEY = _env_vars.get("DEEPSEEK_API_KEY", "")
        DEEPSEEK_MODEL = _env_vars.get("DEEPSEEK_MODEL", "deepseek-v4-pro")

    active = getattr(zyw_cfg, "ACTIVE_LLM_PROVIDER", "deepseek")
    if active == "minimax":
        chat_available = bool(zyw_cfg.MINIMAX_ENABLED and zyw_cfg.MINIMAX_API_KEY
                              and not zyw_cfg.MINIMAX_API_KEY.startswith("sk-your"))
        chat_model = zyw_cfg.MINIMAX_MODEL
    else:
        chat_available = bool(zyw_cfg.DEEPSEEK_API_KEY
                              and not zyw_cfg.DEEPSEEK_API_KEY.startswith("sk-your"))
        chat_model = zyw_cfg.DEEPSEEK_MODEL

    vision_available = bool(zyw_cfg.MINIMAX_ENABLED and zyw_cfg.MINIMAX_API_KEY
                            and zyw_cfg.MINIMAX_BASE_URL
                            and not zyw_cfg.MINIMAX_API_KEY.startswith("sk-your")
                            and not zyw_cfg.MINIMAX_API_KEY.startswith("sk-cp-your"))
    vision_model = zyw_cfg.MINIMAX_MODEL if vision_available else None

    return {
        "ok": True,
        "routing_strategy": "vision_then_chat",
        "description": (
            "含图消息: minimax vision 转文字描述 -> deepseek 处理对话\n"
            "无图消息: 直接 deepseek 处理\n"
            "minimax 永远只做图片描述,不接触对话上下文"
        ),
        "chat_provider": {
            "name": active,
            "model": chat_model,
            "available": chat_available,
            "role": "主对话 / 工具 / 流式 / 用户画像",
        },
        "vision_provider": {
            "name": "minimax",
            "model": vision_model,
            "available": vision_available,
            "role": "图片描述 (只读图,不参与对话)",
            "warning": None if vision_available else "minimax 不可用,含图消息会触发 deepseek image_url 400",
        },
    }



@app.put("/api/settings")
async def api_update_settings(request: Request):
    """更新运行时参数并触发 bot 热加载"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "msg": "无效的 JSON 请求"}
    if not isinstance(body, dict):
        return {"ok": False, "msg": "请求体必须为 JSON 对象"}
    # 校验 + 类型转换
    validated = {}
    for key, raw_val in body.items():
        if key not in _SETTINGS_SCHEMA:
            continue
        meta = _SETTINGS_SCHEMA[key]
        try:
            if meta["type"] == "int":
                val = int(raw_val)
                if "min" in meta:
                    val = max(meta["min"], val)
                if "max" in meta:
                    val = min(meta["max"], val)
            elif meta["type"] == "float":
                val = float(raw_val)
                if "min" in meta:
                    val = max(meta["min"], val)
                if "max" in meta:
                    val = min(meta["max"], val)
            elif meta["type"] == "bool":
                val = bool(raw_val) if not isinstance(raw_val, str) else raw_val.lower() in ("true", "1", "yes")
            else:
                val = raw_val
            validated[key] = val
        except (ValueError, TypeError) as e:
            return {"ok": False, "msg": f"参数 '{key}' 类型错误: {e}"}
    # 写入配置文件（先读旧值合并，避免丢失前端未发送的 key）
    try:
        _RUNTIME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if _RUNTIME_SETTINGS_FILE.exists():
            try:
                existing = json.loads(_RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(validated)
        _RUNTIME_SETTINGS_FILE.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except PermissionError:
        return {"ok": False, "msg": "写入配置失败: 权限不足，请检查文件权限"}
    except OSError as e:
        return {"ok": False, "msg": f"写入配置失败: 系统错误 - {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"写入配置失败: {type(e).__name__} - {e}"}
    # 触发 bot 热加载
    _SETTINGS_RELOAD_TRIGGER.write_text(f"settings updated at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    changed = ", ".join(validated.keys())
    logger.info(f"Settings updated: {changed}")
    return {"ok": True, "msg": f"已保存 {len(validated)} 项参数，Bot 将在下一条消息时生效"}


# ── 日志查看 ──
LOG_DIR = PROJECT_ROOT / "qqbot" / "logs"

@app.get("/api/logs")
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


@app.get("/api/logs/{filename}")
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


# ── WebSocket 配置 ──
WS_PUSH_INTERVAL = int(os.getenv("WS_PUSH_INTERVAL", "5"))  # WebSocket 推送间隔（秒）


# ── WebSocket 实时状态（带错误恢复）──
@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    # Token 认证（WebSocket 不走 HTTP 中间件，需单独校验）
    if _AUTH_ENABLED:
        token = websocket.query_params.get("token", "")
        if token != DASHBOARD_TOKEN:
            await websocket.close(code=4001, reason="Unauthorized")
            return
    await websocket.accept()
    try:
        while True:
            try:
                status = await asyncio.get_event_loop().run_in_executor(None, monitor.get_all_status)
                # 附加看门狗状态
                status["watchdog"] = monitor.get_watchdog_status()
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
