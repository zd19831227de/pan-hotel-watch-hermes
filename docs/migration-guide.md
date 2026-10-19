# Pan Hotel Watch 跨机器迁移指南

> **2026-09-17 更新**（对齐 `scripts\migrate-export.ps1` / `migrate-import.ps1`
> 至 commit `33df945`；同日全局代码复查后两侧模块自检清单补齐并完全对齐，
> 见文末"脚本变更记录"）。本流程已在 Hyper-V `MigrationTest` 虚拟机上完成全流程
> 实测：9 个 Step 全部通过，后端健康检查 `{"status":"ok"}`、前端 5173 / 后端
> 8100 均可从外部访问。8-19 实测命中的 3 处脚本健壮性问题已修复进
> `scripts\migrate-import.ps1`（见文末"脚本变更记录"）；9-16 又补 3 处：V6/V7
> 新模块打包自检、`-Force` 模式容忍 robocopy 锁定文件 8 位退出码、自检
> `hermes … mcp list` 去掉斜杠前缀（原 `/mcp list` 在 CLI 下 exit 2）。

适用场景：把公司主机上的整套系统（项目代码 + 数据库 + Hermes 安装 + 飞书配置）
迁移到一台新的 Windows 10/11 机器，部署完成后只需重新登录 OTA 平台浏览器账号。

整套迁移由两个脚本完成：

| 脚本 | 运行位置 | 作用 |
|------|---------|------|
| `scripts\migrate-export.ps1` | 公司主机（源机） | 打包项目本体 + Hermes 安装目录 → 一个 7z 迁移包 + `migrate-boot\` 引导包 |
| `scripts\migrate-import.ps1` | 新机器（目标机） | 解压 + 自动装依赖 + 重建 venv + 配置 + 启动 + 自检（9 个 Step） |

迁移包结构：

```
output\
├─ pan-hotel-watch-migrate-{时间戳}.7z   主包（约 1.6-3GB）
│   ├─ project\                 项目本体（源码、.env、数据库、.runtime 浏览器登录态、
│   │                           manifest；已排除 .git、缓存、日志等垃圾）
│   ├─ hermes\                  %LOCALAPPDATA%\hermes 整目录（hermes-agent 仓库含 venv、
│   │                           profiles、auth.json、根 .env / config.yaml、
│   │                           gateway-service、hooks、plugins、memories、bin）
│   └─ migration-manifest.json  源机环境信息（Python/Node 版本与路径、profiles 清单）
└─ migrate-boot\                引导包（免 7-Zip/winget 也能部署）
    ├─ migrate-import.ps1       部署脚本（export 自动从 scripts\ 同步，含全部修复）
    ├─ 7z.exe / 7z.dll          免安装解压工具
    └─ installers\              离线安装件（-IncludeInstallers 时）：Python 3.11、
                                Node 20 zip、uv.exe、playwright-browsers（chromium 1148）
```

已排除的纯垃圾/可再生目录：项目 `.git`（可加 `-IncludeGit` 打开）、`__pycache__`、
`.runtime` 的 logs/pids/backups、`output\`、browser-state 的 `Cache`/`Code Cache`/
`GPUCache`/`OptGuideOnDeviceModel`（美团端上模型，约 3.98GB，新机器自动重建）、
`backend\pan_hotel_watch.db`（0 字节陈旧库，真库在仓库根）、
`.gitnexus`（索引可 `npx gitnexus analyze` 重建）、`.playwright-cli`、
Hermes 的 `audio_cache`/`image_cache`/`cache`/`logs`/`sessions`/`sandboxes`、
`*.pid`/`*.lock`/`state.db-wal`/`state.db-shm`。

**V6/V7 新模块打包自检**（2026-09-16 `756fc4b` 起）：export 侧维护一份
`$V6v7RequiredFiles` 清单（蒸馏/QA 卡片/页面变更告警/chat 浏览器/全局推送/周期读取/
评价/退款/预约/金字塔/促销/新 worker/pan_hotel_mcp/skill 参考文档/业务版话术表
等代表文件），robocopy 全量拷贝后**强制核对**这些文件是否进入 staging，任一缺失即
抛错终止（防止未来排除规则误伤关键新模块）。import 侧有对应 `$V6v7ProbeFiles`
落盘核对（老版本迁移包缺这些文件时只警告不阻断）。新增模块时把代表文件加进两份清单。

---

## 一、打包（在公司主机上，一条命令）

```powershell
cd "D:\work\pan-hotel-watch"

