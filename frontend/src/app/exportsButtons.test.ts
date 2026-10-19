import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const exportsApiSource = readFileSync(
  new URL("../services/exportsApi.ts", import.meta.url),
  "utf8",
);
const distillPanelSource = readFileSync(
  new URL("./DistillPanel.tsx", import.meta.url),
  "utf8",
);
const historyPanelSource = readFileSync(
  new URL("./HistoryPanel.tsx", import.meta.url),
  "utf8",
);
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");

test("exportsApi wraps four csv export endpoints with blob download", () => {
  for (const fn of [
    "exportCompetitorPriceTrendCsv",
    "exportPriceChangeAlertsCsv",
    "exportCtripEbkRunsCsv",
    "exportDistillRunsCsv",
  ]) {
    assert.ok(exportsApiSource.includes(`export async function ${fn}`), fn);
  }
  assert.match(exportsApiSource, /\/api\/v1\/export\/competitor-price-trend\.csv/);
  assert.match(exportsApiSource, /\/api\/v1\/export\/price-change-alerts\.csv/);
  assert.match(exportsApiSource, /\/api\/v1\/export\/ctrip-ebk-runs\.csv/);
  assert.match(exportsApiSource, /\/api\/v1\/export\/distill-runs\.csv/);
  assert.match(exportsApiSource, /\.blob\(\)/);
});

test("distill panel has per-run inline csv export button", () => {
  assert.match(distillPanelSource, /exportDistillRun/);
  assert.match(distillPanelSource, /导出/);
});

test("history panel exposes csv export for ebk/price blocks", () => {
  for (const marker of [
    "exportCtripEbkRunsCsv",
    "exportPriceChangeAlertsCsv",
    "导出 EBK 运行记录",
    "导出价格告警",
  ]) {
    assert.ok(historyPanelSource.includes(marker), marker);
  }
  // 修正3：蒸馏历史导出在自学习面板内，不在业务历史面板
  assert.ok(!historyPanelSource.includes("exportDistillRunsCsv"), "distill export must stay in distill panel");
});

test("competitor tab exposes trend csv export", () => {
  assert.match(appSource, /exportCompetitorPriceTrendCsv/);
  assert.match(appSource, /导出 CSV/);
});
