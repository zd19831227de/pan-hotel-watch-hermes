# EBK 经营数据导出:来源合并 + 导出范围选择(当天/本周/本月/自定义)

> 创建日期:2026-08-24
> 性质:**开发设计文档,未修改任何代码与数据。**
> 上位约束:`AGENTS.md`「Real data first / 不发明经营数据 / 导出字段白名单不泄漏敏感信息」。
> 关联文档:`docs/pan-hotel-watch-ebk-business-export-and-brief-optimization-2026-08-21.md`(导出格式优化方向)。

---

## 一、背景与问题

### 问题 1:导出数据来源不全(按链路而非按触发方式缺口)

`/api/v1/export/ebk-business-data` 只读统一快照表 `ebk_read_snapshots`,而全项目只有
`read_all_ebk_snapshots`(`backend/app/domain/ebk_snapshots/ebk_snapshot_reader.py`)一个写入入口。
各触发链路落库现状:

| 触发方式 | 链路 | 落库表 | 现在是否进导出 | 评估结论 |
|---|---|---|---|---|
| 携程盯盘**定时轮询**推送 | `ctrip_ebooking_watch/scheduler.py` → `run_watch_group` | `ctrip_ebooking_watch_runs` | ❌ | **应进**。时间序列最完整(默认 60 分钟/轮) |
| 携程**每日简报**定时推送 | `run_daily_brief`(actor=`daily_brief_scheduler`) | 同上 | ❌ | **应进**(只读 2 个数据集,真实数据) |
| 携程控制台手动「立即读取并推送」 | run-now(source=`api`,actor=`operator`) | 同上 | ❌ | **应进**(与定时同构) |
| 机器人「刷新 EBK 盯盘」 | hermes `run_ebk_watch_once` → run-now | 同上 | ❌ | **应进**(与上同链路) |
| 机器人「联合经营分析+现读」 | hermes `refresh_before_analysis=true` → `/ebk-snapshots/read-all` | `ebk_read_snapshots` | ✅ | 保持 |
| 美团/飞猪控制台「读取并保存」 | `/ebk-snapshots/read-all` | 同上 | ✅ | 保持(两平台无定时任务,现状即全量) |
| 机器人 `read_ebk_datasets` 按指标问答直读 | `/sessions/{id}/read-dataset`,**不落库** | — | ❌ | **不进**。单数据集+临时 filters,问答场景;进导出需先落库改造,属审计日志范畴 |

结论:**导出口径 = 所有成功读取并持久化的经营数据**,与触发方式无关。数据可信度同源
(同一套 parser、逐数据集 `status=success` 校验、入库前脱敏)。

技术可行性已核实:`ctrip_ebooking_watch_dataset_results.data_json` 与统一快照
`payload_json.datasets[id].data` 完全同构(同一 parser 输出),导出层加适配器即可,不改读取链路。

### 问题 2:导出范围只有「留存天数」,默认 180 天等于全量导出

现状前端 `HistoryPanel.tsx:130-140` 只有一个「数据留存时间(天)」输入(默认 180),
后端 `days` 参数(1~3650)。操作员想导当天/本周/本月数据必须手算天数,体验差且易错。

---

## 二、目标口径(已定决策)

### 2.1 数据来源合并口径

1. **导出层合并查询,不双写**:exports 同时查 `ebk_read_snapshots` +
   `ctrip_ebooking_watch_runs`(仅 ctrip),run 适配成 snapshot 形状后统一喂给
   `ebk_business_metrics`。不让盯盘服务双写快照表——避免同一数据两份存储、两处口径漂移。
2. **不静默去重**:同一天盯盘 run 与机器人分析 read-all 各读一遍携程属真实重复读取,
   时间戳不同都保留;用新增「**数据来源**」列区分,操作员在 Excel 自行筛选。
3. **只取 `data_json`,不取 `evidence_json` / `browser_session_id`**——延续导出安全白名单
   (不泄漏 cookie / profile 路径 / 截图路径 / webhook)。
