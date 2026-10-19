import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const historyPanelPath = new URL("./HistoryPanel.tsx", import.meta.url);
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const competitorApiSource = readFileSync(
  new URL("../services/competitorWatchApi.ts", import.meta.url),
  "utf8",
);
const ctripWatchApiSource = readFileSync(
  new URL("../services/ctripEbookingWatchApi.ts", import.meta.url),
  "utf8",
);
const opsApiSource = readFileSync(
  new URL("../services/opsApi.ts", import.meta.url),
  "utf8",
);
const exportsApiSource = readFileSync(
  new URL("../services/exportsApi.ts", import.meta.url),
  "utf8",
);

test("history panel renders business blocks only (no distill)", () => {
  assert.ok(existsSync(historyPanelPath), "HistoryPanel.tsx must exist");
  const panelSource = readFileSync(historyPanelPath, "utf8");
  for (const marker of ["EBK 监控运行历史", "操作记录", "价格与日历价告警"]) {
    assert.ok(panelSource.includes(marker), marker);
  }
  // 修正3：自学习历史不属于业务历史面板
  assert.ok(!panelSource.includes("蒸馏运行历史"), "distill history must live in the distill panel");
  assert.ok(!panelSource.includes("fetchDistillRuns"), "history panel must not fetch distill runs");
  assert.match(panelSource, /listDataCenterWatchRuns/);
  assert.match(panelSource, /fetchOperatorRecentActivity/);
  assert.match(panelSource, /listPriceChangeAlerts/);
  assert.match(panelSource, /listCalendarPriceAlerts/);
  assert.match(panelSource, /还没有.*记录|暂无/);
  assert.ok(!panelSource.includes("调价"), "history panel must not expose pricing actions");
});

test("app registers history tab", () => {
  assert.match(appSource, /"history"/);
  assert.match(appSource, /import \{ HistoryPanel \}/);
  assert.match(appSource, /activeTab === "history"/);
  assert.match(appSource, /历史记录/);
});

test("ebk runs and operator activity services exist", () => {
  assert.match(ctripWatchApiSource, /export async function listDataCenterWatchRuns/);
  assert.match(ctripWatchApiSource, /\/runs\?limit=/);
  assert.match(opsApiSource, /export async function fetchOperatorRecentActivity/);
  assert.match(opsApiSource, /\/api\/v1\/ops\/operator-recent-activity/);
});

test("price trend api accepts days and granularity options", () => {
  assert.match(competitorApiSource, /days\?: number/);
  assert.match(competitorApiSource, /granularity\?: "run" \| "hour" \| "day"/);
  assert.match(competitorApiSource, /days=\$\{days\}/);
  assert.match(competitorApiSource, /granularity=\$\{granularity\}/);
});

test("competitor tab exposes trend range and granularity switches", () => {
  assert.match(appSource, /trendDays/);
  assert.match(appSource, /trendGranularity/);
  assert.match(appSource, /近 \$\{days\} 天/);
  assert.match(appSource, /\[1, 7, 30\]/);
  assert.match(appSource, /按天|按小时|按次/);
});

test("history export covers business data and strategies with retention and excel", () => {
  const panelSource = readFileSync(historyPanelPath, "utf8");
  // 规范化：经营数据导出为三平台指标明细（CSV）/核心对比+明细双 Sheet（Excel）
  assert.match(panelSource, /exportEbkBusinessData/);
  assert.match(panelSource, /exportPricingStrategyHistory/);
  assert.match(panelSource, /导出经营数据（携程\/美团\/飞猪）/);
  assert.match(panelSource, /历史策略/);
  assert.match(panelSource, /retentionDays/);
  assert.match(panelSource, /180/);
  assert.match(panelSource, /xlsx/);
  assert.match(panelSource, /留存/);
});

test("exports api exposes business-data and strategy-history exports with format", () => {
  for (const fn of [
    "exportEbkBusinessData",
    "exportPricingStrategyHistory",
  ]) {
    assert.ok(exportsApiSource.includes(`export async function ${fn}`), fn);
  }
  assert.match(exportsApiSource, /\/api\/v1\/export\/ebk-business-data/);
  assert.match(exportsApiSource, /\/api\/v1\/export\/pricing-strategy-history/);
  assert.match(exportsApiSource, /ExportFormat = "csv" \| "xlsx"/);
  assert.match(exportsApiSource, /format=\$\{format\}/);
});

