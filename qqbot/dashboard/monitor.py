"""进程检测与系统监控（修复版：async-friendly + 看门狗 + 日志捕获）"""
import os, time, asyncio, subprocess, socket, logging, shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
import psutil

logger = logging.getLogger("dashboard.monitor")

PROJECT_ROOT = Path(__file__).parent.parent.parent  # skill_communication/
QQBOT_DIR = PROJECT_ROOT / "qqbot"
VENV_PYTHON = PROJECT_ROOT / "skill_qqbot" / "Scripts" / "python.exe"
BOT_SCRIPT = QQBOT_DIR / "bot.py"
LOG_DIR = QQBOT_DIR / "logs"
NAPCAT_DESKTOP_EXE = "NapCatQQ-Desktop.exe"  # NapCatQQ Desktop 进程名（外部管理）

# ── 可配置常量 ──
NONEBOT_PORT = int(os.getenv("NONEBOT_PORT", "8080"))
WATCHDOG_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "30"))
STATUS_CACHE_TTL = int(os.getenv("STATUS_CACHE_TTL", "5"))

# 确保日志目录存在
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── 日志轮转常量 ──
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_LOG_BACKUP_COUNT = 3


def _rotate_log(log_path: Path):
    """启动前轮转日志文件（保留 3 份备份）"""
    if not log_path.exists() or log_path.stat().st_size < _LOG_MAX_BYTES:
        return
    for i in range(_LOG_BACKUP_COUNT, 1, -1):
        src = log_path.with_suffix(f".{i-1}")
        dst = log_path.with_suffix(f".{i}")
        if src.exists():
            shutil.move(str(src), str(dst))
    backup = log_path.with_suffix(".1")
    shutil.move(str(log_path), str(backup))
    logger.info(f"Rotated {log_path.name} → {backup.name}")

# ── 进程状态缓存（避免高频 process_iter 调用）──
_status_cache: dict = {}
_status_cache_time: float = 0
_CACHE_TTL = STATUS_CACHE_TTL  # 使用可配置的缓存TTL


