@echo off
chcp 65001 >nul
echo Creating desktop shortcuts...
echo.

REM 2026-10-31: 统一由 create_shortcuts.ps1 创建启动 / 停止 / SnowLuma 三个快捷方式。
REM 快捷方式目标都是 powershell.exe（系统签名组件），不会触发「无法验证发布者」。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcuts.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] 创建失败，请看上面的输出。
  pause
  exit /b 1
)

echo.
pause
