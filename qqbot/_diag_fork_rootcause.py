"""
临时诊断脚本 — 定位 anaconda fork 根因
2026-07-23 临时,排查完删除

打印每个关键节点的 sys.executable + sys._base_executable,以及 os.environ 里的 PYTHON 相关
"""
import os
import sys
from pathlib import Path


def dump(label):
    print(f"[{label}] sys.executable={sys.executable!r}")
    print(f"[{label}] sys._base_executable={getattr(sys, '_base_executable', None)!r}")
    for k, v in os.environ.items():
        if "PYTHON" in k.upper() or "VIRTUAL" in k.upper() or k == "PATH":
            print(f"[{label}] env {k}={v[:200]!r}")


print("=" * 60)
print("STEP 1: 脚本入口 (venv 启动, sys.executable 应该是 venv)")
print("=" * 60)
dump("step1")

print()
print("=" * 60)
print("STEP 2: 跑 venv 现有的 patch")
print("=" * 60)
if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    print(f"patch: _base_executable != executable, applying patch")
    sys._base_executable = sys.executable
else:
    print(f"patch: no-op (_base_executable={getattr(sys, '_base_executable', None)}, executable={sys.executable})")
dump("step2")

print()
print("=" * 60)
print("STEP 3: 加载 .env")
print("=" * 60)
_PRIVATE_ENV = Path(__file__).resolve().parent.parent / "private" / ".env"
if _PRIVATE_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(_PRIVATE_ENV)
    print("loaded .env")
else:
    print("no .env")
dump("step3")

print()
print("=" * 60)
print("STEP 4: import nonebot")
print("=" * 60)
import nonebot  # noqa
dump("step4")

print()
print("=" * 60)
print("STEP 5: import nonebot.drivers.fastapi")
print("=" * 60)
from nonebot.drivers.fastapi import Driver  # noqa
dump("step5")

print()
print("=" * 60)
print("STEP 6: import uvicorn")
print("=" * 60)
import uvicorn  # noqa
dump("step6")

print()
print("=" * 60)
print("STEP 7: import anyio / anyio.to_process")
print("=" * 60)
import anyio  # noqa
import anyio.to_process  # noqa
dump("step7")

print()
print("=" * 60)
print("STEP 8: 检查 multiprocessing 启动方式")
print("=" * 60)
import multiprocessing
print(f"default start method = {multiprocessing.get_start_method()}")
print(f"default ctx = {multiprocessing.get_context()!r}")
dump("step8")

print()
print("=" * 60)
print("DONE. If any 'sys.executable' is anaconda path, that's the root cause.")
print("=" * 60)
