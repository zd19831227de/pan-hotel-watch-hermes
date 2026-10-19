# Pan Hotel Watch — 酒店 OTA 数据监控系统

面向酒店运营团队的生产级监控系统：自动读取携程 / 美团 / 飞猪 EBK 后台经营数据，
监控携程公网竞品房价，数据落库沉淀趋势，通过飞书群推送简报与提醒，并提供一条
严格受控的飞书调价审批链路。

```text
飞书群 ⇄ Hermes 网关（自然语言助手）
              │
              ▼
       后端 API（FastAPI，默认 :8100）──► SQLite（业务数据 / 快照 / 审计）
              │                              ▲
              ▼                              │
   Playwright 浏览器自动化                前端控制台（Vite/React，:5173）
   （携程 9221 / 美团 9521 / 飞猪 9531 / 竞品公网 9222）
```

## 功能总览

| 模块 | 能力 |
|------|------|
| 三平台 EBK 读取 | 携程 / 美团 / 飞猪 EBK 后台经营数据（出租率、ADR、RevPAR、销售额、订单、评价等）现读与落库 |
| EBK 全局推送 | 定时把经营简报卡推送到飞书群，支持自定义标题与 webhook |
| 竞品价格监控 | 携程公网页面 DOM 可信价抓取，本店/竞品配置、手动读取、定时轮询、移动卡与桌面表格双格式推送 |
| 价格趋势 | 仅基于可信 DOM 价格（`room_rate_dom_display_price`）的今日/明日 × 无早/含早四视图趋势图 |
| 日历价监测 | 竞品未来日期日历价矩阵读取、落库、阈值提醒、只读问答 |
| 价格变化提醒 | 相邻两轮可信读数对比，超过阈值自动提醒；竞品降价给规则化参考价，竞品涨价仅提醒 |
| 调价审批 | 飞书卡人工确认 + 预检 + 页面空跑 + 执行的全链路，仅对「非直连」账号开放 |
| 评价管理 | 三平台评价抓取、回复模板、半自动/全自动回复、每日定时汇总卡 |
| 经营 BI | 在手房量、竞争圈、去年同期、热度等四板块日报，预算与完成率口径 |
| 订单 / 退款 | 预订单、退款单读取与时间分布统计 |
| 促销 / 广告 | 携程单条立减促销规则调整（走审批链）、金字塔推广投放读取与调价 |
| 飞书智能助手 | 群内 @机器人 自然语言问答、数据卡、趋势图卡、群资料归档 |
| Web 控制台 | 账号、监控组、轮询状态、趋势图、历史记录的统一运营界面 |

## 目录结构

| 目录 | 内容 |
|------|------|
| `backend/` | FastAPI 后端 + Playwright 浏览器域服务 + 调度 worker + SQLite 持久化 |
| `frontend/` | Vite + React 运营控制台 |
| `pan-hotel-hermes/` | 飞书 Hermes 网关适配层（MCP 工具、路由、多 profile） |
| `scripts/` | 安装、启动、停止、状态、日志、重启等 Windows 运维脚本 |
| `docs/` | 使用与部署文档（见文末文档索引） |

## 快速开始（Windows）

环境要求：Windows 10/11、Python 3.11+、Node.js 18+。Playwright Chromium
由安装脚本自动下载。

```bat
copy /Y .env.example .env
scripts\install.bat
scripts\start-all.bat
```

启动后：

- 后端 API：`http://127.0.0.1:8100`（端口由 `BACKEND_PORT` 控制）
- 前端控制台：`http://127.0.0.1:5173`

完整部署说明：[docs/windows-local-deploy.md](docs/windows-local-deploy.md)；
新机器初始化清单：[docs/new-machine-init-checklist.md](docs/new-machine-init-checklist.md)。

### 关键配置（.env）

| 变量 | 说明 |
|------|------|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书应用凭据（群助手与卡片回调） |
| `FEISHU_AGENT_MODE` | 飞书入口模式：`hermes`（默认，Hermes 网关接管会话）/ `builtin`（旧版内置监听） |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | OpenAI 兼容大模型配置（自然语言问答用） |
| `WATCH_API_SHARED_SECRET` | 前后端共享密钥（开启 API 鉴权时必填） |
| `BROWSER_STATE_DIR` | 浏览器登录态目录（默认 `.runtime/browser-state`） |

配置与数据的存放位置说明见
[docs/configuration-reference.md](docs/configuration-reference.md)。

### 日常运维命令

