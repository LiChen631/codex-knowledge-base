<# 停止知识库 Web 服务 #>
param([int]$Port = 8787)
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $conn) { Write-Output "端口 $Port 未在监听，无需停止"; return }
foreach ($c in $conn) {
    try {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop
        Write-Output "已停止进程 $($c.OwningProcess)"
    } catch { Write-Output "停止失败: $_" }
}
