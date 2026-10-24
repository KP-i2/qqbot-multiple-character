@echo off
chcp 65001 >nul 2>&1
setlocal
set "ROOT=%~dp0"

REM 2026-07-23: 改用 anaconda conda env (botenv) — 真独立 python, 避免 venv launcher 转发到 base python
set "BOTENV_PYW=D:\Work\anaconda3\envs\botenv\pythonw.exe"

if not exist "%BOTENV_PYW%" (
    echo [错误] 未找到 botenv: %BOTENV_PYW%
    echo 创建方法: conda create -n botenv python=3.12 -y ^&^& pip install -r requirements.txt
    pause
    exit /b 1
)

cd /d "%ROOT%"
echo Starting Dashboard (botenv)...
start "Dashboard" /MIN "%BOTENV_PYW%" -m uvicorn qqbot.dashboard.main:app --host 0.0.0.0 --port 8501
timeout /t 3 /nobreak >nul
start http://localhost:8501
