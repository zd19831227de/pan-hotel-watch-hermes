# EBK 全局定时推送模块开发设计

> 日期：2026-08-26
> 最近更新：2026-08-28
> 状态：核心实现、既有四轮设计审查和自动化回归已完成；2026-08-28 人工检查发现的三平台触发、携程评分/数据投影及按钮问题已修复。修复后的三平台完整真实 `run-now`、独立代码审查与飞书投递仍待执行，不能据此宣称已完成线上验收。
> 范围：携程 EBK、美团 EBK、飞猪 EBK 的统一定时推送
> 文档性质：原开发设计已转为设计与实现事实记录；剩余联调和迁移见 `docs/ebk-global-push-remaining-tasks-2026-08-28.md`。

## 0. 实现状态与 2026-08-28 问题闭环

全局模式不是新的第四套平台读取器。它复用三套已经能独立正常读取的链路，单次运行语义固定为：

```text
携程独立读取（抑制旧携程卡片推送）
→ 美团独立读取（不经过旧美团 watch-service 推送）
→ 飞猪独立读取（不经过旧飞猪 watch-service 推送）
→ 汇总本轮三平台真实结果
→ 只发送全局经营/活动/广告分类卡
```

本轮人工检查问题及闭环：

- 美团、飞猪未触发：原挂载组解析对 repository 返回的字典使用 `getattr`，永远无法匹配锚点；现改为按携程锚点直接读取平台运行时记录。美团/飞猪旧监控组的 `enabled` 只控制旧模块自身，不再阻断全局单次读取；是否可读以真实账号和浏览器会话绑定为准。`auto_poll_enabled` 仍用于全局 scheduler 与旧 scheduler 的冲突防护。
- 携程数据不全和评分“未读取”：经营实时指标实际位于 `business_outline.data.time_dimensions.realtime.metrics`，PSI/酒店评分是带 `label/value` 的指标对象。适配层现读取真实 `value`，保留页面精度；PSI 按自身 0–6 量表校验，酒店评分按 0–5 量表校验，不再错误共用五分上限。生产只读检查已观察到 PSI `5.44`、酒店评分 `4.79`。
- 携程运行关联缺失：全局关联 ID 实际位于 `payload.persistence.run_id`，现已用于全局运行记录与 Excel 成组关联。
- 活动和缺平台卡片不完整：携程已报名活动兼容真实 `data.promotions`/`promotion_time` 结构；`config_required` 平台会显式进入三类卡片，不再被整体省略或被广告占位掩盖。
- 前端按钮：美团、飞猪和全局面板已按携程层级统一主操作、次操作及播放/停止/保存图标；全局面板移除与“暂停推送”重复的“软停用”可见按钮。

只读核对到既有携程全局运行 `316` 的 9/9 数据集成功，且上述两个评分原始值存在。该记录证明携程真实读取内容，不等同于修复后三平台全局链路及飞书投递已经联调完成。

## 1. 背景与目标

当前三个平台各自拥有读取、快照、手动推送和定时 scheduler：

| 平台 | 当前读取入口 | 当前定时入口 | 当前数据集范围 |
|---|---|---|---:|
| 携程 | `DataCenterWatchService`；浏览器层 `CtripEbookingBrowserService.read_monitor` 默认 6 项 | `CtripEbookingDataCenterWatchScheduler` | 数据中心白名单 10 项；统一 `read_monitor` 默认 6 项 |
| 美团 | `MeituanEbookingWatchService`、`read_meituan_platform_snapshot` | `MeituanEbookingWatchScheduler` | 7 |
| 飞猪 | `FliggyEbookingWatchService`、`read_fliggy_platform_snapshot` | `FliggyEbookingWatchScheduler` | 4 |

现业务需要在 EBK 操作台的携程、美团、飞猪模块之后增加“全局定时推送”模块：

1. 停止三套旧的自动定时推送（仅人工关闭配置，不删除旧模块）；保留平台手动读取、平台手动推送和 `@飞书机器人` 触发。
2. 新模块按一个门店/携程监控组统一排程，顺序触发已启用平台的独立读取，抑制旧平台卡片推送，最后只发送全局分类卡片。
3. 三张卡片只显示本次读取中能够证明为实时或当前状态的数据：经营数据、活动促销、广告营销；评分按评分快照例外进入经营卡标题/正文评分总览。
4. 经营数据卡标题附加携程 PSI 和店铺评分并显示页面实际精度；评分真实缺失、结构不合法或超出各自量表时才显示“未读取”，且不阻断卡片。
5. 支持“固定时间点多选”和“按间隔推送”两种模式，下拉切换。

“全局”指统一调度三平台，不指整个系统只有一条全局配置。配置必须按门店/投递群隔离，否则多个门店会共用错误账号或 webhook。

## 2. 现状审查结论

### 2.1 可复用的能力

- 三个平台已有浏览器服务、页面解析器、快照持久化和手动 `run_watch_group_now`。
- `backend/app/domain/ctrip_ebooking_watch/service.py` 的 `DataCenterWatchService` 是携程数据中心读取服务；浏览器层 `read_monitor` 只遍历
  `business_outline`、`beneficial_data`、`flow_data`、`competition_profile`、`loss_analysis`、`competition_list` 六项。
- `DATA_CENTER_DATASETS` 业务白名单有 10 项（含 `comment_monitor`），而浏览器 `_CTRIP_DATASETS` 当前有 9 个通用数据集定义，`comment_monitor` 走单独的读取分支；`promotion_effect`、`promotion_registered`、`ad_pyramid` 不会被 `read_monitor` 自动读取。
- `backend/app/domain/ebk_snapshots/ebk_snapshot_reader.py` 的统一读取器目前是：携程调用六项 `read_monitor`，美团遍历 `MEITUAN_READ_ALL_DATASETS`，飞猪遍历 `FLIGGY_READ_ALL_DATASETS`。因此它不能直接作为新全局推送的“携程完整读取”实现。
- `ebk_read_snapshots` 可按 `platform` 和 `platform_account_id` 隔离保存结果。
- `backend/app/domain/notifications/feishu_card_tables.py` 可复用表格行、长文本拆分和横向滚动相关渲染能力。
- 当前平台卡片 builder 位于 `backend/app/domain/ebk_snapshots/feishu_push.py`，但它是旧平台盯盘卡，不应直接改成新的三分类卡，以免影响手动推送和问答。

### 2.2 必须避免的现状问题

- `read_only_scheduler.py` 当前同时启动三个平台 scheduler；新模块启用后如果同一门店的旧排程开关仍为开启，会产生重复读取、重复推送和浏览器 profile 抢占。旧 scheduler 进程本身不要求删除或停掉。
- 携程旧 scheduler 同时处理数据中心盯盘和 `daily_brief`。迁移门店需要人工关闭对应旧定时开关，但 `run_daily_brief_now` 手动入口保留。
- 美团、飞猪的投递配置已经按现有设计继承携程监控组的 webhook；新模块继续使用这个锚点，不新增第二套 webhook 密钥。
- 平台读取失败、登录失效、验证码或页面结构变化不能被包装成“成功的空数据”。失败应持久化审计结果并按冷却策略告警。
- 不能因为字段名称相似就把昨日、上月、同比、趋势数据放入实时卡。必须根据解析器的时间维度和字段元数据过滤。
- 不得根据默认账号、旧别名或历史截图推断平台账号。账号必须来自当前数据库中绑定且经过平台/`intended_use=ebk` 校验的浏览器会话。

