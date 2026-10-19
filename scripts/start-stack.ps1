# Pan Hotel Watch x Hermes 一键编排脚本
#   1. 清理所有内置 feishu_listener（hermes 模式下，避免与 Hermes 抢消息）
#   2. 启动 backend + scheduler(worker) + frontend
#   3. 检查 / 可选拉起部署清单中声明的 Hermes Gateway
#   4. 汇总状态
#
# 用法：
#   .\scripts\start-stack.ps1                 # 起后端三件套并检查命名 Hermes Gateway
#   .\scripts\start-stack.ps1 -StartGateway   # 额外启动清单中的 Gateway
#   .\scripts\start-stack.ps1 -RebuildFrontend
#
param(
    [string]$BackendHost = "0.0.0.0",
    [int]$BackendPort = 8100,
    [string]$FrontendHost = "0.0.0.0",
    [int]$FrontendPort = 5173,
    [ValidateSet("preview", "dev")][string]$FrontendMode = "preview",
    [switch]$RebuildFrontend,
    [switch]$StartGateway,
    [string[]]$HermesGatewayProfiles = @(),
    [string]$FeishuProfilesManifest
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

$Root = Get-PanHotelWatchRoot
$StopListeners = Join-Path $Root "pan-hotel-hermes\scripts\stop-feishu-listeners.ps1"
$StartGateways = Join-Path $Root "pan-hotel-hermes\scripts\start-hermes-gateways.ps1"
$StartAll = Join-Path $PSScriptRoot "start-all.ps1"
$StatusScript = Join-Path $PSScriptRoot "status.ps1"
$HermesExe = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\hermes.exe"
$HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
$FeishuProfilesTool = Join-Path $Root "pan-hotel-hermes\scripts\feishu_profiles.py"
if (-not $FeishuProfilesManifest) {
    $FeishuProfilesManifest = Join-Path $Root "pan-hotel-hermes\config\feishu-profiles.yaml"
}

$feishuAgentMode = (Get-PanHotelWatchEnvValue -Name "FEISHU_AGENT_MODE" -Default "hermes").Trim().ToLower()
if ($feishuAgentMode -eq "hermes" -and $HermesGatewayProfiles.Count -eq 0) {
    if (-not (Test-Path -LiteralPath $FeishuProfilesManifest)) {
        throw "Feishu profile manifest not found: $FeishuProfilesManifest"
    }
    if (-not (Test-Path -LiteralPath $HermesPython)) {
        throw "Hermes Python not found: $HermesPython"
    }
    $HermesGatewayProfiles = @(
        & $HermesPython $FeishuProfilesTool --manifest $FeishuProfilesManifest --hermes-root (Join-Path $Root "pan-hotel-hermes") "list"
    )
    if ($LASTEXITCODE -ne 0 -or $HermesGatewayProfiles.Count -eq 0) {
        throw "Feishu profile manifest validation failed."
    }
}

Write-Host "==============================================="
Write-Host " Pan Hotel Watch x Hermes 一键编排"
Write-Host " FEISHU_AGENT_MODE = $feishuAgentMode"
Write-Host "==============================================="

Write-Host ""
Write-Host "[1/4] 清理内置 feishu_listener ..."
if ($feishuAgentMode -eq "hermes") {
    if (Test-Path $StopListeners) {
        & $StopListeners
    } else {
        Write-Warning "未找到 $StopListeners，跳过清理。"
    }
} else {
    Write-Host "FEISHU_AGENT_MODE=$feishuAgentMode：内置 feishu-listener 为期望进程，跳过清理。"
}

Write-Host ""
Write-Host "[2/4] 启动 backend + scheduler + frontend ..."
$startArgs = @{
    BackendHost = $BackendHost
    BackendPort = $BackendPort
    FrontendHost = $FrontendHost
    FrontendPort = $FrontendPort
    FrontendMode = $FrontendMode
}
if ($RebuildFrontend) {
    $startArgs["RebuildFrontend"] = $true
}
& $StartAll @startArgs

Write-Host ""
Write-Host "[3/4] 检查 Hermes Gateway ..."
if ($feishuAgentMode -ne "hermes") {
    Write-Host "当前非 hermes 模式，飞书由内置 listener 处理，跳过 Hermes Gateway。"
} elseif (-not (Test-Path $HermesExe)) {
    Write-Warning "未找到 Hermes 可执行文件：$HermesExe"
    Write-Warning "请先安装并注册 Hermes：pan-hotel-hermes\scripts\register-hermes.ps1"
} else {
    Write-Host "Hermes: $HermesExe"
    Write-Host "gateway status:"
    try {
        & $HermesExe gateway list
        foreach ($profile in $HermesGatewayProfiles) {
            Write-Host ""
            Write-Host "[$profile]"
            & $HermesExe -p $profile gateway status
        }
    } catch {
        Write-Warning "读取 Hermes gateway status 失败：$($_.Exception.Message)"
    }
    if ($StartGateway) {
        Write-Host "启动 Hermes Gateway profiles: $($HermesGatewayProfiles -join ', ')"
        & $StartGateways -Profiles $HermesGatewayProfiles -ManifestPath $FeishuProfilesManifest -Replace
    } else {
        Write-Host "如需启动 Hermes Gateway，请加 -StartGateway，或手动运行：pan-hotel-hermes\scripts\start-hermes-gateways.ps1"
    }
}

Write-Host ""
Write-Host "[4/4] 汇总状态 ..."
& $StatusScript -BackendPort $BackendPort -FrontendPort $FrontendPort -HermesGatewayProfiles $HermesGatewayProfiles -FeishuProfilesManifest $FeishuProfilesManifest

Write-Host ""
Write-Host "完成。hermes 模式下飞书入口应仅由 Hermes Gateway 监听。"
