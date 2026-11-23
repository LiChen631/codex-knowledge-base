<#
.SYNOPSIS
  备份 Codex 知识库。
.EXAMPLE
  powershell -File scripts/backup.ps1
  powershell -File scripts/backup.ps1 -Tag weekly
#>
param([string]$Tag = "manual")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $out = & python "scripts\kb.py" backup --tag $Tag 2>&1
    $out | Write-Output
    if ($LASTEXITCODE -ne 0) { throw "备份失败（退出码 $LASTEXITCODE）" }
    $latest = Get-ChildItem "$root\backups\kb-*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) { Write-Output ("最新备份: {0} ({1:N1} MB)" -f $latest.FullName, ($latest.Length/1MB)) }
} finally { Pop-Location }
