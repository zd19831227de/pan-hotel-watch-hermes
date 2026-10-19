# 修复迁移后飞书 @机器人 401 / 连不上后台 的问题（在目标机运行）。
#
# 根因（2026-08 迁移 10.10.10.3 实测）：
#   1) migrate-import Step 6 的 feishu_profiles.py write-backend-env 会把后端 .env 的
#      PAN_HOTEL_ENFORCE_API_AUTH 强制写为 true（源机为 false）；
#   2) hermes-agent 启动 MCP 子进程时只透传白名单环境变量（PATH/HOME 等，见
#      hermes-agent tools/mcp_tool.py _build_safe_env），profile .env 里的
#      WATCH_API_SHARED_SECRET 根本进不了 pan_hotel_mcp 子进程；
#   3) 后端鉴权一开，MCP 的 run-now / 日简报 / 分析类调用全部 401「没有权限」。
#   另：profile config.yaml 的 WATCH_API_BASE_URL 端口不对（如遗留 8010）时，
#      MCP 报「无法连接 pan-hotel-watch 后台 API」。
#
# 本脚本自包含（不依赖仓库脚本版本），做四件事：
#   A. 后端 .env：按 -SetEnforceAuth 调整 PAN_HOTEL_ENFORCE_API_AUTH（默认 keep）
#   B. Hermes 各 profile 的 .env：写入与后端一致的 WATCH_API_SHARED_SECRET，
#      以及 WATCH_API_SUPERVISOR_KEY（取自后端 PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY）
#   C. Hermes 根/各 profile 的 config.yaml：把 mcp_servers.pan-hotel-watch.env 补上
#      ${WATCH_API_SHARED_SECRET} / ${WATCH_API_SUPERVISOR_KEY} 占位符（hermes-agent
#      会用 profile .env 插值解析后传给 MCP 子进程），并同步 WATCH_API_BASE_URL 端口
#   D. 重启飞书 profile 的 Hermes Gateway（--replace），并自检
#
# 用法（目标机，普通用户 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File .\fix-hermes-mcp-auth.ps1 -DryRun    # 先预览
#   powershell -ExecutionPolicy Bypass -File .\fix-hermes-mcp-auth.ps1            # 执行修复
#   powershell -ExecutionPolicy Bypass -File .\fix-hermes-mcp-auth.ps1 -SetEnforceAuth true
#   参数：-ProjectDir D:\work\pan-hotel-watch   -NoGatewayRestart
#
# 安全：所有密钥只显示前 4 位；不动 FEISHU_* 配置；支持 -DryRun。

