# 三平台（携程/美团/飞猪）调价全流程操作剧本与代码修改方向

> 创建日期：2026-08-21
> 性质：**开发方向设计文档，未修改任何代码与数据。**
> 上位约束：`AGENTS.md`「调价安全契约（Pricing Safety Contract — authoritative）」全文适用；
> 本文档是该契约在三平台页面操作层的落地模拟，不得削弱其中任何一条硬规则。
> 关联计划：`docs/pan-hotel-watch-dev-plan-v9.md`（V9 美团/飞猪调价支持）。

---

## 一、现状基线（2026-08-21 代码核实）

V9 计划文档头部仍写「规划稿，未开始实施」，**已过时**。git 记录显示 Step 0~4
及前端解锁已落地（`48c8eb6` Step0、`03234f8` Step1+2、`8e38eb4` Step3+4、
`4e99852` 前端解锁）。当前真实状态：

| 事实 | 位置 | 说明 |
|------|------|------|
| 三平台写价三件套代码均已存在 | `backend/app/domain/browser/{ctrip,meituan,fliggy}_pricing_page_reader.py` / `_pricing_page_writer.py` / `_pricing_executor.py` | 美团/飞猪为携程模式的同构复制，**未经真实账号联机验证** |
| 执行链平台分发已实现 | `backend/app/domain/pricing_execution/service.py:50-74`（`_pricing_platform_for_browser_session` / `_pricing_browser_service_for`） | 按 browser session 的 platform 字段分发 |
| **写价白名单仍只有携程** | `backend/app/domain/browser/sessions.py:601` `WRITE_CAPABLE_PLATFORMS: tuple = ("ctrip",)` | `can_auto_write_price()`（:604）要求 platform 在白名单内且 `connection_mode=="non_direct"`；美团/飞猪账号即使 non_direct 也是 `write_not_allowed` |
| 携程有空跑+执行双流程 | `backend/app/domain/browser/ctrip_pricing_write_flow.py:23`（dry-run）/ `:302`（execute） | 唯一已真实跑通的平台 |
| 美团/飞猪挂载在各自 service | `meituan_ebooking.py:363/:448`、`fliggy_ebooking.py:276/:361` | 结构与携程对齐 |
| 前端已解锁三平台 connection_mode 编辑 | `frontend/src/app/accountPlatformModel.ts:18`（`isReadonlyEbkPlatform` 仍标记只读）+ AccountsPanel 二次确认 | 可写显示以 `can_auto_write_price` 为准 |

结论：**调价链路的代码骨架三平台已对齐，剩余工作不在"写新代码"，而在
（1）补齐平台默认价格页 URL 与 selector 词表、（2）修执行链残留硬编码、
（3）真实账号联机验证、（4）逐平台放开写价白名单。**

---

## 二、统一调价链路（三平台共用，业务层）

```
可信持久化调价证据（竞对轮询跌价告警 / 飞书 @机器人 人工目标价指令）
→ 规则/AI 解释与参考价（仅建议，AI 失败有规则 fallback）
→ 运营在参考卡点「自定义调价」输入目标价（目标价是唯一候选执行价）
→ 规则化 preview（基于可信持久价 room_rate_dom_display_price + 运营目标价）
→ 飞书目标价确认卡 →「确认按此价格调价」
→ preflight（重查账号策略、锁价）
→ 页面写 dry-run（打开真实价格页、定位控件、不填不提交）
→ execute（填价 + 保存 + 网络响应验证 + 逐目标回读）
→ 飞书结果卡
```

关键代码节点（已实现，三平台共用）：

| 阶段 | 代码位置 |
|------|---------|
| 竞对轮询 → 阈值 → 参考卡 | `domain/competitor_prices/scheduler.py:696-725` → `domain/price_change_alerts/service.py:46` → `domain/competitor_prices/price_change_pricing_orchestration.py:41` |
| 参考卡（仅「自定义调价/暂不处理」） | `domain/pricing_approvals/cards.py:88 build_pricing_reference_card` |
| 自定义调价 → 人工 preview | `domain/notifications/feishu_bot.py:570 handle_pricing_custom_price_submit` → `domain/pricing_suggestions/preview.py:270 create_manual_pricing_preview` |
| 审批记录 + 排队执行 | `domain/pricing_approvals/service.py:35` / `domain/notifications/feishu_bot.py:456` → `domain/notifications/pricing_execution_pipeline.py:15` |
| P6 preflight / P7 dry-run / P8 execute | `domain/pricing_execution/service.py:77 / :180 / :296` |
| 账号策略闸门 | `domain/pricing_safety.py:16 account_policy_allows_write` + `browser/sessions.py:604 can_auto_write_price` |

