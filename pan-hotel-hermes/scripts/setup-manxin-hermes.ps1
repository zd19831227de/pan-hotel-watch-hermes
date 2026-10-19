# Convert the former quanji-ai Feishu app/profile to serve Manxin hotel.
# Usage:
#   .\scripts\setup-manxin-hermes.ps1
#   .\scripts\setup-manxin-hermes.ps1 -StartGateway

param(
    [string]$ManxinChatId = "oc_11111111111111111111111111111111",
    [string]$SourceProfile = "quanji-ai",
    [string]$TargetProfile = "manxin-ai",
    [switch]$StartGateway
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$WatchRoot = Split-Path $ProjectRoot -Parent
$BackendEnv = if ($env:PAN_HOTEL_WATCH_ROOT) {
    Join-Path $env:PAN_HOTEL_WATCH_ROOT ".env"
} else {
    Join-Path $WatchRoot ".env"
}
$HermesExe = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\hermes.exe"

function Read-DotEnvValue {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in Get-Content $Path) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '=(.*)$')) {
            return $Matches[1].Trim().Trim('"').Trim([char]39)
        }
    }
    return $null
}

function Set-DotEnvValue {
    param([string]$Path, [string]$Key, [string]$Value)
    $line = "$Key=$Value"
    if (Test-Path $Path) {
        $lines = Get-Content -LiteralPath $Path
        $matched = $false
        $updated = foreach ($existing in $lines) {
            if ($existing -match "^$([regex]::Escape($Key))=") {
                $matched = $true
                $line
            } else {
                $existing
            }
        }
        if (-not $matched) { $updated += $line }
        Set-Content -LiteralPath $Path -Value $updated -Encoding UTF8
    } else {
        Set-Content -LiteralPath $Path -Value @($line) -Encoding UTF8
    }
}

if (-not (Test-Path $HermesExe)) {
    throw "Hermes not found: $HermesExe"
}

$sourceEnv = Join-Path $env:LOCALAPPDATA "hermes\profiles\$SourceProfile\.env"
$appId = Read-DotEnvValue -Path $sourceEnv -Key "FEISHU_APP_ID"
$appSecret = Read-DotEnvValue -Path $sourceEnv -Key "FEISHU_APP_SECRET"
if (-not $appId -or -not $appSecret) {
    throw "Missing Feishu credentials in $sourceEnv. Configure $SourceProfile first."
}

$targetRoot = Join-Path $env:LOCALAPPDATA "hermes\profiles\$TargetProfile"
if (-not (Test-Path $targetRoot)) {
    Write-Host "Creating Hermes profile: $TargetProfile"
    & $HermesExe profile create $TargetProfile --clone
}

& (Join-Path $PSScriptRoot "register-hermes.ps1") -Profile $TargetProfile -ResetMemory -SkipModelHint

$secureSecret = ConvertTo-SecureString $appSecret -AsPlainText -Force
& (Join-Path $PSScriptRoot "setup-feishu-gateway.ps1") `
    -Profile $TargetProfile `
    -AppId $appId `
    -AppSecret $secureSecret `
    -AllowedChats $ManxinChatId

$updateMaps = if ($env:PAN_HOTEL_WATCH_ROOT) {
    Join-Path $env:PAN_HOTEL_WATCH_ROOT ".runtime\update_manxin_feishu_maps.py"
} else {
    Join-Path $WatchRoot ".runtime\update_manxin_feishu_maps.py"
}
$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = Join-Path $WatchRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path $pythonExe)) {
    throw "Python venv not found for map update."
}

& $pythonExe $updateMaps `
    --env-path $BackendEnv `
    --chat-id $ManxinChatId `
    --profile $TargetProfile `
    --app-id $appId `
    --app-secret $appSecret `
    --remove-chat-id "oc_33333333333333333333333333333333" `
    --remove-profile $SourceProfile

Write-Host "Updated backend Feishu routing for $TargetProfile -> $ManxinChatId"
Write-Host ""
Write-Host "IMPORTANT: The Manxin group must use the Feishu bot for app $appId (former quanji app)."
Write-Host "If the group still has the jingli bot (cli_a11111111111111), @mentions will be dropped as feishu_chat_not_allowed."
Write-Host "Add the Manxin bot to chat $ManxinChatId and @ that bot when testing."

if ($StartGateway) {
    & (Join-Path $PSScriptRoot "start-hermes-gateways.ps1") -Profiles @($TargetProfile, "jingli-ai") -Replace
} else {
    Write-Host "Start with:"
    Write-Host "  .\pan-hotel-hermes\scripts\start-hermes-gateways.ps1 -Profiles manxin-ai,jingli-ai -Replace"
}
