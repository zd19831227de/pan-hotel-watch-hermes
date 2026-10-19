param(
    [string]$TaskName = "Pan Hotel Watch"
)

$ErrorActionPreference = "Stop"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    Write-Host "Scheduled task not found: $TaskName"
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Unregistered scheduled task: $TaskName"
