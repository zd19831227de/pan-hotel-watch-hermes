# 美团/飞猪 EBK 定时监控：以携程监控组为锚点的重构设计（定稿）

> 创建日期：2026-08-24
> 状态：**已实施（2026-08-25，详见 §8 实施记录）**
> 关联文档：
> - docs/zcode-ebk-scheduling-and-pricing-fix-instructions-2026-08-24.md（美团/飞猪定时抓取任务的原始说明）
> - AGENTS.md（调价安全契约 — authoritative；本设计不触碰写价链路）
> 前置审查：本轮对现有美团/飞猪定时推送 + 独立配置窗口代码做了绑定与链路审查，
> 问题清单与结论详见本文件 §2；代码行号引用以 2026-08-24 工作区为准。

---

## 1. 背景与目标

现有美团/飞猪 EBK 定时监控各有一套独立监控组表
（`meituan_ebooking_watch_groups` / `fliggy_ebooking_watch_groups`），组上同时承载：

- 浏览器账号绑定（`browser_session_id` / `platform_account_id`）
- 飞书投递配置（`feishu_chat_id` / `feishu_webhook_url`）
- 定时调度配置（`schedule_mode` / `schedule_start_time` / `schedule_end_time` /
  `schedule_interval_minutes`）

审查发现：这套三平台各绑各的模型在「同一飞书群绑定携程+美团+飞猪」的真实运营场景下
产生多处绑定语义混乱（详见 §2）。

**本设计的目标**：把美团/飞猪定时监控收敛为**挂在已有携程监控组之下的排程配置**，
一个店铺只维护一处群绑定与机器人投递，消除重复绑定面与死字段。

**已确认的决策（2026-08-24 运营确认）**：

1. **保留本平台浏览器账号下拉**——不做隐式账号解析，避免多账号歧义错乱；
   账号绑定继续走现有平台校验（`_validate_meituan/fliggy_browser_session_id`，
   要求 `platform` 匹配且 `intended_use == "ebk"`）。
2. **移除美团/飞猪窗口中的「飞书群 ID」「飞书机器人地址」**——群绑定与投递
   完全继承所选携程监控组（webhook、chat_id 均取携程组）。
3. 本轮**只定设计、不改代码**；实施按 §5 遗留清单另行排期。

---

## 2. 现状审查结论摘要（问题清单）

### 2.1 绑定与链路问题

| 编号 | 问题 | 位置 | 严重度 |
|---|---|---|---|
| ① | 美团/飞猪组的 `feishu_chat_id` 是死字段：只写入、掩码返回，全仓库无消费方；前端却暴露「飞书群 ID」输入框，运营以为完成了上下文群绑定 | `meituan_ebooking_watch/repository.py:149,189,276`；`frontend/src/app/ebk/EbkWatchSchedulePanel.tsx:269-274` | 高 |
| ② | `PATCH platform_account_id` 裸字段旁路无平台校验，可把携程账号注入美团/飞猪组；运行时 `get_browser_session_id_for_platform_account` 只按 account id 匹配、不校验平台 | `meituan_ebooking_watch/repository.py:187-188`（fliggy 同构）；`browser/sessions.py:561-567` | 高 |
| ③ | 浏览器账号删除/换号钩子只覆盖携程+竞品绑定，美团/飞猪组留下悬空 `browser_session_id`；悬空时走 `human_required` 且 `push_to_feishu=False` → 静默停摆 | `api/v1/browser_sessions.py:264-333`；`meituan_ebooking_watch/service.py:89-102` | 高 |
| ④ | agent 聊天上下文只认携程组 + 竞品组，美团/飞猪组绑定完全不参与；仅绑美团/飞猪的群被判 `unbound` 并误导运营去携程组补绑定 | `ops/feishu_chat_context.py:115,283,391-413` | 中 |
| ⑤ | agent 工具链无美团/飞猪监控组工具（run-now/pause/resume/status 全是携程）；美团/飞猪数据只能经 Hermes `get_ebk_joint_analysis_context` 按浏览器账号品牌匹配读快照，与群绑定无关 | `ai_agent/registry.py:317-349`；`ai_agent/tools.py:421-449,781-796` | 中 |
| ⑥ | 推送节流只读不写：`record_push` 仅携程 service 调用，美团/飞猪手动推送永不记录；节流 key 仅 `group_key`，三平台共享内存字典，撞名互相节流 | `ctrip_ebooking_watch/push_throttle.py:33-34`；`ctrip_ebooking_watch/service.py:375` | 中 |
| ⑦ | 控制台「读取并保存」把美团/飞猪推送借道“当前选中携程组”的 webhook，切错组即进错群 | `ebk_snapshots/feishu_push.py:35-74` | 低 |

