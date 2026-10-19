import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  buildRecentRunRows,
  globalPushFormFromConfig,
  globalPushNextRunLabel,
  globalPushPatchFromForm,
  globalPushScheduleSummary,
  globalPushStatusLabel,
  globalPushTriggerLabel,
  parseGlobalPushTimesInput,
} from "./globalPushModel.ts";

test("push times input parses, dedupes and sorts", () => {
  const result = parseGlobalPushTimesInput("20:00、08:00, 08:00 12:30");
  assert.deepEqual(result.times, ["08:00", "12:30", "20:00"]);
  assert.equal(result.error, null);
});

test("push times input rejects invalid format", () => {
  const result = parseGlobalPushTimesInput("08:00, 24:00");
  assert.equal(result.times.length, 0);
  assert.ok(result.error?.includes("24:00"));
});

test("patch from form validates interval bounds", () => {
  const form = globalPushFormFromConfig(null);
  form.ctrip_watch_group_key = "anchor";
  form.name = "门店";
  form.schedule_mode = "last_run_interval";
  form.interval_minutes = "5";
  const invalid = globalPushPatchFromForm(form);
  assert.equal(invalid.error, "间隔分钟需在 30-1440 之间。");
  form.interval_minutes = "90";
  const valid = globalPushPatchFromForm(form);
  assert.equal(valid.error, null);
  assert.equal((valid.patch as { interval_minutes: number }).interval_minutes, 90);
});

test("patch from form requires anchor and platforms", () => {
  const noAnchor = globalPushPatchFromForm(globalPushFormFromConfig(null));
  assert.ok(noAnchor.error?.includes("携程监控组"));
  const form = globalPushFormFromConfig(null);
  form.ctrip_watch_group_key = "anchor";
  form.name = "门店";
  form.enabled_platforms = [];
  const noPlatform = globalPushPatchFromForm(form);
  assert.ok(noPlatform.error?.includes("平台"));
});

test("schedule summary and next run labels", () => {
  const view = {
    id: 1,
    ctrip_watch_group_key: "anchor",
    name: "门店",
    enabled: true,
    enabled_platforms: ["ctrip"],
    schedule_mode: "fixed_slots" as const,
    push_times: ["08:00", "20:00"],
    interval_minutes: 60,
    window_start_time: "06:00",
    window_end_time: "23:00",
    card_title_business: null,
    card_title_promotion: null,
    card_title_advertising: null,
    last_attempted_at: null,
    last_successful_push_at: null,
    next_run_at: "2026-08-28T20:00:00",
    consecutive_failure_count: 0,
  };
  assert.equal(globalPushScheduleSummary(view), "固定时刻 08:00、20:00");
  assert.equal(globalPushNextRunLabel(view), "2026-08-28 20:00");
  assert.equal(globalPushNextRunLabel({ ...view, enabled: false }), "已停用");
});

test("status labels cover run states", () => {
  assert.equal(globalPushStatusLabel("partial"), "部分成功");
  assert.equal(globalPushStatusLabel("skipped_overlapping"), "互斥跳过");
  assert.equal(globalPushStatusLabel("legacy"), "legacy");
});

test("global push panel exposes resolved platform targets and all three card titles", () => {
  // T2.4/设计 §10.1：详情解析三平台账号 + 三张卡标题均可编辑
  const panelSource = readFileSync(new URL("./GlobalPushPanel.tsx", import.meta.url), "utf8");
  const apiSource = readFileSync(
    new URL("../../services/ebkGlobalPushApi.ts", import.meta.url),
    "utf8",
  );
  assert.ok(apiSource.includes("getEbkGlobalPushConfigDetail"));
  assert.ok(panelSource.includes("platform_targets"));
  assert.ok(panelSource.includes("已解析平台账号"));
  assert.ok(panelSource.includes("card_title_promotion"));
  assert.ok(panelSource.includes("card_title_advertising"));
});

test("global push panel run-now button renamed with progress + legacy warning", () => {
  // 问题 1/2 修复：按钮改名「立即读取并推送」+ 读取中进度；legacy 块醒目提示
  const panelSource = readFileSync(new URL("./GlobalPushPanel.tsx", import.meta.url), "utf8");
  assert.ok(panelSource.includes("立即读取并推送"));
  assert.ok(panelSource.includes("runningNow"));
  assert.ok(panelSource.includes("legacy_schedule_still_enabled"));
  assert.ok(panelSource.includes("读取中"));
});

