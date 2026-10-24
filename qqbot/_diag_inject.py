"""
In-process diag — monkey-patch multiprocessing.Process 抓所有 fork
2026-07-23 临时
"""
import os
import sys
import multiprocessing
import multiprocessing.spawn
import traceback
from pathlib import Path

# patch
if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    sys._base_executable = sys.executable

_PRIVATE_ENV = Path(__file__).resolve().parent.parent / "private" / ".env"
if _PRIVATE_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(_PRIVATE_ENV)


# ── monkey-patch multiprocessing.Process (基类) ──
_orig_start = multiprocessing.Process.start

def _patched_start(self, *args, **kwargs):
    print(f"\n[FORK_DETECT] multiprocessing.Process.start() called", flush=True)
    print(f"[FORK_DETECT]   pid={os.getpid()}, sys.executable={sys.executable!r}", flush=True)
    print(f"[FORK_DETECT]   target={getattr(self, '_target', None)!r}", flush=True)
    print(f"[FORK_DETECT]   name={self.name!r}", flush=True)
    print(f"[FORK_DETECT]   STACK:", flush=True)
    traceback.print_stack(file=sys.stdout, limit=10)
    return _orig_start(self, *args, **kwargs)

multiprocessing.Process.start = _patched_start


# ── monkey-patch subprocess.Popen ──
import subprocess
_orig_popen_init = subprocess.Popen.__init__

def _patched_popen_init(self, args, *more_args, **kwargs):
    try:
        if args:
            cmd0 = args[0] if isinstance(args, (list, tuple)) else args
            cmd_str = str(cmd0)[:120] if cmd0 else ""
            if "python" in cmd_str.lower() or "D:\\" in cmd_str or "Work" in cmd_str:
                print(f"\n[POPEN_DETECT] subprocess.Popen called with: {args[:3]!r}", flush=True)
                print(f"[POPEN_DETECT]   my sys.executable={sys.executable!r}", flush=True)
                print(f"[POPEN_DETECT]   kwargs.executable={kwargs.get('executable')!r}", flush=True)
                traceback.print_stack(file=sys.stdout, limit=8)
    except Exception as e:
        pass
    return _orig_popen_init(self, args, *more_args, **kwargs)

subprocess.Popen.__init__ = _patched_popen_init


print(f"[diag] all patches applied, sys.executable={sys.executable!r}", flush=True)


# ── 跑真实的 bot.py 流程 ──
proc = multiprocessing.Process(
    target=lambda: __import__("subprocess").run(
        [sys.executable, "-X", "utf8", str(Path(__file__).parent / "bot.py")],
        cwd=str(Path(__file__).parent),
    ),
    name="bot_runner",
)
proc.start()
proc.join()
