$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

Ensure-PanHotelWatchRuntimeDirs

Remove-Item env:PYTHONHOME -ErrorAction SilentlyContinue
Stop-PanHotelWatchService -Name worker | Out-Null
Reset-PanHotelWatchPlaywrightEnv
Set-PanHotelWatchDatabaseEnv

Write-Host "PLAYWRIGHT_BROWSERS_PATH=$($env:PLAYWRIGHT_BROWSERS_PATH)"

$Root = Get-PanHotelWatchRoot
$Backend = Get-PanHotelWatchBackendDir
$Python = Get-PanHotelWatchPython

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $Backend
if (-not [string]::IsNullOrWhiteSpace($previousPythonPath)) {
    $env:PYTHONPATH = "$Backend;$previousPythonPath"
}

$stdout = Get-PanHotelWatchLogPath -Name worker -Stream out
$stderr = Get-PanHotelWatchLogPath -Name worker -Stream err
$process = Start-Process `
    -FilePath $Python `
    -ArgumentList "-m app.workers.read_only_scheduler" `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru
Set-PanHotelWatchPid -Name worker -ProcessId $process.Id
Start-Sleep -Milliseconds 700

$started = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
if ($null -eq $started) {
    Write-Warning "Worker exited quickly. Check $stderr"
    exit 1
}

Write-Host "Started worker with PID $($process.Id)."
