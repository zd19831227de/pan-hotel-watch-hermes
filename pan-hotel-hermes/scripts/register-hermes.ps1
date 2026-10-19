# Register Pan Hotel Watch into a Hermes profile.
# Usage:
#   .\scripts\register-hermes.ps1
#   .\scripts\register-hermes.ps1 -Profile quanji-ai -ResetMemory -SkipModelHint

param(
    [string]$Profile = "default",
    [switch]$SkipModelHint,
    [switch]$RestartGateway,
    [switch]$ResetMemory
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$WatchRoot = Split-Path $ProjectRoot -Parent
. (Join-Path $WatchRoot "scripts\common.ps1")

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
    throw "Hermes profile not found: $Profile ($HermesRoot). Create it first with: hermes profile create $Profile --clone"
}
if (-not (Test-Path $HermesRoot)) {
    throw "Hermes root not found: $HermesRoot. Install Hermes first."
}

$BackendPort = [int](Get-PanHotelWatchEnvValue -Name "PAN_HOTEL_WATCH_BACKEND_PORT" -Default "8100")
$WatchApiBaseUrl = "http://127.0.0.1:$BackendPort"
$BrowserRunTimeoutSeconds = [int](Get-PanHotelWatchEnvValue -Name "WATCH_API_BROWSER_TIMEOUT" -Default "300")
$PreferredPythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SkillsRoot = Join-Path $ProjectRoot "skills"
$PluginSrc = Join-Path $ProjectRoot "hermes_plugin\pan-hotel-feishu-session"
$PluginDst = Join-Path $HermesRoot "plugins\pan-hotel-feishu-session"
$BaseSoulSrc = Join-Path $ProjectRoot "config\hermes.SOUL.md"
$ProfileSoulSrc = Join-Path $ProjectRoot "config\profiles\$Profile\SOUL.md"
$SoulDst = Join-Path $HermesRoot "SOUL.md"
$ConfigLocal = Join-Path $ProjectRoot "config\hermes.config.local.yaml"

function Test-PanHotelHermesPython {
    param([string]$Candidate)
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
        return $false
    }
    try {
        & $Candidate -c "import encodings, mcp; from concurrent.futures import thread; print('ok')" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-PanHotelHermesPython {
    $rootPythonExe = Join-Path $WatchRoot ".venv\Scripts\python.exe"
    $candidates = @($PreferredPythonExe, $rootPythonExe)
    $pathPython = Get-Command python -ErrorAction SilentlyContinue
    if ($pathPython -and $pathPython.Source) {
        $candidates += $pathPython.Source
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-PanHotelHermesPython -Candidate $candidate) {
            return $candidate
        }
    }
    throw "No usable Python found: expected import encodings, mcp. Fix pan-hotel-hermes\.venv or ensure Hermes agent Python is on PATH."
}

# --- Single-source deployment helpers (V4-Step2) ---------------------------
# Goal: the install copies of the plugin and the skill must point at the repo
# source so edits in the repo take effect after a gateway restart. We prefer a
# directory symlink; if Windows denies it (no Developer Mode / not admin) we
# fall back to a copy and warn. Before replacing a real install directory we
# hash-compare it against the repo (excluding __pycache__) so unexpected local
# edits are never silently destroyed.

function Get-PanHotelSourceFileHashes {
    param([Parameter(Mandatory)][string]$Dir)
    $map = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Dir)) { return $map }
    Get-ChildItem -LiteralPath $Dir -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike '*__pycache__*' } |
        ForEach-Object {
            $rel = $_.FullName.Substring($Dir.Length).TrimStart('\', '/')
            $map[$rel] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    return $map
}

function Test-PanHotelLinkTraversal {
    # A link is only usable if it can actually be traversed: pick a real file
    # from the source and stat it through the link. Windows 11 24H2+ 挂载点
    # 加固会让「创建成功」的跨卷 Junction 在遍历时报
    # ERROR_UNTRUSTED_MOUNT_POINT (448)（生产机A 2026-09-18 实测），
    # Python pathlib / Test-Path 全部失败，必须降级拷贝。
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )
    $probe = Get-ChildItem -LiteralPath $Source -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike '*__pycache__*' } | Select-Object -First 1
    if (-not $probe) { return $true }
    $rel = $probe.FullName.Substring($Source.Length).TrimStart('\', '/')
    return [bool](Test-Path -LiteralPath (Join-Path $Destination $rel))
}

