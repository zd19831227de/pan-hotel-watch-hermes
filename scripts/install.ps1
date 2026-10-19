$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Venv = Join-Path $Root ".venv"
$Runtime = Join-Path $Root ".runtime"

if (-not (Test-Path $Venv)) {
    python -m venv $Venv
}

New-Item -ItemType Directory -Force -Path `
    (Join-Path $Runtime "logs"), `
    (Join-Path $Runtime "pids") | Out-Null

$Python = Join-Path $Venv "Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Backend "requirements.txt")
& $Python -m playwright install chromium

$HermesDir = Join-Path $Root "pan-hotel-hermes"
$HermesVenv = Join-Path $HermesDir ".venv"
if (-not (Test-Path $HermesVenv)) {
    & $Python -m venv $HermesVenv
}
$HermesPython = Join-Path $HermesVenv "Scripts\python.exe"
& $HermesPython -m pip install --upgrade pip
& $HermesPython -m pip install -e $HermesDir

Push-Location $Frontend
try {
    npm install
    npm run build
} finally {
    Pop-Location
}

Write-Host "Install complete."
