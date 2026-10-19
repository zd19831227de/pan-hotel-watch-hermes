$script:PanHotelWatchRoot = Split-Path -Parent $PSScriptRoot
$script:PanHotelWatchBackendDir = Join-Path $script:PanHotelWatchRoot "backend"
$script:PanHotelWatchFrontendDir = Join-Path $script:PanHotelWatchRoot "frontend"
$script:PanHotelWatchRuntimeDir = Join-Path $script:PanHotelWatchRoot ".runtime"
$script:PanHotelWatchLogDir = Join-Path $script:PanHotelWatchRuntimeDir "logs"
$script:PanHotelWatchPidDir = Join-Path $script:PanHotelWatchRuntimeDir "pids"

function Get-PanHotelWatchRoot {
    return $script:PanHotelWatchRoot
}

function Get-PanHotelWatchBackendDir {
    return $script:PanHotelWatchBackendDir
}

function Get-PanHotelWatchFrontendDir {
    return $script:PanHotelWatchFrontendDir
}

function Get-PanHotelWatchRuntimeDir {
    return $script:PanHotelWatchRuntimeDir
}

function Get-PanHotelWatchLogDir {
    return $script:PanHotelWatchLogDir
}

function Get-PanHotelWatchPidDir {
    return $script:PanHotelWatchPidDir
}