4. **数据量不做每日聚合(本期)**:`limit` 维持参数化(默认 1000、上限 5000)。
   定时轮询开启时约 24 run/天,「本月」约 720 run,接近默认上限——导出页提示操作员
   长区间可调大 limit;「经营简报按日聚合」列为后续可选,本期不做。
5. 机器人问答式直读(`read_ebk_datasets`)不进导出,不改其落库行为。

### 2.2 导出范围口径

新增预设 **当天 / 本周 / 本月 / 自定义**,保留 **最近 N 天**(现状)向后兼容:

| 预设 | 窗口(服务器本地时区,与 `captured_at`/`created_at` 的 `local_now` 一致) |
|---|---|
| 当天 `today` | 今日 00:00:00 ≤ t |
| 本周 `week` | 本周一 00:00:00 ≤ t(周一为一周开始,业务习惯) |
| 本月 `month` | 本月 1 日 00:00:00 ≤ t |
| 自定义 `custom` | `start_date` 00:00:00 ≤ t ≤ `end_date` 23:59:59(两端必填,起 ≤ 止) |
| 最近 N 天 | 现有 `days` 行为;`range` 与 `days` 同传时 `range` 优先 |

范围选择器**本期只作用于「导出经营数据」按钮**;历史策略/价格告警/EBK 运行记录导出
继续沿用「留存天数」字段(避免为对齐语义改动多个端点,推广列为后续可选)。

---

## 三、方案设计

### 3.1 后端:窗口解析(API 层)

`backend/app/api/v1/exports.py`:

```text
GET /api/v1/export/ebk-business-data
  range:     "today" | "week" | "month" | "custom"   (可选;缺省回落 days)
  start_date: YYYY-MM-DD                              (range=custom 必填)
  end_date:   YYYY-MM-DD                              (range=custom 必填)
  days / limit / platforms / format                    (不变)
```

- 新增私有 helper `_resolve_export_window(range, start_date, end_date, days)`
  → 返回 `(start_at: datetime, end_at: datetime | None)`;`days` 分支复用现有 cutoff 逻辑。
- 校验失败(custom 缺日期、起>止、日期格式错、range 值非法)→ HTTP 422,中文 detail。
- `days` 与 `range` 均不传时维持现状(days=180)。

### 3.2 后端:来源合并(导出读侧)

1. **新查询函数** `ctrip_ebooking_watch/repository.py`:

   ```python
   async def list_data_center_watch_runs_for_export(
       *, start_at: datetime, end_at: datetime | None, limit: int
   ) -> list[dict[str, Any]]
   ```

   返回 run 级字段(`platform_account_id / actor_id / source / captured_at`)及
   `results: [{dataset_id, dataset_label, status, data_json}]`。窗口过滤 `captured_at`,
   按 `captured_at asc` 排序,`limit` 与快照查询各自独立生效(合并后总量仍受端点
   `limit` 上限约束的说明见 §3.4)。

2. **适配器** 新模块 `backend/app/domain/exports/ebk_sources.py`:

   ```python
   def watch_runs_to_snapshots(runs: list[dict]) -> list[dict]
   ```

   每个 run → 一条 snapshot 形状:

   ```python
   {
     "platform": "ctrip",
     "platform_account_id": run["platform_account_id"],
     "created_at": run["captured_at"].isoformat(),
     "payload_json": {"datasets": {ds.dataset_id: {"status": ds.status, "data": ds.data_json}}},
     "export_source": <来源标签,见 3.3>,
   }
   ```

   - 仅收 `status="success"` 的数据集(与现有 `ebk_business_metrics` 逐数据集过滤一致,
     适配器不过滤、由指标层过滤亦可,二选一在实现时与现有行为对齐)。
   - 不携带 `evidence_json`、`browser_session_id`。

3. **合并**:`exports.py` 中两路查询结果 `sorted(key=created_at)` 后统一传入
   `extract_business_metric_rows` / `build_*_rows`;CSV/XLSX 生成逻辑不变。

### 3.3 数据来源列

