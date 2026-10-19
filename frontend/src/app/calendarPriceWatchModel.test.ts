import assert from "node:assert/strict";
import test from "node:test";

import {
  addMatrixBriefPushTime,
  calendarAlertLabel,
  calendarConfigFormFromConfig,
  calendarConfigPatchFromForm,
  isValidTimeOfDay,
  matrixBriefSlotLabel,
  matrixBriefStatusLabel,
  matrixBriefTimesLabel,
  normalizeMatrixBriefPushTimes,
  normalizeTimeOfDay,
  removeMatrixBriefPushTime,
} from "./calendarPriceWatchModel.ts";

test("calendar config form preserves operator defaults and caps at thirty days", () => {
  const form = calendarConfigFormFromConfig(null);
  assert.equal(form.future_days, "15");
  assert.equal(form.high_gap_threshold_yuan, "10");
  assert.equal(form.low_gap_threshold_yuan, "20");
  assert.deepEqual(form.matrix_brief_push_times, ["10:00", "15:00", "21:00"]);

  const patch = calendarConfigPatchFromForm({
    ...form,
    future_days: "31",
  });
  assert.equal(patch.future_days, 30);
});

test("calendar matrix brief form maps enabled flag and push times", () => {
  const form = calendarConfigFormFromConfig({
    watch_group_key: "ops_watch",
    future_days: 15,
    high_gap_threshold_yuan: 10,
    low_gap_threshold_yuan: 20,
    auto_poll_enabled: false,
    schedule_interval_minutes: 120,
    schedule_mode: "fixed_slots",
    schedule_start_time: "06:00",
    schedule_end_time: "01:00",
    schedule_source: "competitor_watch_group",
    browser_binding_source: "calendar_price_watch_config",
    browser_session_configured: true,
    feishu_webhook_configured: true,
    matrix_brief_enabled: true,
    matrix_brief_title: "测试矩阵",
    matrix_brief_push_times: ["21:00", "10:00", "15:00"],
    matrix_brief_morning_push_time: "10:00",
    matrix_brief_evening_push_time: "21:00",
    browser_session_id: "browser-a",
  });
  const patch = calendarConfigPatchFromForm(form);
  assert.equal(patch.matrix_brief_enabled, true);
  assert.equal(patch.low_gap_threshold_yuan, 20);
  assert.equal(patch.matrix_brief_title, "测试矩阵");
  assert.deepEqual(patch.matrix_brief_push_times, ["10:00", "15:00", "21:00"]);
  assert.equal(patch.matrix_brief_morning_push_time, "10:00");
  assert.equal(patch.matrix_brief_evening_push_time, "21:00");
  assert.equal(patch.browser_session_id, "browser-a");
});

test("matrix brief helpers validate normalize and edit push times", () => {
  assert.equal(matrixBriefStatusLabel(true), "已开启");
  assert.equal(matrixBriefStatusLabel(false), "未开启");
  assert.equal(matrixBriefSlotLabel("15:00"), "15:00 简报");
  assert.equal(matrixBriefTimesLabel(["21:00", "10:00"]), "10:00 / 21:00");
  assert.equal(isValidTimeOfDay("10:00"), true);
  assert.equal(isValidTimeOfDay("25:00"), false);
  assert.equal(normalizeTimeOfDay("15:00:00", "10:00"), "15:00");
  assert.deepEqual(
    normalizeMatrixBriefPushTimes(["21:00", "10:00", "10:00", "15:00"]),
    ["10:00", "15:00", "21:00"],
  );
  assert.deepEqual(addMatrixBriefPushTime(["10:00", "21:00"], "15:00"), ["10:00", "15:00", "21:00"]);
  assert.deepEqual(removeMatrixBriefPushTime(["10:00", "15:00", "21:00"], "15:00"), ["10:00", "21:00"]);
  assert.deepEqual(removeMatrixBriefPushTime(["10:00"], "10:00"), ["10:00"]);
});

test("calendar alert labels separate high and low risk", () => {
  assert.equal(calendarAlertLabel("own_price_high"), "本店偏高");
  assert.equal(calendarAlertLabel("own_price_low"), "本店明显低于竞对");
});
