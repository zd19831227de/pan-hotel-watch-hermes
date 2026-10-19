# 配置与数据分布参考

> 一句话结论：**项目自有数据全部集中在仓库目录**（源码、凭据来源、venv、浏览器登录态、数据库）。
> C 盘只剩两类**外部依赖**：Hermes 安装本体（`%LOCALAPPDATA%\hermes`）和 Playwright Chromium
> 二进制（`%LOCALAPPDATA%\ms-playwright`）。install 目录下的 `.env` / `config.yaml` 是**生成物**，
> 其唯一来源在仓库，重建机器时从仓库即可恢复。
>
> 本文只列**变量名和文件位置**，不含任何真实凭据值。

---

## 1. 数据分布总览

| 类别 | 位置 | 在仓库内? | 说明 |
|------|------|-----------|------|
| 源码 / 模板 / pyproject | `pan-hotel-hermes/`、`backend/`、`frontend/` | ✅ | 改这里全局生效 |
| **后端凭据来源** | 仓库根 `.env` | ✅（gitignore） | 后端所有秘钥/开关的唯一来源 |
| **飞书多 profile 凭据来源** | `pan-hotel-hermes/config/feishu-profiles.yaml` | ✅（gitignore） | manifest，apply 的输入 |
| 后端 venv | `.venv/` | ✅ | 后端 + worker + Playwright 调用 |
| Hermes MCP venv | `pan-hotel-hermes/.venv/` | ✅ | `pan_hotel_mcp` editable install |
| **浏览器登录态 + 缓存** | `.runtime/browser-state/` | ✅ | 10+ GB，含 Cookies/登录态 |
| SQLite 数据库 | `pan_hotel_watch.db` | ✅ | 监控/审批/趋势持久化 |
| Playwright 截图 | `output/` | ✅ | 运行产物 |
| 日志 / PID | `.runtime/logs/`、`.runtime/pids/` | ✅ | 托管进程 |
| editable install 产物 | `pan-hotel-hermes/pan_hotel_mcp.egg-info/` | ✅（gitignore） | pip install -e . 生成 |
| — | — | — | — |
| **Hermes 安装本体** | `%LOCALAPPDATA%\hermes\hermes-agent\` | ❌ C 盘 | 外部产品，安装器装在此 |
| Hermes agent venv | 同上 `\venv\` | ❌ C 盘 | editable install 指向仓库 |
| install `.env`（生成物） | `%LOCALAPPDATA%\hermes\[profiles\<key>\].env` | ❌ C 盘 | **由 apply 生成，勿手改** |
| install `config.yaml`（生成物） | `%LOCALAPPDATA%\hermes\[profiles\<key>\]config.yaml` | ❌ C 盘 | register-hermes 生成 |
| install SOUL / 记忆（生成物） | `%LOCALAPPDATA%\hermes\profiles\<key>\{SOUL.md,memories\}` | ❌ C 盘 | register-hermes 生成 |
| install 插件 / skill | `%LOCALAPPDATA%\hermes\...\{plugins,skills}\...` | 链接→仓库 | V4 起为**符号链接**，内容在仓库 |
| **Playwright Chromium** | `%LOCALAPPDATA%\ms-playwright\`（~5 GB） | ❌ C 盘 | 公共浏览器二进制，可改路径 |

**为什么这两类在 C 盘：**
- Hermes 是独立产品，固定装在 `%LOCALAPPDATA%\hermes`，运行时只认这个位置（install
  `.env`/`config.yaml`/SOUL 必须落在那）。V4 把它的**来源**（manifest + apply）收进仓库，
  所以 C 盘那份是可重建的生成物。
- Playwright Chromium 默认 `%LOCALAPPDATA%\ms-playwright`，可设 `PLAYWRIGHT_BROWSERS_PATH` 改到仓库内（见 §7）。

---

## 2. 配置文件位置速查

| 文件 | 路径 | 用途 | tracked | 谁改 / 谁生成 |
|------|------|------|---------|----------------|
| 后端 `.env` | 仓库根 `.env` | 后端所有秘钥/开关 | ❌ gitignore | 人工填（模板 `.env.example`） |
| 后端模板 | `.env.example` | 变量名 + 注释 | ✅ tracked | 升级时维护 |
| 飞书 manifest | `pan-hotel-hermes/config/feishu-profiles.yaml` | 多 profile 飞书凭据 | ❌ gitignore | 人工填 |
| manifest 模板 | `pan-hotel-hermes/config/feishu-profiles.example.yaml` | 结构示例 | ✅ tracked | 升级时维护 |
| Settings 字段定义 | `backend/app/core/settings.py` | 变量名/默认值/类型 | ✅ tracked | 改字段时维护 |
| Hermes config 模板 | `pan-hotel-hermes/config/hermes.config.yaml.example` | config.yaml 示例 | ✅ tracked | 参考 |
| 浏览器审计报告 | `docs/review/browser-cache-audit.md` | 缓存可清理清单 | ✅ tracked | 审计脚本生成 |
| install `.env`（生成物） | `%LOCALAPPDATA%\hermes\[profiles\<key>\].env` | profile 运行时凭据 | 不入库 | `feishu_profiles.py apply` |
| install `config.yaml`（生成物） | `%LOCALAPPDATA%\hermes\[profiles\<key>\]config.yaml` | MCP/插件/路由 | 不入库 | `register-hermes.ps1` |

---

## 3. 秘钥 / 凭据查询（按系统）

> 查「某个秘钥在哪」：先看下表「位置」列；真实值只在该文件里，不进仓库、不入日志。

### 3.1 后端（仓库根 `.env`）

| 变量 | 用途 | 默认 / 说明 |
|------|------|-------------|
| `PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY` | supervisor API key（高级配置/缓存清理/角色管理） | 空；`X-Pan-Hotel-API-Key` 头 |
| `WATCH_API_SHARED_SECRET` | 服务间 service token（worker/Hermes 调 operator 级 API） | 空=降级为 API key 鉴权；`X-Pan-Hotel-Service-Token` 头 |
| `PAN_HOTEL_ENFORCE_API_AUTH` | 是否强制 API 鉴权 | `true`；本地调试可 `false` |
| `LLM_API_KEY` | LLM（DeepSeek）密钥 | 空；也可从 Web 控制台存 |
| `LLM_ALLOWED_HOSTS` | LLM 出网白名单（见 §4） | 空=仅 `api.deepseek.com` |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 后端直连飞书（非 Hermes 模式时） | 空 |
| `FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY` | 飞书事件验签/加密 | 空 |
| `FEISHU_WEBHOOK_URL` | 后端推送 webhook | 空 |
| `CTRIP_EBOOKING_USERNAME` / `_PASSWORD` | EBK 登录（自动登录用，默认关闭） | 空；`AUTO_SUBMIT_LOGIN=false` |
| `DATABASE_URL` | 数据库连接 | `sqlite+aiosqlite:///./pan_hotel_watch.db` |
| `BROWSER_STATE_DIR` | 浏览器 profile 根 | `.runtime/browser-state`（可改绝对路径） |