### 2.3 审查结论

统一 scheduler、三平台梯队读取、快照审计和三张卡边界的总体架构可行；但“携程完整数据集”和“只展示实时字段”不能直接复用当前统一读取器或字段名推断。实现必须先落实本文件第 5.1、8.1.1、8.2、8.4 节的读取白名单、时间证据、缺配置状态和触发元数据扩展，否则活动/广告卡可能缺数据，或把昨日/无时间口径数据误推送。

## 3. 目标架构

```text
EBK 操作台
  ├── 携程 EBK（读取、手动推送、问答上下文）
  ├── 美团 EBK（读取、手动推送、问答上下文）
  ├── 飞猪 EBK（读取、手动推送、问答上下文）
  └── 全局定时推送（新模块）
        └── 每个携程监控组/门店一条配置
              ├── 携程账号：来自携程监控组
              ├── 美团账号：来自挂载到该携程组的美团 EBK 监控组
              ├── 飞猪账号：来自挂载到该携程组的飞猪 EBK 监控组
              ├── 飞书 webhook：来自携程监控组
              └── 统一 scheduler → 三平台读取 → 三张分类卡
```

### 3.1 配置归属

推荐以 `ctrip_watch_group_key` 作为门店和投递群的稳定锚点：

- 一条全局配置只能绑定一个携程监控组。
- 一个携程监控组只保留一条全局配置记录；停用采用软停用，不为同一锚点创建第二条记录。
- 携程账号取携程组当前有效账号。
- 美团、飞猪账号取当前挂载到该携程组的对应平台运行时记录；若不存在或无有效账号/浏览器会话，该平台状态为 `config_required`，不影响其它平台读取。查询必须按锚点返回可供读取的 runtime，不能再从 `list_*_watch_groups()` 的字典结果上用 `getattr`。旧监控组的 `enabled=false` 不阻断这次全局独立读取；`auto_poll_enabled` 只参与旧 scheduler 冲突检查。当前锚点唯一性由应用层 `_require_anchor_not_taken` 保证，迁移前仍须审计历史重复记录。
- 飞书群、webhook、门店名称继承携程组；新模块不再要求重复录入 webhook 或 chat ID。
- 账号选择页面仍显示真实账号名称和平台类型；服务端再次校验平台、用途和会话注册状态。

这样可以保持现有“携程组为锚点”的设计，也不会复制账号和密钥字段。仅有美团/飞猪的门店允许沿用 2026-08-24 锚点设计创建不绑定携程账号的空壳携程锚点组；该决议已确认，不再作为本方案的待确认项。空壳组只承担门店、群和 webhook 锚定，缺少携程账号时该平台运行结果记为 `config_required`，不得生成伪造空快照。

### 3.2 模块边界

新模块只负责：配置、到期判断、编排读取、实时字段投影、三张卡组装、投递、运行记录和 scheduler 心跳。

新模块不负责：

- 浏览器 DOM 操作；
- 账号登录、验证码、二维码或风险控制绕过；
- 修改价格或任何 OTA 写入；
- 改变旧手动卡片和问答卡片模板；
- 重新实现平台解析器；
- 生成历史经营简报或 AI 经营结论。

## 4. 旧定时链路停用方案

本节的“停用”是运营配置层面的停用，不是删除或重构旧功能。携程、美团、飞猪三套旧 scheduler、service、API、数据表和卡片模板全部保留，按现有方式继续运行；只把需要迁移到全局推送的旧自动定时开关人工关闭。

### 4.1 停用范围

切换后，以下旧自动路径不再对已迁移门店产生 due 任务：

- 携程监控组的 `auto_poll_enabled=false`；
- 携程监控组的 `daily_brief_enabled` 不在本方案迁移范围内，保持原值不动：定时每日简报继续由旧携程 scheduler 生成，手动入口照旧（决议见 §13.7）。只有产品另行决定下线定时简报时，才由运营单独关闭该开关；
- 美团监控组的 `auto_poll_enabled=false`；
- 飞猪监控组的 `auto_poll_enabled=false`。

旧 worker 进程可以继续运行，未关闭的其它门店旧任务也可以继续运行。新模块只处理已启用的全局配置，不自动修改或删除任何旧配置。

这里要区分两个开关：`auto_poll_enabled` 决定旧 scheduler 是否会产生 due 任务，必须在启用全局 scheduler 前关闭；美团/飞猪旧监控组的 `enabled` 不作为全局一次性读取的准入条件，否则会把“旧模块停用”误判为“账号不可读取”。

旧 scheduler 类、service、API、数据库记录和 Feishu 事件调用均不删除，供以下主动触发继续使用：

- 三个平台控制台的“立即读取/读取并推送”；
- 现有平台 `run-now` API；
- Hermes/Feishu `@机器人` 触发的 EBK 读取和问答；
- 携程“今日简报”手动预览或手动运行（如产品仍保留该入口）。

### 4.2 实现方式

新模块增加一个全局 EBK scheduler 任务；旧三个 scheduler 的代码和 worker 启动方式不改。旧 scheduler 是否运行由现有 worker 部署方式决定，但对已迁移门店必须先人工关闭旧排程字段，确保旧 scheduler 查询不到 due 任务。

新模块已增加自己的总开关和启动保护，而未删除旧代码：

- `EBK_GLOBAL_PUSH_SCHEDULER_ENABLED`：新 scheduler 总开关，默认 `false`，完成迁移后打开；
- 不新增或修改旧 scheduler 的启停语义；旧任务通过现有页面/API人工关闭；
- 旧排程关闭检查不是一次性的启用前校验：API 侧在启用/保存时校验并返回 `legacy_schedule_still_enabled`；scheduler 每个 tick 处理某全局配置前，还会重新确认对应携程、美团、飞猪旧 `auto_poll_enabled` 仍为关闭。启用后被运营重新打开旧开关的，tick 检测到冲突时自动将该全局配置软停用（`enabled=false`）、写一条 `failed` 运行记录（`error_summary=legacy_schedule_conflict`）并发一次告警，不重复读取。
- 已知游标机制：携程 `run_watch_group` 每次运行（含失败/human_required）都会回写锚点组 `last_polled_at`，旧盯盘 due 判定即基于该游标，因此双轨窗口期内全局运行会推迟旧盯盘的下一个 due。上述每 tick 反向校验 + 自动软停用即为消除该窗口的机制；切换顺序必须以旧开关先关闭为准（§4.3）。

数据库中的旧 `auto_poll_enabled`、`daily_brief_enabled` 字段和旧配置先原样保留。切换时由运营人员在现有配置页面手动关闭；不通过迁移脚本批量删除或强制覆盖。手动 API 和 Feishu 触发不受这些自动开关影响。

### 4.3 切换顺序

