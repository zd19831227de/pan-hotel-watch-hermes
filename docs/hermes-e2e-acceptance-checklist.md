# Pan Hotel Watch × Hermes 运营验收表

> 用途：Hermes Gateway 接管飞书入口后的端到端回归验收。  
> **现行 Profile**：[hermes-profiles-current.md](hermes-profiles-current.md)（曼馨 `manxin-ai` + 静里 `jingli-ai`）。  
> 相关文档：[feishu-open-platform-checklist.md](../pan-hotel-hermes/docs/feishu-open-platform-checklist.md)、[hermes-integration-plan.md](../pan-hotel-hermes/docs/hermes-integration-plan.md) Phase 2–3、[operator-user-guide.md](operator-user-guide.md)。

---

## 历史快照（归档，不作为当前状态）

早期 Agent 预检记录仅用于追溯当时问题，不代表当前进程、工具数量、PID 或验收状态。当前验收必须重新运行下方命令和飞书手测；不要引用历史 PID 或历史工具数量判断系统是否正常。

### 当前结论摘要（每轮验收需重新填写）

| 类别         | 通过 | 待你测 / 待确认 | 问题 |
| ---------- | --- | --------------- | ---- |
| 〇 AUTO     | 待跑 | —               | —    |
| A 基础设施     | 待跑 | 飞书开放平台项需手测 | —    |
| B～H 飞书 E2E | 待测 | 全部             | —    |

**推荐手测群**：与目标 EBK 账号 / 竞品组绑定的群。
**勿先用** 未绑定 `feishu_chat_id` 的 EBK 组，避免把未绑定状态误判为 Hermes 故障。

### 当前环境快照（执行时填写）

| 项                        | 值 |
| ------------------------ | -- |
| 后端地址                     | ______ |
| `FEISHU_AGENT_MODE`      | `hermes` |
| Hermes Gateway           | 以 `.\scripts\status.ps1 -HermesGatewayProfiles manxin-ai,jingli-ai` 当前输出为准 |
| `feishu-listener`        | 应停止，避免与 Hermes 双监听 |
| `FEISHU_CONNECTION_MODE` | `websocket` |
| MCP                      | `pan-hotel-watch` 已注册；工具集合以 `pan-hotel-hermes/tests/test_mcp_tool_contract.py` 为准 |
| EBK 写价测试账号               | ______ |
| 竞品读价                     | ______ |

### 额外自动化验证（未列入下表 ID）

- REST 冒烟：`/health`、`/ops/runtime-health`、`/ops/weekly-briefing`、EBK/竞品 watch-groups、`/price-change-alerts/latest`。
- 路由单测：卡片动作 / 「调到420」→ `watch`；自然语言 → `hermes`。
- MCP 契约 + 集成：`tests.test_mcp_tool_contract` + `tests.test_integration`，工具集合必须包含 `get_calendar_price_latest`、`get_calendar_price_watch_status` 和 `run_calendar_price_watch_once`。

### 双 Profile / 双飞书应用前置检查

| ID | 检查项 | 命令或位置 | 预期 |
|----|--------|------------|------|
| PRE-DUAL-01 | Hermes profiles | `hermes profile list` | 存在 `manxin-ai` 和 `jingli-ai`；默认 profile 不是生产群入口；`quanji-ai` 若仍存在则勿作生产入口 |
| PRE-DUAL-02 | 曼馨允许群 | `%LOCALAPPDATA%\hermes\profiles\manxin-ai\.env` | 只允许 `oc_11111111111111111111111111111111` |
| PRE-DUAL-03 | 静里允许群 | `%LOCALAPPDATA%\hermes\profiles\jingli-ai\.env` | 只允许 `oc_44444444444444444444444444444444` |
| PRE-DUAL-04 | 双 Gateway 状态 | `.\scripts\status.ps1 -HermesGatewayProfiles manxin-ai,jingli-ai` | 两个命名 gateway 都可见；内置 `feishu-listener` 未运行 |
| PRE-DUAL-05 | 后端飞书应用路由 | 后端 `.env` | `FEISHU_CHAT_APP_MAP_JSON` 将两个群分别映射到 `manxin-ai` / `jingli-ai` |
| PRE-DUAL-06 | 调价权限边界 | 控制台账号绑定 | 曼馨 EBK 为 `direct`，静里 EBK 为 `non_direct`；最终以后端 `connection_mode` 为准 |

