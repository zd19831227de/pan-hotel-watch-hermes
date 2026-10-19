# Configure Hermes to use DeepSeek with an API key.
# Usage:
#   .\scripts\setup-deepseek.ps1 -ApiKey "sk-xxxxxxxx"
#   .\scripts\setup-deepseek.ps1 -ApiKey "sk-xxxxxxxx" -Profile jingli-ai -RestartGateway
# Or enter the key interactively without echo:
#   .\scripts\setup-deepseek.ps1

param(
    [string]$ApiKey,
    [ValidateSet("deepseek-v4-flash", "deepseek-v4-pro")]
    [string]$Model = "deepseek-v4-flash",
    [string]$Profile = "default",
    [switch]$RestartGateway
)

$ErrorActionPreference = "Stop"

$HermesExe = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\hermes.exe"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

function Resolve-HermesProfileRoot {
    param([string]$ProfileName)
    $base = Join-Path $env:LOCALAPPDATA "hermes"
    $name = ([string]$ProfileName).Trim()
    if ([string]::IsNullOrWhiteSpace($name) -or $name -eq "default") {
        return $base
    }
    return (Join-Path (Join-Path $base "profiles") $name)
}

$HermesRoot = Resolve-HermesProfileRoot -ProfileName $Profile
$env:HERMES_HOME = $HermesRoot
if ($Profile -ne "default" -and -not (Test-Path -LiteralPath $HermesRoot)) {
    throw "Hermes profile not found: $Profile ($HermesRoot)"
}
if (-not (Test-Path -LiteralPath $HermesRoot)) {
    throw "Hermes root not found: $HermesRoot"
}

if (-not (Test-Path $HermesExe)) {
    throw "Hermes not found: $HermesExe. Install Hermes first."
}

if (-not $ApiKey) {
    $secure = Read-Host "Enter DeepSeek API Key (sk-...)" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $ApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

$ApiKey = $ApiKey.Trim()
if (-not $ApiKey) {
    throw "API key cannot be empty. Create one at https://platform.deepseek.com/api_keys."
}

Write-Host "Setting DeepSeek API key ..."
& $HermesExe config set DEEPSEEK_API_KEY $ApiKey | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Failed to set DEEPSEEK_API_KEY for Hermes profile: $Profile"
}

Write-Host "Setting model: deepseek/$Model ..."
& $HermesExe config set "model" "deepseek/$Model" | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Failed to set model for Hermes profile: $Profile"
}

Write-Host "Registering MCP and Skill ..."
& (Join-Path $ProjectRoot "scripts\register-hermes.ps1") -Profile $Profile -SkipModelHint -RestartGateway:$RestartGateway
if ($LASTEXITCODE -ne 0) {
    throw "Failed to register Hermes profile: $Profile"
}

Write-Host "Hermes profile: $Profile"
if (-not $RestartGateway) {
    Write-Host "Gateway was not restarted. Add -RestartGateway to apply the config immediately."
}

Write-Host ""
Write-Host "Verify:"
Write-Host "  hermes doctor"
Write-Host "  hermes"
Write-Host "  # Prompt: Reply with one short sentence."
Write-Host "  # Run: /mcp list"
