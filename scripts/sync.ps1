<#
.SYNOPSIS
  同步知识库到 Git（私有仓库）。
.EXAMPLE
  powershell -File scripts/sync.ps1
  powershell -File scripts/sync.ps1 -Push
  powershell -File scripts/sync.ps1 -Init -CreateRepo
#>
param(
    [switch]$Init,
    [switch]$Push,
    [switch]$CreateRepo,
    [string]$RepoName = "codex-global-knowledge-base",
    [string]$Message
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if ($Init -and -not (Test-Path "$root\.git")) {
        git init -b main | Out-Null
        git add -A
        git -c user.name="Codex" -c user.email="codex@local" commit -m "kb: initial import" | Out-Null
        Write-Output "已初始化本地 git 仓库"
    }
    if ($CreateRepo) {
        $status = gh auth status 2>&1 | Out-String
        if ($status -notmatch "Logged in") {
            Write-Output "⛔ gh 未登录，无法创建私有仓库。请先运行： gh auth login"
            exit 4
        }
        $exists = gh repo view "$RepoName" 2>&1 | Out-String
        if ($exists -match "Could not resolve") {
            gh repo create $RepoName --private --source=. --remote=origin --push
            Write-Output "已创建私有仓库并推送"
        } else {
            Write-Output "仓库已存在: $RepoName（未重复创建）"
        }
    }
    $msg = if ($Message) { $Message } else { "kb: sync $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
    $args = @("scripts\kb.py", "sync", "--message", $msg)
    if ($Push) { $args += "--push" }
    & python @args
    $code = $LASTEXITCODE
    if ($code -eq 3) { Write-Output "⛔ 检测到疑似密钥，已中止提交"; exit 3 }
    Write-Output "同步完成（退出码 $code）"
} finally { Pop-Location }