param(
    [string]$ProjectDir = "D:\work\pan-hotel-watch",
    [string]$HermesHome,
    [switch]$DryRun,
    [switch]$NoGatewayRestart,
    [ValidateSet("keep", "true", "false")]
    [string]$SetEnforceAuth = "keep"
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

function Mask-Secret([string]$v) {
    if ([string]::IsNullOrWhiteSpace($v)) { return "(空)" }
    if ($v.Length -le 4) { return $v.Substring(0, $v.Length) + "****" }
    return $v.Substring(0, 4) + "****"
}

function Read-DotEnv([string]$Path) {
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($line in @(Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#") -or $t -notmatch "=") { continue }
        $k = $t.Substring(0, $t.IndexOf("=")).Trim()
        $v = $t.Substring($t.IndexOf("=") + 1).Trim().Trim('"').Trim("'")
        $map[$k] = $v
    }
    return $map
}

function Update-DotEnvValue {
    # 与 feishu_profiles._merge_dotenv 同语义：已有行原位替换，缺失则追加。
    param([string]$Path, [hashtable]$Values)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $existing = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    $remaining = @{}
    foreach ($k in $Values.Keys) { $remaining[$k] = $Values[$k] }
    $managed = $remaining.Keys
    $result = @()
    $changed = $false
    foreach ($line in $existing) {
        $k = $null
        if ($line -match "^\s*([^=#\s]+)\s*=") { $k = $Matches[1].Trim() }
        if ($k -and $remaining.ContainsKey($k)) {
            $newLine = "$k=$($remaining[$k])"
            if ($line -cne $newLine) { $changed = $true }
            $result += $newLine
            $remaining.Remove($k) | Out-Null
        } elseif (-not ($k -and ($managed -contains $k))) {
            $result += $line
        }
    }
    foreach ($k in $remaining.Keys) { $result += "$k=$($remaining[$k])"; $changed = $true }
    if ($changed -and -not $DryRun) {
        Set-Content -LiteralPath $Path -Value ($result -join "`r`n") -Encoding UTF8
    }
    return $changed
}

function Sync-McpConfig {
    # 在 mcp_servers.pan-hotel-watch.env 中：同步 WATCH_API_BASE_URL，补两个密钥占位符。
    param([string]$ConfigPath, [string]$BaseUrl)
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return 0 }
    $lines = @(Get-Content -LiteralPath $ConfigPath -Encoding UTF8)
    $changed = 0
    $inServer = $false
    $inEnv = $false
    $lastEnvIndex = -1
    $seenSecret = $false
    $seenSupervisor = $false
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
            if ($line -match '^\s{6}([A-Za-z_][\w]*)\s*:\s*(.*)$') {
                $key = $Matches[1]
                $val = $Matches[2].Trim()
                $lastEnvIndex = $i
                if ($key -eq "WATCH_API_BASE_URL" -and $val -match '^http://127\.0\.0\.1:\d+$' -and $val -ne $BaseUrl) {
                    $lines[$i] = "      WATCH_API_BASE_URL: $BaseUrl"
                    $changed++
                } elseif ($key -eq "WATCH_API_SHARED_SECRET") {
                    $seenSecret = $true
                    if ($val -ne '${WATCH_API_SHARED_SECRET}') {
                        $lines[$i] = "      WATCH_API_SHARED_SECRET: `${WATCH_API_SHARED_SECRET}"
                        $changed++
                    }
                } elseif ($key -eq "WATCH_API_SUPERVISOR_KEY") {
                    $seenSupervisor = $true
                    if ($val -ne '${WATCH_API_SUPERVISOR_KEY}') {
                        $lines[$i] = "      WATCH_API_SUPERVISOR_KEY: `${WATCH_API_SUPERVISOR_KEY}"
                        $changed++
                    }
                }
            } elseif ($line -match '^\s{0,4}\S') {
                $inEnv = $false
            }
        }
    }
    if ($lastEnvIndex -lt 0) { return $changed }
    $insert = @()
    if (-not $seenSecret) { $insert += "      WATCH_API_SHARED_SECRET: `${WATCH_API_SHARED_SECRET}" }
    if (-not $seenSupervisor) { $insert += "      WATCH_API_SUPERVISOR_KEY: `${WATCH_API_SUPERVISOR_KEY}" }
    if ($insert.Count -gt 0) {
        $at = $lastEnvIndex + 1
        $before = @($lines | Select-Object -First $at)
        $after = @()
        if ($at -lt $lines.Count) { $after = @($lines[$at..($lines.Count - 1)]) }
        $lines = @($before + $insert + $after)
        $changed += $insert.Count
    }
    if ($changed -gt 0 -and -not $DryRun) {
        Set-Content -LiteralPath $ConfigPath -Value $lines -Encoding UTF8
    }
    return $changed
}

# --- 定位项目与 Hermes --------------------------------------------------------
if (-not (Test-Path -LiteralPath $ProjectDir)) {
    throw "项目目录不存在：$ProjectDir（请用 -ProjectDir 指定目标机上的项目路径）"
}
if (-not $HermesHome) { $HermesHome = Join-Path $env:LOCALAPPDATA "hermes" }
if (-not (Test-Path -LiteralPath $HermesHome)) { throw "Hermes 目录不存在：$HermesHome" }

$BackendEnv = Join-Path $ProjectDir ".env"
$envMap = Read-DotEnv $BackendEnv
$port = $envMap["PAN_HOTEL_WATCH_BACKEND_PORT"]
if (-not $port) { $port = $envMap["BACKEND_PORT"] }
if (-not $port) { $port = "8100" }
$BaseUrl = "http://127.0.0.1:$port"
$ServiceToken = [string]$envMap["WATCH_API_SHARED_SECRET"]
$SupervisorKey = [string]$envMap["PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"]
$EnforceAuth = [string]$envMap["PAN_HOTEL_ENFORCE_API_AUTH"]

