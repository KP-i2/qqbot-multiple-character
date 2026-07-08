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
SNOWLUMA_DIR = PROJECT_ROOT / "snowluma"  # snowluma 安装目录
SNOWLUMA_NODE_EXE: Optional[Path] = None  # 解析后填入: snowluma/SnowLuma-*/node.exe
# 旧 NapCat 兼容：保留常量名但实际指向 snowluma，避免大改
LEGACY_PROTOCOL_EXE = "NapCatQQ-Desktop.exe"  # 仅用于日志注释


def _resolve_snowluma_node() -> Optional[Path]:
    """解析 snowluma 安装路径: 找 snowluma/SnowLuma-*/node.exe

    支持多个版本目录（按字典序第一个）。
    返回 None 表示未安装。
    """
    global SNOWLUMA_NODE_EXE
    if SNOWLUMA_NODE_EXE is not None and SNOWLUMA_NODE_EXE.exists():
        return SNOWLUMA_NODE_EXE
    if not SNOWLUMA_DIR.exists():
        return None
    for sub in sorted(SNOWLUMA_DIR.iterdir()):
        if sub.is_dir() and sub.name.startswith("SnowLuma-"):
            candidate = sub / "node.exe"
            if candidate.exists():
                SNOWLUMA_NODE_EXE = candidate
                return candidate
    return None

# ── 可配置常量 ──
NONEBOT_PORT = int(os.getenv("NONEBOT_PORT", "8080"))
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8501"))
WATCHDOG_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "30"))
STATUS_CACHE_TTL = int(os.getenv("STATUS_CACHE_TTL", "5"))
_STOP_GRACE = 5  # 优雅退出等待（秒）

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


def invalidate_status_cache():
    """手动刷新状态缓存（start/stop 后立即生效）"""
    global _status_cache_time
    _status_cache_time = 0


