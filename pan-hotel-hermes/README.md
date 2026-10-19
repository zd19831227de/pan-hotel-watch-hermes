# pan-hotel-watch-hermes

> 当前路由权威说明：`client/domain_intent_router.py` + 飞书插件的 `pre_llm_call` / `pre_tool_call`。本阶段是“首轮上下文约束 + 执行前硬门禁”，不是动态裁剪 Hermes 核心 `tools` schema。不要把早期专项 router 或旧计划中的“模型只看到一个工具”当作当前实现。

Pan Hotel Watch 的 **Hermes 适配层**。通过 HTTP 调用 watch 后端 `/api/v1/*` 接口；
MCP Server 本身**不嵌入**后端进程，也**不直接**操作浏览器。

在本 monorepo 中，`pan-hotel-hermes/` 与 `backend/` 同仓维护。Hermes 对话所需的
运营聚合接口（如 `/api/v1/ops/feishu-chat-context`、`/weekly-briefing`、运营卡片投递）
由后端提供；MCP 层仍只通过 REST 访问，不在 MCP 进程内跑 Playwright。

- GitHub: https://github.com/meichungen/pan-hotel-watch-hermes
- 接入方案: [docs/hermes-integration-plan.md](docs/hermes-integration-plan.md)
- 调价安全契约（全文在 watch 主仓库）: [../AGENTS.md#调价安全契约](../AGENTS.md#调价安全契约pricing-safety-contract--authoritative)
- 运营人员指南: [../docs/operator-user-guide.md](../docs/operator-user-guide.md)
- 能力口径（Hermes Skill，与代码对齐）: [skills/pan-hotel-watch/SKILL.md](skills/pan-hotel-watch/SKILL.md)
- 现行双 Profile（曼馨 / 静里）: [../docs/hermes-profiles-current.md](../docs/hermes-profiles-current.md)

## 架构

```text
Feishu → Hermes Gateway → pan_hotel_mcp (MCP tools) → pan-hotel-watch API
                         → router/feishu_forward → POST /api/v1/feishu/events
```

**不要** 与 pan-hotel-watch 的 `feishu-listener` worker 同时监听同一飞书应用。

## 前置条件

1. [pan-hotel-watch](https://github.com/meichungen/pan-hotel-watch) 后端已启动（默认 `http://127.0.0.1:8000`）
2. 已安装 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 并完成 LLM 配置
3. Python 3.11+（见 watch 主仓库 [docs/python-environment.md](../docs/python-environment.md)）

## 快速开始

在 **monorepo 根目录** 执行 `.\scripts\install.ps1` 会自动用根 `.venv` 的 Python
创建 `pan-hotel-hermes/.venv`。不要单独用系统 `python`（可能是损坏的 3.13）建 venv。

单独 clone 本目录时：

```powershell
git clone https://github.com/meichungen/pan-hotel-watch-hermes.git
cd pan-hotel-watch-hermes

Copy-Item .env.example .env
# 用 Python 3.11 创建 venv，例如：py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

$env:WATCH_API_BASE_URL = "http://127.0.0.1:8000"
$env:PYTHONPATH = (Get-Location)

# 验证 MCP Server 能启动（stdio，Ctrl+C 退出）
.\.venv\Scripts\python.exe -m pan_hotel_mcp
```

## 注册到 Hermes

1. 将 [config/hermes.config.yaml.example](config/hermes.config.yaml.example) 中的 `mcp_servers` 段合并进 Hermes 的 `config.yaml`
2. 把 `cwd` / `PYTHONPATH` 改成你本机 clone 路径
3. 将 [skills/pan-hotel-watch/SKILL.md](skills/pan-hotel-watch/SKILL.md) 复制到 Hermes skills 目录（或按 Hermes 文档引用）
4. 将 [config/hermes.SOUL.md](config/hermes.SOUL.md) 复制到 `%LOCALAPPDATA%\hermes\SOUL.md`（飞书简体中文输出约束）
5. `hermes gateway restart`，用 `hermes /mcp list` 确认出现 `pan-hotel-watch` 工具集

或一键执行：`.\scripts\register-hermes.ps1`（会同步 Skill、SOUL、MCP 与 `display.language: zh`）

普通 Hermes 飞书回复由 `pan-hotel-feishu-session` 插件包装成应用机器人当前群卡片；查询工具透传 `chat_id`，`message_id` 仅兼容。

```yaml
mcp_servers:
  pan-hotel-watch:
    command: C:/path/to/pan-hotel-watch-hermes/.venv/Scripts/python.exe
    args: ["-m", "pan_hotel_mcp"]
    cwd: C:/path/to/pan-hotel-watch-hermes
    env:
      WATCH_API_BASE_URL: http://127.0.0.1:8010
      PYTHONPATH: C:/path/to/pan-hotel-watch-hermes
```

`command` 请写 **绝对路径** 到 `pan-hotel-hermes/.venv/Scripts/python.exe`，不要写裸 `python`。详见 [docs/python-environment.md](../docs/python-environment.md)。

## MCP 工具一览

### Hermes 飞书路由边界

- 三端 / 三 OTA / 各平台经营简报：`get_ebk_joint_analysis_context`
- EBK 指标、出租率、销售数据：`read_ebk_datasets`，必要时读取 `get_ebk_latest_result`
- 竞对现价：`get_competitor_latest_prices`
- 竞对趋势、涨跌幅、历史变化：`get_competitor_price_trend`
- 竞对变化提醒：`analyze_competitor_price_change`
- 本店房型价格：读取持久化竞对现价，不使用日历价工具
- 明确刷新才调用 `run_*_watch_once`；普通问答不会自动刷新浏览器

明确的天气、翻译、网页搜索等非酒店问题由 Hermes 自身能力处理；无法分类的酒店问题应先澄清，不得猜测 EBK 或竞对数字。

工具集合以 `tests/test_mcp_tool_contract.py` 的 `EXPECTED_TOOL_NAMES` 为准：

| 工具 | 用途 / watch 接口 |
|------|-------------------|
| `list_watch_groups` | 列出 EBK / 竞品监控组 key |
| `resolve_feishu_chat_context` | `GET /api/v1/ops/feishu-chat-context`；一群一店 / 多店解析 |
| `get_operator_watch_configuration` | EBK/竞品组详情 + `/hotels` + `/browser-sessions` |
| `get_watch_scheduler_status` | `GET /api/v1/ops/runtime-health` |
| `get_weekly_operator_briefing` | `GET /api/v1/ops/weekly-briefing`（按需周报，非定时推送） |
| `get_operator_hotel_overview` | 聚合 EBK / 竞品 / 变化 / 调度状态，发运营概览卡 |
| `get_operator_recent_activity` | `GET /api/v1/ops/operator-recent-activity` |
| `get_ebk_latest_result` | `GET .../data-center-watch-groups/{key}/latest`（持久化快照） |
| `read_ebk_datasets` | `POST .../sessions/{id}/read-dataset` 按需现读 EBK 数据集（不推送） |
| `run_ebk_watch_once` | `POST .../data-center-watch-groups/{key}/run-now`（完整 EBK 盯盘） |
| `run_competitor_watch_once` | `POST .../competitor-watch-groups/{key}/run-now` |
| `get_competitor_latest_prices` | `GET .../competitor-watch-groups/{key}/latest` |
| `get_competitor_price_trend` | 竞品历史趋势；Hermes 问答只回文字，不发趋势卡 |
| `get_calendar_price_latest` | `GET /api/v1/calendar-price-watch/{key}/latest`（未来日历价持久化矩阵，只读） |
| `get_calendar_price_watch_status` | `GET /api/v1/calendar-price-watch/{key}/config` + `/latest` + `/scheduler-status`（日历价状态、自动轮询和间隔，只读） |
| `run_calendar_price_watch_once` | `POST /api/v1/calendar-price-watch/{key}/run-now`（明确刷新时现读；Hermes 强制 `push_to_feishu=false`） |
| `analyze_competitor_price_change` | `GET /api/v1/price-change-alerts/latest`（相邻轮询口径） |
| `pause_watch` / `resume_watch` | `PATCH` 对应 watch group |
| `create_manual_pricing_approval` | 合成飞书消息 → `POST /api/v1/feishu/events` |
| `dispatch_watch_feishu_event` | `POST /api/v1/feishu/events`（卡片回调 / 结构化命令） |

工具选择与飞书回复规范见 [skills/pan-hotel-watch/SKILL.md](skills/pan-hotel-watch/SKILL.md)。

## 目录

```text
pan-hotel-watch-hermes/
  pan_hotel_mcp/              # MCP Server（stdio；工具集合以契约测试为准）
  client/
    watch_api.py              # watch HTTP 客户端
    feishu_cards.py           # 运营 / 趋势 / 竞品卡片
    competitor_trend_chart.py # 趋势 PNG 折线图
    weekly_briefing.py        # 周报卡片 enrichment
  router/                     # 飞书事件路由（可选 sidecar）
  skills/pan-hotel-watch/SKILL.md
  config/hermes.SOUL.md
  docs/
  tests/
```

## 测试

```powershell
$env:PYTHONPATH = (Get-Location)
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

## 与 pan-hotel-watch 的关系

| 职责 | pan-hotel-watch (`backend/`) | 本目录 (`pan-hotel-hermes/`) |
|------|------------------------------|------------------------------|
| 浏览器 / Playwright | ✓ | |
| 数据库 / 持久化 | ✓ | |
| 调价审批链 / 执行管线 | ✓ | |
| 定时轮询 Worker | ✓ | |
| 运营聚合 API / 飞书运营卡片投递 | ✓（审批卡与定时 webhook；Hermes 问答不发简报卡） | |
| 飞书入口（Hermes 自然语言） | | ✓ |
| MCP 工具 / Skill（问答短文字） | | ✓ |

切换飞书入口时：默认 `FEISHU_AGENT_MODE=hermes`，停 watch 的 `feishu-listener` worker，
改由 Hermes Gateway 接收飞书事件。详见 [docs/hermes-integration-plan.md](docs/hermes-integration-plan.md)。
