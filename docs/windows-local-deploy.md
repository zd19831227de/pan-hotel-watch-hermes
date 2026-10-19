# Windows 本地部署说明

本项目面向一台固定 Windows 电脑作为盯盘主机：后端 API、监控轮询 worker、前端控制台都在这台机器上运行；同事通过局域网访问前端；Ctrip EBK 登录态保存在这台电脑的浏览器 profile；所有 secrets 只写入本机 `.env`，不提交 git。

## 1. 主机准备

在固定主机上安装：

- Windows 10/11 或 Windows Server 桌面环境。
- Python 3.11+（**不要用残缺或未验证的 Python 3.13 建项目 venv**）。
- Node.js 20+。
- Git。
- Chrome/Playwright Chromium 由安装脚本安装。

建议：

- 给这台电脑使用固定内网 IP 或 DHCP 绑定。
- 关闭睡眠，保留屏幕锁定即可。
- 用专门的 Windows 用户运行盯盘系统。
- 真实浏览器读取需要用户桌面会话，开机自启推荐使用“登录后启动”。如需开机无人值守，先配置该 Windows 用户自动登录，再使用登录触发任务。

## 2. 首次安装

```bat
cd /d C:\Users\Administrator\Desktop\pan-hotel-watch
copy /Y .env.example .env
notepad .env
scripts\install.bat
```

环境与 venv 分工（根 `.venv` / `pan-hotel-hermes/.venv` / Hermes Agent）见
[python-environment.md](python-environment.md)。`install.bat` 会创建根 venv 与
Hermes MCP venv，并安装 Playwright Chromium。

`.env` 至少确认这些项：

```text
DATABASE_URL=sqlite+aiosqlite:///./pan_hotel_watch.db
BROWSER_STATE_DIR=.runtime/browser-state
PLAYWRIGHT_ARTIFACT_DIR=output/playwright
CTRIP_EBOOKING_PROFILE_DIR=C:\pan-hotel-watch-runtime\ctrip-ebk-profile
CTRIP_EBOOKING_DEFAULT_PLATFORM_ACCOUNT_ID=ctrip-ebk-main
CTRIP_EBOOKING_DEFAULT_BROWSER_SESSION_ID=ctrip-ebk-main
CTRIP_EBOOKING_DEFAULT_SESSION_PORT=9221
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFICATION_TOKEN=
```

MySQL 示例：

```text
DATABASE_URL=mysql+aiomysql://user:password@127.0.0.1:3306/pan_hotel_watch?charset=utf8mb4
```

注意：

- `.env` 不提交 git。
- 不要把 OTA 密码、验证码、cookies、浏览器 profile、Feishu secret 写进代码或文档。
- `CTRIP_EBOOKING_PROFILE_DIR` 推荐放在仓库外的固定目录，便于升级代码时保留登录态。
- 飞书入口模式由 `.env` 的 `FEISHU_AGENT_MODE` 控制：
  - `hermes`（默认）：飞书对话由 Hermes Gateway 接管。`start-all.bat` 不再启动
    内置 `feishu-listener`，后端也不调用内置大模型。后端仍通过
    `POST /api/v1/feishu/events` 处理 Hermes 转发的卡片动作与结构化调价命令。
    此模式下飞书凭证与大模型配置在 Hermes 侧维护，后端 `.env` 的 `LLM_*` 可留空。
  - `builtin`：旧模式。`start-all.bat` 启动 `feishu-listener`，后端用内置大模型
    回复 @机器人 自由文本。`.env` 需要写入 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、
    `FEISHU_VERIFICATION_TOKEN`，并配置 `LLM_*`。
- 两种模式下，调价审批链、定时轮询、浏览器读取、可信 DOM 价格趋势、Feishu 卡片
  格式都不变；切换模式只改变「谁回答自由文本」和「是否启动内置 feishu-listener」。
- 任何模式都不要让两个进程同时监听同一个飞书应用：使用 Hermes 时不要再启动内置
  `feishu-listener`，也不要运行旧 `pan-hotel-ai` 的 `app.workers.feishu_listener`，
  否则群消息可能被旧只读 bot 接走，调价指令会返回 `write_preview_required`。

### WebUI 的 LLM 配置菜单

启动前端后，进入主导航的 **LLM 配置** 菜单，可以维护：

