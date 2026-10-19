# Pan Hotel Watch 一键迁移打包脚本（公司主机上运行）。
#
# 打包内容（staging 布局）：
#   project/   <- 整个项目仓库（含 .env、.runtime 登录态、数据库；不含垃圾目录）
#   hermes/    <- 整个 %LOCALAPPDATA%\hermes（hermes-agent、profiles、auth.json、
#                 根 .env / config.yaml、gateway-service、hooks、plugins、memories、bin）
#   migration-manifest.json <- 源机环境信息 + 打包摘要（供 migrate-import.ps1 使用）
#
# 排除项（详见 AGENTS/docs/zcode-migration-instructions.md）：
#   - 项目：.git（-IncludeGit 打开）、__pycache__、*.pyc、*.pyo、*.lock、*.pid、
#     .zcode、.pytest_cache、.gitnexus（可重建索引）、.playwright-cli、tmp\、
#     output\、debug.log、backend\pan_hotel_watch.db（0 字节陈旧库）、
#     .runtime\logs|pids|backups、
#     browser-state 纯缓存目录（Cache / Code Cache / GPUCache / OptGuideOnDeviceModel 等，
#     保留 Cookies、Login Data 等登录态文件结构）
#   - Hermes：hermes-agent\.git、audio_cache、image_cache、cache、logs、sessions、
#     sandboxes、__pycache__、*.pid、*.lock、state.db-wal、state.db-shm
#
# 压缩：7z（必须；Compress-Archive 在 PowerShell 5.1 有 2GB 单文件限制，包体 4GB+ 必败）。
#
# 安全提示：包内含 .env（飞书密钥/API key）、auth.json、tunnel-credentials.txt、
# 浏览器登录态等敏感数据，传输必须加密，不得明文走网络。
#
# 用法：
#   .\scripts\migrate-export.ps1 -StopProcesses          # 自动停进程后打包到 output\
#   .\scripts\migrate-export.ps1 -StopProcesses -IncludeGit
#   .\scripts\migrate-export.ps1 -StopProcesses -IncludeInstallers   # 额外带 Python/Node/uv 离线安装包
#   .\scripts\migrate-export.ps1 -DryRun                 # 只列文件，不实际拷贝/压缩（仍生成引导包）
#
# 引导包（output\migrate-boot\）默认包含：migrate-import.ps1、免安装 7z、
# Playwright 浏览器离线件（headless-shell/ffmpeg，防镜像缺文件）。
#   .\scripts\migrate-export.ps1 -StopProcesses -Force   # 进程停不掉也强行打包（跳过锁定文件）

param(
    [string]$OutputDir,
    [switch]$StopProcesses,
    [switch]$IncludeGit,
    [switch]$IncludeInstallers,   # 把 Python/Node/uv 离线安装包放进 migrate-boot（新机器免下载）
    [switch]$Force,      # 进程未停也强制打包（robocopy 跳过锁定文件）
    [switch]$DryRun      # robocopy /L 只列清单，不实际拷贝、不压缩
)

$ErrorActionPreference = "Stop"

# --- UTF-8 控制台输出（PowerShell 5.1） --------------------------------------
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONIOENCODING = "utf-8"

$RepoRoot = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