function Copy-PanHotelSourceDir {
    # Copy fallback（含清 __pycache__，防旧 .pyc 盖仓库改动）。
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item (Join-Path $Source "*") $Destination -Recurse -Force
    Get-ChildItem -LiteralPath $Destination -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq '__pycache__' } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

function Sync-PanHotelSourceDir {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination,
        [Parameter(Mandatory)][string]$Label,
        # For directories whose content is intentionally canonicalized by the
        # repo (e.g. the merged SKILL.md), back up the existing install copy to
        # <Destination>.pre-single-source before replacing it instead of
        # refusing on a hash difference.
        [switch]$BackupBeforeReplace
    )
    $parent = Split-Path $Destination -Parent
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    # Already linked to this source? Then nothing to do. Junction 与 SymbolicLink
    # 等价（junction 不需要开发者模式/管理员权限，且 Python pathlib rglob 与
    # os.walk 都会跟随 —— skill_manage 的 rglob 扫描不跟随符号链接，symlink
    # 部署的 skill 会被报 "not found in active profile"）。
    $destItem = Get-Item -LiteralPath $Destination -ErrorAction SilentlyContinue
    if ($destItem -and (@('SymbolicLink', 'Junction') -contains $destItem.LinkType)) {
        $targets = @($destItem.Target)
        if (-not $targets) { $targets = @($destItem.ResolvedTarget) }
        foreach ($t in $targets) {
            $resolvedT = try { (Resolve-Path -LiteralPath $t -ErrorAction Stop).Path } catch { $t }
            $resolvedS = try { (Resolve-Path -LiteralPath $Source -ErrorAction Stop).Path } catch { $Source }
            if ($resolvedT -eq $resolvedS) {
                if (Test-PanHotelLinkTraversal -Source $Source -Destination $Destination) {
                    Write-Host "$Label already linked ($($destItem.LinkType)) -> $Source"
                    return
                }
                # 链接指对了但被系统策略拦截遍历：只删重解析点本身
                # （.NET Delete($false) 绝不碰目标内容），转拷贝兜底。
                Write-Warning "$Label link exists but traversal is blocked by Windows mount-point policy (WinError 448); replacing with copy fallback."
                [System.IO.Directory]::Delete($Destination, $false)
                Copy-PanHotelSourceDir -Source $Source -Destination $Destination
                Write-Warning "$Label deployed as COPY (link blocked by OS). Rerun register-hermes.ps1 after editing the repo."
                return
            }
        }
    }

    # Hash-compare repo source vs existing install copy (exclude __pycache__).
    $srcHashes = Get-PanHotelSourceFileHashes -Dir $Source
    $dstHashes = Get-PanHotelSourceFileHashes -Dir $Destination
    $diffs = New-Object System.Collections.Generic.List[string]
    foreach ($rel in $srcHashes.Keys) {
        if (-not $dstHashes.Contains($rel)) {
            $diffs.Add("$rel (missing in install copy)")
        } elseif ($dstHashes[$rel] -ne $srcHashes[$rel]) {
            $diffs.Add("$rel (content differs)")
        }
    }
    foreach ($rel in $dstHashes.Keys) {
        if (-not $srcHashes.Contains($rel)) { $diffs.Add("$rel (extra in install copy)") }
    }

    if ($diffs.Count -gt 0 -and -not $BackupBeforeReplace) {
        Write-Warning "$Label differs from repo source; NOT replacing it to preserve local edits:"
        foreach ($d in $diffs) { Write-Warning "  - $d" }
        Write-Warning "Reconcile these files with the repo, then rerun register-hermes.ps1."
        return
    }

    # Remove the existing install copy (with a one-time backup when canonicalizing).
    if (Test-Path -LiteralPath $Destination) {
        if ($BackupBeforeReplace) {
            $backup = "$Destination.pre-single-source"
            if (Test-Path -LiteralPath $backup) {
                Remove-Item -LiteralPath $Destination -Recurse -Force
            } else {
                Move-Item -LiteralPath $Destination -Destination $backup -Force
                Write-Host "$Label previous install copy backed up -> $backup"
            }
        } else {
            Remove-Item -LiteralPath $Destination -Recurse -Force
        }
    }

    # Prefer a directory junction; fall back to symlink, then a copy.
    # Junction first: no admin/Developer Mode required, and junctions are
    # followed by BOTH os.walk(followlinks=True)（会话 skill 加载）and pathlib
    # rglob（skill_manage 查找）。SymbolicLink 会被 rglob 跳过，导致后台
    # review 的 skill_manage 报 "Skill ... not found in active profile"。
    $linkKind = $null
    try {
        New-Item -ItemType Junction -Path $Destination -Target $Source -ErrorAction Stop | Out-Null
        if (Test-PanHotelLinkTraversal -Source $Source -Destination $Destination) {
            $linkKind = 'Junction'
            Write-Host "$Label junction -> $Source"
        } else {
            # 创建成功但遍历被 Win11 挂载点加固拦截：删链接走拷贝。
            Write-Warning "$Label junction created but traversal blocked (WinError 448); using copy fallback."
            [System.IO.Directory]::Delete($Destination, $false)
        }
    } catch {
        try {
            New-Item -ItemType SymbolicLink -Path $Destination -Target $Source -ErrorAction Stop | Out-Null
            $linkKind = 'SymbolicLink'
            Write-Host "$Label symlinked -> $Source"
        } catch [System.UnauthorizedAccessException] {
            Write-Warning "$Label junction/symlink denied (enable Windows Developer Mode or run as admin); using copy fallback."
        } catch {
            Write-Warning "$Label link failed ($($_.Exception.GetType().Name)); using copy fallback."
        }
    }
    if (-not $linkKind) {
        Copy-PanHotelSourceDir -Source $Source -Destination $Destination
        Write-Warning "$Label deployed as COPY (fallback). Rerun register-hermes.ps1 after editing the repo."
    }
}