- 模型供应商、OpenAI-compatible URL、API key 和当前模型；
- 保存后端持久化配置，API key 只在后端保存，前端只显示脱敏状态；
- 使用当前表单参数拉取 `/models` 模型列表，并从列表选择模型；
- 使用当前表单参数测试 `/chat/completions` 连接，未保存的 URL、模型和 API key 也可以直接测试。

对应后端接口为：

```text
GET  /api/v1/config/integrations
POST /api/v1/config/integrations
POST /api/v1/config/integrations/llm/models
POST /api/v1/config/integrations/llm/test
```

模型拉取和连接测试不会把 API key 放入响应；服务商需要支持 OpenAI-compatible 的 `/models` 和 `/chat/completions` 接口。

## 3. 打开局域网访问

用管理员命令提示符执行：

```bat
cd /d C:\Users\Administrator\Desktop\pan-hotel-watch
netsh advfirewall firewall add rule name="Pan Hotel Watch Backend" dir=in action=allow protocol=TCP localport=8100
netsh advfirewall firewall add rule name="Pan Hotel Watch Frontend" dir=in action=allow protocol=TCP localport=5173
```

默认开放：

- 后端：TCP `8100`
- 前端：TCP `5173`

## 4. 启动、停止、状态、日志

启动全部服务：

```bat
scripts\start-all.bat
```

一键编排（推荐 hermes 模式）：先清理内置 `feishu-listener`，再启动
backend + scheduler + frontend，然后检查 / 可选拉起 Hermes Gateway，最后汇总状态：

```bat
scripts\start-stack.bat                 rem 起后端三件套
scripts\start-stack.bat --start-gateway rem 额外启动 Hermes Gateway
```

停止全部服务：

```bat
scripts\stop-all.bat
```

检查状态：

```bat
scripts\status.bat
```

`status.bat` 会显示：

- 当前 `FEISHU_AGENT_MODE`。`hermes` 模式下飞书入口由 Hermes Gateway 接管，
  status 不再把内置 `feishu-listener` 当作常驻进程或健康心跳，并会主动扫描是否
  仍有内置 `app.workers.feishu_listener` 在「抢消息」。
- backend、worker、frontend 进程是否仍在运行（`builtin` 模式下额外包含 feishu-listener）。
- 8100、5173 端口当前占用情况。
- 后端和前端 HTTP 检查结果。
- worker 心跳文件（`builtin` 模式下含 feishu-listener），前端会用同一类心跳区分
  “已开启轮询”和“后台确认存活”。
- 当前主机局域网访问地址。

查看全部日志：

```bat
scripts\show-logs.bat all
```

实时跟随后端日志：

```bat
scripts\show-logs.bat backend
```

实时跟随 worker 应用日志：

```bat
scripts\show-logs.bat worker
```

实时跟随 Feishu @机器人长连接日志：

```bat
scripts\show-logs.bat feishu-listener
```

默认日志位置：

```text
.runtime\logs\backend.out.log
.runtime\logs\backend.err.log
.runtime\logs\worker.out.log
.runtime\logs\worker.err.log
.runtime\logs\feishu-listener.out.log
.runtime\logs\feishu-listener.err.log
.runtime\logs\frontend.out.log
.runtime\logs\frontend.err.log
.runtime\logs\ctrip_ebooking_data_center_scheduler.log
```

## 5. 局域网访问

启动后脚本会打印当前主机 IP。局域网同事访问：

```text
http://<主机内网IP>:5173
```

后端健康检查：

```text
http://<主机内网IP>:8100/api/v1/health
```

前端默认通过 Vite 代理把 `/api` 转发到本机后端，所以同事只需要打开前端地址。

## 6. Ctrip 登录态

首次登录流程：

1. 启动后端：`scripts\start-all.bat`。
2. 通过前端浏览器账号入口打开 Ctrip EBK 或 Ctrip 官网浏览器。
3. 人工完成账号、短信、二维码、验证码或风控验证。
4. 登录态会保存在 `CTRIP_EBOOKING_PROFILE_DIR` 指向的本机目录。

运行规则：

- 浏览器账号只有两类用途：Ctrip EBK 后台、Ctrip 官网竞品价格读取。
- 遇到登录、验证码、短信、二维码、风控，系统返回 `human_required`。
- 不绕过平台风控。
- 不保存 OTA 密码、验证码或 cookies 到数据库、日志、API 响应或 git。
- 同一个 Ctrip profile 同一时间只能被一个 Chrome 实例持有。后台、worker、
  手动登录会优先复用已打开浏览器；如果另一个任务正在启动或占用该 profile，
  页面会提示稍后重试或关闭占用窗口，不应看到原始 Playwright
  `TargetClosedError` 作为业务结果。

