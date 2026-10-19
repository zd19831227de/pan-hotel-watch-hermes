import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const accountsSource = readFileSync(new URL("./tabs/AccountsPanel.tsx", import.meta.url), "utf8");
const briefSource = readFileSync(new URL("./ebk/CtripDailyBriefPanel.tsx", import.meta.url), "utf8");
const dataCenterSource = readFileSync(new URL("./ebk/CtripDataCenterPanel.tsx", import.meta.url), "utf8");
const operatorUiSource = [appSource, accountsSource, briefSource, dataCenterSource].join("\n");

test("操作台主文案不直接展示 profile 和 webhook 技术词", () => {
  assert.ok(!operatorUiSource.includes("浏览器 profile"));
  assert.ok(!operatorUiSource.includes("手机 webhook"));
  assert.ok(!operatorUiSource.includes("电脑 webhook"));
  assert.ok(!operatorUiSource.includes("粘贴手机卡片 webhook"));
  assert.ok(!operatorUiSource.includes("粘贴电脑表格 webhook"));
  assert.ok(!operatorUiSource.includes("电脑飞书机器人地址"));
  assert.ok(!operatorUiSource.includes("表格全量明细"));
  assert.ok(!operatorUiSource.includes("电脑端机器人推送表格明细"));
});

test("EBK 经营数据面板与简报推送配置保留在操作台携程页", () => {
  const dashboardSource = readFileSync(new URL("./ebk/CtripEbkDashboard.tsx", import.meta.url), "utf8");
  assert.ok(appSource.includes("EBK 操作台"));
  assert.ok(!appSource.includes('label="EBK 数据简报"'));
  assert.ok(!briefSource.includes("AI 每日简报"));
  // 经营数据取代失效的 PSI 服务概览 + 点评两块（comment_monitor 已不再读取）。
  assert.ok(briefSource.includes("经营数据"));
  assert.ok(briefSource.includes("收益概览"));
  assert.ok(briefSource.includes("经营对比"));
  assert.ok(briefSource.includes("流量曝光"));
  assert.ok(dashboardSource.includes("CtripBusinessDataPanel"));
  assert.ok(dashboardSource.includes("DailyBriefPanel"));
  // 简报推送配置（绑定组/机器人/时间）保持不变。
  assert.ok(briefSource.includes("绑定监控组"));
  assert.ok(briefSource.includes("飞书机器人地址"));
  assert.ok(briefSource.includes("推送时间"));
  assert.ok(briefSource.includes("启用每日定时推送"));
  assert.ok(briefSource.includes("保存简报设置"));
});

test("EBK 操作台为唯一 EBK 顶栏入口且按平台分组", () => {
  const opsSource = readFileSync(new URL("./ebk/EbkOpsConsole.tsx", import.meta.url), "utf8");
  assert.ok(appSource.includes("EBK 操作台"));
  assert.ok(appSource.includes('"ebk_ops"'));
  assert.ok(!appSource.includes('"daily_brief"'));
  assert.ok(!appSource.includes('label="EBK 数据中心"'));
  assert.match(
    appSource,
    /type TabKey = "accounts" \| "ebk_ops" \| "competitor" \| "calendar_prices" \| "price_alerts"/,
  );
  assert.ok(opsSource.includes("携程 EBK"));
  assert.ok(opsSource.includes("美团 EBK"));
  assert.ok(opsSource.includes("飞猪 EBK"));
  assert.ok(!opsSource.includes("联合分析"));
  assert.ok(!opsSource.includes("EbkJointAnalysisPanel"));
  assert.ok(!appSource.includes("buildEbkJointAnalysisContext"));
  assert.ok(!opsSource.includes("立即调价"));
  assert.ok(!opsSource.includes("写入携程"));
  assert.ok(!opsSource.includes("自动执行"));
});

