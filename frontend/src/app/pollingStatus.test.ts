import assert from "node:assert/strict";
import test from "node:test";

import {
  calendarAggregateStatus,
  calendarPriceStatusText,
  competitorAggregateStatus,
  dailyBriefStatusText,
  globalPushStripStatus,
  priceChangeAggregateStatus,
  ebkStatusStripDetail,
  ebkStatusStripText,
  listActiveEbkScheduleTasks,
  manualCompetitorRunButtonText,
  manualEbkRunButtonText,
  otherActiveEbkScheduleSummary,
  pollingStatusText,
  priceChangeAlertDetailText,
  priceChangeAlertStatusText,
  schedulerDetailText,
  startPollingButtonText,
  stopPollingButtonText,
  canStartPolling,
  canStopDailyBrief,
  canStopPolling,
  ebkPushSourceText,
} from "./pollingStatus.ts";

test("轮询状态区分配置开关和后台心跳", () => {
  assert.equal(pollingStatusText(null), "未创建");
  assert.equal(pollingStatusText({ enabled: false, auto_poll_enabled: true }), "已停用");
  assert.equal(pollingStatusText({ enabled: true, auto_poll_enabled: false }), "自动轮询已暂停");
  assert.equal(
    pollingStatusText({
      enabled: true,
      auto_poll_enabled: true,
      scheduler_status: { status: "healthy", process_confirmed: true },
    }),
    "自动轮询已开启",
  );
  assert.equal(
    pollingStatusText({
      enabled: true,
      auto_poll_enabled: true,
      scheduler_status: { status: "missing", process_confirmed: false },
    }),
    "自动轮询开启，后台未确认",
  );
});

test("轮询状态详情不展示技术字段", () => {
  assert.equal(schedulerDetailText(null), "暂无轮询心跳");
  assert.match(
    schedulerDetailText({
      status: "healthy",
      process_confirmed: true,
      last_heartbeat_at: "2026-06-18T08:05:00+00:00",
    }),
    /后台已确认，最近心跳/,
  );
});

test("手动读取按钮语义统一为读取并推送", () => {
  assert.equal(manualEbkRunButtonText(), "读取并推送");
  assert.equal(manualCompetitorRunButtonText(), "读取并推送");
});

test("轮询开关按钮根据当前配置决定可用状态", () => {
  assert.equal(startPollingButtonText(), "开启自动轮询");
  assert.equal(stopPollingButtonText(), "暂停自动轮询");
  assert.equal(canStartPolling({ enabled: true, auto_poll_enabled: false }), true);
  assert.equal(canStartPolling({ enabled: true, auto_poll_enabled: true }), false);
  assert.equal(canStartPolling({ enabled: false, auto_poll_enabled: false }), false);
  assert.equal(canStopPolling({ enabled: true, auto_poll_enabled: true }), true);
  assert.equal(canStopPolling({ enabled: true, auto_poll_enabled: false }), false);
});

test("日历价与价格变化提醒状态条文案", () => {
  assert.equal(calendarPriceStatusText(null), "未创建");
  assert.equal(
    calendarPriceStatusText({
      groupEnabled: true,
      configLoaded: undefined,
    }),
    "检查中",
  );
  assert.equal(
    calendarPriceStatusText({
      groupEnabled: true,
      configLoaded: true,
      auto_poll_enabled: true,
      scheduler_status: { status: "healthy", process_confirmed: true },
    }),
    "自动轮询已开启",
  );
  assert.equal(
    priceChangeAlertStatusText({
      strategyEnabled: false,
      watchGroup: { enabled: true, auto_poll_enabled: true },
    }),
    "提醒已停用",
  );
  assert.equal(
    priceChangeAlertStatusText({
      strategyEnabled: true,
      watchGroup: {
        enabled: true,
        auto_poll_enabled: true,
        scheduler_status: { status: "healthy", process_confirmed: true },
      },
    }),
    "自动轮询已开启",
  );
  assert.match(
    priceChangeAlertDetailText(
      {
        enabled: true,
        auto_poll_enabled: true,
        scheduler_status: {
          status: "healthy",
          process_confirmed: true,
          last_heartbeat_at: "2026-07-07T12:37:58+00:00",
        },
      },
      "2026-07-07T10:15:00+00:00",
    ),
    /后台已确认，最近心跳.*最近评估/,
  );
});

