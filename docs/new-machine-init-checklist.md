# 新机器初始化清单

> 目标：在一台干净的 Windows 主机上，把 Pan Hotel Watch（后端 + worker + 前端 + Hermes
> Gateway 多 profile）跑起来，并保证「改了仓库源码 → 重启即生效」。
> 主机准备、`.venv` 创建、前端构建的细节见 `docs/windows-local-deploy.md`；本文只列
> **凭据与配置的单一来源**流程，避免多份 `.env` / config 散落。

## 单一来源原则（先读）

| 配置 | 唯一来源 | 仓库内形态 |
|------|----------|------------|
| 后端凭据 | 仓库根 `.env` | `.env.example`（tracked 模板）；`.env`（gitignore，真实值） |
| Hermes 飞书凭据 | `pan-hotel-hermes/config/feishu-profiles.yaml`（manifest） | `feishu-profiles.example.yaml`（tracked 模板）；`feishu-profiles.yaml`（gitignore，真实值） |
| 每个 profile 的 install `.env` | **由 `feishu_profiles.py apply` 生成**，不要手写 | 不进仓库，运行时生成到 `%LOCALAPPDATA%\hermes\[profiles\X]\.env` |
| 插件 / Skill 源码 | 仓库 `pan-hotel-hermes/hermes_plugin/...` 与 `.../skills/...` | install 目录由 `register-hermes.ps1` 软链到仓库源（V4-Step2） |

**铁律**：install 目录下的 `.env` 一律由 `feishu_profiles.py apply` 生成；`.example` 模板必须
tracked，真实配置文件必须 gitignore。不要在 install 目录手改 `.env`，会被下次 apply 覆盖。

## 0. 前置

- 已按 `docs/windows-local-deploy.md` §1 准备主机（Python 3.11、Node 20、Windows Developer
  Mode 用于软链接，或具备管理员权限）。
- 已 `git clone` 本仓库到固定路径（路径会写进 Hermes `config.yaml` 与软链接 target，换路径后需重跑 `register-hermes.ps1`）。
- 已安装 Hermes（`%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe` 存在）。

## 1. 安装依赖

```bat
scripts\install.bat
```

- 创建根 `.venv`（后端 + worker + Playwright）与 `pan-hotel-hermes\.venv`（MCP）。
- V4 起 `pan-hotel-hermes` 通过 `pip install -e .` 安装（`pyproject.toml` 为依赖唯一来源）。
- 若 `.env` 不存在，会从 `.env.example` 复制一份空模板。

## 2. 填后端凭据（仓库根 `.env`）

编辑仓库根 `.env`，至少填：

- 数据库 / 后端运行参数（见 `.env.example` 注释）。
- `WATCH_API_SHARED_SECRET=`（worker / Hermes 调用 operator 级 API 的共享密钥；空则降级为 API key 鉴权）。
- `FEISHU_APP_ID=` / `FEISHU_APP_SECRET=` 等（若后端直接处理飞书事件；Hermes 模式下多数由 manifest 接管）。

> 飞书 **多 profile** 凭据不写根 `.env`，走第 3 步 manifest。

## 3. 填飞书多 profile manifest

```powershell
Copy-Item .\pan-hotel-hermes\config\feishu-profiles.example.yaml `
          .\pan-hotel-hermes\config\feishu-profiles.yaml
```

编辑 `pan-hotel-hermes\config\feishu-profiles.yaml`，为每个 profile 填：

- `app_id` / `app_secret`（飞书自建应用凭证）
- `group_rules[].chat_id`（运营问答上下文群的 `oc_` 开头 ID）与 `policy`（open / allowlist / disabled）
- `allowed_users[]`（allowlist 时的 `ou_` 开头 open_id）
- `display_name`、`soul`（指向 `config/profiles/<key>/SOUL.md`）

> 真实 manifest 已 gitignore；模板 `feishu-profiles.example.yaml` tracked。**不要把真实密钥提交进仓库。**

校验 manifest（`--manifest` / `--hermes-root` 是父级参数，必须在子命令之前；
`--hermes-root` 传 **Hermes 项目根** `pan-hotel-hermes`，用于解析 manifest 里的 `soul:` 相对路径）：

```powershell
.\pan-hotel-hermes\.venv\Scripts\python.exe .\pan-hotel-hermes\scripts\feishu_profiles.py `
  --manifest .\pan-hotel-hermes\config\feishu-profiles.yaml `
  --hermes-root .\pan-hotel-hermes validate
