# Python 环境说明（Windows）

本仓库在同一台 Windows 主机上会同时出现 **多个 Python 虚拟环境**。用错环境会导致 EBK 浏览器读取失败（例如 `ModuleNotFoundError: No module named 'concurrent.futures.thread'`）、Hermes MCP 无法启动，或测试/脚本跑到错误的依赖上。

本文是 **环境选型权威说明**；`README.md` 与部署文档只保留短链接。

## 1. 环境总览

| 环境 | 路径 | Python 基座 | 用途 | 是否跑 Playwright |
|------|------|-------------|------|-------------------|
| **根 venv（主环境）** | `<repo>/.venv` | **Python 3.11**（推荐 uv 安装的 cpython-3.11） | 后端 API、定时 worker、EBK/竞品浏览器读取、后端单元测试 | **是** |
| **Hermes MCP venv** | `<repo>/pan-hotel-hermes/.venv` | **必须与根 venv 同源**（3.11） | `pan_hotel_mcp`、Hermes 工具层单元测试 | 否（只调 HTTP API） |
| **Hermes Agent venv** | `%LOCALAPPDATA%\hermes\hermes-agent\venv` | Hermes 安装器自带 | `hermes gateway`、Hermes CLI | 否 |

**不要用** 系统 PATH 里的裸 `python`，尤其当它是 **Python 3.13** 且安装不完整时。本机若存在残缺的 `Python313`，用它建 venv 会导致标准库缺模块。

## 2. 各组件必须用哪个 Python

### 后端 API（uvicorn，默认 `:8100`）

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8100
```

或由脚本托管：

```bat
scripts\run-backend.bat
rem 重启请执行 scripts\stop-all.bat 后再执行 scripts\start-all.bat
```

### 盯盘 Worker（EBK 数据中心 + 竞品价格调度）

```powershell
.\.venv\Scripts\python.exe -m app.workers.read_only_scheduler
```

或：

```bat
scripts\run-worker.bat
rem 重启请执行 scripts\stop-all.bat 后再执行 scripts\start-all.bat
```

Worker 与后端 **必须共用根 `.venv`**，因为真实 Ctrip 浏览器读取只在后端域服务里执行。

### 后端单元测试

```powershell
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m unittest backend.tests.domain.test_data_center_watch_service
```

### Hermes MCP（`pan_hotel_mcp`）

优先使用 **`pan-hotel-hermes/.venv`**（由 `scripts\install.bat` 用根 venv 的 Python 创建）：

```powershell
cd pan-hotel-hermes
.\.venv\Scripts\python.exe -m pan_hotel_mcp
```

注册到 Hermes 时执行：

```powershell
.\pan-hotel-hermes\scripts\register-hermes.ps1
```

`register-hermes.ps1` 会按顺序尝试：`pan-hotel-hermes/.venv` → 根 `.venv` → PATH 中的 `python`，并校验 `encodings`、`mcp`、`concurrent.futures.thread` 均可导入。

`register-hermes.ps1` 还会向 **Hermes agent venv**（`%LOCALAPPDATA%\hermes\hermes-agent\venv`，即 `config.yaml` 的 `command` 实际指向、真正运行 MCP 子进程的 venv）执行 `pip install -e pan-hotel-hermes`，以 editable 方式安装 `pan_hotel_mcp` 及其同级首方包 `client` / `router`。这是 `pan_hotel_mcp` 的正式加载方式，取代了原先手写的 `pan_hotel_mcp.pth`（已删除）。editable 安装后，仓库内修改 MCP 源码只需重启 Hermes Gateway 即可生效。依赖以 `pan-hotel-hermes/pyproject.toml` 的 `[project.dependencies]` 为唯一来源（旧 `pan-hotel-hermes/requirements.txt` 已移除）。

### Hermes MCP 单元测试

```powershell
$env:PYTHONPATH = 'pan-hotel-hermes'
.\pan-hotel-hermes\.venv\Scripts\python.exe -m unittest discover -s pan-hotel-hermes/tests -p "test_*.py"
```

### Hermes Gateway（飞书对话入口）

使用 Hermes 安装器自带的 venv，**不要**与根 venv 混用：

```powershell
hermes gateway run
# 或
%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe gateway run
```

MCP 子进程由 `config.yaml` 的 `mcp_servers.pan-hotel-watch.command` 指定；修改 Python 或 Skill 后需重启 Gateway。

## 3. 架构关系（避免误以为 MCP 直接读浏览器）

```text
飞书 → Hermes Gateway（hermes-agent venv）
         → pan_hotel_mcp（pan-hotel-hermes/.venv）
            → HTTP → 后端 API（根 .venv）
                        → Playwright / Ctrip EBK 读取
