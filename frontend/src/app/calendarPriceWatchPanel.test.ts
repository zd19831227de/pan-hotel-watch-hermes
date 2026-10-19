import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");

test("calendar price watch follows competitor timing and read account configuration", () => {
  const calendarPanelStart = appSource.indexOf("function CalendarPricePanel");
  assert.ok(calendarPanelStart > 0);
  const calendarPanelSource = appSource.slice(calendarPanelStart, calendarPanelStart + 20000);

  assert.ok(calendarPanelSource.includes("读取窗口"));
  assert.ok(calendarPanelSource.includes("轮询模式"));
  assert.ok(calendarPanelSource.includes("下一次轮询"));
  assert.ok(calendarPanelSource.includes("默认跟随竞品价格"));
  assert.ok(calendarPanelSource.includes("日历价读取账号"));
  assert.ok(calendarPanelSource.includes("独立 Profile"));
  assert.doesNotMatch(calendarPanelSource, /跟随竞品价格固定读取账号/);
});

test("calendar price panel gates polling buttons by current auto-poll state", () => {
  const calendarPanelStart = appSource.indexOf("function CalendarPricePanel");
  assert.ok(calendarPanelStart > 0);
  const calendarPanelSource = appSource.slice(calendarPanelStart, calendarPanelStart + 20000);

  assert.ok(calendarPanelSource.includes("canStartPolling"));
  assert.ok(calendarPanelSource.includes("canStopPolling"));
  assert.ok(calendarPanelSource.includes("props.config?.auto_poll_enabled"));
});

test("calendar price panel exposes matrix brief management and flat competitor table", () => {
  const calendarPanelStart = appSource.indexOf("function CalendarPricePanel");
  assert.ok(calendarPanelStart > 0);
  const calendarPanelSource = appSource.slice(calendarPanelStart, calendarPanelStart + 25000);

  assert.ok(calendarPanelSource.includes("矩阵定时推送"));
  assert.ok(calendarPanelSource.includes("立即推送矩阵到飞书"));
  assert.ok(calendarPanelSource.includes("未来价格矩阵"));
  assert.ok(calendarPanelSource.includes("calendar-matrix"));
  assert.ok(calendarPanelSource.includes("competitorColumns"));
  assert.doesNotMatch(calendarPanelSource, /展开竞对/);
  assert.doesNotMatch(calendarPanelSource, /calendar-competitor-details/);
  assert.ok(calendarPanelSource.includes("onPushMatrixBrief"));
  assert.ok(calendarPanelSource.includes("matrix_brief_push_times"));
  assert.ok(calendarPanelSource.includes("10:00 / 15:00 / 21:00"));
  assert.ok(calendarPanelSource.includes("addMatrixBriefPushTime"));
  assert.ok(calendarPanelSource.includes("removeMatrixBriefPushTime"));
});
