# Pan Hotel Watch 一键迁移部署脚本（新 Windows 机器上运行）。
#
# 输入：migrate-export.ps1 生成的迁移包（pan-hotel-watch-migrate-{timestamp}.7z）。
# 自动完成：环境检查与安装 -> 解压 -> 重建 venv -> 配置环境变量 -> 注册 Hermes
# profiles -> 应用飞书配置 -> 启动服务 -> 自检 -> 提示登录 OTA 账号。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File D:\migrate\migrate-boot\migrate-import.ps1 -Package D:\pan-hotel-watch-migrate-xxxx.7z
#   （migrate-boot\ 随迁移包一起由 migrate-export.ps1 生成：含免安装 7z，
#     新机器没有 7-Zip / winget 也能解压；无 winget 时依赖自动走官方安装包直连静默安装）
#   常用参数：
#     -ProjectDir E:\pan-hotel-watch - hermes   项目安装路径（默认与源机一致，推荐保持一致）
#     -SkipEnvCheck                             已确认环境就绪时跳过 Step 0
#     -SkipRebuild                              调试：跳过 venv / node_modules 重建
#     -Force                                    目标目录已有内容时强制覆盖合并
#     -DryRun                                   只打印步骤，不实际执行
#
# 依赖说明：
#   - 需要网络（winget 安装依赖、pip/npm/uv 下载包、playwright 下载 chromium）
#   - feishu_profiles.py 必须用 Hermes agent venv 的 python.exe 运行（系统 Python 无 pyyaml）
#   - --hermes-root 必须指向 pan-hotel-hermes 项目目录，不是 %LOCALAPPDATA%\hermes

param(
    [Parameter(Mandatory = $true)][string]$Package,
    [string]$ProjectDir = "D:\work\pan-hotel-watch",
    [switch]$SkipEnvCheck,
    [switch]$SkipRebuild,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$NoChinaMirror   # 关闭国内镜像加速（海外机器用；默认全走国内镜像源）
)

$ErrorActionPreference = "Stop"

# --- UTF-8 控制台输出（PowerShell 5.1） ---------------------------------------
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONIOENCODING = "utf-8"

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$AgentDir = Join-Path $HermesHome "hermes-agent"
$HermesPython = Join-Path $AgentDir "venv\Scripts\python.exe"
$HermesExe = Join-Path $AgentDir "venv\Scripts\hermes.exe"
# HERMES_HOME 未设置时 hermes CLI 可能出现 120s 超时，提前设置
if (-not $env:HERMES_HOME) { $env:HERMES_HOME = $HermesHome }

# --- ProjectDir 盘符兜底（新机器可能没有源机的 E 盘） ---------------------------
$ProjectDirWasDefault = -not $PSBoundParameters.ContainsKey("ProjectDir")
$projectDriveRoot = [System.IO.Path]::GetPathRoot($ProjectDir)
if ($projectDriveRoot -and -not (Test-Path -LiteralPath $projectDriveRoot)) {
    if ($ProjectDirWasDefault) {
        # 默认路径的盘符不存在：改用迁移包所在盘 + 相同目录结构，并明确提示
        $packageDriveRoot = [System.IO.Path]::GetPathRoot((Split-Path -Parent $Package))
        if (-not $packageDriveRoot) { $packageDriveRoot = [System.IO.Path]::GetPathRoot($PWD.Path) }
        $ProjectDir = Join-Path $packageDriveRoot "pan-hotel-watch"
        Write-Warning "默认项目目录所在盘符 $projectDriveRoot 在本机不存在，自动改用：$ProjectDir"
        Write-Warning "如需安装到其他位置，请用 -ProjectDir 显式指定后重跑。"
    } else {
        throw "-ProjectDir 指定的盘符不存在：$projectDriveRoot（$ProjectDir）。请换一个存在的盘后重跑。"
    }
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    Write-Host ("[{0}] [{1}] {2}" -f (Get-Date -Format "HH:mm:ss"), $Level, $Message)
}

function Resolve-SevenZip {
    # 优先用随包引导的免安装 7z（migrate-boot），其次迁移包同目录，再退回
    # PATH / Program Files。保证零依赖新机器也能解压。
    $candidates = @(
        (Join-Path $PSScriptRoot "7z.exe"),
        (Join-Path (Split-Path -Parent $Package) "7z.exe"),
        (Join-Path (Split-Path $PSScriptRoot -Parent) "output\migrate-boot\7z.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
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

function Update-SessionPath {
    # winget 安装后当前会话的 PATH 不会自动刷新，从注册表重建
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Sync-PanHotelMcpSecretPlaceholders {
    # hermes-agent 启动 MCP 子进程时只透传白名单环境变量（tools/mcp_tool.py
    # _build_safe_env），profile/根 .env 里的 WATCH_API_SHARED_SECRET 进不了
    # pan_hotel_mcp。唯一通道是 mcp_servers.pan-hotel-watch.env 显式声明，
    # 值用 ${VAR} 占位符由对应 .env 插值解析。缺占位符时后端
    # PAN_HOTEL_ENFORCE_API_AUTH=true 会让 run-now 类 MCP 调用全部 401。
    # 返回变更行数（0 = 已符合要求）。
    param([Parameter(Mandatory)][string]$ConfigPath)
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return 0 }
    $placeholders = [ordered]@{
        "WATCH_API_SHARED_SECRET"  = '${WATCH_API_SHARED_SECRET}'
        "WATCH_API_SUPERVISOR_KEY" = '${WATCH_API_SUPERVISOR_KEY}'
    }
    $lines = @(Get-Content -LiteralPath $ConfigPath -Encoding UTF8)
    $changed = 0
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
            if ($line -match '^\s{6}([A-Za-z_][\w]*)\s*:') {
                $key = $Matches[1]
                $lastEnvIndex = $i
                if ($placeholders.Contains($key)) {
                    $desired = "      ${key}: $($placeholders[$key])"
                    if ("$($lines[$i])" -cne "$desired") { $lines[$i] = $desired; $changed++ }
                    $seen[$key] = $true
                }
                continue
            } elseif ($line -match '^\s{0,4}\S') {
                $inEnv = $false
            }
        }
    }
    if ($lastEnvIndex -ge 0) {
        $insert = @()
        foreach ($key in @($placeholders.Keys)) {
            if (-not $seen.ContainsKey($key)) { $insert += "      ${key}: $($placeholders[$key])" }
        }
        if ($insert.Count -gt 0) {
            $at = $lastEnvIndex + 1
            $before = @($lines | Select-Object -First $at)
            $after = @()
            if ($at -lt $lines.Count) { $after = @($lines[$at..($lines.Count - 1)]) }
            $lines = @($before + $insert + $after)
            $changed += $insert.Count
        }
    }
    if ($changed -gt 0) {
        Set-Content -LiteralPath $ConfigPath -Value $lines -Encoding UTF8
    }
    return $changed
}

function Test-PythonVersion {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    try {
        $out = (& python --version | Out-String).Trim()
    } catch { return $false }
    if ($out -match 'Python\s+(\d+)\.(\d+)') {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) { return $true }
    }
    return $false
}