每阶段安全闸门不变式（三平台一致，不得因平台差异放松）：

1. `direct` / `unknown` 账号全阶段 `write_not_allowed`，统一文案
   「当前携程 EBK 账号为直连或未确认账号…」（美团/飞猪需平台化文案，见 §六-4）。
2. 竞对**涨价仅告警**，任何平台都不得生成涨价建议/预览/审批卡。
3. 执行价只能来自运营输入并被审批锁定，审批后不得重新生成自由目标价。
4. 登录/验证码/扫码/风控/页面结构不确定 → `human_required` / `needs_review`，链路中止。

---

## 三、携程页面操作剧本（已实现，真实链路基准）

页面：**携程 EBK 价格日历**
`https://ebooking.ctrip.com/ebkovsroom/inventory/calendar?microJump=true`
（常量 `CTRIP_EBOOKING_PRICING_CALENDAR_URL`，
`ctrip_ebooking_session_helpers.py:22`）

### 3.1 页面获取（三平台共用的三层策略）

`ctrip_ebooking.py:352 _get_or_create_page`：

1. 进程内缓存页复用（`self._pages` + per-key `asyncio.Lock`）；
2. CDP 连接已运行的 Chrome（`_connect_existing_debug_page`，:515）；
3. 跨进程 profile 启动锁 → `launch_persistent_context`；
   `ProcessSingleton` / Lock 错误 → 人类可读 409（`_is_profile_singleton_error`，:613）。

### 3.2 dry-run 流程（`ctrip_pricing_write_flow.py:23 prepare_pricing_page_write_dry_run_flow`）

| 步骤 | 操作 | 代码 |
|------|------|------|
| 1 | `page.goto(价格日历URL, domcontentloaded, 45s)`；空参 target_url 回退默认 URL | `_resolve_ctrip_pricing_target_url`（session_helpers.py:834，**空参→默认 URL**） |
| 2 | 等待页面稳定 + 关闭轻量弹窗 | `_settle_page` / `dismiss_lightweight_popups`（ctrip_room_type_selector.py） |
| 3 | 读 body 文本，页面态分类：URL 含 captcha/verify/security/risk → `verification_required`；登录特征 → `login_required`；ebooking.ctrip.com 域 + 价格页词表命中 → `pricing_page`；其余 → `page_structure_changed` | `classify_ctrip_pricing_page_state` |
| 4 | 登录/验证 → 返回 `human_required`，同步 session 状态 | `_sync_ebk_browser_session_state` |
| 5 | 只读定位：房型快捷选择触发器 → 点击 → 弹窗内搜索/匹配房型 → 确认 → 点击目标日期价格块 → 收集价格输入框候选（不填不提交） | `_select_pricing_room_type_read_only`（session_helpers.py:850） |
| 6 | 生成 page write preview（`build_ctrip_pricing_page_write_preview`），evidence 标注 `read_only: true / input_fill_invoked: false / save_click_invoked: false` | `ctrip_pricing_page_writer.py` |

### 3.3 execute 流程（`ctrip_pricing_write_flow.py:302 execute_pricing_page_write_flow`）

对每个 action（room_type + checkin_date + proposed_base_price_yuan）：

| 步骤 | 操作 | 失败状态 |
|------|------|---------|
| 1 | 关闭轻量弹窗 | — |
| 2 | `detect_room_type_trigger` 找房型快捷选择触发器，取分最高者点击 | `page_structure_changed` |
| 3 | 等 1.5s → `detect_room_type_popup` 找弹窗内搜索框 | `page_structure_changed` |
| 4 | `select_matching_room_results`：先默认列表直接匹配房型名；不行则 `safe_fill_search` 输入房型名再匹配 | `page_structure_changed` |
| 5 | `click_room_selection_confirm` 确认房型选择 | `page_structure_changed` |
| 6 | `click_first_price_block(room_type, checkin_date)`：定位该房型×入住日期的价格块并点击展开输入 | `page_structure_changed` |
| 7 | `fill_ctrip_room_price`（ctrip_pricing_executor.py:239）：输入框**三击全选** → `Ctrl+A` → `Backspace` → 键盘逐字输入（80ms/字）→ 等 500ms → `input_value()` 回读，与目标价差值 < EPSILON 才算 `filled` | `mismatch` / `input_failed` / `not_located` |
| 8 | 全部 action 填完且无失败 → `submit_ctrip_price_changes`（:328） | — |

