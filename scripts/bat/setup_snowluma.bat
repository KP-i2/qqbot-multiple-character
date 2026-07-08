@echo off
REM ============================================================
REM   setup_snowluma.bat - Write SnowLuma OneBot config
REM
REM   Writes a OneBot v11 reverse WebSocket config so SnowLuma
REM   connects to NoneBot2 at ws://127.0.0.1:8080/onebot/v11/ws
REM
REM   Run from project root:
REM     scripts\bat\setup_snowluma.bat
REM ============================================================

setlocal enabledelayedexpansion

REM Switch to project root (this bat lives in scripts\bat\)
cd /d "%~dp0\..\.."

echo ============================================
echo   SnowLuma OneBot config writer
echo ============================================
echo.
echo   Connects SnowLuma to NoneBot2 (reverse WebSocket)
echo   Target: ws://127.0.0.1:8080/onebot/v11/ws
echo.

REM Auto-detect SnowLuma directory (first match wins; supports multiple versions)
set "SNOWLUMA="
for /d %%D in ("snowluma\SnowLuma*") do (
    if not defined SNOWLUMA if exist "%%D\node.exe" set "SNOWLUMA=%%D"
)

if not defined SNOWLUMA (
    echo [ERROR] SnowLuma not found at snowluma\SnowLuma*
    echo   Please extract SnowLuma into snowluma\ directory
    pause
    exit /b 1
)

echo   SnowLuma dir: %SNOWLUMA%

REM Config dir (SnowLuma creates it on first run)
set "CFG=%SNOWLUMA%\config"
if not exist "%CFG%" mkdir "%CFG%" >nul 2>&1
echo   config dir: %CFG%

REM Find existing onebot_<QQ>.json or ask for QQ number
set "TARGET="
for %%F in ("%CFG%\onebot_*.json") do set "TARGET=%%F"

if "%TARGET%"=="" (
    set /p QQ="Enter QQ number (or press Enter for scan-login): "
    set "TARGET=%CFG%\onebot_!QQ!.json"
    echo   config file: !TARGET!
) else (
    echo   config file: %TARGET%
)

REM Copy template
copy /y "qqbot\snowluma_onebot_config.example.json" "%TARGET%" >nul
if %errorlevel% neq 0 (
    echo [ERROR] Write failed
    pause
    exit /b 1
)

echo.
echo [OK] Config written.
echo   Start SnowLuma via Dashboard (recommended):
echo     1. Double-click manage.bat (opens Dashboard at http://localhost:8501)
echo     2. Click "Start" on the SnowLuma card
echo     3. Open SnowLuma WebUI http://localhost:5099 to scan-login QQ
echo.
echo   OR start manually: double-click snowluma\SnowLuma-*\launcher.bat
echo.
pause