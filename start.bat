@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0"

echo.
echo  ==========================================
echo    QQ Bot 一键启动
echo  ==========================================
echo.

REM ── 检查 Python 环境 ──
if not exist "%ROOT%skill_qqbot\Scripts\python.exe" (
    echo [!] 未找到虚拟环境，请先运行 setup.bat
    pause
    exit /b 1
)

REM ── 自动检测 NapCat 目录 ──
set "NAPCAT="
for /d %%D in ("%ROOT%qqbot\napcat\NapCat.*.Shell") do (
    if exist "%%D\NapCatWinBootMain.exe" set "NAPCAT=%%D"
)

REM ── 激活虚拟环境 ──
call "%ROOT%skill_qqbot\Scripts\activate.bat"

REM ── 检查 Bot 是否已在运行 ──
echo [1/4] 检查服务状态...
set "BOT_RUNNING=0"
set "DASH_RUNNING=0"

netstat -ano 2>nul | findstr ":8080.*LISTENING" >nul && set "BOT_RUNNING=1"
netstat -ano 2>nul | findstr ":8501.*LISTENING" >nul && set "DASH_RUNNING=1"

if "!BOT_RUNNING!"=="1" (
    echo       Bot: 已运行 (port 8080)
) else (
    echo       Bot: 未运行
)

if "!DASH_RUNNING!"=="1" (
    echo       Dashboard: 已运行 (port 8501)
) else (
    echo       Dashboard: 未运行
)

REM ── 启动 Bot + NapCat ──
if "!BOT_RUNNING!"=="0" (
    if not defined NAPCAT (
        echo [!] 未找到 NapCat: qqbot\napcat\NapCat.*.Shell
        echo     请下载 NapCat 并解压到 qqbot\napcat\ 目录
        echo     跳过 Bot 启动，仅启动 Dashboard
    ) else (
        if not exist "%ROOT%qqbot\.env" (
            echo [!] 未找到 .env 配置文件，请先运行 setup.bat
            pause
            exit /b 1
        )
        echo.
        echo [2/4] 启动 Bot...
        cd /d "%ROOT%qqbot"
        start "NoneBot2" /MIN python -X utf8 bot.py
        echo       Bot 启动中，等待端口就绪...
        timeout /t 5 /nobreak >nul

        echo [3/4] 启动 NapCat...
        cd /d "%NAPCAT%"
        set /p QQ_NUM="      输入 QQ 号（直接回车则扫码登录）: "
        if not "!QQ_NUM!"=="" (
            echo       快速登录: !QQ_NUM!
            start "" .\NapCatWinBootMain.exe !QQ_NUM!
        ) else (
            echo       扫码登录模式
            start "" .\NapCatWinBootMain.exe
        )
        cd /d "%ROOT%"
    )
) else (
    echo.
    echo [2/4] Bot 已在运行，跳过
    echo [3/4] NapCat 跳过
)

REM ── 启动 Dashboard ──
echo.
if "!DASH_RUNNING!"=="0" (
    echo [4/4] 启动 Dashboard...
    cd /d "%ROOT%"
    start "Dashboard" /MIN python -m uvicorn qqbot.dashboard.main:app --host 0.0.0.0 --port 8501
    timeout /t 3 /nobreak >nul
    echo       Dashboard 已启动
) else (
    echo [4/4] Dashboard 已在运行，跳过
)

REM ── 打开浏览器 ──
start http://localhost:8501

echo.
echo  ==========================================
echo    启动完成！浏览器已打开
echo    Dashboard: http://localhost:8501
echo    关闭此窗口不影响服务运行
echo  ==========================================
echo.
pause
