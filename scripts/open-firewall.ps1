param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"

function Ensure-FirewallRule {
    param(
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $existing = Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Write-Host "Firewall rule exists: $DisplayName"
        return
    }
    New-NetFirewallRule `
        -DisplayName $DisplayName `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $Port `
        -Action Allow | Out-Null
    Write-Host "Created firewall rule: $DisplayName on TCP $Port"
}

Ensure-FirewallRule -DisplayName "Pan Hotel Watch Backend $BackendPort" -Port $BackendPort
Ensure-FirewallRule -DisplayName "Pan Hotel Watch Frontend $FrontendPort" -Port $FrontendPort