$PythonExe = Resolve-PanHotelHermesPython
$McpPythonCommand = ($PythonExe -replace "\\", "/")
$McpCwd = ($ProjectRoot -replace "\\", "/")

# Ensure pan_hotel_mcp (+ sibling first-party packages client/router) is
# importable in the venv that ACTUALLY runs the MCP subprocess. The live
# config.yaml command points at the Hermes agent venv, so we editable-install
# the package there. This replaces the fragile hand-written pan_hotel_mcp.pth
# as the loading mechanism (kept only as a migration fallback).
$HermesAgentPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
if (Test-Path $HermesAgentPython) {
    Write-Host "Installing pan_hotel_mcp (editable) into Hermes agent venv..."
    # pip writes progress/deprecation notices to stderr; under Stop preference
    # that raises a NativeCommandError even on success. Relax for this one
    # native call and judge by the real exit code.
    $priorEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $HermesAgentPython -m pip install -e $ProjectRoot 2>&1 | Out-Null
    $installExit = $LASTEXITCODE
    $ErrorActionPreference = $priorEap
    if ($installExit -ne 0) {
        Write-Warning "Editable install of pan_hotel_mcp failed (exit $installExit). Keep pan_hotel_mcp.pth in place or rerun register-hermes.ps1 after fixing pip."
    }
} else {
    Write-Warning "Hermes agent venv python not found ($HermesAgentPython); skipped editable install. Ensure pan_hotel_mcp is importable by the runtime venv another way."
}

