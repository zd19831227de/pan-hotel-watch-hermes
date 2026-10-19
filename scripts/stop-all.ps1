$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

Ensure-PanHotelWatchRuntimeDirs

foreach ($service in @("frontend", "feishu-listener", "worker", "backend")) {
    $stopped = Stop-PanHotelWatchService -Name $service
    if ($stopped) {
        Write-Host "Stopped $service."
    } else {
        Write-Host "$service was not running."
    }
}