1. 发布新表、API 和 scheduler，但新 scheduler 保持关闭；旧三套代码和 worker 不做删除或改造。
2. 从现有携程、美团、飞猪组生成待迁移清单，确认门店、账号和 webhook 映射。
3. 创建全局配置并做一次 `run-now` 只读测试：依次读三平台、不发送旧平台卡，只发送全局分类卡；投递到已经由运营确认的携程锚点群。
4. 由运营人员在现有携程、美团、飞猪页面/API中逐个关闭对应旧自动排程；确认 `auto_poll_enabled` 已为 `false`（`daily_brief_enabled` 不动，见 §4.1）。
5. 打开全局 scheduler；观察至少一个完整固定时间槽或一个完整间隔周期。
6. 确认每个槽位每个平台最多一条读取记录、每个门店最多三个分类（同类超配额时分页连发，见 §5.5 第 7 条）；旧模块仍可通过手动入口正常触发。

## 5. 三张卡的数据契约

### 5.1 通用原则

每个卡片数据行统一为：

```json
{
  "platform": "ctrip",
  "dataset_id": "business_outline",
  "field_key": "realtime_booking_orders",
  "label": "实时预订订单",
  "value": 12,
  "value_type": "integer",
  "unit": "单",
  "time_scope": "realtime",
  "freshness": "realtime",
  "captured_at": "2026-08-26T08:00:32+08:00",
  "source_path": "datasets.business_outline.data.metrics.realtime_booking_orders"
}
```

`value` 保持解析器原始数值和类型；展示层只负责单位和千分位，不得把整数乘 10、除 10 或把百分数重复乘 100。`value_type`、`unit`、`time_scope` 和 `freshness` 是新投影层的标准化契约，并不代表当前三个平台 parser 已经全部输出这些字段；实现时必须先补充 parser 的时间/单位证据适配，再生成该行。

允许的 `freshness` 分类为：

- `realtime`：页面明确标识实时或今日当前；
- `current_state`：当前活动、当前报名状态等存量状态，不用于趋势计算；
- `score_snapshot`：当前可见评分快照，只能按评分例外规则使用；
- `unknown`：无法证明时间口径，禁止进入实时卡。

实时字段允许的证据包括：

- 字段名带 `realtime` 只能作为候选，不能单独证明实时；页面明确标记“实时/今日当前”才算有效证据；
- 数据块带当前读取时间且不是历史趋势；
- 平台 parser 输出的 `time_scope`/`freshness` 或等价、可审计的页面元数据。

实现已采用“parser 时间证据补齐 → 标准化 freshness/time_scope → 白名单投影”的两阶段流程。携程活动/广告与竞争维度、美团今日实时页签、飞猪今日更新时间和已参加活动均已接入可审计证据；仍无法证明时间口径的字段继续进入 `needs_review` 或被过滤，不能仅凭字段名归类。

明确排除：昨日、上周同期、上月、去年同期、同比/环比、历史数据、趋势图序列、预测值和没有时间范围的旧快照。排名只有页面明确标记“实时排名”时才允许进入经营卡。

展示文案约定（2026-08-28 运营确认）：三张卡本身即实时/当日口径（卡片有读取时间、按排程推送），展示层不重复「实时」「今日」字样——指标标签去掉「实时」「今日」前缀（如「实时预订订单」显示为「预订订单」、「今日核销」显示为「核销」），板块列「实时」「今日」「今日实时」「活动」（与活动卡分类重复）一律省略，有信息量的板块（如「在店」「预定」「流量」）保留；该规则由卡片显示层统一执行，不依赖 parser 适配层的标签写法。板块标题前的标志使用平台特有标识：携程 ✈️、美团 🦘、飞猪 🧳（失败态为「平台标志＋状态图标」如 🧳⚠️，不再使用通用数据集图标）。

### 5.2 卡片一：经营数据

卡片固定标题格式：

```text
经营数据｜携程 PSI {psi}｜携程店铺评分 {rating}
```

若评分未读取，使用“未读取”占位，不阻断其它经营数据。标题中的评分来源必须是本轮携程 `business_outline` 的实际 `value`，并保留页面精度；不得把美团或飞猪评分套入携程标题。

建议数据白名单：

| 平台 | 数据集 | 实时字段/区块 | 备注 |
|---|---|---|---|
| 携程 | `business_outline` | 仅页面明确标为实时的预订/在店/访客/排名字段 | APP 漏斗 `list_exposure`、`app_detail_visitors`、`order_page_visitors`、`order_submitters` 当前是概要页昨日口径，不进入实时卡； PSI/店铺评分按评分快照例外处理 |
| 携程 | `competition_profile` | 页面明确标为实时的竞争圈排名或当前指标 | `ctrip_app_visitors`/`ctrip_app_conversion_rate` 不能仅凭字段名视为实时；月度经营对比整块排除 |
| 美团 | `workbench_home`、`data_center_home`、`data_center_trade` | `workbench_home.realtime_data`，以及读取层切换并校验“今日实时”页签后生成的 `today_realtime_metrics` | 默认“昨日”指标仍保留在完整快照供审计/导出，但不进入全局实时卡；DOM 选中态校验失败则弃用今日实时块 |
| 美团 | `data_center_traffic` | 经“今日实时”页签证据确认的曝光、浏览/访问人数、支付订单、支付转化率 | 保留原始单位和数值类型；不得把无时间范围的旧快照当实时 |
| 美团 | `data_center_service` | 只有页面明确当前的服务分、评价分、待回复差评等 | 仅展示当前值，不展示历史趋势；不能作为携程店铺评分 |
| 美团 | `comment_manage_home` | 当前评价分和待处理量（页面明确当前且 parser 有证据时） | 不能作为携程店铺评分 |
| 飞猪 | `home_v1` | `today_data` 中有明确今日口径的销售额、浏览人数、预订订单量、预订间夜、转化率等 | `yesterday_data` 排除 |
| 飞猪 | `operating_data` | `today_evidence.today_confirmed=true` 后的在店、预定、流量区数据 | 以页面当日“数据更新时间”和今日选中态为证据；证据不足时过滤或 `needs_review` |
| 飞猪 | `diagnose_report` | parser 证明为当前的交易概览、宾客评价分、MCI | 评分作为飞猪区块字段，不改写携程标题 |

没有可信 PSI 的平台不显示伪造的 PSI。平台等价评分不进标题；2026-08-28 运营确认：标题只附加携程 PSI/携程店铺评分，三平台存量评分同时统一显示在经营卡正文顶部的「评分总览」小节——携程 PSI/携程店铺评分（business_outline）、美团 HOS（workbench_home.hos_score）/美团点评分（comment_manage_home.review_score）、飞猪点评得分（diagnose_report.guest_review_score），均按 score_snapshot 口径，数据集表格不重复展示评分行。

### 5.3 卡片二：活动促销

| 平台 | 数据集 | 允许内容 |
|---|---|---|
| 携程 | `promotion_effect` | 页面“今日实时数据”区块中带 realtime 证据的参加促销/总促销数等 | 昨日/过去 7 天/过去 30 天主表不进入实时活动卡 |
| 携程 | `promotion_registered` | 当前已报名/进行中的活动名称、状态、有效期 | 真实 parser 结构为 `data.promotions`，活动时间兼容 `promotion_time`；作为 `current_state` |
| 美团 | `promotion_coupon` | parser 证明为当前的神券/活动名称、状态、活动时间、今日活动指标 | 当前没有统一时间元数据，证据不足时 `needs_review` |
| 飞猪 | `promotion_agent` | parser 证明为当前的已参加活动、状态、有效期、房型/活动强度等字段 | “已参加活动”按 `current_state` 进入活动卡；无证据时 `needs_review` |

