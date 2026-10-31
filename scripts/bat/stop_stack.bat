@echo off
chcp 65001 >nul
REM 命令行入口：双击也可以，但更推荐桌面「停止 QQ Bot」快捷方式。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_stack.ps1" %*