Write-Host "==== 诊断 ===="
Write-Host "后端 .env          : $BackendEnv"
Write-Host "后端端口           : $port （MCP 目标 $BaseUrl）"
Write-Host "服务令牌           : $(Mask-Secret $ServiceToken)"
Write-Host "Supervisor key     : $(Mask-Secret $SupervisorKey)"
Write-Host "ENFORCE_API_AUTH   : $EnforceAuth （-SetEnforceAuth 可调整，默认 keep）"
if (-not $ServiceToken) {
    throw "后端 .env 缺少 WATCH_API_SHARED_SECRET，无法继续（请先在后端配置该值）。"
}

# 后端健康检查（不通过则提示「连接不上」根因，但继续修复配置）
$HealthUrl = "$BaseUrl/api/v1/health"
$backendUp = $false
try {
    $resp = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5
    if ($resp.status -eq "ok") { $backendUp = $true }
} catch { }
if ($backendUp) {
    Write-Host "后端健康检查       : 正常（$HealthUrl）"
} else {
    Write-Warning "后端健康检查失败：$HealthUrl 不可达。这就是「无法连接后台 API」的直接原因——"
    Write-Warning "  请确认目标机后端已启动（scripts\start-all.bat）且端口与 .env 一致。配置修复继续。"
}

# --- A. 后端 ENFORCE 开关 ------------------------------------------------------
if ($SetEnforceAuth -ne "keep" -and $EnforceAuth -ne $SetEnforceAuth) {
    if ($DryRun) {
        Write-Host "[DryRun] 后端 PAN_HOTEL_ENFORCE_API_AUTH: $EnforceAuth -> $SetEnforceAuth"
    } else {
        $null = Update-DotEnvValue -Path $BackendEnv -Values @{ "PAN_HOTEL_ENFORCE_API_AUTH" = $SetEnforceAuth }
        Write-Host "后端 PAN_HOTEL_ENFORCE_API_AUTH: $EnforceAuth -> $SetEnforceAuth（重启后端后生效）"
    }
}

# --- B/C. Hermes 根 + 各 profile 的 .env 与 config.yaml ------------------------
$HermesExe = Join-Path $HermesHome "hermes-agent\venv\Scripts\hermes.exe"
$profileRoots = @()
if (Test-Path -LiteralPath (Join-Path $HermesHome "profiles")) {
    $profileRoots = @(Get-ChildItem -LiteralPath (Join-Path $HermesHome "profiles") -Directory | ForEach-Object { $_.FullName })
}
$targets = @($HermesHome) + $profileRoots
$feishuProfiles = @()
foreach ($root in $targets) {
    $name = if ($root -eq $HermesHome) { "(根/default)" } else { Split-Path $root -Leaf }
    $dotEnv = Join-Path $root ".env"
    $config = Join-Path $root "config.yaml"
    if (-not (Test-Path -LiteralPath $config)) { continue }

    # B. profile .env 密钥
    if (Test-Path -LiteralPath $dotEnv) {
        $before = Read-DotEnv $dotEnv
        $vals = @{ "WATCH_API_SHARED_SECRET" = $ServiceToken }
        if ($SupervisorKey) { $vals["WATCH_API_SUPERVISOR_KEY"] = $SupervisorKey }
        $changed = Update-DotEnvValue -Path $dotEnv -Values $vals
        if ($changed) {
            Write-Host ("[{0}] .env 已写入服务令牌 {1} -> {2}{3}" -f $name, (Mask-Secret ([string]$before["WATCH_API_SHARED_SECRET"])), (Mask-Secret $ServiceToken), $(if ($DryRun) { "（DryRun，未落盘）" } else { "" }))
        } else {
            Write-Host ("[{0}] .env 服务令牌已一致 {1}" -f $name, (Mask-Secret $ServiceToken))
        }
        if ($before.ContainsKey("FEISHU_APP_ID") -and $before["FEISHU_APP_ID"]) { $feishuProfiles += $name }
    }

    # C. config.yaml MCP env
    $cfgBefore = Get-Content -LiteralPath $config -Raw -Encoding UTF8
    $urlNow = $null
    if ($cfgBefore -match 'WATCH_API_BASE_URL:\s*(\S+)') { $urlNow = $Matches[1] }
    $n = Sync-McpConfig -ConfigPath $config -BaseUrl $BaseUrl
    if ($n -gt 0) {
        Write-Host ("[{0}] config.yaml 修正 {1} 处（BASE_URL: {2} -> {3}；密钥占位符补齐）{4}" -f $name, $n, $urlNow, $BaseUrl, $(if ($DryRun) { "（DryRun，未落盘）" } else { "" }))
    } else {
        Write-Host ("[{0}] config.yaml 已符合要求（端口 {1}，占位符齐全）" -f $name, $BaseUrl)
    }

    # 提示可疑 cwd（MCP 起不来的另一类根因）
    if ($cfgBefore -match '(?m)^\s{4}cwd:\s*(\S+)$') {
        $cwd = $Matches[1] -replace '/', '\'
        if (-not (Test-Path -LiteralPath $cwd)) {
            Write-Warning ("[{0}] MCP cwd 指向不存在的目录：{1}（若机器人完全无回复，需修正）" -f $name, $Matches[1])
        }
    }
}