| ID | 场景 | 飞书操作 | 预期 |
|----|------|----------|------|
| E2E-DUAL-01 | 曼馨上下文隔离 | 在曼馨群问“本群绑定了哪家店？” | 只回答曼馨，不提泉吉/静里；回复来自曼馨应用 |
| E2E-DUAL-02 | 静里上下文隔离 | 在静里群问“本群绑定了哪家店？” | 只回答静里，不提曼馨/泉吉；回复来自静里应用 |
| E2E-DUAL-03 | Memory 隔离 | 检查两个 profile 的 `memories\MEMORY.md` | 任一 MEMORY 都不包含另一个门店的业务事实、chat_id、webhook 或 secret |
| E2E-DUAL-04 | 曼馨直连写价阻断 | 在曼馨群发手动目标价调价命令 | 返回不可自动写价/建议口径，不进入可执行写价链 |
| E2E-DUAL-05 | 静里非直连审批链 | 在静里群用真实可控房型和测试价触发手动调价 | 必须经历目标价、飞书审批、preflight、dry-run、execute；不能由 AI 或 MEMORY 决定价格 |
| E2E-DUAL-06 | 曼馨三端短分析 | `@曼馨 ai 管家 三端联合简报` | `get_ebk_joint_analysis_context`；短综合分析，非长表 |
| E2E-DUAL-07 | 曼馨竞品只读 | `@曼馨 ai 管家 竞品最新价` | 竞品只读工具；不跑写价 / 不误跑完整 EBK 三端发卡 |

---

## 验收元数据


| 字段                           | 填写                                                                |
| ---------------------------- | ----------------------------------------------------------------- |
| 验收日期                         | 2026-06-30（Agent 预检）/ ______（你的手测）                                |
| 验收人                          | Agent 预检 + ______                                                 |
| 环境                           | Windows 单机                                                        |
| 飞书应用                         | 曼馨：`cli_a22222222222222`；静里：本地配置的独立自建应用 App ID                     |
| 测试群 chat_id                  | 曼馨：`oc_11111111111111111111111111111111`；静里：`oc_44444444444444444444444444444444` |
| EBK 浏览器账号                    | 曼馨 `connection_mode=direct`；静里 `connection_mode=non_direct`                |
| 竞品监控组 key                    | 曼馨：`watch_1784518960083`；静里：以控制台当前绑定为准       |
| `.env` 中 `FEISHU_AGENT_MODE` | **hermes** ✅                                                      |
| Hermes Gateway               | **已启动** ✅                                                         |
| `feishu-listener`            | **已停止** ✅                                                         |


**图例**：`✅` 通过 · `❌` 失败 · `⏭` 跳过（写明原因）· `N/A` 前置不满足 · `👤` 待你手测

**Bug 编号**：失败项记 `BUG-001`、`BUG-002`……

---

## 〇、跑测前：自动化基线（约 5 分钟）

> 飞书手测前先确认代码层没红，避免把代码 bug 当成配置问题。


