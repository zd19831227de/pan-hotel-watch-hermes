# EBK 全局推送卡片 v2 — 2026-09-01 调整说明（交接执行文档）

> 交接对象：新开的执行窗口。分支 `feat/ebk-time-dimensions`。
> 硬性约束：**完成后只生成预览样式，不提交、不推送代码**；预览 HTML 文件不纳入提交范围。
> 需要真实字段核对时可用浏览器抓取：三平台浏览器均已打开并登录，CDP 只读 attach（端口见文末）。

---

## 〇、前置必读：环比基线的正确语义（本次调整的核心依赖）

> **状态更新（2026-09-01 本会话）：本节描述的数据层基线修复已完成并验证**
> （GitNexus impact LOW risk；新增 4 个测试 + 9 个相关套件共 115 个测试全绿）。
> 实现落点：`service.py` 基线加载移到读循环前（`_load_previous_metrics` 遍历
> resolved targets）；`ebk_snapshot_reader.persist_ctrip_run_snapshot` 补存携程
> 快照（`default_deps._read_ctrip(group, global_run_id)` 调用，补存失败仅告警
> 不阻断）。新窗口无需重做本节，只需做展示层并按第五节验证。

所有平台"问题1"的**增减幅度**都依赖下面这条语义：

**基线 = 本次读取之前、最近一条成功快照，不限来源**（自动推送 / 定时看板 / 人工刷新都算）。

用户原话举例：
- 13:00 读取 → 自动匹配 12:00 的自动推送数据
- 14:00 人工刷新过一次 → 15:00 读取就对比 14:00 那次
- 每天早上第一次拉取（找不到更早成功快照）→ 显示「凌晨数据」，不输出数值

### 当前代码的两个缺陷（已实测确认，未修复）

1. **美团/飞猪自比自**：`run_global_push` 先读数据存快照（`service.py:300-343`），**之后**才加载基线（`service.py:346-349`）。`get_latest_success_ebk_snapshot`（`ebk_snapshots/repository.py:98-127`）按 `created_at DESC` 取第一条，拿到的是本次自己刚存的快照 → 环比恒为 ±0；本次读取失败时回退到的可能是早上 8:30 定时任务的旧快照。
2. **携程基线来源错误**：全局推送读携程走 `_read_ctrip` → `run_watch_group`（`service.py:100-110`），只写 `ctrip_ebooking_watch_runs` 表，**不写 `ebk_read_snapshots`**；而 `_get_previous_metrics`（`service.py:193-209`）只查 `ebk_read_snapshots` → 携程"上一次"永远是别的来源（8:30 定时读）的数据。

### 修复方向

- 基线加载移到读循环**之前**（或按 `global_run_id` 在查询里排除本次快照）。
- 携程全局推送读取完成后，补存一条到 `ebk_read_snapshots`（带 `read_trigger.via="global_push"` + `global_run_id`），三平台基线来源统一。
- 「凌晨数据」判定与标记已存在：`_EARLY_MORNING_MARKER`（`service.py:435`）、`_inject_comparison`（`service.py:438-462`），修完基线后语义自然正确。

---

## 一、携程（4 项）

### 问题 1 — 收益概览「较上次」改为增减幅度
- 收益概览行的对比列输出**增减幅度**，只与上一条成功快照比（依赖前置修复）。
- 早上首次拉取 → 该列显示「凌晨数据」，不输出数值。
- 现有实现 `_change_text`（`service.py:489-499`）已输出「差值 (+百分比%)」格式，保留该格式即可。

### 问题 2 — 经营对比缺「在店间夜」
- 经营对比模块需要出现「在店间夜」数据行，**该行带竞争圈排名**。
- 其增减幅度按自身字段独立计算，与销售额的幅度互不相同（不得复用销售额的差值）。
- 注意：`_COMPARISON_FIELD_KEYS` 已含 `realtime_in_house_room_nights` / `in_house_room_nights`，但当前分组把在店间夜归入收益概览（`_CTRIP_REVENUE_KEYS`），**经营对比里缺一条带排名的在店间夜行**。

### 问题 3 — 竞争圈排名带竞品圈总数
- 经营对比中的竞争圈排名显示为 `N/总数`，如现有排名 N、竞品圈 M 家店 → 显示 `N/M`。
- 已有工具函数 `_competition_total` / `_format_rank`（`global_push_cards.py:315-331`）可复用；需确认数据源能取到总数（页面上的 `X/21` 格式行），取不到时保留原值。

### 问题 4 — 流量曝光两处补齐
- 「实时排名」行的数值后追加「（排名）」标注，如 `3（排名）`（`_traffic_row` 已实现，`global_push_cards.py:405-410`）。
- 补上遗漏的「携程APP访客转化率」行（`_CTRIP_COMPETITION_TRAFFIC_FIELDS` 已含 `ctrip_app_conversion_rate`，需确认 adapter 真的产出该字段、数据集里有值）。

