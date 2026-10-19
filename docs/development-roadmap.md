# Pan Hotel Watch · Hermes 开发顺序与路线图

> 来源：`D:\work\docs-src\酒店hermes系统的开发记录表.xlsx`（30 条记录）+ 项目代码实际状态核对
> 整理日期：2026-08-11
> 维护规则：每次开发完成后回填本表「实际状态」列；新增事项追加到对应版本末尾。

---

## 一、版本总览（调整后）

| 版本 | 主题 | 关键词 |
|------|------|--------|
| **V4（当前）** | 文件整合 + 缓存清理 + 收尾加固 | 自包含、瘦身、稳定基线 |
| **V5** | 模型与分析能力 | 备用模型回退、全局/单项分析、推送延迟、报表固化 |
| **V6** | 知识与自学习 | skill 管理、RAG、MCP 整理、数据蒸馏 |
| **V7** | 桌面应用封装 | 统一目录前置完成后封装 |

**顺延规则**：原记录表标 V4 的功能项 → V5；原 V5 → V6；V6/V7 不变。空出 V4 专做「文件整合 + 缓存清理」。

---

## 二、V4 · 文件整合 + 缓存清理（当前版本）

> **为什么优先做**：今天（8/11）连续踩了三个坑，全部根因是「代码改了但不生效」——
> ① 改了仓库插件源码，Hermes 加载的是 C 盘 4 份副本之一；
> ② 改了 `.env` 的鉴权开关，多进程并发覆盖 `browser_sessions.json` 清空账号；
> ③ EBK 卡片 table 超限降级文本。
> 文件散落 + 无缓存治理 = 每次改动都要反复验证是否生效。**V4 的目标是让"改了就生效"成为基线。**

### V4-1 · MCP 服务文件整合（原记录 [22]）

**现状（勘察确认）**
- `pan_hotel_mcp`（MCP 服务）源码在仓库 `pan-hotel-hermes/pan_hotel_mcp/`
- Hermes 实际通过一个手动 `.pth` 文件加载它：
  `C:\Users\Administrator\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages\pan_hotel_mcp.pth`
  内容：`D:\work\pan-hotel-watch\pan-hotel-hermes`
- 没有 `pip install -e`，没有 `setup.py`/`pyproject.toml`，`.pth` 是手写的
- 风险：换机器/换路径/重建 venv 时 `.pth` 丢失，MCP 直接启动崩溃（历史已发生过）

**目标**：用标准 `pip install -e .`（editable install）替代手写 `.pth`
- 给 `pan-hotel-hermes/` 加 `pyproject.toml`，声明 `pan_hotel_mcp` 包
- 安装脚本 `register-hermes.ps1` 改为执行 `pip install -e`
- 迁移期保留 `.pth` 兼容，验证后移除
- 文档 `docs/python-environment.md` 同步更新

**验收**：新机器跑一次 `register-hermes.ps1` → MCP 启动正常，无需手写 `.pth`

---

### V4-2 · Hermes 插件单一来源（原记录 [22][23] 衍生）