| ID      | 检查项       | 命令                                                                                                                               | 预期                      | 结果  | 备注                                                                  |
| ------- | --------- | -------------------------------------------------------------------------------------------------------------------------------- | ----------------------- | --- | ------------------------------------------------------------------- |
| AUTO-01 | Hermes 单测 | `cd pan-hotel-hermes` `$env:PYTHONPATH=(Get-Location)` `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"` | 全绿                      | ✅   | **135 passed**（须用 `pan-hotel-hermes\.venv`；根目录 `.venv` 缺 `mcp` 会失败） |
| AUTO-02 | 后端服务      | `.\scripts\status.ps1`                                                                                                           | backend + scheduler 运行中 | ✅   | backend PID 25760；worker 运行；EBK/竞品 scheduler heartbeat healthy      |
| AUTO-03 | API 可达    | `http://127.0.0.1:8100/api/v1/ops/runtime-health`                                                                                | 200，含调度心跳               | ✅   | `summary.status=fresh`；`feishu_listener.managed_by=hermes_gateway`  |


---

## A、基础设施前置（全部 ✅ 才能进 B 段）


| ID     | 检查项                 | 操作                               | 预期                                 | 结果  | 备注                                                                                              |
| ------ | ------------------- | -------------------------------- | ---------------------------------- | --- | ----------------------------------------------------------------------------------------------- |
| PRE-01 | 后端 API              | `.\scripts\start-all.bat` 或确认已在跑 | API 正常                             | ✅   | `http://127.0.0.1:8100/api/v1/health` → 200                                                     |
| PRE-02 | 调度 Worker           | `status.ps1`                     | EBK / 竞品 scheduler 有心跳             | ✅   | 两路 scheduler `status=running`，heartbeat healthy                                                 |
| PRE-03 | `FEISHU_AGENT_MODE` | `status.ps1`                     | `hermes`                           | ✅   | 输出 `Feishu agent mode: hermes`                                                                  |
| PRE-04 | 停旧监听                | 确认无 `feishu-listener` 进程         | 不与 Hermes 双监听                      | ✅   | 无 `feishu_listener` python 进程；status 提示由 Hermes 接管                                              |
| PRE-05 | EBK 账号登录            | `GET /api/v1/browser-sessions`   | 状态正常                               | ✅   | `browser-ebk-2` logged_in；`browser-ebk-1` logged_in                                             |
| PRE-06 | 竞品账号登录              | 同上                               | 状态正常                               | ✅   | `browser-trip-1/2` logged_in                                                                    |
| PRE-07 | 飞书群绑定               | 控制台 / API                        | EBK + 竞品组绑测试群                      | ⚠️  | `watch_1782109266413` ✅；`browser-ebk-2` 调价群 ✅；**EBK 组 `ctrip_ebooking_data_center` 未绑 chat_id** |
| PRE-08 | 调价 EBK 绑定           | 竞品组「调价 EBK 账号」                   | 已选正确 EBK                           | ⚠️  | `watch_1782109266413` → `browser-ebk-2` ✅；`**default_competitor_watch` 未选调价 EBK**               |
| PRE-09 | Hermes LLM          | Hermes 侧模型配置                     | 能正常对话                              | ✅   | 需飞书 `@机器人 你好` 验证；                                                                               |
| PRE-10 | 飞书应用发布              | 开放平台                             | 机器人已启用、版本已发布                       | ✅   | 飞书开发者后台启用了                                                                                      |
| PRE-11 | WebSocket           | `%LOCALAPPDATA%\hermes\profiles\<profile>\.env`     | `manxin-ai` / `jingli-ai` 均为 `FEISHU_CONNECTION_MODE=websocket` | ✅   | 双 Profile 分别核对，不看默认 Hermes home                                                                                   |
| PRE-12 | 事件订阅                | 开放平台                             | `im.message.receive_v1` + 卡片动作     | ✅   | 已核对                                                                                             |
| PRE-13 | 权限                  | 开放平台                             | im 消息/群@/发卡片权限                     | ✅   | 已核对                                                                                             |
| PRE-14 | 机器人入群               | 测试群                              | 已添加 Hermes 机器人                     | ✅   | 已核对                                                                                             |
| PRE-15 | 白名单                 | `%LOCALAPPDATA%\hermes\profiles\<profile>\.env`     | 每个 profile 只允许自己的 `FEISHU_ALLOWED_CHATS`                    | ✅   | 曼馨只允许 `oc_xxxx1...`；静里只允许 `oc_xxxx2...`；                                            |
| PRE-16 | MCP 注册              | `config.yaml`                    | pan-hotel-watch 已注册                | ✅   | `mcp_servers.pan-hotel-watch` 存在；`WATCH_API_BASE_URL=8100`                                      |
| PRE-17 | Gateway             | `.\scripts\status.ps1 -HermesGatewayProfiles manxin-ai,jingli-ai`          | 两个命名 Gateway 均运行；default 不作为生产入口                  | 👤   | 每轮验收重新记录当前状态，不沿用历史进程号                                             |
| PRE-18 | MCP 工具              | 契约测试 / `/mcp list`               | 工具集合与 `pan-hotel-hermes/tests/test_mcp_tool_contract.py` 一致，包含日历价工具 | 👤   | 可选在 Hermes 里 `/mcp list` 再确认；不记录旧工具数量                  |


