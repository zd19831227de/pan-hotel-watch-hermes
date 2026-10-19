# 从旧 pan-hotel-watch 一键切换到 pan-hotel-watch - hermes 主仓库
#
# 流程：
#   1. 停止所有 feishu_listener（含 pan-hotel-ai 等旧项目）
#   2. 停止旧仓库与本仓库已托管进程，并清理旧路径残留 worker/backend/frontend
#   3. 备份 hermes 侧数据库与浏览器状态，再从旧仓库同步最新数据
#   4. 启动 start-stack.ps1（可选 -StartGateway）
#
# 用法：
#   .\scripts\switch-to-hermes.ps1
#   .\scripts\switch-to-hermes.ps1 -StartGateway
#   .\scripts\switch-to-hermes.ps1 -LegacyRoot "C:\Users\Administrator\Desktop\pan-hotel-watch" -Force
#   .\scripts\switch-to-hermes.ps1 -SkipBrowserState   # 仅同步数据库，不动浏览器 profile

param(
    [string]$LegacyRoot = "C:\Users\Administrator\Desktop\pan-hotel-watch",
    [ValidateSet("preview", "dev")][string]$FrontendMode = "preview",
    [switch]$StartGateway,
    [switch]$RebuildFrontend,
    [switch]$SkipBrowserState,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

$HermesRoot = Get-PanHotelWatchRoot
$resolvedLegacy = Resolve-Path -LiteralPath $LegacyRoot -ErrorAction SilentlyContinue
if ($resolvedLegacy) {
    $LegacyRoot = $resolvedLegacy.Path
} else {
    $LegacyRoot = $LegacyRoot.TrimEnd('\')
}
$StopListeners = Join-Path $HermesRoot "pan-hotel-hermes\scripts\stop-feishu-listeners.ps1"
$StartStack = Join-Path $PSScriptRoot "start-stack.ps1"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupDir = Join-Path $HermesRoot ".runtime\backups\switch-to-hermes-$Timestamp"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host ">>> $Message"
}

function Get-PanHotelWatchProcessesByRoot {
    param([Parameter(Mandatory = $true)][string]$RootPath)
    $pattern = [regex]::Escape($RootPath)
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and (
                ($_.CommandLine -match $pattern) -and (
                    $_.CommandLine -match 'app\.workers\.(read_only_scheduler|feishu_listener)' -or
                    $_.CommandLine -match 'uvicorn.*app\.main:app' -or
                    $_.CommandLine -match 'vite\.js'
                )
            )
        })
}

function Stop-PanHotelWatchManagedStack {
    param([Parameter(Mandatory = $true)][string]$RootPath)
    $stopAll = Join-Path $RootPath "scripts\stop-all.ps1"
    if (Test-Path $stopAll) {
        Write-Host "运行 $stopAll"
        & $stopAll
    } else {
        Write-Host "未找到 $stopAll，跳过托管 stop-all。"
    }
}

