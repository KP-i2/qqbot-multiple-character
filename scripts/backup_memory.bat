@echo off
chcp 65001 >nul
REM 备份记忆/对话数据（P2）
"D:\Work\anaconda3\envs\botenv\python.exe" "%~dp0backup_memory.py" %*
pause