**A 段准出**：PRE-01～18 必须基于本轮命令或手测重新确认；PRE-09～15 需你确认后再进 B 段。

---

## B、Gateway 与路由冒烟（Phase 2）— 👤 待你手测


| ID    | 场景     | 飞书操作                               | 预期（用户可见）                   | 后台验证（可选）                            | 结果  | Bug          |
| ----- | ------ | ---------------------------------- | -------------------------- | ----------------------------------- | --- | ------------ |
| CB-01 | 自然语言   | `@机器人 你好`                          | 简体中文回复，非英文开场；无 `#` 标题、无代码块 | Hermes 日志有 LLM 回合                   | ✅   |              |
| CB-02 | 结构化调价  | `@机器人 把今天高级大床房调到420` （房型名换成库里真实房型） | **审批卡**出现（非纯文字「已调价」）       | 后端 `POST /api/v1/feishu/events` 被转发 | 👤  | 路由单测已通过；飞书待验 |
| CB-03 | 卡片回调透传 | 在 CB-02 卡上点 **拒绝**                 | 卡片状态更新 / Toast；**不**触发写价   | 审批记录 status=rejected                | 👤  |              |
| CB-04 | 自定义价表单 | 若有「自定义调价」→ 输入如 `360` → 提交          | 卡片就地更新；仍须再次确认才执行           | preview 目标价更新                       | 👤  |              |


---

## C、只读查询（Phase 3 · 场景 1～7）— 👤 待你手测

> 在**已绑定监控组**的测试群发。多店群需按提示补店名。建议群：`oc_6***4578`。


