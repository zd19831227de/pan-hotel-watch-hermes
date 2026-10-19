# Export Pan Hotel Watch ENTIRE project tree into a portable migration archive.
#
# Goal: a full-fidelity migration pack — source, .runtime (browser cache +
# models + login state), venvs, node_modules, database, credentials, configs —
# everything except pure garbage. Unzip on the new machine and you have the
# same tree. No content filtering layer.
#
# What IS packed (full copy):
#   - source code, .env (credentials), Feishu manifest, pan_hotel_watch.db
#   - .runtime/browser-state/ ENTIRELY (cookies + login state + cache + models)
#   - .venv/ and pan-hotel-hermes/.venv/ (Python virtualenvs)
#   - frontend/node_modules, frontend/dist
#   - output/ (playwright artifacts), everything else under the repo
#
# What is NOT packed (pure garbage / regenerable / version-control):
#   .git (use git clone on the new machine — toggle with -IncludeGit),
#   __pycache__ / *.pyc, .zcode/ (agent session artifacts),
#   .runtime/logs, .runtime/pids, .runtime/backups, *.lock (lock markers).
#
# IMPORTANT before running:
#   Stop the backend, worker, Hermes Gateway, and any Playwright/Chrome browser
#   first. Browser profile files (Cookies etc.) are held open while the browser
#   runs and cannot be copied. Pass -StopProcesses to let this script stop the
#   detected processes for you.
#
# Cross-machine caveats (printed again at the end):
#   - venvs may not survive if Python 3.11 is installed at a different path on
#     the new machine -> rerun scripts\install.bat to rebuild them.
#   - Chromium login cookies are DPAPI-encrypted for the current Windows user;
#     if the new machine/user cannot decrypt them, re-login the OTA accounts.
#
# Usage:
#   .\scripts\export-state.ps1                       # stops detected procs, packs to output\
#   .\scripts\export-state.ps1 -StopProcesses        # explicitly stop backend/worker/gateway/chrome
#   .\scripts\export-state.ps1 -IncludeGit           # also pack .git history
#   .\scripts\export-state.ps1 -OutputDir D:\backup -KeepStaging

param(
    [string]$OutputDir,
    [switch]$StopProcesses,
    [switch]$IncludeGit,
    [switch]$IncludeOutput,
    [switch]$KeepStaging,
    [switch]$Force,     # proceed even if processes are still running (robocopy skips locked files)
    [switch]$DryRun     # robocopy /L list-only, no real copy, no zip (validates scope)
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

function Get-PanHotelRunningProcesses {
    $found = @()
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -or $_.CommandLine }
    foreach ($p in $procs) {
        $exe = [string]$p.ExecutablePath
        $cmd = [string]$p.CommandLine
        $name = [string]$p.Name
        # Python running our backend / worker / MCP
        if ($name -match 'python(w)?\.exe$' -and ($cmd -match 'pan-?hotel' -or $cmd -match 'uvicorn' -or $cmd -match 'read_only_scheduler' -or $cmd -match 'pan_hotel_mcp')) {
            $found += [pscustomobject]@{ Id = $p.ProcessId; Name = $name; Detail = 'python (pan-hotel)' }
            continue
        }
        # Hermes gateway
        if ($name -match 'hermes\.exe$' -and ($cmd -match 'gateway' -or $cmd -match 'pan-?hotel')) {
            $found += [pscustomobject]@{ Id = $p.ProcessId; Name = $name; Detail = 'hermes gateway' }
            continue
        }
        # Playwright/Chrome launched against our browser-state profile
        if ($name -match 'chrome\.exe$' -and $cmd -match 'browser-state') {
            $found += [pscustomobject]@{ Id = $p.ProcessId; Name = $name; Detail = 'chrome (playwright profile)' }
            continue
        }
        # node frontend dev / build
        if ($name -match 'node\.exe$' -and $cmd -match 'pan-?hotel') {
            $found += [pscustomobject]@{ Id = $p.ProcessId; Name = $name; Detail = 'node (frontend)' }
            continue
        }
    }
    return $found
}

