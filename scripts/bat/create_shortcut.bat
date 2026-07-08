@echo off
chcp 65001 >nul
echo Creating desktop shortcut...
echo.

setlocal
set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$scriptDir = '%SCRIPT_DIR%'.TrimEnd('\\');" ^
  "$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir);" ^
  "$startBat = Join-Path $projectRoot 'manage.bat';" ^
  "$iconFile = Join-Path $projectRoot 'assets\icon\icon.ico';" ^
  "$shortcutPath = Join-Path '%DESKTOP%' 'QQ Bot.lnk';" ^
  "Write-Host ('Project root: ' + $projectRoot);" ^
  "Write-Host ('Start script: ' + $startBat);" ^
  "Write-Host ('Icon:         ' + $iconFile);" ^
  "Write-Host ('Shortcut:     ' + $shortcutPath);" ^
  "Write-Host '';" ^
  "if (-not (Test-Path $startBat)) { Write-Host '[ERROR] manage.bat not found' -ForegroundColor Red; exit 1 };" ^
  "if (Test-Path $shortcutPath) { Remove-Item -Path $shortcutPath -Force; Write-Host 'Removed old shortcut' };" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut($shortcutPath);" ^
  "$s.TargetPath = $startBat;" ^
  "$s.WorkingDirectory = $projectRoot;" ^
  "$s.Description = 'QQ Bot dashboard yijian qidong';" ^
  "$s.WindowStyle = 1;" ^
  "if (Test-Path $iconFile) { $s.IconLocation = ($iconFile + [char]44 + '0'); Write-Host ('Using icon: ' + $iconFile) } else { Write-Host '[WARN] icon.ico not found in assets/icon/' -ForegroundColor Yellow };" ^
  "$s.Save();" ^
  "if (Test-Path $shortcutPath) { Write-Host ''; Write-Host ('[OK] Created: ' + $shortcutPath) -ForegroundColor Green } else { Write-Host '[ERROR] Failed' -ForegroundColor Red; exit 1 }"

endlocal
echo.
pause
