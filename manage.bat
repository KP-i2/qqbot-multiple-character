@echo off
REM   QQ Bot dashboard one-click launcher
REM   Desktop shortcut "QQ Bot.lnk" is the only entry.
REM   This bat only calls scripts\bat\launch_dashboard.ps1.
REM   See: qqbot\logs\dashboard.log

setlocal
set "ROOT=%~dp0"
set "PS1=%ROOT%scripts\bat\launch_dashboard.ps1"

if not exist "%PS1%" (
    echo [!] launch_dashboard.ps1 not found: "%PS1%"
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
exit /b %ERRORLEVEL%