活动卡不展示已结束活动的历史效果，不把促销建议或 AI 建议当作实时事实。无活动时显示“当前无可展示活动”，与读取失败的“读取失败”区分。

### 5.4 卡片三：广告营销

| 平台 | 数据集 | 允许内容 |
|---|---|---|
| 携程 | `ad_pyramid` | 页面今日筛选成功且 parser 输出证据后的曝光、点击、点击率、花费、预订金额、ROAS 等区块 |
| 美团 | 暂无独立广告数据集 | 显示“当前版本未配置广告数据”，不从交易/流量字段猜广告字段 |
| 飞猪 | 暂无独立广告数据集 | 显示“当前版本未配置广告数据”，后续有真实页面和 parser 后再加入白名单 |

如果后续平台增加广告页面，先增加 parser、字段元数据和测试，再增加白名单；不允许用通用文本搜索把“曝光量”直接归类为广告。

### 5.5 卡片布局

“三张卡”是逻辑分类边界，不是消息数量上限；每次成功触发按三个分类组装并投递卡片（同类超配额时分页连发，见下第 7 条），卡片内按平台分组：

1. 顶部显示门店、读取时间、整体状态。
2. 经营卡标题显示携程 PSI/店铺评分；正文按携程、美团、飞猪分组。
3. 活动卡和广告卡显示分类标题、平台状态和表格。
4. 多字段使用表格列；长文本放入表格单元格，外层容器负责横向滚动，不能依赖点击展开来“补看”被截断文本。
5. 平台无该类数据时显示明确的“暂无数据/未配置”；平台读取失败时显示“读取失败”和下一步，不伪装为空。
6. 卡片 builder 必须保持三类的逻辑边界；不得因为某个平台数据集多而恢复旧版“一数据集一张卡”的推送方式。
7. 单张飞书卡最多 5 个表格组件（`feishu_card_tables` 现行硬配额）。同一分类的表格需求超过 5 个时，按 CardUX5 既有机制同类分页连发：标题带“（i/N）”、组跨页标“（续）”、每页最多 5 表。分页连发是同类容量分页、分类边界不变，与第 6 条禁止的“一数据集一张卡”（拆散分类）是两回事；“压缩/合并板块”的替代方案已被 2026-08-26 CardUX5 定稿否决，不得采用。
8. 卡片标题拼接规则：`card_title_*` 自定义值仅替换分类前缀（如“经营数据”），固定后缀（评分段等）始终追加，格式为 `自定义前缀｜固定后缀`；未自定义时使用默认前缀。

建议复用表格渲染器的行拆分和滚动能力，但新增独立的 `global_push_cards` builder，不能修改旧 `build_platform_snapshot_feishu_card` 的默认行为。卡片 builder 只消费“实时/当前状态投影”，不能反过来成为 Excel 导出的数据源。

## 6. 评分读取与标题规则

携程生产 parser 输出为指标对象：

- `business_outline.data.metrics.psi_service_score.value`：PSI 服务质量分；
- `business_outline.data.metrics.hotel_review_score.value`：酒店点评分。

实时经营指标另位于 `business_outline.data.time_dimensions.realtime.metrics`，不能和上述评分对象混读。这两个评分不是经营趋势数据，而是本次成功读取时页面可见的存量评分；因此允许作为“评分快照例外”进入经营卡标题和正文评分总览，不得用于同比、环比或趋势计算。读取失败、对象缺少 `value` 或各自范围校验失败时才显示“未读取”。

新模块在投影层增加 `extract_ctrip_score_summary()`，职责是：

1. 从本次成功的携程 `business_outline` 结果读取两个字段；
2. 按字段分别校验数值范围和 `value_type`：PSI 使用 0–6，酒店点评分使用 0–5，拒绝把排名或百分比当评分；
3. 保留页面实际 `value` 的精度（例如 `5.44`、`4.79`），不再统一四舍五入为一位小数；
4. 生成标题 suffix 和正文评分行；
5. 标记来源维度和口径（`score_snapshot`、页面当前可见/昨日口径）；任一评分缺失时只填该项“未读取”，不影响经营卡主体。

美团的 `review_score`、飞猪的 `guest_review_score` 只作为各自平台正文数据。不得因为字段名都叫“评分”而跨平台覆盖携程评分。

## 7. 配置和数据库设计

### 7.1 全局推送配置表

建议新增 `ebk_global_push_configs`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BigInt PK | 配置 ID |
| `ctrip_watch_group_key` | String(64) | 门店/投递群锚点，普通唯一约束；同一锚点只保留一条记录，停用采用软停用 |
| `name` | String(128) | 配置显示名称 |
| `enabled` | Boolean | 是否允许新 scheduler 执行 |
| `enabled_platforms_json` | JSON | `['ctrip','meituan','fliggy']` 子集，至少一项 |
| `schedule_mode` | String(32) | `fixed_slots` 或 `last_run_interval` |
| `push_times_json` | JSON | 固定模式的 `HH:MM` 数组，去重排序 |
| `interval_minutes` | Integer | 间隔模式，范围 30-1440（见 §7.3） |
| `window_start_time` | String(5) | 间隔模式起始时间 |
| `window_end_time` | String(5) | 间隔模式结束时间，可跨午夜 |
| `timezone` | String(64) | 默认且建议固定 `Asia/Shanghai` |
| `card_title_business` | String(128) | 经营卡标题前缀，可选 |
| `card_title_promotion` | String(128) | 活动卡标题，可选 |
| `card_title_advertising` | String(128) | 广告卡标题，可选 |
| `last_attempted_at` | DateTime | 最近一次尝试 |
| `last_successful_push_at` | DateTime | 最近一次成功投递 |
| `next_run_at` | DateTime | 计算后的下一次执行时间 |
| `consecutive_failure_count` | Integer | 连续失败计数；间隔模式退避档位与 worker 重启恢复依据，任一次成功后清零 |
| `created_at`/`updated_at` | DateTime | 审计字段 |

不在本表复制 `platform_account_id`、`browser_session_id`、`feishu_webhook_url`、`feishu_chat_id`。这些值必须从当前携程组和平台挂载组解析。

### 7.2 运行记录表

