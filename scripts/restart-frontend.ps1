param(
    [string]$FrontendHost = "0.0.0.0",
    [int]$FrontendPort = 5173,
    [ValidateSet("preview", "dev")][string]$FrontendMode = "preview",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

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

Ensure-PanHotelWatchRuntimeDirs

Stop-PanHotelWatchService -Name frontend | Out-Null

$Frontend = Get-PanHotelWatchFrontendDir
$Node = Get-PanHotelWatchNode
$Npm = Get-PanHotelWatchNpm
$ViteCli = Join-Path $Frontend "node_modules\vite\bin\vite.js"
$BackendPort = Get-PanHotelWatchBackendPort

if (-not (Test-Path $ViteCli)) {
    throw "Frontend dependencies not found. Run scripts/install.ps1 first."
}

if ($FrontendMode -eq "preview" -and (-not $SkipBuild)) {
    Push-Location $Frontend
    try {
        & $Npm run build
    } finally {
        Pop-Location
    }
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
    $stdout = Get-PanHotelWatchLogPath -Name frontend -Stream out
    $stderr = Get-PanHotelWatchLogPath -Name frontend -Stream err
    $argumentString = Format-StartProcessArguments -ArgumentList $frontendArgs
    $process = Start-Process `
        -FilePath $Node `
        -ArgumentList $argumentString `
        -WorkingDirectory $Frontend `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Set-PanHotelWatchPid -Name frontend -ProcessId $process.Id
    Start-Sleep -Milliseconds 900

    $started = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if ($null -eq $started) {
        Write-Warning "Frontend exited quickly. Check $stderr"
        exit 1
    }

    if (-not (Wait-PanHotelWatchHttp -Url "http://127.0.0.1:$FrontendPort" -TimeoutSeconds 25)) {
        Write-Warning "Frontend did not become reachable on http://127.0.0.1:$FrontendPort. Check $stderr"
        exit 1
    }

    Write-Host "Started frontend with PID $($process.Id) at http://127.0.0.1:$FrontendPort (API proxy -> http://127.0.0.1:$BackendPort)."
} finally {
    $env:VITE_API_PROXY_TARGET = $previousApiProxyTarget
}
