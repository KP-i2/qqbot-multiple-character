@echo off
REM ============================================================
REM   start_all.bat - DEPRECATED 2026-07-01
REM
REM   The project migrated from NapCat desktop launcher to
REM   SnowLuma + Dashboard unified management.
REM
REM   Use this instead:
REM     1. Double-click manage.bat (or desktop "QQ Bot" shortcut)
REM        -> Dashboard opens at http://localhost:8501
REM     2. In Dashboard: click "Start" for bot and snowluma
REM     3. Scan-login QQ in SnowLuma WebUI (http://localhost:5099)
REM
REM   This file is kept as a hint for users with old habits.
REM ============================================================

setlocal
echo ============================================
echo   start_all.bat is DEPRECATED (since 2026-07-01).
echo ============================================
echo.
echo   The project now uses SnowLuma + Dashboard.
echo   No more manual start of NapCat desktop launcher.
echo.
echo   Migration instructions:
echo     1. Edit qqbot\.env (set DEEPSEEK_API_KEY)
echo     2. Double-click manage.bat
echo     3. Dashboard opens at http://localhost:8501
echo     4. Click "Start" on the bot and snowluma cards
echo.
echo   For full instructions: see README.md
echo ============================================
echo.
pause
