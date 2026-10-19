# Configure Feishu credentials for one Hermes Gateway profile.
# Usage:
#   $secret = Read-Host "Feishu App Secret" -AsSecureString
#   .\scripts\setup-feishu-gateway.ps1 -Profile quanji-ai -AppId "cli_xxx" -AppSecret $secret -AllowedChats "oc_xxx"

param(
    [string]$Profile = "default",
    [string]$AppId,
    [securestring]$AppSecret,
    [string]$AllowedChats,
    [string]$ManifestPath,
    [string]$HermesBasePath,
    [string]$HermesExePath,
    [string]$PythonExePath,
    [string]$BackendEnvPath,
    [switch]$StartGateway,
    [ValidateSet("open", "allowlist", "disabled")]
    [string]$GroupPolicy = "allowlist"
)

$ErrorActionPreference = "Stop"

$WatchRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$HermesProjectRoot = Split-Path $PSScriptRoot -Parent
$WatchEnv = Join-Path $WatchRoot ".env"
$ManifestTool = Join-Path $PSScriptRoot "feishu_profiles.py"
if (-not $ManifestPath) {
    $ManifestPath = Join-Path $HermesProjectRoot "config\feishu-profiles.yaml"
}
if (-not $HermesBasePath) {
    $HermesBasePath = Join-Path $env:LOCALAPPDATA "hermes"
}
if (-not $HermesExePath) {
    $HermesExePath = Join-Path $HermesBasePath "hermes-agent\venv\Scripts\hermes.exe"
}
if (-not $PythonExePath) {
    $PythonExePath = Join-Path $HermesBasePath "hermes-agent\venv\Scripts\python.exe"
}
if (-not $BackendEnvPath) {
    $BackendEnvPath = $WatchEnv
}
$HermesExe = $HermesExePath

function Resolve-HermesProfileRoot {
    param(
        [string]$ProfileName,
        [string]$BasePath = $HermesBasePath
    )
    $name = ([string]$ProfileName).Trim()
    if ([string]::IsNullOrWhiteSpace($name) -or $name -eq "default") {
        return $BasePath
    }
    return (Join-Path (Join-Path $BasePath "profiles") $name)
}

function Read-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key
    )
    if (-not (Test-Path $Path)) {
        return $null
    }
    foreach ($line in Get-Content $Path) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '=(.*)$')) {
            return $Matches[1].Trim().Trim('"').Trim([char]39)
        }
    }
    return $null
}

function Set-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )
    $lines = @()
    $found = $false
    if (Test-Path $Path) {
        foreach ($line in Get-Content $Path) {
            if ($line -match ('^\s*' + [regex]::Escape($Key) + '=')) {
                $lines += "$Key=$Value"
                $found = $true
            } else {
                $lines += $line
            }
        }
    }
    if (-not $found) {
        $lines += "$Key=$Value"
    }
    Set-Content -Path $Path -Value $lines -Encoding UTF8
}

