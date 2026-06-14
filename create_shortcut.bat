@echo off
chcp 65001 >nul
echo Creating desktop shortcuts...

set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"

REM 自动检测 NapCat 目录（支持任意版本号）
set "NAPCAT_EXE="
for /d %%D in ("%SCRIPT_DIR%qqbot\napcat\NapCat.*.Shell") do (
    if exist "%%D\QQ.exe" set "NAPCAT_EXE=%%D\QQ.exe"
)

REM 1. Dashboard 快捷方式
if defined NAPCAT_EXE (
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot Dashboard.lnk'); $s.TargetPath = '%SCRIPT_DIR%dashboard_silent.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'QQ Bot Dashboard'; $s.IconLocation = '%NAPCAT_EXE%,0'; $s.Save()"
) else (
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot Dashboard.lnk'); $s.TargetPath = '%SCRIPT_DIR%dashboard_silent.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'QQ Bot Dashboard'; $s.Save()"
)

REM 2. 启动全部 快捷方式
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot Start.lnk'); $s.TargetPath = '%SCRIPT_DIR%start_all.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = '启动 Bot + NapCat'; $s.Save()"

REM 3. 查看状态 快捷方式
if exist "%SCRIPT_DIR%skill_qqbot\Scripts\python.exe" (
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot Status.lnk'); $s.TargetPath = '%SCRIPT_DIR%skill_qqbot\Scripts\python.exe'; $s.Arguments = 'manage.py status'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = '查看 QQ Bot 服务状态'; $s.Save()"
)

echo.
echo Created shortcuts:
echo   - QQ Bot Dashboard  (打开管理面板)
echo   - QQ Bot Start      (启动 Bot + NapCat)
echo   - QQ Bot Status     (查看服务状态)
echo.
pause
