param(
    [ValidateSet("all", "backend", "worker", "feishu-listener", "frontend")][string]$Service = "all",
    [ValidateSet("out", "err", "app")][string]$Stream = "out",
    [int]$Tail = 120,
    [switch]$Follow
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

function Show-LogFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    Write-Host ""
    Write-Host "== $Path =="
    if (-not (Test-Path $Path)) {
        Write-Host "Log file does not exist yet."
        return
    }
    if ($Follow) {
        Get-Content -LiteralPath $Path -Tail $Tail -Wait
    } else {
        Get-Content -LiteralPath $Path -Tail $Tail
    }
}

Ensure-PanHotelWatchRuntimeDirs

if ($Service -eq "all") {
    if ($Follow) {
        throw "Use -Service backend|worker|frontend when -Follow is set."
    }
    foreach ($name in @("backend", "worker", "feishu-listener", "frontend")) {
        Show-LogFile -Path (Get-PanHotelWatchLogPath -Name $name -Stream "out")
        Show-LogFile -Path (Get-PanHotelWatchLogPath -Name $name -Stream "err")
    }
    Show-LogFile -Path (Join-Path (Get-PanHotelWatchAppLogDir) "ctrip_ebooking_data_center_scheduler.log")
    exit 0
}

if ($Stream -eq "app") {
    if ($Service -ne "worker") {
        throw "-Stream app is only available for -Service worker."
    }
    Show-LogFile -Path (Join-Path (Get-PanHotelWatchAppLogDir) "ctrip_ebooking_data_center_scheduler.log")
} else {
    Show-LogFile -Path (Get-PanHotelWatchLogPath -Name $Service -Stream $Stream)
}
