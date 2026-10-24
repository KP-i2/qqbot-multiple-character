"""进程检测与系统监控（async-friendly + 日志捕获）"""
import os, re, time, asyncio, subprocess, socket, logging, shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
import psutil

logger = logging.getLogger("dashboard.monitor")

PROJECT_ROOT = Path(__file__).parent.parent.parent  # skill_communication/
QQBOT_DIR = PROJECT_ROOT / "qqbot"
VENV_PYTHON = Path(r"D:\Work\anaconda3\envs\botenv\python.exe")
BOT_SCRIPT = QQBOT_DIR / "bot.py"
LOG_DIR = QQBOT_DIR / "logs"
SNOWLUMA_DIR = PROJECT_ROOT / "snowluma"  # snowluma 安装目录
SNOWLUMA_NODE_EXE: Optional[Path] = None  # 解析后填入: snowluma/SnowLuma-*/node.exe
# 旧 NapCat 兼容：保留常量名但实际指向 snowluma，避免大改
LEGACY_PROTOCOL_EXE = "NapCatQQ-Desktop.exe"  # 仅用于日志注释

# 2026-09-10: 自己加载 private/.env（写法跟 weibo_fetcher 一致）。
# 必须放在下面读取 STATUS_CACHE_TTL 等 os.getenv 常量之前 ——
# main.py 里 monitor 是第一个被 import 的模块，此时 weibo_fetcher 的
# load_dotenv 还没执行，不自己加载就读不到 .env 里的配置。
try:
    from dotenv import load_dotenv
    _env_file = PROJECT_ROOT / "private" / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:  # dotenv 缺失时静默降级为纯环境变量
    pass


def _snowluma_version_key(dir_name: str) -> tuple:
    """从目录名 SnowLuma-v2.2.0-win-x64 提取版本元组，用于自然排序。"""
    m = re.search(r"SnowLuma-v(\d+(?:\.\d+)*)", dir_name, re.IGNORECASE)
    if m:
        return tuple(int(x) for x in m.group(1).split("."))
    return (0,)


def _resolve_snowluma_node() -> Optional[Path]:
    """解析 snowluma 安装路径: 找 snowluma/SnowLuma-*/node.exe

    2026-10-25: 多版本目录改为按版本号自然排序取最高版本。
    原实现 `sorted()` 是字典序：v1.10.3 会排在 v2.2.0 前面、v2.10 也会排在
    v2.2 前面，升级后可能一直启动到旧版。同版本时按目录修改时间兜底。
    返回 None 表示未安装。
    """
    global SNOWLUMA_NODE_EXE
    if SNOWLUMA_NODE_EXE is not None and SNOWLUMA_NODE_EXE.exists():
        return SNOWLUMA_NODE_EXE
    if not SNOWLUMA_DIR.exists():
        return None
    candidates = []
    for sub in SNOWLUMA_DIR.iterdir():
        if not (sub.is_dir() and sub.name.startswith("SnowLuma-")):
            continue
        node_exe = sub / "node.exe"
        if not node_exe.exists():
            continue
        try:
            mtime = sub.stat().st_mtime
        except OSError:
            mtime = 0.0
        candidates.append((_snowluma_version_key(sub.name), mtime, node_exe))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    SNOWLUMA_NODE_EXE = candidates[0][2]
    return SNOWLUMA_NODE_EXE

# ── 可配置常量 ──
NONEBOT_PORT = int(os.getenv("NONEBOT_PORT", "8080"))
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8501"))
# get_all_status 优化后冷跑约 30ms（原先 4.4s），30s 缓存足够；
# 需要更快刷新可改 .env 的 STATUS_CACHE_TTL（用户点 start/stop 会走
# invalidate_status_cache() 立即失效，不受此值影响）
STATUS_CACHE_TTL = int(os.getenv("STATUS_CACHE_TTL", "30"))
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
    """检查端口是否在监听。

    2026-08-30: 原实现用 socket connect_ex，连到未监听端口时会等满 1s 超时
    （Windows 防火墙静默丢包），get_all_status 每次冷缓存要拖 1s+。
    改为 psutil 查 LISTEN 状态（毫秒级），AccessDenied 时回退 socket。
    """
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
                return True
        return False
    except (psutil.AccessDenied, OSError):
        pass
    # fallback：socket connect 探测
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