# Skills: the repo source (pan-hotel-hermes/skills) is the single source of
# truth (V4-Step2a). Sync every skill subdir that ships a SKILL.md so newly
# created skills (e.g. via the backend /api/v1/skills endpoint) appear in the
# profile install dir without a manual copy. The curated pan-hotel-watch
# canonicalizes with a one-time backup; other skills refuse to clobber
# unexpected local edits (hash-compared) and link only when content matches.
if (Test-Path -LiteralPath $SkillsRoot) {
    foreach ($skillDir in (Get-ChildItem -LiteralPath $SkillsRoot -Directory -ErrorAction SilentlyContinue)) {
        if (-not (Test-Path -LiteralPath (Join-Path $skillDir.FullName "SKILL.md"))) { continue }
        $skillName = $skillDir.Name
        $skillDst = Join-Path $HermesRoot "skills\$skillName"
        $isCurated = $skillName -eq "pan-hotel-watch"
        Sync-PanHotelSourceDir -Source $skillDir.FullName -Destination $skillDst `
            -Label "Skill $skillName" -BackupBeforeReplace:$isCurated
        # Once a skill is a clean symlink to the repo source, its one-time
        # .pre-single-source backup (left by V4 single-source migration) is
        # stale. Its SKILL.md frontmatter still carries the real skill name,
        # so Hermes sees a duplicate and refuses skill_view with an ambiguous
        # name error. Drop the backup once single-source has taken hold.
        $staleBackup = "$skillDst.pre-single-source"
        $dstItem = Get-Item $skillDst -ErrorAction SilentlyContinue
        if ($dstItem -and (@('SymbolicLink', 'Junction') -contains $dstItem.LinkType) -and (Test-Path -LiteralPath $staleBackup)) {
            Remove-Item -LiteralPath $staleBackup -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host "Removed stale single-source backup: $staleBackup"
        }
    }
}

# Plugin: install copies are expected to be byte-identical to the repo; refuse
# to clobber if an unexpected local edit is detected.
if (Test-Path $PluginSrc) {
    Sync-PanHotelSourceDir -Source $PluginSrc -Destination $PluginDst `
        -Label "Plugin pan-hotel-feishu-session"
} else {
    Write-Host "Hermes plugin template not found: $PluginSrc"
}

function Write-Utf8NoBomFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $encoding = New-Object System.Text.UTF8Encoding $false
    # Strip BOM / ZWNBSP so Hermes context loader does not block SOUL.md.
    $clean = $Content.TrimStart([char]0xFEFF)
    [System.IO.File]::WriteAllText($Path, $clean, $encoding)
}

if (Test-Path $BaseSoulSrc) {
    if ($Profile -ne "default" -and (Test-Path $ProfileSoulSrc)) {
        $baseSoul = (Get-Content -Path $BaseSoulSrc -Raw -Encoding UTF8).TrimStart([char]0xFEFF).TrimEnd()
        $profileSoul = (Get-Content -Path $ProfileSoulSrc -Raw -Encoding UTF8).TrimStart([char]0xFEFF).Trim()
        # Keep the section header ASCII-only: Windows PowerShell may mis-decode
        # non-ASCII literals in .ps1 when the console code page is not UTF-8.
        $composedSoul = $baseSoul + "`n`n---`n`n## profile-specific SOUL`n`n" + $profileSoul + "`n"
        Write-Utf8NoBomFile -Path $SoulDst -Content $composedSoul
        Write-Host "Copied base + profile-specific SOUL (UTF-8 no BOM) -> $SoulDst"
    } else {
        $baseSoul = (Get-Content -Path $BaseSoulSrc -Raw -Encoding UTF8).TrimStart([char]0xFEFF)
        Write-Utf8NoBomFile -Path $SoulDst -Content $baseSoul
        Write-Host "Copied SOUL (UTF-8 no BOM) -> $SoulDst"
    }
} else {
    Write-Host "SOUL template not found: $BaseSoulSrc"
}

$ConfigDir = Join-Path $ProjectRoot "config"
$MemoriesDir = Join-Path $HermesRoot "memories"
New-Item -ItemType Directory -Force -Path $MemoriesDir | Out-Null

