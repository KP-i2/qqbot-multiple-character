"""FastAPI Dashboard 主应用（修复版）"""
import asyncio, html, json, logging, os, re, secrets, tempfile, time
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
    yield
    logger.info("Dashboard shutting down...")
    monitor.stop_watchdog()
    logger.info("Process watchdog stopped")


app = FastAPI(title="QQ Bot Dashboard", lifespan=_lifespan)
STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).parent.parent.parent
COOKIES_FILE = PROJECT_ROOT / "cookies.json"

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
    # 放行：首页、静态文件、健康检查、CORS 预检
    if path == "/" or path.startswith("/static/") or path == "/api/health" or request.method == "OPTIONS":
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


def verify_token(request: Request):
    """简单 token 认证：通过 header 或 query 参数（供外部调用）"""
    token = request.headers.get("X-Dashboard-Token") or request.query_params.get("token")
    if token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── 前端 ──
@app.get("/", response_class=HTMLResponse)
async def index():
    global _INDEX_HTML
    if _INDEX_HTML is None:
        _INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # 注入 auth token 供前端使用（转义防止 XSS）
    safe_token = html.escape(DASHBOARD_TOKEN, quote=True)
    token_script = f'<script>window._DASHBOARD_TOKEN="{safe_token}";</script>'
    html = _INDEX_HTML.replace("</head>", token_script + "</head>", 1)
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


# ── 进程监控 ──
@app.get("/api/status")
async def api_status():
    return await asyncio.get_event_loop().run_in_executor(None, monitor.get_all_status)


@app.post("/api/process/start/{name}")
async def api_start_process(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    if name == "nonebot2":
        result = await asyncio.get_event_loop().run_in_executor(None, monitor.start_nonebot2)
        if result.get("ok"):
            logger.info("NoneBot2 process started")
        return result
    return {"ok": False, "msg": f"Unknown process: {name} (NapCat 由 NapCatQQ Desktop 外部管理)"}


@app.post("/api/process/stop/{name}")
async def api_stop_process(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    if name == "nonebot2":
        result = await asyncio.get_event_loop().run_in_executor(None, monitor.stop_nonebot2)
        if result.get("ok"):
            logger.info("NoneBot2 process stopped")
        return result
    return {"ok": False, "msg": f"Unknown process: {name} (NapCat 由 NapCatQQ Desktop 外部管理)"}


@app.post("/api/process/full-restart")
async def api_full_restart():
    return await monitor.full_restart()


@app.get("/api/health")
async def api_health():
    """系统健康检查（NapCatQQ Desktop 外部管理，检测进程+WS连接状态）"""
    loop = asyncio.get_event_loop()
    napcat = await loop.run_in_executor(None, monitor.get_napcat_status)
    nb = await loop.run_in_executor(None, monitor.get_nonebot2_status)
    watchdog = monitor.get_watchdog_status()

    bot_running = nb["running"]
    desktop_running = napcat["running"]
    qq_running = napcat["qq_running"]
    ws_connected = napcat.get("ws_connected", False)

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
        "napcat": desktop_running,
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


# ── Cookie 管理 ──
@app.get("/api/cookies")
async def api_cookie_status():
    return weibo_fetcher.get_cookie_status()


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
    return weibo_fetcher.list_corpora()


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
        result = weibo_fetcher.extract_qq_messages(tmp_path, qq_uid, dir_uid, name)
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
        return weibo_fetcher.list_senders(tmp_path)
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
    filename = file.filename or "import.txt"
    result = weibo_fetcher.import_text_file(content, filename, uid, name)
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
    result = weibo_fetcher.generate_skill_from_corpus(uid, skill_name, display_name, description, version, character)
    if result.get("ok"):
        trigger = PROJECT_ROOT / ".reload_skills_trigger"
        trigger.write_text(f"generate from dashboard at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Skill generated from corpus: {skill_name} (uid={uid}, character={character})")
    return result


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


@app.post("/api/skills/{name}/retrain")
async def api_retrain_skill(name: str, character: str = "celebrity", research_profile: str = "budget-friendly"):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    # 并发保护：同一 skill 不允许同时蒸馏
    if name in _running_retrains:
        return {"ok": False, "msg": f"蒸馏正在进行中，请等待完成后再试"}
    _running_retrains.add(name)
    asyncio.create_task(_run_retrain(name, character, research_profile))
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
    body = await request.json()
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
    body = await request.json()
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
    result = skill_manager.delete_skill(name)
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
    "stream_max_flush_size": {"type": "int",   "label": "单段最大字符",            "default": 300,   "category": "stream", "min": 50, "max": 1000},
    "max_history_rounds":    {"type": "int",   "label": "最大对话轮数",            "default": 40,    "category": "history", "min": 1, "max": 100},
    "history_ttl_hours":     {"type": "int",   "label": "历史过期时间 (小时)",     "default": 6,     "category": "history", "min": 1, "max": 168},
    "history_save_interval": {"type": "int",   "label": "保存间隔 (秒)",           "default": 60,    "category": "history", "min": 10, "max": 600},
    "thinking_timer_seconds":{"type": "int",   "label": "等待提示秒数",            "default": 5,     "category": "timing", "min": 1, "max": 30},
    "multi_turn_enabled":    {"type": "bool",  "label": "多轮对话",                "default": True,  "category": "bot"},
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


@app.put("/api/settings")
async def api_update_settings(request: Request):
    """更新运行时参数并触发 bot 热加载"""
    body = await request.json()
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
    await websocket.accept()
    try:
        while True:
            try:
                status = monitor.get_all_status()
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
