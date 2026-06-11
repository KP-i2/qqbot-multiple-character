@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "ROOT=%~dp0"

REM 自动检测 NapCat 目录（支持任意版本号）
set "NAPCAT="
for /d %%D in ("%ROOT%qqbot\napcat\NapCat.*.Shell") do (
    if exist "%%D\NapCatWinBootMain.exe" set "NAPCAT=%%D"
)

if not exist "%ROOT%skill_qqbot\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境，请先运行 setup.bat
    pause
    exit /b 1
)
if not defined NAPCAT (
    echo [错误] 未找到 NapCat: qqbot\napcat\NapCat.*.Shell
    echo   请下载 NapCat 并解压到 qqbot\napcat\ 目录
    pause
    exit /b 1
)
if not exist "%ROOT%qqbot\.env" (
    echo [错误] 未找到 .env 配置文件，请先运行 setup.bat
    pause
    exit /b 1
)

call "%ROOT%skill_qqbot\Scripts\activate.bat"

echo [1/3] 启动 NoneBot2...
cd /d "%ROOT%qqbot"
start "NoneBot2" /MIN python -X utf8 bot.py

echo [2/3] 等待 NoneBot2 就绪 (4s)...
timeout /t 4 /nobreak >nul

echo [3/3] 启动 NapCat...
cd /d "%NAPCAT%"

set /p QQ_NUM="输入 QQ 号（直接回车则扫码登录）: "
if not "%QQ_NUM%"=="" (
    echo 快速登录: %QQ_NUM%
    .\NapCatWinBootMain.exe %QQ_NUM%
) else (
    echo 扫码登录模式
    .\NapCatWinBootMain.exe
)

pause