如果出现浏览器账号占用：

```bat
scripts\status.bat
scripts\show-logs.bat backend
scripts\show-logs.bat worker
```

先等待当前读取完成；如果登录窗口已经不需要，手动关闭该 Ctrip Chrome 窗口后重试。

## 7. 盯盘配置和推送

前端控制台保留两个核心监控区：

- 携程 EBK 数据中心盯盘：绑定 EBK 浏览器账号，可编辑推送标题、webhook、
  数据集开关、自动轮询开关；支持立即读取。Feishu 卡片包含读取的年月日时分。
- 竞对酒店盯盘：一个监控组绑定一个 Ctrip 官网浏览器账号，可编辑监控组、
  本店/竞品酒店、手机卡片 webhook、电脑表格 webhook、推送标题、
  自动轮询开关；立即操作为“读取并推送”。

Feishu @机器人调价审批（完整安全契约见仓库根目录
[AGENTS.md — 调价安全契约](../AGENTS.md#调价安全契约pricing-safety-contract--authoritative)）：

- 竞对轮询超阈值后自动提醒；**竞对降价**才生成本店降价建议，**非直连**账号可继续发审批卡。
- **竞对涨价**只提醒变化与涨幅，不建议本店涨价，也不生成审批卡。
- `@机器人 调价建议` / `@机器人 调价` 只说明自动触发机制，不临时跑检测。
- `@机器人 把今天高级大床房调到420` 生成指定房型/日期/目标价审批卡。
- 前端与普通公开 API **不提供**直接执行调价入口；审批卡按钮为「通过 / 拒绝 / 自定义调价」。

竞品趋势图只展示真实可信 DOM 房价：

- 数据源只能是 `room_rate_dom_display_price` 且 `is_trusted=true` 的价格。
- 分为今日无早、今日有早、明日无早、明日有早四张图。
- 每家酒店只展示抓到的第一款房型，通常是页面上较低价格的房型。
- 不展示 mock 数据、run id 或英文技术字段。

## 8. 开机后启动

推荐注册为“用户登录后启动”任务：

```bat
scripts\register-startup-task.bat
```

如果确实要注册为“系统开机时启动”：

```bat
scripts\register-startup-task.bat --startup
```

说明：

- 真实浏览器读取最稳妥的方式是 Windows 用户登录后运行。
- 如果用 `AtStartup`，可能没有可交互桌面会话，Playwright 真实浏览器任务可能无法正常完成。
- 固定主机可以设置 Windows 自动登录，再使用默认的 `AtLogOn` 触发。

取消开机任务：

```bat
scripts\unregister-startup-task.bat
```

## 9. 单独运行

需要调试时可以单独启动：

```bat
scripts\run-backend.bat
scripts\run-worker.bat
scripts\run-worker.bat feishu-listener  rem 仅 FEISHU_AGENT_MODE=builtin 时需要
scripts\run-frontend.bat
```

> `hermes` 模式（默认）下飞书入口由 Hermes Gateway 接管，**不要**手动启动
> `run-worker.bat feishu-listener`；即使运行，它也会因模式守卫直接退出，
> 避免与 Hermes 抢消息。

指定端口示例：

```bat
rem 自定义端口请先设置环境变量，再启动 BAT
set BACKEND_PORT_OVERRIDE=8100
set FRONTEND_PORT_OVERRIDE=5174
scripts\run-backend.bat
scripts\run-frontend.bat
```

## 10. 更新代码后的顺序

```bat
scripts\stop-all.bat
git pull
scripts\install.bat
scripts\start-all.bat
scripts\status.bat
```

如只是改前端页面，可强制重建：

```bat
scripts\start-all.bat --rebuild
```

## 11. 常见检查

端口被占用：

```bat
netstat -ano | findstr ":8100"
netstat -ano | findstr ":5173"
```

确认 `.env` 没进 git：

```bat
git status --short
```

确认 worker 心跳：

```bat
scripts\status.bat
```

确认 Ctrip profile 目录存在：

```bat
if exist C:\pan-hotel-watch-runtime\ctrip-ebk-profile (echo profile exists) else (echo profile missing)
```

确认说明文件和 secrets 边界：

```powershell
git status --short
git diff -- .env
```

`.env`、`.runtime`、`output`、数据库文件、浏览器 profile 和日志都不应提交。