# --- 7z 自检（禁止 Compress-Archive：PS5.1 下有 2GB 限制） --------------------
function Resolve-SevenZip {
    $cmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles "7-Zip\7z.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "7-Zip\7z.exe")
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

$SevenZip = Resolve-SevenZip
if (-not $SevenZip) {
    $hint = "未找到 7z。请先安装：winget install 7zip.7zip（或从 7-zip.org 安装），然后重跑本脚本。"
    if ($DryRun) {
        Write-Warning "(DryRun) $hint"
    } else {
        throw $hint
    }
}

if (-not (Test-Path -LiteralPath $HermesHome)) {
    throw "Hermes 安装目录不存在：$HermesHome。本脚本需同时打包项目本体与 Hermes 安装目录。"
}

# --- 进程检测（扩展自 export-state.ps1） --------------------------------------
# 覆盖：.runtime 下所有 *.pid 文件（含 cloudflared-tunnel / localhost-run /
# ngrok-tunnel / tunnel-auth-proxy / read-only-worker 等）、后端/worker Python、
# Hermes gateway、browser-state Chrome、前端 node、隧道进程（cloudflared.exe /
# ngrok.exe / ssh.exe localhost.run / node tunnel-auth-proxy）。
function Get-MigrationRunningProcesses {
    param([string]$Root)

    $found = @()
    $seenIds = @{}

    # 1) .runtime 与 .runtime\pids 下的 pid 文件：直接按 PID 定位
    # pid 文件里的陈旧 PID 可能被系统进程复用（实测 ngrok-tunnel.pid 指向了 WmiPrvSE），
    # 这些系统进程绝不能停，先建保护名单。
    $systemProcNames = @(
        "WmiPrvSE", "svchost", "services", "lsass", "csrss", "smss",
        "wininit", "winlogon", "dwm", "logonui", "explorer", "conhost",
        "System", "Idle", "Registry", "MemCompression"
    )
    $pidDirs = @((Join-Path $Root ".runtime"), (Join-Path $Root ".runtime\pids"))
    foreach ($dir in $pidDirs) {
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        foreach ($pidFile in @(Get-ChildItem -LiteralPath $dir -Filter "*.pid" -File -ErrorAction SilentlyContinue)) {
            $content = ""
            try { $content = (Get-Content -Raw -LiteralPath $pidFile.FullName).Trim() } catch { continue }
            $procId = 0
            if (-not [int]::TryParse($content, [ref]$procId)) { continue }
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if (-not $proc) { continue }
            if ($systemProcNames -contains $proc.ProcessName) {
                Write-Warning "pid 文件 $($pidFile.Name) 指向系统进程 $($proc.ProcessName)（PID 已被复用），忽略。"
                continue
            }
            if (-not $seenIds.ContainsKey($procId)) {
                $seenIds[$procId] = $true
                $found += [pscustomobject]@{
                    Id = $procId; Name = $proc.ProcessName; Detail = "pid 文件 $($pidFile.Name)"
                }
            }
        }
    }

    # 2) 名称/命令行扫描兜底（进程可能不由我们的 pid 文件管理）
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -or $_.CommandLine }
    foreach ($p in $procs) {
        $procId = [int]$p.ProcessId
        if ($seenIds.ContainsKey($procId)) { continue }
        $exe = [string]$p.ExecutablePath
        $cmd = [string]$p.CommandLine
        $name = [string]$p.Name
        $exeUnderRoot = ($exe -and $exe.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase))
        $cmdMentionsRoot = ($cmd -and $cmd.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
        $detail = $null

        if ($name -match 'python(w)?\.exe$' -and ($cmd -match 'pan-?hotel' -or $cmd -match 'uvicorn' -or $cmd -match 'read_only_scheduler' -or $cmd -match 'pan_hotel_mcp')) {
            $detail = 'python (pan-hotel 后端/worker/MCP)'
        } elseif ($name -match 'hermes\.exe$' -and ($cmd -match 'gateway' -or $cmd -match 'pan-?hotel')) {
            $detail = 'hermes gateway'
        } elseif ($name -match 'node\.exe$' -and $cmd -match 'pan-?hotel') {
            $detail = 'node (前端)'
        } elseif ($name -match 'node\.exe$' -and $cmd -match 'tunnel-auth-proxy') {
            $detail = 'node (tunnel-auth-proxy)'
        } elseif ($name -match 'cloudflared\.exe$' -and ($exeUnderRoot -or $cmdMentionsRoot -or $cmd -match 'cloudflared-tunnel')) {
            $detail = 'cloudflared (隧道)'
        } elseif ($name -match 'ngrok\.exe$' -and ($exeUnderRoot -or $cmdMentionsRoot)) {
            $detail = 'ngrok (隧道)'
        } elseif ($name -match 'ssh\.exe$' -and $cmd -match 'localhost\.run') {
            $detail = 'ssh (localhost.run 隧道)'
        } elseif ($exeUnderRoot -and $name -match 'chrome\.exe$') {
            $detail = 'chrome (browser-state)'
        }

        if ($detail) {
            $seenIds[$procId] = $true
            $found += [pscustomobject]@{ Id = $procId; Name = $name; Detail = $detail }
        }
    }
    return $found
}

