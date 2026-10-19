param(
    [ValidateSet("preview", "dev")][string]$Mode = "preview",
    [string]$HostName = "0.0.0.0",
    [int]$Port = 5173,
    [string]$ApiProxyTarget = "http://127.0.0.1:8100",
    [switch]$Build
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

$Frontend = Get-PanHotelWatchFrontendDir
$Node = Get-PanHotelWatchNode
$Npm = Get-PanHotelWatchNpm
$ViteCli = Join-Path $Frontend "node_modules\vite\bin\vite.js"

if (-not (Test-Path $ViteCli)) {
    throw "Frontend dependencies not found. Run scripts/install.ps1 first."
}

$previousApiProxyTarget = $env:VITE_API_PROXY_TARGET
$env:VITE_API_PROXY_TARGET = $ApiProxyTarget

Push-Location $Frontend
try {
    if ($Mode -eq "preview" -and ($Build -or -not (Test-Path (Join-Path $Frontend "dist\index.html")))) {
        & $Npm run build
    }

    if ($Mode -eq "preview") {
        & $Node $ViteCli preview --host $HostName --port $Port
    } else {
        & $Node $ViteCli --host $HostName --port $Port
    }
} finally {
    $env:VITE_API_PROXY_TARGET = $previousApiProxyTarget
    Pop-Location
}