# --- 1. process check --------------------------------------------------------
$running = Get-PanHotelRunningProcesses
if ($running) {
    Write-Warning "Detected running pan-hotel-watch processes (their files may be locked):"
    $running | Format-Table Id, Name, Detail -AutoSize | Out-String | Write-Host
    if ($StopProcesses) {
        Write-Host "Stopping detected processes (-StopProcesses)..."
        foreach ($r in $running) {
            try { Stop-Process -Id $r.Id -Force -ErrorAction Stop; Write-Host "  stopped PID $($r.Id) ($($r.Detail))" }
            catch { Write-Warning "  could not stop PID $($r.Id): $($_.Exception.Message)" }
        }
        Start-Sleep -Seconds 2
        $still = Get-PanHotelRunningProcesses
        if ($still) {
            Write-Warning "Still running after stop attempt:"
            $still | Format-Table Id, Name, Detail -AutoSize | Out-String | Write-Host
            if (-not $Force) {
                throw "Processes still running. Stop them manually, or rerun with -Force (locked files will be skipped)."
            }
        }
    } elseif (-not $Force) {
        throw "Refusing to pack while processes are running (Cookies/profile files are locked). Rerun with -StopProcesses (auto-stop) or -Force (skip locked files)."
    } else {
        Write-Warning "-Force: continuing; locked files will be skipped by robocopy."
    }
}

# --- 2. staging via robocopy (tolerates locked files) -----------------------
if (-not $OutputDir) { $OutputDir = Join-Path $RepoRoot "output" }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).Path

$ZipPath = Join-Path $OutputDir "pan-hotel-watch-migrate-$timestamp.zip"
$Staging = Join-Path $env:TEMP "phw-migrate-staging-$timestamp"
$RoboLog = Join-Path $env:TEMP "phw-robocopy-$timestamp.log"
if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Staging | Out-Null

$excludeDirs = @(
    "__pycache__",
    ".zcode",
    "$RepoRoot\.runtime\logs",
    "$RepoRoot\.runtime\pids",
    "$RepoRoot\.runtime\backups"
)
if (-not $IncludeGit) { $excludeDirs += "$RepoRoot\.git" }
if (-not $IncludeOutput) { $excludeDirs += "$RepoRoot\output" }
$excludeFiles = @("*.pyc", "*.pyo", "*.lock")

Write-Host "Mirroring repo -> staging via robocopy (this copies the full tree incl. .runtime + venvs)..."
if ($DryRun) { Write-Host "(DryRun: robocopy /L — no real copy, no zip)" }
# /E copy subdirs incl empty  /NFL /NDL quiet listing  /NP no progress  /R:1 /W:1 retry once
# /XD exclude dirs  /XF exclude files  /LOG robocopy log  /L list-only (DryRun)
$roboArgs = @($RepoRoot, $Staging, "/E", "/NFL", "/NDL", "/NP", "/R:1", "/W:1", "/NJH", "/LOG:$RoboLog")
if ($DryRun) { $roboArgs += "/L" }
$roboArgs += ($excludeDirs | ForEach-Object { "/XD"; $_ })
$roboArgs += ($excludeFiles | ForEach-Object { "/XF"; $_ })
& robocopy @roboArgs | Out-Null
$roboExit = $LASTEXITCODE
# robocopy: 0-7 success (<=7), >=8 failure. Skipped files are normal exit 1/3.
if ($roboExit -ge 8) {
    Write-Warning "robocopy reported errors (exit $roboExit). See: $RoboLog"
}

# parse robocopy summary (locale-flexible: EN "Files :" or ZH "文件:")
$skippedFiles = 0; $failedFiles = 0; $copiedFiles = 0
if (Test-Path -LiteralPath $RoboLog) {
    # robocopy writes the system ANSI codepage (GBK on zh-CN). -Encoding Default
    # decodes it correctly so the localized "文件:" label matches.
    $logText = Get-Content -LiteralPath $RoboLog -Raw -Encoding Default
    $filesLine = ($logText -split "`r?`n" | Where-Object { $_ -match '^\s*(Files|文件)\s*:' }) | Select-Object -First 1
    if ($filesLine) {
        # strip label + comma; extract integer runs (file counts have no units)
        $cleaned = $filesLine -replace '(Files|文件)\s*:', '' -replace ',', ''
        $nums = [regex]::Matches($cleaned, '\d+') | ForEach-Object { [int64]$_.Value }
        # robocopy column order: Total Copied Skipped Mismatch FAILED Extras
        if ($nums.Count -ge 5) {
            $copiedFiles  = $nums[1]
            $skippedFiles = $nums[2]
            $failedFiles  = $nums[4]
        }
    }
}

