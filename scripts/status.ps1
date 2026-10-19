param(
    [int]$BackendPort = 0,
    [int]$FrontendPort = 5173,
    [string[]]$HermesGatewayProfiles = @(),
    [string]$FeishuProfilesManifest
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

$Root = Get-PanHotelWatchRoot
if (-not $FeishuProfilesManifest) {
    $FeishuProfilesManifest = Join-Path $Root "pan-hotel-hermes\config\feishu-profiles.yaml"
}
$HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
$FeishuProfilesTool = Join-Path $Root "pan-hotel-hermes\scripts\feishu_profiles.py"
$manifestProfileStatus = @()

if ($BackendPort -le 0) {
    $BackendPort = [int](Get-PanHotelWatchEnvValue -Name "PAN_HOTEL_WATCH_BACKEND_PORT" -Default "8100")
}

Ensure-PanHotelWatchRuntimeDirs

$feishuAgentMode = (Get-PanHotelWatchEnvValue -Name "FEISHU_AGENT_MODE" -Default "hermes").Trim().ToLower()
$hermesMode = $feishuAgentMode -eq "hermes"

Write-Host "Feishu agent mode: $feishuAgentMode"
if ($hermesMode) {
    Write-Host "飞书入口由 Hermes Gateway 接管，本机不应运行内置 feishu-listener。"
}
Write-Host ""

$backendPortOwner = Get-PanHotelWatchPortOwner -Port $BackendPort
$frontendPortOwner = Get-PanHotelWatchPortOwner -Port $FrontendPort
$portOwnersByService = @{
    backend = $backendPortOwner
    frontend = $frontendPortOwner
}

function Resolve-PanHotelWatchStatusProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [object]$PortOwner = $null
    )

    $process = Get-PanHotelWatchProcess -Name $Name
    if ($null -ne $process) {
        return $process
    }
    if ($null -ne $PortOwner -and $null -ne $PortOwner.ProcessId) {
        $process = Get-Process -Id ([int]$PortOwner.ProcessId) -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            return $process
        }
    }
    if ($Name -eq "worker") {
        $workerPidPath = Join-Path (Get-PanHotelWatchRuntimeDir) "read-only-worker.pid"
        if (Test-Path -LiteralPath $workerPidPath) {
            $workerPid = 0
            $workerPidText = (Get-Content -Raw -LiteralPath $workerPidPath).Trim()
            if ([int]::TryParse($workerPidText, [ref]$workerPid)) {
                $process = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
                if ($null -ne $process) {
                    return $process
                }
            }
        }
    }

    $commandPatterns = @{
        backend = 'uvicorn\s+app\.main:app'
        worker = 'app\.workers\.read_only_scheduler'
        "feishu-listener" = 'app\.workers\.feishu_listener'
        frontend = 'vite\.js.*preview'
    }
    $pattern = $commandPatterns[$Name]
    if ($pattern) {
        $candidate = Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern } |
            Select-Object -First 1
        if ($null -ne $candidate) {
            return Get-Process -Id ([int]$candidate.ProcessId) -ErrorAction SilentlyContinue
        }
    }
    return $null
}

if ($hermesMode) {
    $managedServices = @("backend", "worker", "frontend")
} else {
    $managedServices = @("backend", "worker", "feishu-listener", "frontend")
}

$rows = foreach ($service in $managedServices) {
    $pidValue = Get-PanHotelWatchPid -Name $service
    $portOwner = $portOwnersByService[$service]
    $process = Resolve-PanHotelWatchStatusProcess -Name $service -PortOwner $portOwner
    $effectivePid = if ($null -ne $pidValue) {
        $pidValue
    } elseif ($null -ne $process) {
        $process.Id
    } else {
        ""
    }
    [pscustomobject]@{
        Service = $service
        Pid = $effectivePid
        Running = $null -ne $process
        PortPid = if ($null -ne $portOwner) { $portOwner.ProcessId } else { "" }
        StartedAt = if ($null -ne $process) { $process.StartTime } else { "" }
    }
}