test("global push actions use the ctrip button hierarchy without duplicate disable action", () => {
  const panelSource = readFileSync(new URL("./GlobalPushPanel.tsx", import.meta.url), "utf8");
  assert.match(
    panelSource,
    /className="primary-button"[^>]*onClick=\{onRunNow\}[\s\S]*?<Play size=\{16\} \/>/,
  );
  assert.match(
    panelSource,
    /className="secondary-button"[^>]*onClick=\{\(\) => onToggleEnabled\(!selected\.enabled\)\}/,
  );
  assert.match(
    panelSource,
    /className="primary-button"[^>]*onClick=\{selected \? onSave : onCreate\}/,
  );
  assert.doesNotMatch(panelSource, />\s*软停用\s*</);
});

test("global push panel is shop-first: shop select, unprovisioned shops guided, no raw anchor keys", () => {
  // 店铺直连口径：锚点选择按店铺呈现；未接入携程的新店可见且有指引；
  // 配置下拉不再拼裸 ctrip_watch_group_key，展示店铺名保持同步。
  const panelSource = readFileSync(new URL("./GlobalPushPanel.tsx", import.meta.url), "utf8");
  assert.ok(panelSource.includes("推送店铺"));
  assert.ok(panelSource.includes("未接入携程"));
  assert.ok(panelSource.includes("接入携程"));
  assert.ok(panelSource.includes("shops"));
  assert.ok(panelSource.includes("dataCenterGroups"));
  assert.doesNotMatch(panelSource, /\{item\.ctrip_watch_group_key\}/);
});

test("trigger words map to distinct operator-facing labels", () => {
  assert.equal(globalPushTriggerLabel("scheduler"), "定时推送");
  assert.equal(globalPushTriggerLabel("operator"), "手动推送");
  assert.equal(globalPushTriggerLabel("feishu"), "飞书触发");
  assert.equal(globalPushTriggerLabel("test"), "测试");
  // 未知值原样透传，不吞掉新类型。
  assert.equal(globalPushTriggerLabel("cron_v2"), "cron_v2");
});

test("recent run rows merge push runs and day snapshot rounds, sorted desc", () => {
  const rows = buildRecentRunRows(
    [
      {
        id: 11,
        trigger: "scheduler",
        status: "success",
        push_finished_at: "2026-09-08T12:05:00",
        platform_results: { ctrip: { status: "success" }, meituan: { status: "human_required" } },
        error_summary: null,
      },
      {
        id: 10,
        trigger: "operator",
        status: "failed",
        push_finished_at: "2026-09-07T18:40:00",
        platform_results: {},
        error_summary: "美团读取失败",
      },
    ],
    [
      {
        id: 3,
        status: "partial",
        triggered_at: "2026-09-08T09:41:00",
        platform_results: {
          ctrip: { status: "success", detail: "经营成功·排名成功" },
          fliggy: { status: "partial", detail: "经营成功·排名无数据" },
        },
        error_summary: null,
      },
    ],
  );
  assert.equal(rows.length, 3);
  // 按时间倒序：12:05 定时推送 → 09:41 昨日快照轮 → 昨天 18:40 手动推送。
  assert.deepEqual(
    rows.map((row) => row.triggerLabel),
    ["定时推送", "昨日快照轮", "手动推送"],
  );
  assert.equal(rows[0].statusLabel, "成功");
  assert.equal(rows[0].platformResultsText, "✈️成功 / 🦘需人工处理");
  assert.equal(rows[1].statusLabel, "部分完成");
  assert.equal(rows[1].platformResultsText, "✈️完成 / 🧳部分完成");
  // 无 error_summary 时说明列展示未完成平台的明细。
  assert.equal(rows[1].note, "飞猪：经营成功·排名无数据");
  assert.equal(rows[2].statusLabel, "失败");
  assert.equal(rows[2].note, "美团读取失败");
  assert.equal(rows[2].platformResultsText, "-");
});

test("day snapshot runs fetch is fault-isolated from the push panel load", () => {
  // 快照轮运行日志拉取失败（如后端未重启端点 404）只置空，不拖垮主面板加载。
  const panelSource = readFileSync(new URL("./GlobalPushPanel.tsx", import.meta.url), "utf8");
  assert.match(
    panelSource,
    /try \{\s*setDayRuns\(await listEbkDaySnapshotRuns\(10\)\);\s*\} catch \{\s*setDayRuns\(\[\]\);/,
  );
  assert.doesNotMatch(panelSource, /listEbkDaySnapshotRuns\(10\),\s*\n\s*\]/);
});
