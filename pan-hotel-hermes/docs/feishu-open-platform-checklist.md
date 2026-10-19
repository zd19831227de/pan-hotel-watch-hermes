# 飞书开放平台配置清单（Hermes Gateway）

> 用于 Hermes 接管飞书入口后，与 pan-hotel-watch 后端审批链配合。  
> **现行 Profile 以** [docs/hermes-profiles-current.md](../../docs/hermes-profiles-current.md) **为准**（2026-07-22：曼馨 `manxin-ai` + 静里 `jingli-ai`）。  
> 官方参考：[Feishu / Lark | Hermes Agent](https://github.com/NousResearch/hermes-agent)

## 当前生产架构：双 Profile / 双飞书应用

曼馨和静里必须使用不同飞书自建应用、不同 Hermes profile、不同 `HERMES_HOME`。底层 Pan Hotel Watch backend、worker、SQLite 仍然共用，业务数据和调价权限由后端按 `chat_id` 绑定与 EBK `connection_mode` 决定，不能由 Hermes 记忆推断。

| 门店 | Hermes profile | Feishu app | 群 chat_id | 调价边界 |
|------|----------------|------------|------------|----------|
| 曼馨 | `manxin-ai` | `cli_a22222222222222`（原泉吉应用，已改绑曼馨） | `oc_11111111111111111111111111111111` | `direct`，只读与建议，不能自动写价 |
| 静里 | `jingli-ai` | 由静里自建应用本地配置 | `oc_44444444444444444444444444444444` | `non_direct`，仍必须走目标价输入、飞书审批、preflight、dry-run、execute |

> 已废弃：`quanji-ai` + 泉吉群 `oc_33333333333333333333333333333333` 不再作为生产入口。

本地配置示例（不要把 App Secret 写入文档、代码、测试或聊天输出）：

```powershell
# 曼馨：从旧泉吉 profile 迁凭证并注册（推荐）
.\pan-hotel-hermes\scripts\setup-manxin-hermes.ps1

# 或手动
hermes profile create manxin-ai --clone
hermes profile create jingli-ai --clone
.\pan-hotel-hermes\scripts\register-hermes.ps1 -Profile manxin-ai -ResetMemory -SkipModelHint
.\pan-hotel-hermes\scripts\register-hermes.ps1 -Profile jingli-ai -ResetMemory -SkipModelHint

$manxinSecret = Read-Host "Manxin Feishu App Secret" -AsSecureString
.\pan-hotel-hermes\scripts\setup-feishu-gateway.ps1 `
  -Profile manxin-ai `
  -AppId "cli_a22222222222222" `
  -AppSecret $manxinSecret `
  -AllowedChats "oc_11111111111111111111111111111111"

$jingliAppId = Read-Host "Jingli Feishu App ID"
$jingliSecret = Read-Host "Jingli Feishu App Secret" -AsSecureString
.\pan-hotel-hermes\scripts\setup-feishu-gateway.ps1 `
  -Profile jingli-ai `
  -AppId $jingliAppId `
  -AppSecret $jingliSecret `
  -AllowedChats "oc_44444444444444444444444444444444"

.\pan-hotel-hermes\scripts\start-hermes-gateways.ps1 -Profiles manxin-ai,jingli-ai -Replace
.\scripts\status.ps1 -HermesGatewayProfiles manxin-ai,jingli-ai
```

后端 `.env` 需要配置按群路由的本地私有值：

```env
FEISHU_CHAT_APP_MAP_JSON=
FEISHU_APP_CREDENTIALS_JSON=
```

在服务器本机生成 JSON，并直接写入未纳入版本管理的后端 `.env`。脚本只打印更新结果，不打印 secret：

```powershell
$manxinSecret = Read-Host "Manxin Feishu App Secret" -AsSecureString
$jingliAppId = Read-Host "Jingli Feishu App ID"
$jingliSecret = Read-Host "Jingli Feishu App Secret" -AsSecureString
$backendEnv = Join-Path (Get-Location) ".env"

function Convert-SecretToPlainText {
    param([securestring]$Secret)
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Set-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )
    $line = "$Key=$Value"
    if (Test-Path $Path) {
        $lines = Get-Content -LiteralPath $Path
        $matched = $false
        $updated = foreach ($existing in $lines) {
            if ($existing -match "^$([regex]::Escape($Key))=") {
                $matched = $true
                $line
            } else {
                $existing
            }
        }
        if (-not $matched) {
            $updated += $line
        }
        Set-Content -LiteralPath $Path -Value $updated -Encoding UTF8
    } else {
        Set-Content -LiteralPath $Path -Value @($line) -Encoding UTF8
    }
}

$chatMap = @{
    "oc_11111111111111111111111111111111" = "manxin-ai"
    "oc_44444444444444444444444444444444" = "jingli-ai"
} | ConvertTo-Json -Compress

$credentialMap = @{
    "manxin-ai" = @{
        app_id = "cli_a22222222222222"
        app_secret = Convert-SecretToPlainText $manxinSecret
    }
    "jingli-ai" = @{
        app_id = $jingliAppId
        app_secret = Convert-SecretToPlainText $jingliSecret
    }
} | ConvertTo-Json -Compress

Set-DotEnvValue -Path $backendEnv -Key "FEISHU_CHAT_APP_MAP_JSON" -Value $chatMap
Set-DotEnvValue -Path $backendEnv -Key "FEISHU_APP_CREDENTIALS_JSON" -Value $credentialMap
Write-Host "Updated local backend .env Feishu routing keys. Secrets were not printed."
```

## 1. 应用类型与凭证

- [ ] 创建或复用 **企业自建应用**（建议灰度阶段用**第二个应用**，与旧 `feishu-listener` 并存测试）
- [ ] 记录 **App ID**（`cli_xxx`）→ 填入对应 profile 的 `%LOCALAPPDATA%\hermes\profiles\<profile>\.env`，不要写回默认 Hermes home
- [ ] 记录 **App Secret** → `FEISHU_APP_SECRET`
- [ ] 设置 `FEISHU_DOMAIN=feishu`（飞书国内；Lark 国际填 `lark`）

## 2. 机器人能力

- [ ] **应用能力 → 机器人**：启用机器人
- [ ] 设置机器人名称、描述、头像（运营可识别即可）
- [ ] 将机器人 **添加到目标飞书群**（群设置 → 群机器人 → 添加）

## 3. 连接方式（推荐 WebSocket）

Hermes 侧执行 `hermes gateway setup`，选择 **WebSocket 长连接**：

- [ ] `FEISHU_CONNECTION_MODE=websocket`
- [ ] **无需**配置公网回调 URL（适合单机 Windows 部署）
- [ ] 启动后执行 `.\scripts\status.ps1 -HermesGatewayProfiles manxin-ai,jingli-ai` 确认两个命名 Gateway 均已连接
- [ ] 曼馨应用建议开通 `im:chat:readonly`（消除群信息 warn；不阻断核心回复）

> 若仍使用 pan-hotel-watch 的 HTTP 回调模式，会与 Hermes 冲突；切换 Hermes 后必须停 `feishu-listener`。

## 4. 事件订阅

在飞书开放平台 **事件订阅** 中勾选（Hermes Gateway 文档要求的最小集）：

| 事件 | 用途 |
|------|------|
| [ ] `im.message.receive_v1` | 群 @机器人 / 私聊消息 → Hermes 意图理解或转发 watch |
| [ ] 卡片动作事件（`card.action.trigger` 等，以 Hermes 向导提示为准） | 审批卡「通过 / 拒绝 / 自定义调价」→ `dispatch_watch_feishu_event` → 后端 |

**权限相关**（事件订阅页会提示开通）：

- [ ] `im:message` — 读取消息
- [ ] `im:message.group_at_msg` — 接收群 @ 消息
- [ ] `im:message.p2p_msg` — 接收私聊（若需要）
- [ ] 发送消息相关权限（回复文本 / 卡片，按 Hermes 向导补齐）

发布应用版本后，在 **版本管理与发布** 中 **申请发布** 并让企业管理员审核通过。

## 5. Hermes 白名单（强烈建议）

在对应 profile 的 `%LOCALAPPDATA%\hermes\profiles\<profile>\.env` 或 Gateway 配置中：

```env
FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
# 必填：双 Profile 模式下每个 profile 只允许自己的群
# FEISHU_ALLOWED_CHATS=oc_xxx
```

- [ ] 仅添加运营人员的 `open_id`（`ou_` 前缀）
- [ ] 每个 profile 只配置自己的目标群 `chat_id`（`oc_` 前缀），不要把曼馨与静里写进同一个 profile

## 6. 与 pan-hotel-watch 后端的配合

| 组件 | Hermes 接管后 |
|------|----------------|
| 后端 API (`http://127.0.0.1:8010`) | **继续运行** |
| 定时轮询 Worker (`scheduler`) | **继续运行** |
| `feishu-listener` Worker | **必须停止**（避免双监听同一应用） |
| 调价审批 / 执行管线 | **不变**，仍由后端 `POST /api/v1/feishu/events` 处理 |

停掉 watch 侧飞书监听：

```powershell
# 若 feishu-listener 在独立终端运行，直接 Ctrl+C 关闭该窗口
# 或通过你平时的 worker 管理方式停止 scripts/run-worker.ps1 -Service feishu-listener
```

## 7. 端到端验收

> 完整回归表（含 Phase 2–3 全场景、Bug 模板、准出标准）：[docs/hermes-e2e-acceptance-checklist.md](../../docs/hermes-e2e-acceptance-checklist.md)

- [ ] `hermes` → `/mcp list` 可见 **pan-hotel-watch**（工具集合以 `tests/test_mcp_tool_contract.py` 为准，当前约 24 个）
- [ ] `.\pan-hotel-hermes\scripts\start-hermes-gateways.ps1 -Profiles manxin-ai,jingli-ai -Replace` + `.\scripts\status.ps1 -HermesGatewayProfiles manxin-ai,jingli-ai` 正常
- [ ] 飞书群 `@机器人 盯盘状态` → 返回调度/服务状态
- [ ] `@机器人 今天出租率多少` → 调用 `read_ebk_datasets`（按需现读 EBK 数据集，不推送）
- [ ] 曼馨：`@曼馨 ai 管家 美团/飞猪/三端联合简报` → `get_ebk_joint_analysis_context`，短综合分析非长表
- [ ] `@机器人 竞品最新价` → 竞品只读工具；不跑写价
- [ ] `@机器人 最近三天竞品趋势` → 调用 `get_competitor_price_trend`（趋势卡，优先折线图）
- [ ] `@机器人 上周简报` → 调用 `get_weekly_operator_briefing`（按需周报）
- [ ] 静里：`@机器人 把今天高级大床房调到420` → 后端返回 **审批卡**（非 Hermes 自由生成）
- [ ] 点审批卡 **通过** → 卡片回调经 Hermes 转发 → 后端执行管线 → 结果卡
- [ ] `@机器人 调价建议` → 说明由竞对轮询自动触发，不现场评估
- [ ] 曼馨直连：不出现自动改价承诺

## 8. 回滚

- 停两个命名 Hermes Gateway
- 重新启动 `scripts/run-worker.ps1 -Service feishu-listener`
- 飞书应用回调改回 watch 侧（若曾改 HTTP 回调）

后端 `POST /api/v1/feishu/events` 保留，无需改代码即可回滚。
