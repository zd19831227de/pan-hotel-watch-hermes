import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const meituanSource = readFileSync(new URL("./MeituanEbkDashboard.tsx", import.meta.url), "utf8");
const opsSource = readFileSync(new URL("./EbkOpsConsole.tsx", import.meta.url), "utf8");
const appSource = readFileSync(new URL("../App.tsx", import.meta.url), "utf8");

test("meituan dashboard exposes read-and-save CTA without write-price actions", () => {
  assert.ok(meituanSource.includes("读取并保存"));
  assert.ok(meituanSource.includes("onReadAndSave"));
  assert.ok(meituanSource.includes("EbkAdvancedDetails"));
  assert.ok(!meituanSource.includes("刷新最新数据"));
  assert.ok(!meituanSource.includes("onRefreshLatest"));
  assert.doesNotMatch(meituanSource, /立即调价|写入携程|自动执行|确认按此价格调价/);
  assert.ok(!meituanSource.includes("runDataCenterNow"));
  assert.ok(!meituanSource.includes("runCompetitorNow"));
});

test("ebk ops console keeps joint analysis out of the frontend platform switcher", () => {
  assert.ok(!opsSource.includes("EbkJointAnalysisPanel"));
  assert.ok(!opsSource.includes('"joint"'));
  assert.ok(!opsSource.includes("????"));
});

test("app wires meituan read-and-save to ebk snapshots read-all", () => {
  assert.ok(appSource.includes("readAllEbkSnapshots"));
  assert.ok(appSource.includes("onReadAndSave"));
  assert.ok(appSource.includes("pushToFeishu: true"));
  assert.ok(appSource.includes("feishuWatchGroupKey: dataCenterGroup?.group_key"));
});
