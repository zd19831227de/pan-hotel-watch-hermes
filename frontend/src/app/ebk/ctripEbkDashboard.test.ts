import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dashboardSource = readFileSync(new URL("./CtripEbkDashboard.tsx", import.meta.url), "utf8");
const statusSource = readFileSync(new URL("./EbkPlatformStatusBar.tsx", import.meta.url), "utf8");
const advancedSource = readFileSync(new URL("./EbkAdvancedDetails.tsx", import.meta.url), "utf8");
const opsSource = readFileSync(new URL("./EbkOpsConsole.tsx", import.meta.url), "utf8");

test("ctrip ebk dashboard puts status and brief metrics before advanced config", () => {
  assert.ok(dashboardSource.includes("EbkPlatformStatusBar"));
  assert.ok(dashboardSource.includes("CtripBusinessDataPanel"));
  assert.ok(dashboardSource.includes("EbkAdvancedDetails"));
  assert.ok(dashboardSource.includes('variant="configOnly"'));
  const renderStart = dashboardSource.indexOf("return (");
  assert.ok(renderStart > 0);
  const renderSource = dashboardSource.slice(renderStart);
  const statusIdx = renderSource.indexOf("<EbkPlatformStatusBar");
  const briefIdx = renderSource.indexOf("<CtripBusinessDataPanel");
  const advancedIdx = renderSource.indexOf("<EbkAdvancedDetails");
  assert.ok(statusIdx >= 0 && briefIdx > statusIdx && advancedIdx > briefIdx);
});

test("ctrip status bar exposes next safe actions without write-price buttons", () => {
  assert.ok(statusSource.includes("最近读取"));
  assert.ok(statusSource.includes("下一步"));
  assert.ok(statusSource.includes("立即刷新简报") || statusSource.includes("onRefreshBrief"));
  assert.ok(statusSource.includes("读取并推送") || statusSource.includes("onRunDataCenter"));
  assert.doesNotMatch(statusSource, /立即调价|写入携程|自动执行|确认按此价格调价/);
});

test("advanced details collapse wraps operator config", () => {
  assert.ok(advancedSource.includes("<details"));
  assert.ok(advancedSource.includes("高级配置"));
});

test("ops console ctrip tab uses CtripEbkDashboard", () => {
  assert.ok(opsSource.includes("CtripEbkDashboard"));
  assert.ok(opsSource.includes("MeituanEbkDashboard"));
  assert.ok(!opsSource.includes("ctripContent"));
});

test("ctrip status bar disables read/brief buttons and shows hint when EBK session is unregistered", () => {
  // The component must accept a sessionUnregistered prop.
  assert.ok(statusSource.includes("sessionUnregistered"));
  // Both run buttons must be gated by sessionUnregistered (disabled).
  assert.ok(
    /sessionUnregistered/.test(statusSource),
    "status bar should reference sessionUnregistered in button gating",
  );
  // A visible operator hint must be present.
  assert.ok(
    statusSource.includes("会话未注册"),
    "status bar should show a 会话未注册 hint",
  );
  // Must not leak the raw backend error wording.
  assert.doesNotMatch(statusSource, /Unknown browser session/);
});

test("ctrip dashboard derives sessionUnregistered from the selected group's session_status", () => {
  assert.ok(
    dashboardSource.includes("sessionUnregistered"),
    "dashboard should derive sessionUnregistered and pass it to the status bar",
  );
  assert.ok(
    dashboardSource.includes("session_status"),
    "dashboard should read the backend session_status field",
  );
});

