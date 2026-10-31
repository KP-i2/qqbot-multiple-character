# 创建桌面快捷方式：启动 / 停止 / SnowLuma
# 2026-10-31: 快捷方式统一指向 powershell.exe，避免无签名 .bat 触发「无法验证发布者」。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File create_shortcuts.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$desktop = [Environment]::GetFolderPath('Desktop')
$powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$startPs1 = Join-Path $scriptDir 'start_stack.ps1'
$stopPs1 = Join-Path $scriptDir 'stop_stack.ps1'
$iconFile = Join-Path $projectRoot 'assets\icon\icon.ico'

Write-Host ('Project root: ' + $projectRoot)
Write-Host ('Desktop:     ' + $desktop)
Write-Host ''

if (-not (Test-Path $powershellExe)) { throw "powershell.exe not found: $powershellExe" }
if (-not (Test-Path $startPs1)) { throw "start_stack.ps1 not found: $startPs1" }
if (-not (Test-Path $stopPs1)) { throw "stop_stack.ps1 not found: $stopPs1" }

$ws = New-Object -ComObject WScript.Shell

function New-Shortcut {
    param(
        [string]$Name,
        [string]$TargetPath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [string]$Description
    )
    $path = Join-Path $desktop $Name
    if (Test-Path $path) { Remove-Item $path -Force }
    $s = $ws.CreateShortcut($path)
    $s.TargetPath = $TargetPath
    $s.Arguments = $Arguments
    $s.WorkingDirectory = $WorkingDirectory
    $s.Description = $Description
    $s.WindowStyle = 1
    if (Test-Path $iconFile) { $s.IconLocation = ($iconFile + ',0') }
    $s.Save()
    if (-not (Test-Path $path)) { throw "failed to create shortcut: $path" }
    Write-Host ('[OK] ' + $path) -ForegroundColor Green
}

# 启动
New-Shortcut -Name 'QQ Bot.lnk' -TargetPath $powershellExe `
    -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + $startPs1 + '"') `
    -WorkingDirectory $projectRoot `
    -Description 'QQ Bot one-click start (Dashboard + SnowLuma + NoneBot2)'

# 停止
New-Shortcut -Name '停止 QQ Bot.lnk' -TargetPath $powershellExe `
    -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + $stopPs1 + '"') `
    -WorkingDirectory $projectRoot `
    -Description 'QQ Bot one-click stop (NoneBot2 + SnowLuma + Dashboard)'

# SnowLuma 单独启动（可选）
$snowDir = Get-ChildItem (Join-Path $projectRoot 'snowluma') -Directory -Filter 'SnowLuma-*' -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName 'node.exe') } |
    Select-Object -First 1
if ($snowDir) {
    $node = Join-Path $snowDir.FullName 'node.exe'
    New-Shortcut -Name 'SnowLuma.lnk' -TargetPath $node `
        -Arguments './index.mjs' `
        -WorkingDirectory $snowDir.FullName `
        -Description 'SnowLuma (standalone)'
} else {
    Write-Host '[WARN] SnowLuma 未找到，跳过 SnowLuma.lnk' -ForegroundColor Yellow
}

if (-not (Test-Path $iconFile)) {
    Write-Host '[WARN] assets/icon/icon.ico 不存在，快捷方式将使用默认图标' -ForegroundColor Yellow
}

Write-Host ''
Write-Host '完成。' -ForegroundColor Green
