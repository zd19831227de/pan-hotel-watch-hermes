param(
    [string]$HostName = "0.0.0.0",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

if ($Port -le 0) {
    $Port = [int](Get-PanHotelWatchEnvValue -Name "PAN_HOTEL_WATCH_BACKEND_PORT" -Default "8100")
}

$Root = Get-PanHotelWatchRoot
$Backend = Get-PanHotelWatchBackendDir
$Python = Get-PanHotelWatchPython

Reset-PanHotelWatchPlaywrightEnv

Push-Location $Root
try {
    & $Python -m uvicorn app.main:app --app-dir $Backend --host $HostName --port $Port
} finally {
    Pop-Location
}