# 建议先演练看清单（不实际拷贝、不压缩）：
.\scripts\migrate-export.ps1 -DryRun

# 正式打包（自动停后端/worker/Gateway/Chrome/隧道进程，带离线安装件）：
.\scripts\migrate-export.ps1 -StopProcesses -IncludeInstallers
```

- 产物：`output\pan-hotel-watch-migrate-{时间戳}.7z` + `output\migrate-boot\`。
- `-IncludeInstallers` 强烈推荐：目标机无 winget/无外网也能装齐 Python/Node/uv/Chromium
  （实测 npmmirror 缺 headless-shell build 1148，离线件可跳过该下载）。
- 前置：源机需安装 7-Zip（`winget install 7zip.7zip`；PowerShell 自带 Compress-Archive
  有 2GB 单文件限制，本包必败）。

| 参数 | 说明 |
|------|------|
| `-StopProcesses` | 打包前自动停相关进程（推荐，否则登录态文件可能被锁） |
| `-IncludeInstallers` | 附带 Python/Node/uv/playwright 离线安装件（推荐） |
| `-IncludeGit` | 连同项目 `.git` 历史一起打包（默认不带） |
| `-OutputDir D:\backup` | 指定输出目录（默认 `output\`） |
| `-Force` | 进程停不掉也强行打包；2026-09-16 `1b0927c` 起容忍 robocopy 8 位退出码（运行中 Chrome 锁定的 Cookies/Sessions 等跳过，数量见日志统计，降级为告警不终止）；登录态可能不完整，仅紧急时用 |

---

## 二、传输（务必加密）

> **迁移包内含飞书 App 密钥、DeepSeek 等 API key、auth.json、tunnel-credentials.txt、
> 浏览器登录态等敏感数据。禁止明文走网络（公网网盘、微信/QQ、开放共享均不可）。**

可选方式：

1. **加密移动硬盘 / U 盘拷贝**（最简单）。
2. **加密压缩后再传输**：`7z a -p -mhe=on 包.7z.encrypted 包.7z`，密码另渠道告知。
3. **加密通道**：VPN / SSH（scp/sftp）直连。
4. **Hyper-V 场景**：`Copy-VMFile`（PSDirect 可用时）或**离线 VHDX 注入**（目标机
   关机后挂载其虚拟盘，主机本地磁盘速度秒级拷入 1.6GB；PSDirect 断连/无头时的
   首选，见第五节）。

---

## 三、部署 · 方式 A：交互式（正常机器，推荐）

前提：能在目标机控制台登录一个管理员账户。

```powershell
# 1. 把 .7z 与 migrate-boot\ 目录放到目标机（如 C:\migrate\）
# 2. 抽查包完整性（可选）：
C:\migrate\migrate-boot\7z.exe t C:\migrate\pan-hotel-watch-migrate.7z

# 3. 一条命令部署：
powershell -ExecutionPolicy Bypass -File C:\migrate\migrate-boot\migrate-import.ps1 `
    -Package C:\migrate\pan-hotel-watch-migrate.7z `
    -ProjectDir C:\pan-hotel-watch-hermes -Force