$running = Get-MigrationRunningProcesses -Root $RepoRoot
if ($running) {
    Write-Warning "检测到仍在运行的 pan-hotel-watch / 隧道相关进程（文件可能被锁定，打包会不完整）："
    $running | Format-Table Id, Name, Detail -AutoSize | Out-String | Write-Host
    if ($DryRun) {
        Write-Host "(DryRun) 仅报告，不停止进程。实际打包请加 -StopProcesses。"
    } elseif ($StopProcesses) {
        Write-Host "正在停止检测到的进程（-StopProcesses）..."
        foreach ($r in $running) {
            try {
                Stop-Process -Id $r.Id -Force -ErrorAction Stop
                Write-Host "  已停止 PID $($r.Id)（$($r.Detail)）"
            } catch {
                Write-Warning "  无法停止 PID $($r.Id)：$($_.Exception.Message)"
            }
        }
        Start-Sleep -Seconds 2
        $still = Get-MigrationRunningProcesses -Root $RepoRoot
        if ($still) {
            Write-Warning "停止后仍有进程在运行："
            $still | Format-Table Id, Name, Detail -AutoSize | Out-String | Write-Host
            if (-not $Force) {
                throw "进程未能全部停止。请手动停止后重跑，或加 -Force（锁定文件将被 robocopy 跳过）。"
            }
        }
    } elseif (-not $Force) {
        throw "拒绝在有进程运行时打包（Cookies/登录态文件被锁定）。请加 -StopProcesses（自动停止）或 -Force（跳过锁定文件）。"
    } else {
        Write-Warning "-Force：继续打包，锁定文件将被 robocopy 跳过。"
    }
} else {
    Write-Host "未检测到运行中的相关进程。"
}

# --- staging 目录 --------------------------------------------------------------
if (-not $OutputDir) { $OutputDir = Join-Path $RepoRoot "output" }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).Path

$ArchivePath = Join-Path $OutputDir "pan-hotel-watch-migrate-$timestamp.7z"
$Staging = Join-Path $env:TEMP "phw-migrate-staging-$timestamp"
$ProjectStage = Join-Path $Staging "project"
$HermesStage = Join-Path $Staging "hermes"
if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ProjectStage, $HermesStage | Out-Null

# --- robocopy 拷贝（容忍锁定文件） --------------------------------------------
function Invoke-RobocopyStage {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExcludeDirs,
        [string[]]$ExcludeFiles,
        [string]$LogPath
    )
    $roboArgs = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NP", "/R:1", "/W:1", "/NJH", "/LOG:$LogPath")
    if ($DryRun) { $roboArgs += "/L" }
    foreach ($d in $ExcludeDirs) { $roboArgs += @("/XD", $d) }
    foreach ($f in $ExcludeFiles) { $roboArgs += @("/XF", $f) }
    & robocopy @roboArgs | Out-Null
    $exit = $LASTEXITCODE
    # robocopy: 0-7 成功（1/3 = 有跳过，属正常），>=8 失败。
    # -Force 模式下锁定文件（ERROR 32，运行中 Chrome 的 Cookies/Sessions 等）
    # 重试耗尽后计为 FAILED 并置 8 位——这正是「跳过锁定文件」的预期结果，
    # 降级为告警（数量见日志统计）；16 为致命错误，任何模式都终止。
    if ($exit -ge 16) {
        throw "robocopy 失败（exit $exit）：$Source -> $Destination。日志：$LogPath"
    }
    if ($exit -ge 8 -and -not $Force) {
        throw "robocopy 失败（exit $exit）：$Source -> $Destination。日志：$LogPath"
    }
    $summary = @{ Copied = 0; Skipped = 0; Failed = 0; Exit = $exit }
    if (Test-Path -LiteralPath $LogPath) {
        # robocopy 日志是系统 ANSI 代码页（zh-CN 为 GBK），用 -Encoding Default 解码
        $logText = Get-Content -LiteralPath $LogPath -Raw -Encoding Default
        $filesLine = ($logText -split "`r?`n" | Where-Object { $_ -match '^\s*(Files|文件)\s*:' }) | Select-Object -First 1
        if ($filesLine) {
            $cleaned = $filesLine -replace '(Files|文件)\s*:', '' -replace ',', ''
            $nums = [regex]::Matches($cleaned, '\d+') | ForEach-Object { [int64]$_.Value }
            # robocopy 列顺序: Total Copied Skipped Mismatch FAILED Extras
            if ($nums.Count -ge 5) {
                $summary.Copied = $nums[1]
                $summary.Skipped = $nums[2]
                $summary.Failed = $nums[4]
            }
        }
    }
    if ($exit -ge 8) {
        Write-Warning ("-Force：robocopy exit $exit，约 $($summary.Failed) 个文件未能复制（运行中浏览器锁定的 Cookies/Sessions 等）。日志：$LogPath")
    }
    return $summary
}