test("business export has mobile-friendly range presets and custom validation", () => {
  const panelSource = readFileSync(historyPanelPath, "utf8");
  for (const marker of ["当天", "本周", "本月", "自定义", "最近 N 天"]) {
    assert.ok(panelSource.includes(marker), marker);
  }
  assert.match(panelSource, /startDate/);
  assert.match(panelSource, /endDate/);
  assert.match(panelSource, /开始日期不能晚于结束日期/);
  assert.match(panelSource, /自定义范围必须填写开始日期和结束日期/);
  assert.match(panelSource, /limit.*5000|5000.*limit/);
  assert.match(panelSource, /数据较多/);
  assert.match(panelSource, /range/);
  assert.match(panelSource, /startDate/);
  assert.match(panelSource, /endDate/);
});

test("business export service sends range params while other exports keep days", () => {
  assert.match(exportsApiSource, /range\?: "today" \| "week" \| "month" \| "custom"/);
  assert.match(exportsApiSource, /startDate\?: string/);
  assert.match(exportsApiSource, /endDate\?: string/);
  assert.match(exportsApiSource, /params\.set\("range"/);
  assert.match(exportsApiSource, /params\.set\("start_date"/);
  assert.match(exportsApiSource, /params\.set\("end_date"/);
  assert.match(exportsApiSource, /exportPricingStrategyHistory[\s\S]*days/);
});

test("export format defaults to Excel with CSV as an option", () => {
  const panelSource = readFileSync(historyPanelPath, "utf8");
  assert.match(panelSource, /useState<ExportFormat>\("xlsx"\)/, "default export format must be xlsx");
  const xlsxIdx = panelSource.indexOf('value="xlsx"');
  const csvIdx = panelSource.indexOf('value="csv"');
  assert.ok(xlsxIdx !== -1 && csvIdx !== -1, "both xlsx and csv options must exist");
  assert.ok(xlsxIdx < csvIdx, "Excel option should be listed before CSV");
});

test("business export range is decoupled from retention days", () => {
  const panelSource = readFileSync(historyPanelPath, "utf8");
  // ??N? ??????????????? retentionDays
  assert.match(panelSource, /exportDays/);
  assert.ok(
    !panelSource.includes("exportEbkBusinessData({ days: retentionDays"),
    "days-range export must not reuse retentionDays",
  );
  // ??N? ??????????
  assert.ok(!panelSource.includes("\u6700\u8fd1 N \u5929\uff1a{retentionDays}"), "recent-N-days hint must not depend on retentionDays");
});

test("history panel exposes EBK cleanup action wired to retention", () => {
  const panelSource = readFileSync(historyPanelPath, "utf8");
  assert.match(panelSource, /cleanupEbkBusinessData/);
  assert.ok(panelSource.includes("\u7acb\u5373\u6e05\u7406"), "cleanup button label");
  assert.match(exportsApiSource, /\/api\/v1\/export\/ebk-business-data\/cleanup/);
});

test("history panels use an equal-height two-column grid", () => {
  const panelSource = readFileSync(historyPanelPath, "utf8");
  assert.match(panelSource, /history-panels-grid/);
});

test("export filters use a single N-days input; retention is cleanup-only", () => {
  const panelSource = readFileSync(historyPanelPath, "utf8");
  // \u5386\u53f2\u7b56\u7565\u5bfc\u51fa = "??????"
  assert.ok(!panelSource.includes("\u5386\u53f2\u8bb0\u5f55\u5bfc\u51fa\u7a97\u53e3"), "export-window field must be removed");
  assert.match(panelSource, /exportPricingStrategyHistory\(\{ days: exportDays/);
  assert.match(panelSource, /days: exportDays/);
});

test("manual cleanup uses its own day input (0 = clear all)", () => {
  const panelSource = readFileSync(historyPanelPath, "utf8");
  assert.match(panelSource, /cleanupDays/);
  assert.match(panelSource, /cleanupEbkBusinessData\(cleanupDays/);
  assert.ok(!panelSource.includes("cleanupEbkBusinessData(retentionDays)"), "cleanup must not use retentionDays");
  assert.match(exportsApiSource, /retentionDays === undefined \? \{\} : \{ retention_days: retentionDays \}/);
});

test("export settings keep the N-days field inside the aligned form grid", () => {
  const source = readFileSync(new URL("./HistoryPanel.tsx", import.meta.url), "utf8");
  const gridStart = source.indexOf('className="form-grid"');
  const gridEnd = source.indexOf('className="action-strip"', gridStart);
  const daysIdx = source.indexOf("\u5BFC\u51FA\u5929\u6570\uFF08N\uFF09");
  assert.ok(gridStart > -1, "export form-grid exists");
  assert.ok(daysIdx > -1, "days field label exists");
  assert.ok(daysIdx > gridStart && daysIdx < gridEnd, "days field sits inside the form grid");
});
