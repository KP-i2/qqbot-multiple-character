@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0"

REM 自动查找 NapCat 目录（支持任意版本号）
set "NAPCAT="
for /d %%D in ("%ROOT%qqbot\napcat\NapCat.*.Shell") do (
    if exist "%%D\NapCatWinBootMain.exe" set "NAPCAT=%%D"
)

if not exist "%ROOT%skill_qqbot\Scripts\python.exe" (
    echo [ERROR] Virtual env not found. Run setup.bat first.
    pause
    exit /b 1
)
if not defined NAPCAT (
    echo [ERROR] NapCat not found in qqbot\napcat\NapCat.*.Shell
    echo   请下载 NapCat 解压到 qqbot\napcat\ 目录
    pause
    exit /b 1
)
if not exist "%ROOT%qqbot\.env" (
    echo [ERROR] .env not found. Run setup.bat first.
    pause
    exit /b 1
)

call "%ROOT%skill_qqbot\Scripts\activate.bat"

echo [1/3] Starting NoneBot2...
cd /d "%ROOT%qqbot"
start "NoneBot2" /MIN python -X utf8 bot.py

echo [2/3] Waiting for NoneBot2 (4s)...
timeout /t 4 /nobreak >nul

echo [3/3] Starting NapCat...
cd /d "%NAPCAT%"

set /p QQ_NUM="Enter QQ number (or press Enter to QR login): "
if not "%QQ_NUM%"=="" (
    echo Quick login: %QQ_NUM%
    .\NapCatWinBootMain.exe %QQ_NUM%
) else (
    echo QR login mode
    .\NapCatWinBootMain.exe
)

pause
