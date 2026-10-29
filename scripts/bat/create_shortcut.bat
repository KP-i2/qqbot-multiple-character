@echo off
chcp 65001 >nul
echo Creating desktop shortcut...
echo.

setlocal
set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"

REM 2026-10-30: 快捷方式改为直接调用 powershell.exe（而不是 manage.bat）。
REM 原因：部分 Windows 机器双击无签名 .bat 会弹「无法验证发布者」安全警告；
REM 目标换成系统自带的 powershell.exe 就不会触发。
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$scriptDir = '%SCRIPT_DIR%'.TrimEnd('\\');" ^
  "$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir);" ^
  "$startPs1 = Join-Path $projectRoot 'scripts\bat\start_stack.ps1';" ^
  "$iconFile = Join-Path $projectRoot 'assets\icon\icon.ico';" ^
  "$shortcutPath = Join-Path '%DESKTOP%' 'QQ Bot.lnk';" ^
  "$powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe';" ^
  "Write-Host ('Project root: ' + $projectRoot);" ^
  "Write-Host ('Start script: ' + $startPs1);" ^
  "Write-Host ('Icon:         ' + $iconFile);" ^
  "Write-Host ('Shortcut:     ' + $shortcutPath);" ^
  "Write-Host '';" ^
  "if (-not (Test-Path $startPs1)) { Write-Host '[ERROR] start_stack.ps1 not found' -ForegroundColor Red; exit 1 };" ^
  "if (-not (Test-Path $powershellExe)) { Write-Host '[ERROR] powershell.exe not found' -ForegroundColor Red; exit 1 };" ^
  "if (Test-Path $shortcutPath) { Remove-Item -Path $shortcutPath -Force; Write-Host 'Removed old shortcut' };" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut($shortcutPath);" ^
  "$s.TargetPath = $powershellExe;" ^
  "$s.Arguments = '-NoProfile -ExecutionPolicy Bypass -File ""' + $startPs1 + '""';" ^
  "$s.WorkingDirectory = $projectRoot;" ^
  "$s.Description = 'QQ Bot one-click start (Dashboard + SnowLuma + NoneBot2)';" ^
  "$s.WindowStyle = 1;" ^
  "if (Test-Path $iconFile) { $s.IconLocation = ($iconFile + [char]44 + '0'); Write-Host ('Using icon: ' + $iconFile) } else { Write-Host '[WARN] icon.ico not found in assets/icon/' -ForegroundColor Yellow };" ^
  "$s.Save();" ^
  "if (Test-Path $shortcutPath) { Write-Host ''; Write-Host ('[OK] Created: ' + $shortcutPath) -ForegroundColor Green } else { Write-Host '[ERROR] Failed' -ForegroundColor Red; exit 1 }"

endlocal
echo.
pause
