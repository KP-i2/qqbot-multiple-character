@echo off
chcp 65001 >nul
echo Creating desktop shortcut...

set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\QQ Bot Dashboard.lnk'); $s.TargetPath = '%SCRIPT_DIR%dashboard_silent.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'QQ Bot Dashboard'; $s.IconLocation = '%SCRIPT_DIR%qqbot\napcat\NapCat.44498.Shell\QQ.exe,0'; $s.Save()"

echo Done! Check your desktop.
pause