**现状（勘察确认）**
- 插件 `pan-hotel-feishu-session` 有 **4 份字节相同的副本**：
  1. 仓库源码 `pan-hotel-hermes/hermes_plugin/pan-hotel-feishu-session/`
  2. `AppData\Local\hermes\plugins\pan-hotel-feishu-session\`
  3. `AppData\Local\hermes\profiles\jingli-ai\plugins\...`
  4. `AppData\Local\hermes\profiles\manxin-ai\plugins\...`
- **全部是纯拷贝，无符号链接**（`LinkType` 为空）
- `register-hermes.ps1` 只同步到全局 `plugins/`，不同步 per-profile
- 今天踩坑：改仓库源码后，gateway 加载 per-profile 旧副本，改动不生效

**目标**：让仓库成为唯一来源，install 目录通过**符号链接**指向仓库
- `register-hermes.ps1` 改为：删除 4 份拷贝，用 `New-Item -ItemType SymbolicLink`（Windows 需开发者模式或管理员）建立链接
- 链接目标：`hermes_plugin\pan-hotel-feishu-session`（仓库）
- 若符号链接不可用（权限），降级为"部署时拷贝 + 启动前校验 hash"脚本
- 同样处理 `skills/pan-hotel-watch/`（记录表未提，但勘察发现 install 副本比仓库多了 `references/`、`templates/`，需先把这些补进仓库）

**验收**：仓库改插件源码 → 重启 gateway → 立即生效，无需手动同步

---

### V4-3 · `.env` 与配置归位（原记录 [23]）

**现状（勘察确认）**
- 飞书密钥/凭据散落在：
  - `AppData\Local\hermes\.env`（全局，9 个 key）
  - `AppData\Local\hermes\profiles\jingli-ai\.env`（14 个 key，含 `WATCH_API_SHARED_SECRET`）
  - `AppData\Local\hermes\profiles\manxin-ai\.env`（18 个 key）
  - 仓库根 `.env`（后端 `FEISHU_APP_CREDENTIALS_JSON` 映射）
- `config.yaml`（13KB）同样每 profile 一份，含 `mcp_servers`、`platforms.feishu.group_rules`
- 这些都 **gitignore**，换机器要手工重建

**目标**：配置可重建，密钥不落仓库
- 仓库 `pan-hotel-hermes/config/` 已有 `feishu-profiles.yaml`（manifest，gitignore）+ `.example`（tracked）
- 整合方向：所有凭据集中在仓库根 `.env`（后端用）+ `feishu-profiles.yaml`（Hermes 用），install 目录的 `.env` 由 `feishu_profiles.py apply` 生成（机制已有，需确保唯一来源）
- 补一份「新机器初始化清单」文档：跑哪些脚本、填哪些密钥

**验收**：新机器按清单操作 → 3 个 profile 的 `.env` + `config.yaml` 自动生成，值正确

---

### V4-4 · 浏览器缓存清理（原记录 [6]）

**现状（勘察确认）**
- `.runtime/browser-state/` 实测 **10.6 GB**（记录表写 9G，已增长）
  - `ctrip-ebooking/`：4.5 GB（10 个 profile 子目录，每个 400-650 MB）
  - `meituan-ebooking/`：5.3 GB
  - `fliggy-ebooking/`：0.8 GB
- 缓存内容：浏览器 profile（Cookie/缓存/截图），**无任何清理/保留机制**（代码 grep 零命中）
- 同目录的 `browser_sessions.json` 是账号登记，今天被并发覆盖清空过

**目标（原记录方向）**：两种模式
1. **自动清理**：可配置保留天数（默认 60 天），系统闲暇时触发（如每天 03:00），清理浏览器 profile 内的缓存文件但**保留登录态**（Cookie/Local State）
2. **手动清理**：前端高级设置按钮，选保留天数后执行

**实现要点**
- 区分「可清理」（Cache/Code Cache/GPUCache/截图）与「必须保留」（Cookies/Login Data/Local State）——登录态不能清
- 清理对象是 profile 内部子目录，不是整个 profile 目录
- 新增后端 `POST /api/v1/browser-sessions/cleanup` 端点（supervisor 鉴权）+ scheduler 定时任务
- 前端高级设置加「缓存管理」面板：显示各 profile 体积、清理按钮、保留天数

**验收**：清理后体积降到 < 2GB，且所有账号登录态不丢

---

### V4-5 · 收尾：记录表状态回填

下列记录表项实际已完成但表里标"未处理"，V4 收尾时回填：

- **[21] 安全风险** → **部分完成**。
  - 已完成（`d308088` + 工作区 admission/service-token 批）：3 层鉴权
    `require_supervisor / require_operator_api_key / watch_service_token`；
    service token（`X-Pan-Hotel-Service-Token` 命中 `WATCH_API_SHARED_SECRET` 放行 operator 级，
    `hmac.compare_digest` 防时序攻击，supervisor 级不放行）；LLM `base_url` 出网地址白名单
    （拦截私网/环回/链路本地，自定义 relay 须登记 `LLM_ALLOWED_HOSTS`）。
  - 剩余项：生产对外暴露前的网络面加固（反向代理/限流/WAF 等）。当前为本地/内网运行，
    `WATCH_API_SHARED_SECRET` 为空时降级为既有 API key 鉴权，**暂无风险**；正式上线前需补齐。
- 补录今天 3 个事件（已记入 §七 已完成项）：
  - EBK 卡片降级修复（`2e90f5d`，飞书 webhook 限流→table 行数降级）
  - 账号清空文件锁修复（`b994114`，`browser_sessions.json` 跨进程文件锁）
  - 鉴权过严配置修复（`.env` `PAN_HOTEL_ENFORCE_API_AUTH=false`，本地放开）

### V4 完成回填（2026-08-11）

V4-1 ~ V4-4 已全部完成，每步独立 commit，全量测试两轮全绿：

| 步骤 | 主题 | commit | 测试 |
|------|------|--------|------|
| V4-Step1 | MCP 标准化（pyproject.toml editable install，删 requirements.txt，删手写 .pth） | `47c32a1` | backend 655 / hermes 408 / frontend 96 |
| V4-Step2a | SKILL.md 合并（新版 4 段骨架补旧版硬约束）+ references/templates 进仓库 | `747cd11` | hermes 408（calibre 21 项） |
| V4-Step2b | 插件/skill 单一来源（register-hermes.ps1 符号链接 + hash 校验 + 降级拷贝） | `2b2b950` | backend 655 / hermes 408 / frontend 96 |
| V4-Step3 | .env 与配置归位 + 新机器初始化清单文档 | `dc51a32` | backend 655 / hermes 408 / frontend 96 |
| V4-Step4a | 浏览器缓存目录审计 + 清理模块 + 审计报告 | `5fe6e8c` | backend 668（+13 新增） |
| V4-Step4b | 清理端点 + scheduler（每天 03:00）+ 前端面板 + 实测清理 4.6GB | `a23bcb6` | backend 671 / hermes 408 / frontend 101 |

实测结果：浏览器缓存总 11GB → 6.0GB（释放 4.6GB，44% 审计确认基准；其余为
`OptGuideOnDeviceModel`/`screen_ai` 等不可清模型）；登录态文件 88/88、非缓存模型 13/13、
根 registry 文件全部保留。详见 `docs/review/browser-cache-audit.md`。

> V4 后续：实测发现 install 副本实际是 **5 份**（含 `quanji-ai` profile，计划只列了 3 份），
> 已一并符号链接。SKILL.md 旧版仅存于 `.pre-single-source` 备份（install 目录外，AppData 内），
> 确认稳定后可删。

---

## 三、V5 · 模型与分析能力（原 V4 顺延）

### V5-1 · 备用模型回退（原记录 [12]，原 V4）

**内容**：某模型高峰超时/故障时，按配置顺序回退到备用模型。
**前置依赖**：需要先有多模型配置 UI（当前 `config/service.py` 只支持单 LLM）。建议 V5 先做多模型配置，再做回退链。

### V5-2 · 全局/单项数据分析（原记录 [16]，原 V4）

**内容**：携程/飞猪总体分析 + 单酒店单项分析。
**前置依赖标注**：SQLite 够用，无数据库升级前置。三个酒店并发量用 SQLite 即可。

### V5-3 · EBK 推送延迟逻辑（原记录 [28]，原 V5）

**内容**：轮询 2 小时一次；人工触发后延时 2 小时；晚上十点定时推送。
**前置依赖**：推送链路稳定运行 1-2 周无降级（今天刚修 EBK 卡片降级 `2e90f5d`）。**此项不得早于 V5 中期。**

### V5-4 · 数据报表格式固化（原记录 [17]，处理中）

**内容**：AI 生成 → 格式确认 → 固化为 SKILL。
**现状**：已在处理中，可继续。

### V5-5 · 数据库升级：暂缓（决定沿用 SQLite，[18] 关闭）

**内容**：决定沿用 SQLite，三个酒店数据量足够。MySQL 升级暂缓，[18] 关闭。
**顺序调整**：无前置（[18] 已关闭）。

---

## 四、V6 · 知识与自学习（原 V5/V6）

### V6-1 · Skill 管理（原记录 [8]，原 V5）
高级管理中手动添加/管理 skill，含 Hermes 总结的 skill。

### V6-2 · RAG 知识库（原记录 [9]，原 V5）
管理 RAG 知识库 + 向量模型配置；审查 Hermes 代码确保 RAG 被调用；RAG 调度测试。

### V6-3 · MCP 调用纠错（原记录 [27]，原 V6）
agent 误调其他 MCP 服务器浪费 token 的问题。

### V6-4 · 自学习/数据蒸馏（原记录 [30]，原 V6）
接入豆包/千问，定时/人工触发，支持链接/文本/图片/视频。

---

## 五、V7 · 桌面应用封装（原记录 [29]）

**前置**：V4（文件整合 + 缓存清理）完成。记录表原注「需先统一文件目录再开发」——这正是 V4。
**内容**：封装为桌面应用，统一文件位置。

---

## 六、暂不考虑 / 已确认关闭

| 记录 | 内容 | 处理 |
|------|------|------|
| [10] | 长期记忆总结模式 | 暂不考虑 |
| [11] | 未来任务（定时调价） | 暂不考虑 |
| [14] | web 抓取 vs 服务器流 | 已确认延续 web 模式 |
| [15] | 调价仅限携程 | 已确认，飞猪美团关闭 |

---

## 七、已完成项（备查）

| 记录 | 内容 | 完成方式 |
|------|------|---------|
| [3] | API 失效 | 用 deepseek |
| [4] | LLM 密钥失效 | 已更新 |
| [5] | 登录信息过期 | 已拿账号（曼馨未登录） |
| [7] | 竞品跳转 | `4e6cc22` serialize reads |
| [13] | 超时 20s | 延到 30s |
| [19] | 多 agent 并行 | 多 profile gateway |
| [20] | 设置加密 | supervisor key |
| [24] | Hermes 401 | 更新为新 API |
| [26] | 飞猪登录态 | 已修 |
| [21] | 安全风险 | **部分完成**：3 层鉴权 + service token + LLM 出网白名单（`d308088`）；剩余=上线前网络面加固（见 V4-5） |
| [22] | MCP 文件整合 | V4-Step1 `47c32a1`：pyproject.toml editable install，删 requirements.txt 与手写 .pth |
| [23] | .env/config 归位 | V4-Step3 `dc51a32`：新机器初始化清单；install .env 由 feishu_profiles.py apply 唯一生成 |
| [6] | 缓存清理 | V4-Step4 `5fe6e8c`+`a23bcb6`：审计+清理模块/端点/scheduler/前端面板，实测释放 4.6GB，登录态保留 |
| —（V4 衍生）| 插件/skill 单一来源 | V4-Step2 `747cd11`+`2b2b950`：SKILL.md 合并 + references/templates + 5 副本符号链接 |
| —（表外）| EBK 卡片降级 | `2e90f5d`（8/11） |
| —（表外）| 账号清空 | `b994114` 文件锁（8/11） |
| —（表外）| 鉴权过严 | `.env` ENFORCE=false（8/11） |
| —（表外）| admission open_id 选择器 | `4b0e798`（8/11） |
| —（表外）| Q&A 卡片化 | `4194542`（8/11） |

---

## 八、未完成项全览（按新版本排序）

| 新版本 | 记录 | 内容 | 备注 |
|--------|------|------|------|
| **V4** | [22] | MCP 文件整合（`pip install -e`） | ✅ 已完成 V4-Step1 `47c32a1`（见 §七） |
| **V4** | [23] | .env/config 归位 | ✅ 已完成 V4-Step3 `dc51a32`（见 §七） |
| **V4** | [6] | 缓存清理（10.6GB） | ✅ 已完成 V4-Step4 `5fe6e8c`+`a23bcb6`（见 §七） |
| **V4** | — | 插件单一来源（4副本→符号链接） | ✅ 已完成 V4-Step2 `747cd11`+`2b2b950`（见 §七；实测 5 副本） |
| **V4** | [21] | 安全（状态回填为部分完成） | ⚠️ 部分完成（见 V4-5 / §七），剩余=上线前网络面加固 |
| **V5** | [12] | 备用模型回退 | 前置：多模型配置 |
| **V5** | [16] | 全局/单项分析 | SQLite 够用 |
| **V5** | [18] | SQLite→MySQL | 关闭（沿用 SQLite） |
| **V5** | [28] | 推送延迟逻辑 | 前置：推送稳定 1-2 周 |
| **V5** | [17] | 报表格式固化 | 处理中 |
| **V6** | [8] | Skill 管理 | |
| **V6** | [9] | RAG 知识库 | |
| **V6** | [27] | MCP 调用纠错 | |
| **V6** | [30] | 自学习/蒸馏 | |
| **V7** | [29] | 桌面封装 | 前置：V4 完成 |
| — | [25] | 指令对照表 | 文档类，可随时补 |
| — | [10][11] | 长期记忆/未来任务 | 暂不考虑 |

---

## 附：勘察数据（V4 输入）

### 文件散落数据
- 插件副本：4 份（仓库 + 全局 + jingli + manxin），字节相同，无符号链接
- MCP 加载：手写 `.pth`（非 `pip install -e`），路径硬编码
- `.env` 散落：4 处（全局 + 3 profile），共 41+ 个 key
- `config.yaml`：每 profile 一份 13KB，含重复的 `mcp_servers` 块

### 缓存体量数据
- 总计 **10.6 GB**（`.runtime/browser-state/`）
  - ctrip-ebooking：4.5 GB（10 个 profile，每个 400-650 MB）
  - meituan-ebooking：5.3 GB
  - fliggy-ebooking：0.8 GB
- 清理机制：**无**（代码 grep 零命中 cleanup/retention/purge）

### 当前提交基线
- 分支：`feat/ebk-time-dimensions`
- V4 前基线：`2e90f5d`（EBK 卡片修复，8/11 11:46）
- V4 完成提交：`47c32a1`（Step1）→ `747cd11`（Step2a）→ `2b2b950`（Step2b）→
  `dc51a32`（Step3）→ `5fe6e8c`（Step4a）→ `a23bcb6`（Step4b）
- 仍待提交工作区改动：凌晨遗留的 admission/service-token/LLM 校验那批
  （见 `docs/review/uncommitted-development-summary-2026-08-11.md`，V4 未触碰，等运营/开发审核后单独提交）
