@echo off
echo ============================================
echo   NoneBot2 单独启动（不含 NapCat）
echo ============================================
echo.

REM 激活根目录虚拟环境
call "%~dp0..\skill_qqbot\Scripts\activate.bat"

if not exist .env (
    echo [错误] 未找到 .env，请复制 .env.example 并配置
    pause
    exit /b 1
)

echo 启动 NoneBot2...
python -X utf8 bot.py
pause