if ($ResetMemory) {
    foreach ($pair in @(
        @{ Name = "MEMORY.md"; Template = "hermes.MEMORY.template.md" },
        @{ Name = "USER.md"; Template = "hermes.USER.template.md" }
    )) {
        $profileTemplate = Join-Path $ProjectRoot "config\profiles\$Profile\$($pair.Name)"
        $defaultTemplate = Join-Path $ProjectRoot "config\$($pair.Template)"
        $source = if (Test-Path $profileTemplate) { $profileTemplate } else { $defaultTemplate }
        Copy-Item $source (Join-Path $MemoriesDir $pair.Name) -Force
    }
    Write-Host "Reset Hermes profile memories -> $MemoriesDir"
} else {
    $env:PYTHONPATH = $ProjectRoot
    $env:HERMES_SEED_ROOT = $HermesRoot
    $env:HERMES_SEED_TEMPLATE_DIR = $ConfigDir
    & $PythonExe (Join-Path $PSScriptRoot "seed_hermes_memory.py")
    Write-Host "Hermes memory templates checked -> $MemoriesDir"
}

# --- MCP env 占位符同步 --------------------------------------------------------
# hermes-agent 启动 MCP 子进程时只透传白名单变量（PATH/HOME 等，见 hermes-agent
# tools/mcp_tool.py _build_safe_env）；网关进程环境里的 WATCH_API_SHARED_SECRET
# 不会进入子进程。唯一通道是 mcp_servers.pan-hotel-watch.env 显式声明，值用
# ${VAR} 占位符由 profile .env 插值解析（_interpolate_env_vars）。缺这两行时，
# 后端 PAN_HOTEL_ENFORCE_API_AUTH=true 会让所有 run-now/分析类 MCP 调用 401。
function Sync-PanHotelMcpSecretEnv {
    param([Parameter(Mandatory)][string]$ConfigPath)
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return }
    $placeholders = [ordered]@{
        "WATCH_API_SHARED_SECRET"  = '${WATCH_API_SHARED_SECRET}'
        "WATCH_API_SUPERVISOR_KEY" = '${WATCH_API_SUPERVISOR_KEY}'
    }
    $lines = @(Get-Content -LiteralPath $ConfigPath -Encoding UTF8)
    $inServer = $false
    $inEnv = $false
    $lastEnvIndex = -1
    $seen = @{}
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match '^(\s*)pan-hotel-watch:\s*$') {
            $inServer = ($Matches[1].Length -eq 2)
            $inEnv = $false
            continue
        }
        if ($inServer -and $line -match '^\S') { $inServer = $false; $inEnv = $false; continue }
        if (-not $inServer) { continue }
        if ($line -match '^    env:\s*$') { $inEnv = $true; $lastEnvIndex = $i; continue }
        if ($inEnv) {
            if ($line -match '^\s{6}(\S[^\:]*):\s*') {
                $key = $Matches[1].Trim()
                $lastEnvIndex = $i
                if ($placeholders.Contains($key)) {
                    $desired = "      ${key}: $($placeholders[$key])"
                    if ("$($lines[$i])" -cne "$desired") { $lines[$i] = $desired; $script:McpSecretSyncChanges++ }
                    $seen[$key] = $true
                }
            } elseif ($line -match '^\s{0,4}\S') {
                $inEnv = $false
            }
        }
    }
    if ($lastEnvIndex -lt 0) { return }
    $missing = @()
    foreach ($key in @($placeholders.Keys)) {
        if (-not $seen.ContainsKey($key)) { $missing += "      ${key}: $($placeholders[$key])" }
    }
    if ($missing.Count -gt 0) {
        $insertAt = $lastEnvIndex + 1
        $before = @($lines | Select-Object -First $insertAt)
        $after = @()
        if ($insertAt -lt $lines.Count) { $after = @($lines[$insertAt..($lines.Count - 1)]) }
        $lines = @($before + $missing + $after)
        $script:McpSecretSyncChanges += $missing.Count
    }
    Set-Content -LiteralPath $ConfigPath -Value $lines -Encoding UTF8
}

$HermesConfig = Join-Path $HermesRoot "config.yaml"
$mcpBlock = @"

mcp_servers:
  pan-hotel-watch:
    command: $McpPythonCommand
    args: ["-m", "pan_hotel_mcp"]
    cwd: $McpCwd
    timeout: $BrowserRunTimeoutSeconds
    env:
      WATCH_API_BASE_URL: $WatchApiBaseUrl
      WATCH_API_BROWSER_TIMEOUT: "$BrowserRunTimeoutSeconds"
      WATCH_API_SHARED_SECRET: `${WATCH_API_SHARED_SECRET}
      WATCH_API_SUPERVISOR_KEY: `${WATCH_API_SUPERVISOR_KEY}
      PYTHONPATH: $McpCwd
      WATCH_DEFAULT_EBK_GROUP: ctrip_ebooking_data_center
      WATCH_DEFAULT_COMPETITOR_GROUP: default_competitor_watch