| ID     | 场景       | 飞书指令（示例）                      | 预期                                   | 不应出现               | 结果       | Bug                             |
| ------ | -------- | ----------------------------- | ------------------------------------ | ------------------ | -------- | ------------------------------- |
| E2E-01 | EBK 现读指标 | `@机器人 今天出租率多少？`               | 调用现读 EBK；返回出租率等数字 + 时间口径；优先运营卡片      | 编造数字；整段 JSON；英文工具名 | ✅ **P0** |                                 |
| E2E-02 | EBK ADR  | `@机器人 今天 ADR 多少？`             | 同 E2E-01，有 ADR 或明确「暂无/读失败」           | 静默无回复 >2min        | ✅        |                                 |
| E2E-03 | 竞品最新价    | `@机器人 竞品最新价`                  | 最新**持久化** trusted 价格；若需现读应先询问确认      | 假价格                | ✅        |                                 |
| E2E-04 | 竞品趋势     | `@机器人 最近三天竞品趋势`               | **趋势卡**；优先 PNG 折线图；失败则文字趋势块 + 说明非折线图 | 「不支持图表」            | ✅ **P0** |                                 |
| E2E-05 | 竞品变化分析   | `@机器人 竞品要不要关注` / `竞品有没有明显变化`  | 基于相邻轮询的分析结论                          | 现场触发写价审批           | ✅        |                                 |
| E2E-06 | 周报       | `@机器人 上周简报`                   | 上一自然周汇总（按需生成，非定时推送）                  | 自动杜撰未来数据           | ✅        | REST `/ops/weekly-briefing` 200 |
| E2E-07 | 本群上下文    | `@机器人 本群盯着哪家店？`               | 单店：直接回答店名；多店：澄清卡或让用户选店               | 混用多店数据             | ✅        |                                 |
| E2E-08 | 盯盘状态     | `@机器人 盯盘服务正常吗？` / `@机器人 盯盘状态` | EBK/竞品 scheduler 心跳、服务是否正常           | 仅「不知道」             | ✅ **P0** |                                 |
| E2E-ROUTE-01 | 三端口语简报 | `@机器人 今天三端简报` | 只调用 `get_ebk_joint_analysis_context`；返回三平台经营简报 | 分拆成错误的竞品/周报工具；编造数字 | 👤 **P0** | 新增口语覆盖 |
| E2E-ROUTE-02 | 三 OTA 口语简报 | `@机器人 三 OTA 今日经营` | 同 E2E-ROUTE-01 | 只回答单一平台或调用日历价工具 | 👤 | |
| E2E-ROUTE-03 | 本店房型价格 | `@机器人 本店今天房型价格` | 使用持久化可信价格上下文；不调用未来日历价工具 | 把本店价格误路由成日历价或竞品现价卡 | 👤 **P0** | |
| E2E-ROUTE-04 | 非酒店问题隔离 | `@机器人 上海明天天气怎么样？` | 由 Hermes 自身能力回答；不调用 Pan Hotel MCP | 任意 EBK/竞品工具调用 | 👤 | |
| E2E-ROUTE-05 | 失败历史隔离 | 先制造一次失败工具调用，再开新问题“竞品最新价” | 当前问题按最新价路由；失败交换被中性化，不改变工具选择 | 重放失败工具、沿用失败摘要或编造结果 | 👤 **P0** | 需新会话手测 |
| E2E-CAL-01 | 日历价最新 | `@机器人 未来15天日历价怎么样` | 调用 `get_calendar_price_latest`；当前群收到日历价卡片；不出现工具过程消息 | 不创建调价审批或执行链 | 👤 | 只读 |
| E2E-CAL-02 | 日历价刷新 | `@机器人 现在刷新一次未来15天日历价` | 调用 `run_calendar_price_watch_once`；Hermes 默认 `push_to_feishu=false`，当前群收到最终卡片 | 不走竞品 webhook；不创建调价审批 | 👤 | 只读 |
| E2E-CAL-03 | 日历价状态 | `@机器人 日历价是不是设置了自动轮询间隔？` | 调用 `get_calendar_price_watch_status`；当前群收到日历价状态卡片，包含 `auto_poll_enabled`、`schedule_interval_minutes`、上次读取时间 | 不说「没有独立轮询间隔」；不创建调价审批 | 👤 | 只读 |


**C 段准出**：E2E-01、E2E-04、E2E-08、E2E-ROUTE-01、E2E-ROUTE-03、E2E-ROUTE-05 为 P0，必须 ✅。

---

## D、状态变更（Phase 3 · 场景 8～9）— 👤 待你手测

> ⚠️ `run_ebk_watch_once` 会**真实读浏览器并可能推 webhook 卡**，在测试群跑。


| ID     | 场景       | 飞书指令                 | 预期                                           | 结果  | Bug    |
| ------ | -------- | -------------------- | -------------------------------------------- | --- | ------ |
| E2E-09 | EBK 立即盯盘 | `@机器人 跑一次 EBK 盯盘并推送` | 触发 `run_ebk_watch_once`；群或 webhook 收到 EBK 简报 | ✅   |        |
| E2E-10 | 暂停竞品     | `@机器人 暂停竞品盯盘`        | 确认成功；控制台该组 disabled                          | ✅   | 跑完务必恢复 |
| E2E-11 | 恢复竞品     | `@机器人 恢复竞品盯盘`        | 恢复成功；scheduler 可继续轮询                         | ✅   |        |