function Ensure-PanHotelWatchRuntimeDirs {
    New-Item -ItemType Directory -Force -Path `
        $script:PanHotelWatchRuntimeDir, `
        $script:PanHotelWatchLogDir, `
        $script:PanHotelWatchPidDir | Out-Null
}

function Get-PanHotelWatchPython {
    $python = Join-Path $script:PanHotelWatchRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        throw "Virtual environment not found. Run scripts/install.ps1 first."
    }
    return $python
}

function Get-PanHotelWatchNode {
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($null -eq $nodeCommand) {
        throw "node.exe not found. Install Node.js and run scripts/install.ps1."
    }
    return $nodeCommand.Source
}

function Get-PanHotelWatchNpm {
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) {
        throw "npm.cmd not found. Install Node.js and run scripts/install.ps1."
    }
    return $npmCommand.Source
}

function Get-PanHotelWatchPidPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    return Join-Path $script:PanHotelWatchPidDir "$Name.pid"
}

function Get-PanHotelWatchLogPath {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [ValidateSet("out", "err")][string]$Stream = "out"
    )
    return Join-Path $script:PanHotelWatchLogDir "$Name.$Stream.log"
}

function Get-PanHotelWatchPid {
    param([Parameter(Mandatory = $true)][string]$Name)
    $path = Get-PanHotelWatchPidPath -Name $Name
    if (-not (Test-Path $path)) {
        return $null
    }
    $content = (Get-Content -Raw -LiteralPath $path).Trim()
    if ([string]::IsNullOrWhiteSpace($content)) {
        return $null
    }
    try {
        return [int]$content
    } catch {
        return $null
    }
}

function Set-PanHotelWatchPid {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$ProcessId
    )
    Ensure-PanHotelWatchRuntimeDirs
    Set-Content -LiteralPath (Get-PanHotelWatchPidPath -Name $Name) -Value $ProcessId -Encoding ASCII
}

function Remove-PanHotelWatchPid {
    param([Parameter(Mandatory = $true)][string]$Name)
    $path = Get-PanHotelWatchPidPath -Name $Name
    if (Test-Path $path) {
        Remove-Item -LiteralPath $path -Force
    }
}

function Get-PanHotelWatchProcess {
    param([Parameter(Mandatory = $true)][string]$Name)
    $processId = Get-PanHotelWatchPid -Name $Name
    if ($null -eq $processId) {
        return $null
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-PanHotelWatchPid -Name $Name
    }
    return $process
}

function Get-PanHotelWatchProcessTreeIds {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    $allProcesses = @()
    try {
        $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    } catch {
        return @($RootProcessId)
    }

    $ids = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $ids.Add($RootProcessId)
    $queue.Enqueue($RootProcessId)

    while ($queue.Count -gt 0) {
        $parentId = $queue.Dequeue()
        foreach ($child in ($allProcesses | Where-Object { $_.ParentProcessId -eq $parentId })) {
            $childId = [int]$child.ProcessId
            if (-not $ids.Contains($childId)) {
                $ids.Add($childId)
                $queue.Enqueue($childId)
            }
        }
    }

    return @($ids)
}

function Stop-PanHotelWatchService {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [int]$TimeoutSeconds = 10
    )
    $process = Get-PanHotelWatchProcess -Name $Name
    if ($null -eq $process) {
        Remove-PanHotelWatchPid -Name $Name
        return $false
    }

    $processTreeIds = Get-PanHotelWatchProcessTreeIds -RootProcessId $process.Id

    foreach ($childId in ($processTreeIds | Select-Object -Unique | Sort-Object -Descending)) {
        Stop-Process -Id $childId -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $remaining = @($processTreeIds | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($remaining.Count -eq 0) {
            break
        }
        Start-Sleep -Seconds 1
    }

    foreach ($remainingId in ($processTreeIds | Select-Object -Unique | Sort-Object -Descending)) {
        $remaining = Get-Process -Id $remainingId -ErrorAction SilentlyContinue
        if ($null -ne $remaining) {
            Stop-Process -Id $remainingId -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-PanHotelWatchPid -Name $Name
    return $true
}

function Get-PanHotelWatchEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Default = ""
    )
    $envValue = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($envValue)) {
        return $envValue
    }

    $envFile = Join-Path $script:PanHotelWatchRoot ".env"
    if (-not (Test-Path $envFile)) {
        return $Default
    }
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*=\s*(.*)\s*$"
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        $match = [regex]::Match($trimmed, $pattern)
        if (-not $match.Success) {
            continue
        }
        $value = $match.Groups[1].Value.Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        return $value
    }
    return $Default
}

function Get-PanHotelWatchBackendPort {
    $port = Get-PanHotelWatchEnvValue -Name "PAN_HOTEL_WATCH_BACKEND_PORT" -Default ""
    if ([string]::IsNullOrWhiteSpace($port)) {
        $port = Get-PanHotelWatchEnvValue -Name "BACKEND_PORT" -Default "8100"
    }
    return [int]$port
}

function Set-PanHotelWatchDatabaseEnv {
    $configured = Get-PanHotelWatchEnvValue -Name "DATABASE_URL" -Default "sqlite+aiosqlite:///./pan_hotel_watch.db"
    if ($configured -match '^sqlite\+aiosqlite:///(\./)?pan_hotel_watch\.db$') {
        $dbPath = Join-Path $script:PanHotelWatchRoot "pan_hotel_watch.db"
        $env:DATABASE_URL = "sqlite+aiosqlite:///" + ($dbPath -replace '\\', '/')
    } else {
        $env:DATABASE_URL = $configured
    }
    Assert-PanHotelWatchCanonicalDatabase
}

function Assert-PanHotelWatchCanonicalDatabase {
    # Live OTA data must stay on the canonical hermes repo DB. Override only with explicit env.
    $canonicalRoot = "D:\work\pan-hotel-watch"
    $canonicalDb = Join-Path $canonicalRoot "pan_hotel_watch.db"
    $allowNonCanonical = [string]::Equals(
        (Get-PanHotelWatchEnvValue -Name "PAN_HOTEL_WATCH_ALLOW_NON_CANONICAL_DB" -Default ""),
        "1",
        [System.StringComparison]::OrdinalIgnoreCase
    )

    $rootFull = [System.IO.Path]::GetFullPath($script:PanHotelWatchRoot)
    $canonicalRootFull = [System.IO.Path]::GetFullPath($canonicalRoot)
    if (-not $allowNonCanonical -and -not [string]::Equals($rootFull, $canonicalRootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw ("Refusing DB ops outside canonical hermes repo.`n  cwd-root: {0}`n  canonical: {1}`nSet PAN_HOTEL_WATCH_ALLOW_NON_CANONICAL_DB=1 only for intentional non-live work." -f $rootFull, $canonicalRootFull)
    }

    $dbUrl = [string]$env:DATABASE_URL
    if ($dbUrl -notmatch '^sqlite\+aiosqlite:///') {
        return
    }
    $dbPath = $dbUrl -replace '^sqlite\+aiosqlite:///', ''
    if ($dbPath -match '^/.+:') {
        # Windows absolute like /C:/...
        $dbPath = $dbPath.TrimStart('/')
    }
    $dbPath = $dbPath -replace '/', '\'
    if (-not [System.IO.Path]::IsPathRooted($dbPath)) {
        $dbPath = Join-Path $script:PanHotelWatchRoot $dbPath
    }
    $dbFull = [System.IO.Path]::GetFullPath($dbPath)
    $canonicalDbFull = [System.IO.Path]::GetFullPath($canonicalDb)
    if (-not $allowNonCanonical -and -not [string]::Equals($dbFull, $canonicalDbFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw ("Refusing non-canonical DATABASE_URL.`n  resolved:  {0}`n  canonical: {1}" -f $dbFull, $canonicalDbFull)
    }
    if ((Test-Path -LiteralPath $dbFull) -and ((Get-Item -LiteralPath $dbFull).Length -lt 5MB) -and -not $allowNonCanonical) {
        throw ("Refusing suspiciously small live DB ({0} bytes): {1}`nCanonical production DB should be ~100MB+." -f (Get-Item -LiteralPath $dbFull).Length, $dbFull)
    }
}