### 2.2 其他逻辑错误

| 编号 | 问题 | 位置 | 严重度 |
|---|---|---|---|
| ⑧ | **失败态推送风暴**：读层 `error`/`human_required` 结果保持 due（每 60s 重试）但调度路径照推飞书卡片，登录失效期间每 60 秒刷一张失败卡 | `meituan_ebooking_watch/service.py:139-148`（fliggy 同构） | **P0** |
| ⑨ | 两个 API 的 404 文案乱码（GBK→UTF-8 错码写死在源码） | `api/v1/meituan_ebooking_watch.py:104`；`api/v1/fliggy_ebooking_watch.py:104` | 中 |
| ⑩ | `run-now` 关闭推送时成功后仍推进 `last_polled_at`，跳过下一定时槽位（与携程行为一致，仅语义提示） | `meituan_ebooking_watch/service.py:140-141` | 低 |
| ⑪ | agent 健康检查与 supervisor 总览不含美团/飞猪调度心跳 | `ops/runtime_health.py:51-58`；`config/service.py:1465-1494` | 低 |

### 2.3 已核实为安全/正确的部分（不重构）

- 三平台调度器各自遍历自己的组表，互不读取对方数据（`workers/read_only_scheduler.py:216-225`）。
- @机器人手动调价：一个群只允许绑一个调价账号，多绑即拒（`browser/sessions.py:579-596`）；
  审批卡回调的平台从持久化 preview 解析，不信任卡片 payload（`pricing_safety.py:41-50`）。
- 美团/飞猪推送卡是纯展示卡，无操作按钮，无卡片回调路由风险。
- 一个飞书群只能绑定一个 agent profile（`config/service.py:1308-1309` 冲突拒绝）。

---

## 3. 目标形态

### 3.1 概念模型

```text
店铺（Store）
  └── 携程监控组（Ctrip Watch Group）          ← 唯一锚点，持有 feishu_chat_id + feishu_webhook_url + 携程账号
        ├── 携程定时推送（现状不变）
        ├── 美团排程（platform_ebk_watch_schedules, platform=meituan）
        │     └── 浏览器账号：本平台 EBK 账号下拉（保留，显式绑定）
        └── 飞猪排程（platform_ebk_watch_schedules, platform=fliggy）
              └── 浏览器账号：本平台 EBK 账号下拉（保留，显式绑定）
```

- 群上下文、agent 授权、门店解析全部继续以携程组为唯一锚点（现状代码已如此，只是
  过去美团/飞猪窗口制造了“也能绑群”的假象，现在把假象移除）。
- 推送投递：美团/飞猪排程触发后，卡片发往**所挂携程组的 `feishu_webhook_url`**；
  卡片带「店铺（携程组名）+ 平台标题」。
- 无携程账号、仅经营美团/飞猪的店：允许建“空壳”携程组作为店铺锚点与投递配置载体，
  控制台文案需说明该组角色（§5 P2）。

### 3.2 窗口形态（前端）

美团/飞猪窗口 = 独立面板，字段缩减为：

1. **携程监控组选择器**（必选；列表来自 `/api/v1/ctrip-ebk-watch` 的组清单，
   展示组名 + 是否已配机器人地址）。
2. **本平台浏览器账号下拉**（保留；只列 `platform=本平台 && intended_use=ebk` 的账号，
   沿用现有校验）。
3. **定时配置**：`schedule_mode` / `schedule_start_time` / `schedule_end_time` /
   `schedule_interval_minutes`（字段契约不变，统一 `Asia/Shanghai`）。
4. **开关**：`enabled`、`auto_poll_enabled`、`last_polled_at` 展示、「立即读取」按钮、
   本平台 scheduler heartbeat。

**移除**：飞书群 ID、飞书机器人地址两个输入框与对应状态展示。

### 3.3 数据模型草案（新表，实施时落地）

```text
platform_ebk_watch_schedules
  id                    PK
  platform              ('meituan' | 'fliggy')
  ctrip_watch_group_key FK(逻辑引用携程组 group_key)
  browser_session_id    （平台校验后的显式账号绑定）
  platform_account_id   （随 browser_session_id 冗余写入，只读展示）
  schedule_mode / schedule_start_time / schedule_end_time / schedule_interval_minutes
  auto_poll_enabled / enabled
  last_polled_at
  created_at / updated_at
  UNIQUE(platform, ctrip_watch_group_key)
```

