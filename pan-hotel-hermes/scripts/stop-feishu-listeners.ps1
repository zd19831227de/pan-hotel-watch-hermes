# 停止所有 pan-hotel-watch / 旧项目 的 feishu-listener 进程（Hermes 接管飞书后必须停）
# 按命令行匹配 app.workers.feishu_listener，覆盖 python.exe / pythonw.exe / py.exe /
# venv 改名后的解释器，避免旧项目（pan-hotel-watch、pan-hotel-ai）用别的可执行名
# 偷偷监听同一个飞书应用、和 Hermes 抢消息。

$ErrorActionPreference = "Stop"

function Get-FeishuListenerProcesses {
    return Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'app\.workers\.feishu_listener' }
}

$listeners = @(Get-FeishuListenerProcesses)

if ($listeners.Count -eq 0) {
    Write-Host "未发现 feishu_listener 进程。飞书入口应仅由 Hermes Gateway 监听。"
    exit 0
}

foreach ($proc in $listeners) {
    Write-Host "停止 PID $($proc.ProcessId) [$($proc.Name)] 路径=$($proc.ExecutablePath)"
    Write-Host "  命令行: $($proc.CommandLine)"
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1
$left = @(Get-FeishuListenerProcesses)
if ($left.Count -gt 0) {
    Write-Warning "仍有 $($left.Count) 个 feishu_listener 未停止，请手动检查任务管理器："
    foreach ($proc in $left) {
        Write-Warning "  PID $($proc.ProcessId) [$($proc.Name)] $($proc.CommandLine)"
    }
    exit 1
}

Write-Host "已全部停止。飞书入口应仅由 Hermes Gateway 监听。"
