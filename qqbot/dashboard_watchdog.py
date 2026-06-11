"""Dashboard 进程守护 —— 定期健康检查，挂自动重启"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CHECK_INTERVAL = 30       # 健康检查间隔（秒）
HEALTH_URL = "http://127.0.0.1:8501/api/health"
HEALTH_TIMEOUT = 10       # 健康检查超时（秒）
RESTART_DELAY = 3         # 重启前等待（秒）
MAX_FAILURES = 3          # 连续失败 N 次才重启（避免瞬时网络抖动误杀）

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / "skill_qqbot" / "Scripts" / "python.exe"
DASHBOARD_SCRIPT = ROOT / "qqbot" / "dashboard.py"


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [watchdog] {msg}", flush=True)


def check_health() -> bool:
    try:
        resp = urllib.request.urlopen(HEALTH_URL, timeout=HEALTH_TIMEOUT)
        return resp.status == 200
    except Exception:
        return False


def main():
    if not VENV_PYTHON.exists():
        log(f"ERROR: venv python not found: {VENV_PYTHON}")
        log("Please run setup.bat first.")
        sys.exit(1)

    log(f"Dashboard watchdog started (check every {CHECK_INTERVAL}s)")
    log(f"Venv python: {VENV_PYTHON}")
    log(f"Dashboard:   {DASHBOARD_SCRIPT}")

    proc = None
    fail_count = 0

    try:
        while True:
            # 启动 / 重启 Dashboard 进程
            if proc is None or proc.poll() is not None:
                if proc is not None:
                    exit_code = proc.returncode
                    log(f"Dashboard exited with code {exit_code}")
                log("Starting Dashboard...")
                proc = subprocess.Popen(
                    [str(VENV_PYTHON), "-X", "utf8", str(DASHBOARD_SCRIPT)],
                    cwd=str(ROOT / "qqbot"),
                )
                log(f"Dashboard PID: {proc.pid}")
                time.sleep(8)  # 等 Dashboard 启动完成
                fail_count = 0

            # 健康检查
            if check_health():
                fail_count = 0
            else:
                fail_count += 1
                if proc.poll() is not None:
                    log(f"Dashboard process dead (exit={proc.returncode}), restarting...")
                    proc = None
                elif fail_count >= MAX_FAILURES:
                    log(f"Dashboard unresponsive for {fail_count} checks, killing and restarting...")
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    proc = None
                else:
                    log(f"Health check failed ({fail_count}/{MAX_FAILURES})")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("Watchdog stopped by user")
        if proc and proc.poll() is None:
            log("Stopping Dashboard...")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log("Bye")


if __name__ == "__main__":
    main()