**D 段备注**：E2E-10 / E2E-11 跑完后务必 **恢复竞品盯盘**，避免影响生产轮询。

---

## E、调价路由与安全边界（Phase 3 · 场景 10 + 调价建议）— 👤 待你手测


| ID     | 场景         | 飞书指令                             | 预期                 | 结果        | Bug                |
| ------ | ---------- | -------------------------------- | ------------------ | --------- | ------------------ |
| E2E-12 | 手动调价命令     | `@机器人 把今天{真实房型}调到{合理价格}`         | **审批卡**（房型/日期/目标价） | 👤 **P0** | 用 non_direct 账号群测  |
| E2E-13 | 调价建议拒跑     | `@机器人 调价建议`                      | 说明竞对轮询自动触发；不出审批卡   | ✅ **P0**  |                    |
| E2E-14 | 调价别名       | `@机器人 调价`                        | 同 E2E-13           | ✅         |                    |
| E2E-15 | 直连/未确认账号卡面 | 在 `unknown` 账号（如 ebk-1）下测 E2E-12 | 不可自动写价；无「通过」       | ✅         | ebk-1=`unknown` 可用 |
| E2E-16 | 非直连参考卡口径   | 竞对降价参考卡                          | 仅「自定义调价」「暂不处理」     | 👤        | 可选；需轮询跨阈值          |


---

## F、审批闭环（Phase 3 · 场景 11～13）— 👤 待你手测（`non_direct`）

> **前置已满足**：`browser-ebk-2` · `non_direct` · logged_in。在**可控**房型/日期/价格上测。


| ID     | 步骤   | 操作                    | 预期                            | 结果  | Bug |
| ------ | ---- | --------------------- | ----------------------------- | --- | --- |
| E2E-17 | 创建预览 | `@机器人 把今天{房型}调到{测试价}` | 审批卡出现                         | 👤  |     |
| E2E-18 | 拒绝   | 点 **拒绝**              | 不执行写价；有审计记录                   | 👤  |     |
| E2E-19 | 重新创建 | 再发一条手动调价命令            | 新审批卡                          | 👤  |     |
| E2E-20 | 自定义价 | 自定义调价 → `360` → 提交    | 卡片更新；仍需二次确认                   | 👤  |     |
| E2E-21 | 确认执行 | 点执行确认                 | preflight → dry-run → execute | 👤  |     |
| E2E-22 | 结果卡  | 等待 1～3 分钟             | 结果卡（成功 / 需要人工处理 / 需要复核）       | 👤  |     |
| E2E-23 | 登录阻断 | EBK 掉线时               | `human_required`              | 👤  | 可选  |


---

## G、边界与异常（Phase 3 · 场景 14～15）— 👤 待你手测


| ID     | 场景         | 如何触发                  | 预期                          | 结果  | Bug                                       |
| ------ | ---------- | --------------------- | --------------------------- | --- | ----------------------------------------- |
| E2E-24 | EBK 会话过期   | 退出 EBK 后问出租率          | `human_required`；不编造指标      | 👤  |                                           |
| E2E-25 | 非白名单用户     | 非白名单账号 @机器人           | 忽略或拒绝                       | ✅   |                                           |
| E2E-26 | 非白名单群      | 群不在 `ALLOWED_CHATS`   | 同 E2E-25                    | ✅   | 若已配 ALLOWED_CHATS                         |
| E2E-27 | profile 冲突 | 手动开 Ctrip + 触发 EBK 现读 | 可读提示，非 raw 堆栈               | 👤  |                                           |
| E2E-28 | 敏感信息泄露     | 抽查回复 + 日志             | 无 webhook/cookie/profile 路径 | 👤  | runtime-health `secrets_included=false` ✅ |


---

## H、竞对轮询自动链路（可选）— 👤 待你手测