所有 Sheet 的 headers 与行新增「**数据来源**」列(`DETAIL_HEADERS` /
`OPERATING_BRIEF_HEADERS` / `APP_CONVERSION_HEADERS` / `COMPETITION_HEADERS` /
`CORE_HEADERS` 同步加列):

| 数据 | 来源判定 | 标签 |
|---|---|---|
| watch run | `source=scheduler` 且 `actor_id=daily_brief_scheduler`* | 每日简报 |
| watch run | `source=scheduler`(其余) | 定时轮询 |
| watch run | `source=api` 且 `actor_id=hermes` | 机器人触发 |
| watch run | `source=api`(其余) | 手动读取 |
| 快照(read-all) | 新数据:保存时在 `payload_json` 写入 `read_trigger.actor_id` | 美团/飞猪手动→手动读取;携程→机器人分析 |
| 快照(历史) | 无标记字段 | 未标记 |

\* 实际字段值 `daily_brief_scheduler`(`scheduler.py:86`)。

**配套小改(区分机器人触发)**:hermes `run_ebk_watch_once` 调 run-now 时与控制台手动
均为 `source=api, actor=operator`,现有字段不可区分。改法:run-now 端点接受可选
`actor_id` 参数(默认 `operator` 不变),hermes 传 `actor_id=hermes`。历史数据保持
「手动读取」标签,不回填。

**快照来源标记**:`read_all_ebk_snapshots` 保存时把 `actor_id` 与触发方写入
`payload_json["read_trigger"]`(如 `{"actor_id": "operator", "via": "console"}` /
`{"actor_id": "hermes", "via": "bot_analysis"}`);`via` 由 API 请求方传入或按
`actor_id` 推断。`payload_json` 为自由 JSON,无迁移。历史行无此字段 →「未标记」。

### 3.4 limit 与数据量

- 端点 `limit` 语义调整为**合并后总条数上限**(默认 1000、上限 5000 不变),
  两路查询按 `limit` 各自取回后合并截断,避免单路占满。
- 前端导出面板在选中「本月/自定义长区间」且检测到定时轮询开启时,提示
  「数据较多,可将上限调至 5000」(纯文案,本期不做自动调参)。

### 3.5 前端:导出范围选择器

`frontend/src/app/HistoryPanel.tsx` 导出设置区改造:

```text
┌ 导出设置 ────────────────────────────────────────────┐
│ 经营数据导出范围  [当天 ▾ | 本周 | 本月 | 自定义 | 最近N天] │
│   (自定义 → 显示 开始日期[__] 结止日期[__] 两个 date 输入)   │
│   (最近N天 → 显示 天数[__],默认 180,即现状)               │
│ 导出格式          [CSV ▾ | XLSX]                          │
│ 其他导出留存时间(天) [180](服务于历史策略/价格告警/运行记录)│
│ [导出经营数据(携程/美团/飞猪)] [导出历史策略] [导出价格告警]…│
└──────────────────────────────────────────────────────┘
```

- `exportsApi.ts` 的 `exportEbkBusinessData` 入参扩展:
  `range?: "today" | "week" | "month" | "custom"`、`startDate?`、`endDate?`,
  与 `days` 互斥(range 优先),URL 参数名 `range/start_date/end_date`。
- 校验:选「自定义」未填齐起止或起>止时,按钮置灰并给一行 `field-hint` 提示,
  不发请求。
- 快捷范围同时作为导出文件名后缀(如 `ebk-business-data-week-20260824-1015.xlsx`),
  便于操作员分辨(可选,实现时一并做)。

---

## 四、明确不做的(Non-Goals)

1. 不让 `DataCenterWatchService` 双写 `ebk_read_snapshots`(单一事实来源不变)。
2. 不做导出去重/每日聚合(后续可选:`经营简报` Sheet 按「每日最后一次成功读取」聚合)。
3. 机器人 `read_ebk_datasets` 问答式直读不落库、不进导出。
4. 范围参数本期不推广到 `/export/pricing-strategy-history`、`/export/price-change-alerts.csv`
   等端点(沿用 `days`)。
5. 不自动删除窗口外持久化数据(留存口径不变,导出窗口只影响查询)。

