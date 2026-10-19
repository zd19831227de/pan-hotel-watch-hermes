# Pan Hotel Watch x Hermes 接入方案

> **当前实现提醒（2026-07-23）**：本文是集成架构与历史决策记录，不是自然语言路由规范。当前飞书路由以 `client/domain_intent_router.py`、`pre_llm_call` 和 `pre_tool_call` 为准；Hermes 插件 API 目前不能动态替换核心 `tools` 数组，因此不要把本文早期“工具集合自动同步”或“模型只看到目标工具”的描述理解为已实现行为。调价安全契约仍以仓库根目录 `AGENTS.md` 为准。

> 版本: v3.3 | 日期: 2026-07-03 | 仓库: [pan-hotel-watch-hermes](https://github.com/meichungen/pan-hotel-watch-hermes)
>
> 变更记录：
> - v3.2 (2026-06-30)：增加 Hermes 记忆治理、`get_operator_recent_activity`、EBK stale 提示。
> - **v3.3 (2026-07-03)**：bfa95e2 之后——`get_operator_hotel_overview` / `get_weekly_operator_briefing` 融合 session chat_id（多店 briefing 路由），`feishu_store_resolver` 区分 unbound vs missing chat_id，前端文案拆 `调价审批群 chat_id` 为 `手动调价指令群` / `价格变化提醒群` / `运营问答上下文群`；fb10e03 之后——`create_manual_pricing_approval` MCP schema/事件 payload 支持 `store_hint`，`handle_manual_price_adjustment_command` 通过 `_resolve_manual_pricing_target` 按 `store_hint` 选 EBK 调价账号。
>
> 本目录维护 Hermes 适配层。MCP Server **不嵌入**后端进程；在本 monorepo 中，
> watch 后端会为 Hermes 对话补充少量 `/api/v1/ops/*` 聚合接口与运营卡片投递能力，
> 但浏览器、持久化、调价链仍全部在后端 Worker 内执行。

---

## 0. 本文档与旧方案的区别

| 维度 | 旧方案 (v2.0) | 本方案 (v3.x) |
|------|--------------|--------------|
| MCP 传输 | stdio，MCP Server 嵌入后端进程 | stdio，MCP Server 是**独立项目**，通过 HTTP 调后端 REST API |
| 后端改动 | "非零修改"：新增 `backend/app/mcp_server.py` + 4 个审批 MCP 工具 | **MCP 层零嵌入**：不新增后端 MCP 进程；可补充 `/api/v1/ops/*` 等 Hermes 配套 REST |
| 审批回调 | 在后端新增 `pricing_record_decision` 等 MCP 工具 | 用 `dispatch_watch_feishu_event` 转发原始飞书事件到后端 `/api/v1/feishu/events` |
| `read_ebk_datasets` | 进程内调用 `DataCenterWatchService` 实时读浏览器 | HTTP 调 `POST .../sessions/{id}/read-dataset` 按需现读；完整盯盘用 `run_ebk_watch_once` |
| 实现状态 | 纯设计稿 | 本仓库已有可运行代码 |

旧方案的核心问题是假设 MCP Server 必须与后端同进程才能复用 `AgentToolRegistry`。实际代码
采用了一种更干净的解法：**MCP Server 作为独立 HTTP 客户端**，通过 REST 访问后端能力，
不在 MCP 进程内启动浏览器。

---

## 1. 执行摘要

### 1.1 目标

将 Pan Hotel Watch 后端的飞书交互入口从内置 `AiAgentRunner` LLM 循环切换到 Hermes
Agent，由 Hermes 提供意图理解和工具调度，后端只负责浏览器读取、数据持久化、调价审批链和
定时轮询。飞书作为唯一用户交互入口。

### 1.2 已有实现

本仓库是一个**独立 Python 项目**（GitHub: `meichungen/pan-hotel-watch-hermes`），已包含：

- **MCP Server** (`pan_hotel_mcp/server.py`)：18 个 MCP 工具，通过 stdio 与 Hermes 通信（以 `tests/test_mcp_tool_contract.py` 为准）
- **HTTP 客户端** (`client/watch_api.py`)：封装后端 `/api/v1/*` REST 调用
- **事件路由** (`router/`)：飞书事件分类——卡片动作和结构化命令转发后端，自然语言交 Hermes
- **Skill 文档** (`skills/pan-hotel-watch/SKILL.md`)：工具选择指南和安全边界
- **测试** (`tests/`)：事件路由和 HTTP 客户端单元测试
- **配置模板** (`config/hermes.config.yaml.example`)：Hermes 注册 MCP Server 的示例

### 1.3 核心判断

| 问题 | 结论 |
|------|------|
| 后端要改吗？ | **MCP 不必嵌入后端。** 浏览器/DB/调价链仍在后端；Hermes 配套可增 `/api/v1/ops/*` 等 REST |
| MCP 工具能覆盖全部场景吗？ | 覆盖读、趋势、周报、状态变更、手动调价审批；卡片回调用 `dispatch_watch_feishu_event` 转发 |
| 审批流能保留吗？ | **能。** 卡片动作转发到后端 `/api/v1/feishu/events`，由后端 `handle_event` 处理 |
| 浏览器会话会冲突吗？ | **不会。** MCP Server 不碰浏览器，浏览器仍由后端 Worker 独占 |
| 飞书卡片能保留吗？ | **部分保留。** 审批/回调卡由后端生成；Hermes 问答一律短文字，不发运营/趋势简报卡；定时盯盘 webhook 告警/参考卡仍由后台推送 |
| 内置 LLM 循环要退役吗？ | 是。Hermes 取代 `AiAgentRunner`，但后端代码不删（保留 `builtin` 回滚） |

---

## 2. 现状架构分析

### 2.1 后端当前调用链

飞书事件全部进入唯一端点 `POST /api/v1/feishu/events`
（`backend/app/api/v1/feishu.py:12`），由 `handle_event(payload)` 统一路由
（`backend/app/domain/notifications/feishu_bot.py:39`）：

```
飞书群 @机器人 发指令 / 点审批卡按钮
  → POST /api/v1/feishu/events
    → handle_event(payload)
      ├─ URL 验证 → 返回 challenge
      ├─ 自定义价格取消/提交/打开 → handle_pricing_custom_price_*
      ├─ 审批卡动作(approve/reject/human_takeover) → handle_pricing_approval_card_action
      │    → record_pricing_approval_decision()
      │    → 若 approve 且 non_direct → queue_pricing_execution_pipeline()
      │    → mark_approval_execute_queued()
      ├─ 手动调价指令("把今天XX调到420") → handle_manual_price_adjustment_command()
      │    → get_pricing_browser_session_for_feishu_chat_id(chat_id)
      │    → create_manual_pricing_preview()
      │    → FeishuBotClient.reply_interactive_card(build_pricing_approval_card())
      ├─ "调价建议"/"调价" → handle_feishu_pricing_command() → 回复说明文字
      └─ 其他 @机器人 消息 → handle_ai_agent_command()
           → run_default_ai_agent()
             → AiAgentRunner.run()  [内置 LLM 对话循环]
               → AgentToolRegistry.execute_tool() → 浏览器/DB
           → FeishuBotClient.reply_text()
```

### 2.2 后端 9 个 AI Agent 工具

注册在 `backend/app/domain/ai_agent/registry.py` 的 `get_default_agent_tool_registry()`，
实现在 `backend/app/domain/ai_agent/tools.py`：

| # | 工具名 | 风险等级 | 说明 |
|---|--------|---------|------|
| 1 | `get_ebk_latest_result` | `read_only` | 读 EBK 最新持久化盯盘结果，不触发浏览器 |
| 2 | `run_ebk_watch_once` | `state_change` | 立即执行一次 EBK 盯盘并按配置推送飞书 |
| 3 | `read_ebk_datasets` | `read_only` | 立即读 EBK 数据集，结构化返回（不推送） |
| 4 | `get_competitor_latest_prices` | `read_only` | 读竞品最新持久化价格 |
| 5 | `analyze_competitor_price_change` | `read_only` | 基于已持久化竞品价格分析是否需关注 |
| 6 | `get_watch_scheduler_status` | `read_only` | 查 EBK/竞品/飞书监听服务状态 |
| 7 | `pause_watch` | `state_change` | 暂停指定 EBK/竞品盯盘 |
| 8 | `resume_watch` | `state_change` | 恢复指定 EBK/竞品盯盘 |
| 9 | `create_manual_pricing_approval` | `approval_only` | 按房型/日期/目标价创建手动调价审批卡 |

风险等级 `AgentToolRiskLevel = Literal["read_only", "state_change", "approval_only"]`
（`registry.py:11`），不是 LOW/HIGH/CRITICAL。

每个工具的参数模型是 Pydantic BaseModel，`parameters` 字段由
`argument_model.model_json_schema()` 生成（`registry.py:401`），可直接作为
MCP `inputSchema`。

### 2.3 内置 LLM 循环

`backend/app/domain/ai_agent/agent.py` 的 `AiAgentRunner` 是一个完整的 LLM 对话循环：

- 用 `OpenAiCompatibleAgentLlmClient`（`llm.py:38`）调 OpenAI 兼容 API
- 支持 `max_tool_rounds=3` 轮工具调用
- 支持 `AiAgentConversationManager` 多轮对话上下文（按 `chat_id` 隔离）
- `_tool_specs_for_message()` 做意图预过滤：问 EBK 指标时排除 `run_ebk_watch_once`

Hermes 接管后，这个循环由 Hermes Core 的 LLM Agent Loop 替代，工具层不变。

### 2.4 审批与执行管线

**预览生成** — `backend/app/domain/pricing_suggestions/preview.py`
- `create_manual_pricing_preview()`：按房型/日期/目标价创建预览
- `create_pricing_preview_from_alert()`：由竞对轮询触发的自动预览
- 账号策略通过 `pricing_account_policy_from_browser_session()` 解析

**审批卡片** — `backend/app/domain/pricing_approvals/cards.py`
- `build_pricing_approval_card(preview)`：生成审批卡 JSON（"通过"/"拒绝"/"自定义调价"）
- `build_custom_price_form_card(preview)`：自定义价格表单卡
- `build_pricing_approval_fallback_text(preview)`：纯文本降级

**审批决策** — `backend/app/domain/pricing_approvals/service.py`
- `record_pricing_approval_decision(preview_id, decision, source, ...)`：落库
- `mark_approval_execute_queued(approval_record_id)`：标记已入队
- `source="feishu_card"` 的 approve 才触发执行；`source="api"` 只记录不执行

**执行管线** — `backend/app/domain/notifications/pricing_execution_pipeline.py`
- `queue_pricing_execution_pipeline(approval_record_id, chat_id)`：`asyncio.create_task` 后台执行
- 链式：`preflight → dry-run → execute → 结果卡/阻断卡`
- 通过 `FeishuBotClient(chat_id=chat_id).send_interactive_card(chat_id, card)` 推送结果，确保后端按当前群路由到对应 Feishu App

**账号解析** — `backend/app/domain/browser/sessions.py`
- `get_pricing_browser_session_for_feishu_chat_id(chat_id)`：按飞书群 chat_id 查唯一 EBK 账号
- 多个匹配时 `raise ValueError`，无匹配返回 `None`

### 2.5 后端 API 端点

MCP Server 实际调用的后端 REST 端点（核心映射；完整 18 工具见 §3.2）：

| MCP 工具 | 后端 HTTP 端点 | 方法 |
|---------|--------------|------|
| `list_watch_groups` | `/api/v1/ctrip-ebooking/data-center-watch-groups` 等 | GET |
| `resolve_feishu_chat_context` | `/api/v1/ops/feishu-chat-context` | GET |
| `get_operator_watch_configuration` | 多路 GET 聚合 | GET |
| `get_watch_scheduler_status` | `/api/v1/ops/runtime-health` | GET |
| `get_weekly_operator_briefing` | `/api/v1/ops/weekly-briefing` | GET |
| `get_operator_recent_activity` | `/api/v1/ops/operator-recent-activity` | GET |
| `get_ebk_latest_result` | `/api/v1/ctrip-ebooking/data-center-watch-groups/{key}/latest` | GET |
| `read_ebk_datasets` | `/api/v1/ctrip-ebooking/sessions/{id}/read-dataset` | POST |
| `run_ebk_watch_once` | `/api/v1/ctrip-ebooking/data-center-watch-groups/{key}/run-now` | POST |
| `run_competitor_watch_once` | `/api/v1/ctrip-ebooking/competitor-watch-groups/{key}/run-now` | POST |
| `get_competitor_latest_prices` | `/api/v1/ctrip-ebooking/competitor-watch-groups/{key}/latest` | GET |
| `get_competitor_price_trend` | 竞品趋势 API；Hermes 问答只回文字，不发趋势卡 | GET |
| `analyze_competitor_price_change` | `/api/v1/price-change-alerts/latest` | GET |
| `pause_watch` / `resume_watch` | `/api/v1/ctrip-ebooking/data-center-watch-groups/{key}` 或 `.../competitor-watch-groups/{key}` | PATCH |
| `create_manual_pricing_approval` | `/api/v1/feishu/events`（合成飞书消息转发） | POST |
| `dispatch_watch_feishu_event` | `/api/v1/feishu/events`（原始事件转发） | POST |

`get_operator_hotel_overview` 在 MCP 客户端层聚合多个只读端点，不对应单一后端路由。

---

## 3. 已有实现：本仓库结构

### 3.1 目录结构

```
pan-hotel-watch-hermes/
  pan_hotel_mcp/
    __init__.py
    __main__.py          # 入口: python -m pan_hotel_mcp
    server.py            # MCP Server（18 个工具，stdio 传输）
  client/
    __init__.py
    watch_api.py         # 后端 HTTP 客户端 + 飞书消息合成
  router/
    __init__.py          # 导出路由公共接口
    feishu_forward.py    # 事件分类：card action / 结构化命令 → watch，自然语言 → hermes
    forwarder.py         # FeishuEventForwarder：HTTP 转发到后端 /api/v1/feishu/events
  skills/
    pan-hotel-watch/
      SKILL.md           # Hermes Skill 定义
  config/
    hermes.config.yaml.example  # Hermes 注册 MCP Server 示例
  tests/
    test_integration.py  # 事件路由 + HTTP 客户端测试
    test_watch_api.py    # 飞书消息合成测试
  .env.example
  requirements.txt       # httpx, mcp, pydantic
  README.md
```

### 3.2 MCP Server (`pan_hotel_mcp/server.py`)

**18 个 MCP 工具**（以 `tests/test_mcp_tool_contract.py` 为准），覆盖群上下文、读数、趋势、周报、状态变更、审批与飞书事件转发：

| # | MCP 工具名 | 对应后端 / 行为 | 传输方式 |
|---|-----------|-----------------|---------|
| 1 | `list_watch_groups` | 列出 EBK / 竞品组 key | HTTP GET |
| 2 | `resolve_feishu_chat_context` | `GET /api/v1/ops/feishu-chat-context` | HTTP GET |
| 3 | `get_operator_watch_configuration` | 多路 GET 聚合配置 | HTTP GET |
| 4 | `get_watch_scheduler_status` | `GET /api/v1/ops/runtime-health` | HTTP GET |
| 5 | `get_weekly_operator_briefing` | `GET /api/v1/ops/weekly-briefing` | HTTP GET |
| 6 | `get_operator_hotel_overview` | 客户端聚合 EBK / 竞品 / 变化 / 调度 | HTTP GET ×N |
| 7 | `get_operator_recent_activity` | `GET /api/v1/ops/operator-recent-activity` | HTTP GET |
| 8 | `get_ebk_latest_result` | `GET .../data-center-watch-groups/{key}/latest` | HTTP GET |
| 9 | `read_ebk_datasets` | `POST .../sessions/{id}/read-dataset` 按需现读 | HTTP POST |
| 10 | `run_ebk_watch_once` | `POST .../data-center-watch-groups/{key}/run-now` | HTTP POST |
| 11 | `run_competitor_watch_once` | `POST .../competitor-watch-groups/{key}/run-now` | HTTP POST |
| 12 | `get_competitor_latest_prices` | `GET .../competitor-watch-groups/{key}/latest` | HTTP GET |
| 13 | `get_competitor_price_trend` | 趋势 API；Hermes 问答文字说明涨跌，不发趋势卡 | HTTP GET |
| 14 | `analyze_competitor_price_change` | `GET /api/v1/price-change-alerts/latest` | HTTP GET |
| 15 | `pause_watch` | `PATCH` EBK 或竞品组 | HTTP PATCH |
| 16 | `resume_watch` | 同上 | HTTP PATCH |
| 17 | `create_manual_pricing_approval` | `POST /api/v1/feishu/events`（合成飞书消息） | HTTP POST |
| 18 | `dispatch_watch_feishu_event` | `POST /api/v1/feishu/events`（原始事件转发） | HTTP POST |

关键实现细节：

- 工具定义是**手写**的 `Tool(name, description, inputSchema)`，不是从后端
  `AgentToolRegistry` 自动同步。`inputSchema` 是手写 JSON Schema，与后端
  Pydantic 模型的 `model_json_schema()` 输出对齐但略有简化（如去掉了 `$defs`）。
- `call_tool()` 用 if-elif 分发到 `WatchApiClient` 的对应方法。
- `create_manual_pricing_approval` 的特殊处理：它不直接调后端 preview API，而是
  **合成一条飞书 `im.message.receive_v1` 事件**（`build_manual_pricing_feishu_payload()`），
  转发到 `/api/v1/feishu/events`，由后端 `handle_manual_price_adjustment_command()` 处理。
  这样复用了后端完整的 chat_id 账号解析 → 预览 → 审批卡逻辑。
- `read_ebk_datasets` 通过 `POST .../read-dataset` **按需现读**指定 EBK 数据集，
  不发送 webhook 推送，也不改变定时盯盘配置。完整 EBK 盯盘仍用 `run_ebk_watch_once`。
- `get_competitor_price_trend` 只读趋势数据；Hermes 问答强制短文字，不发趋势卡。
  （PNG 折线组装代码仍可本地调试，但不作为飞书问答投递路径。）
- Hermes 触发的 `run_competitor_watch_once` / `run_calendar_price_watch_once` 强制
  `push_to_feishu=false`，避免聊天刷新顺带推 webhook 简报卡。

### 3.3 HTTP 客户端 (`client/watch_api.py`)

`WatchApiClient` 类封装所有后端 REST 调用：

- 基础 URL 从环境变量 `WATCH_API_BASE_URL` 读取；当前本机部署使用 `http://127.0.0.1:8010`
- 超时 120 秒（适配长耗时浏览器读取）
- 每次请求创建新的 `httpx.AsyncClient`（无连接池复用）
- `build_manual_pricing_feishu_payload()`：把 room_type/date_label/target_price 合成为
  `"把今天高级大床房调到420"` 格式的飞书消息 JSON，header.event_type 设为
  `im.message.receive_v1`

### 3.4 事件路由 (`router/`)

**`feishu_forward.py`** — 事件分类逻辑：

```python
def should_forward_to_watch(payload) -> bool:
    # 以下事件必须转发到后端 /api/v1/feishu/events：
    # 1. URL 验证 (url_verification)
    # 2. 卡片动作 (card.action.trigger)
    # 3. 结构化命令：精确匹配 "调价建议"、"调价"、"把今天XX调到XXX"
    # 其余自然语言消息交 Hermes Agent 处理

def route_target(payload) -> str:
    return "watch" if should_forward_to_watch(payload) else "hermes"
```

**`forwarder.py`** — `FeishuEventForwarder` 类：
- `forward(payload)`：HTTP POST 到后端 `/api/v1/feishu/events`
- `route(payload)`：先分类再决定是否转发

路由规则测试（`tests/test_integration.py`）：
- `card.action.trigger` → 转发 watch
- `"把今天高级大床房调到420"` → 转发 watch
- `"@机器人 今天出租率多少？"` → 交 hermes

### 3.5 Skill 文件 (`skills/pan-hotel-watch/SKILL.md`)

当前 Skill 文件是一个**单文件**，包含：
- 安全边界（不泄露凭证、不直写 OTA、调价须审批）
- 飞书事件路由优先级（卡片动作 → `dispatch_watch_feishu_event`）
- 工具选择表（用户意图 → MCP 工具映射）
- 回复风格（简洁中文、面向运营人员）
- 默认 watch_group_key

### 3.6 Hermes 配置 (`config/hermes.config.yaml.example`)

```yaml
mcp_servers:
  pan-hotel-watch:
    command: python
    args: ["-m", "pan_hotel_mcp"]
    cwd: C:/path/to/pan-hotel-watch-hermes
    env:
      WATCH_API_BASE_URL: http://127.0.0.1:8010
      PYTHONPATH: C:/path/to/pan-hotel-watch-hermes
      WATCH_INTERNAL_API_TOKEN: ""

feishu:
  connection_mode: websocket

group_sessions_per_user: true
```

关键点：
- `connection_mode: websocket` — 飞书用 WebSocket 长连接，不需要公网回调 URL
- `group_sessions_per_user: true` — 每个用户每个群独立会话上下文
- MCP Server 通过 stdio 与 Hermes 通信，通过 HTTP 与后端通信

---

## 4. 目标架构

### 4.1 新调用链

```
飞书群 @Hermes bot 发指令 / 点审批卡按钮
  → Hermes Gateway (飞书 WebSocket 长连接)
    → 事件路由 (router/feishu_forward.py)
      ├─ 卡片动作 / URL验证 / 结构化命令
      │    → dispatch_watch_feishu_event MCP 工具
      │    → HTTP POST /api/v1/feishu/events
      │    → 后端 handle_event() 处理
      │    ← 后端返回 card_callback_response (卡片更新/Toast)
      └─ 自然语言消息
           → Hermes Core (LLM Agent Loop)
             → 意图理解 → Skill 匹配 → MCP Tool 调度
               → stdio → pan_hotel_mcp/server.py
                 → WatchApiClient → HTTP → 后端 /api/v1/*
                 ← JSON 结果
           → 生成飞书文本/卡片回复
    ← 飞书 API 发送回复
  ← 用户收到回复
```

### 4.2 部署拓扑

```
┌──────────────────────────────────────────────────────────┐
│  同一台 Windows Server                                    │
│                                                          │
│  ┌─────────────────────┐  stdio  ┌────────────────────┐  │
│  │ Hermes Agent         │◄───────►│ pan_hotel_mcp      │  │
│  │  gateway (飞书 WS)   │         │  (MCP Server)      │  │
│  │  core (LLM loop)    │         │  WatchApiClient    │  │
│  │  skills/pan-hotel/  │         └────────┬───────────┘  │
│  └─────────────────────┘                  │ HTTP         │
│                                ┌───────────▼───────────┐  │
│                                │ Pan Hotel Watch Backend│  │
│                                │  /api/v1/* (FastAPI)   │  │
│                                │  Playwright Browser    │  │
│                                │  + SQLite/MySQL        │  │
│                                └───────────────────────┘  │
│                                          │                │
│                            ┌─────────────┴──────────┐     │
│                            │  Workers (独立进程)      │     │
│                            │  - 竞品价格轮询          │     │
│                            │  - 价格变化告警          │     │
│                            │  - 定时 EBK 盯盘         │     │
│                            └────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
```

三个进程层各司其职：
- **Hermes Agent**：飞书入口、LLM 意图理解、工具调度
- **pan_hotel_mcp**：MCP 协议适配、HTTP 调用后端
- **Watch Backend + Workers**：浏览器、数据库、调价审批链、定时轮询

### 4.3 后台 Worker 与 Hermes 的关系

| Worker | 作用 | 与 Hermes 的关系 |
|--------|------|-----------------|
| 竞品价格轮询 | 定时采集竞品价格入库，跨阈值触发规则化告警/调价建议/审批卡 | 完全独立，不受影响 |
| 价格变化告警 | 定时评估价格变化并推送 | 完全独立，不受影响 |
| 定时 EBK 盯盘 | 定时读 EBK 数据中心并推送 | 完全独立，不受影响 |
| `feishu_listener` | 接收飞书事件 | **退役**（由 Hermes Gateway 的飞书 WebSocket 取代） |

> 调价建议是**规则化**的、由竞对轮询入库后跨阈值自动触发，不是 LLM 生成，也不随
> @机器人 指令即时评估（对齐 `AGENTS.md` 的 price-change 边界）。

---

## 5. 审批流详解

### 5.1 手动调价审批（用户主动发起）

```
用户在飞书群："@bot 把今天高级大床房调到420"
  → Hermes 收到 im.message.receive_v1
  → router 判定为结构化命令 → 转发到后端 /api/v1/feishu/events
  → 后端 handle_event()
    → parse_manual_price_adjustment_command() 解析出 room_type/date_label/target_price
    → get_pricing_browser_session_for_feishu_chat_id(chat_id) 解析 EBK 账号
    → create_manual_pricing_preview() 生成预览
    → 若 preview_ready → FeishuBotClient.reply_interactive_card(build_pricing_approval_card())
  ← 用户看到审批卡（"通过"/"拒绝"/"自定义调价"）
```

### 5.2 审批卡回调

```
用户点审批卡"通过"按钮
  → Hermes 收到 card.action.trigger
  → router 判定为卡片动作 → dispatch_watch_feishu_event 转发原始 payload
  → 后端 handle_event()
    → parse_pricing_approval_card_action() 解析 value.decision=approve
    → record_pricing_approval_decision(decision=approve, source=feishu_card)
    → 若 approved 且 account_policy_allows_write:
        queue_pricing_execution_pipeline(approval_record_id, chat_id)
        mark_approval_execute_queued(approval_record_id)
  ← 后端后台执行 preflight → dry-run → execute → 结果卡推送飞书群
```

### 5.3 自定义价格流

```
用户点"自定义调价"按钮
  → card.action.trigger, value.action=custom_price
  → 转发后端 → handle_pricing_custom_price_action()
  → 返回 build_custom_price_form_card() (表单卡)
  ← Hermes 将 card_callback_response 投递回飞书（卡片就地更新）

用户输入价格并点"确定调价"
  → card.action.trigger, value.workflow=pricing_custom_price, value.action=submit
  → 转发后端 → handle_pricing_custom_price_submit()
  → create_manual_pricing_preview() 用新价格重建预览
  → 返回 build_pricing_approval_card() (更新为新审批内容)
  ← 卡片就地更新，仍需再次点"通过"才会写入
```

### 5.4 审批卡 value 字段约定

后端 `cards.py` 生成的按钮 value 结构（`feishu_bot.py` 的解析逻辑依赖此结构）：

**审批按钮**（"通过"/"拒绝"）：
```json
{
  "workflow": "pricing_approval",
  "pricing_preview_record_id": 123,
  "preview_id": 123,
  "decision": "approve",
  "execute_after_feishu_approval": true
}
```

**自定义价格按钮**：
```json
{
  "workflow": "pricing_approval",
  "pricing_preview_record_id": 123,
  "preview_id": 123,
  "action": "custom_price",
  "execute_after_feishu_approval": false
}
```

**自定义价格表单提交**：
```json
{
  "workflow": "pricing_custom_price",
  "action": "submit",
  "pricing_preview_record_id": 123,
  "preview_id": 123
}
```

Hermes 收到 `card.action.trigger` 后，通过 `dispatch_watch_feishu_event` 把整个
原始 payload 转发后端，后端自行解析这些 value 字段。Hermes 不需要理解卡片格式。

---

## 6. Skill 设计

### 6.1 当前 Skill 文件

`pan-hotel-watch-hermes/skills/pan-hotel-watch/SKILL.md` 是单文件结构，包含 frontmatter +
安全边界 + 事件路由 + 工具选择表 + 回复风格 + 默认 watch_group_key。

### 6.2 工具选择映射

| 用户意图 | MCP 工具 | 必需参数 |
|---------|---------|---------|
| 问出租率/经营报告/销售数据/收益概览 | `read_ebk_datasets` | `dataset_ids` 或 `metric_keys`（可空） |
| 查 EBK 上次盯盘结果 | `get_ebk_latest_result` | `watch_group_key`（可空） |
| 明确要求推送/跑一次盯盘 | `run_ebk_watch_once` | `watch_group_key`（可空） |
| 查竞品最新价 | `get_competitor_latest_prices` | `watch_group_key`（可空） |
| 问竞品是否需关注/价格变化 | `analyze_competitor_price_change` | `watch_group_key`（可空） |
| 查盯盘服务状态 | `get_watch_scheduler_status` | 无 |
| 暂停/恢复 EBK 或竞品盯盘 | `pause_watch` / `resume_watch` | `watch_kind`（必填） |
| 用户明确给出房型、日期、目标价 | `create_manual_pricing_approval` | `room_type`, `date_label`, `target_price`, `chat_id`, `message_id` |
| 收到卡片动作/URL验证/结构化命令 | `dispatch_watch_feishu_event` | `payload`（原始飞书事件） |

### 6.3 安全边界（Skill 中已声明）

- 不泄露 webhook、cookie、token、密码、浏览器 profile、本地路径或截图路径
- 禁止直接执行 OTA 写价、dry-run 或 execute
- 调价只能创建飞书审批卡，必须由人工在卡片中点击"通过"
- 目标价必须来自用户明确输入，不能由模型自由生成后直写
- `@机器人 调价建议`/`@机器人 调价`：说明价格变化提醒由竞对轮询自动触发，不现场跑评估
- 登录/验证码/扫码/风控/页面异常一律停下，返回 `human_required` 或 `needs_review`

### 6.4 建议增强（不改动现有代码）

当前 Skill 是单文件。如果 Hermes 支持渐进式披露，可考虑拆分为多文件结构：

```
skills/pan-hotel-watch/
  SKILL.md                    # 主清单 + 安全边界 + 事件路由
  ebk-read-datasets.md        # read_ebk_datasets 详细用法
  ebk-watch-once.md           # run_ebk_watch_once 触发条件
  pricing-manual-adjust.md    # create_manual_pricing_approval 完整流程
```

这是 Skill 侧的文档增强，不涉及后端代码改动。

---

## 7. 已知限制与待验证项

### 7.1 `read_ebk_datasets`（已落地）

**历史问题（2026-06 审计）**：早期 MCP 客户端只读 `/latest` 持久化快照，无法回答「现在出租率多少」。

**当前实现（2026-06-30）**：`client/watch_api.py` 的 `read_ebk_datasets` 已对接
`POST /api/v1/ctrip-ebooking/sessions/{platform_account_id}/read-dataset`，按请求的
`dataset_ids` / `metric_keys` **按需现读** EBK 页面，且 `push_to_feishu=false`，不改变
定时盯盘配置。

**与 `run_ebk_watch_once` 的分工**：

- `read_ebk_datasets`：回答具体经营指标（出租率、ADR、RevPAR 等），只读所需数据集；
- `run_ebk_watch_once`：跑监控组内**全部已启用数据集**的完整 EBK 盯盘，可按配置推送飞书。

好评/差评问题仍应走 `get_ebk_latest_result`（读 `/latest` 持久化快照），不要用
`read_ebk_datasets(comment_monitor)` 或 `run_ebk_watch_once` 代替。

### 7.2 MCP 工具定义未与后端自动同步

MCP Server 的工具定义是**手写**的（`server.py:33-153`），与后端
`AgentToolRegistry` 的注册是两套独立定义。后端新增/修改工具时，MCP Server 不会自动更新。

**风险**：参数 Schema 不一致导致调用失败。

**缓解**：定期比对 `server.py` 的 `inputSchema` 与 `registry.py` 的
`argument_model.model_json_schema()` 输出。

### 7.3 HTTP 客户端无连接池

`WatchApiClient` 每次请求创建新的 `httpx.AsyncClient`（`watch_api.py:27`），没有连接池
复用。高频调用时可能有性能开销。

**缓解**：低频交互场景（飞书 @机器人）下影响可忽略；如果后续高频化可改为单例 client。

### 7.4 审批卡回调的双跳路径

卡片动作走 `Hermes → dispatch_watch_feishu_event → HTTP → 后端 /api/v1/feishu/events`
路径，相比直连后端多了一跳。但后端返回的 `card_callback_response`（卡片更新/Toast）
需要 Hermes 正确透传回飞书。

**待验证**：Hermes Gateway 收到 MCP 工具返回的 JSON 后，能否正确将其中的
`card_callback_response` 投递回飞书卡片回调接口。

### 7.5 `feishu_listener` 退役时机

后端 `feishu_listener` Worker（`scripts/run-worker.ps1 -Service feishu-listener`）
当前接收飞书事件。Hermes 接管后需要退役它，但两者不能同时监听同一飞书应用。

**建议**：用第二个飞书应用让 Hermes 与旧入口并存测试，验证通过后再切换。

### 7.6 内置 LLM 配置

后端 `OpenAiCompatibleAgentLlmClient`（`llm.py:38`）从 `get_runtime_config().llm_runtime()`
读取 LLM 配置。Hermes 侧需要独立配置同一 LLM 供应商和模型。后端的 LLM 配置不自动传递到
Hermes。

### 7.7 `_client` 名称冲突（已修复）

v3.0 曾记录 `pan_hotel_mcp/server.py` 中 `_client` 变量与 `def _client()` 同名导致所有工具
调用失败。v3.1 已改为 `_client_instance` + `_get_client()`，并有单元测试覆盖。

---

## 8. 部署步骤

### Phase 0: 基础设施 (1天)

1. **部署 Hermes Agent**：按官方方式安装，`hermes --version` 验证
2. **配置 LLM**：在 Hermes 侧配置与后端相同的 LLM 供应商和模型
3. **配置飞书应用**：
   - 在飞书开放平台复用或新建企业自建应用，获取 App ID / App Secret
   - Hermes Gateway 用 WebSocket 长连接模式（`connection_mode: websocket`），不需要公网回调 URL
   - 订阅 `im.message.receive_v1` 和 `card.action.trigger`
   - 权限：`im:message`、`im:message.group_at_msg`、`im:message.p2p_msg`
4. **启动 Hermes Gateway**：双 Profile 模式使用 `.\pan-hotel-hermes\scripts\start-hermes-gateways.ps1 -Profiles manxin-ai,jingli-ai -Replace`（现行见 [docs/hermes-profiles-current.md](../../docs/hermes-profiles-current.md)；勿再默认启动已迁出的 `quanji-ai`）

### Phase 1: 注册 MCP Server (0.5天)

1. **安装依赖**：

   ```powershell
   cd pan-hotel-watch-hermes
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

2. **验证 MCP Server 独立运行**：
   ```powershell
   $env:WATCH_API_BASE_URL = "http://127.0.0.1:8010"
   $env:PYTHONPATH = (Get-Location)
   python -m pan_hotel_mcp
   # 通过 stdin 发送 tools/list，应返回当前 MCP 工具集（以 tests/test_mcp_tool_contract.py 为准）
   ```

3. **注册到 Hermes**：
   把 `config/hermes.config.yaml.example` 的内容合并到 Hermes 的 `config.yaml`
   → `hermes gateway restart` → `hermes /mcp list` 应显示 pan-hotel-watch 工具集

4. **运行测试**：
   ```powershell
   $env:PYTHONPATH = (Get-Location)
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
   ```

### Phase 2: 飞书 Gateway 对接 (1天)

1. **回调路由验证**：
   - 测试群 `@bot hello` → Hermes Agent 回复
   - 发送 `把今天高级大床房调到420` → 转发后端 → 返回审批卡
   - 点审批卡"通过" → 转发后端 → 执行管线 → 结果卡

2. **审批卡回调透传验证**：
   - 确认 Hermes 能把后端返回的 `card_callback_response` 正确投递回飞书
   - 确认自定义价格表单的"提交"和"取消"能就地更新卡片

3. **执行管线触发验证**：
   - 创建预览 → 发审批卡 → 点"通过" → 后端后台执行 → 飞书群收到结果卡
   - 确认 `FeishuBotClient` 在后端进程内能取到 tenant_access_token

### Phase 3: 端到端测试 (1.5天)

| # | 场景 | 预期 |
|---|------|------|
| 1 | "看出租率" / "今天 ADR 多少" | `read_ebk_datasets` 按需现读 EBK 数据集并返回指标 |
| 2 | "看竞品价格" | `get_competitor_latest_prices` 返回竞品最新价格 |
| 3 | "最近三天竞品趋势" | `get_competitor_price_trend` 短文字说明涨跌（不发趋势卡） |
| 4 | "竞品要不要关注" | `analyze_competitor_price_change` 返回相邻轮询分析 |
| 5 | "上周简报" | `get_weekly_operator_briefing` 返回上一自然周汇总 |
| 6 | "本群绑哪家店"（一群多店） | `resolve_feishu_chat_context` 解析或文字澄清 |
| 7 | "盯盘状态" | `get_watch_scheduler_status` 返回调度心跳（短文字，不发状态卡） |
| 8 | "跑一次盯盘" | `run_ebk_watch_once` 触发读取；结果用短文字回复（不推简报卡） |
| 9 | "暂停/恢复竞品盯盘" | `pause_watch` / `resume_watch` 成功 |
| 10 | "把今天高级大床房调到350" | 转发后端 → 审批卡 |
| 11 | 审批卡点"通过" | 落库 approved → 执行管线 → 结果卡 |
| 12 | 审批卡点"拒绝" | 不执行，记录审计 |
| 13 | "自定义价格" → 输入360 → 提交 | 就地更新审批卡，仍需再次通过 |
| 14 | EBK 会话过期 | human_required |
| 15 | 非白名单用户/群 | 忽略/拒绝 |

### Phase 4: 灰度切换 (1天)

- 用第二个飞书应用让 Hermes 与旧入口并存
- 迁移测试用户到 Hermes 飞书应用
- 验证稳定后全量切换
- 关停后端 `feishu_listener` Worker
- **回滚**：保留 `POST /api/v1/feishu/events` 端点，把飞书回调切回旧应用即可

---

## 9. 安全模型

### 9.1 工具门控

MCP Server 的当前 MCP 工具集按风险分类（以 tests/test_mcp_tool_contract.py 为准）：

| 风险 | 工具 | Hermes 可直接调用 |
|------|------|------------------|
| 只读 | `get_watch_scheduler_status`, `get_ebk_latest_result`, `read_ebk_datasets`, `get_competitor_latest_prices`, `analyze_competitor_price_change` | 是 |
| 状态变更 | `run_ebk_watch_once`, `pause_watch`, `resume_watch` | 是（但 `run_ebk_watch_once` 会推送飞书） |
| 审批 | `create_manual_pricing_approval` | 是（只创建审批卡，不写 OTA） |
| 事件转发 | `dispatch_watch_feishu_event` | 是（卡片动作/结构化命令专用） |

### 9.2 审批门控

- 写入只能经 `预览 → 飞书审批 → 执行管线`
- MCP Server 不暴露任何 dry-run/execute 直达入口
- 后端每步执行都重新校验 `can_auto_write_price(account)`
- `direct`/`unknown` 账号一律 `write_not_allowed`
- 只有 `non_direct` 账号在飞书"通过"后可继续
- 目标价只来自用户明确输入或审批通过的预览，模型不得自由生成

### 9.3 凭证隔离

Hermes 和 MCP Server **不接触**任何 OTA 密码/cookie/browser profile/Feishu secrets。
这些只在后端持久化存储中。MCP Server 只透传后端各 REST 端点（`/latest`、`runtime-health`、
`/feishu/events` 等）对外返回的响应字段，本身不读取也不组装任何凭证。

> 注意：`sanitize_operator_payload` 是后端 **AI Agent 工具结果链路**用的脱敏函数，
> 本方案走的是 REST API 响应，不一定经过同一个函数。因此安全性依赖**各 REST 端点
> 自身的对外响应已不含敏感字段**，而不是统一脱敏。Phase 3 应抽查每个被调用端点的响应，
> 确认无 webhook、cookie、token、profile、截图路径泄露。

### 9.4 飞书白名单

在 Hermes Gateway 配置飞书用户/群白名单：

```yaml
gateway:
  platforms:
    feishu:
      allowed_users: ["ou_xxx1", "ou_xxx2"]
      allowed_chats: ["oc_xxx1"]
```

### 9.5 Windows 运行时

后端的 `configure_windows_subprocess_event_loop_policy()`（`core/asyncio_runtime.py`）
确保 Playwright 在 Windows 上使用 Proactor 事件循环。MCP Server 不涉及浏览器操作，
不需要此设置，但后端 Worker 进程必须保留。

---

## 10. 风险矩阵

| # | 风险 | 影响 | 概率 | 应对 |
|---|------|------|------|------|
| R1 | Hermes 收到 card.action.trigger 后不调 dispatch_watch_feishu_event | 审批闭环断裂 | 中 | Skill 文件中声明最高优先级；Phase 2 多轮测试卡片回调 |
| R2 | Hermes 不透传后端返回的 card_callback_response | 卡片不更新/无 Toast | 中 | Phase 2 专项验证回调透传 |
| R3 | read_ebk_datasets 返回持久化数据而非实时数据 | 用户拿到的不是最新指标 | 高 | Skill 明确说明数据来源；需要实时用 run_ebk_watch_once |
| R4 | MCP 工具定义与后端 Registry 不一致 | 参数校验失败 | 中 | 定期比对 Schema；考虑自动同步 |
| R5 | HTTP 调用后端超时（浏览器读取 60s+） | 工具无响应 | 中 | MCP 客户端 timeout=120s；Hermes 会话模式不阻塞 |
| R6 | feishu_listener 与 Hermes 同时监听同一飞书应用 | 消息重复处理 | 高 | 切换前必须停 feishu_listener |
| R7 | 后端 LLM 配置不传递到 Hermes | Hermes 无法调 LLM | 低 | Hermes 侧独立配置同一供应商 |
| R8 | 非白名单用户通过飞书发指令 | 越权操作 | 低 | Hermes Gateway 飞书白名单 |

---

## 11. 关键决策记录

| ID | 决策 | 理由 |
|----|------|------|
| D1 | MCP Server 作为独立 HTTP 客户端，不嵌入后端进程 | 进程隔离；浏览器 profile 单属主；Hermes 配套 REST 可增但不嵌入 MCP |
| D2 | 用 `dispatch_watch_feishu_event` 转发卡片动作，不在 MCP 层拆解审批逻辑 | 复用后端完整的 handle_event 闭环；避免在 MCP 侧重写审批/执行逻辑 |
| D3 | `create_manual_pricing_approval` 合成飞书消息转发后端 | 复用后端的 chat_id 账号解析 → 预览 → 审批卡全链路 |
| D4 | `read_ebk_datasets` 调 `read-dataset` 按需现读；完整盯盘用 `run_ebk_watch_once` | 指标问答不跑全量盯盘；浏览器仍由后端 Worker 独占 |
| D5 | 飞书用 WebSocket 长连接模式 | 不需要公网回调 URL；适合单机部署 |
| D6 | Hermes 取代内置 AiAgentRunner，但后端代码不删 | 保留回滚能力；切换只需停 feishu_listener + 改飞书回调 |
| D7 | 调价建议是规则化、竞对轮询触发，不是 LLM 生成 | 对齐 AGENTS.md 非目标：AI 调价建议/AI 日报 |

---

## 12. 审计 2026-06（Hermes × 后端深度对接基线）

> 记录日期：2026-06-26。实施前基线，用于回归对照。

### 12.1 测试基线

| 套件 | 结果 |
|------|------|
| `pan-hotel-hermes/tests`（unittest discover） | **51 passed** |
| 竞品轮询 `default_competitor_watch` | `enabled=true`, `auto_poll_enabled=true` |

### 12.2 审计问题（按优先级）

| ID | 问题 | 优先级 | 对应 Step |
|----|------|--------|-----------|
| A1 | `feishu_forward` 不转发歧义改价命令 | P0 | Step 1 |
| A2 | 公开 API 脱敏 `feishu_chat_id`，MCP 无法按群解析 | P0 | Step 2 |
| A3 | 无 chat 级上下文 MCP 工具 | P0 | Step 3 |
| A4 | 路由与 `manual_price_command` 双轨漂移风险 | P0 | Step 1 |
| A5 | `list_watch_groups` 信息过薄 | P1 | Step 4（Skill） |
| A6 | 无 routing parity 自动化测试 | P1 | Step 1 |
| A7 | MCP 多 API 聚合缺契约测试 | P1 | Step 5 |
| A8 | Skill 缺本群上下文 / 多轮指代规则 | P2 | Step 4 |

### 12.3 已完成（审计前）

- `get_operator_watch_configuration`：EBK 账号名 + 竞品酒店清单
- 中文输出约束：SOUL / SKILL / `reply_style_hint`

### 12.4 非目标（实施过程不退化）

- 不改 OTA 写价链、不启内置 `AiAgentRunner` 双跑
- 不重启 worker 停竞品轮询
- 不加前端执行按钮

---

## 13. 记忆与任务基线（2026-06-26）

> 实施前对照：`docs/hermes-memory-tasks-plan.md`

### 13.1 测试基线

| 套件 | 结果 |
|------|------|
| `pan-hotel-hermes/tests` | **83 passed** |
| 竞品轮询 | `enabled=true`, `auto_poll_enabled=true` |

### 13.2 Hermes 平台快照

| 项 | 状态 |
|----|------|
| `group_sessions_per_user` | true |
| `hermes memory status` | built-in only，`memories/` 空 |
| `hermes cron list` | 0 jobs |
| 后端 `AiAgentConversationManager` | hermes 模式闲置 |