建议新增 `ebk_global_push_runs`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BigInt PK | 运行 ID |
| `config_id` | FK | 全局配置 |
| `trigger` | String(32) | 运行创建方：`scheduler`、`operator`、`feishu`、`test`。与快照读取来源标记是两回事：`payload_json.read_trigger.via=global_push` 是快照侧元数据（§8.2.1），不是本字段取值 |
| `schedule_key` | String(128) | `config_id + 本地日期 + 槽位/间隔桶` |
| `status` | String(32) | `running`、`success`、`partial`、`failed`、`config_required`、`human_required`、`skipped_overlapping`（同配置互斥跳过）、`missed_slot`（超容忍窗口丢槽） |
| `read_started_at`/`read_finished_at` | DateTime | 读取耗时 |
| `push_started_at`/`push_finished_at` | DateTime | 投递耗时 |
| `platform_results_json` | JSON | 每平台 snapshot ID、状态和字段计数 |
| `card_results_json` | JSON | 三张卡的 Feishu 返回状态，不保存 webhook |
| `error_summary` | Text | 脱敏后的运营错误 |
| `error_fingerprint` | String(64) | 脱敏错误摘要哈希；与配置表 `consecutive_failure_count` 一起作为 worker 重启后告警冷却与退避恢复的持久化依据 |
| `created_at` | DateTime | 创建时间 |

在 `config_id + schedule_key` 上建立唯一约束，作为跨进程幂等保护。所有迁移走各 domain migration 和 `db/session.py` 注册；不得删除旧表或旧运行记录。

运行整体 `status` 由平台结果与卡片投递结果按严重度聚合，严重度序为 `human_required > failed > partial > needs_review > config_required > success`：任一已启用平台读取失败、或三卡全部投递失败 → `failed`；读取成功但部分卡片投递失败、或任一平台 `needs_review` → `partial`；存在 `config_required` 平台且其余平台全部成功 → `config_required`；无任何非成功结果 → `success`。`platform_results_json` 保留每平台明细，展示与导出以明细为准。

现有 `save_ebk_snapshot()` 只保留 `read_trigger.actor_id` 和 `read_trigger.via`，其余 key 会被静默丢弃。精确扩展点：`read_meituan_platform_snapshot()` / `read_fliggy_platform_snapshot()` 的 `trigger_via` 参数直通落库、不经过 `_resolve_trigger_via()`，因此 `via=global_push` 今天即可落库；必须修改的是 `save_ebk_snapshot` 的保留键集合（增加 `global_run_id`）。`_resolve_trigger_via()` 的合法值仅被统一读取器 `read_all_ebk_snapshots` 使用，本方案不复用该入口，无需扩展。携程侧载体见 §8.2.1（run 表无 `read_trigger`/`run_kind` 列，只有 `source` + `actor_id`）。扩展完成前不得在文档或接口中宣称这些元数据已落库。

### 7.3 配置校验

- `fixed_slots`：`push_times_json` 非空，每个值符合 `^([01]\d|2[0-3]):[0-5]\d$`，去重、按分钟排序；允许 `00:00`，按当日零点槽位处理。
- `last_run_interval`：`interval_minutes` 30-1440（单次全局运行为分钟到十分钟级，更小间隔在同配置互斥约束下无法兑现，故下限 30）；开始/结束时间合法；允许跨午夜。
- 锚点携程组 `enabled=false` 时，全局运行记 `config_required`，不执行读取（`run_watch_group` 本身不检查 `group.enabled`，必须在调度层拦截）。
- 同一配置同一时刻只允许一条 `status=running` 运行：以运行表对 `(config_id)` 的部分唯一索引（仅 `status=running`）或事务内条件插入实现；`running` 超过 45 分钟视为 stale，tick 先将其标记 `failed` 再允许新运行。
- 前端可显示“24:00”，存储统一为次日 `00:00` 的槽位语义，避免使用超出既有正则的 `24:00`。
- 固定时间模式不依赖 `last_successful_push_at` 判断当天同一槽位是否执行，必须使用 `schedule_key`。
- 间隔模式以 `next_run_at` 为唯一执行依据（单一游标，避免双基准冲突）：首次运行基准为配置启用时刻——启用时处于推送窗口内则下一个 tick 即执行，否则顺延到下一个 `window_start`。此后在每次运行结束时重算：成功或部分成功（至少一张卡投递成功）→ `结束时间 + interval_minutes`；失败（读取失败或三卡全部投递失败）→ `结束时间 + 退避时长`（见 §8.3）。`last_successful_push_at` 仅作展示与审计，不参与 due 计算，且只在至少一张卡投递成功时更新。
- 时区默认 `Asia/Shanghai`。本期不开放任意 IANA 时区，避免 scheduler、UI 和数据库出现不同日期。

## 8. 调度实现细节

### 8.1 调度循环

新增 `EbkGlobalPushScheduler`，由 `read_only_scheduler.py` 启动一个实例。它是三平台本次读取的唯一自动触发方：到达全局配置的时间槽后，由它调用现有平台读取器，保存完整快照，再生成三张卡并推送。它不调用旧平台卡片 builder，也不改变手动/@机器人触发链路。

```text
每 60 秒 tick
  → 查询 enabled=true 的全局配置
  → 反向校验锚点旧排程开关仍关闭（§4.2），冲突则软停用并告警
  → 计算 fixed slot / interval 是否 due
  → 以唯一 schedule_key 抢占运行记录，并确认该配置无 running 记录（互斥，§8.3）
  → 预解析当前携程组、三平台账号和浏览器会话；缺账号的平台记为 config_required 并跳过真实读取
  → 按梯队读取：携程 → 美团 → 飞猪
  → 保存已配置平台的完整快照，并在运行表记录缺失平台状态
  → 投影 time_scope=realtime/current_state；评分按 score_snapshot 例外进入标题/正文评分总览
  → 组装经营/活动促销/广告营销三张卡
  → 按顺序投递三张卡
  → 写运行结果、下次执行时间和心跳
```

scheduler tick 与 `run-now` 共用同一个 `run_global_push` 读取/投影/推送核心，但入口检查不同，不能写成完全相同：

- scheduler tick：只查询全局配置 `enabled=true` 的记录 → 反向校验旧 `auto_poll_enabled`（`daily_brief_enabled` 不检查）→ due/槽位判断 → 抢占运行记录 → 执行全局读取。冲突时软停用全局配置并告警。
- `run-now`：允许在全局配置尚未启用时做受控测试，也不以旧 `auto_poll_enabled` 作为读取准入；它先回收 stale `running`，再抢占 manual 运行键，同配置已在运行则返回 409。它不修改旧开关，也不发送旧平台卡。
- 两个入口进入读取核心后都要求携程锚点存在且 `enabled=true`，再预解析各平台真实账号和浏览器会话；缺失平台记 `config_required` 并继续其它平台。美团/飞猪旧监控组的 `enabled` 不在此准入条件中。

#### 8.1.1 读取梯队与间隔

三个平台不建议在第一版同一时刻启动。虽然不同平台、不同 profile 在技术上可以并发，但同时读取会增加 CPU/内存峰值、触发 OTA 风控的概率，并且在同一账号或共享 profile 时会直接触发 Chrome `ProcessSingleton`/页面锁冲突。

第一版采用内部梯队策略，不把它暴露成运营排程字段：

1. 携程先读；读取结束后冷却 30 秒；
2. 美团再读；读取结束后冷却 30 秒；
3. 飞猪最后读取；
4. 三个平台都尝试完成后，再统一生成并发送三张卡。

