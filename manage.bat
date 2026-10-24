@echo off
REM   QQ Bot one-click launcher
REM   Desktop shortcut "QQ Bot.lnk" points here.
REM   Starts Dashboard + SnowLuma + NoneBot2, then opens the dashboard.
REM   Set QQBOT_NO_BROWSER=1 to skip opening the browser (for scripts/tests).
REM   See: qqbot\logs\

setlocal
set "ROOT=%~dp0"
set "PS1=%ROOT%scripts\bat\start_stack.ps1"

if not exist "%PS1%" (
    echo [!] start_stack.ps1 not found: "%PS1%"
    pause
    exit /b 1
)

if "%QQBOT_NO_BROWSER%"=="1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -NoBrowser
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [!] startup failed, see qqbot\logs\dashboard.err / dashboard.log
    pause
)
exit /b %RC%