```

Hermes 层 **不嵌入** 后端、**不直接** 操作浏览器。EBK 盯盘失败时，先查后端环境，再查 MCP 环境。

## 4. 常见误用与症状

| 误用 | 典型症状 |
|------|----------|
| 用系统 Python 3.13 创建 `pan-hotel-hermes/.venv` | `No module named 'encodings'`；MCP 无法启动 |
| 后端进程混用 3.13 标准库 + 3.11 site-packages | EBK 读取 `0/10`，报错 `concurrent.futures.thread`；日志里 asyncio 路径指向 `Python313\Lib\...` |
| 环境变量 `PYTHONHOME` 指向错误安装目录 | 与上类似，标准库与 venv 包版本不一致 |
| 手动 `python -m uvicorn` 而非 `.\.venv\Scripts\python.exe` | 可能装上错误依赖版本或找不到 playwright |
| 两个后端/两个 worker 同时监听同一 profile | `human_required` / profile 锁冲突，与 Python 无关但常同时出现 |

## 5. 一键健康检查

在仓库根目录执行：

```powershell
$RootPy = ".\.venv\Scripts\python.exe"
$HermesPy = ".\pan-hotel-hermes\.venv\Scripts\python.exe"

Write-Host "=== 根 venv ==="
& $RootPy -c "import sys, asyncio; from concurrent.futures import thread; import playwright; print(sys.version); print('asyncio', asyncio.__file__); print('playwright', playwright.__version__)"

Write-Host "=== Hermes venv ==="
& $HermesPy -c "import encodings, mcp; from concurrent.futures import thread; print('hermes venv ok')"

Write-Host "=== 后端 HTTP ==="
Invoke-RestMethod http://127.0.0.1:8100/api/v1/health
```

期望结果：

- 版本号为 **3.11.x**
- `asyncio` 路径在 `cpython-3.11` 或 `<repo>\.venv` 下，**不应**出现 `Python313`
- `/api/v1/health` 返回成功

## 6. 标准修复流程

环境疑似损坏或 EBK 读取报 `concurrent.futures.thread` 时：

```bat
rem 1. 重装依赖（根 venv + Hermes venv + Playwright Chromium）
scripts\install.bat

rem 2. 重启后端、worker 和前端
scripts\stop-all.bat
scripts\start-all.bat

# 3. 重新注册 Hermes 并重启 Gateway
.\pan-hotel-hermes\scripts\register-hermes.ps1 -SkipModelHint -RestartGateway

# 4. 验证 EBK 读取（需本机 EBK 已登录）
Invoke-RestMethod -Method POST `
  http://127.0.0.1:8100/api/v1/ctrip-ebooking/data-center-watch-groups/ctrip_ebooking_data_center/run-now
```

若 `pan-hotel-hermes/.venv` 单独损坏，可用根 venv 重建：

```powershell
Remove-Item -Recurse -Force .\pan-hotel-hermes\.venv
.\.venv\Scripts\python.exe -m venv .\pan-hotel-hermes\.venv
.\pan-hotel-hermes\.venv\Scripts\pip install -e .\pan-hotel-hermes
```

**不要**执行：

```powershell
python -m venv pan-hotel-hermes\.venv   # 若 PATH 指向 Python 3.13，会再次建坏环境
```

## 7. 脚本与环境的对应关系

| 脚本 | 使用的 Python |
|------|----------------|
| `scripts/install.bat` | 创建/更新根 `.venv`；用根 Python 创建 `pan-hotel-hermes/.venv` |
| `scripts/run-backend.bat` | 根 `.venv` |
| `scripts/run-worker.bat` | 根 `.venv` |
| `scripts/start-all.bat` / `start-stack.bat` | 根 `.venv`（经 common.bat） |
| `pan-hotel-hermes/scripts/register-hermes.ps1` | `pan-hotel-hermes/.venv` 或根 `.venv` |

`scripts/common.ps1` 中的 `Get-PanHotelWatchPython` **固定**返回 `<repo>/.venv\Scripts\python.exe`；托管进程应始终走这里。

## 8. 给开发 / Agent 的简短口诀

1. **读浏览器、跑后端、跑 worker** → 根 `.venv`
2. **跑 MCP、Hermes 工具测试** → `pan-hotel-hermes/.venv`
3. **跑 Hermes Gateway** → `hermes-agent` 自带 venv
4. **永远不要**用未验证的系统 `python` 建项目 venv
5. 改 Python 或 Skill 后 **重启 Gateway**；改后端代码后 **重启 backend + worker**
