"""
Cookie + 语料采集 + 推特抓取 + 语料蒸馏
（2026-08-30 拆分自 main.py）
"""
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile

from .. import weibo_fetcher
from ._shared import (
    COOKIES_FILE, PROJECT_ROOT, TWITTER_COOKIES_FILE, TWITTER_CORPUS_DIR,
    TWITTER_SCRIPT, _check_upload_size, _safe_name, _safe_path_component, logger,
)

router = APIRouter()


# ── Cookie 管理 ──
@router.get("/api/cookies")
async def api_cookie_status():
    return await asyncio.get_event_loop().run_in_executor(None, weibo_fetcher.get_cookie_status)


@router.post("/api/cookies/upload")
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
@router.get("/api/corpus")
async def api_list_corpora():
    return await asyncio.get_event_loop().run_in_executor(None, weibo_fetcher.list_corpora)


@router.get("/api/corpus/{uid}")
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


@router.post("/api/corpus/fetch")
async def api_fetch_weibo(uid: str = Form(...)):
    if not _safe_path_component(uid):
        return {"ok": False, "msg": "Invalid UID"}
    result = await weibo_fetcher.fetch_weibo(uid)
    if result.get("ok"):
        logger.info(f"Weibo corpus fetched: uid={uid}")
    return result


@router.post("/api/corpus/qq-import")
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


@router.post("/api/corpus/qq-senders")
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


@router.post("/api/corpus/import-text")
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


@router.post("/api/corpus/generate-skill")
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
    # 2026-08-30: dashboard 由 botenv python 启动,直接用它执行抓取脚本
    # （旧代码残留 skill_qqbot/Scripts/python.exe 路径，venv 迁移后已不存在）
    py = sys.executable

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


@router.get("/api/twitter/cookie")
async def api_twitter_cookie_status():
    return await asyncio.get_event_loop().run_in_executor(None, _get_twitter_cookie_status)


@router.post("/api/twitter/cookie/upload")
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


@router.get("/api/twitter/corpora")
async def api_twitter_corpora():
    return await asyncio.get_event_loop().run_in_executor(None, _list_twitter_corpora)


@router.post("/api/twitter/fetch")
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