`submit_ctrip_price_changes` 提交确认序列：

1. 提交前截图（artifact 目录，文件名含 platform_account_id + 时间戳；**路径不外露**）；
2. body 文本预检：验证码/滑动/扫码/风险词 → `human_required`；
3. 定位保存按钮（可见 button 文本匹配）并点击；
4. 同时监听页面网络响应 `_is_price_submit_response`（:128）：必须
   ebooking.ctrip.com 域 + POST/PUT/PATCH + path 含价格域词（price/rate/inventory/calendar）
   + 写动作词（save/submit/update/modify/change/batch）+ 请求 payload 含本次目标价与房型/日期；
5. `_wait_for_submit_confirmation`（:187）：循环 40×250ms，优先网络响应（2xx 为
   confirmed，非 2xx → `needs_review`），其次成功 toast（含「成功/保存」）；
   都没有 → `uncertain → needs_review`；
6. 提交后截图；
7. **逐目标回读**：通过价格日历真实读回（`read_pricing_calendar_current_rates_flow`，
   write_flow.py:121 同源逻辑），每个房型×日期的底价都匹配才返回 `executed`；
   任一不匹配 → `needs_review`。

### 3.4 底价/卖价换算

携程写的是**底价**（底价佣金模式），preview 卡片同时展示卖价/底价两行
（`pricing_approvals/cards.py:216-221/406-407/522`），换算在
`domain/browser/ctrip_price_converter.py`（`sell_to_base` / `base_to_sell`）。
执行前还会读价格日历真实底价做 readback 基准。

---

## 四、美团页面操作剧本（模拟稿，待联机验证）

### 4.1 相关 URL

| 用途 | URL | 常量 |
|------|-----|------|
| 登录 | `https://eb.meituan.com/ebk/login/login.html` | `MEITUAN_EBK_LOGIN_URL`（meituan_ebooking.py:23） |
| 工作台（iframe 壳） | `https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fnew-workbench%2Findex.html%23%2F` | `MEITUAN_EBK_WORKBENCH_URL` |
| **价格页** | **无默认常量。`_resolve_meituan_pricing_target_url`（:2086）只接受显式传入的 meituan.com 域 http(s) 链接，空参直接返回 None → 链路返回「只接受 meituan.com 域名下的 EBK 价格页链接」** | **缺口①** |

预期真实价格页（联机验证时确认后固化为常量）：美团酒店 EBK 的房价/房态日历页，
域名形如 `eb.meituan.com` 或 `me.meituan.com` 下的房价管理路由。**上线前必须：
新增 `MEITUAN_EBK_PRICING_URL` 常量 + 在 `_resolve_meituan_pricing_target_url`
中加空参回退（对齐携程 :834 模式）**，否则执行链每次都因 URL 无效而中止。

### 4.2 页面态分类（`meituan_pricing_page_reader.py:32 classify_meituan_pricing_page_state`）

- URL 含 captcha/verify/security/risk，或 body 含 验证码/滑动验证/拼图/扫码/二维码/安全验证
  → `verification_required`（→ `human_required`）；
- 登录特征 → `login_required`（→ `human_required`）；
- meituan.com 域 + body 词表（房价/房型/日历/库存）命中 ≥2 → `pricing_page`；
- 有正文但不是价格页 → `page_structure_changed`。

### 4.3 dry-run（`meituan_ebooking.py:363 prepare_pricing_page_write_dry_run`）

1. 解析 target_url（见缺口①）→ goto → `_settle_page`（800ms）；
2. `read_meituan_pricing_write_page`：页面态分类；`pricing_page` 才继续；
3. **价格输入框定位（与携程最大差异）**：`_safe_price_input_candidates`
   （reader :153）在**主文档** `page.evaluate` 扫描全部可见
   `input / [contenteditable=true] / textarea`（≤80 个），对每个输入框：
   - 生成 cssPath `selector_hint`（id 或 ≤4 层 tag+class 路径）；
   - 抓容器（最近 row/cell/card/tr/td/li）附近文本 `nearby_text`（≤500 字）、
     placeholder、aria-label、当前 value；