Write-Host "Process status"
$rows | Format-Table -AutoSize

if ($hermesMode) {
    $rogueListeners = @(
        Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -and $_.CommandLine -match 'app\.workers\.feishu_listener' }
    )
    Write-Host ""
    Write-Host "Feishu listener entry (hermes mode)"
    if ($rogueListeners.Count -gt 0) {
        Write-Warning "检测到 $($rogueListeners.Count) 个内置 feishu_listener 进程，可能与 Hermes 抢消息。"
        Write-Warning "请运行 pan-hotel-hermes\scripts\stop-feishu-listeners.ps1 清理："
        foreach ($proc in $rogueListeners) {
            Write-Warning "  PID $($proc.ProcessId) [$($proc.Name)] $($proc.CommandLine)"
        }
    } else {
        Write-Host "未发现内置 feishu_listener 进程（正常，飞书由 Hermes 接管）。"
    }
}

$backend = Test-PanHotelWatchHttp -Url "http://127.0.0.1:$BackendPort/api/v1/health"
$frontend = Test-PanHotelWatchHttp -Url "http://127.0.0.1:$FrontendPort"

Write-Host ""
Write-Host "HTTP checks"
@($backend, $frontend) | Format-Table Url, Ok, StatusCode, Error -AutoSize

Write-Host ""
Write-Host "Port owners"
@(
    $backendPortOwner
    $frontendPortOwner
) | Where-Object { $null -ne $_ } | Format-Table Port, LocalAddress, ProcessId, ProcessName, Path -AutoSize

Write-Host ""
Write-Host "Worker heartbeats"
$workerProcess = Resolve-PanHotelWatchStatusProcess -Name "worker"
if ($null -eq $workerProcess) {
    Write-Host "Worker process is not running. Any polling heartbeat below is the last recorded heartbeat."
}

if ($hermesMode) {
    $heartbeatWorkers = @(
        "ctrip_ebooking_data_center_scheduler",
        "meituan_ebooking_scheduler",
        "fliggy_ebooking_scheduler",
        "competitor_price_scheduler"
    )
} else {
    $heartbeatWorkers = @("ctrip_ebooking_data_center_scheduler", "competitor_price_scheduler", "feishu_listener")
}

foreach ($workerName in $heartbeatWorkers) {
    $heartbeatPath = Get-PanHotelWatchWorkerHeartbeatPath -WorkerName $workerName
    Write-Host ""
    Write-Host "[$workerName] $heartbeatPath"
    if (Test-Path $heartbeatPath) {
        Get-Content -LiteralPath $heartbeatPath -Raw
    } else {
        Write-Host "No heartbeat file found."
    }
}