function Resolve-HomeChannelFromAllowedChats {
    param([string]$AllowedChatsText)
    $items = @(
        ([string]$AllowedChatsText).Split(",", [System.StringSplitOptions]::RemoveEmptyEntries) |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    if ($items.Count -eq 1) {
        return $items[0]
    }
    return $null
}

function Convert-SecureStringToPlainText {
    param([securestring]$Secret)
    if ($null -eq $Secret) { return "" }
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

if (-not (Test-Path $HermesExe)) {
    throw "Hermes not found: $HermesExe"
}

$HermesRoot = Resolve-HermesProfileRoot -ProfileName $Profile -BasePath $HermesBasePath
if ($Profile -ne "default" -and -not (Test-Path -LiteralPath $HermesRoot)) {
    throw "Hermes profile not found: $Profile ($HermesRoot). Create it first with: hermes profile create $Profile --clone"
}
New-Item -ItemType Directory -Force -Path $HermesRoot | Out-Null
$HermesEnv = Join-Path $HermesRoot ".env"

if (Test-Path -LiteralPath $ManifestPath) {
    if (-not (Test-Path -LiteralPath $PythonExePath)) {
        throw "Hermes Python not found: $PythonExePath"
    }
    & $PythonExePath $ManifestTool --manifest $ManifestPath --hermes-root $HermesProjectRoot validate
    if ($LASTEXITCODE -ne 0) { throw "Feishu profile manifest validation failed." }
    & $PythonExePath $ManifestTool --manifest $ManifestPath --hermes-root $HermesProjectRoot "write-backend-env" --dotenv $BackendEnvPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to update backend Feishu credential mappings." }
    & $PythonExePath $ManifestTool --manifest $ManifestPath --hermes-root $HermesProjectRoot apply --profile $Profile --profile-root $HermesRoot --backend-dotenv $BackendEnvPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to apply Feishu profile configuration: $Profile" }
} else {
    Write-Warning "Feishu profile manifest not found; using the legacy single-profile setup path."
    $appIdText = ([string]$AppId).Trim()
    if (-not $appIdText) {
        $appIdText = Read-DotEnvValue -Path $HermesEnv -Key "FEISHU_APP_ID"
    }
    if (-not $appIdText) {
        $appIdText = Read-DotEnvValue -Path $WatchEnv -Key "FEISHU_APP_ID"
    }
    $secretText = Convert-SecureStringToPlainText -Secret $AppSecret
    if (-not $secretText) {
        $secretText = Read-DotEnvValue -Path $HermesEnv -Key "FEISHU_APP_SECRET"
    }
    if (-not $secretText) {
        $secretText = Read-DotEnvValue -Path $WatchEnv -Key "FEISHU_APP_SECRET"
    }
    if (-not $appIdText -or -not $secretText) {
        throw "Missing Feishu app credentials. Create the manifest or pass -AppId and -AppSecret."
    }

    $allowedChatsText = ([string]$AllowedChats).Trim()
    if (-not $allowedChatsText) {
        $allowedChatsText = Read-DotEnvValue -Path $HermesEnv -Key "FEISHU_ALLOWED_CHATS"
    }
    $homeChannel = Resolve-HomeChannelFromAllowedChats -AllowedChatsText $allowedChatsText
    Set-DotEnvValue -Path $HermesEnv -Key "FEISHU_APP_ID" -Value $appIdText
    Set-DotEnvValue -Path $HermesEnv -Key "FEISHU_APP_SECRET" -Value $secretText
    Set-DotEnvValue -Path $HermesEnv -Key "FEISHU_DOMAIN" -Value "feishu"
    Set-DotEnvValue -Path $HermesEnv -Key "FEISHU_CONNECTION_MODE" -Value "websocket"
    Set-DotEnvValue -Path $HermesEnv -Key "FEISHU_GROUP_POLICY" -Value $GroupPolicy
    if ($allowedChatsText) {
        Set-DotEnvValue -Path $HermesEnv -Key "FEISHU_ALLOWED_CHATS" -Value $allowedChatsText
    }
    if ($homeChannel) {
        Set-DotEnvValue -Path $HermesEnv -Key "FEISHU_HOME_CHANNEL" -Value $homeChannel
    }
}
Write-Host "Done. Secret values were not printed."

Write-Host ""
Write-Host "Pairing note: if users see 'I don't recognize you yet', approve the Feishu pairing code."
Write-Host "Do not run watch feishu-listener and Hermes gateway on the same app."

if ($StartGateway) {
    Write-Host "Starting Hermes Gateway profile: $Profile"
    $gatewayArgs = if ($Profile -eq "default") { @("gateway", "run", "--replace") } else { @("-p", $Profile, "gateway", "run", "--replace") }
    Start-Process -FilePath $HermesExe -ArgumentList $gatewayArgs -WindowStyle Hidden
} else {
    Write-Host "Start gateway with:"
    if ($Profile -eq "default") {
        Write-Host "  hermes gateway run --replace"
    } else {
        Write-Host "  hermes -p $Profile gateway run --replace"
    }
}