group_sessions_per_user: true

compression:
  enabled: true
  threshold: 0.45
  protect_last_n: 24

display:
  language: zh
  tool_progress: off
"@

if (-not (Test-Path $HermesConfig)) {
    $initialConfig = 'model: deepseek/deepseek-v4-flash' + $mcpBlock
    Set-Content -Path $HermesConfig -Value $initialConfig -Encoding UTF8
    Write-Host "Created Hermes config.yaml"
} elseif ((Get-Content $HermesConfig -Raw) -notmatch 'pan-hotel-watch') {
    Add-Content -Path $HermesConfig -Value $mcpBlock -Encoding UTF8
    Write-Host "Appended MCP block to Hermes config.yaml"
} else {
    Write-Host "Hermes config.yaml already contains pan-hotel-watch MCP"
}

if (Test-Path $HermesConfig) {
    $configText = Get-Content $HermesConfig -Raw
    $syncedUrl = $configText -replace '(WATCH_API_BASE_URL:\s*)http://127\.0\.0\.1:\d+', "`${1}$WatchApiBaseUrl"
    if ($syncedUrl -ne $configText) {
        Set-Content -Path $HermesConfig -Value $syncedUrl -Encoding UTF8
        Write-Host "Synced Hermes WATCH_API_BASE_URL -> $WatchApiBaseUrl"
        $configText = $syncedUrl
    }
    $script:McpSecretSyncChanges = 0
    Sync-PanHotelMcpSecretEnv -ConfigPath $HermesConfig
    if ($script:McpSecretSyncChanges -gt 0) {
        Write-Host "Synced MCP env secret placeholders (WATCH_API_SHARED_SECRET / WATCH_API_SUPERVISOR_KEY) -> $HermesConfig"
    }
    $syncedCommand = $configText -replace '(?m)^(\s*command:\s*).*(pan-hotel-hermes[/\\]\.venv[/\\]Scripts[/\\]python\.exe)\s*$', "`${1}$McpPythonCommand"
    if ($syncedCommand -ne $configText) {
        Set-Content -Path $HermesConfig -Value $syncedCommand -Encoding UTF8
        Write-Host "Synced pan-hotel-watch MCP command -> $McpPythonCommand"
        $configText = $syncedCommand
    }
    $mcpTimeoutLine = "    timeout: $BrowserRunTimeoutSeconds"
    $panHotelMcpPattern = '(?ms)(^  pan-hotel-watch:\s*\r?\n)(.*?)(?=^\S|\z)'
    $syncedMcpTimeout = [regex]::Replace(
        $configText,
        $panHotelMcpPattern,
        [System.Text.RegularExpressions.MatchEvaluator]{
            param([System.Text.RegularExpressions.Match]$match)
            $header = $match.Groups[1].Value
            $body = $match.Groups[2].Value
            if ($body -match '(?m)^\s{4}timeout:\s*') {
                $body = $body -replace '(?m)^(\s{4}timeout:\s*).*\s*$', "`${1}$BrowserRunTimeoutSeconds"
            } else {
                $body = $body -replace '(?m)^(\s{4}env:\s*)$', "$mcpTimeoutLine`n`$1"
            }
            if ($body -match '(?m)^\s{6}WATCH_API_BROWSER_TIMEOUT:\s*') {
                $body = $body -replace '(?m)^(\s{6}WATCH_API_BROWSER_TIMEOUT:\s*).*\s*$', "`${1}`"$BrowserRunTimeoutSeconds`""
            } else {
                $body = $body -replace '(?m)^(\s{6}WATCH_API_BASE_URL:.*)$', "`$1`n      WATCH_API_BROWSER_TIMEOUT: `"$BrowserRunTimeoutSeconds`""
            }
            return $header + $body
        },
        1
    )
    if ($syncedMcpTimeout -ne $configText) {
        Set-Content -Path $HermesConfig -Value $syncedMcpTimeout -Encoding UTF8
        Write-Host "Synced pan-hotel-watch MCP browser timeout -> $BrowserRunTimeoutSeconds seconds"
        $configText = $syncedMcpTimeout
    }
    if ($configText -notmatch '(?m)^display:\s*$' -and $configText -notmatch 'display:\s*\n\s*language:\s*zh') {
        if ($configText -notmatch 'display:') {
            Add-Content -Path $HermesConfig -Value "`ndisplay:`n  language: zh`n  tool_progress: off" -Encoding UTF8
            Write-Host "Added display.language: zh"
        }
    }
    if ($configText -notmatch '(?m)^\s*tool_progress:\s*') {
        if ($configText -match '(?m)^display:\s*$') {
            $configText = $configText -replace '(?m)^(display:\s*)$', "`$1`n  tool_progress: off"
            Set-Content -Path $HermesConfig -Value $configText -Encoding UTF8
            Write-Host "Added display.tool_progress: off"
        }
    }
    if ($configText -notmatch '(?m)^compression:\s*$' -and $configText -notmatch 'compression:\s*\n\s*enabled:') {
        Add-Content -Path $HermesConfig -Value @"

compression:
  enabled: true
  threshold: 0.45
  protect_last_n: 24
"@ -Encoding UTF8
        Write-Host "Added compression"
    }
    $pluginName = "pan-hotel-feishu-session"
    if ($configText -notmatch [regex]::Escape($pluginName)) {
        if ($configText -match '(?ms)^plugins:\s*\r?\n\s*enabled:\s*\r?\n') {
            $configText = $configText -replace '(?ms)(^plugins:\s*\r?\n\s*enabled:\s*\r?\n)', "`$1  - $pluginName`n"
            Set-Content -Path $HermesConfig -Value $configText -Encoding UTF8
            Write-Host "Added plugin -> $pluginName"
        } elseif ($configText -notmatch '(?m)^plugins:\s*$') {
            Add-Content -Path $HermesConfig -Value @"

plugins:
  enabled:
    - $pluginName
"@ -Encoding UTF8
            Write-Host "Added plugins.enabled -> $pluginName"
        }
    } else {
        Write-Host "Hermes config.yaml already contains $pluginName"
    }
}

