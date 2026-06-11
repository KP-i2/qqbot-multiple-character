@echo off
chcp 65001 >nul
echo ============================================
echo   NoneBot2 启动器（如需同时启动请运行 start_all.bat）
echo ============================================
echo.

REM 激活虚拟环境
call "%~dp0..\skill_qqbot\Scripts\activate.bat"

if not exist .env (
    echo [错误] 未找到 .env，请复制 .env.example 并编辑
    pause
    exit /b 1
)

echo 正在启动 NoneBot2...
python -X utf8 bot.py
pause
