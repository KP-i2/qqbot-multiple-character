@echo off
REM ============================================================
REM   Setup script - QQ Bot (SnowLuma + NoneBot2)
REM
REM   Migration notes:
REM     2026-07-01 - Old NapCat desktop QQ launcher archived to archive/
REM                  Project now uses SnowLuma (node.exe based)
REM                  Bot / SnowLuma managed by Dashboard (manage.bat shortcut)
REM     2026-07-23 - Switched from python venv (skill_qqbot/) to anaconda conda env (botenv).
REM                  venv launcher forwards to base python (D:\Work\anaconda3\python.exe),
REM                  which made uvicorn watchfiles spawn anaconda workers.
REM                  conda env's python is a real standalone interpreter.
REM ============================================================

setlocal

echo ============================================
echo   Setup - QQ Bot
echo ============================================
echo.

REM ========== 1. Check conda ==========
echo [1/5] Checking conda (anaconda)...
set "CONDA_EXE=D:\Work\anaconda3\Scripts\conda.exe"
if not exist "%CONDA_EXE%" (
    echo   [ERROR] conda not found at %CONDA_EXE%
    echo   Please install Anaconda / Miniconda first.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('"%CONDA_EXE%" --version 2^>^&1') do echo   %%v

REM ========== 2. Create botenv (conda env) ==========
echo.
echo [2/5] Creating conda env 'botenv' (Python 3.12)...
if exist "D:\Work\anaconda3\envs\botenv\python.exe" (
    echo   botenv already exists, skipping
) else (
    "%CONDA_EXE%" create -n botenv python=3.12 -y --override-channels -c defaults
    if %errorlevel% neq 0 (
        echo   [ERROR] Failed to create botenv
        pause
        exit /b 1
    )
    echo   Created
)

REM ========== 3. Install pip deps ==========
echo.
echo [3/5] Installing Python dependencies into botenv...
set "BOTENV_PY=D:\Work\anaconda3\envs\botenv\python.exe"
"%BOTENV_PY%" -m pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo   [ERROR] pip install failed
    pause
    exit /b 1
)
echo   Done

REM Install Playwright Chromium for Weibo fetcher
echo.
echo   Installing Playwright Chromium (for weibo fetcher)...
"D:\Work\anaconda3\envs\botenv\Scripts\playwright.exe" install chromium -q 2>nul
if %errorlevel% neq 0 (
    echo   [INFO] Playwright install may have failed (likely already installed).
    echo   To re-run manually: D:\Work\anaconda3\envs\botenv\Scripts\playwright install chromium
)

REM ========== 4. Create private/.env ==========
REM 实际生效的配置文件在 private/.env（bot.py 硬编码读取 private/.env）
echo.
echo [4/5] Setting up private/.env...
if not exist "private\.env" (
    if not exist "private" mkdir "private"
    copy "qqbot\.env.example" "private\.env" >nul 2>&1
    echo   Created private\.env from template
    echo   [!!] Edit private\.env to set DEEPSEEK_API_KEY etc.
) else (
    echo   private\.env already exists
)

REM ========== 5. Check SnowLuma ==========
REM SnowLuma 安装在项目根目录 snowluma\（%~dp0 是 scripts\bat\, 要回退两级）
echo.
echo [5/5] Checking SnowLuma...
set "SNOW_DIR="
for /d %%D in ("%~dp0..\..\snowluma\SnowLuma-*") do set "SNOW_DIR=%%D"
if defined SNOW_DIR (
    echo   SnowLuma: installed [%%SNOW_DIR%%]
) else (
    echo   SnowLuma: NOT installed
    echo     1. Download SnowLuma-v*-win-x64.zip from
    echo        https://github.com/MaaYYDS/SnowLuma/releases
    echo     2. Extract into project-root snowluma\ (so snowluma\SnowLuma-v2.2.0-win-x64\ exists)
)

echo.
echo ============================================
echo   Setup complete.
echo ============================================
echo.
echo   Next steps:
echo     1. Edit private\.env (set DEEPSEEK_API_KEY etc.)
echo     2. Double-click manage.bat (or desktop "QQ Bot" shortcut)
echo     3. Dashboard opens at http://localhost:8501
echo     4. In Dashboard: start bot + start snowluma
echo     5. Open SnowLuma WebUI at http://localhost:5099 to scan-login QQ
echo.
echo   See README.md for full guide.
echo.
pause
