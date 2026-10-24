"""
Dashboard 自重启 helper（由 monitor.restart_dashboard() spawn 出来的独立进程）

为什么是独立文件：原先这段逻辑是拼成 python -c 的字符串跑的，结果父进程
f-string 里的 {{}} 求值后塌缩成 {}，helper 里变成空表达式 f-string ->
SyntaxError -> helper 秒退 -> 旧 Dashboard 已自杀、新的没起来、面板掉线。
改成真实文件后：语法在导入期就能被发现，也能单独 dry-run 测试。

职责（顺序）：
  1. 打个开始标记（万一后面再出事，至少知道 helper 起没起）
  2. 等旧进程退出；还活着就 kill
  3. 等端口释放
  4. 拉起新的 uvicorn，stdout/stderr 进 logs/dashboard.log
  5. 写 pid 文件（.runtime/dashboard.pid，与 launch_dashboard.ps1 保持一致）
  6. 确认新实例真的起来了（轮询端口 / 检测子进程提前退出）
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # skill_communication/
LOG_DIR = PROJECT_ROOT / "qqbot" / "logs"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
PID_FILE = RUNTIME_DIR / "dashboard.pid"
BOOT_LOG = LOG_DIR / "dashboard.log"
HELPER_LOG = LOG_DIR / "dashboard_restart.log"
VENV_PYTHON = Path(r"D:\Work\anaconda3\envs\botenv\python.exe")

APP = "qqbot.dashboard.main:app"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(HELPER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", port)) != 0
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True, help="旧 Dashboard 的 pid")
    ap.add_argument("--port", type=int, required=True, help="Dashboard 端口")
    ap.add_argument("--dry-run", action="store_true", help="不真的拉 uvicorn，用假子进程验证流程")
    args = ap.parse_args()

    _log(f"=== helper started (old_pid={args.pid}, port={args.port}, dry_run={args.dry_run}) ===")

    # 1) 等旧进程自己退出（父进程会调 SIGTERM）
    time.sleep(2)

    # 2) 旧进程还活着就强杀
    try:
        import psutil  # type: ignore

        p = psutil.Process(args.pid)
        if p.is_running():
            p.kill()
            _log(f"killed old dashboard pid={args.pid}")
        else:
            _log("old dashboard already gone")
    except Exception as e:  # psutil 缺失 / 进程不存在都无所谓
        _log(f"kill step skipped: {type(e).__name__}: {e}")

    # 3) 等端口释放
    waited = 0
    while waited < 15 and not _port_free(args.port):
        time.sleep(1)
        waited += 1
    _log(f"port {args.port} free after {waited}s")

    # 4) 拉起新实例
    if args.dry_run:
        argv = [sys.executable, "-c", "import time; time.sleep(8)"]
        _log("dry-run: 用假子进程替代 uvicorn")
        child_out = subprocess.DEVNULL
        child_err = None
    else:
        argv = [
            str(VENV_PYTHON), "-m", "uvicorn", APP,
            "--host", "0.0.0.0", "--port", str(args.port),
        ]
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        child_out = open(BOOT_LOG, "a", encoding="utf-8")
        child_out.write(f"\n--- Dashboard restarted at {datetime.now().isoformat()} ---\n")
        child_out.flush()
        child_err = subprocess.STDOUT

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(PROJECT_ROOT),
            stdout=child_out,
            stderr=child_err,
            creationflags=0x00000200 if sys.platform == "win32" else 0,
        )
    except Exception as e:
        _log(f"FAILED to spawn new dashboard: {type(e).__name__}: {e}")
        return 1

    # 5) 写 pid 文件
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except Exception as e:
        _log(f"pid file write failed: {type(e).__name__}: {e}")

    _log(f"spawned new dashboard pid={proc.pid}")

    # 6) 确认真的起来了
    if args.dry_run:
        time.sleep(2)
        alive = proc.poll() is None
        _log(f"dry-run child alive={alive}")
        if alive:
            proc.kill()
        _log("=== helper done (dry-run) ===")
        return 0

    ok = False
    for _ in range(20):
        time.sleep(1)
        rc = proc.poll()
        if rc is not None:
            _log(f"new dashboard 提前退出, returncode={rc}")
            break
        if not _port_free(args.port):
            ok = True
            break

    _log(f"=== helper done: restart {'OK' if ok else 'FAILED'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