# browser-state 纯缓存目录（保留 Cookies / Login Data / IndexedDB 等登录态结构；
# OptGuideOnDeviceModel 为美团端上模型，实测约 3.98GB，新机器可重新生成）
$browserCacheDirs = @(
    "Cache", "Code Cache", "GPUCache", "Media Cache", "CacheStorage",
    "Crashpad", "GrShaderCache", "ShaderCache", "DawnCache",
    "OptGuideOnDeviceModel", "OptimizationGuidePredictionModels", "component_crx_cache"
)

# 2026-09-16 追加：.gitnexus 索引（~1GB，npx gitnexus analyze 可重建）、
# 仓库根 tmp/（会话杂物）、.playwright-cli/；backend\pan_hotel_watch.db 是
# 0 字节陈旧文件（真库在仓库根，docs/migration-code-review P2-3），带上只会误导。
$projectExcludeDirs = @("__pycache__", ".zcode", ".pytest_cache",
    (Join-Path $RepoRoot ".runtime\logs"),
    (Join-Path $RepoRoot ".runtime\pids"),
    (Join-Path $RepoRoot ".runtime\backups"),
    (Join-Path $RepoRoot "output"),
    (Join-Path $RepoRoot "tmp"),
    (Join-Path $RepoRoot ".gitnexus"),
    (Join-Path $RepoRoot ".playwright-cli")
) + $browserCacheDirs
if (-not $IncludeGit) { $projectExcludeDirs += (Join-Path $RepoRoot ".git") }
$projectExcludeFiles = @("*.pyc", "*.pyo", "*.lock", "*.pid",
    "debug.log", (Join-Path $RepoRoot "backend\pan_hotel_watch.db"))

# Hermes 运行时垃圾（profiles 与 hermes-agent 同样适用，/XD 裸名全层级匹配）
$hermesExcludeDirs = @(
    "audio_cache", "image_cache", "cache", "logs", "sessions", "sandboxes", "__pycache__",
    (Join-Path $HermesHome "hermes-agent\.git")
)
$hermesExcludeFiles = @("*.pid", "*.lock", "*.pyc", "*.pyo", "state.db-wal", "state.db-shm")

Write-Host ""
Write-Host "1/3 通过 robocopy 拷贝项目本体 -> staging（.runtime + venv 较大，可能需要数分钟）..."
if ($DryRun) { Write-Host "(DryRun: robocopy /L 仅列清单，不实际拷贝)" }
$ProjectRoboLog = Join-Path $env:TEMP "phw-robocopy-project-$timestamp.log"
$projectSummary = Invoke-RobocopyStage -Source $RepoRoot -Destination $ProjectStage `
    -ExcludeDirs $projectExcludeDirs -ExcludeFiles $projectExcludeFiles -LogPath $ProjectRoboLog

Write-Host "2/3 通过 robocopy 拷贝 Hermes 安装目录 $HermesHome -> staging..."
$HermesRoboLog = Join-Path $env:TEMP "phw-robocopy-hermes-$timestamp.log"
$hermesSummary = Invoke-RobocopyStage -Source $HermesHome -Destination $HermesStage `
    -ExcludeDirs $hermesExcludeDirs -ExcludeFiles $hermesExcludeFiles -LogPath $HermesRoboLog

