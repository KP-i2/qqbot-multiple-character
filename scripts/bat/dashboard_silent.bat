@echo off
chcp 65001 >nul 2>&1
setlocal
set "ROOT=%~dp0"

REM 统一用 venv 全路径，不依赖 activate.bat / PATH
set "VENV_PYW=%ROOT%skill_qqbot\Scripts\pythonw.exe"

if not exist "%VENV_PYW%" (
    echo [错误] 未找到虚拟环境，请先运行 setup.bat
    pause
    exit /b 1
)

cd /d "%ROOT%"
start "Dashboard" /MIN "%VENV_PYW%" -m uvicorn qqbot.dashboard.main:app --host 0.0.0.0 --port 8501
timeout /t 3 /nobreak >nul
start http://localhost:8501