### 3.2 Hermes 飞书多 profile（`pan-hotel-hermes/config/feishu-profiles.yaml`）

每个 profile（`jingli-ai` / `manxin-ai` / `quanji-ai`）块下：

| 字段 | 用途 |
|------|------|
| `app_id` / `app_secret` | 该 profile 的飞书自建应用凭证 |
| `group_rules[].chat_id` | 运营问答上下文群（`oc_` 开头） |
| `group_rules[].policy` | 准入策略：`open` / `allowlist` / `disabled` |
| `group_rules[].allowed_users[]` | 白名单 `ou_` open_id |
| `require_mention` | 是否要求 @机器人 |
| `display_name` / `soul` | 机器人称呼 / SOUL 模板路径 |

### 3.3 install `.env`（生成物，**勿手改**）

`feishu_profiles.py apply` 从 manifest + 后端 service token 生成，含该 profile 运行所需映射：
`FEISHU_APP_ID/SECRET`、`FEISHU_CONNECTION_MODE`、`FEISHU_GROUP_POLICY`、
`FEISHU_ALLOWED_CHATS/USERS`、`WATCH_API_BASE_URL`、`WATCH_API_SHARED_SECRET`、
`DEEPSEEK_API_KEY`、`TAVILY_API_KEY` 等。**手改会被下次 apply 覆盖。**

---

## 4. LLM 出网白名单（`LLM_ALLOWED_HOSTS`）

防 supervisor 把 LLM `base_url` 误配到内网/本机（SSRF）。

- **位置**：仓库根 `.env`，变量 `LLM_ALLOWED_HOSTS`
- **格式**：逗号分隔的精确 host 名（如 `api.deepseek.com,relay.internal corp.com`）
- **默认**：空 = **仅允许 `api.deepseek.com`**
- **校验逻辑**：`backend/app/domain/config/service.py`
  - `_validate_llm_base_url` → `_validate_llm_hostname_addresses` → `_is_disallowed_llm_address`
  - 解析 host 的 DNS 地址，拦截私网（10/172.16/192.168）、环回（127.0.0.0/8）、链路本地（169.254）等
  - 自定义 OpenAI 兼容 relay **必须**显式登记到 `LLM_ALLOWED_HOSTS`，且只解析到公网地址
