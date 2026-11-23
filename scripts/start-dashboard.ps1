<#
.SYNOPSIS
  启动 Codex 知识库 Web 管理界面（已在运行则直接打开浏览器）。
.EXAMPLE
  powershell -File scripts/start-dashboard.ps1
  powershell -File scripts/start-dashboard.ps1 -Lan        # 允许局域网访问（需要 Token）
  powershell -File scripts/start-dashboard.ps1 -NoBrowser
#>
param(
    [int]$Port = 8787,
    [switch]$Lan,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$url = "http://127.0.0.1:$Port/"

function Test-Port {
    param([int]$p)
    $null -ne (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}

if (Test-Port $Port) {
    Write-Output "服务已在运行: $url"
    if (-not $NoBrowser) { Start-Process $url }
    return
}

$env:PYTHONIOENCODING = "utf-8"
$args = @("$root\dashboard\serve.py", "--port", "$Port")
if ($Lan) {
    if (-not $env:KB_DASH_TOKEN) {
        $env:KB_DASH_TOKEN = -join ((48..57) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
        Write-Output "已生成本次访问 Token（写入环境变量 KB_DASH_TOKEN，仅当前会话有效）"
    }
    $args += @("--host", "0.0.0.0", "--allow-write")
}

$logDir = "$root\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$p = Start-Process -FilePath "python" -ArgumentList $args -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput "$logDir\dashboard.out.log" -RedirectStandardError "$logDir\dashboard.err.log"

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 400
    try {
        $r = Invoke-RestMethod "http://127.0.0.1:$Port/healthz" -TimeoutSec 2
        if ($r.ok) { break }
    } catch { }
}

if (-not (Test-Port $Port)) {
    Write-Output "启动失败，请查看 $logDir\dashboard.err.log"
    Get-Content "$logDir\dashboard.err.log" -ErrorAction SilentlyContinue | Select-Object -Last 10
    exit 1
}

Write-Output "知识库界面已启动: $url   (PID $($p.Id))"
if ($Lan) {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254*" } | Select-Object -First 1).IPAddress
    Write-Output "局域网地址: http://${ip}:$Port/   （需要 Token: $env:KB_DASH_TOKEN）"
}
if (-not $NoBrowser) { Start-Process $url }