function Stop-PanHotelWatchOrphanProcesses {
    param([Parameter(Mandatory = $true)][string]$RootPath)
    $procs = Get-PanHotelWatchProcessesByRoot -RootPath $RootPath
    if ($procs.Count -eq 0) {
        Write-Host "未发现 $RootPath 下的残留 backend/worker/frontend 进程。"
        return
    }
    Write-Host "清理 $($procs.Count) 个 $RootPath 残留进程 ..."
    foreach ($proc in $procs) {
        Write-Host "  停止 PID $($proc.ProcessId) [$($proc.Name)]"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

function Copy-WithBackup {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$BackupRoot,
        [switch]$Recurse
    )
    if (-not (Test-Path $Source)) {
        Write-Warning "源不存在，跳过：$Source"
        return $false
    }
    $relative = if ($Source.StartsWith($LegacyRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        $Source.Substring($LegacyRoot.Length).TrimStart('\')
    } else {
        Split-Path -Leaf $Source
    }
    if (Test-Path $Destination) {
        $backupTarget = Join-Path $BackupRoot $relative
        $backupParent = Split-Path -Parent $backupTarget
        if (-not (Test-Path $backupParent)) {
            New-Item -ItemType Directory -Force -Path $backupParent | Out-Null
        }
        if ($Recurse) {
            Copy-Item -LiteralPath $Destination -Destination $backupTarget -Recurse -Force
        } else {
            Copy-Item -LiteralPath $Destination -Destination $backupTarget -Force
        }
        Write-Host "  已备份 hermes 旧文件 -> $backupTarget"
    }
    if ($Recurse) {
        if (Test-Path $Destination) {
            Remove-Item -LiteralPath $Destination -Recurse -Force
        }
        Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
    } else {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
    }
    return $true
}

Write-Host "==============================================="
Write-Host " Pan Hotel Watch -> Hermes 主仓库 一键切换"
Write-Host "==============================================="
Write-Host "Hermes 主仓库 : $HermesRoot"
Write-Host "旧仓库路径   : $LegacyRoot"
Write-Host "备份目录     : $BackupDir"
Write-Host ""

if (-not (Test-Path $LegacyRoot)) {
    throw ("Legacy repo not found: {0}. Use -LegacyRoot to set the correct path." -f $LegacyRoot)
}
if ($LegacyRoot -eq $HermesRoot) {
    throw "LegacyRoot is the same as the Hermes repo; nothing to switch."
}

$legacyDb = Join-Path $LegacyRoot "pan_hotel_watch.db"
$hermesDb = Join-Path $HermesRoot "pan_hotel_watch.db"
$legacyBrowser = Join-Path $LegacyRoot ".runtime\browser-state"
$hermesBrowser = Join-Path $HermesRoot ".runtime\browser-state"

Write-Host "计划："
Write-Host "  1) 停止 feishu_listener + 旧/新托管进程 + 旧路径残留进程"
Write-Host "  2) 同步数据库：$legacyDb -> $hermesDb"
if (-not $SkipBrowserState) {
    Write-Host "  3) 同步浏览器状态：$legacyBrowser -> $hermesBrowser"
} else {
    Write-Host "  3) 跳过浏览器状态同步（-SkipBrowserState）"
}
Write-Host "  4) 启动 Hermes 主仓库 start-stack.ps1"
if ($StartGateway) {
    Write-Host "     并新窗口启动 Hermes Gateway"
}

if (-not $Force) {
    $answer = Read-Host "Confirm switch [y/N]"
    if (@("y", "Y", "yes", "Yes") -notcontains $answer) {
        Write-Host "已取消。"
        exit 0
    }
}

Write-Step "[1/5] 停止 feishu_listener（含 pan-hotel-ai 等）"
if (Test-Path $StopListeners) {
    & $StopListeners
} else {
    Write-Warning "未找到 $StopListeners"
}

Write-Step "[2/5] 停止旧仓库与本仓库托管进程"
Stop-PanHotelWatchManagedStack -RootPath $LegacyRoot
Stop-PanHotelWatchManagedStack -RootPath $HermesRoot

Write-Step "[3/5] 清理旧路径残留 backend/worker/frontend 进程"
Stop-PanHotelWatchOrphanProcesses -RootPath $LegacyRoot
Stop-PanHotelWatchOrphanProcesses -RootPath $HermesRoot

Start-Sleep -Seconds 2

$port8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
$port5173 = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $port8000 -or $null -ne $port5173) {
    Write-Warning "8000/5173 仍被占用。若 start-stack 失败，请手动结束占用端口的进程后重试。"
    if ($null -ne $port8000) { Write-Warning "  8000 -> PID $($port8000.OwningProcess)" }
    if ($null -ne $port5173) { Write-Warning "  5173 -> PID $($port5173.OwningProcess)" }
}

Write-Step "[4/5] 备份并同步数据"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

if (-not (Test-Path $legacyDb)) {
    throw ("Legacy database not found: {0}" -f $legacyDb)
}

$legacyDbInfo = Get-Item -LiteralPath $legacyDb
Write-Host "旧库：$($legacyDbInfo.Length) bytes，最后修改 $($legacyDbInfo.LastWriteTime)"
if (Test-Path $hermesDb) {
    $hermesDbInfo = Get-Item -LiteralPath $hermesDb
    Write-Host "hermes 库（将被覆盖）：$($hermesDbInfo.Length) bytes，最后修改 $($hermesDbInfo.LastWriteTime)"
}

Copy-WithBackup -Source $legacyDb -Destination $hermesDb -BackupRoot $BackupDir | Out-Null
Write-Host "  已同步数据库 -> $hermesDb"

if (-not $SkipBrowserState) {
    $copied = Copy-WithBackup -Source $legacyBrowser -Destination $hermesBrowser -BackupRoot $BackupDir -Recurse
    if ($copied) {
        Write-Host "  已同步浏览器状态 -> $hermesBrowser"
    }
}

$feishuMode = (Get-PanHotelWatchEnvValue -Name "FEISHU_AGENT_MODE" -Default "hermes").Trim().ToLower()
if ($feishuMode -ne "hermes") {
    Write-Warning "当前 hermes 主仓库 FEISHU_AGENT_MODE=$feishuMode（非 hermes）。"
    Write-Warning "建议在 .env 设置 FEISHU_AGENT_MODE=hermes 后重启，避免内置 listener 与 Hermes 抢消息。"
}

Write-Step "[5/5] 启动 Hermes 主仓库"
$startArgs = @{
    FrontendMode = $FrontendMode
}
if ($StartGateway) { $startArgs["StartGateway"] = $true }
if ($RebuildFrontend) { $startArgs["RebuildFrontend"] = $true }
& $StartStack @startArgs

Write-Host ""
Write-Host "==============================================="
Write-Host " 切换完成"
Write-Host "==============================================="
Write-Host "Hermes 前端：http://127.0.0.1:5173"
Write-Host "Hermes 后端：http://127.0.0.1:8000/api/v1/health"
Write-Host "备份位置  ：$BackupDir"
Write-Host ""
Write-Host "旧仓库 $LegacyRoot 已不再启动；后续请只维护 Hermes 主仓库。"
Write-Host "飞书测试：@机器人 问经营数据；调价卡仍走 dispatch_watch_feishu_event。"
Write-Host "若需回滚 hermes 侧数据，可从备份目录恢复 pan_hotel_watch.db 与 browser-state。"
