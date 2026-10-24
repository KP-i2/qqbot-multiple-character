"""
Diag — 在 NoneBot run() 前后立刻打印所有 python 进程对比
2026-07-23 临时
"""
import os
import sys
import time
import subprocess
from pathlib import Path

if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    sys._base_executable = sys.executable

_PRIVATE_ENV = Path(__file__).resolve().parent.parent / "private" / ".env"
if _PRIVATE_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(_PRIVATE_ENV)


def list_python(label):
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process python* -ErrorAction SilentlyContinue | Select-Object Id, ParentProcessId, ProcessName, @{N='Cmd';E={(Get-CimInstance Win32_Process -Filter \\\"ProcessId=$($_.Id)\\\").CommandLine.Substring(0,[Math]::Min(120,(Get-CimInstance Win32_Process -Filter \\\"ProcessId=$($_.Id)\\\").CommandLine.Length))}} | Format-Table -AutoSize -Wrap"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    print(f"=== {label} (my pid={os.getpid()}, sys.executable={sys.executable!r}) ===")
    print(ps.stdout, flush=True)


list_python("BEFORE import nonebot")

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from fastapi import FastAPI

print(f"[diag] AFTER import nonebot, sys.executable={sys.executable!r}", flush=True)

list_python("AFTER import nonebot")

# 不 load 任何 plugin, 启动最小 uvicorn
nonebot.init(_env_file=str(_PRIVATE_ENV) if _PRIVATE_ENV.exists() else None)
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

list_python("AFTER nonebot.init + register_adapter")

# 启动 uvicorn 在 thread
import threading
import uvicorn

def start_uv():
    uvicorn.run("D:\\agent_function\\skill_communication\\qqbot\\_diag_min_nonebot:app",
                host="127.0.0.1", port=18081, log_level="info", reload=False)

# 没 app 变量,改成简易:
async def hello():
    return "hi"

app = FastAPI()
app.add_api_route("/hello", hello)


def start_uv2():
    uvicorn.run(app, host="127.0.0.1", port=18081, log_level="info")

t = threading.Thread(target=start_uv2, daemon=True)
t.start()
time.sleep(2)
list_python("AFTER uvicorn.run in thread, 2s")
time.sleep(3)
list_python("AFTER 5s total")

os._exit(0)