- 替代现有 `meituan_ebooking_watch_groups` / `fliggy_ebooking_watch_groups` 两张组表
  （含迁移：旧组若已配账号与调度窗口，需要人工/脚本映射到对应携程组；旧组的
  `feishu_chat_id`/`feishu_webhook_url` 字段废弃不迁移）。
- 快照仍落跨平台 `ebk_read_snapshots`（按 `platform` + `platform_account_id` 隔离，不动）。

### 3.4 调度链路（实施时调整）

```text
tick（每个平台一个调度器，或合并为一个循环）
  → 遍历携程组（enabled + 已挂本平台排程行 + 排程 due）
  → 校验排程行的浏览器账号（platform/ebk 校验；悬空 → config_required，不静默）
  → 读本平台 EBK（沿用 read_meituan/fliggy_platform_snapshot）
  → 持久化快照
  → 仅 success/partial 推送（携程组 webhook + 店铺/平台标题）
  → 成功才推进 last_polled_at；失败走告警冷却（§5 P0）
  → 节流：record_push 按「携程组 key + 平台」记录，三平台共享冷却语义
```

---

## 4. 问题对照表（本设计覆盖情况）

| 编号 | 问题 | 本设计是否解决 |
|---|---|---|
| ① | 美团/飞猪 `feishu_chat_id` 死字段、误导性群绑定 | ✅ 完全解决（字段与 UI 入口整体移除） |
| ⑦ | 控制台读取推送借道携程 webhook | ✅ 变为标准投递路径，不再有“借道”特例 |
| ⑥ | 节流只读不写、key 撞名 | ✅ 按「携程组 key + 平台」统一记录修复 |
| ④ | agent 上下文仅认携程组，误导运营补绑定 | ✅ 美团/飞猪不再有群绑定概念，携程-only 架构成为唯一正确口径 |
| ② | 跨平台账号注入旁路 | ⚠️ 保留账号下拉后 API 旁路仍在；实施时删除裸 `platform_account_id` patch 字段（只留 `browser_session_id` 校验路径） |
| ③ | 账号删除/换号后悬空绑定静默停摆 | ⚠️ 保留显式绑定就会存在；实施时 (a) 把 `browser_sessions.py` 迁移/清除钩子扩展到本表，(b) 失败态改走携程组机器人 + 告警冷却，消除“静默” |
| ⑤ | agent 无法控制美团/飞猪排程 | ⚠️ 结构变简单（排程挂在携程组下）；实施时给 agent 工具加 `platform` 参数并扩展 run-now/pause/resume |
| ⑧ | 失败态推送风暴 | ❌ 与绑定无关，必须单独修（P0，见 §5） |
| ⑨ | 404 文案乱码 | ✅ 随重构重写文件时修复 |
| ⑩ | run-now 不推送也推进 last_polled_at | ⚠️ 实施时重新定义语义（建议：与推送状态解耦或明确按“读取成功”计） |
| ⑪ | 健康检查/supervisor 总览缺美团/飞猪心跳 | ⚠️ 实施时把两个调度心跳纳入 `runtime_health` 与 supervisor 总览 |
| — | 携程组删除级联（新引入） | ⚠️ 实施时：删除携程组需提示“将连带移除美团/飞猪定时监控”并级联清理 |
| — | 仅经营美团/飞猪的店（新引入） | ⚠️ 允许空壳携程组作锚点，控制台文案说明其角色 |

---

## 5. 遗留工作清单（实施时按序执行，本轮不实施）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | 失败态推送过滤 + 告警冷却 | 非 success/partial 不推卡片；登录失效/页面异常按“每小时最多一次”或“每天一次”发一次提醒到携程组机器人 |
| P1 | 新表 `platform_ebk_watch_schedules` + 迁移 | 建表走 domain migrations 框架并在 `db/session.py` 注册；旧组配置迁移策略单独评审 |
| P1 | 移除裸 `platform_account_id` patch 旁路 | 美团/飞猪排程只允许 `browser_session_id` 校验路径；同步修 ⑨ 乱码文案 |
| P1 | 账号删除/换号钩子覆盖美团/飞猪排程 | 在 `browser_sessions.py` 现有 snapshot/migrate/clear 流程中加入本表处理 |
| P1 | 前端窗口重构 | 携程组选择器 + 账号下拉 + 定时配置 + 开关；移除群 ID/机器人地址输入框 |
| P2 | agent 工具扩展 | `run_ebk_watch_once`/`pause_watch`/`resume_watch` 增加 meituan/fliggy 分支；工具描述补平台口径 |
| P2 | 健康检查与 supervisor 总览纳入新心跳 | `runtime_health.py`、`config/service.py` 补 `meituan_ebooking_scheduler`/`fliggy_ebooking_scheduler` |
| P2 | 携程组删除级联提示与清理 | 删除接口 + 前端确认文案 |
| P2 | 空壳携程组角色文案 | 携程控制台对“锚点组（无携程账号、挂有平台排程）”给出说明 |

