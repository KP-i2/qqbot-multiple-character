@echo off
REM ============================================================
REM   Setup script - qqbot-multiple-character
REM
REM   - Detect Python 3.10+
REM   - Create skill_qqbot/ venv (project root)
REM   - Install pip dependencies from requirements.txt
REM   - Copy private\.env.example to private\.env (user fills API keys)
REM   - Check SnowLuma presence (manual install)
REM
REM   Run from project root:
REM     scripts\bat\setup.bat
REM ============================================================

setlocal

REM Switch to project root (this bat lives in scripts\bat\)
cd /d "%~dp0\..\.."

echo ============================================
echo   Setup - qqbot-multiple-character
echo ============================================
echo.

REM ========== 1. Check Python ==========
echo [1/5] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Python 3.10+ not found.
    echo   Install from https://www.python.org/downloads/
    echo   Make sure to check "Add Python to PATH".
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Python %%v OK

REM ========== 2. Create venv ==========
echo.
echo [2/5] Creating virtual environment...
if exist "skill_qqbot\Scripts\python.exe" (
    echo   skill_qqbot\ already exists, skipping
) else (
    python -m venv skill_qqbot
    if %errorlevel% neq 0 (
        echo   [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo   Created
)

REM ========== 3. Install pip deps ==========
echo.
echo [3/5] Installing Python dependencies...
call "skill_qqbot\Scripts\activate.bat"
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo   [ERROR] pip install failed
    pause
    exit /b 1
)
echo   Done

REM Install Playwright Chromium (only needed for Weibo fetcher; Twitter fetcher uses twikit pure HTTP)
echo.
echo   Installing Playwright Chromium (for weibo fetcher)...
playwright install chromium -q 2>nul
if %errorlevel% neq 0 (
    echo   [INFO] Playwright install may have failed (likely already installed).
    echo   To re-run manually: skill_qqbot\Scripts\playwright install chromium
)

REM ========== 4. Create .env ==========
echo.
echo [4/5] Setting up .env...
if not exist "private\.env" (
    if not exist "private\.env.example" (
        echo   [ERROR] private\.env.example not found
        pause
        exit /b 1
    )
    copy "private\.env.example" "private\.env" >nul 2>&1
    echo   Created private\.env from template
    echo   [!!] Edit private\.env to set DEEPSEEK_API_KEY and ADMIN_QQ
) else (
    echo   private\.env already exists
)

REM ========== 5. Check SnowLuma ==========
echo.
echo [5/5] Checking SnowLuma...
set "SNOW_DIR="
for /d %%D in ("snowluma\SnowLuma-*") do (
    if not defined SNOW_DIR set "SNOW_DIR=%%D"
)
if defined SNOW_DIR (
    echo   SnowLuma: installed [!SNOW_DIR!]
) else (
echo   SnowLuma: NOT installed
echo     1. Download SnowLuma.Shell.Windows.OneKey.zip from
echo        https://github.com/NapNeko/snowluma/releases
echo     2. Extract into snowluma\ (so snowluma\SnowLuma-v1.10.3-win-x64\ exists)
)

echo.
echo ============================================
echo   Setup complete.
echo ============================================
echo.
echo   Next steps:
echo     1. Edit private\.env (set DEEPSEEK_API_KEY, ADMIN_QQ, etc.)
echo     2. Run scripts\bat\setup_snowluma.bat (write SnowLuma reverse WS config)
echo     3. Double-click manage.bat (or desktop "QQ Bot" shortcut)
echo     4. Dashboard opens at http://localhost:8501
echo     5. In Dashboard: start bot + start snowluma
echo     6. Open SnowLuma WebUI at http://localhost:5099 to scan-login QQ
echo.
echo   See README.md and docs\INSTALL.md for full guide.
echo.
pause