```

## 4. 把 manifest 凭据映射进后端 `.env`

```powershell
.\pan-hotel-hermes\.venv\Scripts\python.exe .\pan-hotel-hermes\scripts\feishu_profiles.py `
  --manifest .\pan-hotel-hermes\config\feishu-profiles.yaml `
  --hermes-root .\pan-hotel-hermes write-backend-env `
  --dotenv .\.env
```

这会把 manifest 里的 `FEISHU_APP_CREDENTIALS_JSON` 等映射写进根 `.env`，是后端读取飞书凭据的**唯一入口**。

## 5. 为每个 profile 生成 install `.env`（apply）

对 manifest 里的每个 profile key（如 `jingli-ai` / `manxin-ai` / `quanji-ai`）执行：

```powershell
.\pan-hotel-hermes\.venv\Scripts\python.exe .\pan-hotel-hermes\scripts\feishu_profiles.py `
  --manifest .\pan-hotel-hermes\config\feishu-profiles.yaml `
  --hermes-root .\pan-hotel-hermes apply `
  --profile jingli-ai `
  --profile-root $env:LOCALAPPDATA\hermes\profiles\jingli-ai `
  --backend-dotenv .\.env
```

`--hermes-root` 传 Hermes 项目根（解析 `soul:` 路径）；`--profile-root` 传该 profile 的
install 根（`.env` 生成目标）。`apply` 是 install 目录 `.env` 的**唯一生成入口**；它会读取
manifest + 后端 service token，写出该 profile 运行时所需的 `.env`。

> 也可以用封装脚本 `pan-hotel-hermes\scripts\setup-feishu-gateway.ps1 -Profile <key> -AppId ... -AppSecret ...`，它内部调 `apply` 并可附带启动 gateway。

## 6. 注册 Hermes profile（软链 + editable install + config.yaml）

对每个 profile 执行（`default` 即全局）：

```powershell
.\pan-hotel-hermes\scripts\register-hermes.ps1 -Profile jingli-ai -SkipModelHint
.\pan-hotel-hermes\scripts\register-hermes.ps1 -Profile manxin-ai -SkipModelHint
.\pan-hotel-hermes\scripts\register-hermes.ps1 -Profile quanji-ai -SkipModelHint
.\pan-hotel-hermes\scripts\register-hermes.ps1 -Profile default -SkipModelHint
```

每个 profile 会：

1. 向 Hermes agent venv 执行 `pip install -e pan-hotel-hermes`（V4-Step1，取代手写 `.pth`）。
2. 把该 profile 的 `skills\pan-hotel-watch` 与 `plugins\pan-hotel-feishu-session` **软链**到仓库源（V4-Step2；软链失败自动降级为拷贝 + 启动前 hash 校验）。
3. 写 / 同步 `config.yaml`（MCP command、超时、插件、display、compression）与 `SOUL.md`、记忆模板。

软链后，仓库内改插件 / Skill 源码，只需重启对应 profile 的 Gateway 即生效。

## 7. 启动服务 + 重启 Gateway

```bat
scripts\start-all.bat
```

或分进程：`run-backend.bat` + `run-worker.bat` + 前端（见 `docs/windows-local-deploy.md` §4）。

重启各 Hermes Gateway（让软链后的源码与新生成的 `.env` 生效）：

```powershell
.\pan-hotel-hermes\scripts\register-hermes.ps1 -Profile jingli-ai -RestartGateway -SkipModelHint
```

## 8. 自检

- `Invoke-RestMethod http://127.0.0.1:8100/api/v1/health` 后端健康。
- 各 profile install 目录是软链：
  `Get-Item $env:LOCALAPPDATA\hermes\profiles\jingli-ai\plugins\pan-hotel-feishu-session | Select LinkType,Target`
- `hermes -p jingli-ai /mcp list` 能看到 `pan-hotel-watch` 工具。
- 飞书群里 @机器人 能收到「运营助手」卡片回复。

## 9. 常见偏离（禁止）

- 不要手改 install 目录的 `.env` —— 以 `apply` 为准。
- 不要在 `register-hermes.ps1` 之外手动拷贝插件 / Skill 到 install 目录 —— 会与软链冲突。
- 不要把 `feishu-profiles.yaml`、`.env`、真实 `app_secret` / `chat_id` 提交进仓库。
- 不要在手写 `.pth` 里硬编码路径加载 `pan_hotel_mcp` —— V4 起统一 `pip install -e .`。
