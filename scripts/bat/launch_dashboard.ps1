# Start dashboard (NoneBot bot 管理后台) in hidden window
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File launch_dashboard.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$root   = (Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent | Split-Path -Parent)
# 2026-07-23: 改用 anaconda conda env (botenv) — 真独立 python, 避免 venv launcher 转发到 base python
$venv   = 'D:\Work\anaconda3\envs\botenv\pythonw.exe'
$qwDir  = $root
$logs   = Join-Path $root 'qqbot\logs'
$port   = 8501

if (-not (Test-Path $venv)) {
    Write-Host "[!] botenv pythonw.exe not found: $venv"
    Write-Host "    创建方法: conda create -n botenv python=3.12 -y"
    pause
    exit 1
}
if (-not (Test-Path $logs)) {
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
}

# 1) 端口检查
$listenPort = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listenPort) {
    Write-Host "[dashboard] port $port already listening (pid=$($listenPort.OwningProcess)), skip launch."
    exit 0
}

# 2) 后台启动 dashboard (Hidden, 重定向 stdout/stderr)
$outLog = Join-Path $logs 'dashboard.out'
$errLog = Join-Path $logs 'dashboard.err'
$proc = Start-Process -FilePath $venv `
    -ArgumentList @('-m','uvicorn','qqbot.dashboard.main:app','--host','0.0.0.0','--port',"$port") `
    -WorkingDirectory $qwDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

# 3) 写 PID
$runtimeDir = Join-Path $root '.runtime'
if (-not (Test-Path $runtimeDir)) { New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null }
($proc.Id) | Set-Content -Path (Join-Path $runtimeDir 'dashboard.pid')

Write-Host "[dashboard] launched pid=$($proc.Id)"
Write-Host "[dashboard] waiting for port $port ..."

# 4) 等端口就绪, 最长 15s
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    $listenPort = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listenPort) {
        $ready = $true
        Write-Host "[dashboard] port $port ready after ${i}s."
        break
    }
}

if (-not $ready) {
    Write-Host "[!] dashboard failed to bind port $port within 15s."
    Write-Host "    see $logs\dashboard.err for details"
    pause
    exit 1
}
exit 0