# --- 新增模块核对（robocopy 全量拷贝应自动覆盖；防止未来排除规则误伤） ---
# V6/V7 清单 + 2026-08~09 新域各取代表文件；任一缺失说明排除规则出了问题。
# 2026-09-17 全局复查补齐：hotel_analytics / rag / skills / platform_ebk_watch /
# 美团·飞猪 ebooking_watch 域本体 / pyramid_bidding / promotion_adjust 域本体 /
# 三平台 review adapter / 网关侧 watch_api.py；import 侧 $V6v7ProbeFiles 与本清单保持一致。
$V6v7RequiredFiles = @(
    "backend/app/domain/feishu_qa_cards.py",
    "backend/app/domain/page_change_alerts/service.py",
    "backend/app/domain/page_change_alerts/page_health_config.json",
    "backend/app/domain/distill/service.py",
    "backend/app/domain/browser/llm_chat_browser.py",
    "backend/app/domain/browser/doubao_chat.py",
    "backend/app/domain/browser/qianwen_chat.py",
    "backend/app/domain/ebk_global_push/service.py",
    "backend/app/domain/ebk_period_reads/service.py",
    "backend/app/domain/hotel_analytics/service.py",
    "backend/app/domain/rag/service.py",
    "backend/app/domain/skills/service.py",
    "backend/app/domain/platform_ebk_watch/push_policy.py",
    "backend/app/domain/meituan_ebooking_watch/service.py",
    "backend/app/domain/fliggy_ebooking_watch/service.py",
    "backend/app/domain/pyramid_bidding/pipeline.py",
    "backend/app/domain/promotion_adjust/pipeline.py",
    "backend/app/domain/review_management/scheduler.py",
    "backend/app/domain/review_management/service.py",
    "backend/app/domain/review_management/ctrip_review_adapter.py",
    "backend/app/domain/review_management/fliggy_review_adapter.py",
    "backend/app/domain/review_management/meituan_review_adapter.py",
    "backend/app/domain/refund_orders/service.py",
    "backend/app/domain/reservation_orders/service.py",
    "backend/app/domain/exports/__init__.py",
    "backend/app/domain/browser/ctrip_pyramid_bidding_flow.py",
    "backend/app/domain/browser/ctrip_promotion_adjust_flow.py",
    "backend/app/domain/browser/ebk_period_read_flows.py",
    "backend/app/workers/review_scheduler.py",
    "backend/app/workers/meituan_ebooking_scheduler.py",
    "backend/app/workers/fliggy_ebooking_scheduler.py",
    "pan-hotel-hermes/pan_hotel_mcp/server.py",
    "pan-hotel-hermes/client/watch_api.py",
    "pan-hotel-hermes/skills/pan-hotel-watch/references/ebk-period-read-quirks.md",
    "docs/飞书话术速查-业务版.xlsx"
)
if ($DryRun) {
    Write-Host "(DryRun: 跳过 V6/V7 新模块 staging 存在性核对；实际导出时会强制检查)"
} else {
    $v6v7Missing = @($V6v7RequiredFiles | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $ProjectStage $_))
    })
    if ($v6v7Missing.Count -gt 0) {
        throw "V6/V7 新增模块未进入打包 staging（检查排除规则）：$($v6v7Missing -join '; ')"
    }
    Write-Host "V6/V7 新增模块（蒸馏/QA卡片/页面告警/chat浏览器）已确认进入打包范围。"
}

