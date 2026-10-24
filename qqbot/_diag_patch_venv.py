"""
Bot.py monkey-patch runner — 在 bot.py 入口 patch multiprocessing.popen_spawn_win32.Popen
抓所有 fork 时刻的 executable + callstack
2026-07-23 临时
"""
import sys
import os
import traceback
import subprocess
from pathlib import Path

# patch sys._base_executable 跟 bot.py 一致
if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    sys._base_executable = sys.executable

_PRIVATE_ENV = Path(__file__).resolve().parent.parent / "private" / ".env"
if _PRIVATE_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(_PRIVATE_ENV)


# patch multiprocessing.popen_spawn_win32.Popen 抓 fork 时刻
def _patch_popen_spawn_win32():
    import multiprocessing.popen_spawn_win32
    orig = multiprocessing.popen_spawn_win32.Popen.__init__

    def patched(self, process_obj):
        python_exe = multiprocessing.popen_spawn_win32.spawn.get_executable()
        winenv = multiprocessing.popen_spawn_win32.WINENV
        path_eq = multiprocessing.popen_spawn_win32._path_eq(python_exe, sys.executable)
        print(f"\n[PATCH] Popen.__init__ called!", flush=True)
        print(f"[PATCH]   my pid={os.getpid()}, my sys.executable={sys.executable!r}", flush=True)
        print(f"[PATCH]   my sys._base_executable={getattr(sys, '_base_executable', None)!r}", flush=True)
        print(f"[PATCH]   python_exe (get_executable)={python_exe!r}", flush=True)
        print(f"[PATCH]   WINENV={winenv}, _path_eq={path_eq}", flush=True)
        print(f"[PATCH]   process_name={process_obj.name!r}", flush=True)
        print(f"[PATCH]   STACK (bottom 10):", flush=True)
        traceback.print_stack(file=sys.stdout, limit=10)
        return orig(self, process_obj)

    multiprocessing.popen_spawn_win32.Popen.__init__ = patched
    print("[PATCH] multiprocessing.popen_spawn_win32.Popen.__init__ patched", flush=True)

_patch_popen_spawn_win32()


# patch subprocess.Popen 抓所有 subprocess
import subprocess
orig_popen = subprocess.Popen.__init__

def patched_popen(self, args, *more_args, **kwargs):
    try:
        if args:
            cmd0 = args[0] if isinstance(args, (list, tuple)) else args
            cmd_str = str(cmd0)[:200] if cmd0 else ""
            if "python" in cmd_str.lower() or "D:\\" in cmd_str or "Work" in cmd_str:
                print(f"\n[PATCH] subprocess.Popen called!", flush=True)
                print(f"[PATCH]   args={args[:3]!r}", flush=True)
                print(f"[PATCH]   kwargs.executable={kwargs.get('executable')!r}", flush=True)
                print(f"[PATCH]   my sys.executable={sys.executable!r}", flush=True)
                print(f"[PATCH]   STACK (bottom 8):", flush=True)
                traceback.print_stack(file=sys.stdout, limit=8)
    except Exception:
        pass
    return orig_popen(self, args, *more_args, **kwargs)

subprocess.Popen.__init__ = patched_popen
print("[PATCH] subprocess.Popen.__init__ patched", flush=True)


# 现在跑真实 bot.py
print(f"\n[RUN] launching bot.py as subprocess via Popen (using sys.executable)", flush=True)
print(f"[RUN] sys.executable={sys.executable!r}", flush=True)
proc = subprocess.Popen(
    [sys.executable, "-X", "utf8", str(Path(__file__).parent / "bot.py")],
    cwd=str(Path(__file__).parent),
    stdout=sys.stdout, stderr=sys.stderr,
)
proc.wait()