4. 目标匹配计分（`_matching_input_candidates`，:214）：附近文本含
   房型名 / 入住日的「日」数字 / 当前价各 +1，含价格词（价格/房价/卖价/底价/¥/￥）再 +1，
   取分高者为 `located`；完全无命中 → `not_found`（dry-run 可通过，execute 会失败）；
5. 生成 write preview，evidence 标注不填不提交。

### 4.4 execute（`meituan_ebooking.py:448 execute_pricing_page_write`）

对每个 action：校验 room_type/checkin_date/proposed_base_price_yuan 齐备 →
`fill_meituan_room_price`（executor :165）：定位 selector_hint 输入框 → 可见等待
10s → 三击全选 → `Ctrl+A` → `Backspace` → 键盘输入（80ms/字）→ 500ms 后
`input_value()` 回读比对（EPSILON）→ `filled` / `mismatch` / `not_located` / `input_failed`。

全部 filled 后 `submit_meituan_price_changes`（executor :353）：

1. 提交前 body 预检（验证码/扫码/风控 → `human_required`）+ 截图；
2. `_locate_submit_button`（:265）：扫描可见 button/[role=button]/a[class*=btn]，
   文本精确等于 保存/确认/提交/发布 者为提交按钮，生成 cssPath；
3. 点击（5s 超时），同时监听 response：`_is_price_submit_response`（:107）要求
   **meituan.com 域** + POST/PUT/PATCH + path 含价格域词和写动作词 + payload
   含目标价（数字边界匹配）与房型名或日期（YYYY-MM-DD / 无分隔 / 斜杠三种变体）；
4. 等待确认（40×250ms）：网络响应 2xx > 成功 toast（含成功/保存）> `needs_review`；
5. 提交后截图；
6. 逐目标回读：通过各自 selector_hint 读输入框现值，全部匹配才 `executed`。

### 4.5 美团特有风险与必查项（联机验证清单）

| # | 风险 | 说明与方向 |
|---|------|-----------|
| R1 | **iframe 嵌套**：美团 EBK 是 ebIframe 壳结构，数据中心读取已按 `page.frames` 遍历（`meituan_ebooking.py:2171 _read_meituan_dataset_text_once`），但 pricing reader 的输入框扫描只查主文档 `page.evaluate` —— **iframe 内的价格输入框将扫不到** | 方向：reader/executor 的候选扫描与提交按钮定位改为遍历 `page.frames`（对齐数据中心读取的帧打分模式）。这是联机验证头号必查项 |
| R2 | **角色未选择**（V9 决策 D3）：美团新版工作台首用需选角色，未选时页面结构不可判 | 方向：preflight 增加美团角色检查，未选角色返回 `needs_review` 附操作指引。目前未见实现 |
| R3 | 页面词表空：`page_health_config.json` 的 `meituan:pricing:calendar` 为空对象 `{}`（:93），selector 失效时无法触发 V7S1 页面健康告警 | 方向：联机验证后回填 expected_terms |
| R4 | 保存按钮文本匹配过宽：「确认」等词在复杂页面可能命中非提交按钮 | 方向：联机验证后把按钮定位收紧（容器/按钮顺序/平台真实文案），词表进 page_health_config |
| R5 | 佣金结构：携程有 sell/base 换算器；美团底价/卖价模式不同 | 方向：preview 卡片对美团标注「美团底价」，如需卖价展示则新增 `meituan_price_converter`（V9 范围外，先不做） |

---

## 五、飞猪页面操作剧本（模拟稿，待联机验证）

### 5.1 相关 URL

| 用途 | URL | 常量 |
|------|-----|------|
| 基底 | `https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm` | `FLIGGY_EBK_BASE_URL`（fliggy_ebooking.py:26） |
| 工作台 | `{BASE}#/ebk/homeV1` | `FLIGGY_EBK_HOME_URL` |
| **价格页** | **无默认常量。`_resolve_fliggy_pricing_target_url`（fliggy_ebooking.py:917）同样只接受显式 fliggy.com 域链接，空参返回 None** | **缺口①（同美团）** |