---

## 五、实施步骤(Incremental Step Contract)

> 每步先写失败测试再实现;改任何符号前先跑 GitNexus `impact`。
> 涉及 `export_ebk_business_data` / `list_ebk_snapshots_with_payload_for_export` /
> `ebk_business_metrics` 各 build_* 与 HEADERS,预计影响面集中在 exports API + 前端
> HistoryPanel,风险等级以 impact 实测为准(HIGH 以上需确认后继续)。

### Step 1 后端:窗口参数解析

- **需求点**:`range/start_date/end_date` 解析与校验,`days` 兼容。
- **失败测试**:`backend/tests/domain/`(新增 `test_export_ebk_business_window.py`)
  - today/week/month/custom 各返回正确 `(start_at, end_at)`;
  - custom 缺日期 / 起>止 / 非法格式 / 非法 range → 422;
  - 仅 days 传参时行为与现状一致。
- **运行命令**:`$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.domain.test_export_ebk_business_window`
- **准出**:新测试由 FAIL → PASS;现有 exports 测试不回归。

### Step 2 后端:盯盘 run 合并进导出 + 数据来源列

- **需求点**:`list_data_center_watch_runs_for_export` + `watch_runs_to_snapshots`
  适配器 + 合并排序 + 各 Sheet「数据来源」列。
- **失败测试**:同测试模块追加
  - 适配器输出与 snapshot 同构、不含 evidence/browser_session_id;
  - 端点级:构造一条 scheduler run + 一条快照,导出结果含两路数据且来源标签正确
    (定时轮询/每日简报/手动读取/机器人触发/未标记);
  - limit 合并截断语义。
- **运行命令**:同 Step 1。
- **准出**:导出结果包含定时轮询与每日简报数据;敏感字段零泄漏(断言列白名单)。

### Step 3 后端+Hermes:run-now actor 透传

- **需求点**:run-now 端点可选 `actor_id`,hermes `run_ebk_watch_once` 传
  `actor_id=hermes`,使后续导出来源列可区分机器人触发。
- **失败测试**:后端端点测试(默认 actor 不变、传参生效)+
  `pan-hotel-hermes/tests/`(watch_api 请求体断言)。
- **运行命令**:`$env:PYTHONPATH='pan-hotel-hermes'; .\.venv\Scripts\python.exe -m unittest discover -s pan-hotel-hermes/tests -p 'test_*.py'`(聚焦相关模块)
- **准出**:新触发的机器人 run 在导出中标「机器人触发」;控制台手动仍标「手动读取」。

### Step 4 前端:导出范围选择器

- **需求点**:`HistoryPanel` 范围选择 + 自定义日期输入 + 参数传递与前置校验。
- **失败测试**:`frontend/src/app/historyPanel.test.ts` 追加
  - 渲染五个预设选项;自定义模式显示起止输入;
  - 缺日期/起>止时按钮置灰;合法时 `exportEbkBusinessData` 收到正确参数;
  - 其他导出按钮仍用留存天数。
- **运行命令**:`cd frontend; npm.cmd test`
- **准出**:测试 PASS;手工核对当天/本周/本月/自定义四条导出链路文件可下载且行数符合窗口。

### Step 5(可选,后续单列)经营简报按日聚合、范围推广其他导出端点

---

## 六、验收清单(整体)

1. 开着定时轮询的环境下,导出「当天」能看到今天的定时轮询数据(来源=定时轮询)。
2. @机器人 刷新盯盘后导出,该次读取标「机器人触发」。
3. 美团/飞猪手动读取照旧出现在导出(来源=手动读取/未标记历史数据不受影响)。
4. 范围四预设 + 最近N天的窗口边界与 §2.2 表一致;`days` 旧调用不破坏。
5. 导出所有 Sheet 新增「数据来源」列;全文件无 cookie/webhook/profile/截图路径。
6. `backend/tests/domain/`、`pan-hotel-hermes/tests/`、`frontend npm test` 相关聚焦套件全绿;
   完成前跑 `detect_changes()` 确认改动只落在预期符号。
