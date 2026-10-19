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

Remove-Item env:PYTHONHOME -ErrorAction SilentlyContinue
Reset-PanHotelWatchPlaywrightEnv

Stop-PanHotelWatchService -Name backend | Out-Null

$Root = Get-PanHotelWatchRoot
$Backend = Get-PanHotelWatchBackendDir
$Python = Get-PanHotelWatchPython
$BackendHost = Get-PanHotelWatchEnvValue -Name "BACKEND_HOST" -Default "0.0.0.0"
$BackendPort = Get-PanHotelWatchBackendPort

Set-PanHotelWatchDatabaseEnv

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $Backend
if (-not [string]::IsNullOrWhiteSpace($previousPythonPath)) {
    $env:PYTHONPATH = "$Backend;$previousPythonPath"
}

$stdout = Get-PanHotelWatchLogPath -Name backend -Stream out
$stderr = Get-PanHotelWatchLogPath -Name backend -Stream err
$argumentString = Format-StartProcessArguments -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--app-dir", $Backend, "--host", $BackendHost, "--port", "$BackendPort"
)
$process = Start-Process `
    -FilePath $Python `
    -ArgumentList $argumentString `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru
Set-PanHotelWatchPid -Name backend -ProcessId $process.Id
Start-Sleep -Milliseconds 900

$started = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
if ($null -eq $started) {
    Write-Warning "Backend exited quickly. Check $stderr"
    exit 1
}

if (-not (Wait-PanHotelWatchHttp -Url "http://127.0.0.1:$BackendPort/api/v1/health" -TimeoutSeconds 25)) {
    Write-Warning "Backend did not become healthy on http://127.0.0.1:$BackendPort/api/v1/health. Check $stderr"
    exit 1
}

Write-Host "Started backend with PID $($process.Id) (database: $env:DATABASE_URL)."