若某个平台失败、登录失效或需要人工处理，记录该平台状态后继续下一平台，不因单个平台失败阻断其它平台。冷却时间建议配置为 15-60 秒，默认 30 秒；第一版全局并发数为 1：不同门店的全局配置在同一 worker 内按 config_id 串行执行。后续只有在实测 profile 隔离、资源和风控都稳定后，才考虑将不同 profile 的最大并发数提高到 2。无论全局并发值如何设置，同一 `browser_session_id` 永远只能有一个读取任务。

`read_all_ebk_snapshots` 虽然按携程、美团、飞猪顺序调用，但它的携程分支只走 `read_monitor` 六项，且缺账号时会抛 `KeyError`/`ValueError`；它不能同时满足本方案的 10 项数据集和“缺平台继续读取”的要求。新 scheduler 必须在调度层显式保留上述梯队和 profile 锁，并在读取前完成账号预解析，不依赖统一读取器的异常来表达 `config_required`。

### 8.2 读取策略

全局编排分别调用三个独立平台读取入口，每个平台只走一条路径：

1. 携程使用 `DataCenterWatchService.run_watch_group(source="global_push", actor_id="ebk_global_push_scheduler", push_to_feishu=False, dataset_ids=..., include_disabled_datasets=True)` 的显式全局白名单。第一版白名单为 9 项（数据中心 10 项去掉 `comment_monitor`）：`comment_monitor` 不服务三卡任何一类，且多一次页面导航成本，不纳入全局读取；其数据仍可由旧盯盘/手动链路读取。全局白名单必须校验实际 parser 可实现集合，不能把 `read_monitor` 六项误称为白名单全集；缺少数据集记录、parser 或页面证据的项记录 `needs_review`，不能被旧的 `enabled` 开关静默过滤。
2. 美团使用 `read_meituan_platform_snapshot()`，飞猪使用 `read_fliggy_platform_snapshot()`；平台之间的 30 秒冷却由 scheduler 控制。
3. 不调用旧平台 watch service 的推送路径，由新模块统一生成三张卡：携程侧显式传 `push_to_feishu=False`；美团/飞猪的读取函数本身没有推送参数（推送控制本就在各平台 watch service 层），新 scheduler 直调读取函数天然不推送。不能在同一运行中再调用旧平台 service 或旧卡片 builder。
4. 美团/飞猪的旧监控组 `enabled=false` 只代表旧模块停用，不影响上述全局单次读取；只有账号或浏览器会话绑定缺失/失效才记 `config_required` 或 `human_required`。

不直接把 `read_all_ebk_snapshots()` 作为新全局完整实现：它适合现有统一快照读取，但当前携程只读六项，且对缺少 session 直接抛异常。若后续需要复用其公共逻辑，应先增加显式 `dataset_ids`、缺账号结果和全局触发元数据支持，再由全局 scheduler 调用。

无论是否读取完整数据集，推送层都必须执行实时/当前状态投影白名单。持久化快照可以包含历史字段，用于审计和后续导出，但不能出现在本次三张卡；评分只能按第 6 节的快照例外进入经营卡标题和正文评分总览。

### 8.2.1 全局推送与 Excel 导出的边界

全局推送和 Excel 导出是两条独立链路：

```text
全局 scheduler
  → 三个平台真实读取
  → 完整读取结果落库（美团/飞猪写 ebk_read_snapshots；携程保留既有 ctrip_ebooking_watch_runs，并由全局运行表关联 run/snapshot ID）
  → 实时字段投影
  → 三张飞书卡

Excel API
  → 读取 ebk_read_snapshots + 携程既有 watch runs
  → 业务指标规范化
  → 经营简报/APP 转化/竞争圈/明细等 Sheet
```

因此，三张飞书卡只展示经营、活动促销和广告营销的实时/当前状态白名单，不会删掉或覆盖读取结果中的完整数据；Excel 已有 `watch_runs_to_snapshots()` 兼容携程 watch runs，只要全局 scheduler 保存完整标准读取结果，导出不会因为卡片拆分而丢字段，也不会读取卡片中的截断文本。

触发元数据落库按平台分两侧实现：

- 美团/飞猪：在扩展后的 `payload_json.read_trigger` 中标记 `via=global_push` 和 `global_run_id`（精确扩展点见 §7.2），并在全局运行表保存已生成的 `snapshot_id`。
- 携程：`ctrip_ebooking_watch_runs` 表没有 `read_trigger`/`payload_json`/`run_kind` 列（`run_kind` 仅是 service 层参数，不持久化），元数据载体只能是 `source` + `actor_id`。全局 scheduler 调用 `run_watch_group` 时固定传 `source="global_push"`、`actor_id="ebk_global_push_scheduler"`。读取返回中的真实关联 ID 位于 `payload.persistence.run_id`，全局运行表必须从该嵌套路径保存携程 run ID，不能只读顶层 `run_id`。

Excel“数据来源”列同步扩展两个标签映射：`_watch_run_source_label` 增加 `source="global_push"` → “全局定时推送”；`_snapshot_source_label` 增加 `via="global_push"` → “全局定时推送”。不扩展的话，全局运行在导出中会退化为“未标记”。列结构契约不变，仅新增标签值。

Excel 继续使用现有 `/api/v1/export/ebk-business-data?format=xlsx` 链路，平台字段和账号实际名称照旧输出。建议导出层对同一 `global_run_id` 使用固定顺序 `携程 → 美团 → 飞猪`；多个运行混合时先按快照时间，再按该平台顺序排序。若只按当前时间排序，三平台仍能导出，但不同平台读取耗时不同，表格顺序可能随时间变化；固定排序可以保持用户熟悉的排版。

导出裁剪规则：现有 `merge_export_snapshots()` 按时间全局截断到 limit，可能把同一 `global_run_id` 的三平台结果拆散。本次扩展为按运行成组裁剪——先按 `global_run_id` 分组（组时间取组内最新 `captured_at`），按组裁剪到 limit，组内保持 `携程 → 美团 → 飞猪` 固定顺序；无 `global_run_id` 的记录各自成组，行为与现状一致。

### 8.3 幂等、并发和重试

- 抢占 `schedule_key` 使用数据库唯一约束或短租约；重复 worker 只能得到 `already_claimed`。
- 同配置互斥：`schedule_key` 唯一只防同槽重复，不防相邻槽重叠——间隔模式的相邻桶是不同 `schedule_key`。因此必须另有 per-config 互斥：同一配置存在 `status=running` 记录时，新 due 记 `skipped_overlapping`，不推进任何游标（`next_run_at` 保持原值），互斥解除后的下一个 tick 自然补上本次 due，绝不并发读取同一 `browser_session_id`（与 §8.1.1 硬规则一致）。
- 固定槽位：槽位开始后允许一次短暂延迟执行；容忍窗口默认 30 分钟，需覆盖多门店同刻排队（第一版全局并发 1，同刻门店按 config_id 串行）。超过容忍窗口记录 `missed_slot`，不要补发多张历史卡。
- 间隔模式退避：连续失败采用 1、2、5、10 分钟递增退避，最长不超过一个配置间隔。退避状态持久化：连续失败计数存配置表 `consecutive_failure_count`、错误指纹存运行表 `error_fingerprint`，worker 重启后从计数恢复退避档位，不立即重试；任一次成功后计数清零。
- 三张卡使用同一个运行 ID。单卡失败时继续发送其它卡，并把运行标记为 `partial`；三卡全部失败才使用旧的纯文本 fallback（如 Feishu API 支持）。
- Feishu 成功返回后才写 `last_successful_push_at`（至少一张卡投递成功即可更新；该字段仅作展示与审计，不参与 due 计算）；只读成功但三卡全部投递失败按失败退避处理，不能被当作成功推送。
- 手动旧链路的节流 key 与新链路分开，建议 `ebk_global_push:<config_id>:<schedule_key>`，不能因为手动推送而丢失当天固定槽位，也不能让新推送反向节流问答卡。