function Test-PyLauncher {
    # 只查 where py 不够：py 可能指向已卸载的 Python（报 0x80070003），必须实测能启动
    $cmd = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    & py -3 -c "print('pyok')" | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Test-NodeVersion {
    $cmd = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    try {
        $out = (& node --version | Out-String).Trim()
    } catch { return $false }
    if ($out -match 'v(\d+)\.') { return ([int]$Matches[1] -ge 20) }
    return $false
}

function Install-DependencyViaWinget {
    param([string]$WingetId)
    Write-Log "通过 winget 安装 $WingetId（等待完成）..."
    & winget install --id $WingetId -e --accept-source-agreements --accept-package-agreements
    $code = $LASTEXITCODE
    Update-SessionPath
    if ($code -ne 0) {
        throw "winget install 失败：$WingetId（exit $code）。请手动安装后重跑，或修好后加 -SkipEnvCheck。"
    }
}

# --- 无 winget 机器的直连静默安装回退 ------------------------------------------
# 新机器可能没有 winget（不少 Windows 10 出厂就没有）。解压用的 7z 来自
# migrate-boot 引导包（免安装），其余依赖走官方安装包静默安装；这不需要
# 额外前提——install.bat 的 pip/npm/chromium 本来就必须联网。

function Invoke-DownloadFile {
    # 多源依次尝试（镜像在前、官方在后），全部失败才抛错
    param([string[]]$Urls, [string]$Destination)
    foreach ($url in $Urls) {
        Write-Log "下载 $url ..."
        try {
            try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
            Invoke-WebRequest -Uri $url -OutFile $Destination -UseBasicParsing -ErrorAction Stop
            if ((Test-Path -LiteralPath $Destination) -and ((Get-Item -LiteralPath $Destination).Length -gt 0)) { return }
        } catch {
            Write-Warning "下载失败（$($_.Exception.Message)），尝试下一个源..."
        }
    }
    throw "全部下载源失败：$($Urls -join ' ; ')"
}

function Get-BundledInstaller {
    # migrate-boot\installers\ 里的离线安装包（export -IncludeInstallers 生成）
    param([string]$Pattern)
    if (-not (Test-Path -LiteralPath $InstallersDir)) { return $null }
    $found = @(Get-ChildItem -LiteralPath $InstallersDir -Filter $Pattern -File -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($found.Count -gt 0) { return $found[0].FullName }
    return $null
}

function Add-PanHotelToolPath {
    # 便携版工具（node/uv）解压后加入会话 + 用户 PATH
    param([string]$Dir)
    if (-not (Test-Path -LiteralPath $Dir)) { return }
    if (($env:Path -split ';') -notcontains $Dir) { $env:Path = "$Dir;$env:Path" }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @($userPath -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($entries -notcontains $Dir) {
        [Environment]::SetEnvironmentVariable("Path", (($entries + $Dir) -join ";"), "User")
    }
}

function Install-PythonFromInstaller {
    param([string]$InstallerPath)
    # 官方安装器静默安装：含 py 启动器、写入 PATH（顺带可修复损坏的 py 启动器）
    Write-Log "静默安装 Python（$InstallerPath，含 py 启动器 + PATH，约 1-3 分钟）..."
    $proc = Start-Process -FilePath $InstallerPath `
        -ArgumentList @("/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_launcher=1", "Include_test=0", "Include_doc=0") `
        -Wait -PassThru
    Update-SessionPath
    if ($proc.ExitCode -ne 0) { throw "Python 静默安装失败（exit $($proc.ExitCode)）。" }
}

function Install-PythonDirect {
    $installer = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
    Invoke-DownloadFile -Urls $script:PythonInstallerUrls -Destination $installer
    Install-PythonFromInstaller -InstallerPath $installer
}

function Install-NodeFromZip {
    # 便携版 zip 解压即用，不写注册表；解压到 pan-hotel-tools 并入 PATH
    param([string]$ZipPath)
    $extractRoot = Join-Path $ToolDir "nodejs"
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    $sevenZip = Resolve-SevenZip
    if (-not $sevenZip) { throw "解压 Node 离线包需要 7z（migrate-boot 应已包含）。" }
    Write-Log "解压 Node 便携版 -> $extractRoot ..."
    & $sevenZip x "-o$extractRoot" -y $ZipPath | Out-Null
    if ($LASTEXITCODE -ge 2) { throw "Node 离线包解压失败（exit $LASTEXITCODE）。" }
    $nodeExe = Get-ChildItem -LiteralPath $extractRoot -Recurse -Filter "node.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $nodeExe) { throw "解压结果中未找到 node.exe。" }
    $nodeDir = Split-Path $nodeExe.FullName -Parent
    Add-PanHotelToolPath -Dir $nodeDir
    Write-Log "Node 便携版就绪：$nodeDir（已加入 PATH）"
}

function Install-NodeDirect {
    $msi = Join-Path $env:TEMP "node-v20.18.1-x64.msi"
    Invoke-DownloadFile -Urls $script:NodeMsiUrls -Destination $msi
    Write-Log "静默安装 Node.js 20.18.1（msiexec /qn，约 1-2 分钟）..."
    $proc = Start-Process -FilePath "msiexec.exe" -ArgumentList @("/i", $msi, "/qn") -Wait -PassThru
    Update-SessionPath
    if ($proc.ExitCode -ne 0) { throw "Node.js 静默安装失败（exit $($proc.ExitCode)）。" }
}

function Install-UvFromBundle {
    param([string]$Path)
    New-Item -ItemType Directory -Force -Path $ToolDir | Out-Null
    Copy-Item -LiteralPath $Path -Destination (Join-Path $ToolDir "uv.exe") -Force
    Add-PanHotelToolPath -Dir $ToolDir
    Write-Log "uv 离线就绪：$ToolDir\uv.exe（已加入 PATH）"
}

function Install-UvDirect {
    # Python 已就绪，pip 走 PyPI 镜像装 uv 最快；失败再退官方脚本（GitHub 源，慢）
    Write-Log "通过 pip 安装 uv（走镜像源）..."
    $priorEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & python -m pip install --upgrade uv 2>&1 | Out-Null
    $pipExit = $LASTEXITCODE
    $ErrorActionPreference = $priorEap
    if ($pipExit -eq 0 -and (Get-Command uv.exe -ErrorAction SilentlyContinue)) { return }
    Write-Log "pip 安装 uv 未成功（exit $pipExit），改用官方安装脚本..."
    $installer = Join-Path $env:TEMP "uv-install.ps1"
    Invoke-DownloadFile -Urls @("https://astral.sh/uv/install.ps1") -Destination $installer
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw "uv 官方安装脚本失败（exit $LASTEXITCODE）。" }
    Update-SessionPath
    $uvBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path -LiteralPath (Join-Path $uvBin "uv.exe")) { $env:Path = "$env:Path;$uvBin" }
}

function Install-SevenZipDirect {
    $installer = Join-Path $env:TEMP "7z2409-x64.exe"
    Invoke-DownloadFile -Urls @("https://www.7-zip.org/a/7z2409-x64.exe") -Destination $installer
    Write-Log "静默安装 7-Zip（/S，装到 Program Files；不加 PATH，由脚本按路径探测）..."
    Start-Process -FilePath $installer -ArgumentList "/S" -Wait | Out-Null
    Start-Sleep -Seconds 2
    if (-not (Resolve-SevenZip)) { throw "7-Zip 静默安装后仍找不到 7z.exe。" }
}

# --- 国内镜像加速（默认开启，-NoChinaMirror 关闭） ------------------------------
# 覆盖全部联网下载：pip（install.bat 两套 venv 依赖）、npm（前端依赖）、
# playwright（chromium 浏览器）、uv（hermes-agent 依赖）、Step 0 直连安装包
# （华为云 Python / npmmirror Node）。已手动设置的环境变量不会被覆盖。
$InstallersDir = Join-Path $PSScriptRoot "installers"
$ToolDir = Join-Path $env:LOCALAPPDATA "pan-hotel-tools"
if (-not $NoChinaMirror) {
    if (-not $env:PIP_INDEX_URL) { $env:PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple" }
    if (-not $env:UV_DEFAULT_INDEX) { $env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple" }
    if (-not $env:PLAYWRIGHT_DOWNLOAD_HOST) { $env:PLAYWRIGHT_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/playwright" }
    if (-not $env:npm_config_registry) { $env:npm_config_registry = "https://registry.npmmirror.com" }
    $script:PythonInstallerUrls = @(
        "https://mirrors.huaweicloud.com/python/3.11.9/python-3.11.9-amd64.exe",
        "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    )
    $script:NodeMsiUrls = @(
        "https://cdn.npmmirror.com/binaries/node/v20.18.1/node-v20.18.1-x64.msi",
        "https://nodejs.org/dist/v20.18.1/node-v20.18.1-x64.msi"
    )
} else {
    $script:PythonInstallerUrls = @("https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe")
    $script:NodeMsiUrls = @("https://nodejs.org/dist/v20.18.1/node-v20.18.1-x64.msi")
}

# --- 日志（部署全过程的 transcript 写入 migration-{timestamp}.log） ------------
$LogPath = Join-Path $ProjectDir "migration-$timestamp.log"
$transcriptStarted = $false
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $ProjectDir | Out-Null
    try {
        Start-Transcript -Path $LogPath -ErrorAction Stop | Out-Null
        $transcriptStarted = $true
    } catch {
        Write-Warning "无法启动日志记录（$($_.Exception.Message)），继续执行，日志仅输出到控制台。"
    }
}

$exitCode = 0
try {
    Write-Log "Pan Hotel Watch 迁移部署开始"
    Write-Log ("  迁移包 : {0}" -f $Package)
    Write-Log ("  项目目录: {0}" -f $ProjectDir)
    Write-Log ("  Hermes : {0}" -f $HermesHome)
    if ($DryRun) { Write-Log "DryRun 模式：只打印步骤，不实际执行。" }

    # --- 前置检查 ------------------------------------------------------------
    if (-not $DryRun) {
        if (-not (Test-Path -LiteralPath $Package)) {
            throw "迁移包不存在：$Package。请先在公司主机上运行 scripts\migrate-export.ps1 生成迁移包。"
        }
        if ((Test-Path -LiteralPath $ProjectDir) -and -not $Force) {
            # 目录可能是本脚本刚才为写 transcript 自建的（只含 migration-*.log），
            # 不能被自己误判为"非空"而拒绝部署（DryRun 不经过这段，实测才会暴露）
            $preExistingItems = @(Get-ChildItem -LiteralPath $ProjectDir -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -notlike "migration-*.log" } | Select-Object -First 1)
            if ($preExistingItems.Count -gt 0) {
                throw "目标项目目录已存在且非空：$ProjectDir。换一个 -ProjectDir，或加 -Force 覆盖合并。"
            }
        }
        if ((Test-Path -LiteralPath $HermesHome) -and (Get-ChildItem -LiteralPath $HermesHome -Force -ErrorAction SilentlyContinue | Select-Object -First 1) -and -not $Force) {
            throw "目标 Hermes 目录已存在且非空：$HermesHome。如确认覆盖合并请加 -Force。"
        }
    }

    # =========================================================================
    # Step 0：环境检查与自动安装
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 0/9 环境检查与自动安装 ===="
    if ($DryRun) {
        Write-Log "[DryRun] 将检查 Python 3.11+ / py 启动器(实测 py -3) / Node 20+ / npm / uv / 7z。"
        $mirrorLabel = "国内镜像（清华 PyPI / npmmirror npm+playwright / 华为云+npmmirror 安装包）"
        if ($NoChinaMirror) { $mirrorLabel = "官方源（-NoChinaMirror）" }
        Write-Log "[DryRun]   依赖安装优先级：migrate-boot\installers 离线包 -> 直连安装 -> winget；包下载源：$mirrorLabel"
        if (Test-Path -LiteralPath $InstallersDir) {
            $bundledCount = @(Get-ChildItem -LiteralPath $InstallersDir -File -ErrorAction SilentlyContinue).Count
            Write-Log "[DryRun]   离线安装包：$InstallersDir（$bundledCount 个文件，将优先使用）"
        } else {
            Write-Log "[DryRun]   离线安装包：不存在（走在线安装；可在源机 export -IncludeInstallers 生成）"
        }
        Write-Log "[DryRun]   解压用的 7z 优先取 migrate-boot\7z.exe（随包引导，免安装）。Git 缺失且无 winget 时仅提示，不阻塞。"
    } elseif ($SkipEnvCheck) {
        Write-Log "-SkipEnvCheck：跳过环境检查。"
    } else {
        $wingetCmd = Get-Command winget.exe -ErrorAction SilentlyContinue
        $wingetOk = $false
        if ($wingetCmd) {
            & winget --version | Out-Null
            if ($LASTEXITCODE -eq 0) { $wingetOk = $true }
        }
        if ($wingetOk) {
            Write-Log "winget 可用（作为最后的安装兜底；依赖安装优先走离线包/镜像直连，避免 winget 源慢）。"
        } else {
            Write-Warning "winget 不可用（不影响：依赖优先走离线包/镜像直连；仅 Git 会跳过，迁移流程本身不需要 Git）。"
        }

        if (Test-PythonVersion) {
            Write-Log "Python >= 3.11 已安装。"
        } else {
            Write-Log "Python 3.11+ 缺失或版本过低，开始安装（离线包 -> 镜像直连 -> winget）..."
            $pythonOk = $false
            $bundledPython = Get-BundledInstaller -Pattern "python-*.exe"
            try {
                if ($bundledPython) { Install-PythonFromInstaller -InstallerPath $bundledPython }
                else { Install-PythonDirect }
                $pythonOk = Test-PythonVersion
            } catch {
                Write-Warning "Python 直连安装失败：$($_.Exception.Message)"
            }
            if (-not $pythonOk -and $wingetOk) {
                Write-Log "改用 winget 安装 Python..."
                Install-DependencyViaWinget -WingetId "Python.Python.3.11"
            }
            if (-not (Test-PythonVersion)) { throw "Python 安装后仍未检测到 3.11+，请手动检查 PATH。" }
        }

        if (Test-PyLauncher) {
            Write-Log "py 启动器可用（py -3 实测通过）。"
        } else {
            Write-Warning "py 启动器缺失或损坏（install.bat 的硬依赖），尝试修复..."
            $pyFixed = $false
            $bundledPython = Get-BundledInstaller -Pattern "python-*.exe"
            try {
                # 官方安装器带 Include_launcher=1，可顺带修复损坏的 py 启动器
                if ($bundledPython) { Install-PythonFromInstaller -InstallerPath $bundledPython }
                else { Install-PythonDirect }
                $pyFixed = Test-PyLauncher
            } catch {
                Write-Warning "py 启动器修复安装失败：$($_.Exception.Message)"
            }
            if (-not $pyFixed -and $wingetOk) {
                & winget install --id Python.Python.3.11 -e --accept-source-agreements --accept-package-agreements
                $pyInstallCode = $LASTEXITCODE
                Update-SessionPath
                if ($pyInstallCode -ne 0) {
                    Write-Warning "winget 返回 exit $pyInstallCode（可能 Python 已安装但 py 启动器损坏）。"
                }
                $pyFixed = Test-PyLauncher
            }
            if (-not $pyFixed) {
                throw "py -3 启动器不可用。常见原因：py.exe 指向已卸载的 Python（运行 py 报 0x80070003）。请到「设置-应用」卸载旧 Python Launcher 后重装 Python 3.11（勾选 py launcher 与 PATH），再重跑本脚本。"
            }
            Write-Log "py 启动器已修复。"
        }

        if (Test-NodeVersion) {
            Write-Log "Node.js >= 20 已安装。"
        } else {
            Write-Log "Node.js 20+ 缺失或版本过低，开始安装（离线包 -> 镜像直连 -> winget）..."
            $nodeOk = $false
            $bundledNode = Get-BundledInstaller -Pattern "node-*-win-*.zip"
            try {
                if ($bundledNode) { Install-NodeFromZip -ZipPath $bundledNode }
                else { Install-NodeDirect }
                $nodeOk = Test-NodeVersion
            } catch {
                Write-Warning "Node.js 直连安装失败：$($_.Exception.Message)"
            }
            if (-not $nodeOk -and $wingetOk) {
                Write-Log "改用 winget 安装 Node.js..."
                Install-DependencyViaWinget -WingetId "OpenJS.NodeJS.LTS"
            }
            if (-not (Test-NodeVersion)) { throw "Node.js 安装后仍未检测到 20+，请手动检查 PATH。" }
        }

        if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
            Write-Log "npm 已安装。"
        } else {
            throw "npm 不可用（应随 Node.js 安装）。请检查 Node.js 安装是否完整。"
        }

        if (Get-Command uv.exe -ErrorAction SilentlyContinue) {
            Write-Log "uv 已安装。"
        } else {
            Write-Log "uv 缺失，开始安装（离线包 -> pip 镜像 -> 官方脚本 -> winget）..."
            $uvOk = $false
            $bundledUv = Get-BundledInstaller -Pattern "uv.exe"
            try {
                if ($bundledUv) { Install-UvFromBundle -Path $bundledUv }
                else { Install-UvDirect }
                $uvOk = [bool](Get-Command uv.exe -ErrorAction SilentlyContinue)
            } catch {
                Write-Warning "uv 安装失败：$($_.Exception.Message)"
            }
            if (-not $uvOk -and $wingetOk) {
                Write-Log "改用 winget 安装 uv..."
                Install-DependencyViaWinget -WingetId "astral-sh.uv"
            }
            if (-not (Get-Command uv.exe -ErrorAction SilentlyContinue)) {
                throw "uv 安装失败。请手动安装：pip install uv，或 irm https://astral.sh/uv/install.ps1 | iex"
            }
        }

        if (Get-Command git.exe -ErrorAction SilentlyContinue) {
            Write-Log "Git 已安装。"
        } elseif ($wingetOk) {
            Write-Log "Git 缺失，开始安装..."
            Install-DependencyViaWinget -WingetId "Git.Git"
            if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { throw "Git 安装失败，请手动安装。" }
        } else {
            # 迁移部署流程本身不用 git（.git 不在包内）；无 winget 时不阻塞
            Write-Warning "Git 未安装且无 winget。迁移部署不需要 Git，跳过；后续开发如需 git 请手动安装。"
        }

        $sevenZipPath = Resolve-SevenZip
        if ($sevenZipPath) {
            Write-Log "7z 可用：$sevenZipPath"
        } elseif ($wingetOk) {
            Write-Log "7-Zip 缺失，开始安装..."
            Install-DependencyViaWinget -WingetId "7zip.7zip"
            if (-not (Resolve-SevenZip)) { throw "7-Zip 安装失败，请手动安装（解压迁移包必需）。" }
        } else {
            Install-SevenZipDirect
        }
    }

    # =========================================================================
    # Step 1：解压迁移包
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 1/9 解压迁移包 ===="
    $TempExtract = Join-Path $env:TEMP "phw-migrate-import-$timestamp"
    if ($DryRun) {
        Write-Log "[DryRun] 将用 7z 解压迁移包到 $TempExtract，读取 migration-manifest.json，"
        Write-Log "[DryRun] 并把 project\ 拷贝到 $ProjectDir、hermes\ 拷贝到 $HermesHome。"
        $script:ManifestProfiles = @("jingli-ai", "manxin-ai", "quanji-ai")
    } else {
        $SevenZip = Resolve-SevenZip
        if (-not $SevenZip) { throw "7-Zip 不可用，无法解压。请安装 7-Zip 后重跑。" }

        # 快速完整性预检：只读归档目录（秒级），拦截“包还在拷贝中/传输被截断”的情况
        Write-Log "校验迁移包头（完整性预检）..."
        & $SevenZip l -bso0 $Package | Out-Null
        if ($LASTEXITCODE -ge 2) {
            throw "迁移包校验失败（7z exit $LASTEXITCODE）：文件可能不完整或仍在拷贝中。等拷贝彻底结束后重跑，或重新传输该包。"
        }

        if (Test-Path -LiteralPath $TempExtract) { Remove-Item -LiteralPath $TempExtract -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $TempExtract | Out-Null

        Write-Log "解压中（体积大，需要数分钟）：$Package"
        & $SevenZip x "-o$TempExtract" -y -bso0 -bsp1 $Package
        $unzipExit = $LASTEXITCODE
        # 7z: 0 成功，1 警告（可接受），>=2 失败
        if ($unzipExit -ge 2) { throw "7z 解压失败（exit $unzipExit）。" }
        if ($unzipExit -eq 1) { Write-Warning "7z 解压返回警告（exit 1），请留意上方输出。" }

        $manifestFile = Join-Path $TempExtract "migration-manifest.json"
        if (-not (Test-Path -LiteralPath $manifestFile)) {
            throw "解压结果中缺少 migration-manifest.json，不是 migrate-export.ps1 生成的标准迁移包。"
        }
        $Manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $manifestProfiles = @($Manifest.profiles | Where-Object { $_ })
        if ($manifestProfiles.Count -eq 0) {
            throw "migration-manifest.json 中没有 profiles，无法继续注册 Hermes。"
        }
        $script:ManifestProfiles = $manifestProfiles
        $extractProject = Join-Path $TempExtract "project"
        $extractHermes = Join-Path $TempExtract "hermes"
        if (-not (Test-Path -LiteralPath $extractProject)) { throw "解压结果中缺少 project\ 目录。" }
        if (-not (Test-Path -LiteralPath $extractHermes)) { throw "解压结果中缺少 hermes\ 目录。" }

        Write-Log ("迁移包导出时间: {0}；profiles: {1}" -f $Manifest.exportedAt, ($manifestProfiles -join ", "))
        if ($Manifest.source.repoRoot -and -not [string]::Equals([string]$Manifest.source.repoRoot, $ProjectDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Warning ("源机项目路径 {0} 与目标路径 {1} 不同：Step 5 会修正 profile config.yaml 中的 MCP 路径。" -f $Manifest.source.repoRoot, $ProjectDir)
        }

        Write-Log "拷贝 project\ -> $ProjectDir ..."
        & robocopy $extractProject $ProjectDir /E /NFL /NDL /NP /R:1 /W:1 /NJH | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy 拷贝项目失败（exit $LASTEXITCODE）。" }
        Write-Log "拷贝 hermes\ -> $HermesHome ..."
        & robocopy $extractHermes $HermesHome /E /NFL /NDL /NP /R:1 /W:1 /NJH | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy 拷贝 Hermes 目录失败（exit $LASTEXITCODE）。" }

        # 新模块落盘核对（老版本迁移包缺这些文件时只警告，不阻断）；
        # 与 migrate-export.ps1 的 $V6v7RequiredFiles 保持一致（2026-09-17 对齐）
        $V6v7ProbeFiles = @(
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
        $v6v7MissingOnTarget = @($V6v7ProbeFiles | Where-Object {
            -not (Test-Path -LiteralPath (Join-Path $ProjectDir $_))
        })
        if ($v6v7MissingOnTarget.Count -gt 0) {
            Write-Warning ("迁移包缺少 V6/V7 新模块（旧版本包？）：{0}" -f ($v6v7MissingOnTarget -join '; '))
        } else {
            Write-Log "V6/V7 新模块已确认落盘（蒸馏 / 页面变更告警 / QA 卡片模板 / chat 浏览器）。"
        }

        Remove-Item -LiteralPath $TempExtract -Recurse -Force -ErrorAction SilentlyContinue
        Write-Log "解压与落盘完成（临时目录已清理）。"
    }
    $profiles = @($script:ManifestProfiles)

    # =========================================================================
    # Step 2：重建 Python 虚拟环境
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 2/9 重建 Python 虚拟环境 ===="
    if ($DryRun) {
        Write-Log "[DryRun] 将删除打包带来的 .venv / pan-hotel-hermes\.venv / hermes-agent\venv，"
        Write-Log "[DryRun] 运行 scripts\install.bat（含双 venv + pip 依赖 + npm install + 前端构建 + playwright chromium 下载），"
        Write-Log "[DryRun] 再在 hermes-agent 下执行 uv venv venv --seed --python 3.11 + uv sync --extra all --extra feishu --extra edge-tts。"
    } elseif ($SkipRebuild) {
        Write-Log "-SkipRebuild：跳过 venv 重建（调试模式）。"
    } else {
        foreach ($staleVenv in @(
            (Join-Path $ProjectDir ".venv"),
            (Join-Path $ProjectDir "pan-hotel-hermes\.venv"),
            (Join-Path $AgentDir "venv"),
            (Join-Path $AgentDir ".venv")
        )) {
            if (Test-Path -LiteralPath $staleVenv) {
                Write-Log "删除打包带来的 venv：$staleVenv"
                # 7z 解包还原的 venv 文件可能带只读属性（归档保留），Remove-Item
                # -Force 对只读文件仍报"访问被拒绝"（生产机A 0918 实测
                # lark_oapi）；也可能是杀软正在扫描刚落盘文件的瞬时锁。先清属性，
                # 再按 Remove-Item -> cmd rd 交替重试（间隔吸收扫描锁）。
                & cmd.exe /c "attrib -r -s -h `"$staleVenv\*`" /s /d" 2>$null
                $venvRemoved = $false
                for ($venvTry = 1; $venvTry -le 3; $venvTry++) {
                    Remove-Item -LiteralPath $staleVenv -Recurse -Force -ErrorAction SilentlyContinue
                    if (-not (Test-Path -LiteralPath $staleVenv)) { $venvRemoved = $true; break }
                    & cmd.exe /c "rd /s /q `"$staleVenv`"" 2>$null
                    if (-not (Test-Path -LiteralPath $staleVenv)) { $venvRemoved = $true; break }
                    Start-Sleep -Seconds 3
                }
                if (-not $venvRemoved) {
                    throw "无法删除打包带来的旧 venv：$staleVenv（请手动删除后重跑）"
                }
            }
        }

        $installBat = Join-Path $ProjectDir "scripts\install.bat"
        if (-not (Test-Path -LiteralPath $installBat)) { throw "未找到 $installBat。" }

        # Playwright 浏览器离线件就位（npmmirror 实测缺 headless-shell build 1148，
        # 会导致 playwright install 404 失败；提前放置后 install.bat 会跳过对应下载）
        # 兼容两种引导包布局：installers\playwright-browsers（export 实际生成）与
        # playwright-browsers（早期布局）
        $pwBrowsersSrc = $null
        foreach ($pwCandidate in @(
            (Join-Path $PSScriptRoot "installers\playwright-browsers"),
            (Join-Path $PSScriptRoot "playwright-browsers")
        )) {
            if (Test-Path -LiteralPath $pwCandidate) { $pwBrowsersSrc = $pwCandidate; break }
        }
        if ($pwBrowsersSrc) {
            $msPlaywright = Join-Path $env:LOCALAPPDATA "ms-playwright"
            New-Item -ItemType Directory -Force -Path $msPlaywright | Out-Null
            Write-Log "放置 Playwright 浏览器离线件（$pwBrowsersSrc）-> $msPlaywright ..."
            & robocopy $pwBrowsersSrc $msPlaywright /E /NFL /NDL /NP /R:1 /W:1 | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "Playwright 浏览器离线件放置失败（exit $LASTEXITCODE）。" }
            $markerOk = [bool](Get-ChildItem -Path (Join-Path $msPlaywright "chromium_headless_shell-*\INSTALLATION_COMPLETE") -ErrorAction SilentlyContinue)
            Write-Log "Playwright 浏览器离线件已就位（install.bat 将跳过对应下载；标记文件检查：$markerOk）。"
        } else {
            Write-Warning "未找到 Playwright 浏览器离线件（migrate-boot\installers\playwright-browsers），chromium 相关文件将走镜像下载（npmmirror 可能缺 headless-shell 1148）。"
        }

        # 网络预检：死代理（代理软件已退出但系统代理开关残留）会让 pip/npm 全部失败，
        # 且报错深藏在 install.bat 内部难定位；在这里提前拦下并给出修复指引
        Write-Log "网络预检：测试镜像源连通性..."
        $mirrorReachable = $false
        $netError = ""
        try {
            $resp = Invoke-WebRequest -Uri "https://pypi.tuna.tsinghua.edu.cn/simple/" -UseBasicParsing -TimeoutSec 15
            $mirrorReachable = ($resp.StatusCode -eq 200)
        } catch {
            $netError = $_.Exception.Message
        }
        if (-not $mirrorReachable) {
            $proxyInfo = ""
            try {
                $iset = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction Stop
                $proxyInfo = "系统代理 ProxyEnable=$($iset.ProxyEnable) ProxyServer=$($iset.ProxyServer)"
            } catch { $proxyInfo = "系统代理状态读取失败" }
            $envProxy = @(($env:HTTP_PROXY, $env:HTTPS_PROXY, $env:http_proxy, $env:https_proxy) | Where-Object { $_ })
            $envPart = ""
            if ($envProxy) { $envPart = "；代理环境变量：$($envProxy -join ' ')" }
            throw "网络预检失败：无法访问镜像源（$netError）。常见原因：代理软件（Clash/v2rayN 等）已退出但系统代理开关仍开着（pip 报 ProxyError/10053）。修复：到「设置 → 网络和 Internet → 代理」关闭代理开关，或正常启动代理软件，然后重跑本脚本。检测到的配置：$proxyInfo$envPart"
        }
        Write-Log "镜像源连通，继续。"

        Write-Log "运行 scripts\install.bat（需要网络：pip/npm 下载与 playwright chromium 下载，耗时较长）..."
        & cmd.exe /c " `"$installBat`" --nopause "
        if ($LASTEXITCODE -ne 0) {
            throw "install.bat 失败（exit $LASTEXITCODE）。修复后可单独重跑：cmd /c `"$installBat`" --nopause"
        }

        if (-not (Test-Path -LiteralPath $AgentDir)) { throw "未找到 Hermes agent 目录：$AgentDir。" }
        Write-Log "重建 Hermes agent venv（目录名必须是 venv，register/start 脚本硬编码 hermes-agent\venv\Scripts\...）..."
        Push-Location $AgentDir
        $hadUvProjectEnv = $false
        try {
            # UV_PROJECT_ENVIRONMENT 确保 uv sync 写入 venv\ 而不是默认的 .venv\
            $env:UV_PROJECT_ENVIRONMENT = Join-Path $AgentDir "venv"
            $hadUvProjectEnv = $true
            & uv venv venv --seed --python 3.11
            if ($LASTEXITCODE -ne 0) { throw "uv venv 失败（exit $LASTEXITCODE）。" }
            # feishu / edge-tts 自 2026-05 起从 [all] 移除，迁移场景必须显式安装
            & uv sync --extra all --extra feishu --extra edge-tts
            if ($LASTEXITCODE -ne 0) { throw "uv sync 失败（exit $LASTEXITCODE）。" }
        } finally {
            if ($hadUvProjectEnv) { Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue }
            Pop-Location
        }
        if (-not (Test-Path -LiteralPath $HermesPython)) { throw "Hermes venv 重建后仍缺少 python.exe：$HermesPython" }
        if (-not (Test-Path -LiteralPath $HermesExe)) { throw "Hermes venv 重建后仍缺少 hermes.exe：$HermesExe（uv sync 应生成 [project.scripts] 入口）" }

        # 实测发现：部分 uv 版本的 sync 只装依赖+生成入口 exe，不把项目本体以
        # editable 方式装进 venv —— hermes.exe 一运行就 ModuleNotFoundError:
        # hermes_cli，Gateway 全部起不来。显式补 editable 安装（与源机 venv 的
        # __editable__.hermes_agent-*.pth 状态对齐），并实测 CLI 可运行。
        Write-Log "确保 hermes-agent 本体以 editable 方式安装进 venv..."
        $priorEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        # 用 uv 自带安装器：uv venv --seed 注入的 pip 可能不完整（实测 pip._vendor.packaging.__about__
        # 缺失导致 python -m pip 直接 ModuleNotFoundError）。uv pip install 不依赖 venv 内 pip，
        # 且不再吞输出（| Out-Null 会把失败原因吃掉，排查全靠猜），让输出进入 transcript。
        & uv pip install -e $AgentDir --python $HermesPython 2>&1
        $editExit = $LASTEXITCODE
        $ErrorActionPreference = $priorEap
        # uv sync 在多数版本已把项目本体以 editable 方式装进 venv，此处安装失败不代表部署失败：
        # 降级为告警，由下方 hermes CLI 实测兜底（真正失败会在那里 throw）。
        if ($editExit -ne 0) { Write-Log "editable 安装返回 exit $editExit（uv sync 可能已安装项目本体；以 hermes CLI 验证为准）" "WARN" }
        Write-Log "验证 hermes CLI 可运行..."
        & $HermesExe --version 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "hermes.exe 无法运行（venv 重建后自检失败，通常是项目本体未安装）。" }
        Write-Log "venv 重建完成（hermes CLI 验证通过；pan_hotel_mcp 的 editable 安装由 Step 5 register-hermes.ps1 补装）。"
    }

    # =========================================================================
    # Step 3：重建 Node.js 依赖（通常已含在 install.bat 中）
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 3/9 Node.js 依赖（通常由 install.bat 完成） ===="
    $frontendDir = Join-Path $ProjectDir "frontend"
    $nodeModulesOk = (Test-Path -LiteralPath (Join-Path $frontendDir "node_modules"))
    $distOk = (Test-Path -LiteralPath (Join-Path $frontendDir "dist"))
    if ($DryRun) {
        Write-Log "[DryRun] 常规路径（Step 2 已含 npm install + build）会直接跳过本步骤。"
    } elseif (-not $SkipRebuild) {
        Write-Log "Step 2 的 install.bat 已执行 npm install + npm run build，本步骤跳过。"
    } elseif ($nodeModulesOk -and $distOk) {
        Write-Log "-SkipRebuild：node_modules 与 dist 均存在，跳过。"
    } else {
        Write-Log "node_modules/dist 缺失，执行 npm ci + npm run build ..."
        Push-Location $frontendDir
        try {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci 失败（exit $LASTEXITCODE）。" }
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build 失败（exit $LASTEXITCODE）。" }
        } finally {
            Pop-Location
        }
    }

    # =========================================================================
    # Step 4：配置环境变量（用户级）
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 4/9 配置环境变量 ===="
    if ($DryRun) {
        Write-Log "[DryRun] 将设置用户级 HERMES_HOME = $HermesHome，并把以下目录追加到用户 PATH："
        Write-Log "[DryRun]   $AgentDir\venv\Scripts"
        Write-Log "[DryRun]   $ProjectDir\scripts"
    } else {
        [Environment]::SetEnvironmentVariable("HERMES_HOME", $HermesHome, "User")
        $env:HERMES_HOME = $HermesHome
        Write-Log "已设置用户级 HERMES_HOME = $HermesHome"

        $pathAdditions = @(
            (Join-Path $AgentDir "venv\Scripts"),
            (Join-Path $ProjectDir "scripts")
        )
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $entries = @($userPath -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        $newEntries = @()
        foreach ($addition in $pathAdditions) {
            $already = $false
            foreach ($entry in $entries) {
                if ([string]::Equals($entry, $addition, [System.StringComparison]::OrdinalIgnoreCase)) { $already = $true; break }
            }
            if (-not $already -and $newEntries -notcontains $addition) { $newEntries += $addition }
        }
        if ($newEntries.Count -gt 0) {
            [Environment]::SetEnvironmentVariable("Path", (($entries + $newEntries) -join ";"), "User")
            $env:Path = "$env:Path;$($newEntries -join ';')"
            Write-Log ("已追加用户 PATH：{0}" -f ($newEntries -join ' ; '))
        } else {
            Write-Log "用户 PATH 已包含所需条目。"
        }
    }

    # =========================================================================
    # Step 5：注册 Hermes profiles
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 5/9 注册 Hermes profiles ===="
    if ($DryRun) {
        Write-Log ("[DryRun] 将对每个 profile（{0}）执行 register-hermes.ps1 -Profile <key> -SkipModelHint，" -f ($profiles -join ", "))
        Write-Log "[DryRun] 并修正 profile config.yaml 中 pan-hotel-watch MCP 的 cwd / PYTHONPATH / command 路径。"
    } else {
        $registerScript = Join-Path $ProjectDir "pan-hotel-hermes\scripts\register-hermes.ps1"
        if (-not (Test-Path -LiteralPath $registerScript)) { throw "未找到 $registerScript。" }
        foreach ($profile in $profiles) {
            $profileRoot = Join-Path (Join-Path $HermesHome "profiles") $profile
            if (-not (Test-Path -LiteralPath $profileRoot)) {
                throw "Hermes profile 目录不存在：$profileRoot（迁移包 hermes\profiles 应包含它）。"
            }
            Write-Log "注册 profile：$profile"
            & $registerScript -Profile $profile -SkipModelHint
        }

        # register-hermes 只同步 command（旧 venv 模式时）/ WATCH_API_BASE_URL / timeout
        # 和 manifest profiles 的 MCP env 密钥占位符，不重写 MCP 块中的 cwd 与
        # PYTHONPATH。ProjectDir 或用户名与源机不同时必须修正；根 config.yaml
        # （默认 profile Gateway 使用）不在 register 名单里，占位符也在这里补。
        $ProjectHermesRoot = Join-Path $ProjectDir "pan-hotel-hermes"
        $McpPythonFwd = ($HermesPython -replace "\\", "/")
        $McpCwdFwd = ($ProjectHermesRoot -replace "\\", "/")
        foreach ($configPath in (@($profiles | ForEach-Object { Join-Path (Join-Path $HermesHome "profiles\$_") "config.yaml" }) + @(Join-Path $HermesHome "config.yaml"))) {
            if (-not (Test-Path -LiteralPath $configPath)) { continue }
            $lines = @(Get-Content -LiteralPath $configPath -Encoding UTF8)
            $inBlock = $false
            $changed = 0
            for ($i = 0; $i -lt $lines.Count; $i++) {
                if ($lines[$i] -match '^  pan-hotel-watch:\s*$') { $inBlock = $true; continue }
                if ($inBlock -and $lines[$i] -match '^\S') { $inBlock = $false }
                if (-not $inBlock) { continue }
                $newLine = $null
                if ($lines[$i] -match '^(\s*cwd:\s*).*$') {
                    $newLine = $Matches[1] + $McpCwdFwd
                } elseif ($lines[$i] -match '^(\s*PYTHONPATH:\s*).*$') {
                    $newLine = $Matches[1] + $McpCwdFwd
                } elseif ($lines[$i] -match '^(\s*command:\s*).*$') {
                    $newLine = $Matches[1] + $McpPythonFwd
                }
                if ($null -ne $newLine -and $newLine -ne $lines[$i]) {
                    $lines[$i] = $newLine
                    $changed++
                }
            }
            if ($changed -gt 0) {
                Set-Content -LiteralPath $configPath -Value $lines -Encoding UTF8
                Write-Log "已修正 $configPath 中 pan-hotel-watch MCP 路径（$changed 处）。"
            }
            $secretChanges = Sync-PanHotelMcpSecretPlaceholders -ConfigPath $configPath
            if ($secretChanges -gt 0) {
                Write-Log "已补齐 $configPath 中 MCP env 密钥占位符（$secretChanges 处，根与全部 profiles 均覆盖）。"
            }
        }
    }

    # =========================================================================
    # Step 6：应用飞书配置
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 6/9 应用飞书配置 ===="
    $ProjectHermesRoot = Join-Path $ProjectDir "pan-hotel-hermes"
    $ManifestTool = Join-Path $ProjectHermesRoot "scripts\feishu_profiles.py"
    $ManifestPath = Join-Path $ProjectHermesRoot "config\feishu-profiles.yaml"
    $BackendEnvPath = Join-Path $ProjectDir ".env"
    if ($DryRun) {
        Write-Log "[DryRun] 将用 Hermes venv 的 python.exe 运行 feishu_profiles.py（系统 Python 无 pyyaml，不可用）："
        Write-Log "[DryRun]   validate --hermes-root $ProjectHermesRoot（注意：指向项目目录，不是 $HermesHome）"
        Write-Log "[DryRun]   write-backend-env --dotenv $BackendEnvPath"
        Write-Log ("[DryRun]   对每个 profile（{0}）执行 apply --profile-root %LOCALAPPDATA%\hermes\profiles\<key> --backend-dotenv ..." -f ($profiles -join ", "))
    } else {
        if (-not (Test-Path -LiteralPath $HermesPython)) { throw "Hermes venv Python 不存在：$HermesPython（Step 2 重建应生成）。" }
        if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "飞书清单不存在：$ManifestPath（应由迁移包带过来）。" }

        & $HermesPython $ManifestTool --manifest $ManifestPath --hermes-root $ProjectHermesRoot validate
        if ($LASTEXITCODE -ne 0) { throw "feishu_profiles validate 失败。请检查 $ManifestPath。" }

        & $HermesPython $ManifestTool --manifest $ManifestPath --hermes-root $ProjectHermesRoot write-backend-env --dotenv $BackendEnvPath
        if ($LASTEXITCODE -ne 0) { throw "write-backend-env 失败（后端 .env：$BackendEnvPath）。" }

        # apply 名单以飞书清单为准，不按 profiles 目录数。源机可能存在"目录有、
        # 清单没有"的 profile（如未接飞书的 quanji-ai），对它 apply 会报 Unknown profile。
        $feishuProfiles = @(& $HermesPython $ManifestTool --manifest $ManifestPath --hermes-root $ProjectHermesRoot "list" | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
        if ($LASTEXITCODE -ne 0 -or $feishuProfiles.Count -eq 0) { throw "无法从飞书清单列出 profiles（$ManifestPath）。" }

        foreach ($profile in $feishuProfiles) {
            $profileRoot = Join-Path (Join-Path $HermesHome "profiles") $profile
            & $HermesPython $ManifestTool --manifest $ManifestPath --hermes-root $ProjectHermesRoot apply --profile $profile --profile-root $profileRoot --backend-dotenv $BackendEnvPath
            if ($LASTEXITCODE -ne 0) { throw "apply 失败：profile $profile。" }
        }
        $notInManifest = @($profiles | Where-Object { $feishuProfiles -notcontains $_ })
        if ($notInManifest.Count -gt 0) {
            Write-Warning ("这些 Hermes profile 不在飞书清单中，已注册但跳过飞书配置（与源机行为一致）：{0}" -f ($notInManifest -join ', '))
        }

        # 不在飞书清单中的 profile（如 quanji-ai）不会经过 feishu_profiles apply，
        # 其 .env 拿不到 WATCH_API_SHARED_SECRET；MCP env 里的 ${VAR} 占位符会解析
        # 失败（保持字面量），后端开鉴权时 run-now 类调用全部 401。这里对全部
        # profiles + 根（默认 profile Gateway 读根 .env，源机曾带过期令牌）统一
        # 补齐；.env 不存在则创建（只含这两个键）。
        $backendEnvMap = @{}
        foreach ($line in (Get-Content -LiteralPath $BackendEnvPath -Encoding UTF8)) {
            $t = $line.Trim()
            if (-not $t -or $t.StartsWith("#") -or $t -notmatch "=") { continue }
            $k = $t.Substring(0, $t.IndexOf("=")).Trim()
            $v = $t.Substring($t.IndexOf("=") + 1).Trim().Trim('"').Trim("'")
            $backendEnvMap[$k] = $v
        }
        $tokenForProfiles = [string]$backendEnvMap["WATCH_API_SHARED_SECRET"]
        $supervisorForProfiles = [string]$backendEnvMap["PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"]
        $secretBackfillTargets = @($profiles | ForEach-Object { Join-Path (Join-Path $HermesHome "profiles") "$_\.env" })
        $secretBackfillTargets += (Join-Path $HermesHome ".env")
        foreach ($dotEnvPath in $secretBackfillTargets) {
            if (-not (Test-Path -LiteralPath $dotEnvPath)) {
                New-Item -ItemType File -Path $dotEnvPath -Force | Out-Null
            }
            $vals = @{}
            if ($tokenForProfiles) { $vals["WATCH_API_SHARED_SECRET"] = $tokenForProfiles }
            if ($supervisorForProfiles) { $vals["WATCH_API_SUPERVISOR_KEY"] = $supervisorForProfiles }
            if ($vals.Count -eq 0) { continue }
            $existingLines = @(Get-Content -LiteralPath $dotEnvPath -Encoding UTF8)
            $remaining = @{}
            foreach ($k in $vals.Keys) { $remaining[$k] = $vals[$k] }
            $resultLines = @()
            foreach ($line in $existingLines) {
                $k = $null
                if ($line -match "^\s*([^=#\s]+)\s*=") { $k = $Matches[1].Trim() }
                if ($k -and $remaining.ContainsKey($k)) {
                    $resultLines += "$k=$($remaining[$k])"
                    $remaining.Remove($k) | Out-Null
                } else {
                    $resultLines += $line
                }
            }
            foreach ($k in $remaining.Keys) { $resultLines += "$k=$($remaining[$k])" }
            Set-Content -LiteralPath $dotEnvPath -Value ($resultLines -join "`r`n") -Encoding UTF8
        }
        Write-Log "已为根与全部 profiles 补齐 WATCH_API_SHARED_SECRET / WATCH_API_SUPERVISOR_KEY（供 MCP env 占位符解析）。"
        Write-Log "飞书配置应用完成（validate -> write-backend-env -> apply，清单 profiles：$($feishuProfiles -join ', ')）。"
    }

    # =========================================================================
    # Step 7：启动服务
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 7/9 启动服务 ===="
    if ($DryRun) {
        Write-Log "[DryRun] 将运行 scripts\start-all.bat（后端 + 调度 worker + 前端 + Hermes Gateway 检查）。"
    } else {
        $startAllBat = Join-Path $ProjectDir "scripts\start-all.bat"
        if (-not (Test-Path -LiteralPath $startAllBat)) { throw "未找到 $startAllBat。" }
        # < nul：start-all.bat 末尾的 pause 在非交互会话（计划任务/session 0）里永远等按键，
        # 会把整个迁移卡死在 Step 7；喂入 nul 让 pause 立即读到 EOF 返回，交互式运行无副作用。
        & cmd.exe /c " `"$startAllBat`" < nul "
        if ($LASTEXITCODE -ne 0) {
            throw "start-all.bat 失败（exit $LASTEXITCODE）。请查看 $ProjectDir\.runtime\logs 下各服务日志。"
        }
    }

    # =========================================================================
    # Step 8：自检
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 8/9 自检 ===="
    if ($DryRun) {
        Write-Log "[DryRun] 将检查后端 /api/v1/health、hermes -p <首个profile> mcp list、profile 软链状态、"
        Write-Log "[DryRun] 主数据库/.env 落盘，并尽力注册开机自启计划任务 + 开放防火墙端口（失败仅告警）。"
    } else {
        # 端口：优先 PAN_HOTEL_WATCH_BACKEND_PORT，其次 BACKEND_PORT，默认 8100
        $backendPort = 8100
        if (Test-Path -LiteralPath $BackendEnvPath) {
            $panPort = $null; $bkPort = $null
            foreach ($line in (Get-Content -LiteralPath $BackendEnvPath)) {
                $trimmed = $line.Trim()
                if (-not $trimmed -or $trimmed.StartsWith("#") -or $trimmed -notmatch '=') { continue }
                $key = $trimmed.Substring(0, $trimmed.IndexOf('=')).Trim()
                $value = $trimmed.Substring($trimmed.IndexOf('=') + 1).Trim().Trim('"').Trim("'")
                if ($key -eq "PAN_HOTEL_WATCH_BACKEND_PORT") { $panPort = $value }
                elseif ($key -eq "BACKEND_PORT") { $bkPort = $value }
            }
            $portValue = $panPort
            if (-not $portValue) { $portValue = $bkPort }
            if ($portValue) {
                try { $backendPort = [int]$portValue } catch { $backendPort = 8100 }
            }
        }
        $healthUrl = "http://127.0.0.1:$backendPort/api/v1/health"
        Write-Log "后端健康检查：$healthUrl"
        $healthy = $false
        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline) {
            try {
                $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
                if ($resp.status -eq "ok") { $healthy = $true; break }
            } catch { Start-Sleep -Seconds 3 }
        }
        if ($healthy) {
            Write-Log "后端健康检查通过。"
        } else {
            throw "后端健康检查失败（60 秒内 $healthUrl 未返回 ok）。请查看 $ProjectDir\.runtime\logs\backend.*.log。"
        }

        $checkResults = @()
        if (Test-Path -LiteralPath $HermesExe) {
            $firstProfile = $profiles[0]
            Write-Log "Hermes 工具注册检查：hermes -p $firstProfile mcp list"
            $priorEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            $mcpOutput = ""
            $mcpExit = -1
            try {
                # 子命令不带斜杠：`/mcp list` 是网关内斜杠命令，CLI 下 exit 2
                $mcpOutput = (& $HermesExe -p $firstProfile mcp list 2>&1 | Out-String).Trim()
                $mcpExit = $LASTEXITCODE
            } catch {
                $mcpOutput = $_.Exception.Message
            }
            $ErrorActionPreference = $priorEap
            if ($mcpExit -eq 0 -and $mcpOutput -match 'pan-hotel-watch') {
                $checkResults += "Hermes MCP 注册（$firstProfile）：通过"
            } else {
                Write-Warning "hermes /mcp list 未确认 pan-hotel-watch（exit $mcpExit）。输出片段：$($mcpOutput -split "`r?`n" | Select-Object -First 5)"
                $checkResults += "Hermes MCP 注册（$firstProfile）：未确认（请手动运行 hermes -p $firstProfile /mcp list 检查）"
            }
        } else {
            $checkResults += "Hermes MCP 注册：跳过（未找到 $HermesExe）"
        }

        foreach ($profile in $profiles) {
            $profileRoot = Join-Path (Join-Path $HermesHome "profiles") $profile
            foreach ($pair in @(
                @{ Label = "skill pan-hotel-watch"; Path = (Join-Path $profileRoot "skills\pan-hotel-watch") },
                @{ Label = "plugin pan-hotel-feishu-session"; Path = (Join-Path $profileRoot "plugins\pan-hotel-feishu-session") }
            )) {
                $item = Get-Item -LiteralPath $pair.Path -ErrorAction SilentlyContinue
                if ($null -eq $item) {
                    $checkResults += "[$profile] $($pair.Label)：缺失"
                } elseif (@('SymbolicLink', 'Junction') -contains $item.LinkType) {
                    $checkResults += "[$profile] $($pair.Label)：链接生效（$($item.LinkType)）-> $($item.Target)"
                } else {
                    $checkResults += "[$profile] $($pair.Label)：拷贝模式（未开开发者模式时的降级，可正常使用，仓库改动后需重跑 register-hermes.ps1）"
                }
            }
        }

        # 核心数据落盘核对：主库与 .env 是 robocopy 全量拷贝最容易静默丢的两样
        # （backend\pan_hotel_watch.db 0 字节陈旧库已在 export 侧排除，这里只认真库）。
        $mainDb = Join-Path $ProjectDir "pan_hotel_watch.db"
        if ((Test-Path -LiteralPath $mainDb) -and ((Get-Item -LiteralPath $mainDb).Length -gt 0)) {
            $checkResults += ("主数据库：已落盘（{0:N1} MB）" -f ((Get-Item -LiteralPath $mainDb).Length / 1MB))
        } else {
            $checkResults += "主数据库：缺失或为空（$mainDb）——迁移包可能不完整"
        }
        if (Test-Path -LiteralPath $BackendEnvPath) {
            $checkResults += "后端 .env：已落盘"
        } else {
            $checkResults += "后端 .env：缺失（$BackendEnvPath）——Step 6 飞书配置将不可用"
        }

        # 开机自启 + 防火墙（migration-code-review P2-1/P2-2：均不随文件夹迁移）。
        # 尽力而为：失败只告警，不影响部署结果（可事后手动跑 scripts\ 下同名脚本）。
        $startupTaskScript = Join-Path $ProjectDir "scripts\register-startup-task.ps1"
        if (Test-Path -LiteralPath $startupTaskScript) {
            try {
                & powershell -NoProfile -ExecutionPolicy Bypass -File $startupTaskScript 2>&1 | ForEach-Object { Write-Log "  [startup-task] $_" }
                if ($LASTEXITCODE -eq 0) { $checkResults += "开机自启计划任务：已注册（登录触发，preview 前端）" }
                else { $checkResults += "开机自启计划任务：注册返回 exit $LASTEXITCODE（可手动重跑 register-startup-task.ps1）" }
            } catch {
                Write-Warning "开机自启注册失败：$($_.Exception.Message)（可手动重跑 scripts\register-startup-task.ps1）"
                $checkResults += "开机自启计划任务：注册失败（手动重跑 register-startup-task.ps1）"
            }
        }
        $firewallScript = Join-Path $ProjectDir "scripts\open-firewall.ps1"
        if (Test-Path -LiteralPath $firewallScript) {
            try {
                # open-firewall.ps1 默认 8000，实际端口从后端 .env 解析（Step 8 上方）
                & powershell -NoProfile -ExecutionPolicy Bypass -File $firewallScript -BackendPort $backendPort 2>&1 | ForEach-Object { Write-Log "  [firewall] $_" }
                if ($LASTEXITCODE -eq 0) { $checkResults += "防火墙入站规则：已开放（后端/前端端口）" }
                else { $checkResults += "防火墙入站规则：脚本返回 exit $LASTEXITCODE（同事局域网访问可能不通）" }
            } catch {
                Write-Warning "防火墙规则开放失败：$($_.Exception.Message)（需要管理员权限；可手动重跑 open-firewall.ps1）"
                $checkResults += "防火墙入站规则：开放失败（手动重跑 open-firewall.ps1）"
            }
        }
        Write-Log "部署自检结果："
        foreach ($result in $checkResults) { Write-Log "  - $result" }
    }

    # =========================================================================
    # Step 9：提示用户登录
    # =========================================================================
    Write-Log ""
    Write-Log "==== Step 9/9 完成 ===="
    Write-Host ""
    Write-Host "✅ 系统部署完成"
    if (-not $DryRun) {
        Write-Host "⚠️ 请登录以下平台浏览器账号："
        Write-Host "   1. 携程 EBK - 在系统操作台点击「EBK 管理」→「登录」"
        Write-Host "   2. 美团 EBK - 同上"
        Write-Host "   3. 飞猪 EBK - 同上"
        Write-Host "登录完成后在飞书群 @机器人 测试是否正常回复。"
        Write-Host ""
        Write-Host "生产迁移注意事项："
        Write-Host "   - 如旧机器的服务仍在运行，请尽快停掉（旧机器跑 scripts\stop-all.bat），"
        Write-Host "     否则两台机器的 Hermes Gateway 会抢飞书消息，机器人回复时灵时不灵。"
        Write-Host "   - 首次打开 OTA 页面较慢属正常（冷缓存）。若解压/构建异常缓慢，"
        Write-Host "     可把项目目录加入杀毒软件排除列表（实测可快数倍）。"
        Write-Host "   - 公网隧道（cloudflared/ngrok 等）不随迁移自动恢复，需要时在新机器手动重启。"
        Write-Host ""
        Write-Log ("部署日志：{0}" -f $LogPath)
    } else {
        Write-Host "（DryRun 演练完成，以上步骤未实际执行）"
    }
} catch {
    $exitCode = 1
    Write-Log "迁移失败：$($_.Exception.Message)" "ERROR"
    if ($_.InvocationInfo.PositionMessage) { Write-Log $_.InvocationInfo.PositionMessage "ERROR" }
    Write-Host ""
    Write-Host "❌ 迁移部署失败。排查后可重跑本脚本（目录已存在时加 -Force 覆盖合并）。" 
    if (-not $DryRun -and $transcriptStarted) { Write-Host ("完整日志：{0}" -f $LogPath) }
} finally {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch { }
    }
}
exit $exitCode
