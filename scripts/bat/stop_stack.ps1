# 一键停止 QQ Bot 全栈：NoneBot2 + SnowLuma + Dashboard
#
# 逻辑：
#   1) 优先走 Dashboard API 优雅停止 NoneBot2 / SnowLuma（有 5s 优雅退出）
#   2) Dashboard 不可用时，按端口兜底强杀（8080 / 5099）
#   3) 最后按 .runtime\dashboard.pid 关掉隐藏的 Dashboard（pythonw）
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File stop_stack.ps1
#   powershell ... -File stop_stack.ps1 -NoPause          # 给自动化/快捷方式用
#   powershell ... -File stop_stack.ps1 -KeepDashboard    # 只停 bot + SnowLuma，保留面板

param(
    [switch]$NoPause,
    [switch]$KeepDashboard
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dashPort = 8501
$baseUrl = "http://127.0.0.1:$dashPort"
$dashHeaders = @{}
$runtimeDir = Join-Path $root '.runtime'
$pidFile = Join-Path $runtimeDir 'dashboard.pid'

function Write-Step([string]$msg) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg)
}

function Get-EnvValue([string]$name) {
    $envFile = Join-Path $root 'private\.env'
    if (-not (Test-Path $envFile)) { return '' }
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        if ($line -match ('^\s*' + [regex]::Escape($name) + '\s*=\s*(.*)$')) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ''
}

function Get-PortOwner([int]$port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return 0
}

function Stop-ProcessTree([int]$procId, [string]$label) {
    if ($procId -le 0) { return }
    try {
        Write-Step ("强杀 {0} (PID {1})" -f $label, $procId)
        & taskkill.exe /T /F /PID $procId 2>$null | Out-Null
    } catch {
        Write-Host ("  [!] 结束 PID {0} 失败: {1}" -f $procId, $_.Exception.Message) -ForegroundColor Yellow
    }
}

function Stop-ByName([string]$name) {
    try {
        $resp = Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/process/stop/' + $name) `
            -Headers $dashHeaders -TimeoutSec 30
        if ($resp.ok) {
            Write-Step ("已停止 {0}：{1}" -f $name, $resp.msg)
            return $true
        }
        Write-Host ("  [!] 停止 {0} 失败：{1}" -f $name, $resp.msg) -ForegroundColor Yellow
        return $false
    } catch {
        Write-Host ("  [!] Dashboard API 停止 {0} 失败：{1}" -f $name, $_.Exception.Message) -ForegroundColor Yellow
        return $false
    }
}

$token = Get-EnvValue 'DASHBOARD_TOKEN'
if ($token) { $dashHeaders = @{ 'X-Dashboard-Token' = $token } }

Write-Host ''
Write-Host '=== 停止 QQ Bot 全栈 ===' -ForegroundColor Cyan
Write-Host ''

# 1) Dashboard 是否在跑
$dashUp = $false
try {
    Invoke-RestMethod -Uri ($baseUrl + '/api/health') -Headers $dashHeaders -TimeoutSec 3 | Out-Null
    $dashUp = $true
} catch {
    $dashUp = $false
}

if ($dashUp) {
    Write-Step 'Dashboard 在线，优先优雅停止'
    Stop-ByName 'nonebot2' | Out-Null
    Stop-ByName 'snowluma' | Out-Null
    Start-Sleep -Seconds 2
} else {
    Write-Step 'Dashboard 不在线，按端口兜底强杀'
}

# 2) 端口兜底（API 失败 / 面板不在 / 进程没退干净）
foreach ($item in @(@(8080, 'NoneBot2'), @(5099, 'SnowLuma'))) {
    $port = [int]$item[0]
    $label = [string]$item[1]
    $procId = Get-PortOwner $port
    if ($procId -gt 0) {
        Stop-ProcessTree $procId $label
        Start-Sleep -Milliseconds 500
    }
    if ((Get-PortOwner $port) -gt 0) {
        Write-Host ("  [!] 端口 {0} 仍被占用" -f $port) -ForegroundColor Yellow
    } else {
        Write-Step ("端口 {0} 已释放（{1}）" -f $port, $label)
    }
}

# 3) Dashboard（隐藏的 pythonw）
if (-not $KeepDashboard) {
    $dashPid = 0
    if (Test-Path $pidFile) {
        $raw = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue).Trim()
        if ($raw -match '^\d+$') { $dashPid = [int]$raw }
    }

    $dashProc = $null
    if ($dashPid -gt 0) {
        $dashProc = Get-CimInstance Win32_Process -Filter "ProcessId=$dashPid" -ErrorAction SilentlyContinue
        if ($dashProc -and $dashProc.CommandLine -notmatch 'qqbot\.dashboard|uvicorn') {
            Write-Host ("  [!] PID 文件里的 {0} 不是 Dashboard，改用端口查找" -f $dashPid) -ForegroundColor Yellow
            $dashProc = $null
        }
    }

    if (-not $dashProc) {
        $dashPid = Get-PortOwner $dashPort
        if ($dashPid -gt 0) {
            $dashProc = Get-CimInstance Win32_Process -Filter "ProcessId=$dashPid" -ErrorAction SilentlyContinue
        }
    }

    if ($dashProc) {
        Stop-ProcessTree ([int]$dashProc.ProcessId) 'Dashboard'
        Start-Sleep -Milliseconds 500
    } else {
        Write-Step 'Dashboard 未在运行'
    }

    if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
} else {
    Write-Step '按 -KeepDashboard 要求，保留 Dashboard'
}

# 4) 汇总
Write-Host ''
$left = @()
foreach ($item in @(@(8080, 'NoneBot2'), @(5099, 'SnowLuma'), @(8501, 'Dashboard'))) {
    if ((Get-PortOwner ([int]$item[0])) -gt 0) { $left += ('{0}({1})' -f $item[1], $item[0]) }
}
if ($left.Count -eq 0) {
    Write-Host '[OK] 全部已停止：8080 / 5099 / 8501 均已释放。' -ForegroundColor Green
} else {
    Write-Host ('[!] 仍在运行：' + ($left -join '、')) -ForegroundColor Yellow
}

Write-Host ''
if (-not $NoPause) {
    Read-Host '按 Enter 关闭窗口' | Out-Null
}
