#Requires -Version 5.1
<#
.SYNOPSIS
  Dry-run or apply one-shot migration of misplaced Chrome/Playwright browser profiles.

.DESCRIPTION
  Copies real OTA profiles from known relative-path fallout locations into the
  canonical repo BROWSER_STATE_DIR. Default is dry-run. Close Pan Hotel Watch
  Chrome windows before -Apply.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

if ($Apply -and $DryRun) {
    throw "Use either -DryRun or -Apply, not both."
}

$modeArgs = @()
if ($Apply) {
    $modeArgs += "--apply"
    Write-Host "Applying browser profile migration (close Chrome first)..."
} else {
    Write-Host "Dry-run browser profile migration..."
}

$env:PYTHONPATH = "backend"
& .\.venv\Scripts\python.exe -m app.domain.browser.profile_migration_cli @modeArgs
exit $LASTEXITCODE
