@echo off
chcp 65001 >nul
echo ============================================
echo   NoneBot2 启动器（如需同时启动请运行 start_all.bat）
echo ============================================
echo.

REM 2026-07-23: 改用 anaconda conda env (botenv) — 真独立 python, 避免 venv launcher 转发到 base python
set "BOTENV_PY=D:\Work\anaconda3\envs\botenv\python.exe"
if not exist "%BOTENV_PY%" (
    echo [错误] 未找到 botenv: %BOTENV_PY%
    echo 创建方法: conda create -n botenv python=3.12 -y ^&^& pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist .env (
    echo [错误] 未找到 .env，请复制 .env.example 并编辑
    pause
    exit /b 1
)

echo 正在启动 NoneBot2 (botenv)...
"%BOTENV_PY%" -X utf8 bot.py
pause