```bat
scripts\start-all.bat      :: 启动后端 + worker + 前端 + Hermes 网关
scripts\stop-all.bat       :: 全部停止
scripts\status.bat         :: 查看各进程状态
scripts\show-logs.bat all  :: 查看日志（backend / worker / frontend / hermes）
scripts\restart-backend.ps1
scripts\restart-frontend.ps1
scripts\restart-worker.ps1
```

## 使用说明

### 1. Web 控制台

控制台主导航共八个页签：

| 页签 | 用途 |
|------|------|
| 账号与浏览器 | 浏览器账号的创建、登录态管理、平台绑定、飞书群绑定、连接模式标注 |
| EBK 操作台 | EBK 监控组配置：推送标题、webhook、手动读取、定时轮询、心跳状态 |
| 竞品价格 | 竞品监控组：本店/竞品酒店配置、手动读取并推送、定时轮询、最新可信价、趋势图 |
| 日历价监测 | 未来日期日历价矩阵、自动轮询开关、读取时间与提醒 |
| 价格变化提醒 | 变价阈值配置、提醒记录、每监控组独立推送标题 |
| 评价管理 | 三平台评价抓取与回复配置、待回复统计 |
| 历史记录 | 历史问答与推送记录 |
| 经营 BI | BI 日报四板块、预算与完成率、飞书推送开关 |

另有 LLM 配置页（填写 OpenAI 兼容地址 / API key / 模型并测试连通）与
高级配置（Hermes 网关准入、代理等）。

### 2. 账号与浏览器

浏览器账号分两类用途：

- **EBK 账号**：登录携程 / 美团 / 飞猪 EBK 后台，读取经营数据；
- **竞品公网账号**：登录携程公网，读取竞品酒店页面价格。

每个 EBK 账号还需标注连接模式：

| 模式 | 含义 | 权限 |
|------|------|------|
| `unknown`（未确认） | 新建账号默认值 | 只读 + 建议，等同直连 |
| `direct`（直连） | 与平台直连的账号 | 只读 + 建议，禁止自动改价 |
| `non_direct`（非直连） | 非直连账号 | 唯一可在飞书审批后进入写价链路的模式 |

浏览器登录态是单实例资源：后端、worker、人工登录窗口通过 CDP 复用同一个
浏览器实例；如遇「浏览器被占用」提示，等待当前读取完成或关闭闲置的浏览器
窗口后重试。出现登录、验证码、短信、扫码、风控时，系统返回「需人工处理」，
请在浏览器窗口人工完成后重试。

### 3. EBK 数据监控与推送

EBK 监控组支持：可编辑推送标题、可编辑 webhook、手动读取、定时轮询；
飞书卡片附带读取时间。EBK 读取是纯只读操作，不会报名活动、改促销、改价。

### 4. 竞品价格监控

竞品监控组支持：本店与竞品酒店增删改、移动卡与桌面表格双 webhook、手动
读取并推送、定时轮询。趋势图只用可信 DOM 价格，页面显示优惠（如
`无早 ¥416（优惠32）`）时一并记录并推送；查不到优惠就留空，不猜测回填。

### 5. 价格变化提醒与调价参考

系统只比较「最新一轮成功读取」与「上一轮成功读取」的可信价格，超过阈值时：

- **竞品降价**：推送市场变化说明 + 规则化参考价 + 风险提示，卡片仅提供
  「自定义调价」和「暂不处理」；运营输入目标价后才可能进入执行确认；
- **竞品涨价**：仅推送变化提醒与幅度，不建议本店跟涨，不产生任何可执行
  审批路径。

手动检测默认不推送飞书。

### 6. 调价审批链路

唯一受支持的写价流程：

```text
可信变价证据或手动指令
  → 规则化说明与参考价（仅建议）
  → 运营输入/确认具体目标价
  → 规则化预览（基于可信数据）
  → 飞书人工确认卡
  → 「确认按此价格调价」
  → 执行预检 → 页面空跑（dry-run）→ 执行 → 飞书结果卡
```

安全边界（摘要）：

- `@机器人 调价建议` / `@机器人 调价` 不会按需评测，只解释自动触发机制；
- `@机器人 把今天高级大床房调到420` 创建手动房型/日期/目标价审批卡；
- 前端与公开 API 没有任何直接执行/空跑入口；卡片「通过」仅记录决定，
  不会触发执行；
- 执行价只能来自运营输入并已确认的目标价；
- 登录、验证码、扫码、风控、页面结构不确定时立即停止并返回
  「需人工处理」/「需复核」。