# --- 3. manifest ------------------------------------------------------------
$manifestLines = @(
    "Pan Hotel Watch full migration archive"
    "Exported: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "Source repo: $RepoRoot"
    "Machine: $env:COMPUTERNAME  User: $env:USERNAME"
    ""
    "Robocopy: copied=$copiedFiles skipped=$skippedFiles failed=$failedFiles (exit $roboExit)"
    "Excluded: .git (toggle -IncludeGit), __pycache__, *.pyc, *.lock, .zcode,"
    "          .runtime/logs, .runtime/pids, .runtime/backups."
    ""
    "Restore on a new machine:"
    "  1. Create the target folder (e.g. E:\pan-hotel-watch), unzip this archive"
    "     into it so the tree matches the source repo root."
    "  2. Install Python 3.11 + Node 20 + Hermes on the new machine."
    "  3. If venvs do not work (different Python install path), rerun:"
    "         scripts\install.bat"
    "  4. Re-register Hermes per profile (rebuilds editable install, symlinks,"
    "     install .env/config.yaml from the manifest):"
    "         pan-hotel-hermes\scripts\register-hermes.ps1 -Profile <key> -RestartGateway"
    "  5. Re-login OTA accounts if copied cookies do not decrypt under the new"
    "     Windows user (Chromium DPAPI binding)."
    ""
    "NOTE: .runtime/browser-state was packed in full (cache + models + login)."
    "      Install-dir artifacts under %LOCALAPPDATA%\hermes are NOT in this"
    "      archive; they are regenerated by register-hermes.ps1 + feishu_profiles apply."
)
if ($skippedFiles -gt 0 -or $failedFiles -gt 0) {
    $manifestLines += @(
        ""
        "WARNING: $skippedFiles file(s) skipped, $failedFiles failed during copy"
        "(typically Cookies/profile files held open by a running browser)."
        "Re-run after stopping all pan-hotel-watch processes for a complete copy."
    )
}
$manifestLines | Set-Content -LiteralPath (Join-Path $Staging "_MIGRATE-MANIFEST.txt") -Encoding UTF8

if ($DryRun) {
    Write-Host ""
    Write-Host "DryRun complete (no real copy, no zip)."
    Write-Host ("  robocopy would copy ~{0:N0} file(s); skipped={1} failed={2} (exit {3})" -f $copiedFiles, $skippedFiles, $failedFiles, $roboExit)
    Write-Host ("  robocopy log  : {0}" -f $RoboLog)
    Write-Host "  Review the log for the exact include/exclude scope."
    if (-not $KeepStaging -and (Test-Path -LiteralPath $Staging)) {
        Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    return
}

# --- 4. compress -----------------------------------------------------------
Write-Host "Compressing staging -> $ZipPath (large; .runtime + venvs may take several minutes)..."
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
Compress-Archive -Path (Join-Path $Staging "*") -DestinationPath $ZipPath -CompressionLevel Optimal

$zipBytes = (Get-Item -LiteralPath $ZipPath).Length
# staging size
$stagingBytes = (Get-ChildItem -LiteralPath $Staging -Recurse -File -Force -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum
Write-Host ""
Write-Host "Done."
Write-Host ("  archive       : {0}" -f $ZipPath)
Write-Host ("  uncompressed  : {0:N1} GB ({1:N0} files copied)" -f ($stagingBytes / 1GB), $copiedFiles)
Write-Host ("  zip size      : {0:N1} GB" -f ($zipBytes / 1GB))
if ($skippedFiles -gt 0 -or $failedFiles -gt 0) {
    Write-Host ("  skipped/failed: {0} skipped, {1} failed (see _MIGRATE-MANIFEST.txt in zip)" -f $skippedFiles, $failedFiles)
}
Write-Host ("  robocopy log  : {0}" -f $RoboLog)
Write-Host ""
Write-Host "Cross-machine reminders:"
Write-Host "  - venvs may need rebuild via scripts\install.bat if Python path differs."
Write-Host "  - OTA cookies may need re-login under a new Windows user (DPAPI)."

# --- cleanup ----------------------------------------------------------------
if (-not $KeepStaging -and (Test-Path -LiteralPath $Staging)) {
    Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
}
