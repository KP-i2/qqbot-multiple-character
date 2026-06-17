#!/usr/bin/env python3
"""QQ Bot 统一管理脚本 —— 解决旧进程残留导致端口冲突的问题

用法:
    python manage.py start   [bot|dashboard|all]   启动服务
    python manage.py stop    [bot|dashboard|all]   停止服务
    python manage.py restart [bot|dashboard|all]   重启服务
    python manage.py status                        查看所有服务状态
    python manage.py log     [bot|dashboard]       查看最近日志

特性:
    - 启动前自动清理占用端口的旧进程（不再出现 Errno 10048）
    - PID 文件追踪，stop 时精确定位进程
    - 端口兜底检测，即使 PID 文件丢失也能找到旧进程
    - 优雅退出: SIGTERM → 等待 → SIGKILL
"""

import sys
import os
import time
import signal
import subprocess
import socket
import argparse
from pathlib import Path
from datetime import datetime

# ── 路径常量 ──
PROJECT_ROOT = Path(__file__).resolve().parent
QQBOT_DIR = PROJECT_ROOT / "qqbot"
VENV_PYTHON = PROJECT_ROOT / "skill_qqbot" / "Scripts" / "python.exe"
PID_DIR = PROJECT_ROOT / ".pids"
LOG_DIR = QQBOT_DIR / "logs"

BOT_PORT = 8080
DASHBOARD_PORT = 8501

BOT_LOG = LOG_DIR / "nonebot2.log"
DASHBOARD_LOG = LOG_DIR / "dashboard.log"

BOT_PID_FILE = PID_DIR / "bot.pid"
DASHBOARD_PID_FILE = PID_DIR / "dashboard.pid"

# ── 启动等待时间 ──
BOT_STARTUP_WAIT = 10      # Bot 启动等待（秒）
DASHBOARD_STARTUP_WAIT = 6  # Dashboard 启动等待（秒）
PORT_CHECK_TIMEOUT = 15     # 端口就绪检测超时（秒）
STOP_GRACE_PERIOD = 5       # 优雅退出等待（秒）


# ════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════

def log(msg: str, level: str = "INFO"):
    ts = time.strftime("%H:%M:%S")
    colors = {"INFO": "\033[36m", "OK": "\033[32m", "WARN": "\033[33m",
              "ERROR": "\033[31m", "ACTION": "\033[35m"}
    reset = "\033[0m"
    c = colors.get(level, "")
    print(f"{c}[{ts}] [{level}]{reset} {msg}", flush=True)


