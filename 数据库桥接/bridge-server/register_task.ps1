# 注册开机自启（当前用户登录时启动，最高权限）
# 用法：右键"使用 PowerShell 运行"，或管理员 PowerShell 中执行 .\register_task.ps1

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat = Join-Path $dir "run_server.bat"

schtasks /Create /F /TN "GalaxyBridgeServer" `
    /TR "`"$bat`"" `
    /SC ONLOGON /RL HIGHEST

if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] 已注册开机自启任务 GalaxyBridgeServer（本次登录即生效）"
    Write-Host "删除自启：schtasks /Delete /TN GalaxyBridgeServer /F"
} else {
    Write-Host "[ERROR] 注册失败，请用管理员身份运行 PowerShell 后重试"
}