- **改后生效**：改 `.env` → 重启后端 + worker

---

## 5. 运行产物预留天数（可配，不改源码）

两类运行产物的保留天数都走配置：定时任务读 `.env`，手动清理读 API/前端参数。

### 5.1 浏览器网页缓存

- **配置变量**：`BROWSER_CACHE_RETENTION_DAYS`（仓库根 `.env`，默认 **60**）
- **定时自动清理**：每天 03:00 本地，清审计可清理的网页缓存中超过保留天数的（`read_only_scheduler.py` 读 settings）
- **手动 API**：`POST /api/v1/browser-sessions/cleanup` body `min_age_days`（`0`/`null`=清全部审计可清理；正数=清 N 天以上）
- **手动前端**：高级配置 → 缓存管理 → 浏览器网页缓存 → 立即清理

### 5.2 OTA 抓取截图（`output/playwright/*.png`）

- **配置变量**：`PLAYWRIGHT_SCREENSHOT_RETENTION_DAYS`（仓库根 `.env`，默认 **30**）
- **定时自动清理**：每天 03:00 本地，删除超过保留天数的 PNG（非 PNG 文件保留）
- **手动 API**：
  - `GET /api/v1/browser-sessions/screenshot-audit?retention_days=N`（审计：总量 + 可清理量）
  - `POST /api/v1/browser-sessions/cleanup-screenshots` body `retention_days`（`null`=用配置默认；`<=0`=清全部；正数=清 N 天以上）
- **手动前端**：高级配置 → 缓存管理 → OTA 抓取截图 → 保留天数输入框 + 清理按钮

**改保留天数**：改 `.env` 的两个变量 → 重启 worker（定时任务生效）；手动即时清理直接前端输天数，无需重启。

**清理范围**（以 `docs/review/browser-cache-audit.md` 为准）：
- 浏览器：仅删审计标记的 `cleanable_cache`（HTTP/GPU/Service Worker 缓存、指标、崩溃转储）。
- **绝不删**：登录态（Cookies/Login Data/Local State/Web Data）、Chromium 模型
  （screen_ai/OptGuideOnDeviceModel，属业务/核心组件）、未知条目、根目录 `browser_sessions.json` + `.lock`。
- 截图：仅删 `output/playwright/**/*.png` 中超期的；非 PNG（如 visual-checks JSON）保留。

---

## 6. 改配置后如何生效

| 改了什么 | 重启什么 |
|----------|----------|
| 后端 `.env`（鉴权/LLM/数据库/浏览器路径） | 后端 + worker（`scripts/stop-all.bat` → `start-all.bat`） |
| `feishu-profiles.yaml` manifest | `feishu_profiles.py apply` 每个 profile → 重启对应 Hermes Gateway |
| 仓库插件 / Skill 源码 | 重启对应 Hermes Gateway（install 目录是符号链接，即时生效） |
| `pan_hotel_mcp` 源码 | 重启 Hermes Gateway（editable install，无需重装） |
| 浏览器预留天数（代码常量） | 改代码后重启 worker |

---

## 7. 能否把 C 盘残留也搬进仓库？

| 项 | 能否搬 | 说明 |
|----|--------|------|
| install `.env` / `config.yaml` / SOUL / 记忆 | ❌ 不能 | Hermes 运行时强制读 `%LOCALAPPDATA%\hermes`。但它们是**生成物**，源（manifest + apply + 仓库模板）在仓库，可重建。 |
| install 插件 / skill | ✅ 已搬 | V4 起为符号链接，内容已在仓库。 |
| Hermes 安装本体（hermes-agent venv） | ❌ 不建议 | 独立产品，随 Hermes 安装器走；`pan_hotel_mcp` 已 editable 指向仓库。 |
| Playwright Chromium（~5 GB） | ✅ 可选 | 在 `.env` 设 `PLAYWRIGHT_BROWSERS_PATH=<仓库内路径>`，重装 Chromium。`start-all.ps1` 会处理该变量。代价：仓库体积 +5 GB、浏览器二进制不再多项目共享。 |

---

## 附：相关文档

- `docs/new-machine-init-checklist.md` — 新机器从 0 起的初始化流程
- `docs/python-environment.md` — 三个 venv 的用途与切换
- `docs/feishu-config-ref.md` — 飞书 Session 插件与 config 数据模型
- `docs/review/browser-cache-audit.md` — 浏览器缓存可清理清单
- `AGENTS.md` §「调价安全契约」— 写价链路与硬规则
