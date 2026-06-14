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

REM 创建 QQ Bot 一键启动快捷方式
if defined NAPCAT_EXE (
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot.lnk'); $s.TargetPath = '%SCRIPT_DIR%start.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'QQ Bot 一键启动'; $s.IconLocation = '%NAPCAT_EXE%,0'; $s.Save()"
) else (
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot.lnk'); $s.TargetPath = '%SCRIPT_DIR%start.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'QQ Bot 一键启动'; $s.Save()"
)

echo.
echo Created: QQ Bot (desktop shortcut)
echo.
pause
