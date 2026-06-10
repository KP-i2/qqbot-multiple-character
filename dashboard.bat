@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
set "VENV=%ROOT%skill_qqbot\Scripts\activate.bat"
set "DASH=%ROOT%qqbot\dashboard.py"

echo ============================================
echo   ✦ QQ Bot Dashboard
echo ============================================
echo.

if not exist "%VENV%" (
    echo [ERROR] Virtual env not found. Run setup.bat first.
    pause
    exit /b 1
)

call "%VENV%"

echo Starting dashboard on http://localhost:8501
echo Press Ctrl+C to stop
echo.

start "" http://localhost:8501
python -X utf8 "%DASH%"
pause
