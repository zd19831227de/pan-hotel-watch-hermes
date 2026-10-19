param(
    [string]$BackendHost = "0.0.0.0",
    [int]$BackendPort = 0,
    [string]$FrontendHost = "0.0.0.0",
    [int]$FrontendPort = 5173,
    [ValidateSet("preview", "dev")][string]$FrontendMode = "preview",
    [switch]$RebuildFrontend
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

if ($BackendPort -le 0) {
    $BackendPort = [int](Get-PanHotelWatchEnvValue -Name "PAN_HOTEL_WATCH_BACKEND_PORT" -Default "8100")
}

function Format-StartProcessArguments {
    param([Parameter(Mandatory = $true)][string[]]$ArgumentList)
    return ($ArgumentList | ForEach-Object {
        $value = [string]$_
        if ($value -match '[\s"]') {
            '"' + ($value -replace '"', '\"') + '"'
        } else {
            $value
        }
    }) -join ' '
}

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    $existing = Get-PanHotelWatchProcess -Name $Name
    if ($null -ne $existing) {
        Write-Host "$Name already running with PID $($existing.Id)."
        return
    }
    Remove-PanHotelWatchPid -Name $Name

    $stdout = Get-PanHotelWatchLogPath -Name $Name -Stream "out"
    $stderr = Get-PanHotelWatchLogPath -Name $Name -Stream "err"
    $argumentString = Format-StartProcessArguments -ArgumentList $ArgumentList
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $argumentString `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Set-PanHotelWatchPid -Name $Name -ProcessId $process.Id
    Start-Sleep -Milliseconds 700
    $started = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if ($null -eq $started) {
        Write-Warning "$Name exited quickly. Check $stdout and $stderr."
    } else {
        Write-Host "Started $Name with PID $($process.Id)."
    }
}

Ensure-PanHotelWatchRuntimeDirs

$Root = Get-PanHotelWatchRoot
$Backend = Get-PanHotelWatchBackendDir
$Frontend = Get-PanHotelWatchFrontendDir
$Python = Get-PanHotelWatchPython
$Node = Get-PanHotelWatchNode
$Npm = Get-PanHotelWatchNpm
$ViteCli = Join-Path $Frontend "node_modules\vite\bin\vite.js"

if (-not (Test-Path $ViteCli)) {
    throw "Frontend dependencies not found. Run scripts/install.ps1 first."
}

if ($FrontendMode -eq "preview" -and ($RebuildFrontend -or -not (Test-Path (Join-Path $Frontend "dist\index.html")))) {
    Push-Location $Frontend
    try {
        & $Npm run build
    } finally {
        Pop-Location
    }
}

Assert-PanHotelWatchPortAvailable -Port $BackendPort -ServiceName "backend"
Assert-PanHotelWatchPortAvailable -Port $FrontendPort -ServiceName "frontend"

Reset-PanHotelWatchPlaywrightEnv

Start-ManagedProcess `
    -Name "backend" `
    -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--app-dir", $Backend, "--host", $BackendHost, "--port", "$BackendPort") `
    -WorkingDirectory $Root

if (-not (Wait-PanHotelWatchHttp -Url "http://127.0.0.1:$BackendPort/api/v1/health" -TimeoutSeconds 25)) {
    Write-Warning "Backend did not become healthy on http://127.0.0.1:$BackendPort/api/v1/health. Check .\scripts\show-logs.ps1 -Service backend -Stream err."
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $Backend
if (-not [string]::IsNullOrWhiteSpace($previousPythonPath)) {
    $env:PYTHONPATH = "$Backend;$previousPythonPath"
}
try {
    Start-ManagedProcess `
        -Name "worker" `
        -FilePath $Python `
        -ArgumentList @("-m", "app.workers.read_only_scheduler") `
        -WorkingDirectory $Root

    $feishuAgentMode = (Get-PanHotelWatchEnvValue -Name "FEISHU_AGENT_MODE" -Default "hermes").Trim().ToLower()
    if ($feishuAgentMode -eq "builtin") {
        Start-ManagedProcess `
            -Name "feishu-listener" `
            -FilePath $Python `
            -ArgumentList @("-m", "app.workers.feishu_listener") `
            -WorkingDirectory $Root
    } else {
        Write-Host "FEISHU_AGENT_MODE=$feishuAgentMode：飞书入口由 Hermes Gateway 接管，跳过内置 feishu-listener。"
        Write-Host "如需后端内置大模型回复，请在 .env 设置 FEISHU_AGENT_MODE=builtin 后重启。"
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}

$frontendArgs = @()
if ($FrontendMode -eq "preview") {
    $frontendArgs = @($ViteCli, "preview", "--host", $FrontendHost, "--port", "$FrontendPort")
} else {
    $frontendArgs = @($ViteCli, "--host", $FrontendHost, "--port", "$FrontendPort")
}

$previousApiProxyTarget = $env:VITE_API_PROXY_TARGET
$env:VITE_API_PROXY_TARGET = "http://127.0.0.1:$BackendPort"
try {
    Start-ManagedProcess `
        -Name "frontend" `
        -FilePath $Node `
        -ArgumentList $frontendArgs `
        -WorkingDirectory $Frontend
} finally {
    $env:VITE_API_PROXY_TARGET = $previousApiProxyTarget
}

$lanIp = Get-PanHotelWatchLanIp
Write-Host ""
Write-Host "Frontend LAN URL: http://$lanIp`:$FrontendPort"
Write-Host "Backend health:   http://$lanIp`:$BackendPort/api/v1/health"
Write-Host "Logs:             .\scripts\show-logs.ps1 -Service all"