def check_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_process_by_name(name: str) -> list[dict]:
    results = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time", "memory_info"]):
        try:
            if name.lower() in (proc.info["name"] or "").lower():
                mem = proc.info["memory_info"]
                results.append({
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "memory_mb": round(mem.rss / 1024 / 1024, 1) if mem else 0,
                    "uptime": _format_uptime(proc.info["create_time"]),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return results


def _format_uptime(create_time) -> str:
    if not create_time:
        return "unknown"
    delta = time.time() - create_time
    hours = int(delta // 3600)
    minutes = int((delta % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def get_nonebot2_status() -> dict:
    port_open = check_port_open(NONEBOT_PORT)
    procs = find_process_by_name("python")
    bot_procs = []
    for p in procs:
        try:
            cmdline = " ".join(psutil.Process(p["pid"]).cmdline() or [])
            if "bot.py" in cmdline:
                bot_procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {
        "running": port_open and len(bot_procs) > 0,
        "port": NONEBOT_PORT,
        "port_open": port_open,
        "processes": bot_procs,
    }


def get_napcat_status() -> dict:
    """检测 NapCatQQ Desktop 进程状态（外部管理，只读检测，不控制）"""
    # 检测 NapCatQQ Desktop 进程
    desktop_procs = []
    for proc in psutil.process_iter(["pid", "name", "create_time", "memory_info"]):
        try:
            pname = (proc.info["name"] or "").lower()
            if "napcatqq" in pname or "napcat-desktop" in pname or "napcatqq-desktop" in pname:
                mem = proc.info["memory_info"]
                desktop_procs.append({
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "memory_mb": round(mem.rss / 1024 / 1024, 1) if mem else 0,
                    "uptime": _format_uptime(proc.info["create_time"]),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # 检测 QQ 进程（排除 QQMusic 等）
    qq_procs = [p for p in find_process_by_name("QQ")
                if (p["name"] or "").lower().startswith("qq")
                and "music" not in (p["name"] or "").lower()]
    # 检测 WebSocket 连接状态
    ws_connected = check_ws_connected(NONEBOT_PORT)
    return {
        "running": len(desktop_procs) > 0,
        "processes": desktop_procs,
        "qq_running": len(qq_procs) > 0,
        "qq_processes": qq_procs,
        "ws_connected": ws_connected,
        "managed_externally": True,
    }


def start_nonebot2() -> dict:
    if get_nonebot2_status()["running"]:
        return {"ok": False, "msg": "NoneBot2 already running"}
    try:
        log_file = LOG_DIR / "nonebot2.log"
        _rotate_log(log_file)
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(f"\n--- NoneBot2 started at {datetime.now().isoformat()} ---\n")
            subprocess.Popen(
                [str(VENV_PYTHON), "-X", "utf8", str(BOT_SCRIPT)],
                cwd=str(QQBOT_DIR),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                stdout=lf,
                stderr=subprocess.STDOUT,
            )
        logger.info(f"NoneBot2 started, log: {log_file}")
        return {"ok": True, "msg": "NoneBot2 starting..."}
    except Exception as e:
        logger.error(f"Failed to start NoneBot2: {e}")
        return {"ok": False, "msg": f"Failed to start NoneBot2: {e}"}


def stop_nonebot2() -> dict:
    stopped = 0
    procs = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(p.cmdline() or [])
            if "bot.py" in cmdline and "python" in (p.name() or "").lower():
                p.terminate()
                procs.append(p)
                stopped += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # 等待进程真正退出（最多5秒）
    for p in procs:
        try:
            p.wait(timeout=5)
        except psutil.TimeoutExpired:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"ok": stopped > 0, "msg": f"Stopped {stopped} process(es)"}


def get_all_status() -> dict:
    """获取所有状态（带缓存，避免高频 process_iter）"""
    global _status_cache, _status_cache_time
    now = time.time()
    if now - _status_cache_time < _CACHE_TTL and _status_cache:
        return _status_cache
    result = {
        "nonebot2": get_nonebot2_status(),
        "napcat": get_napcat_status(),
        "timestamp": datetime.now().isoformat(),
    }
    _status_cache = result
    _status_cache_time = now
    return result


def check_ws_connected(port: int = NONEBOT_PORT) -> bool:
    """检查是否有 WebSocket 客户端连接到 NoneBot2 端口"""
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr and conn.laddr.port == port and conn.status == 'ESTABLISHED':
                return True
    except (psutil.AccessDenied, OSError):
        pass
    return False


async def full_restart() -> dict:
    """重启 NoneBot2（NapCatQQ Desktop 由外部管理，不干预）"""
    global _status_cache_time
    _status_cache_time = 0

    # 停止 NoneBot2
    stop_nonebot2()
    await asyncio.sleep(2)

    # 启动 NoneBot2，等待端口就绪
    nb_result = start_nonebot2()
    if not nb_result.get("ok"):
        return {"ok": False, "msg": f"NoneBot2 启动失败: {nb_result['msg']}"}

    # 等待 NoneBot2 端口打开（最多15秒）
    port_ready = False
    for _ in range(15):
        if check_port_open(NONEBOT_PORT):
            port_ready = True
            break
        await asyncio.sleep(1)

    if not port_ready:
        return {"ok": False, "msg": f"NoneBot2 启动失败 (端口 {NONEBOT_PORT} 未就绪)"}

    return {
        "ok": True,
        "msg": f"NoneBot2 已重启，端口 {NONEBOT_PORT} 就绪。\nNapCatQQ Desktop 由外部管理，请确认其已运行。"
    }


# ── 进程看门狗 ──
_watchdog_task: Optional[asyncio.Task] = None
_watchdog_running = False


async def _watchdog_loop(interval: int = 30):
    """进程看门狗 v2：监控 NoneBot2 + WebSocket 连接健康
    
    策略：
    1. NoneBot2 崩溃 → 自动重启
    2. WebSocket 断开（NapCat 未连接）→ 日志告警
    3. NapCatQQ Desktop 由外部管理，不干预其进程
    """
    global _watchdog_running
    _watchdog_running = True
    logger.info(f"[watchdog v2] Started (interval={interval}s)")

    # 启动后等 30 秒再开始检查
    await asyncio.sleep(30)

    _ws_warn_count = 0  # WebSocket 断开连续告警计数
    _WS_WARN_THRESHOLD = 3  # 连续 3 次断开才告警（避免启动期间误报）

    while _watchdog_running:
        try:
            nb_status = await asyncio.get_event_loop().run_in_executor(None, get_nonebot2_status)

            # NoneBot2 挂了 → 自动重启
            if not nb_status["running"]:
                logger.warning("[watchdog] NoneBot2 is down, auto-restarting...")
                result = await asyncio.get_event_loop().run_in_executor(None, start_nonebot2)
                logger.info(f"[watchdog] NoneBot2 restart: {result['msg']}")
                _ws_warn_count = 0  # 重置 WS 告警计数
                # 等端口就绪
                for _ in range(15):
                    if await asyncio.get_event_loop().run_in_executor(None, check_port_open, NONEBOT_PORT):
                        break
                    await asyncio.sleep(1)
            else:
                # NoneBot2 运行时，检查 WebSocket 连接
                ws_ok = await asyncio.get_event_loop().run_in_executor(None, check_ws_connected, NONEBOT_PORT)
                if ws_ok:
                    _ws_warn_count = 0
                else:
                    _ws_warn_count += 1
                    if _ws_warn_count == _WS_WARN_THRESHOLD:
                        logger.warning(
                            f"[watchdog] No WebSocket client connected to port {NONEBOT_PORT} "
                            f"(checked {_WS_WARN_THRESHOLD} times). "
                            "Please ensure NapCatQQ Desktop is running with correct WS config."
                        )
                    elif _ws_warn_count > _WS_WARN_THRESHOLD and _ws_warn_count % 10 == 0:
                        # 每 10 次（约5分钟）提醒一次
                        logger.warning(
                            f"[watchdog] WebSocket still disconnected (count={_ws_warn_count})"
                        )

        except Exception as e:
            logger.error(f"[watchdog] Error during check: {e}")

        await asyncio.sleep(interval)


def start_watchdog(interval: int = 30) -> Optional[asyncio.Task]:
    """启动进程看门狗"""
    global _watchdog_task
    if _watchdog_task and not _watchdog_task.done():
        logger.warning("[watchdog] Already running")
        return _watchdog_task
    _watchdog_task = asyncio.create_task(_watchdog_loop(interval))
    return _watchdog_task


def stop_watchdog():
    """停止进程看门狗"""
    global _watchdog_running, _watchdog_task
    _watchdog_running = False
    if _watchdog_task and not _watchdog_task.done():
        _watchdog_task.cancel()
        _watchdog_task = None
    logger.info("[watchdog] Stopped")


def get_watchdog_status() -> dict:
    return {
        "running": _watchdog_running and _watchdog_task is not None and not _watchdog_task.done(),
    }
