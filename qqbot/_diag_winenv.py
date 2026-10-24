"""
Diag: 在 patch 之后, 看 WINENV 和 _base_executable
2026-07-23 临时
"""
import sys
import os
from pathlib import Path

# 跟 bot.py 一样的 patch
if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    sys._base_executable = sys.executable

_PRIVATE_ENV = Path(__file__).resolve().parent.parent / "private" / ".env"
if _PRIVATE_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(_PRIVATE_ENV)

print("=" * 60)
print("STEP 1: 跟 bot.py 一样的 patch 之后")
print("=" * 60)
print(f"sys.executable = {sys.executable!r}")
print(f"sys._base_executable = {getattr(sys, '_base_executable', None)!r}")
print(f"WINENV (未 import popen_spawn_win32) = ?")

# 关键: 在 import popen_spawn_win32 之前 patch 看看
import multiprocessing.popen_spawn_win32 as p
print()
print("=" * 60)
print("STEP 2: import multiprocessing.popen_spawn_win32 之后")
print("=" * 60)
print(f"sys.executable = {sys.executable!r}")
print(f"sys._base_executable = {getattr(sys, '_base_executable', None)!r}")
print(f"p.WINENV = {p.WINENV}")
print(f"p._path_eq(exe, base) = {p._path_eq(sys.executable, sys._base_executable)}")
print(f"p.get_executable() = {p.spawn.get_executable()!r}")
print()

# 现在 import multiprocessing 完整, 看看 _python_exe
import multiprocessing.spawn
print(f"multiprocessing.spawn._python_exe = {multiprocessing.spawn._python_exe!r}")
print()

# 试着 fork 一个, 看看实际是什么
import multiprocessing
def f():
    print(f"[child] sys.executable = {sys.executable!r}")
    print(f"[child] __PYVENV_LAUNCHER__ = {os.environ.get('__PYVENV_LAUNCHER__')!r}")
    return sys.executable

print("=" * 60)
print("STEP 3: 实际 fork 一个进程看看")
print("=" * 60)
print(f"my sys.executable = {sys.executable!r}")
print(f"my sys._base_executable = {getattr(sys, '_base_executable', None)!r}")
ctx = multiprocessing.get_context('spawn')
p = ctx.Process(target=f)
p.start()
p.join()
print(f"child returncode = {p.returncode}")
print(f"child exitcode via exitcode = {p.exitcode}")