test("EBK 定时任务汇总区分数据中心轮询与每日简报", () => {
  const groups = [
    {
      group_key: "default",
      name: "默认组",
      enabled: true,
      auto_poll_enabled: false,
      daily_brief_enabled: true,
      daily_brief_push_time: "12:00",
    },
    {
      group_key: "extra",
      name: "高空江景组",
      enabled: true,
      auto_poll_enabled: true,
      daily_brief_enabled: false,
      daily_brief_push_time: "08:30",
    },
  ];
  assert.equal(listActiveEbkScheduleTasks(groups).length, 2);
  assert.equal(ebkStatusStripText(groups), "2 个定时任务运行中");
  assert.match(ebkStatusStripDetail(groups), /默认组：每日简报 12:00/);
  assert.equal(dailyBriefStatusText(groups[0]), "每日 12:00 推送");
  assert.equal(canStopDailyBrief(groups[0]), true);
  assert.equal(
    otherActiveEbkScheduleSummary(groups, "default"),
    "另有 1 个定时任务在其他监控组：高空江景组（数据中心轮询）",
  );
});

const G_GLOBAL = "全局定时推送接管中";
const G_ON = "自动轮询已开启";
const G_OFF = "自动轮询已暂停";
test("ebkPushSourceText smart source", () => {
  const group = { enabled: true, auto_poll_enabled: true, scheduler_status: { status: "healthy", process_confirmed: true } };
  assert.equal(ebkPushSourceText(group, { globalPushEnabled: true }), G_GLOBAL);
  assert.equal(ebkPushSourceText(group, { globalPushEnabled: false }), G_ON);
  assert.equal(ebkPushSourceText({ enabled: true, auto_poll_enabled: false }, { globalPushEnabled: false }), G_OFF);
});

test("竞品监控状态条跨组聚合，不再只看选中组", () => {
  assert.equal(competitorAggregateStatus(null).text, "未创建");
  assert.equal(competitorAggregateStatus([]).text, "未创建");
  assert.equal(
    competitorAggregateStatus([{ enabled: false, auto_poll_enabled: false }]).text,
    "全部已停用",
  );
  const pollingOff = competitorAggregateStatus([
    { enabled: true, auto_poll_enabled: false, name: "澜江路组" },
  ]);
  assert.equal(pollingOff.text, "1 组启用，轮询未开启");
  assert.match(pollingOff.detail, /澜江路组/);
  const healthy = { status: "healthy", process_confirmed: true, last_heartbeat_at: "2026-09-10T12:00:00" };
  const mixed = competitorAggregateStatus([
    { enabled: true, auto_poll_enabled: true, name: "A组", scheduler_status: healthy },
    { enabled: true, auto_poll_enabled: false, name: "B组" },
  ]);
  assert.equal(mixed.text, "1/2 组自动轮询中");
  assert.match(mixed.detail, /A组/);
  assert.match(mixed.detail, /后台已确认/);
});

test("日历价提醒状态条跨组聚合", () => {
  assert.equal(calendarAggregateStatus(null, null).text, "未创建");
  assert.equal(calendarAggregateStatus([], null).text, "未创建");
  const stopped = calendarAggregateStatus(
    [{ watch_group_key: "g1", group_name: "G1", group_enabled: true, auto_poll_enabled: false }],
    null,
  );
  assert.equal(stopped.text, "1 组启用，轮询未开启");
  const running = calendarAggregateStatus(
    [
      { watch_group_key: "g1", group_name: "G1", group_enabled: true, auto_poll_enabled: true },
      { watch_group_key: "g2", group_name: "G2", group_enabled: true, auto_poll_enabled: false },
    ],
    { status: "healthy", process_confirmed: true },
  );
  assert.equal(running.text, "1/2 组自动轮询中");
  assert.match(running.detail, /G1/);
});

test("价格变化提醒聚合状态跟随竞品轮询", () => {
  assert.equal(priceChangeAggregateStatus(false, null, [{ enabled: true, auto_poll_enabled: true }]).text, "提醒已停用");
  const waiting = priceChangeAggregateStatus(true, "A组", [
    { enabled: true, auto_poll_enabled: false },
  ]);
  assert.equal(waiting.text, "已启用，等待竞品轮询");
  const running = priceChangeAggregateStatus(
    true,
    "A组",
    [{ enabled: true, auto_poll_enabled: true, name: "A组" }],
  );
  assert.equal(running.text, "跟随竞品轮询运行中");
});

test("EBK 全局推送状态条", () => {
  assert.equal(globalPushStripStatus(null, null).text, "未配置");
  assert.equal(
    globalPushStripStatus([{ id: 1, name: "推送1", enabled: false }], null).text,
    "全部已停用",
  );
  assert.equal(
    globalPushStripStatus(
      [{ id: 1, name: "推送1", enabled: true }],
      { enabled: true, status: "healthy", last_heartbeat_at: "2026-09-10T08:00:00" },
    ).text,
    "1 个推送运行中",
  );
  assert.equal(
    globalPushStripStatus(
      [{ id: 1, name: "推送1", enabled: true }],
      { enabled: false, status: "disabled", last_heartbeat_at: null },
    ).text,
    "1 个推送，调度未启用",
  );
});