```

脚本自动完成 9 个步骤：

| 步骤 | 内容 |
|------|------|
| Step 0 | 检查并自动安装 Python 3.11 / py 启动器（实测 `py -3`）/ Node 20 / npm / uv / Git / 7-Zip（优先 migrate-boot\installers 离线件 → 镜像直连 → winget 兜底） |
| Step 1 | 7z 解压迁移包，项目落到 `-ProjectDir`，Hermes 落到 `%LOCALAPPDATA%\hermes` |
| Step 2 | 删打包带来的 venv 并重建：`scripts\install.bat`（双 venv + pip + npm install + 前端构建 + chromium）；Hermes agent 用 `uv venv venv --seed --python 3.11` + `uv sync --extra all --extra feishu --extra edge-tts`（目录名必须是 `venv`，启动链全部硬编码该路径）；随后 `uv pip install -e` 确保 hermes CLI 可运行 |
| Step 3 | Node 依赖（install.bat 已含，自动跳过） |
| Step 4 | 设置用户级 `HERMES_HOME`，把 Hermes venv 与项目 scripts 加入 PATH |
| Step 5 | 逐 profile 运行 `register-hermes.ps1`（skill/插件以 **Junction** 链接到仓库），修正 config.yaml 中 MCP 的 `cwd`/`PYTHONPATH`/`command`，并补 MCP env 密钥占位符（profiles + 根 config.yaml） |
| Step 6 | 用 Hermes venv 的 Python 运行 `feishu_profiles.py`：validate → write-backend-env（保留源机 `PAN_HOTEL_ENFORCE_API_AUTH`）→ apply；随后为全部 profiles + 根 `.env` 补齐 `WATCH_API_SHARED_SECRET` / `WATCH_API_SUPERVISOR_KEY` |
| Step 7 | 运行 `scripts\start-all.bat`（自动 `pause` 免交互）启动后端 + worker + 前端 + Gateway |
| Step 8 | 自检：后端 `/api/v1/health`、`hermes -p <profile> mcp list`（**注意：CLI 下不带斜杠前缀**，`/mcp list` 是网关内斜杠命令、CLI 下 exit 2，2026-09-16 `33df945` 已修）、profile 链接（Junction/软链）状态、主库与 `.env` 落盘、开机自启计划任务、防火墙入站规则（尽力而为，失败仅告警） |
| Step 9 | 输出登录提示 |

全程约 20-60 分钟（离线件 + 国内镜像时较快），日志写入
`<ProjectDir>\migration-{时间戳}.log`。任何一步失败即停并给出明确错误；修复后重跑，
目录已存在时加 `-Force`。

| 参数 | 说明 |
|------|------|
| `-ProjectDir` | 项目安装路径。**默认与源机一致，推荐保持一致**（不同路径脚本会修正 MCP 路径，但保持一致最稳） |
| `-SkipEnvCheck` | 环境已就绪时跳过 Step 0 |
| `-SkipRebuild` | 调试用：跳过 venv/node_modules 重建 |
| `-Force` | 目标目录已有内容时强制覆盖合并（重跑部署时用） |
| `-DryRun` | 只打印步骤不实际执行 |
| `-NoChinaMirror` | 海外机器用官方源（默认走清华 PyPI / npmmirror 国内镜像） |

---

## 四、部署 · 方式 B：无头 / 非交互（计划任务、PSDirect 失效的机器）

实测场景：目标机无法真正完成交互登录（autologon 只到壁纸、Startup 不触发、
PSDirect 报凭据无效、桌面键盘不可靠）。此时唯一可靠通道是 **SYSTEM 计划任务**。

### 前置检查（重要，都是实测踩过的坑）

1. **目标用户账户必须真实存在**：`net user <用户>` 能查到。sakura1 曾"看起来能登录"
   实际不在 SAM 里，导致 Step 6 icacls 全部报 1332。
2. **Hyper-V 快照还原参数名**：部分 Hyper-V 模块（v2.0）`Restore-VMSnapshot` 只认
   `-Name`，不认 `-VMSnapshotName`——参数绑定失败**可能被吞掉**，务必用 uptime 归零
   验证还原真的发生了。

### 部署步骤

```powershell
# --- 1. 传文件（PSDirect 可用走 Copy-VMFile；不可用走离线 VHDX 注入）---
# 离线注入（目标机关机状态，主机执行）：
$avhdx = "F:\HyperV\Machines\<VM>\xxx.avhdx"
Mount-VHD -Path $avhdx -Passthru            # 挂为 G:
robocopy "E:\...\output\migrate-boot" "G:\migrate\migrate-boot" /E /NFL /NDL /NP
Copy-Item "E:\...\output\pan-hotel-watch-migrate-<ts>.7z" "G:\migrate\" -Force
#   写入 run-migration.cmd（模板见下）并授权：
icacls "G:\migrate" /grant "*S-1-5-11:(OI)(CI)F" /T /C
Dismount-VHD -Path $avhdx

