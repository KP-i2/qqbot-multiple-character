"""
最小 NoneBot 测试 — 验证 fork 行为
2026-07-23 临时
"""
import os
import sys
import time
from pathlib import Path

# patch (跟 bot.py 一致)
if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    sys._base_executable = sys.executable

# .env
_PRIVATE_ENV = Path(__file__).resolve().parent.parent / "private" / ".env"
if _PRIVATE_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(_PRIVATE_ENV)

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from fastapi import FastAPI
import uvicorn

print(f"[diag] BEFORE run: sys.executable={sys.executable!r}", flush=True)
print(f"[diag] BEFORE run: parent_pid={os.getppid()}, pid={os.getpid()}", flush=True)


# 拿 NoneBot init 后立刻跑
nonebot.init(_env_file=str(_PRIVATE_ENV) if _PRIVATE_ENV.exists() else None)
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
# 故意不 load plugin,避免 plugin startup hook 跑任何东西

print(f"[diag] AFTER init: sys.executable={sys.executable!r}", flush=True)


# 启动一个简单的 uvicorn server
async def hello():
    return "hi"

app = FastAPI()
app.add_api_route("/hello", hello)

print(f"[diag] BEFORE uvicorn.run: sys.executable={sys.executable!r}", flush=True)
print(f"[diag] before uvicorn.run: children? trying to detect fork", flush=True)

# 启动 uvicorn 在后台 thread
import threading
def start_uvicorn():
    try:
        uvicorn.run(app, host="127.0.0.1", port=18080, log_level="info")
    except Exception as e:
        print(f"[diag] uvicorn.run failed: {e}", flush=True)

t = threading.Thread(target=start_uvicorn, daemon=True)
t.start()
time.sleep(3)
print(f"[diag] AFTER 3s: sys.executable={sys.executable!r}", flush=True)

# 列出所有 python 进程
import subprocess
ps = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process python* -ErrorAction SilentlyContinue | Select-Object Id, @{N='Exe';E={(Get-CimInstance Win32_Process -Filter \\\"ProcessId=$($_.Id)\\\").CommandLine}} | Format-Table -AutoSize"], capture_output=True, text=True)
print(ps.stdout, flush=True)

# 退出
os._exit(0)
