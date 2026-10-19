# Pan Hotel Watch - Hermes 项目开发说明

## 项目概述

酒店价格监控与智能调价系统。后端 FastAPI + Playwright 浏览器自动化，Hermes Agent 作为飞书消息网关，前端 React + Ant Design。

## 技术栈

- **后端：** Python 3.11 / FastAPI / SQLAlchemy (async) / Playwright
- **Agent 网关：** Hermes（每店铺一个独立 profile 进程）
- **前端：** React + Ant Design + Vite
- **数据库：** SQLite（当前）→ MySQL（后续）
- **AI：** DeepSeek / OpenAI 兼容模型，通过 MCP 工具调用后端能力
- **版本管理：** Git（本地仓库，未绑远程）
- **代码审查：** Codex CLI / GitNexus

## 项目根目录结构

```
D:\work\pan-hotel-watch\
├── .env                          # 环境变量（实际值，不提交）
├── .env.example                  # 环境变量模板（参考）
├── AGENTS.md                     # AI Agent 开发规范
├── CLAUDE.md                     # Claude Code 配置
├── README.md                     # 项目说明
├── RUNTIME_STORAGE_GUIDE.md     # 运行时存储说明
├── pan_hotel_watch.db            # SQLite 数据库
├── debug.log                     # 调试日志
│
├── backend/                     # 后端服务
├── frontend/                    # 前端服务
├── pan-hotel-hermes/            # Hermes Agent 网关
├── scripts/                     # 项目级脚本
├── docs/                        # 文档
│   └── review/                  # 代码审查报告
│
├── .runtime/                    # 运行时数据
│   ├── browser-state/           # Playwright 浏览器状态（各平台EBK登录态）
│   │   ├── ctrip-ebooking/
│   │   ├── meituan-ebooking/
│   │   ├── fliggy-ebooking/
│   │   └── browser_sessions.json
│   ├── heartbeats/              # 各调度器心跳
│   ├── logs/                    # 运行日志
│   ├── backups/                 # 备份
│   ├── ngrok/                   # 内网穿透
│   └── operator_pending_actions.json
│
├── .venv/                       # 后端虚拟环境
├── .gitnexus/                   # GitNexus 代码图谱缓存
├── .agents/                     # Agent skills
├── .claude/                     # Claude Code skills
└── .vscode/                     # VS Code 配置
```

## 后端结构（backend/）

```
backend/
├── pyproject.toml               # 项目配置
├── requirements.txt             # 依赖
│
└── app/
    ├── api/v1/                  # API 路由层
    │   ├── feishu.py             # 飞书事件回调入口
    │   ├── config.py             # 配置管理 API
    │   ├── browser_sessions.py   # 浏览器会话管理 API
    │   ├── ops.py                # 运维操作 API
    │   └── competitor_watch.py   # 竞品监控 API
    │
    ├── core/                    # 核心配置
    │   ├── settings.py           # Settings 类（.env 加载）
    │   └── ...
    │
    ├── db/                      # 数据库层
    │   ├── session.py            # DB Session
    │   └── ...
    │
    ├── domain/                  # 业务领域层（核心）
    │   │
    │   ├── ai_agent/            # AI Agent 工具
    │   │   ├── tools.py          # 9 个工具函数（★ Task 2 修改重点）
    │   │   ├── registry.py       # 工具注册和上下文
    │   │   └── llm.py            # LLM 调用封装
    │   │
    │   ├── browser/             # 浏览器自动化
    │   │   ├── sessions.py       # Session Store（★ Task 1/3 修改重点）
    │   │   ├── pool.py           # 浏览器池
    │   │   └── js/              # 注入脚本
    │   │
    │   ├── config/              # 集成配置
    │   │   ├── service.py        # RuntimeIntegrationConfig（★ 全局单例）
    │   │   ├── models.py         # 配置数据模型
    │   │   └── repository.py     # 配置持久化
    │   │
    │   ├── notifications/       # 飞书通知
    │   │   ├── feishu_bot.py     # 飞书机器人（★ Task 5 修改重点）
    │   │   ├── feishu_listener.py # 事件监听器
    │   │   ├── feishu_client.py  # 飞书 API 客户端
    │   │   └── feishu_app_credentials.py # 凭证路由（★ Task 5 修改重点）
    │   │
    │   ├── ops/                 # 运维操作
    │   │   └── feishu_chat_context.py  # chat_id 上下文解析（★ 核心依赖）
    │   │
    │   ├── competitor_prices/   # 竞品价格
    │   │   └── repository.py     # 竞品数据查询
    │   │
    │   ├── ctrip_ebooking_watch/ # 携程 EBK 监控
    │   ├── calendar_price_watch/  # 日历价格监控
    │   ├── ebk_snapshots/         # EBK 快照
    │   ├── pricing_approvals/     # 调价审批
    │   ├── pricing_execution/     # 调价执行
    │   ├── pricing_strategy/     # 调价策略
    │   ├── pricing_suggestions/  # 调价建议
    │   └── price_change_alerts/  # 价格变化告警
    │
    ├── workers/                 # 后台调度任务
    └── tests/                   # 测试
```

