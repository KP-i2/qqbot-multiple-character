@echo off
REM ============================================================
REM   setup_snowluma.bat - Write snowluma OneBot config
REM
REM   Writes a OneBot v11 reverse WebSocket config so snowluma
REM   connects to NoneBot2 at ws://127.0.0.1:8080/onebot/v11/ws
REM
REM   Migration note (2026-07-01):
REM     SnowLuma config schema (networks/accessToken/wsClients)
REM     differs from NapCat. Template at qqbot/snowluma_onebot_config.example.json
REM ============================================================

setlocal enabledelayedexpansion

echo ============================================
echo   snowluma OneBot config writer
echo ============================================
echo.
echo   Connects snowluma to NoneBot2 (reverse WebSocket)
echo   Target: ws://127.0.0.1:8080/onebot/v11/ws
echo.

set "ROOT=%~dp0"

REM Auto-detect snowluma directory (supports multiple versions)
REM %~dp0 是 scripts\bat\, snowluma 在项目根目录, 回退两级
set "SNOWLUMA="
for /d %%D in ("%ROOT%..\..\snowluma\SnowLuma*") do (
    if exist "%%D\node.exe" set "SNOWLUMA=%%D"
)

if not defined SNOWLUMA (
    echo [ERROR] snowluma not found at %ROOT%..\..\snowluma\SnowLuma*
    echo   Please extract snowluma into snowluma\ directory
    pause
    exit /b 1
)

echo   snowluma dir: %SNOWLUMA%

REM Config dir (snowluma creates it on first run)
set "CFG=%SNOWLUMA%\config"
if not exist "%CFG%" mkdir "%CFG%" >nul 2>&1
echo   config dir: %CFG%

REM Find existing onebot_<QQ>.json: 已配置就直接跳过（不覆盖真值配置）
set "TARGET="
for %%F in ("%CFG%\onebot_*.json") do set "TARGET=%%F"

if not "%TARGET%"=="" (
    echo   existing config found: %TARGET%
    echo   [SKIP] 已存在配置，不覆盖。若要重新生成，先删掉该文件。
    goto :done
)

set /p QQ="Enter QQ number (or press Enter for scan-login): "
set "TARGET=%CFG%\onebot_!QQ!.json"
echo   config file: !TARGET!

REM Copy template (v2.x schema; 模板在 qqbot/snowluma_onebot_config.example.json)
set "TPL=%ROOT%..\..\qqbot\snowluma_onebot_config.example.json"
copy /y "%TPL%" "%TARGET%" >nul
if %errorlevel% neq 0 (
    echo [ERROR] Write failed. Template: %TPL%
    pause
    exit /b 1
)

:done

echo.
echo [OK] Done. (existing config kept / template written)
echo   Start snowluma via Dashboard (recommended):
echo     1. Double-click manage.bat (opens Dashboard at http://localhost:8501)
echo     2. Click "Start" on the snowluma card
echo     3. Open SnowLuma WebUI http://localhost:5099 to scan-login QQ
echo.
echo   OR start manually: double-click snowluma\SnowLuma-*\launcher.bat
echo.
pause