if ($hermesMode) {
    Write-Host ""
    Write-Host "[feishu_listener] 已由 Hermes Gateway 接管，跳过内置 listener 心跳检查。"
    Write-Host "（即使存在历史残留心跳文件，也不代表内置 listener 仍在运行。）"
    $HermesExe = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\hermes.exe"
    Write-Host ""
    Write-Host "Hermes Gateways"
    if (-not (Test-Path -LiteralPath $FeishuProfilesManifest)) {
        Write-Warning "Feishu profile manifest not found: $FeishuProfilesManifest"
    } elseif (-not (Test-Path -LiteralPath $HermesPython)) {
        Write-Warning "Hermes Python not found: $HermesPython"
    } else {
        try {
            $contextApiBase = "http://127.0.0.1:$BackendPort/api/v1/ops/feishu-chat-context"
            $manifestJson = (& $HermesPython $FeishuProfilesTool --manifest $FeishuProfilesManifest --hermes-root (Join-Path $Root "pan-hotel-hermes") "describe" "--context-api-base" $contextApiBase) -join "`n"
            if ($LASTEXITCODE -ne 0) { throw "Manifest describe failed." }
            $decodedManifest = $manifestJson | ConvertFrom-Json
            $manifestProfileStatus = @($decodedManifest)
            if ($HermesGatewayProfiles.Count -eq 0) {
                $HermesGatewayProfiles = @($manifestProfileStatus | ForEach-Object { $_.profile })
            }
        } catch {
            Write-Warning "读取 Feishu profile manifest 失败：$($_.Exception.Message)"
        }
    }
    if (Test-Path $HermesExe) {
        Write-Host "Hermes: $HermesExe"
        try {
            & $HermesExe gateway list
            $gatewayProcesses = @(
                Get-CimInstance Win32_Process |
                    Where-Object { $_.CommandLine -and $_.CommandLine -match 'gateway\s+run' }
            )
            $gatewayRows = foreach ($profile in $HermesGatewayProfiles) {
                $profileName = ([string]$profile).Trim()
                if (-not $profileName) { continue }
                $metadata = $manifestProfileStatus |
                    Where-Object { $_.profile -eq $profileName } |
                    Select-Object -First 1
                $boundStore = if ($null -ne $metadata -and $metadata.bound_stores.Count -gt 0) {
                    $metadata.bound_stores -join "、"
                } else {
                    "未解析"
                }
                [pscustomobject]@{
                    Profile = $profileName
                    Expected = $true
                    Running = @(
                        $gatewayProcesses |
                            Where-Object { $_.CommandLine -like "*-p $profileName gateway*" }
                    ).Count -gt 0
                    AllowedGroups = if ($null -eq $metadata) { "?" } else { $metadata.allowed_group_count }
                    BoundStore = $boundStore
                }
            }
            $gatewayRows | Format-Table -AutoSize
            foreach ($profile in $HermesGatewayProfiles) {
                $profileName = ([string]$profile).Trim()
                if (-not $profileName) {
                    continue
                }
                Write-Host ""
                Write-Host "[$profileName]"
                & $HermesExe -p $profileName gateway status
            }
        } catch {
            Write-Warning "读取 Hermes gateway status 失败：$($_.Exception.Message)"
        }
    } else {
        Write-Warning "未找到 Hermes 可执行文件：$HermesExe"
    }
}

Write-Host ""
Write-Host "Playwright browser path"
Reset-PanHotelWatchPlaywrightEnv
Write-Host "PLAYWRIGHT_BROWSERS_PATH=$($env:PLAYWRIGHT_BROWSERS_PATH)"
if ($env:PLAYWRIGHT_BROWSERS_PATH -match "cursor-sandbox-cache") {
    Write-Warning "Playwright 路径仍指向 Cursor 沙箱，请用 .\\scripts\\restart-worker.ps1 或 start-all.ps1 重启 worker。"
}

Write-Host ""
Write-Host "Worker tick summary (operator view)"
foreach ($workerName in $heartbeatWorkers) {
    $heartbeatPath = Get-PanHotelWatchWorkerHeartbeatPath -WorkerName $workerName
    if (-not (Test-Path $heartbeatPath)) {
        continue
    }
    try {
        $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw | ConvertFrom-Json
    } catch {
        continue
    }
    $tick = $heartbeat.last_tick_summary
    if ($null -eq $tick) {
        continue
    }
    $failed = [int]($tick.last_failed_count)
    $success = [int]($tick.last_success_count)
    if ($failed -gt 0 -and $success -eq 0) {
        Write-Warning "[$workerName] 进程心跳正常，但最近一次 tick 全部失败（$failed 次）。读价/推送可能已中断。"
    } elseif ($failed -gt 0) {
        Write-Warning "[$workerName] 最近一次 tick 有 $failed 次失败、$success 次成功。"
    }
}

$lanIp = Get-PanHotelWatchLanIp
Write-Host ""
Write-Host "LAN URLs"
Write-Host "Frontend: http://$lanIp`:$FrontendPort"
Write-Host "Backend:  http://$lanIp`:$BackendPort/api/v1/health"
