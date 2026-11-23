<#
.SYNOPSIS
  从备份恢复知识库。
.EXAMPLE
  powershell -File scripts/restore.ps1 -Latest
  powershell -File scripts/restore.ps1 -File backups\kb-20261111-120000-manual.zip -Force
#>
param(
    [string]$File,
    [switch]$Latest,
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ($Latest) {
    $f = Get-ChildItem "$root\backups\kb-*.zip" -ErrorAction Stop |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $f) { throw "没有找到任何备份文件" }
    $File = $f.FullName
}
if (-not $File) { throw "请指定 -File 或 -Latest" }
if (-not (Test-Path -LiteralPath $File)) { throw "备份文件不存在: $File" }
if (-not $Force) {
    $ans = Read-Host "将用 $File 覆盖当前数据库，已存在的数据会先自动备份。继续？(y/N)"
    if ($ans -ne "y") { Write-Output "已取消"; return }
}
Push-Location $root
try {
    $args = @("scripts\kb.py", "restore", "`"$File`"")
    if ($Force) { $args += "--force" }
    & python @args
    if ($LASTEXITCODE -ne 0) { throw "恢复失败（退出码 $LASTEXITCODE）" }
    Write-Output "恢复完成，正在校验…"
    & python "scripts\kb.py" doctor | Out-String | Write-Output
} finally { Pop-Location }