### 8.4 失败和告警

失败分类：

- `config_required`：缺少锚点、账号或 webhook；
- `human_required`：登录、验证码、风险控制或 profile 被人工占用；
- `needs_review`：页面结构或字段证据不足；
- `failed`：网络、解析或 Feishu API 错误。

`EbkSnapshotStatus` 当前只有 `success`、`partial`、`human_required`、`needs_review`、`error`，没有 `config_required`。因此 scheduler 必须先解析账号和 session：缺配置的平台只写全局运行表的 `config_required` 平台结果，不调用统一 snapshot 保存假的空快照，并继续读取其它已配置平台。

失败不生成“全是空值”的成功卡。告警接线必须显式实现：美团/飞猪现有失败告警卡挂在各平台 watch service 收尾路径，新 scheduler 直调读取函数不会触发它们。新模块复用 `platform_ebk_watch/push_policy.py` 的冷却函数（`should_send_failure_alert`/`record_failure_alert`）与 `ebk_snapshots/feishu_push.py` 的 `build_platform_failure_alert_card`，按独立节流 key `ebk_global_push_failure:<config_id>:<platform>` 投递到锚点携程组 webhook。新模块为三平台统一接线该告警路径——携程现有 service 收尾逻辑不含失败告警冷却（未复用 push_policy），此为本次新增能力而非现有行为。60 分钟告警冷却是进程内存态，worker 重启后会重置；V1 可接受该语义，但必须监控重复告警。运行幂等仍由 `schedule_key` 唯一约束负责；如需重启后保持告警冷却，以运行表 `error_fingerprint`（§7.2 已定义）作为持久化判断依据，退避档位的重启恢复见 §8.3。告警只包含门店、平台、状态和下一步，不暴露密码、cookie、profile 路径、webhook 或原始 Playwright 日志。

## 9. API 设计

新增 router 建议前缀 `/api/v1/ebk-global-push`：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/configs` | 列出当前操作员可见的全局配置和脱敏运行状态 |
| POST | `/configs` | 创建配置；校验携程组、平台绑定和时间模式 |
| GET | `/configs/{id}` | 查看单个配置、解析后的平台账号状态和下一次执行时间 |
| PATCH | `/configs/{id}` | 更新开关、平台选择、标题和排程；敏感值不回显 |
| DELETE | `/configs/{id}` | 软停用并保留运行历史，二次确认后才允许物理删除 |
| POST | `/configs/{id}/run-now` | 操作员主动执行一次新三卡流程，默认标记 `trigger=operator` |
| GET | `/configs/{id}/runs` | 查看最近运行结果和三卡状态 |
| GET | `/scheduler-status` | 返回新 scheduler 心跳、最近 tick 和当前 due 数量 |

`run-now` 是新三卡测试入口，不替代旧平台 `run-now`。现有旧 API 和 Feishu 事件路由继续调用原 service 和原卡片 builder。

`run-now` 幂等语义：不占用固定槽位 `schedule_key`，运行记录使用独立键 `manual:<config_id>:<运行创建时间戳>`（`trigger=operator`，时间戳为 UTC 含毫秒精度），不影响当天固定槽位执行；若同一毫秒内重复手动触发导致撞 `(config_id, schedule_key)` 唯一约束，按 `already_claimed` 语义返回 409，不重试（同刻重复手动触发本属冗余，等效于互斥拦截）。另受同配置互斥约束——存在 `running` 记录时返回 409 冲突，不并发读取；间隔模式下 run-now 结束后同样按 §7.3 游标规则重算 `next_run_at`。

API 响应只返回账号展示名、平台、状态和运行摘要。禁止返回 webhook URL、chat ID 原值、密码、cookie、浏览器 profile 路径和截图路径。

## 10. 前端交互设计

在 `EbkOpsConsole` 的携程、美团、飞猪 tab 后增加“全局定时推送” tab。页面顺序遵循：当前状态 → 配置 → 风险/历史。

### 10.1 配置表单

1. 选择携程监控组/门店。
2. 展示已解析的携程、美团、飞猪账号名称和登录状态；账号绑定入口仍回到各平台模块。
3. 平台启用复选框：携程、美团、飞猪。
4. 调度模式下拉：
   - 固定时间点：多选时间控件，示例 `08:00、12:00、20:00、00:00`；
   - 按间隔推送：间隔分钟 + 开始时间 + 结束时间。
5. 三张卡标题可编辑，经营卡预览明确显示评分来源。
6. 开关：全局推送启用/暂停。
7. 显示最近成功、最近失败、下次执行和 scheduler 心跳。
8. 提供“立即读取并推送”主按钮，明确提示按“携程→美团→飞猪”读取并发送全局分类卡，不改变旧平台定时配置。
9. 排程控件可复用 `EbkWatchSchedulePanel` 的固定时刻/间隔两模式，但其 `platform` prop 当前限定 `"meituan" | "fliggy"`，复用前需先泛化类型，或在全局推送 tab 内新建独立面板；不得改变现有三平台面板的行为。

前端不得再在新模块复制 webhook 输入框。缺少 webhook 时显示跳转到携程监控组配置的链接和 `config_required` 状态。

按钮层级以携程操作台为基准：立即读取使用 `primary-button` + 播放图标；保存定时配置、启用/暂停使用 `secondary-button` + 保存/播放/停止图标；创建或保存全局配置使用明确的主按钮。全局“暂停推送”已经覆盖软停用语义，不再并列显示重复的“软停用”按钮。

### 10.2 旧模块文案

携程、美团、飞猪原模块保留“立即读取/主动推送”和问答说明，但将旧的自动轮询开关标记为“已由全局定时推送接管”，避免操作员误以为打开旧开关会产生第二次推送。兼容期可以只读展示旧字段，切换完成后隐藏或禁用。

## 11. 测试方案

### 11.1 后端单元测试

- 固定时间多选：排序、去重、空数组、跨午夜、`00:00` 次日语义。
- 间隔模式：首次执行、成功后间隔、失败退避、开始/结束相等和跨午夜。
- 实时投影：每个平台只保留有 parser 时间证据的白名单字段；昨日、同比、历史趋势全部被过滤，评分仅按 `score_snapshot` 例外进入标题。
- 数据集路径：携程全局读取覆盖明确白名单；确认 `read_monitor` 六项不会替代活动/广告数据集，缺失 parser 证据进入 `needs_review`。
- 时间元数据：美团、飞猪及携程活动/广告数据集缺少统一 `time_scope/value_type/unit` 时，验证不会把字段误判为实时。
- 数值类型：整数、金额、百分比、评分、排名不发生数量级变化；缺单位字段进入 `needs_review`。
- 评分标题：生产指标对象 `label/value`、两个评分均有、单项缺失、全部缺失、非法范围；PSI 0–6 与酒店评分 0–5 分别校验并保留实际精度。
- 挂载解析：repository 字典结果不得导致美团/飞猪漏触发；旧监控组 `enabled=false` 仍可凭有效账号/session 完成全局单次读取，缺绑定才是 `config_required`。
- 运行关联：携程 `payload.persistence.run_id` 能写入全局平台结果并供 Excel 成组裁剪。
- 卡片缺平台状态：`config_required` 的携程/美团/飞猪在经营、活动和广告卡均有明确状态块，不能被省略或被广告占位覆盖。
- 三张卡：始终按经营/活动促销/广告营销生成；无广告数据时显示明确占位，不伪造字段。
- 幂等：相同 `config_id + schedule_key` 只能创建一个运行；并发 tick 不重复读取。
- 同配置互斥：上一运行未结束时相邻间隔桶记 `skipped_overlapping`，不推进任何游标（`next_run_at` 保持原值，互斥解除后下一 tick 自然补上），不并发读取；stale running 超 45 分钟被回收后可新开运行。
- 导出来源标签：`source="global_push"` 的携程 run 与 `via="global_push"` 的快照在 Excel“数据来源”列显示“全局定时推送”，不出现“未标记”。
- 反向防护：启用后重新打开旧 `auto_poll_enabled`，下一 tick 软停用全局配置、写 `legacy_schedule_conflict` 运行记录并告警。
- 间隔模式游标：首跑基准（窗口内启用即执行/窗口外顺延到 `window_start`）、部分成功与全败后的 `next_run_at` 重算、`skipped_overlapping` 不推进游标、重启后从 `consecutive_failure_count` 恢复退避档位。
- 运行状态聚合：`human_required/failed/partial/needs_review/config_required/success` 严重度排序与混合场景取最重。
- `run-now` 幂等：不占固定槽位 `schedule_key`、`running` 时返回 409、结束后重算 `next_run_at`。
- 导出成组裁剪：同一 `global_run_id` 的三平台结果不被 limit 拆散，组内保持固定平台顺序。
- 失败状态：`human_required/config_required/failed` 不生成成功空卡；告警冷却有效。
- 旧链路回归：旧平台 `run-now`、`run_daily_brief_now` 和 Feishu 事件仍调用原 service 和原卡片模板。

### 11.2 API 和集成测试

- 配置创建/更新校验平台账号、锚点和排程模式。
- 操作员 API key 保护写操作；GET 脱敏响应不泄露 webhook 等秘密。
- scheduler status 能反映 stopped、running、degraded。
- 三平台一个失败、两个成功时，三张卡状态和运行记录为 `partial`，并不丢失成功平台数据。
- Feishu 三卡 payload JSON schema 校验，确认表格长文本可横向查看；同类表格需求超每卡 5 表配额时分页连发（标题 i/N、组跨页标“（续）”），分类边界不变。

### 11.3 推荐验证命令

实现阶段按 `AGENTS.md` 执行聚焦测试：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m unittest backend.tests.domain.test_ebk_global_push_scheduler
.\.venv\Scripts\python.exe -m unittest backend.tests.domain.test_ebk_global_push_cards
cd frontend
npm.cmd test
npm.cmd run build
```