## Hermes 网关结构（pan-hotel-hermes/）

```
pan-hotel-hermes/
├── .env.example                 # Hermes 环境变量模板
├── README.md
├── requirements.txt
│
├── config/
│   ├── hermes.config.local.yaml # Hermes 主配置
│   └── profiles/                # 每个店铺的 profile 配置
│       └── <profile>/SOUL.md   # Agent 人设
│
├── hermes_plugin/               # Hermes 插件
│   └── pan-hotel-feishu-session/ # 飞书会话插件（★ 全局 dict 问题）
│       └── __init__.py
│
├── pan_hotel_mcp/               # MCP 服务（★ Task 2 修改重点）
│   └── server.py                # MCP 工具入口
│
├── client/                      # Hermes 客户端
│   ├── feishu_store_resolver.py # 飞书凭证解析
│   └── watch_api.py            # Watch API 客户端
│
├── scripts/
│   ├── start-hermes-gateways.ps1 # 启动脚本（每 profile 一个进程）
│   └── setup-feishu-gateway.ps1  # 配置脚本
│
├── router/                      # 路由
├── skills/                      # 技能文件
└── tests/                       # 测试
```

## 前端结构（frontend/）

```
frontend/
├── package.json
├── vite.config.ts
└── src/
    └── app/
        ├── App.tsx              # 主应用（含 ModelConfigPanel 等）
        └── ...
```

## 关键文件速查

### 多 Agent 修复涉及的核心文件

| 文件 | 路径 | 说明 |
|------|------|------|
| AI 工具 | `backend/app/domain/ai_agent/tools.py` | 9 个工具函数，Task 2 改这里 |
| 工具注册 | `backend/app/domain/ai_agent/registry.py` | AgentToolCallContext 定义 |
| chat 上下文 | `backend/app/domain/ops/feishu_chat_context.py` | chat_id → watch_group_key 解析 |
| Session Store | `backend/app/domain/browser/sessions.py` | EBK 账号管理，Task 1/3 改这里 |
| LLM 配置 | `backend/app/domain/config/service.py` | RuntimeIntegrationConfig 全局单例 |
| 飞书凭证 | `backend/app/domain/notifications/feishu_app_credentials.py` | 凭证路由，Task 5 改这里 |
| 飞书监听 | `backend/app/domain/notifications/feishu_listener.py` | 事件入口 |
| 飞书机器人 | `backend/app/domain/notifications/feishu_bot.py` | 消息处理 |
| MCP 服务 | `pan-hotel-hermes/pan_hotel_mcp/server.py` | MCP 工具入口，Task 2 也要改 |
| 飞书会话插件 | `pan-hotel-hermes/hermes_plugin/pan-hotel-feishu-session/__init__.py` | 全局 dict 问题 |
| 启动脚本 | `pan-hotel-hermes/scripts/start-hermes-gateways.ps1` | 多 profile 启动 |
| 环境配置 | `.env` / `.env.example` | 全局环境变量 |
| 核心配置 | `backend/app/core/settings.py` | Settings 类 |