预期真实价格页：飞猪商家中心 hash 路由下的房价管理页（形如
`{BASE}#/ebk-room/...`，联机验证确认后固化为 `FLIGGY_EBK_PRICING_URL` 常量并加空参回退）。

### 5.2 流程结构

`fliggy_ebooking.py:276`（dry-run）/ `:361`（execute），与美团逐环节同构：

- 页面态分类 `classify_fliggy_pricing_page_state`（fliggy_pricing_page_reader.py）：
  fliggy.com 域 + 同一套价格页词表；
- 输入框扫描/计分、填价（三击+键盘+回读）、提交按钮文本匹配（保存/确认/提交/发布）、
  网络响应验证（**fliggy.com 域** + 同一组 path 词 + payload 匹配）、toast、逐目标回读
  —— 逻辑与 §4.3/§4.4 完全一致，仅域名校验不同。

### 5.3 飞猪特有风险与必查项

| # | 风险 | 说明与方向 |
|---|------|-----------|
| R1 | **hash 路由 SPA**：飞猪是 `#/ebk-...` 前端路由，`goto` 同一基底 URL 后需等 SPA 渲染；`_settle_page` 固定 800ms 可能不足 | 方向：按数据中心读取已有的「文本稳定判据」（`_normalize_visible_text_for_stability` + 连续 2 次一致）改造 pricing 流程的等待；或 goto 完整 hash URL |
| R2 | **executor 复制残留**：`fliggy_pricing_executor.py` 是美团 executor 逐行复制，docstring/文案仍写「美团」—— :1-7 模块 docstring「美团 EBK 价格页执行器」、:111「只接受美团域名下」、:171「填入美团 EBK 价格输入框」、:360「点击美团 EBK 保存按钮」。逻辑虽只认 fliggy.com，但文档性错误必须修正 | 方向：纯文案修正，零行为变化，可先行 |
| R3 | iframe 情况未知（飞猪数据中心解析是否走 frames 待核） | 联机验证时确认价格页是否含 iframe，同美团 R1 方向 |
| R4 | `page_health_config.json` 的 `fliggy:pricing:calendar` 为空 `{}`（:94） | 联机验证后回填 |
| R5 | 飞猪价格体系（日常价/促销价/会员价多级）可能使「当前价」与回读基准不一致 | 联机验证时确认回读取哪个输入框；必要时在 preview 卡片标注价格类型 |

---

## 六、代码修改点清单（按优先级，均为方向，未实施）

### P0 —— 美团/飞猪上线闸门前的必改项

1. **平台默认价格页 URL 与空参回退**
   - 文件：`backend/app/domain/browser/meituan_ebooking.py:2086`、
     `fliggy_ebooking.py:917`；
   - 方向：新增 `MEITUAN_EBK_PRICING_URL` / `FLIGGY_EBK_PRICING_URL` 常量
     （联机确认真实价格页后固化），`_resolve_*_pricing_target_url` 空参回退默认值，
     显式传入仍做域名校验——完全对齐携程 `ctrip_ebooking_session_helpers.py:834` 模式。

2. **执行链 platform 硬编码**
   - 文件：`backend/app/domain/pricing_execution/service.py:536`
     `_execute_request_from_actions` 中 action 的 `"platform": "ctrip"` 写死；
   - 方向：改用 `_pricing_platform_for_browser_session(pricing_account.get("browser_session_id"))`
     或从 preview 记录携带平台，action.platform 与实际执行平台一致。

3. **执行链失败文案硬编码「携程」**
   - 文件：`pricing_execution/service.py:239`「没有可用的携程 EBK 浏览器账号，无法进行页面空跑」、
     `:246`「请先绑定并登录携程 EBK 浏览器账号」、`:355`「无法执行写价」同款；
   - 方向：统一走已有的 `_pricing_platform_label(platform)`（:46，三平台标签表已建好）。

4. **飞猪 executor 文案修正**
   - 文件：`backend/app/domain/browser/fliggy_pricing_executor.py:1-7/:111/:171/:360`；
   - 方向：把「美团」改为「飞猪」，纯文案，零行为变化。