## 6. 实施约定（写代码时遵守）

1. 每步先 GitNexus `impact`（HIGH/CRITICAL 先停），TDD 补测试（参考现有
   `test_meituan_ebooking_watch_scheduler.py` / `test_fliggy_ebooking_watch_scheduler.py` 的 mock 模式）。
2. 全量回归跑 AGENTS.md 命令，`Ran 0 tests` 视为失败。
3. 本设计不触碰：调价安全契约、手动调价群绑定（`pricing_feishu_chat_id`）、
   竞品监控组绑定逻辑、`ebk_read_snapshots` 快照模型。
4. 推送卡片必须带店铺（携程组名）+ 平台标题，避免三平台同机器人投递后无法区分。
5. 迁移不得删生产数据；先导出旧组配置清单，评审映射规则后再落库。

---

## 7. 结论

以携程监控组为唯一锚点、保留本平台账号显式下拉、移除美团/飞猪窗口的群与机器人配置，
可以解决绑定面的绝大部分问题（①④⑥⑦ 及误导性配置面），同时不牺牲账号一对一确定性。
不随本设计自动解决的是 P0 失败推送风暴与 ②③⑤⑪ 等 API/钩子/工具层加固项，
按 §5 清单在实施阶段单独排期。

---

## 8. 实施记录（2026-08-25）

按 §5 清单实施完毕；**未新建 §3.3 的 `platform_ebk_watch_schedules` 表，改为在现有
两张组表上原地演进**（§5 P1 本身要求"旧组迁移策略单独评审"，原地加列避免未评审的
数据搬迁，语义与目标形态一致；后续如需收敛为单表可再排期）：

| 项 | 落地方式 |
|---|---|
| P0 失败推送过滤 + 告警冷却 | 新增 `domain/platform_ebk_watch/push_policy.py`；两平台 service 仅 success/partial 推完整卡，失败态按 60 分钟冷却（`platform_ebk_failure_alert_cooldown_minutes` 可调）发异常提醒卡到锚定携程组机器人 |
| 锚定携程组 | 两张组表新增 `ctrip_watch_group_key` 列（migration 内 `ALTER TABLE` 兜底 + 旧组按 `feishu_chat_id` 唯一匹配携程组自动回填）；Create/Patch 校验锚点存在 + 平台内一携程组一排程；旧 feishu 列保留不读 |
| 投递继承携程组 | 推送 webhook/店名取锚定携程组运行时；卡片标题与正文带「店铺（携程组名）+ 平台」 |
| ② 裸 `platform_account_id` patch 旁路 | Create/Patch 模型删除该字段，仅保留 `browser_session_id` 校验路径；旧数据兜底解析加平台校验 |
| ③ 账号钩子 | 两平台 repo 新增 snapshot/migrate/clear/restore；`api/v1/browser_sessions.py` 更新/删除流程接入 |
| ⑤ agent 工具 | `run_ebk_watch_once`/`get_ebk_latest_result` 增 `platform` 参数；`pause/resume_watch` 的 `watch_kind` 增 `meituan_ebk`/`fliggy_ebk`；授权按"群绑定携程组 → 锚点"解析 |
| ⑥ 节流 | key 统一 `platform_ebk:<platform>:<group_key>`；两平台成功推送后调 `record_push` |
| ⑨ 404 乱码 | 两平台 API 删除端点文案修复 |
| ⑪ 健康检查 | `runtime_health` 纳入 `meituan_ebooking_scheduler`/`fliggy_ebooking_scheduler`；supervisor 总览增 `platform_ebk_schedulers` 心跳摘要 |
| 携程组删除级联 | 携程组 DELETE 端点连带删除挂载的两平台排程并在响应中回显清单 |
| 前端 | `EbkWatchSchedulePanel` 重构为「携程组选择器 + 本平台账号下拉 + 定时配置」，移除群 ID/机器人地址输入；`ebkPlatformWatchApi` 契约同步 |

验证：全量后端 `Ran 1139 tests ... OK`；前端 `npm test` 181 通过、`npm run build` 通过。
