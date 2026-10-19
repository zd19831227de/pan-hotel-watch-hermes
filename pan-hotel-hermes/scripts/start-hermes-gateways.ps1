param(
    [string[]]$Profiles = @(),
    [string]$ManifestPath,
    [string]$HermesBasePath,
    [string]$HermesExePath,
    [string]$PythonExePath,
    [switch]$Replace
)

$ErrorActionPreference = "Stop"

$HermesProjectRoot = Split-Path $PSScriptRoot -Parent
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
$HermesExe = $HermesExePath
if (-not (Test-Path $HermesExe)) {
    throw "Hermes not found: $HermesExe"
}
if (-not (Test-Path -LiteralPath $PythonExePath)) {
    throw "Hermes Python not found: $PythonExePath"
}
if (-not (Test-Path -LiteralPath $ManifestPath)) {
    throw "Feishu profile manifest not found: $ManifestPath"
}

& $PythonExePath $ManifestTool --manifest $ManifestPath --hermes-root $HermesProjectRoot "validate"
if ($LASTEXITCODE -ne 0) { throw "Feishu profile manifest validation failed." }
$manifestProfiles = @(
    & $PythonExePath $ManifestTool --manifest $ManifestPath --hermes-root $HermesProjectRoot "list"
)
if ($LASTEXITCODE -ne 0) { throw "Could not list Feishu profiles from the manifest." }
if ($Profiles.Count -eq 0) {
    $Profiles = $manifestProfiles
} else {
    foreach ($requestedProfile in $Profiles) {
        if ($requestedProfile -notin $manifestProfiles) {
            throw "Profile is not declared in the Feishu manifest: $requestedProfile"
        }
    }
}

foreach ($profile in $Profiles) {
    $profileName = ([string]$profile).Trim()
    if (-not $profileName) {
        continue
    }
    $profileRoot = Join-Path (Join-Path $HermesBasePath "profiles") $profileName
    if (-not (Test-Path -LiteralPath $profileRoot)) {
        throw "Hermes profile not found: $profileName"
    }
    $args = @("-p", $profileName, "gateway", "run")
    if ($Replace) {
        $args += "--replace"
    }
    Write-Host "Starting Hermes Gateway profile: $profileName"
    Start-Process -FilePath $HermesExe -ArgumentList $args -WindowStyle Hidden
}

Start-Sleep -Seconds 2
& $HermesExe gateway list