## 二、美团（3 项）

### 问题 1 — 工作台改为「收益概览」并放第一位
- 含实时销售额的模块（workbench_home）放在卡片**第一个**，标题改为「收益概览」，与携程叫法统一。
- 收益行同样增加增减幅度 + 早上首次拉取判定（同携程规则，依赖前置修复）。

### 问题 2 — 榜单带店铺总数
- 同行榜单排名显示 `N/M`：如现在第 3 名、榜单共 10 家店 → `3/10`。
- 榜单值本身可能已是排名格式（「第 N 名」或 `N/M`），统一规整为 `N/M`。

### 问题 3 — 流量曝光对比口径改实时「较上期」
- 流量曝光数据的对比**不要「较昨日」**，改为实时对比上一次读取，标签用「较上期」（EBK 网页本身就显示「较上期」，以页面实际为准）。
- 同样依赖前置修复（基线=上一次读取）。

## 三、飞猪（1 项）

### 问题 1 — 「首页」改「收益概览」+ 预订间夜并入
- 模块标题「首页」改为「收益概览」，与携程统一；收益行增加增减幅度 + 首次拉取判定（同上，依赖前置修复）。
- 「预订间夜」数据并入收益概览模块，**不单独拆分**。
- 模块顺序：收益概览 → 流量曝光数据（流量曝光是下一个模块）。

---

## 四、工作区现状（执行前先看）

- 工作区有一批**未提交**改动：上一个执行窗口做到一半卡住（2026-09-01 15:35–17:02 的一波，含 `service.py`/`adapters.py`/`projection.py`/`global_push_cards.py`/`feishu_card_tables.py` + 测试 + 预览脚本）。**用户已指示不用管它**。
- 快速处置建议：先跑下方测试命令看现状；卡住会话的展示层改动（分组/标题/排名格式）与需求方向一致，可用则续完、不可用则 `git checkout -- <file>` 恢复后按本说明重做（注意：`service.py`/`ebk_snapshot_reader.py`/两个测试文件含本会话的数据层修复，**不要整文件回滚**）。
- 14:25 前已完成的独立小改动：`feishu_card_tables.py` 的 `part_headers` 自定义分栏表头（保留，后续要用）。

## 五、实施与验收要求

1. 按 AGENTS.md 工作流：严格 TDD（先写失败测试）；改任何函数前跑 GitNexus `impact`；完成前跑验证并贴输出。
2. 基线修复的测试必须覆盖三个场景：
   - 本次成功读取 → 对比的是**上一次**快照（不是本次自己）；
   - 本次读取失败 → 跳过本次，取更早成功快照；
   - 无任何更早成功快照 → 收益行 comparison = 「凌晨数据」，无数值。
3. 涉及测试模块：
   - `backend/tests/domain/test_ebk_global_push_service.py`
   - `backend/tests/domain/test_ebk_global_push_cards.py`
   - `backend/tests/domain/test_ebooking_dataset_read_flow.py`
   - 运行：`$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.domain.test_ebk_global_push_service`（其余同理）。
4. 表格渲染必须走共享 `backend/app/domain/notifications/feishu_card_tables.py`，遵守约束：单表 ≤10 行自动分页、单元格内省略号、单卡 ≤5 表。
5. 完成后运行 `backend/scripts/generate_global_push_card_preview.py` 生成新预览 HTML 供业务确认（**预览 HTML 不提交**）。
6. **不提交、不推送。**
7. 真实字段核对（可选但推荐）：三平台 EBK 浏览器均已打开登录，CDP 只读 attach 即可（勿新起 Chrome、勿动 profile）：携程 `127.0.0.1:9221`、美团 `127.0.0.1:9521`、飞猪 `127.0.0.1:9531`；如端口有变，以 backend 已配置的浏览器会话数据（browser_sessions 配置）为准。核对点：竞争圈总数（21 这类分母）、榜单店铺总数、携程APP访客转化率的页面字段名。

## 六、涉及文件清单

| 文件 | 改什么 |
|------|--------|
| `backend/app/domain/ebk_global_push/service.py` | 基线加载时机（读前加载）、携程补存快照、凌晨判定 |
| `backend/app/domain/ebk_snapshots/repository.py` | `get_latest_success_ebk_snapshot` 支持排除本次 global_run_id（若采用排除方案） |
| `backend/app/domain/ebk_global_push/adapters.py` | 在店间夜（带排名）、携程APP访客转化率、榜单总数、流量较上期字段 |
| `backend/app/domain/ebk_global_push/global_push_cards.py` | 模块标题/顺序（收益概览第一）、排名 N/M 格式、（排名）标注 |
| `backend/app/domain/ebk_global_push/projection.py` | 字段归组配套 |
| `backend/scripts/generate_global_push_card_preview.py` | 预览数据同步以上口径 |
| 上述三个测试模块 | 失败测试先行 |