### 配置文件

| 文件 | 说明 |
|------|------|
| `.env` | 后端环境变量（数据库、LLM、飞书、浏览器等） |
| `pan-hotel-hermes/config/hermes.config.local.yaml` | Hermes 主配置 |
| `pan-hotel-hermes/config/profiles/<profile>/.env` | 每店铺的 Hermes profile 配置 |
| `pan-hotel-hermes/config/profiles/<profile>/SOUL.md` | 每店铺的 Agent 人设 |

## 数据流

```
飞书用户消息
  → Hermes Gateway (per profile, per process)
  → pre_gateway_dispatch hook: 提取 chat_id
  → MCP 子进程: pan_hotel_mcp/server.py
  → POST /api/v1/feishu/events (后端)
  → build_feishu_chat_context(chat_id): 解析 watch_group_key
  → AI Agent 工具 / 调价流程 / 监控查询
  → 响应回飞书群
```

## 开发环境

```bash
# 后端
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8100

# 前端
cd frontend
npm run dev

# Hermes
cd pan-hotel-hermes
.venv\Scripts\activate
hermes -p <profile_name> gateway run

# 测试
cd backend
.venv\Scripts\python.exe -m pytest tests\ -v

# Hermes 测试
cd pan-hotel-hermes
.venv\Scripts\python.exe -m pytest tests\ -v
```

## Git

- 仓库路径：`D:\work\pan-hotel-watch\.git`
- 用户：pan-hotel-watch / pan-hotel-watch@users.noreply.github.com（仓库级匿名身份）
- 当前分支：`feat/ebk-time-dimensions`
- 无远程仓库，所有 commit 在本地
- **修改前务必先 commit 一次快照做备份**

## 审查文档索引

| 文档 | 路径 | 日期 |
|------|------|------|
| 多租户隔离问题报告(旧) | `docs/review/multi-tenant-isolation-issue-report.md` | 2026-08-04 |
| 多租户隔离修复方案(旧) | `docs/review/multi-tenant-isolation-remediation-plan.md` | 2026-08-04 |
| 配置参考说明 | `docs/review/configuration-reference.md` | 2026-08-04 |
| 维护指南 | `docs/review/maintenance-guide.md` | 2026-08-04 |
| 飞书多Agent绑定问题报告(新) | `docs/review/feishu-multi-agent-binding-issue-report.md` | 2026-08-05 |
| 飞书多Agent绑定修复方案(新) | `docs/review/feishu-multi-agent-binding-remediation-plan.md` | 2026-08-05 |
| 执行指令v2 | `docs/review/codex-execution-instructions-v2.md` | 2026-08-05 |
| 多店铺UI设计 | `docs/multi-shop-ui-design.md` | 2026-08-05 |

> 注：2026-08-04 的旧版报告已被 2026-08-05 的新版覆盖，仅作参考。

## 注意事项

1. **不要改 `create_manual_pricing_approval`**（已是正确实现，作为参考模板）
2. **App Secret 不进数据库**，只存在 Hermes profile 的 `.env` 中
3. **`.env` 不提交到 git**，`.env.example` 是模板
4. **浏览器状态目录** `.runtime/browser-state/` 包含各平台 EBK 登录态，不要随意删除
5. **Codex 的 commit 只落本地**，不会推云端
6. **修改前先备份**：`git add -A && git commit -m "backup: before multi-agent fix"`
7. **测试先写失败用例**，确认当前实现有问题，再写修复