| ID    | 步骤       | 操作              | 预期            | 结果  | Bug                           |
| ----- | -------- | --------------- | ------------- | --- | ----------------------------- |
| PX-01 | 两次成功 run | 手动读或等 scheduler | 两轮 trusted 价格 | 👤  | scheduler 当前 tick 无 due group |
| PX-02 | 跨阈值      | 价格变化超阈值         | 价格变化提醒        | 👤  |                               |
| PX-03 | 降价参考卡    | 竞对降价            | 仅自定义/暂不处理     | 👤  |                               |
| PX-04 | 涨价保持链路  | 竞对涨价            | 保持现有链路：推送价格变化卡片 → 调价 → preview → 审批 → 执行 → 回填（**不**改为 alert-only，详见 dev-plan-v3 Step 4） | 👤  | 与 dev-plan-v3 Step 4「保持现有业务逻辑」一致 |
| PX-05 | 自定义后执行   | 自定义 → 确认 → 执行   | 同 F 段         | 👤  |                               |


---

## I、验收记录模板（失败必填）


| Bug ID  | 对应用例            | 复现步骤                                 | 实际结果                                                 | 预期结果                   | 优先级    | 截图/日志                                              |
| ------- | --------------- | ------------------------------------ | ---------------------------------------------------- | ---------------------- | ------ | -------------------------------------------------- |
| BUG-001 | PRE-17 / PRE-09 | `hermes memory status` 或任意 hermes 命令 | `config.yaml` 第 543 行 `streaming.cursor` 解析失败，回退默认配置 | YAML 正常，用户 override 生效 | **P0** | `line 543, column 11 ... unexpected end of stream` |
| BUG-001 状态 | — | 本轮重新执行 `hermes -p manxin-ai memory status`、`hermes -p jingli-ai memory status` 与双 Gateway status | 以当前命令输出为准，不引用历史进程状态 | 两个 profile override 生效，`streaming.cursor` 不再告警 | — | 👤 待本轮手测确认关闭 |
| BUG-002 |                 |                                      |                                                      |                        |        |                                                    |
| BUG-003 |                 |                                      |                                                      |                        |        |                                                    |


**优先级建议**：

- **P0**：审批链断裂、误写价、双监听、敏感信息泄露、卡片回调不通、**config.yaml 损坏**
- **P1**：查询无响应、趋势卡全失败、路由错误工具
- **P2**：文案/SOUL 格式、非关键降级体验

---

## J、整体验收准出标准


| 级别               | 条件                                          |
| ---------------- | ------------------------------------------- |
| **最低可灰度**        | A 段机器侧 ✅ + 你完成 B/C/E P0 + CB-02/03；无 P0 bug |
| **可全量切换 Hermes** | 上表 + F 段或 N/A + E2E-25 + 3 天 scheduler 正常   |
| **可宣告 V1 完成**    | 上表 + 真实运营独立走完 C+E + H 段 PX-03 或 PX-04       |


**当前进度（2026-06-30 Agent 预检）**：机器侧 AUTO + A 核心项 ✅；**未达最低可灰度**（缺飞书手测）。

---

## K、推荐执行顺序（你接下来）

```text
1. 确认 PRE-14/15：机器人在测试群 + 你的 ou_ 在白名单
2. B 段：CB-01 → CB-02 → CB-03
3. C 段 P0：E2E-08 → E2E-01 → E2E-04
4. E 段 P0：E2E-12 → E2E-13
5. 有余力：C 段其余、F 段（non_direct 全链）、G/H 可选
```

**跑测时建议开日志**（另开终端）：

```powershell
.\scripts\show-logs.ps1 -Service backend -Tail 100 -Follow
# Hermes Gateway 窗口保持可见
```

---

## L、每条失败时最少记录什么

1. 精确飞书原文（含 @）
2. 机器人完整回复或「无回复」+ 等待时长
3. 当时 EBK/竞品账号状态（控制台截图）
4. `backend.out.log` 对应时间段约 20 行
5. Hermes Gateway 是否收到 `card.action.trigger`（审批类失败必查）