若改动涉及现有 EBK/Feishu 链路，还要运行 README 中对应的后端聚焦套件；`Ran 0 tests` 视为失败。

## 12. 上线、监控和回滚

### 12.1 上线检查

- 迁移前导出旧三平台组、账号、锚点、webhook 是否配置（只导出脱敏值）。
- 一一确认全局配置到门店、携程组和平台账号的映射。
- 确认同一 browser session 没有被并发绑定到不同平台任务。
- 监控 `legacy_schedule_conflict` 软停用事件与 `skipped_overlapping`/`missed_slot` 计数：前者说明运营改回了旧开关，后两者异常增多说明单次运行时长或同刻并发估算需要调整。
- 先测试三张卡的真实 Feishu 投递，再打开正式时间槽。
- 监控新心跳 `ebk_global_push_scheduler`、tick 延迟、各平台读取平均耗时、运行成功率、`partial` 比例、Feishu 失败率、profile lock 错误，以及 worker CPU/内存；新模块会让 `read_only_scheduler.py` 从现有 6 个 loop 增加到第 7 个 loop。

### 12.2 回滚

回滚必须可在不删数据的情况下完成：

1. 关闭 `EBK_GLOBAL_PUSH_SCHEDULER_ENABLED`。
2. 确认没有 `running` 的全局任务后，按迁移前清单由运营人员恢复对应组的 `auto_poll_enabled`（`daily_brief_enabled` 未被本方案改动，无需恢复）。
3. 旧 scheduler 代码和 worker 无需恢复或重新部署；只确认旧配置已恢复并按原方式工作。
4. 保留新配置表和运行记录，标记为 `rolled_back`，不得删除快照。
5. 如回滚期间需要手动推送，继续使用原平台 API/Feishu 入口。

切换期间禁止新全局配置和旧自动排程同时对同一门店启用。必要时以数据库租约和新模块启动保护双重控制；不要求停止旧 scheduler 进程。

## 13. 产品决议

1. 已决议：“全局”按门店/携程监控组一条配置，不是所有门店共用一个 webhook 和时间表。
2. 已决议：经营卡标题只显示携程 PSI/店铺评分；正文“评分总览”分别显示三平台有可信来源的评分，不跨平台改名或替代。
3. 已决议：美团和飞猪当前没有已确认的广告数据集，广告卡保留“当前版本未配置广告数据”区块，不隐藏平台。
4. 已决议：允许 `00:00`，按当日零点槽位处理；不存储 `24:00`。
5. 已决议：间隔模式下限 30 分钟（受同配置互斥与单次运行时长约束，5 分钟无法兑现），默认 60 分钟。
6. 已决议：采用默认值——三卡单轮失败不自动重试（下一槽位自然重试），间隔模式退避 1、2、5、10 分钟且上限一个间隔，60 分钟告警冷却；告警投递到锚点携程组 webhook。
7. 已决议：每日简报不迁移。`daily_brief_enabled` 保持原值，定时简报继续由旧携程 scheduler 生成，手动入口保留；仅 `auto_poll_enabled` 进入迁移清单。
8. 空壳携程锚点组已按 2026-08-24 设计确认允许；本方案只需在实现时落实缺携程账号的 `config_required` 运行结果，不再等待产品确认。

## 14. 结论

当前实现按现有携程监控组建立全局配置，以新 scheduler 统一编排三次独立平台读取和全局分类卡；旧平台 service、手动 API、Feishu 问答和旧卡片模板保持不变。新卡片只接收带实时证据的字段，评分显示本轮真实值，评分和广告真实缺失时如实表达，不能用默认值或历史值补齐。

该方案可以在不复制账号/webhook、不破坏快照和手动链路的前提下完成调度收敛，并通过唯一槽位记录、profile 锁、失败冷却和可逆开关控制重复推送风险。
