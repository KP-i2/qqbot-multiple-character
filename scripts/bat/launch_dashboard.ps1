# Start dashboard (NoneBot bot 管理后台) in hidden window
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File launch_dashboard.ps1 [-NoPause]
#   -NoPause: 失败时不等待按键（供 start_stack.ps1 等自动化调用，避免卡住调用方）

param(
    [switch]$NoPause
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$root   = (Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent | Split-Path -Parent)
# 2026-07-23: 改用 anaconda conda env (botenv) — 真独立 python, 避免 venv launcher 转发到 base python
$venv   = 'D:\Work\anaconda3\envs\botenv\pythonw.exe'
$qwDir  = $root
$logs   = Join-Path $root 'qqbot\logs'
$port   = 8501
$runtimeDir = Join-Path $root '.runtime'

function Test-PortOpen([int]$p, [int]$timeoutMs = 400) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $p, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($timeoutMs)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PortOwner([int]$p) {
    try {
        $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { return $c.OwningProcess }
    } catch { }
    return $null
}

if (-not (Test-Path $venv)) {
    Write-Host "[!] botenv pythonw.exe not found: $venv"
    Write-Host "    创建方法: conda create -n botenv python=3.12 -y"
    if (-not $NoPause) { pause }
    exit 1
}
if (-not (Test-Path $logs)) {
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
}

# 1) 已有实例检查：用 TCP 连接探测。
#    2026-10-25: 之前用 Get-NetTCPConnection 判断，偶发明明在监听却返回空 ->
#    重复拉起一个进程（绑定失败退出），pid 文件也被写歪。TCP connect 更可靠。
if (Test-PortOpen $port) {
    $owner = Get-PortOwner $port
    Write-Host "[dashboard] port $port already accepting connections (pid=$owner), skip launch."
    if ($owner) {
        if (-not (Test-Path $runtimeDir)) { New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null }
        ($owner) | Set-Content -Path (Join-Path $runtimeDir 'dashboard.pid')
    }
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

Write-Host "[dashboard] launched pid=$($proc.Id)"
Write-Host "[dashboard] waiting for port $port ..."

# 3) 等端口就绪: 进程必须还活着 + 端口可连接（最长 20s）
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if ($proc.HasExited) {
        Write-Host "[!] dashboard process exited during startup (exit_code=$($proc.ExitCode))."
        if (Test-Path $errLog) {
            Write-Host "    --- $errLog (tail) ---"
            Get-Content $errLog -Tail 15 -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ("    " + $_) }
        }
        if (-not $NoPause) { pause }
        exit 1
    }
    if (Test-PortOpen $port) {
        $ready = $true
        Write-Host ("[dashboard] port $port ready after {0:N1}s." -f ($i * 0.5))
        break
    }
}

if (-not $ready) {
    Write-Host "[!] dashboard failed to bind port $port within 20s."
    Write-Host "    see $errLog for details"
    if (-not $NoPause) { pause }
    exit 1
}

# 4) 成功后写 PID（写入真正监听端口的进程；拿不到时退回启动进程）
$owner = Get-PortOwner $port
if (-not $owner) { $owner = $proc.Id }
if (-not (Test-Path $runtimeDir)) { New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null }
($owner) | Set-Content -Path (Join-Path $runtimeDir 'dashboard.pid')
exit 0