5. **美团/飞猪 pricing reader 的 iframe 支持**
   - 文件：`meituan_pricing_page_reader.py:153 _safe_price_input_candidates`、
     对应 executor `_locate_submit_button`、飞猪同构位置；
   - 方向：输入框/按钮扫描从主文档 `page.evaluate` 扩展为遍历 `page.frames`
     （参照 `meituan_ebooking.py:2171` 数据中心帧遍历 + URL marker 加分模式）。
     若联机验证证明价格页无 iframe，则记录结论并降级为可选加固。

### P1 —— 联机验证配套

6. **`page_health_config.json` 词表回填**：`meituan:pricing:calendar`（:93）、
   `fliggy:pricing:calendar`（:94）目前空对象；联机验证后回填 expected_terms，
   使 selector 失效能触发 V7S1 页面健康告警（红/橙/蓝分级）。
7. **美团角色选择 preflight 检查**（V9 决策 D3，未实现）：
   `pricing_execution/service.py` preflight 阶段对 meituan 会话检查工作台角色已选择，
   未选 → `needs_review` + 指引文案。
8. **房型名跨平台归一**：携程有 `normalized_room_type` + 弹窗搜索兜底；
   美团/飞猪完全依赖 nearby_text 与原始房型名字符串匹配，EBK 后台显示名与
   配置名不一致时 dry-run 即 `not_found`。方向：preview 生成时带上平台侧房型
   候选（读价格页房型列表），匹配失败时卡片提示运营核对房型命名。

### P2 —— 白名单放开与增强（每平台联机验证通过后）

9. **`WRITE_CAPABLE_PLATFORMS` 逐平台放开**：`sessions.py:601`，按 V9 决策 D2
   白名单式，先验证先放开（预计顺序：美团 → 飞猪）；配套测试：三平台
   direct/unknown 全阶段 `write_not_allowed`、non_direct 未入白名单不可写。
10. **结果卡/阻断卡平台标注复核**：`pricing_approvals/cards.py` 与
    `notifications/pricing_execution_card.py` 中平台文案已部分接入
    `_PRICING_PLATFORM_LABELS`，放开前逐卡复核。
11. **SPA 等待策略统一**（飞猪 R1）：`_settle_page` 从固定 800ms 升级为
    文本稳定判据，三平台共用。

### 明确不做（Non-Goals，沿 V9）

- 不做通用 OTA 写价框架抽象，按平台独立实现；
- 不扩美团/飞猪竞品比价数据源（另议）；
- 不做 AI 定价/AI 触发执行/前端执行按钮/通用写价 API（安全契约红线）。

---

## 七、联机验证人工清单（TDD 例外项，每平台独立执行）

> 沿用 V9 §四 Step1/2 的验证要求；全程截图留档（内部 artifact，不外露路径）。

1. 真实账号登录价格页，人工确认页面 URL 并固化常量（P0-1）；
2. 人工触发 dry-run：确认能定位房型×日期×价格输入框（记录 iframe 结构结论）；
3. dry-run 通过后小额真实写价一次：填价 → 保存 → 网络响应/回读全通过 → 结果卡送达；
4. 把页面词表回填 `page_health_config.json`（P1-6）；
5. 上述全部通过后才把平台加入 `WRITE_CAPABLE_PLATFORMS`（P2-9）；
6. 回归：direct/unknown 账号走同一链路必须 `write_not_allowed`；验证码页必须
   `human_required`。

---

## 八、安全契约红线自查表（三平台上线前逐项勾选）

- [ ] 任何平台、任何阶段都没有 AI 生成执行价；执行价 = 运营输入并经审批锁定；
- [ ] 参考卡只有「自定义调价」「暂不处理」，无一键通过/拒绝；
- [ ] 竞对涨价仅告警（无建议/无预览/无审批卡），三平台一致；
- [ ] direct/unknown 三平台全阶段统一 `write_not_allowed` 文案（平台名正确）；
- [ ] 验证码/扫码/风控/结构不确定 → `human_required` / `needs_review`，绝不绕过；
- [ ] 保存提交必须满足：域名白名单 + 写接口特征 + payload 含目标价 + 逐目标回读，
      四者缺一即 `needs_review`；
- [ ] 截图/cookie/profile 路径不出现在任何 API 响应与日志；
- [ ] 前端无执行按钮，写价入口只有飞书卡片链路。
