import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const pageHealthApiSource = readFileSync(
  new URL("../services/pageHealthApi.ts", import.meta.url),
  "utf8",
);
const meituanApiSource = readFileSync(
  new URL("../services/meituanEbookingApi.ts", import.meta.url),
  "utf8",
);
const fliggyApiSource = readFileSync(
  new URL("../services/fliggyEbookingApi.ts", import.meta.url),
  "utf8",
);
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const globalCssSource = readFileSync(
  new URL("../styles/global.css", import.meta.url),
  "utf8",
);
const pageHealthPanelPath = new URL("./PageHealthPanel.tsx", import.meta.url);

test("pageHealthApi wraps /page-health/alerts with filters", () => {
  assert.match(pageHealthApiSource, /export async function fetchPageHealthAlerts/);
  assert.match(pageHealthApiSource, /\/api\/v1\/page-health\/alerts/);
  assert.match(pageHealthApiSource, /platform/);
  assert.match(pageHealthApiSource, /level/);
});

test("page health panel exists with level colors and empty state", () => {
  assert.ok(existsSync(pageHealthPanelPath), "PageHealthPanel.tsx must exist");
  const panelSource = readFileSync(pageHealthPanelPath, "utf8");
  assert.match(panelSource, /fetchPageHealthAlerts/);
  assert.match(panelSource, /hard_fail/);
  assert.match(panelSource, /partial_fail/);
  assert.match(panelSource, /layout_changed/);
  assert.match(panelSource, /暂无.*告警|还没有/);
  // 级别颜色与飞书卡片一致：红 critical / 橙 warning / 蓝 info
  assert.match(globalCssSource, /#dc2626/);
  assert.match(globalCssSource, /#ea580c/);
  assert.match(globalCssSource, /#2563eb/);
  assert.match(panelSource, /level-critical/);
  assert.match(panelSource, /level-warning/);
  assert.match(panelSource, /level-info/);
  assert.match(panelSource, /page-health-alert-row/);
  assert.match(globalCssSource, /\.page-health-alert-row\s*\{[^}]*display:\s*flex/s);
  assert.match(globalCssSource, /\.page-health-alert-content\s*\{[^}]*min-width:\s*0[^}]*flex:\s*1/s);
  // 只读卡片：不得出现任何写价/执行入口
  assert.ok(!panelSource.includes("调价"), "page health panel must not expose pricing actions");
});

test("顶部 Tab 标签不被压缩成逐字换行", () => {
  assert.match(globalCssSource, /\.tab\s*\{[^}]*white-space:\s*nowrap/s);
  assert.match(globalCssSource, /\.tab\s*\{[^}]*flex-shrink:\s*0/s);
  assert.match(globalCssSource, /@media\s*\(max-width:\s*680px\)[\s\S]*?\.tabbar\s*\{[^}]*overflow-x:\s*auto/s);
});

test("app renders page health card inside advanced maintenance area", () => {
  assert.match(appSource, /import \{ PageHealthPanel \}/);
  assert.match(appSource, /PageHealthPanel/);
  assert.match(appSource, /页面健康/);
});

test("meituanEbookingApi exposes read-data-center and read-dataset wrappers", () => {
  assert.match(meituanApiSource, /export async function readMeituanDataCenter/);
  assert.match(meituanApiSource, /export async function readMeituanDataset/);
  assert.match(
    meituanApiSource,
    /\/api\/v1\/meituan-ebooking\/sessions\/\$\{encodeURIComponent\(input\.platformAccountId\)\}\/read-data-center/,
  );
  assert.match(
    meituanApiSource,
    /\/api\/v1\/meituan-ebooking\/sessions\/\$\{encodeURIComponent\(input\.platformAccountId\)\}\/read-dataset/,
  );
  assert.match(meituanApiSource, /dataset: input\.dataset/);
});

test("fliggyEbookingApi exposes read-dataset wrapper", () => {
  assert.match(fliggyApiSource, /export async function readFliggyDataset/);
  assert.match(
    fliggyApiSource,
    /\/api\/v1\/fliggy-ebooking\/sessions\/\$\{encodeURIComponent\(input\.platformAccountId\)\}\/read-dataset/,
  );
  assert.match(fliggyApiSource, /dataset: input\.dataset/);
});

test("page health panel supports deleting a handled alert", () => {
  const panelSource = readFileSync(new URL("./PageHealthPanel.tsx", import.meta.url), "utf8");
  const apiSource = readFileSync(new URL("../services/pageHealthApi.ts", import.meta.url), "utf8");
  assert.match(panelSource, /deletePageHealthAlert/);
  assert.match(panelSource, /page-health-alert-delete/);
  assert.match(apiSource, /export async function deletePageHealthAlert/);
  assert.match(apiSource, /method: "DELETE"/);
});