test("EBK 操作台为店铺优先两级分支树（店铺/全局/未分组）", () => {
  const opsSource = readFileSync(new URL("./ebk/EbkOpsConsole.tsx", import.meta.url), "utf8");
  // 一级=店铺+全局+未分组；二级=平台（店铺树 §2.1）
  assert.ok(opsSource.includes('aria-label="店铺切换"'));
  assert.ok(opsSource.includes('aria-label="平台切换"'));
  assert.ok(opsSource.includes("全局定时推送"));
  assert.ok(opsSource.includes("未分组监控组("));
  // 店铺分支只见本店：组与账号都按 shop_key 过滤
  assert.match(opsSource, /group\.shop_key === branch\.shopKey/);
  assert.match(opsSource, /session\.shop_key === branch\.shopKey/);
  // 跨店选中账号传空串，读取按钮自动禁用（防跨店误读）
  assert.ok(opsSource.includes("scopedMeituanAccountId"));
  assert.ok(opsSource.includes("scopedFliggyAccountId"));
  // 店内无携程配置：空态用「接入」口径，不出现「创建监控组」话术（店铺直连）
  assert.ok(opsSource.includes("该店铺尚未接入携程平台"));
  assert.ok(opsSource.includes("接入携程"));
  assert.ok(!opsSource.includes("新建本店携程监控组"));
  assert.ok(opsSource.includes("onCreateCtripGroupForShop"));
  // 未分组补绑：三平台列表 + 行内归属店铺
  assert.ok(opsSource.includes("归入店铺"));
  assert.ok(opsSource.includes("patchDataCenterGroup"));
  assert.ok(opsSource.includes("patchWatchGroup"));
  assert.ok(opsSource.includes("onRefreshDataCenterGroups"));
  // 安全契约：不出现写价入口
  assert.doesNotMatch(opsSource, /立即调价|写入携程|自动执行|确认按此价格调价/);
});

