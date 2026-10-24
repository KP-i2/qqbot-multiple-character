"""
最终 diag: 用 __main__ 保护, 让子进程真的只跑 f() 函数
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


def child_proc():
    print(f"[child pid={os.getpid()}] sys.executable={sys.executable!r}", flush=True)
    print(f"[child pid={os.getpid()}] sys._base_executable={getattr(sys, '_base_executable', None)!r}", flush=True)
    print(f"[child pid={os.getpid()}] __PYVENV_LAUNCHER__={os.environ.get('__PYVENV_LAUNCHER__')!r}", flush=True)
    print(f"[child pid={os.getpid()}] sys.path[0:5]={sys.path[:5]}", flush=True)


if __name__ == "__main__":
    print(f"[parent pid={os.getpid()}] sys.executable={sys.executable!r}", flush=True)
    print(f"[parent pid={os.getpid()}] sys._base_executable={getattr(sys, '_base_executable', None)!r}", flush=True)
    print(f"[parent pid={os.getpid()}] sys.path[0:5]={sys.path[:5]}", flush=True)

    import multiprocessing
    ctx = multiprocessing.get_context('spawn')
    p = ctx.Process(target=child_proc, name="test_child")
    p.start()
    p.join(timeout=5)
    print(f"[parent] child exitcode = {p.exitcode}")