def check_port(port: int) -> bool:
    """检测端口是否被监听"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_pids_on_port(port: int) -> list[int]:
    """找到占用指定端口的进程 PID（Windows netstat 兜底）"""
    pids = set()
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status in (
                "LISTEN", "ESTABLISHED", "FIN_WAIT_1", "FIN_WAIT_2"
            ):
                pids.add(conn.pid)
    except (ImportError, psutil.AccessDenied, OSError):
        # psutil 不可用时，回退到 netstat
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"], text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    local = parts[1]
                    if local.endswith(f":{port}") and parts[3] in ("LISTENING", "ESTABLISHED"):
                        try:
                            pids.add(int(parts[-1]))
                        except ValueError:
                            pass
        except Exception:
            pass
    return [p for p in pids if p and p > 0]


def kill_pid(pid: int, grace: float = STOP_GRACE_PERIOD) -> bool:
    """优雅终止进程: terminate → 等待 → kill"""
    try:
        import psutil
        p = psutil.Process(pid)
        if not p.is_running():
            return True
        p.terminate()
        try:
            p.wait(timeout=grace)
            return True
        except psutil.TimeoutExpired:
            p.kill()
            p.wait(timeout=3)
            return True
    except ImportError:
        # 无 psutil 时用 subprocess
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(grace)
            return True
        except ProcessLookupError:
            return True
        except Exception:
            return False
    except Exception:
        return False


def ensure_dirs():
    """确保必要目录存在"""
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def read_pid(pid_file: Path) -> int | None:
    """读取 PID 文件，如果进程已不存在则清理文件"""
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return None
    # 检查进程是否还活着
    try:
        import psutil
        if not psutil.pid_exists(pid):
            pid_file.unlink(missing_ok=True)
            return None
    except ImportError:
        # 无 psutil 时假设 PID 有效
        pass
    return pid


def write_pid(pid_file: Path, pid: int):
    pid_file.write_text(str(pid), encoding="utf-8")


def clear_stale_port(port: int, label: str) -> int:
    """清理占用指定端口的旧进程，返回清理数量"""
    pids = find_pids_on_port(port)
    if not pids:
        return 0
    killed = 0
    for pid in pids:
        # 跳过 PID 0 (系统) 和自己
        if pid <= 0 or pid == os.getpid():
            continue
        log(f"清理占用端口 {port} 的旧进程 {label} (PID {pid})", "ACTION")
        if kill_pid(pid):
            killed += 1
        else:
            log(f"无法终止进程 PID {pid}", "WARN")
    if killed:
        time.sleep(2)  # 等待端口释放
    return killed


# ════════════════════════════════════════════
#  Bot 管理
# ════════════════════════════════════════════

def start_bot(force: bool = False) -> bool:
    ensure_dirs()

    # 检查是否已经在运行
    pid = read_pid(BOT_PID_FILE)
    if pid and not force:
        if check_port(BOT_PORT):
            log(f"Bot 已在运行 (PID {pid}, 端口 {BOT_PORT})", "OK")
            return True
        else:
            log(f"PID 文件存在 (PID {pid}) 但端口 {BOT_PORT} 未监听，清理残留", "WARN")

    # 清理旧进程
    if pid:
        log(f"终止旧的 Bot 进程 (PID {pid})", "ACTION")
        kill_pid(pid)
        BOT_PID_FILE.unlink(missing_ok=True)
        time.sleep(1)

    # 清理端口上的残留进程（兜底）
    cleared = clear_stale_port(BOT_PORT, "Bot")
    if cleared:
        log(f"已清理 {cleared} 个占用端口的旧进程", "OK")

    # 启动
    if not VENV_PYTHON.exists():
        log(f"未找到 venv Python: {VENV_PYTHON}", "ERROR")
        log("请先运行 setup.bat", "ERROR")
        return False

    log_file = LOG_DIR / "nonebot2.log"
    with open(log_file, "a", encoding="utf-8") as lf:
        lf.write(f"\n--- Bot started at {datetime.now().isoformat()} (manage.py) ---\n")
        proc = subprocess.Popen(
            [str(VENV_PYTHON), "-X", "utf8", str(QQBOT_DIR / "bot.py")],
            cwd=str(QQBOT_DIR),
            stdout=lf,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )

    write_pid(BOT_PID_FILE, proc.pid)
    log(f"Bot 已启动 (PID {proc.pid})", "OK")

    # 等待端口就绪
    log(f"等待 Bot 端口 {BOT_PORT} 就绪...")
    for i in range(PORT_CHECK_TIMEOUT):
        time.sleep(1)
        if check_port(BOT_PORT):
            log(f"Bot 端口 {BOT_PORT} 已就绪 ({i + 1}s)", "OK")
            return True

    log(f"Bot 端口 {BOT_PORT} 在 {PORT_CHECK_TIMEOUT}s 内未就绪，请检查日志", "WARN")
    log(f"日志: {log_file}", "INFO")
    return False


def stop_bot() -> bool:
    stopped = 0

    # 1. PID 文件
    pid = read_pid(BOT_PID_FILE)
    if pid:
        log(f"停止 Bot (PID {pid})", "ACTION")
        if kill_pid(pid):
            stopped += 1
        BOT_PID_FILE.unlink(missing_ok=True)

    # 2. 端口兜底（PID 文件丢失或进程残留）
    time.sleep(1)
    cleared = clear_stale_port(BOT_PORT, "Bot")
    stopped += cleared

    # 3. cmdline 兜底（psutil 按命令行匹配）
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(p.info.get("cmdline") or [])
                if "bot.py" in cmdline and "python" in (p.info["name"] or "").lower():
                    if p.info["pid"] != os.getpid():
                        p.terminate()
                        stopped += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        pass

    if stopped:
        log(f"Bot 已停止 (清理了 {stopped} 个进程)", "OK")
    else:
        log("Bot 未在运行", "INFO")
    return True


# ════════════════════════════════════════════
#  Dashboard 管理
# ════════════════════════════════════════════

def start_dashboard(force: bool = False) -> bool:
    ensure_dirs()

    pid = read_pid(DASHBOARD_PID_FILE)
    if pid and not force:
        if check_port(DASHBOARD_PORT):
            log(f"Dashboard 已在运行 (PID {pid}, 端口 {DASHBOARD_PORT})", "OK")
            return True
        else:
            log(f"PID 文件存在 (PID {pid}) 但端口 {DASHBOARD_PORT} 未监听，清理残留", "WARN")

    if pid:
        log(f"终止旧的 Dashboard 进程 (PID {pid})", "ACTION")
        kill_pid(pid)
        DASHBOARD_PID_FILE.unlink(missing_ok=True)
        time.sleep(1)

    cleared = clear_stale_port(DASHBOARD_PORT, "Dashboard")
    if cleared:
        log(f"已清理 {cleared} 个占用端口的旧进程", "OK")

    if not VENV_PYTHON.exists():
        log(f"未找到 venv Python: {VENV_PYTHON}", "ERROR")
        return False

    log_file = LOG_DIR / "dashboard.log"
    with open(log_file, "a", encoding="utf-8") as lf:
        lf.write(f"\n--- Dashboard started at {datetime.now().isoformat()} (manage.py) ---\n")
        proc = subprocess.Popen(
            [str(VENV_PYTHON), "-m", "uvicorn",
             "qqbot.dashboard.main:app",
             "--host", "0.0.0.0", "--port", str(DASHBOARD_PORT)],
            cwd=str(PROJECT_ROOT),
            stdout=lf,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )

    write_pid(DASHBOARD_PID_FILE, proc.pid)
    log(f"Dashboard 已启动 (PID {proc.pid})", "OK")

    log(f"等待 Dashboard 端口 {DASHBOARD_PORT} 就绪...")
    for i in range(PORT_CHECK_TIMEOUT):
        time.sleep(1)
        if check_port(DASHBOARD_PORT):
            log(f"Dashboard 端口 {DASHBOARD_PORT} 已就绪 ({i + 1}s)", "OK")
            return True

    log(f"Dashboard 端口 {DASHBOARD_PORT} 在 {PORT_CHECK_TIMEOUT}s 内未就绪，请检查日志", "WARN")
    log(f"日志: {log_file}", "INFO")
    return False


def stop_dashboard() -> bool:
    stopped = 0

    pid = read_pid(DASHBOARD_PID_FILE)
    if pid:
        log(f"停止 Dashboard (PID {pid})", "ACTION")
        if kill_pid(pid):
            stopped += 1
        DASHBOARD_PID_FILE.unlink(missing_ok=True)

    time.sleep(1)
    cleared = clear_stale_port(DASHBOARD_PORT, "Dashboard")
    stopped += cleared

    # uvicorn 兜底
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(p.info.get("cmdline") or [])
                if "uvicorn" in cmdline and "dashboard" in cmdline:
                    if p.info["pid"] != os.getpid():
                        p.terminate()
                        stopped += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        pass

    if stopped:
        log(f"Dashboard 已停止 (清理了 {stopped} 个进程)", "OK")
    else:
        log("Dashboard 未在运行", "INFO")
    return True


# ════════════════════════════════════════════
#  状态查看
# ════════════════════════════════════════════

def show_status():
    ensure_dirs()
    services = [
        ("Bot", BOT_PORT, BOT_PID_FILE),
        ("Dashboard", DASHBOARD_PORT, DASHBOARD_PID_FILE),
    ]

    print()
    print(f"  {'服务':<12} {'状态':<10} {'PID':<8} {'端口':<8} {'运行时间':<12} {'内存'}")
    print(f"  {'─' * 12} {'─' * 10} {'─' * 8} {'─' * 8} {'─' * 12} {'─' * 10}")

    for name, port, pid_file in services:
        pid = read_pid(pid_file)
        port_ok = check_port(port)
        uptime_str = "-"
        mem_str = "-"
        running = False

        # 尝试通过 psutil 获取详细信息
        if pid:
            try:
                import psutil
                p = psutil.Process(pid)
                if p.is_running():
                    running = True
                    ct = p.create_time()
                    delta = time.time() - ct
                    h, m = int(delta // 3600), int((delta % 3600) // 60)
                    uptime_str = f"{h}h {m}m" if h else f"{m}m"
                    mem = p.memory_info().rss
                    mem_str = f"{mem / 1024 / 1024:.1f} MB"
            except Exception:
                pass

        # PID 文件没有但端口被占用（外部启动的进程）
        if not running and port_ok:
            pids = find_pids_on_port(port)
            if pids:
                pid = pids[0]
                running = True
                try:
                    import psutil
                    p = psutil.Process(pid)
                    ct = p.create_time()
                    delta = time.time() - ct
                    h, m = int(delta // 3600), int((delta % 3600) // 60)
                    uptime_str = f"{h}h {m}m" if h else f"{m}m"
                    mem = p.memory_info().rss
                    mem_str = f"{mem / 1024 / 1024:.1f} MB"
                except Exception:
                    pass

        if running and port_ok:
            status = "\033[32m● 运行中\033[0m"
        elif running:
            status = "\033[33m◐ 启动中\033[0m"
        elif port_ok:
            status = "\033[33m◉ 端口占用\033[0m"
        else:
            status = "\033[31m○ 未运行\033[0m"

        pid_str = str(pid) if pid else "-"
        print(f"  {name:<12} {status:<20} {pid_str:<8} {port:<8} {uptime_str:<12} {mem_str}")

    # NapCat 状态（只读检测）
    napcat_running = False
    napcat_pid = "-"
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            try:
                pname = (p.info["name"] or "").lower()
                if "napcatqq" in pname or "napcat-desktop" in pname:
                    napcat_running = True
                    napcat_pid = str(p.info["pid"])
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        pass

    ws_ok = False
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == BOT_PORT and conn.status == "ESTABLISHED":
                ws_ok = True
                break
    except Exception:
        pass

    nap_status = "\033[32m● 运行中\033[0m" if napcat_running else "\033[31m○ 未运行\033[0m"
    ws_status = "\033[32m● 已连接\033[0m" if ws_ok else "\033[31m○ 未连接\033[0m"
    print(f"  {'NapCat':<12} {nap_status:<20} {napcat_pid:<8} {'-':<8} {'-':<12} -")
    print(f"  {'WS 连接':<12} {ws_status}")
    print()


# ════════════════════════════════════════════
#  日志查看
# ════════════════════════════════════════════

def show_log(target: str, lines: int = 50):
    log_map = {"bot": BOT_LOG, "dashboard": DASHBOARD_LOG}
    log_file = log_map.get(target)
    if not log_file:
        log(f"未知目标: {target}，可选: bot, dashboard", "ERROR")
        return
    if not log_file.exists():
        log(f"日志文件不存在: {log_file}", "WARN")
        return

    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    print(f"\n── {log_file.name} (最近 {len(tail)} 行) ──")
    for line in tail:
        print(line, end="")
    print()


# ════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="QQ Bot 统一管理脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python manage.py start all        启动 Bot + Dashboard
  python manage.py stop all         停止所有服务
  python manage.py restart bot      重启 Bot（自动清理旧进程）
  python manage.py status           查看服务状态
  python manage.py log bot          查看 Bot 最近日志
        """,
    )
    sub = parser.add_subparsers(dest="command", help="操作命令")

    # start
    p_start = sub.add_parser("start", help="启动服务")
    p_start.add_argument("target", nargs="?", default="all",
                         choices=["bot", "dashboard", "all"], help="启动目标")

    # stop
    p_stop = sub.add_parser("stop", help="停止服务")
    p_stop.add_argument("target", nargs="?", default="all",
                        choices=["bot", "dashboard", "all"], help="停止目标")

    # restart
    p_restart = sub.add_parser("restart", help="重启服务（自动清理旧进程）")
    p_restart.add_argument("target", nargs="?", default="all",
                           choices=["bot", "dashboard", "all"], help="重启目标")

    # status
    sub.add_parser("status", help="查看所有服务状态")

    # log
    p_log = sub.add_parser("log", help="查看最近日志")
    p_log.add_argument("target", nargs="?", default="bot",
                       choices=["bot", "dashboard"], help="日志目标")
    p_log.add_argument("-n", "--lines", type=int, default=50, help="显示行数")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "status":
        show_status()
        return

    if args.command == "log":
        show_log(args.target, args.lines)
        return

    if args.command == "start":
        if args.target in ("bot", "all"):
            start_bot()
        if args.target in ("dashboard", "all"):
            start_dashboard()
        if args.target == "all":
            show_status()
        return

    if args.command == "stop":
        if args.target in ("bot", "all"):
            stop_bot()
        if args.target in ("dashboard", "all"):
            stop_dashboard()
        return

    if args.command == "restart":
        if args.target in ("bot", "all"):
            stop_bot()
            time.sleep(1)
            start_bot()
        if args.target in ("dashboard", "all"):
            stop_dashboard()
            time.sleep(1)
            start_dashboard()
        if args.target == "all":
            show_status()
        return


if __name__ == "__main__":
    main()