---

## M、回滚

若验收中 Hermes 不稳定，可临时回滚到内置监听：

1. 停 Hermes Gateway
2. `.env` 中设 `FEISHU_AGENT_MODE=builtin`
3. `.\scripts\run-worker.ps1 -Service feishu-listener`
4. 飞书应用回调改回 watch 侧（若曾改 HTTP 回调）

后端 `POST /api/v1/feishu/events` 保留，无需改代码即可回滚。

---

## N、dev-plan-v3 Step 4 自动化验收映射（补充）

> 本节把 dev-plan-v3 Step 4 的断言逐条映射到自动化测试，作为「代码层没红」的可复现清单。

### 自动化验收命令

```bash
# 后端
PYTHONPATH=backend ./.venv/Scripts/python.exe -m unittest \
  backend.tests.domain.test_feishu_chat_context \
  backend.tests.domain.test_ai_agent_action_tools \
  backend.tests.domain.test_ai_agent_read_only_tools \
  backend.tests.domain.test_competitor_browser_pool_feishu_pricing_boundary \
  backend.tests.domain.test_feishu_pricing_bot_commands \
  backend.tests.domain.test_ctrip_pricing_executor

# Hermes
cd pan-hotel-hermes && PYTHONPATH=. ../.venv/Scripts/python.exe -m unittest \
  tests.test_feishu_chat_context \
  tests.test_mcp_tool_contract \
  tests.test_feishu_store_resolver
```

### 断言 → 测试映射

| Step 4 断言 | 覆盖测试 | 现状 |
|------------|----------|------|
| A chat + A key 成功 | `test_feishu_chat_context`（多条已绑群解析为 resolved） | ✅ |
| A chat + B key → `watch_group_not_allowed` | `test_mcp_tool_contract`（跨组拒绝）、`test_ai_agent_action_tools.test_explicit_watch_group_key_must_belong_to_current_chat` | ✅ |
| 未带 chat 保持现有默认店铺兜底（不改为 `session_context_required`） | `test_feishu_chat_context.test_empty_chat_id`（返回 `unbound`，保留既有默认行为） | ✅ |
| direct/unknown 全链路 `write_not_allowed`（preview/dry-run/execute/queue） | `test_ai_agent_action_tools`、`test_competitor_browser_pool_feishu_pricing_boundary.test_direct_or_unknown_pricing_ebk_still_blocks_execution_with_pool_enabled` | ✅ |
| 竞品涨价保持现有链路（推送卡片→调价→preview→审批→执行→回填） | `test_competitor_browser_pool_feishu_pricing_boundary`（`try_auto_preview_and_approval_for_alert` 保留 reference preview→approval） | ✅ |
| 竞品降价需可信证据 + 操作员手动确认目标价后进 preview | `test_feishu_store_resolver`（仅可信证据驱动 reference 卡片） | ✅ |
| 写价成功 = `executed`（响应验证 + 目标价回读一致） | `test_ctrip_pricing_executor`（Step 2A） | ✅ |
| 写价不确定 = `needs_review`（不假装成功） | `test_ctrip_pricing_executor`（Step 2A） | ✅ |
| CAPTCHA/登录/风控 = `human_required` | `test_ctrip_pricing_executor` + scheduler/executor 链路 | ✅ |

### 待确认（记入汇总表）
- **同一 chat 并发两个 Agent → 409**：dev-plan-v3 Step 4 列此断言并要求「说明锁的所有者和释放条件」。当前实现由 `_resolve_tool_watch_group_key` 的 `watch_group_not_allowed`（跨组/越权）+ preview/approval 仓储层冲突覆盖；尚未单列「同 chat 并发两 Agent → HTTP 409 + 锁 owner/释放条件」的显式契约测试。建议作为 Step 4 收尾的增量测试单独立项（不阻塞当前自动化验收）。