def _memory_mb(proc: psutil.Process) -> float:
    """按需读取单个进程的内存占用（MB）。

    2026-09-10 性能修复：**不要在 process_iter 的 attrs 里带 memory_info**。
    本机实测（296 个进程）：
        process_iter(["pid","name"])                   0.001s
        process_iter(["pid","name","create_time"])     0.001s
        process_iter(["pid","name","memory_info"])     0.620s   <-- 唯一杀手
    原因是 memory_info 要对每个进程 OpenProcess 取一次 RSS。而真正需要的
    往往只有命中名字的那几个，按需取 5 个进程实测 0.0000s。
    """
    try:
        return round(proc.memory_info().rss / 1024 / 1024, 1)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def find_process_by_name(name: str) -> list[dict]:
    results = []
    # 注意：attrs 里刻意不带 memory_info（见 _memory_mb 的性能说明）
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            if name.lower() in (proc.info["name"] or "").lower():
                results.append({
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "memory_mb": _memory_mb(proc),
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
    # 注意：attrs 里刻意不带 memory_info（见 _memory_mb 的性能说明）
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            pinfo = proc.info
            pname = (pinfo["name"] or "").lower()
            if pname != "node.exe":
                continue
            # 直接用 proc，不再 psutil.Process(pid) 重开句柄（省一次 OpenProcess）
            exe_path = (proc.exe() or "").lower()
            if "snowluma" in exe_path or "\\snowluma\\" in exe_path:
                snowluma_procs.append({
                    "pid": pinfo["pid"],
                    "name": "snowluma",
                    "memory_mb": _memory_mb(proc),
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
    # 2026-08-30: get_napcat_status 只调一次，snowluma/napcat 两个字段复用（原实现调了两次）
    napcat_status = get_napcat_status()
    result = {
        "nonebot2": get_nonebot2_status(),
        "snowluma": napcat_status,   # 2026-06-27 字段名统一:snowluma
        "napcat": napcat_status,     # 兼容旧前端
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


# ── WS 健康探活任务 ──
_ws_health_task: Optional[asyncio.Task] = None

# ── 重启互斥锁（防止并发重启冲突） ──
_restart_lock = asyncio.Lock()


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
    """重启 Dashboard 自身（spawn 独立 helper：杀旧 → 等端口 → 启新 → 校验）

    2026-09-10 修复：此前这段逻辑是拼成 `python -c` 的字符串跑的。父进程 f-string 里的
    `{{}}` 求值后塌缩成 `{}`，写进 helper 就变成空表达式 f-string ->
    SyntaxError -> helper 秒退：旧进程已经自杀、新进程根本没起来，面板直接掉线，
    而且报错落在继承来的 stderr 里（很难查）。
    现在改成 spawn 真实文件 qqbot/dashboard/_restart_helper.py —— 语法在导入期就能发现，
    能单独 dry-run 测试，helper 的 stdout/stderr 也落到 logs/dashboard_restart.log。
    """
    import sys

    helper = Path(__file__).resolve().parent / "_restart_helper.py"
    helper_log = LOG_DIR / "dashboard_restart.log"

    if not helper.exists():
        logger.error(f"restart helper not found: {helper}")
        return {"ok": False, "msg": f"重启组件缺失: {helper.name}"}

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logf = open(helper_log, "ab")
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            if sys.platform == "win32" else 0
        )
        subprocess.Popen(
            [str(VENV_PYTHON), str(helper), "--pid", str(os.getpid()), "--port", str(DASHBOARD_PORT)],
            creationflags=creation_flags,
            close_fds=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        logger.info(f"Dashboard restart helper spawned (log: {helper_log})")
        return {"ok": True, "msg": "Dashboard 正在重启，页面将在几秒后自动刷新"}
    except Exception as e:
        logger.error(f"Failed to spawn dashboard restart helper: {e}")
        return {"ok": False, "msg": f"重启失败: {e}"}


# ── snowluma 日志实时 tail（dashboard 前端 SSE 推流用） ──
# 2026-10-24 修正：SnowLuma v2.x 不再写 snowluma/start.out；实际日志是
# monitor.start_snowluma() 重定向到的 qqbot/logs/snowluma.log，指向它才看得到内容。
SNOWLUMA_LOG_PATH = LOG_DIR / "snowluma.log"
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