def check_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def clean_stale_port(port: int, exclude_pid: int = 0) -> int:
    """清理占用指定端口的旧进程，返回清理数量（排除自身 PID）"""
    killed = 0
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status in (
                "LISTEN", "ESTABLISHED", "FIN_WAIT_1", "FIN_WAIT_2"
            ):
                pid = conn.pid
                if pid and pid > 0 and pid != exclude_pid and pid != os.getpid():
                    try:
                        p = psutil.Process(pid)
                        p.terminate()
                        try:
                            p.wait(timeout=_STOP_GRACE)
                        except psutil.TimeoutExpired:
                            p.kill()
                            p.wait(timeout=3)
                        killed += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
    except (psutil.AccessDenied, OSError) as e:
        logger.warning(f"clean_stale_port({port}): {e}")
    if killed:
        time.sleep(2)  # 等待端口释放
    return killed


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
    """检测 snowluma 进程状态（外部管理，只读检测，不控制）
    注意：函数名保留 get_napcat_status 以兼容 /api/status 调用方；
    实际检测的是 snowluma（node.exe + 路径匹配）。
    """
    # 检测 snowluma 进程：node.exe 且 路径在 snowluma/ 下
    snowluma_procs = []
    for proc in psutil.process_iter(["pid", "name", "create_time", "memory_info"]):
        try:
            pinfo = proc.info
            pname = (pinfo["name"] or "").lower()
            if pname != "node.exe":
                continue
            p = psutil.Process(pinfo["pid"])
            exe_path = (p.exe() or "").lower()
            if "snowluma" in exe_path or "\\snowluma\\" in exe_path:
                mem = pinfo["memory_info"]
                snowluma_procs.append({
                    "pid": pinfo["pid"],
                    "name": "snowluma",
                    "memory_mb": round(mem.rss / 1024 / 1024, 1) if mem else 0,
                    "uptime": _format_uptime(pinfo["create_time"]),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # 检测 QQ 进程（排除 QQMusic 等）
    qq_procs = [p for p in find_process_by_name("QQ")
                if (p["name"] or "").lower().startswith("qq")
                and "music" not in (p["name"] or "").lower()]
    # 检测 WebSocket 连接状态（用 psutil 数 ESTABLISHED：NoneBot2 v11 reverse ws 只接受
    # 已有 client 的连接，发新 ws Upgrade 探测会被拒；看有没有连接即可）
    ws_connected = check_ws_connected(NONEBOT_PORT)
    return {
        "running": len(snowluma_procs) > 0,
        "processes": snowluma_procs,
        "qq_running": len(qq_procs) > 0,
        "qq_processes": qq_procs,
        "ws_connected": ws_connected,
        "managed_externally": False,
    }


def start_nonebot2() -> dict:
    if get_nonebot2_status()["running"]:
        return {"ok": False, "msg": "NoneBot2 already running"}
    # 启动前清理端口残留进程（解决旧进程端口冲突）
    cleared = clean_stale_port(NONEBOT_PORT)
    if cleared:
        logger.info(f"Cleaned {cleared} stale process(es) on port {NONEBOT_PORT}")
    try:
        log_file = LOG_DIR / "nonebot2.log"
        _rotate_log(log_file)
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(f"\n--- NoneBot2 started at {datetime.now().isoformat()} ---\n")
            subprocess.Popen(
                [str(VENV_PYTHON), "-X", "utf8", str(BOT_SCRIPT)],
                cwd=str(QQBOT_DIR),
                # CREATE_NO_WINDOW: 关掉控制台宿主接管, 防止 WindowsTerminal tab hijack
                # CREATE_NEW_PROCESS_GROUP: 让 CTRL_BREAK_EVENT 能干净地 stop_nonebot2
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW,
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
    # 端口兜底清理（防止残留）
    time.sleep(1)
    cleared = clean_stale_port(NONEBOT_PORT)
    if cleared:
        stopped += cleared
        logger.info(f"stop_nonebot2: cleaned {cleared} stale process(es) on port {NONEBOT_PORT}")
    return {"ok": stopped > 0, "msg": f"Stopped {stopped} process(es)"}


def start_snowluma() -> dict:
    """启动 snowluma (node.exe index.mjs, 后台无窗口)

    与 start_nonebot2 同款: CREATE_NO_WINDOW 防止 WindowsTerminal hijack
    """
    status = get_napcat_status()
    if status["running"]:
        return {"ok": False, "msg": "snowluma already running"}
    node_exe = _resolve_snowluma_node()
    if node_exe is None:
        return {"ok": False, "msg": f"snowluma 未找到: {SNOWLUMA_DIR}/SnowLuma-*/node.exe"}
    snowluma_dir = node_exe.parent
    index_mjs = snowluma_dir / "index.mjs"
    if not index_mjs.exists():
        return {"ok": False, "msg": f"snowluma 主程序缺失: {index_mjs}"}
    try:
        log_file = LOG_DIR / "snowluma.log"
        # 启动前清理>5MB, 滚动成 .1
        _rotate_log(log_file)
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(f"\n--- snowluma started at {datetime.now().isoformat()} ---\n")
            subprocess.Popen(
                [str(node_exe), "./index.mjs"],
                cwd=str(snowluma_dir),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW,
                stdout=lf,
                stderr=subprocess.STDOUT,
            )
        logger.info(f"snowluma started, log: {log_file}")
        return {"ok": True, "msg": "snowluma starting..."}
    except Exception as e:
        logger.error(f"Failed to start snowluma: {e}")
        return {"ok": False, "msg": f"Failed to start snowluma: {e}"}


def stop_snowluma() -> dict:
    """停止 snowluma 进程 (按路径匹配)"""
    stopped = 0
    procs = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            pname = (proc.info["name"] or "").lower()
            if pname != "node.exe":
                continue
            exe = (proc.info["exe"] or "").lower()
            if "snowluma" not in exe:
                continue
            psutil.Process(proc.info["pid"]).terminate()
            procs.append(proc.info["pid"])
            stopped += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # 等待退出（最多 5s）
    for pid in procs:
        try:
            psutil.Process(pid).wait(timeout=5)
        except psutil.TimeoutExpired:
            try:
                psutil.Process(pid).kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"ok": stopped > 0, "msg": f"Stopped {stopped} snowluma process(es)"}


def get_all_status() -> dict:
    """获取所有状态（带缓存，避免高频 process_iter）"""
    global _status_cache, _status_cache_time
    now = time.time()
    if now - _status_cache_time < _CACHE_TTL and _status_cache:
        return _status_cache
    result = {
        "nonebot2": get_nonebot2_status(),
        "snowluma": get_napcat_status(),   # 2026-06-27 字段名统一:snowluma
        "napcat": get_napcat_status(),     # 兼容旧前端
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


# ── Dashboard 主动 WebSocket 探活（无需装 websockets 库） ──
# 用 socket 发 ws Upgrade 握手请求，看 NoneBot2 是否真的能应答 ws 协议
_ws_health: dict = {
    "tcp_alive": False,        # TCP 连得上 8080
    "ws_upgrade_ok": False,    # ws Upgrade 握手成功（ws 协议层活）
    "last_check_at": 0.0,      # 上次检查时间戳
    "last_ok_at": 0.0,         # 上次 ws Upgrade 成功时间
    "last_disconnect_at": 0.0, # 上次 ws Upgrade 失败时间
    "consecutive_fails": 0,    # 连续失败次数
    "last_error": "",          # 最后一次错误信息
}


def get_ws_health() -> dict:
    """获取当前 ws 健康状态（前端直接读）"""
    return dict(_ws_health)


def _probe_ws_once(host: str = "127.0.0.1", port: int = NONEBOT_PORT, timeout: float = 1.5) -> tuple[bool, bool, str]:
    """主动探测 ws 健康: 返回 (tcp_alive, ws_upgrade_ok, error_msg)

    设计说明（snowluma / NoneBot2 v11 reverse ws 适配）：
    - tcp_alive: 能不能 TCP 连上 port（bot 在 listen）
    - ws_upgrade_ok: NoneBot2 v11 adapter 默认是 reverse-ws-server 模式，
      只接受已建立好的 ws client 连接；自己发 ws Upgrade 会被拒
      （要么返回 401/426，要么握手挂起）。所以正确判断方式是
      看 8080 端口有没有 ESTABLISHED 的反向连接 —— 有一条就说明
      snowluma 已经握手成功。
    """
    # 1) TCP 层：能不能连上 port（说明 bot 进程在 listen）
    tcp_alive = check_port_open(port)
    if not tcp_alive:
        return False, False, f"port {port} not listening"
    # 2) WS 层：看 8080 端口有没有 ESTABLISHED 状态的连接
    #    NoneBot2 v11 reverse ws 只有成功握手后才会有 ESTABLISHED
    try:
        for conn in psutil.net_connections(kind="inet"):
            if (conn.laddr and conn.laddr.port == port
                and conn.status == "ESTABLISHED"
                and conn.pid and conn.pid > 0):
                # 找到一条握手成功的连接
                return True, True, ""
    except (psutil.AccessDenied, OSError) as e:
        return True, False, f"psutil: {e}"
    return True, False, "no ESTABLISHED connection on ws port"


async def _ws_health_loop(interval: float = 3.0):
    """后台循环探活 ws 状态，更新 _ws_health"""
    logger.info(f"[ws-health] Probe loop started (interval={interval}s)")
    while True:
        loop = asyncio.get_event_loop()
        # 跑在 executor 里避免阻塞 event loop
        tcp_alive, ws_upgrade_ok, err = await loop.run_in_executor(None, _probe_ws_once)
        now = time.time()
        _ws_health["tcp_alive"] = tcp_alive
        _ws_health["ws_upgrade_ok"] = ws_upgrade_ok
        _ws_health["last_check_at"] = now
        if ws_upgrade_ok:
            _ws_health["last_ok_at"] = now
            _ws_health["consecutive_fails"] = 0
            _ws_health["last_error"] = ""
        else:
            _ws_health["consecutive_fails"] += 1
            if _ws_health["last_disconnect_at"] == 0.0:
                _ws_health["last_disconnect_at"] = now
            _ws_health["last_error"] = err
        await asyncio.sleep(interval)


def start_ws_health_probe(interval: float = 3.0) -> asyncio.Task:
    """启动 ws 探活后台任务（idempotent）"""
    global _ws_health_task
    if _ws_health_task and not _ws_health_task.done():
        return _ws_health_task
    _ws_health_task = asyncio.create_task(_ws_health_loop(interval))
    return _ws_health_task


async def full_restart() -> dict:
    """重启 NoneBot2 + snowluma（两者 dashboard 统一管理）"""
    global _status_cache_time
    async with _restart_lock:
        _status_cache_time = 0

        # 1) 停止 NoneBot2
        await asyncio.get_event_loop().run_in_executor(None, stop_nonebot2)
        await asyncio.sleep(2)

        # 2) 停止 snowluma (顺序: 先 bot 再 snowluma, 避免 WS 反向连入被打断)
        snow_stop = await asyncio.get_event_loop().run_in_executor(None, stop_snowluma)
        logger.info(f"full_restart: snowluma stop -> {snow_stop['msg']}")
        await asyncio.sleep(2)

        # 3) 启动 NoneBot2，等待端口就绪
        nb_result = await asyncio.get_event_loop().run_in_executor(None, start_nonebot2)
        if not nb_result.get("ok"):
            return {"ok": False, "msg": f"NoneBot2 启动失败: {nb_result['msg']}"}

        port_ready = False
        for _ in range(15):
            if await asyncio.get_event_loop().run_in_executor(None, check_port_open, NONEBOT_PORT):
                port_ready = True
                break
            await asyncio.sleep(1)
        if not port_ready:
            return {"ok": False, "msg": f"NoneBot2 启动失败 (端口 {NONEBOT_PORT} 未就绪)"}

        # 4) 启动 snowluma
        snow_start = await asyncio.get_event_loop().run_in_executor(None, start_snowluma)
        if not snow_start.get("ok"):
            return {"ok": False, "msg": f"snowluma 启动失败: {snow_start['msg']}"}

    return {
        "ok": True,
        "msg": f"已重启 NoneBot2 + snowluma。\n"
               f"NoneBot2 端口 {NONEBOT_PORT} 就绪。\n"
               f"snowluma: {snow_start['msg']}",
    }


# ── 进程看门狗 ──
_watchdog_task: Optional[asyncio.Task] = None
_watchdog_running = False

# ── WS 健康探活任务 ──
_ws_health_task: Optional[asyncio.Task] = None

# ── 重启互斥锁（防止看门狗与手动重启冲突） ──
_restart_lock = asyncio.Lock()


async def _watchdog_loop(interval: int = 30):
    """进程看门狗 v3：监控 NoneBot2 + snowluma + WebSocket 连接健康

    策略：
    1. NoneBot2 崩溃 → 自动重启
    2. snowluma 崩溃 → 自动重启
    3. WebSocket 断开（snowluma 未连接）→ 日志告警
    """
    global _watchdog_running
    _watchdog_running = True
    logger.info(f"[watchdog v3] Started (interval={interval}s)")

    # 启动后等 30 秒再开始检查
    await asyncio.sleep(30)

    _ws_warn_count = 0  # WebSocket 断开连续告警计数
    _WS_WARN_THRESHOLD = 3  # 连续 3 次断开才告警（避免启动期间误报）

    while _watchdog_running:
        try:
            nb_status = await asyncio.get_event_loop().run_in_executor(None, get_nonebot2_status)

            # NoneBot2 挂了 → 自动重启（加锁防止与手动重启冲突）
            if not nb_status["running"]:
                if _restart_lock.locked():
                    logger.info("[watchdog] Restart already in progress, skipping")
                else:
                    async with _restart_lock:
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
                    # WS 没连: 可能是 snowluma 挂了, 先看进程
                    snow_status = await asyncio.get_event_loop().run_in_executor(None, get_napcat_status)
                    if not snow_status["running"]:
                        # snowluma 进程也不在 → 自动拉起
                        if _restart_lock.locked():
                            logger.info("[watchdog] Restart already in progress, skipping snowluma")
                        else:
                            async with _restart_lock:
                                logger.warning("[watchdog] snowluma is down (no process), auto-restarting...")
                                result = await asyncio.get_event_loop().run_in_executor(None, start_snowluma)
                                logger.info(f"[watchdog] snowluma restart: {result['msg']}")
                    # 给 QQ 客户端 + snowluma 一个连接窗口 (每轮 30s, 连续告警)
                    _ws_warn_count += 1
                    if _ws_warn_count == _WS_WARN_THRESHOLD:
                        logger.warning(
                            f"[watchdog] No WebSocket client connected to port {NONEBOT_PORT} "
                            f"(checked {_WS_WARN_THRESHOLD} times). "
                            "snowluma/QQ may need attention."
                        )
                    elif _ws_warn_count > _WS_WARN_THRESHOLD and _ws_warn_count % 10 == 0:
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


# ── Dashboard 自身管理 ──

def get_dashboard_status() -> dict:
    """获取 Dashboard 进程自身的状态"""
    pid = os.getpid()
    try:
        p = psutil.Process(pid)
        mem = p.memory_info().rss
        ct = p.create_time()
        delta = time.time() - ct
        hours, minutes = int(delta // 3600), int((delta % 3600) // 60)
        uptime = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        return {
            "running": True,
            "pid": pid,
            "port": DASHBOARD_PORT,
            "port_open": check_port_open(DASHBOARD_PORT),
            "memory_mb": round(mem / 1024 / 1024, 1),
            "uptime": uptime,
        }
    except Exception as e:
        logger.error(f"get_dashboard_status error: {e}")
        return {"running": False, "pid": pid, "port": DASHBOARD_PORT}


def restart_dashboard() -> dict:
    """重启 Dashboard 自身（spawn 独立进程：杀旧 → 等端口 → 启新）"""
    import sys
    pid_file = PROJECT_ROOT / ".pids" / "dashboard.pid"
    log_file = LOG_DIR / "dashboard.log"
    current_pid = os.getpid()

    # 构建重启脚本（Python one-liner）
    restart_script = (
        f"import time, subprocess, socket, os, sys\n"
        f"def port_free(p):\n"
        f"    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
        f"    s.settimeout(1)\n"
        f"    r=s.connect_ex(('127.0.0.1',p))!=0\n"
        f"    s.close()\n"
        f"    return r\n"
        f"time.sleep(2)\n"  # 等待当前进程退出
        f"# 如果旧进程还在，强制终止\n"
        f"try:\n"
        f"    import psutil\n"
        f"    p=psutil.Process({current_pid})\n"
        f"    if p.is_running(): p.kill()\n"
        f"except: pass\n"
        f"time.sleep(2)\n"
        f"# 等待端口释放\n"
        f"for _ in range(10):\n"
        f"    if port_free({DASHBOARD_PORT}): break\n"
        f"    time.sleep(1)\n"
        f"# 启动新 Dashboard\n"
        f"logf=open(r'{log_file}','a',encoding='utf-8')\n"
        f"logf.write(f'\\n--- Dashboard restarted at {{}}\\n'.format(__import__('datetime').datetime.now().isoformat()))\n"
        f"proc=subprocess.Popen(\n"
        f"    [r'{VENV_PYTHON}','-m','uvicorn','qqbot.dashboard.main:app','--host','0.0.0.0','--port','{DASHBOARD_PORT}'],\n"
        f"    cwd=r'{PROJECT_ROOT}',\n"
        f"    stdout=logf,stderr=subprocess.STDOUT,\n"
        f"    creationflags=0x00000200 if sys.platform=='win32' else 0\n"
        f")\n"
        f"# 写 PID 文件\n"
        f"pid_dir=os.path.dirname(r'{pid_file}')\n"
        f"os.makedirs(pid_dir,exist_ok=True)\n"
        f"open(r'{pid_file}','w').write(str(proc.pid))\n"
    )

    # Spawn 独立重启进程（detached，不受当前进程影响）
    try:
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
        subprocess.Popen(
            [str(VENV_PYTHON), "-c", restart_script],
            creationflags=creation_flags,
            close_fds=True,
        )
        logger.info("Dashboard restart subprocess spawned")
        return {"ok": True, "msg": "Dashboard 正在重启，页面将在几秒后自动刷新"}
    except Exception as e:
        logger.error(f"Failed to spawn dashboard restart: {e}")
        return {"ok": False, "msg": f"重启失败: {e}"}


# ── snowluma 日志实时 tail（dashboard 前端 SSE 推流用） ──
SNOWLUMA_LOG_PATH = PROJECT_ROOT / "snowluma" / "start.out"
_snowluma_log_subscribers: set = set()  # 所有 SSE 订阅者（asyncio.Queue 集合）


def _read_snowluma_log_tail(max_bytes: int = 64 * 1024) -> str:
    """读 snowluma 日志最后 max_bytes 字节（按行对齐），返回解码文本"""
    try:
        if not SNOWLUMA_LOG_PATH.exists():
            return ""
        size = SNOWLUMA_LOG_PATH.stat().st_size
        with open(SNOWLUMA_LOG_PATH, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                data = f.read()
                # 丢弃首行不完整部分
                nl = data.find(b"\n")
                if 0 <= nl < len(data) - 1:
                    data = data[nl + 1:]
            else:
                data = f.read()
        # snowluma 日志是 utf-8（js 写出去），但有 BOM/颜色码就丢掉
        return data.decode("utf-8", errors="replace")
    except OSError as e:
        return f"[log read error] {e}\n"


async def _snowluma_log_watcher(poll_interval: float = 0.5):
    """后台轮询 snowluma 日志文件，新行推给所有订阅者"""
    last_size = 0
    try:
        if SNOWLUMA_LOG_PATH.exists():
            last_size = SNOWLUMA_LOG_PATH.stat().st_size
    except OSError:
        pass
    while True:
        await asyncio.sleep(poll_interval)
        try:
            if not SNOWLUMA_LOG_PATH.exists():
                last_size = 0
                continue
            cur_size = SNOWLUMA_LOG_PATH.stat().st_size
            if cur_size < last_size:
                # 文件被截断/重建，全量重发
                last_size = 0
            if cur_size == last_size:
                continue
            with open(SNOWLUMA_LOG_PATH, "rb") as f:
                f.seek(last_size)
                new_data = f.read(cur_size - last_size)
            last_size = cur_size
            text = new_data.decode("utf-8", errors="replace")
            # 推给所有订阅者
            dead = []
            for q in list(_snowluma_log_subscribers):
                try:
                    q.put_nowait(text)
                except Exception:
                    dead.append(q)
            for q in dead:
                _snowluma_log_subscribers.discard(q)
        except OSError as e:
            # 文件暂时打不开（snowluma 重启瞬间），跳过本轮
            await asyncio.sleep(1.0)


def start_snowluma_log_watcher() -> asyncio.Task:
    """启动 snowluma 日志 tail 协程（idempotent）"""
    global _snowluma_log_watcher_task
    if _snowluma_log_watcher_task and not _snowluma_log_watcher_task.done():
        return _snowluma_log_watcher_task
    _snowluma_log_watcher_task = asyncio.create_task(_snowluma_log_watcher())
    return _snowluma_log_watcher_task


_snowluma_log_watcher_task: Optional[asyncio.Task] = None
