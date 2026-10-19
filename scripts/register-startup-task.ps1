param(
    [string]$TaskName = "Pan Hotel Watch",
    [ValidateSet("AtLogOn", "AtStartup")][string]$Trigger = "AtLogOn",
    [ValidateSet("preview", "dev")][string]$FrontendMode = "preview",
    [switch]$RunElevated
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $PSScriptRoot "start-all.ps1"
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -FrontendMode $FrontendMode"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $Root
if ($Trigger -eq "AtStartup") {
    $taskTrigger = New-ScheduledTaskTrigger -AtStartup
} else {
    $taskTrigger = New-ScheduledTaskTrigger -AtLogOn
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 365)

if ($RunElevated) {
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $taskTrigger -Settings $settings -Principal $principal -Force | Out-Null
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $taskTrigger -Settings $settings -Force | Out-Null
}

Write-Host "Registered scheduled task: $TaskName ($Trigger)"
Write-Host "Action: powershell.exe $arguments"
if ($Trigger -eq "AtStartup") {
    Write-Host "Note: real browser reads are most reliable after the Windows user has logged in."
}
