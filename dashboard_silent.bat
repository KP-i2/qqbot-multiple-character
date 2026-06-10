@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
call "%ROOT%skill_qqbot\Scripts\activate.bat"
cd /d "%ROOT%qqbot"
start "Dashboard" /MIN python -X utf8 dashboard.py
timeout /t 2 /nobreak >nul
start http://localhost:8501