Write-Host "Hermes profile: $Profile"
Write-Host "Hermes root: $HermesRoot"
Write-Host "Hermes config path: $HermesConfig"

if (-not $SkipModelHint) {
    Write-Host ""
    Write-Host "Next if LLM is not configured:"
    Write-Host "  .\scripts\setup-deepseek.ps1 -ApiKey `"sk-...`""
    Write-Host ""
    Write-Host "Verify:"
    Write-Host "  1. Start pan-hotel-watch backend ($WatchApiBaseUrl)"
    Write-Host "  2. hermes -> /mcp list"
    Write-Host "  3. hermes gateway setup"
    Write-Host "  4. Stop watch feishu-listener"
}

$HermesExe = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\hermes.exe"
Write-Host ""
Write-Host "Deploy sync: Skill/SOUL written to $HermesRoot."
Write-Host "Restart Hermes Gateway after Python or Skill changes."
if (Test-Path $HermesExe) {
    if ($RestartGateway) {
        Write-Host "Restarting Hermes Gateway for profile $Profile..."
        $gatewayArgs = if ($Profile -eq "default") { @("gateway", "run", "--replace") } else { @("-p", $Profile, "gateway", "run", "--replace") }
        Start-Process -FilePath $HermesExe -ArgumentList $gatewayArgs -WindowStyle Hidden
        Start-Sleep -Seconds 2
        Write-Host "Gateway restarted. Run: hermes gateway status"
    } else {
        Write-Host "Next: .\scripts\register-hermes.ps1 -Profile $Profile -RestartGateway -SkipModelHint"
        Write-Host "Manual fallback: hermes gateway stop; then hermes gateway run --replace"
    }
} else {
    Write-Warning ("Hermes not found: " + $HermesExe)
}