function Resolve-PanHotelWatchPath {
    param([Parameter(Mandatory = $true)][string]$PathValue)
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return Join-Path $script:PanHotelWatchRoot $PathValue
}

function Get-PanHotelWatchBrowserStateDir {
    $value = Get-PanHotelWatchEnvValue -Name "BROWSER_STATE_DIR" -Default ".runtime/browser-state"
    return Resolve-PanHotelWatchPath -PathValue $value
}

function Get-PanHotelWatchAppLogDir {
    $browserStateDir = Get-PanHotelWatchBrowserStateDir
    $runtimeParent = Split-Path -Parent $browserStateDir
    return Join-Path $runtimeParent "logs"
}

function Get-PanHotelWatchWorkerHeartbeatPath {
    param([string]$WorkerName = "ctrip_ebooking_data_center_scheduler")
    $browserStateDir = Get-PanHotelWatchBrowserStateDir
    $runtimeParent = Split-Path -Parent $browserStateDir
    return Join-Path $runtimeParent "heartbeats\$WorkerName.json"
}

function Get-PanHotelWatchLanIp {
    try {
        $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*" -and
                $_.PrefixOrigin -ne "WellKnown"
            } |
            Select-Object -First 1 -ExpandProperty IPAddress
        if (-not [string]::IsNullOrWhiteSpace($ip)) {
            return $ip
        }
    } catch {
        return "127.0.0.1"
    }
    return "127.0.0.1"
}

function Get-PanHotelWatchPortOwner {
    param([Parameter(Mandatory = $true)][int]$Port)
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $connection) {
        return $null
    }
    $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        Port = $Port
        ProcessId = $connection.OwningProcess
        ProcessName = if ($null -ne $process) { $process.ProcessName } else { "" }
        Path = if ($null -ne $process) { $process.Path } else { "" }
        LocalAddress = $connection.LocalAddress
    }
}

function Assert-PanHotelWatchPortAvailable {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ServiceName
    )
    $owner = Get-PanHotelWatchPortOwner -Port $Port
    if ($null -eq $owner) {
        return
    }
    $managedProcess = Get-PanHotelWatchProcess -Name $ServiceName
    if ($null -ne $managedProcess) {
        $managedTreeIds = Get-PanHotelWatchProcessTreeIds -RootProcessId $managedProcess.Id
        if ($owner.ProcessId -in $managedTreeIds) {
            return
        }
    }
    throw "Port $Port is already used by PID $($owner.ProcessId) ($($owner.ProcessName)). Stop that process or choose another port."
}

function Test-PanHotelWatchHttp {
    param([Parameter(Mandatory = $true)][string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return [pscustomobject]@{
            Url = $Url
            Ok = $true
            StatusCode = [int]$response.StatusCode
            Error = ""
        }
    } catch {
        $statusCode = 0
        if ($null -ne $_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        return [pscustomobject]@{
            Url = $Url
            Ok = $false
            StatusCode = $statusCode
            Error = $_.Exception.Message
        }
    }
}

function Wait-PanHotelWatchHttp {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 20
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $result = Test-PanHotelWatchHttp -Url $Url
        if ($result.Ok) {
            return $true
        }
        Start-Sleep -Milliseconds 700
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Reset-PanHotelWatchPlaywrightEnv {
    $defaultBrowsersPath = Join-Path $env:LOCALAPPDATA "ms-playwright"
    $current = $env:PLAYWRIGHT_BROWSERS_PATH
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        $looksLikeSandbox = $current -match "cursor-sandbox-cache"
        $hasChromium = $false
        if (-not $looksLikeSandbox -and (Test-Path -LiteralPath $current)) {
            $hasChromium = @(Get-ChildItem -LiteralPath $current -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue |
                Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "chrome-win\chrome.exe") }).Count -gt 0
        }
        if ($looksLikeSandbox -or -not $hasChromium) {
            Remove-Item env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
            $current = $null
        }
    }
    if ([string]::IsNullOrWhiteSpace($current) -and (Test-Path -LiteralPath $defaultBrowsersPath)) {
        $env:PLAYWRIGHT_BROWSERS_PATH = $defaultBrowsersPath
    }
}