# --- 2. 确保账户存在（sethc 后门或任何 SYSTEM 上下文）---
net user <用户> <密码> /add
net localgroup Administrators <用户> /add

# --- 3. 注册 SYSTEM 计划任务并触发 ---
schtasks /create /tn PanHotelMigrate /tr C:\migrate\run-migration.cmd /sc onstart /ru SYSTEM /f
schtasks /run /tn PanHotelMigrate
#    /ru SYSTEM：无需 batch-logon 权限、无需密码；/sc onstart：重启自动续跑

# --- 4. 监控与验证（无 PSDirect 时）---
#    CPU 趋势：持续>0 = 在跑（解压/安装是磁盘 IO 密集，CPU 可能仅 5-24%）
(Get-VM -Name <VM> | Select-Object Uptime,CPUUsage)
#    定论性验证必须关机刷盘后离线读（VM 运行时挂载 live 盘被拒 0x80070020）：
Stop-VM -Name <VM> -Force
Mount-VHD -Path $avhdx -ReadOnly -Passthru | Out-Null
Get-Content "G:\migrate\migration.log" -Tail 30
Get-Content "G:\migrate\migration-exit.txt"          # EXIT=0 = 成功
Dismount-VHD -Path $avhdx
```

**run-migration.cmd 模板**（方式 B 的关键件：给 SYSTEM 进程设目标用户的路径环境）：

```cmd
@echo off
rem == 让 SYSTEM 进程使用目标用户的路径（hermes 装到用户 profile）==
set USERPROFILE=C:\Users\<用户>
set LOCALAPPDATA=C:\Users\<用户>\AppData\Local
set APPDATA=C:\Users\<用户>\AppData\Roaming
set HOMEDRIVE=C:
set HOMEPATH=\Users\<用户>
set USERNAME=<用户>
rem == 必须用真实计算机名（regquery HKLM\SYSTEM...\ComputerName），否则 Step6 icacls 1332 ==
set USERDOMAIN=<计算机名>
set COMPUTERNAME=<计算机名>
set HERMES_HOME=C:\Users\<用户>\AppData\Local\hermes