# --- D. 重启 Gateway -----------------------------------------------------------
if ($DryRun) {
    Write-Host "[DryRun] 将重启飞书 profile 的 Gateway：$($feishuProfiles -join ', ')（除非 -NoGatewayRestart）"
} elseif (-not $NoGatewayRestart) {
    if (-not (Test-Path -LiteralPath $HermesExe)) {
        Write-Warning "未找到 hermes.exe（$HermesExe）；请手动重启各 Gateway 后再验证。"
    } else {
        foreach ($p in $feishuProfiles) {
            Write-Host "重启 Gateway：$p ..."
            $procArgs = if ($p -eq "(根/default)") { @("gateway", "run", "--replace") } else { @("-p", $p, "gateway", "run", "--replace") }
            Start-Process -FilePath $HermesExe -ArgumentList $procArgs -WindowStyle Hidden
            Start-Sleep -Seconds 2
        }
        Write-Host "Gateway 重启指令已发出（--replace 原地替换）。"
    }
} else {
    Write-Host "-NoGatewayRestart：跳过重启。配置已落盘，重启 Gateway 后生效。"
}

# --- 自检 ----------------------------------------------------------------------
Write-Host ""
Write-Host "==== 自检 ===="
if ($backendUp) {
# supervisor 凭据：GET /config/supervisor-overview（supervisor 鉴权保护）
    $supOk = $false
    try {
        $null = Invoke-RestMethod -Uri "$BaseUrl/api/v1/config/supervisor-overview" -Headers @{ "X-Pan-Hotel-API-Key" = $SupervisorKey } -TimeoutSec 10
        $supOk = $true
    } catch {
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        Write-Host ("supervisor 凭据自检：HTTP {0}（401=凭据与后端不一致；5xx=后端内部错误）" -f $code)
    }
    if ($supOk) { Write-Host "supervisor 凭据自检：通过（GET supervisor-overview 200）" }

    # 服务令牌（MCP 实际使用的路径）：POST 一个不存在的监控组 run-now。
    # 鉴权通过 → 404 not_found（无副作用：不启动浏览器、不推送）；鉴权失败 → 401。
    $probeCode = $null
    try {
        $null = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/ctrip-ebooking/data-center-watch-groups/__auth_probe__/run-now?push_to_feishu=false" -Headers @{ "X-Pan-Hotel-Service-Token" = $ServiceToken } -TimeoutSec 10
        $probeCode = 200
    } catch {
        if ($_.Exception.Response) { $probeCode = [int]$_.Exception.Response.StatusCode } else { $probeCode = -1 }
    }
    if ($probeCode -eq 404) {
        Write-Host "服务令牌自检：通过（探针组返回 404，说明鉴权已接受该令牌；MCP run-now 类调用将正常）"
    } elseif ($probeCode -eq 401) {
        Write-Warning "服务令牌自检：401 —— 令牌与后端不一致。请确认本脚本写入的令牌来自同一份后端 .env。"
    } else {
        Write-Host ("服务令牌自检：HTTP {0}（非 401/404，请人工复核）" -f $probeCode)
    }
} else {
    Write-Host "后端未运行，跳过在线自检（请先启动后端）。"
}
Write-Host ""
Write-Host "完成后请在飞书群 @机器人 验证："
Write-Host "  1. 发「盯盘状态」（读接口，无需令牌）    -> 应回正常状态卡片"
Write-Host "  2. 发「现刷竞品」/「日简报」（run-now 类） -> 修复前 401，修复后应正常执行"
Write-Host "  3. 若修改了 ENFORCE 或重启了后端，请先运行 scripts\restart-backend.ps1"
if ($DryRun) { Write-Host ""; Write-Host "（DryRun 演练完成，以上未实际修改）" }
