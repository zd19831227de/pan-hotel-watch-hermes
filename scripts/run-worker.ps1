param(
    [ValidateSet("scheduler", "feishu-listener")][string]$Service = "scheduler"
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

$Root = Get-PanHotelWatchRoot
$Backend = Get-PanHotelWatchBackendDir
$Python = Get-PanHotelWatchPython

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $Backend
if (-not [string]::IsNullOrWhiteSpace($previousPythonPath)) {
    $env:PYTHONPATH = "$Backend;$previousPythonPath"
}
Reset-PanHotelWatchPlaywrightEnv

Push-Location $Root
try {
    if ($Service -eq "feishu-listener") {
        & $Python -m app.workers.feishu_listener
    } else {
        & $Python -m app.workers.read_only_scheduler
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