完整契约：[AGENTS.md — 调价安全契约](AGENTS.md#调价安全契约pricing-safety-contract--authoritative)。

### 7. 飞书智能助手

默认 `FEISHU_AGENT_MODE=hermes`：Hermes 网关接管 @机器人 自然语言会话，
后端只处理卡片动作与结构化调价指令。**不要**同时运行旧版 `feishu-listener`
或其他机器人监听同一个飞书应用。

常用问法示例：

```text
@机器人 今天出租率多少？
@机器人 昨天好评几条？
@机器人 竞品最新价
@机器人 最近三天竞品趋势
@机器人 未来15天日历价怎么样
@机器人 上周简报
@机器人 今天三端简报
@机器人 本店今天房型价格
@机器人 竞品有没有明显变化？
@机器人 盯盘服务正常吗？
```

回答基于落库的可信数据；普通提问不会触发浏览器刷新，只有明确说「刷新 /
现在读一次」才会现读。业务人员详细指南：
[docs/feishu-interactive-guide-for-business-users.md](docs/feishu-interactive-guide-for-business-users.md)；
指令与话术对照：[docs/feishu-interaction-phrases-reference.md](docs/feishu-interaction-phrases-reference.md)。

多店场景：一个飞书群覆盖多家门店时，助手会先追问店名再查数，不会把不同
门店的数据混在同一个回答里。

### 8. 定时任务

定时轮询（EBK 读取、竞品价格、日历价、评价抓取、BI 日报、全局推送等）由
后台 worker 进程承载；控制台显示各调度器心跳与下一次轮询时间。修改配置后
按需重启 worker（`scripts\restart-worker.ps1`）。

## 测试与验证

后端（仓库根目录）：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m unittest backend.tests.domain.test_<模块名>
```

Hermes 适配层：

```powershell
$env:PYTHONPATH='pan-hotel-hermes'
.\.venv\Scripts\python.exe -m unittest discover -s pan-hotel-hermes/tests -p 'test_*.py'
```

前端：

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/windows-local-deploy.md](docs/windows-local-deploy.md) | Windows 完整部署指南 |
| [docs/new-machine-init-checklist.md](docs/new-machine-init-checklist.md) | 新机器初始化清单 |
| [docs/configuration-reference.md](docs/configuration-reference.md) | 配置与数据分布参考 |
| [docs/operator-user-guide.md](docs/operator-user-guide.md) | 运营人员操作手册 |
| [docs/feishu-interactive-guide-for-business-users.md](docs/feishu-interactive-guide-for-business-users.md) | 飞书业务人员使用指南 |
| [docs/feishu-interaction-phrases-reference.md](docs/feishu-interaction-phrases-reference.md) | 飞书指令话术对照 |
| [docs/hermes-profiles-current.md](docs/hermes-profiles-current.md) | Hermes 多 profile 现行配置 |
| [docs/python-environment.md](docs/python-environment.md) | Python / venv 环境说明 |
| [RUNTIME_STORAGE_GUIDE.md](RUNTIME_STORAGE_GUIDE.md) | 运行时文件与磁盘占用说明 |
| [AGENTS.md](AGENTS.md) | 调价安全契约（权威全文）与开发规范 |
| [pan-hotel-hermes/README.md](pan-hotel-hermes/README.md) | Hermes 网关适配层说明 |

`docs/` 下其余文件为历次开发计划、审查与修复记录，作为归档保留。

## 数据与命名规范

- 仓库内的门店名、城市、街道、平台账号 ID、飞书 `chat_id`、webhook 等均为
  示例数据（示例词表：城市 `云州`，街道 `澜江路`，门店 `静里` / `曼馨` /
  `泉吉` / `立瑞`），不对应任何真实店铺或账号；
- 贡献时请沿用上述示例命名，或使用「示例门店」，**不要提交**真实门店名、
  地址、账号、群 ID、webhook、Cookie、浏览器 profile 路径、验证码或截图；
- `.env`、SQLite 数据库、`.runtime/`、本地 Hermes profile 配置、截图预览
  均在 `.gitignore` 中，请保持不入库。

## 设计边界（非目标）

本项目刻意不包含：AI 自动定价决策、AI 定时日报、前端执行按钮、通用 API
执行入口、绕过飞书卡审批的任何 OTA 写入、对直连/未确认账号的自动改价、
演示用经营数据。变价提醒与参考卡在生产环境使用规则化生成，AI 仅用于
自然语言解释，且解释失败时回退规则文案。