echo [%date% %time%] RUN_START > C:\migrate\run-debug.log
rem == skip 守卫：成功后开机只起服务不重跑迁移 ==
findstr /C:"EXIT=0" C:\migrate\migration-exit.txt >nul 2>&1
if %errorlevel%==0 (
  echo [%date% %time%] starting_services >> C:\migrate\run-debug.log
  cmd /c "C:\pan-hotel-watch-hermes\scripts\start-all.bat" < nul
  exit /b 0
)
cd /d C:\migrate\migrate-boot
set PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.playwright.dev
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\migrate\migrate-boot\migrate-import.ps1" -Package "C:\migrate\pan-hotel-watch-migrate-<ts>.7z" -ProjectDir "C:\pan-hotel-watch-hermes" -Force > C:\migrate\migration.log 2>&1
set PSEXIT=%errorlevel%
echo [%date% %time%] ps_exit=%PSEXIT% >> C:\migrate\run-debug.log
rem == 注意：EXIT=0 后面必须有空格再重定向，否则 0 会被当成 "0>"(stdin 重定向)吃掉 ==
echo EXIT=%PSEXIT% > C:\migrate\migration-exit.txt
```

### 方式 B 的收尾（SYSTEM 运行带来的差异）

1. **补设目标用户的环境变量**：Step 4 的用户级 `HERMES_HOME`/PATH 写进了 **SYSTEM 的
   HKCU**（`HKEY_USERS\S-1-5-19`），目标用户登录后并没有。离线补（目标机关机）：
   ```powershell
   reg load HKLM\OFFLINE_USER "G:\Users\<用户>\NTUSER.DAT"
   reg add "HKLM\OFFLINE_USER\Environment" /v HERMES_HOME /t REG_SZ /d "C:\Users\<用户>\AppData\Local\hermes" /f
   #   Path：先 reg query 读原值，把 hermes venv\Scripts 与项目 scripts 追加后写回
   reg unload HKLM\OFFLINE_USER
   ```
2. **开放访问端口**（如需从别的机器访问操作台）：
   `netsh advfirewall firewall add rule name="PanHotel-Backend-8100" dir=in protocol=TCP localport=8100 action=allow`（5173 同理）。
3. **清理临时后门**：sethc.exe 若被换成过 cmd.exe，务必从基础盘拷回原文件；autologon
   用完即禁（`AutoAdminLogon=0` 并删除 `DefaultPassword`）。

---

## 五、部署后操作

1. **登录 OTA 浏览器账号**（登录态是 DPAPI 加密的，跨机器/跨 Windows 用户无法解密，必须重登）：
   携程 EBK / 美团 EBK / 飞猪 EBK：打开系统操作台 →「EBK 管理」→「登录」。
   美团注意：新版工作台**每个账号首次使用需选一次角色**（前台/店长/老板）；角色在美团
   服务端持久化，选过一次即可。未选时读取会返回带指引的 needs_review 提示（2026-08-19
   适配），按提示在登录窗口完成一次选择即可。
2. **验证**：
   ```powershell
   curl http://127.0.0.1:8100/api/v1/health      # 应返回 {"status":"ok"}
   hermes -p <首个profile> mcp list                # 应列出 pan-hotel-watch（CLI 不带斜杠）
   #   远程访问：http://<目标机IP>:5173（操作台）/ :8100（API，需开防火墙）
   ```
3. **测试飞书机器人**：在飞书群 @机器人 发一条指令，确认正常回复。
4. **旧机器停服**：两台机器的 Hermes Gateway 会抢飞书消息，尽快在旧机跑 `scripts\stop-all.bat`。
5. **重建公网隧道（如需要）**：`start-all.bat` 不启动隧道。cloudflared 等二进制已在
   `.runtime\` 随包带过来；ngrok 未随包迁移，需另装并恢复 authtoken。
6. **开机自启（推荐）**：`scripts\register-startup-task.ps1`；方式 B 部署的机器已由
   onstart 计划任务接管（skip 守卫保证成功后只起服务不重跑迁移）。不注册时机器重启后
   后端/网关不会自动恢复（2026-08-19 远端实测：重启后全套服务掉线，需手动 start-all）。

---

## 六、常见问题

**Q：winget 不可用 / 安装依赖失败？**
migrate-boot\installers 离线件优先（`-IncludeInstallers` 打包）；无离线件时脚本走
镜像直连静默安装；仍失败就手动装（Python 3.11、Node 20、uv、7-Zip，勾选 PATH）后
`-SkipEnvCheck` 重跑。

**Q：`py -3` 报错 0x80070003 或指向不存在的 Python？**
py 启动器指向了已卸载的 Python。「设置-应用」卸载旧 Python Launcher 后重装
Python 3.11（勾选 py launcher 与 PATH）。

**Q：Step 2 `uv sync` / editable 安装报错？**
- 旧版脚本曾用 `python -m pip install -e`，在 `uv venv --seed` 注入的 pip 不完整时
  直接 `ModuleNotFoundError: pip._vendor.packaging.__about__`——现行脚本已改用
  `uv pip install -e`（不依赖 venv 内 pip），失败会降级为告警并由 hermes CLI 实测兜底。
- `uv sync` 网络失败：确认网络后重跑；单独重试时必须先
  `$env:UV_PROJECT_ENVIRONMENT = "$PWD\venv"`，不能裸 `uv sync`（会建到 `.venv\`
  且缺 feishu/edge-tts 依赖）。

**Q：Step 6 报 icacls 1332「帐户名与安全标识间无任何映射完成」？**
`feishu_profiles.py` 会按 `<USERDOMAIN>\<USERNAME>` 给 .env 授权。三种成因：
1. **目标用户账户不存在**（实测遇到过：账户"看似能登录"实际不在 SAM）——`net user <用户>`
   验证，不存在则 `net user <用户> <密码> /add`；
2. **以 SYSTEM/计划任务运行且 USERDOMAIN 不是本机计算机名**（SYSTEM 默认 USERDOMAIN
   为 WORKGROUP，icacls 解析不了本地账户）——在启动包装里 `set USERDOMAIN=<真实计算机名>`；
3. 交互式以目标用户登录运行则天然正确，不会触发。

**Q：Step 7 卡在「Press any key to continue . . .」不动？**
start-all.bat 末尾的 `pause` 在非交互会话里永远等按键。现行脚本已自动 `< nul` 免交互；
手动跑 start-all.bat 于计划任务/无头场景时同样加 `< nul`。

**Q：`install.bat` 下载 playwright chromium 失败（404）？**
npmmirror 缺部分 build（如 headless-shell 1148）。打包带 `-IncludeInstallers` 的
playwright-browsers 离线件可整段跳过下载；否则设
`$env:PLAYWRIGHT_DOWNLOAD_HOST = "https://cdn.playwright.dev"` 后重跑。

**Q：飞书 @机器人 正常，但后台 review 报 `Skill 'pan-hotel-watch' not found in active profile ...`？**

register-hermes 旧版把 skill/插件部署为 **SymbolicLink**；hermes 会话加载 skill 用
`os.walk(followlinks=True)`（跟随），但后台 review 调用的 `skill_manage` 工具用
pathlib `rglob` 查找——Python 的 `**` 递归**不跟随符号链接目录**，于是符号链接部署的
skill 全部「找不到」（default 根目录是打包物化解出的实体目录，所以提示语说 skill
在 default 里）。2026-08-19 修复：register-hermes 改用 **Windows Junction**
（无需开发者模式/管理员权限，rglob 与 os.walk 都跟随，单源语义不变），已部署机器
可重跑 register-hermes.ps1 或把 skills/plugins 下的 SymbolicLink 就地转 Junction。

**Q：飞书机器人不回复？**
1. 确认 Step 6 三条命令（validate / write-backend-env / apply）都成功；
2. `hermes gateway status`（每个 profile）确认 Gateway 在运行；
3. 重启 Gateway：`hermes -p <profile> gateway run --replace`；
4. 旧机器 Gateway 是否还在抢消息（见部署后操作 4）；
5. 查看 `%LOCALAPPDATA%\hermes\profiles\<profile>\logs\`。

**Q：飞书 @机器人 回复「后台 API 返回 HTTP 401 / 没有权限」或「无法连接 pan-hotel-watch 后台 API」，但操作台手动刷新/推送正常？**

这是 2026-08-19 在 10.10.10.3 上实测命中的迁移缺陷，根因是**三层因素叠加**：

1. 源机后端 `.env` 的 `PAN_HOTEL_ENFORCE_API_AUTH=false`（鉴权关闭），MCP 一直
   「裸调」后端也能通过；`feishu_profiles.py write-backend-env` 曾**无条件把该值
   翻转为 `true`**，鉴权即刻收紧；
2. hermes-agent 启动 MCP 子进程时只透传白名单环境变量（PATH/HOME 等，见
   hermes-agent `tools/mcp_tool.py _build_safe_env`），profile `.env` 里的
   `WATCH_API_SHARED_SECRET` 进不了 `pan_hotel_mcp` 子进程，唯一通道是
   profile `config.yaml` 的 `mcp_servers.pan-hotel-watch.env` 显式声明（支持
   `${VAR}` 占位符由 profile `.env` 插值）——迁移链路从未写入；
3. 于是所有 run-now / 日简报 / 分析类 MCP 调用被后端 401 拒绝，机器人把它转述
   给用户。操作台正常是因为前端带 supervisor API key。
   「无法连接后台」则是另一形态：个别 profile（如 quanji-ai）的
   `WATCH_API_BASE_URL` 还是旧端口（8010），或后端未启动。

修复（新机器已内置，老部署跑一次）：

```powershell
# 仓库已修复 register-hermes.ps1 / feishu_profiles.py / migrate-import.ps1；
# 已部署机器直接运行自包含修复脚本：
powershell -ExecutionPolicy Bypass -File scripts\fix-hermes-mcp-auth.ps1 -DryRun   # 预览
powershell -ExecutionPolicy Bypass -File scripts\fix-hermes-mcp-auth.ps1          # 修复+重启 Gateway
```

脚本会：保留/调整 `PAN_HOTEL_ENFORCE_API_AUTH`、把与后端一致的
`WATCH_API_SHARED_SECRET` / `WATCH_API_SUPERVISOR_KEY` 写进各 profile `.env`、
在 MCP env 块补 `${VAR}` 占位符、纠正旧端口、重启 Gateway 并自检。

**Q：浏览器打开 OTA 平台是未登录状态？**
正常现象：Cookies 由 DPAPI 按用户加密，跨机不可解。操作台重新登录一次即可。

**Q：换了 `-ProjectDir` 与源机不同会有问题吗？**
Step 5 会修正 profile config.yaml 里 MCP 的路径；但数据库、外部引用仍可能残留旧路径，
没有特殊理由建议保持一致。

**Q：后端健康检查失败（Step 8）？**
多为端口占用（默认 8100，见 `.env`）。换端口或释放后重跑 `scripts\start-all.bat`。
远程访问不通时先确认防火墙入站规则（8100/5173）与主机到目标机的路由。

**Q：`hermes update` 报错？**
迁移包刻意不含 `hermes-agent\.git`。需要更新时重新克隆 hermes-agent 仓库并重跑
`register-hermes.ps1`。

**Q：打包时提示文件被锁定 / skipped？**
打包时有进程未停干净（通常是 Chrome 持有 Cookies）。停掉相关进程重新打包；
`-Force` 打出的包登录态可能不完整。

**Q：无头机器上怎么确认部署到底跑没跑？**
不要依赖截屏/OCR（虚拟显卡合成层可能冻结在旧帧，视觉模型会把报错画面"脑补"成
进度日志，实测踩过）。可靠信号：① CPU 占用趋势（`Get-VM | Select Uptime,CPUUsage`）；
② 关机刷盘后离线读 `migration.log` / `migration-exit.txt`（VM 运行中挂载 live 盘会被
拒 0x80070020）；③ Hyper-V 缩略图抓帧用非 640x480 尺寸（该档位常返回陈旧帧）。

---

## 脚本变更记录（本次实测后合入 `scripts\migrate-import.ps1`）

| 位置 | 变更 | 原因（实测） |
|------|------|--------------|
| Step 2 | `python -m pip install -e` → `uv pip install -e … --python $HermesPython`，并去掉 `\| Out-Null` | `uv venv --seed` 注入的 pip 可能不完整（`pip._vendor.packaging.__about__` 缺失），venv 内 pip 直接挂；uv 自带安装器不受影响。原 `\| Out-Null` 吞掉了全部失败原因 |
| Step 2 | editable 安装失败由 `throw` 降级为 `WARN` | 现代 uv sync 多已把项目本体 editable 装进 venv，`pip install -e` 失败≠部署失败；由紧随的 `hermes --version` CLI 实测兜底（真失败仍会 throw） |
| Step 7 | `cmd.exe /c "…start-all.bat"` → `… < nul` | start-all.bat 末尾 `pause` 在非交互会话（计划任务/session 0）永久阻塞，曾把迁移卡死在 Step 7；交互式运行无副作用 |
| Step 6 | `write-backend-env` 保留已有 `PAN_HOTEL_ENFORCE_API_AUTH`（缺失才默认 true）；apply 顺带把后端 supervisor key 写入 profile `.env`；Step 6 末尾为**所有** profile（含不在飞书清单的）补齐 `WATCH_API_SHARED_SECRET` / `WATCH_API_SUPERVISOR_KEY` | 无条件翻转 ENFORCE=true + MCP 子进程拿不到服务令牌（hermes-agent `_build_safe_env` 白名单过滤）→ 部署后 @机器人 run-now 类调用全部 401（见上方 FAQ） |
| Step 5 | `register-hermes.ps1` 在 MCP env 块写入 `${WATCH_API_SHARED_SECRET}` / `${WATCH_API_SUPERVISOR_KEY}` 占位符 | 同上：profile `.env` 的密钥只能经 config.yaml env 占位符进入 MCP 子进程 |
| export 全局 | 新增 `$V6v7RequiredFiles` 清单，robocopy 后强制核对 V6/V7 新模块（蒸馏/QA卡片/页面告警/chat浏览器/全局推送/周期读取/评价/退款/预约/金字塔/促销/新worker/MCP/skill参考/话术表）是否进入 staging | `756fc4b`：防止未来排除规则误伤关键新模块；新增模块时须把代表文件加进清单 |
| export `-Force` | robocopy 退出码 8 位（锁定文件 ERROR 32）由 `throw` 降级为告警 | `1b0927c`：`-Force` 本意就是"跳过锁定文件强行打包"，原逻辑会把 Chrome 占用的 Cookies 当致命错误终止，与 `-Force` 语义矛盾 |
| Step 8 | 自检命令 `hermes -p <profile> /mcp list` → `mcp list`（去斜杠） | `33df945`：`/mcp list` 是网关内斜杠命令，CLI 下 exit 2；旧脚本此自检项长期显示"未确认"，实际 MCP 注册没问题 |
| export+import 清单 | `$V6v7RequiredFiles` / `$V6v7ProbeFiles` 补 2026-08~09 漏登记域的代表文件并完全对齐（hotel_analytics、rag、skills、platform_ebk_watch、美团/飞猪 ebooking_watch 域本体、pyramid_bidding、promotion_adjust 域本体、三平台 review adapter、`pan-hotel-hermes/client/watch_api.py`、`docs/飞书话术速查-业务版.xlsx`） | 2026-09-17 全局复查：全量 robocopy 本就会带上这些文件，漏登记不阻断迁移，但按「新增模块须登记两份清单」的约定应补齐；两份清单此后必须同步增删 |
| Step 2 venv 删除 | 旧 venv 删除改为 `attrib -r -s -h` 清属性 + `Remove-Item`/`cmd rd` 交替重试 3 次，仍失败才 `throw` | 2026-09-18 生产机A 实测：7z 解包还原的 venv 文件带只读属性（lark_oapi），`Remove-Item -Force` 报「访问被拒绝」直接终止迁移；重试间隔同时吸收杀软扫描刚落盘文件的瞬时锁 |
| Step 5 skill/plugin 链接 | `register-hermes.ps1` 新增 `Test-PanHotelLinkTraversal`：junction 创建成功后实测遍历（源目录取探针文件经链接 stat）；已存在且指向正确的链接同样校验。遍历被拦截（Win11 24H2+/26200 挂载点加固，WinError 448）时用 .NET `Directory::Delete($path,$false)` 只删重解析点，降级拷贝兜底 | 2026-09-18 生产机A（Win11 26200）实测：跨卷 Junction 创建不报错但 Python pathlib/Test-Path 遍历全部 448，`hermes gateway list` 直接崩溃；原脚本只兜底「创建失败」，不兜底「创建成功但被策略拦截」 |