# --- 环境信息采集 + migration-manifest.json -----------------------------------
function Get-ToolInfo {
    param([string]$ExeName, [string[]]$VersionArgs)
    $cmd = Get-Command $ExeName -ErrorAction SilentlyContinue
    if (-not $cmd -or -not $cmd.Source) { return $null }
    $version = ""
    try {
        $version = (& $cmd.Source @VersionArgs 2>$null | Out-String).Trim()
    } catch { $version = "" }
    return [ordered]@{ version = $version; path = $cmd.Source }
}

$pythonInfo = Get-ToolInfo -ExeName "python.exe" -VersionArgs @("--version")
$nodeInfo = Get-ToolInfo -ExeName "node.exe" -VersionArgs @("--version")
$pathEntries = @($env:Path -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$profileNames = @()
$profilesDir = Join-Path $HermesHome "profiles"
if (Test-Path -LiteralPath $profilesDir) {
    $profileNames = @(Get-ChildItem -LiteralPath $profilesDir -Directory | ForEach-Object { $_.Name })
}

$manifest = [ordered]@{
    schema        = "pan-hotel-watch-migrate/1"
    exportedAt    = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    machine       = $env:COMPUTERNAME
    user          = $env:USERNAME
    source        = [ordered]@{
        repoRoot      = $RepoRoot
        hermesHome    = $HermesHome
        hermesHomeEnv = $env:HERMES_HOME
        python        = $pythonInfo
        node          = $nodeInfo
        pathEntries   = $pathEntries
    }
    profiles      = $profileNames
    package       = [ordered]@{
        layout     = [ordered]@{ project = "project/"; hermes = "hermes/" }
        includeGit = [bool]$IncludeGit
        project    = $projectSummary
        hermes     = $hermesSummary
        v6v7Modules = $V6v7RequiredFiles
    }
}
$ManifestPath = Join-Path $Staging "migration-manifest.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8

Write-Host "已生成 $ManifestPath（profiles: $($profileNames -join ', ')）"

# --- 引导工具包（新机器零依赖冷启动） ------------------------------------------
# 新机器可能既没有 7-Zip 也没有 winget，连解压迁移包都做不到。这里把
# migrate-import.ps1 + 免安装版 7z.exe/7z.dll 放到 output\migrate-boot\，
# 新机器拷走「.7z + migrate-boot\」即可开始部署。DryRun 也会生成（不依赖压缩结果）。
$BootDir = Join-Path $OutputDir "migrate-boot"
New-Item -ItemType Directory -Force -Path $BootDir | Out-Null
$ImportScriptSrc = Join-Path $PSScriptRoot "migrate-import.ps1"
if (Test-Path -LiteralPath $ImportScriptSrc) {
    Copy-Item -LiteralPath $ImportScriptSrc -Destination $BootDir -Force
}
if ($SevenZip) {
    $sevenZipDir = Split-Path $SevenZip -Parent
    foreach ($bootFile in @("7z.exe", "7z.dll")) {
        $bootSrc = Join-Path $sevenZipDir $bootFile
        if (Test-Path -LiteralPath $bootSrc) { Copy-Item -LiteralPath $bootSrc -Destination $BootDir -Force }
    }
    Write-Host "引导工具包已生成：$BootDir（migrate-import.ps1 + 免安装 7z）"
} else {
    Write-Warning "本机无 7z，引导工具包仅含 migrate-import.ps1；新机器需另备 7z.exe + 7z.dll（与脚本同目录放置即可）。"
}

# --- Playwright 浏览器离线件（默认打包：本地复制无下载成本） ------------------
# npmmirror 的 binaries 镜像存在同步缺口（实测缺 build 1148 的 headless-shell，
# 404 会让 playwright install 直接失败）。浏览器文件是本机现成的，默认随引导包
# 带走，让新机器完全不掉进这类镜像缺口。chromium 主包体积过大（3.8GB）且镜像
# 实测有货，不打包。版本对齐：以后端 venv 的 browsers.json 为准。
$BootInstallersDir = Join-Path $BootDir "installers"
$browsersJson = Join-Path $RepoRoot ".venv\Lib\site-packages\playwright\driver\package\browsers.json"
if (Test-Path -LiteralPath $browsersJson) {
    try {
        $browserList = (Get-Content -Raw -LiteralPath $browsersJson | ConvertFrom-Json).browsers
        $msPlaywright = Join-Path $env:LOCALAPPDATA "ms-playwright"
        $pwDst = Join-Path $BootInstallersDir "playwright-browsers"
        New-Item -ItemType Directory -Force -Path $pwDst | Out-Null
        foreach ($b in $browserList) {
            if ($b.name -notin @("chromium-headless-shell", "ffmpeg")) { continue }
            $dirName = ($b.name -replace "-", "_") + "-" + $b.revision
            $srcDir = Join-Path $msPlaywright $dirName
            if (Test-Path -LiteralPath $srcDir) {
                & robocopy $srcDir (Join-Path $pwDst $dirName) /E /NFL /NDL /NP /R:1 /W:1 | Out-Null
                if ($LASTEXITCODE -ge 8) {
                    Write-Warning "  拷贝 Playwright $dirName 失败（exit $LASTEXITCODE）"
                } else {
                    Write-Host "  已打包 Playwright $dirName"
                }
            } else {
                Write-Warning "  本机 ms-playwright 缺少 $dirName，新机器将走镜像下载（可能缺文件）"
            }
        }
    } catch {
        Write-Warning "Playwright 浏览器离线件打包失败：$($_.Exception.Message)"
    }
} else {
    Write-Warning "未找到后端 venv 的 browsers.json，跳过 Playwright 浏览器离线件"
}

# --- 离线安装包（-IncludeInstallers：Python/Node/uv 联网下载，可选） ------------
function Invoke-MirrorDownload {
    param([string[]]$Urls, [string]$Destination)
    foreach ($url in $Urls) {
        try {
            try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
            Invoke-WebRequest -Uri $url -OutFile $Destination -UseBasicParsing -ErrorAction Stop
            if ((Test-Path -LiteralPath $Destination) -and ((Get-Item -LiteralPath $Destination).Length -gt 0)) {
                Write-Host "  已下载 $(Split-Path $Destination -Leaf) <- $url"
                return
            }
        } catch {
            Write-Warning "  下载失败：$url"
        }
    }
    throw "全部下载源失败：$($Urls -join ' ; ')"
}

if ($IncludeInstallers) {
    $installersDir = Join-Path $BootDir "installers"
    New-Item -ItemType Directory -Force -Path $installersDir | Out-Null
    Write-Host "下载离线安装包（国内镜像优先）-> $installersDir"
    try {
        Invoke-MirrorDownload -Urls @(
            "https://mirrors.huaweicloud.com/python/3.11.9/python-3.11.9-amd64.exe",
            "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
        ) -Destination (Join-Path $installersDir "python-3.11.9-amd64.exe")
    } catch {
        Write-Warning "Python 离线包下载失败（新机器将走在线镜像安装）：$($_.Exception.Message)"
    }
    try {
        # 便携版 zip（非 MSI）：新机器解压即用，不需要管理员权限
        Invoke-MirrorDownload -Urls @(
            "https://cdn.npmmirror.com/binaries/node/v20.18.1/node-v20.18.1-win-x64.zip",
            "https://nodejs.org/dist/v20.18.1/node-v20.18.1-win-x64.zip"
        ) -Destination (Join-Path $installersDir "node-v20.18.1-win-x64.zip")
    } catch {
        Write-Warning "Node 离线包下载失败（新机器将走在线镜像安装）：$($_.Exception.Message)"
    }
    $uvCmd = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($uvCmd -and $uvCmd.Source) {
        Copy-Item -LiteralPath $uvCmd.Source -Destination (Join-Path $installersDir "uv.exe") -Force
        Write-Host "  已复制本机 uv.exe（$($uvCmd.Source)）"
    } else {
        Write-Warning "本机未安装 uv，离线包不含 uv.exe（新机器将经 pip 镜像安装）。"
    }
    Write-Host "离线安装包完成：$installersDir"
}

# --- DryRun 到此为止 -----------------------------------------------------------
if ($DryRun) {
    Write-Host ""
    Write-Host "DryRun 完成（未实际拷贝、未压缩）。"
    Write-Host ("  项目本体预计拷贝 ~{0:N0} 个文件；跳过 {1}，失败 {2}（exit {3}）" -f `
        $projectSummary.Copied, $projectSummary.Skipped, $projectSummary.Failed, $projectSummary.Exit)
    Write-Host ("  Hermes 目录预计拷贝 ~{0:N0} 个文件；跳过 {1}，失败 {2}（exit {3}）" -f `
        $hermesSummary.Copied, $hermesSummary.Skipped, $hermesSummary.Failed, $hermesSummary.Exit)
    Write-Host ("  项目 robocopy 日志: {0}" -f $ProjectRoboLog)
    Write-Host ("  Hermes robocopy 日志: {0}" -f $HermesRoboLog)
    Write-Host "日志中包含完整的纳入/排除范围，请检查。"
    Write-Host ("  引导工具包    : {0}（已生成，与 .7z 一起拷到新机器）" -f $BootDir)
    Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
}

# --- 7z 压缩 -------------------------------------------------------------------
Write-Host ""
Write-Host "3/3 使用 7z 压缩 staging -> $ArchivePath（体积大，可能需要数分钟）..."
if (Test-Path -LiteralPath $ArchivePath) { Remove-Item -LiteralPath $ArchivePath -Force }
& $SevenZip a -t7z -mx=5 -mmt=on $ArchivePath (Join-Path $Staging "*")
$sevenZipExit = $LASTEXITCODE
# 7z: 0 成功，1 警告（可接受），>=2 失败
if ($sevenZipExit -ge 2) {
    throw "7z 压缩失败（exit $sevenZipExit）。"
}
if ($sevenZipExit -eq 1) {
    Write-Warning "7z 返回警告（exit 1），压缩包可能不完整，请检查上方输出。"
}

# --- 结果报告 ------------------------------------------------------------------
$stagingBytes = (Get-ChildItem -LiteralPath $Staging -Recurse -File -Force -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum
$archiveBytes = (Get-Item -LiteralPath $ArchivePath).Length
Write-Host ""
Write-Host "打包完成。"
Write-Host ("  压缩包        : {0}" -f $ArchivePath)
Write-Host ("  未压缩体积    : {0:N2} GB（项目 {1:N0} 文件 / Hermes {2:N0} 文件）" -f `
    ($stagingBytes / 1GB), $projectSummary.Copied, $hermesSummary.Copied)
Write-Host ("  压缩包大小    : {0:N2} GB" -f ($archiveBytes / 1GB))
if ($projectSummary.Skipped -gt 0 -or $projectSummary.Failed -gt 0 -or $hermesSummary.Skipped -gt 0 -or $hermesSummary.Failed -gt 0) {
    Write-Warning ("拷贝中有跳过/失败：项目 skipped={0} failed={1}；Hermes skipped={2} failed={3}（通常为被运行中浏览器占用的文件）。" -f `
        $projectSummary.Skipped, $projectSummary.Failed, $hermesSummary.Skipped, $hermesSummary.Failed)
}
Write-Host ""
Write-Host "新机器需拷贝（放同一目录）："
Write-Host ("  1. {0}" -f $ArchivePath)
Write-Host ("  2. {0}\ 整个文件夹（部署脚本 + 免安装 7z，新机器无需预装任何东西）" -f $BootDir)
Write-Host ""
Write-Host "跨机注意事项："
Write-Host "  - venv/node_modules 跨机不可靠，migrate-import.ps1 会自动重建。"
Write-Host "  - 浏览器登录态为 DPAPI 加密，新机器/新用户大概率需要重新登录 OTA 账号。"
Write-Host ""
Write-Host "安全提示：压缩包含 .env（飞书密钥/API key）、auth.json、tunnel-credentials.txt、"
Write-Host "浏览器登录态等敏感数据。传输时必须加密（加密压缩/加密通道），不得明文走网络。"

Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
exit 0
