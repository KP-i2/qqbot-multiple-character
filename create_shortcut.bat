@echo off
chcp 65001 >nul
echo Creating desktop shortcut...

set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"

REM 自动检测 NapCat 目录（支持任意版本号）
set "NAPCAT_EXE="
for /d %%D in ("%SCRIPT_DIR%qqbot\napcat\NapCat.*.Shell") do (
    if exist "%%D\QQ.exe" set "NAPCAT_EXE=%%D\QQ.exe"
)

if defined NAPCAT_EXE (
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot Dashboard.lnk'); $s.TargetPath = '%SCRIPT_DIR%dashboard_silent.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'QQ Bot Dashboard'; $s.IconLocation = '%NAPCAT_EXE%,0'; $s.Save()"
) else (
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot Dashboard.lnk'); $s.TargetPath = '%SCRIPT_DIR%dashboard_silent.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'QQ Bot Dashboard'; $s.Save()"
)

echo Done! Check your desktop.
pause
