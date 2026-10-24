# 一键启动 QQ Bot 全栈：Dashboard + SnowLuma + NoneBot2
#
# 流程：
#   1) 启动前检查（botenv / private/.env / SnowLuma 安装 / QQ 是否在运行）
#   2) 确保 Dashboard 在跑（复用 launch_dashboard.ps1）
#   3) 通过 Dashboard API 启动 SnowLuma + NoneBot2（已在跑会跳过）
#   4) 等待 bot / SnowLuma / WS 全部就绪
#   5) 自动打开浏览器到 Dashboard
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File start_stack.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File start_stack.ps1 -NoBrowser

param(
    [switch]$NoBrowser
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dashPort = 8501
$baseUrl = "http://127.0.0.1:$dashPort"
$dashHeaders = @{}

function Write-Step([string]$msg) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg)
}

function Invoke-DashApi {
    param(
        [string]$Path,
        [string]$Method = 'GET',
        [int]$TimeoutSec = 30
    )
    if ($Method -eq 'POST') {
        return Invoke-RestMethod -Method Post -Uri ($baseUrl + $Path) -Headers $dashHeaders -TimeoutSec $TimeoutSec
    }
    return Invoke-RestMethod -Uri ($baseUrl + $Path) -Headers $dashHeaders -TimeoutSec $TimeoutSec
}

# ---------- 1) 启动前检查 ----------
$problems = @()
$warnings = @()

$venvPy = 'D:\Work\anaconda3\envs\botenv\pythonw.exe'
if (-not (Test-Path $venvPy)) {
    $problems += "botenv pythonw.exe 不存在：$venvPy（先运行 scripts\bat\setup.bat）"
}

$envFile = Join-Path $root 'private\.env'
$token = ''
if (-not (Test-Path $envFile)) {
    $problems += "缺少 private\.env（参考 qqbot\.env.example 创建）"
} else {
    $envMap = @{}
    Get-Content $envFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $envMap[$Matches[1]] = $Matches[2].Trim()
        }
    }
    $token = [string]$envMap['DASHBOARD_TOKEN']
    $deepseekKey = [string]$envMap['DEEPSEEK_API_KEY']
    if (-not $deepseekKey -or $deepseekKey -match 'your|你的') {
        $warnings += "DEEPSEEK_API_KEY 看起来没配置，对话会失败"
    }
    if (-not $token) {
        $warnings += "private\.env 没写 DASHBOARD_TOKEN，将尝试从日志里找自动生成的 token"
    }
}

$snowDir = Get-ChildItem (Join-Path $root 'snowluma') -Directory -Filter 'SnowLuma-*' -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName 'node.exe') } |
    Select-Object -First 1
if (-not $snowDir) {
    $warnings += "没找到 SnowLuma（snowluma\SnowLuma-*\node.exe），将跳过它的启动"
}

if (-not (Get-Process QQ -ErrorAction SilentlyContinue)) {
    $warnings += "QQ 客户端没运行。SnowLuma 需要 QQ 已登录才能 hook，请先打开并登录 QQ"
}

if ($problems.Count -gt 0) {
    Write-Host '[X] 启动前检查失败：' -ForegroundColor Red
    $problems | ForEach-Object { Write-Host ('    - ' + $_) -ForegroundColor Red }
    exit 1
}
if ($warnings.Count -gt 0) {
    Write-Host '[!] 提示：' -ForegroundColor Yellow
    $warnings | ForEach-Object { Write-Host ('    - ' + $_) -ForegroundColor Yellow }
}

# ---------- 2) Dashboard ----------
Write-Step "确保 Dashboard 在运行（端口 $dashPort）..."
$launchPs1 = Join-Path $root 'scripts\bat\launch_dashboard.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launchPs1 -NoPause
if ($LASTEXITCODE -ne 0) {
    Write-Host '[X] Dashboard 启动失败，见 qqbot\logs\dashboard.err' -ForegroundColor Red
    exit 1
}

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $h = Invoke-RestMethod -Uri ($baseUrl + '/api/health') -TimeoutSec 3
        if ($h.status) { $healthy = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $healthy) {
    Write-Host '[X] Dashboard 30 秒内没有就绪，见 qqbot\logs\dashboard.err' -ForegroundColor Red
    exit 1
}
Write-Step "Dashboard 就绪：$baseUrl"

# token 兜底：DASHBOARD_TOKEN 为空时 Dashboard 会自动生成并写日志
if (-not $token) {
    foreach ($lf in @((Join-Path $root 'qqbot\logs\dashboard.log'), (Join-Path $root 'qqbot\logs\dashboard.out'))) {
        if (-not (Test-Path $lf)) { continue }
        $hit = Get-Content $lf -Tail 300 -Encoding UTF8 -ErrorAction SilentlyContinue |
            Select-String -Pattern '([A-Za-z0-9_-]{24,})' | Select-Object -Last 1
        if ($hit) { $token = $hit.Matches[0].Groups[1].Value; break }
    }
}

if (-not $token) {
    Write-Host '[!] 拿不到 DASHBOARD_TOKEN，跳过自动启动服务，请到面板手动点启动' -ForegroundColor Yellow
} else {
    $dashHeaders = @{ 'X-Dashboard-Token' = $token }

    # ---------- 3) 启动服务 ----------
    $services = @(
        @{ name = 'snowluma'; label = 'SnowLuma' },
        @{ name = 'nonebot2'; label = 'NoneBot2 (bot)' }
    )
    foreach ($svc in $services) {
        Write-Step ("启动 " + $svc.label + " ...")
        try {
            $r = Invoke-DashApi -Path ('/api/process/start/' + $svc.name) -Method 'POST' -TimeoutSec 60
            Write-Host ('    -> ' + $r.msg)
        } catch {
            Write-Host ('    -> 启动失败：' + $_.Exception.Message) -ForegroundColor Red
        }
        Start-Sleep -Seconds 2
    }

    # ---------- 4) 等待就绪 ----------
    # 用 /api/ws-health 而不是 /api/health：后者复用 monitor 的 30s 状态缓存，
    # 刚启动后最多要等 30s 才显示 ws_connected=true；ws-health 每 3s 探活一次。
    Write-Step '等待 QQ / WS 连接就绪（最多 60s）...'
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $wh = Invoke-RestMethod -Uri ($baseUrl + '/api/ws-health') -TimeoutSec 3
            if ($wh.ws_upgrade_ok) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if ($ready) {
        Write-Step '全部就绪：bot / SnowLuma / WS 均已连接'
    } else {
        Write-Host '[!] 60 秒内没有全部就绪，可能是 QQ 未登录或端口被占，请打开面板查看状态' -ForegroundColor Yellow
    }
}

# ---------- 5) 打开浏览器 ----------
if (-not $NoBrowser) {
    Write-Step "打开面板 $baseUrl"
    Start-Process $baseUrl
}

Write-Host ''
Write-Host '启动完成。'
Write-Host ('  Dashboard : ' + $baseUrl)
Write-Host '  SnowLuma  : http://localhost:5099 （只有首次登录 / 升级同意协议 / QQ 掉线时才需要打开）'
Write-Host '  日志      : qqbot\logs\'
exit 0