test("EBK 操作台携程分支换装窗口渲染占位，不透传他店数据", () => {
  const opsSource = readFileSync(new URL("./ebk/EbkOpsConsole.tsx", import.meta.url), "utf8");
  // 店内解析组 ≠ App 选中组（latest/datasets/businessData 仍属他店）时
  // 渲染占位，等 App 换装完成才挂 dashboard，堵住跨店旧数据窗口。
  assert.match(opsSource, /ctripSyncKey \? \(/);
  assert.ok(opsSource.includes("正在载入该店铺的携程数据"));
});

test("模型配置面板说明飞书对话已由 Hermes 接管", () => {
  assert.ok(appSource.includes("飞书对话由 Hermes 接管"));
  assert.ok(appSource.includes("FEISHU_AGENT_MODE=builtin"));
});

test("LLM 配置保留在高级维护入口并提供模型管理能力", () => {
  assert.ok(appSource.includes("advanced-maintenance"));
  assert.ok(appSource.includes("advancedMaintenanceOpen"));
  assert.ok(!appSource.includes('label="LLM 配置"'));
  assert.ok(appSource.includes("模型列表") || appSource.includes("拉取模型"));
  assert.ok(appSource.includes("listLlmModels"));
});

test("EBK 数据中心支持监控组增删和当前组选择", () => {
  assert.ok(appSource.includes("新的 EBK 数据中心监控组"));
  assert.ok(appSource.includes("已删除 EBK 数据中心监控组"));
  assert.ok(dataCenterSource.includes("当前组"));
  assert.ok(dataCenterSource.includes("删除当前监控组"));
});

test("calendar price watch is a standalone read-only tab", () => {
  const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
  assert.match(
    appSource,
    /type TabKey = "accounts" \| "ebk_ops" \| "competitor" \| "calendar_prices" \| "price_alerts"/,
  );
  assert.match(appSource, /日历价监测/);
  const calendarPanelStart = appSource.indexOf("function CalendarPricePanel");
  assert.ok(calendarPanelStart > 0);
  const calendarPanelSource = appSource.slice(calendarPanelStart, calendarPanelStart + 12000);
  assert.doesNotMatch(calendarPanelSource, /立即调价|写入携程|自动执行|确认调价/);
});

test("calendar price watch exposes Feishu robot configuration", () => {
  const calendarPanelStart = appSource.indexOf("function CalendarPricePanel");
  assert.ok(calendarPanelStart > 0);
  const calendarPanelSource = appSource.slice(calendarPanelStart, calendarPanelStart + 25000);
  assert.ok(calendarPanelSource.includes("日历价预警机器人地址"));
  assert.ok(calendarPanelSource.includes("保存机器人"));
  assert.ok(calendarPanelSource.includes("清除机器人"));
});

test("首页状态条展示日历价与价格变化提醒", () => {
  assert.ok(appSource.includes('label="日历价提醒"'));
  assert.ok(appSource.includes('label="价格变化提醒"'));
  assert.ok(appSource.includes("calendarStrip"));
  assert.ok(appSource.includes("priceChangeStrip"));
});

test("飞书群角色文案区分手动调价、价格变化投递和运营问答上下文", () => {
  assert.ok(accountsSource.includes("手动调价指令群"));
  assert.ok(appSource.includes("价格变化提醒群"));
  assert.ok(appSource.includes("调价参考卡投递"));
  assert.ok(appSource.includes("运营问答上下文群"));
  assert.ok(accountsSource.includes("手动调价指令群只用于"));
  assert.ok(appSource.includes("普通经营问答、今日简报、跑一次 EBK、竞品价和日历价查询"));
  assert.ok(appSource.includes("运营问答上下文群（兼价格变化提醒群）"));
  assert.ok(accountsSource.includes("不要把手动调价指令群当作经营问答绑定"));
  assert.ok(!operatorUiSource.includes("调价审批群 chat_id"));
});

test("competitor watch copy separates public read account pool from EBK pricing account", () => {
  const oldEmptyPricingBindingCopy = "不绑定，只生成" + "调价建议";
  const oldMissingPricingBindingHint = "未绑定时" + "只生成建议";
  assert.ok(appSource.includes("竞对读取账号"));
  assert.ok(appSource.includes("账号池轮换"));
  assert.ok(appSource.includes("调价 EBK 账号（仅写价链路）"));
  assert.ok(appSource.includes("不绑定：只读查询正常，禁止写价"));
  assert.ok(appSource.includes("只用于携程官网酒店价格读取"));
  assert.ok(appSource.includes("飞书群 ID 与 EBK/竞品监控组决定只读上下文"));
  assert.ok(appSource.includes("这里仅决定降价参考卡和后续 Feishu 审批写价能否继续"));
  assert.ok(!appSource.includes(oldEmptyPricingBindingCopy));
  assert.ok(!appSource.includes(oldMissingPricingBindingHint));
});

test("account panel copy shows competitor browser pool usage separately from EBK accounts", () => {
  assert.ok(accountsSource.includes("用于竞对账号池"));
});

test("account panel groups browser accounts shop-first (v2) with per-platform rows", () => {
  // v2 重构：店铺=唯一锚点；旧「平台×用途」四段标题退役。
  assert.ok(!accountsSource.includes("携程 EBK 后台账号"));
  assert.ok(!accountsSource.includes("携程竞品价格账号"));
  assert.ok(!accountsSource.includes("美团 EBK 后台账号"));
  assert.ok(!accountsSource.includes("飞猪 EBK 后台账号"));
  assert.ok(accountsSource.includes("竞品酒店"));
  assert.ok(accountsSource.includes("未分组"));
  assert.ok(accountsSource.includes("打开美团 EBK 登录"));
  assert.ok(accountsSource.includes("打开飞猪 EBK 登录"));
  assert.ok(accountsSource.includes("确认登录态"));
});

test("EBK 数据中心监控组暴露运营问答上下文群入口", () => {
  assert.ok(dataCenterSource.includes("EBK 数据中心监控组的运营问答上下文群"));
  assert.ok(dataCenterSource.includes("保存上下文群"));
  assert.ok(dataCenterSource.includes("清除上下文群"));
  assert.ok(dataCenterSource.includes("同一个飞书群 ID 也要填到对应竞品监控组"));
});
