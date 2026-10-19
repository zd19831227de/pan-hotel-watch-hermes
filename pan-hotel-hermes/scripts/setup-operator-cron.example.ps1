# 可选：Hermes 定时只读运营推送示例（默认不执行）
# 用法（需先确认飞书群 chat_id 与 Hermes gateway 已配置）：
#   1. 阅读本脚本，按注释修改 CHAT_ID / 时间
#   2. 手动执行下方 hermes cron create 命令
# 约束：只允许只读 MCP 查询或 hermes send 文字/卡片，禁止 run_*_once 与写价。

$ErrorActionPreference = "Stop"

$HermesExe = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\hermes.exe"
if (-not (Test-Path $HermesExe)) {
    throw "未找到 Hermes: $HermesExe"
}

Write-Host "当前 cron 任务："
& $HermesExe cron list

Write-Host ""
Write-Host "示例（请勿直接复制执行，先改 CHAT_ID 与时间）："
Write-Host @'
hermes cron create `
  --name "pan-hotel-morning-overview" `
  --cron "0 9 * * *" `
  --timezone "Asia/Shanghai" `
  --message "请调用 get_operator_hotel_overview 生成今日酒店概览并发送到当前群。只读查询，禁止 run_ebk_watch_once / run_competitor_watch_once。"
'@

Write-Host ""
Write-Host "说明：cron 由 Hermes 调度，与 pan-hotel-watch worker 竞品轮询独立。"
Write-Host "删除示例 job: hermes cron rm pan-hotel-morning-overview"
