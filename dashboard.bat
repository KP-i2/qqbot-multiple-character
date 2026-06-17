@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
call "%ROOT%skill_qqbot\Scripts\activate.bat"
cd /d "%ROOT%"
echo Starting Dashboard...
start "Dashboard" /MIN python -m uvicorn qqbot.dashboard.main:app --host 0.0.0.0 --port 8501
timeout /t 3 /nobreak >nul
start http://localhost:8501
