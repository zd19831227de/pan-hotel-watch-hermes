/** 经营 BI 前端模型测试：缺失口径文案、矩阵装配、质量面板、映射标签。 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  backfillFailureSamples,
  backfillPlatformLines,
  backfillStatusText,
  forwardMatrixRows,
  metricText,
  performanceMatrixRows,
  priorYearMappingText,
  qualityLines,
  ratePercent,
  reasonText,
} from "./biDailyModel.ts";

test("reasonText 把缺失原因翻译成运营口径", () => {
  assert.equal(reasonText("no_on_the_books_source"), "暂无在手预订数据源");
  assert.equal(reasonText("budget_baseline_missing"), "未导入预算");
  assert.equal(reasonText("prior_year_baseline_missing"), "缺去年同期数据");
  assert.equal(reasonText("unknown_code"), "unknown_code");
  assert.equal(reasonText(null), "");
});

test("metricText 缺失时给原因，不用 0 顶替", () => {
  assert.equal(
    metricText({ value: null, status: "unavailable", reason: "no_inventory_source" }),
    "暂无库存数据源"
  );
  assert.equal(metricText({ value: 12, status: "available", reason: null }), "12");
  assert.equal(metricText(undefined), "");
});

test("forwardMatrixRows 装配 14 天矩阵并保留缺失口径", () => {
  const report = {
    shop_key: "shop_x",
    as_of: "2026-10-08T10:00:00",
    horizon_days: 14,
    forward: [
      {
        stay_date: "2026-10-09",
        weekday: 5,
        own: {
          booked_room_nights: {
            value: null,
            status: "unavailable" as const,
            reason: "no_on_the_books_source",
          },
        },
        competitor: {
          status: "available",
          prices: { min: 300, max: 350, count: 2 },
          hotels: [
            { hotel_id: 1, alias: "本店", role: "own", price: 300, availability: "normal", room_rates: [] },
          ],
        },
      },
      {
        stay_date: "2026-10-10",
        weekday: 6,
        own: {
          booked_room_nights: {
            value: null,
            status: "unavailable" as const,
            reason: "no_on_the_books_source",
          },
        },
        competitor: { status: "empty", prices: null, hotels: [] },
      },
    ],
    performance: [],
    quality: { coverage: {}, generated_at: "2026-10-08T10:00:00" },
  };
  const rows = forwardMatrixRows(report);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].dateLabel, "10-09");
  assert.equal(rows[0].weekday, "周五");
  assert.equal(rows[0].competitorPriceBand, "¥300 – ¥350");
  assert.equal(rows[0].ownPrice, "¥300");
  assert.equal(rows[0].ownNights, "—");
  assert.equal(rows[1].competitorPriceBand, "暂无");
  assert.equal(rows[1].ownPrice, "暂无");
});

test("performanceMatrixRows 展示三类完成率并区分缺失", () => {
  const report = {
    shop_key: "shop_x",
    as_of: "",
    horizon_days: 14,
    forward: [],
    performance: [
      {
        stay_date: "2026-10-07",
        weekday: 3,
        platforms: [],
        totals: { booked_room_nights: 88, room_revenue: 23559.62 },
        prior_year: {
          mapped_date: "2025-10-08",
          matching_rule: "minus_364",
          actual: { booked_room_nights: 80 },
          completion: { value: 1.1, status: "available" as const, reason: null },
        },
        budget: {
          version: "v1",
          budget_room_nights: 100,
          completion: { value: 0.88, status: "available" as const, reason: null },
        },
        forecast: {
          demand_forecast_room_nights: null,
          completion: {
            value: null,
            status: "unavailable" as const,
            reason: "forecast_baseline_missing",
          },
        },
      },
    ],
    quality: { coverage: {}, generated_at: "" },
  };
  const rows = performanceMatrixRows(report);
  assert.equal(rows[0].nights, "88");
  assert.equal(rows[0].priorYear, "110%");
  assert.equal(rows[0].budget, "88%");
  assert.equal(rows[0].forecast, "—");
  assert.equal(priorYearMappingText(report.performance[0]), "10-08 · 同星期（52 周前）");
});

test("qualityLines 输出口语化数据情况", () => {
  const lines = qualityLines({
    shop_key: "shop_x",
    as_of: "",
    horizon_days: 14,
    forward: [],
    performance: [],
    quality: {
      coverage: {
        dimensions: {
          competitor_prices: { status: "stale", last_captured_at: "2026-10-07T22:00:00" },
          budget_forecast: { status: "missing" },
          own_actuals: { status: "covered", latest_business_date: "2026-10-07" },
        },
      },
      generated_at: "",
    },
  });
  const text = lines.map((line) => `${line.label}:${line.text}`).join("\n");
  assert.ok(text.includes("竞品价格盯盘可能掉线"));
  assert.ok(text.includes("未导入；导入预算文件后展示完成率"));
  assert.ok(text.includes("最新到 2026-10-07"));
  assert.ok(text.includes("不会当作 0 处理"));
});

test("ratePercent 空值返回空串", () => {
  assert.equal(ratePercent(null), "");
  assert.equal(ratePercent(undefined), "");
  assert.equal(ratePercent(0.5), "50%");
});

test("biDailyApi 端点与仓库只读口径一致", async () => {
  const { readFileSync } = await import("node:fs");
  const path = await import("node:path");
  const source = readFileSync(
    path.join(process.cwd(), "src", "services", "biDailyApi.ts"),
    "utf-8"
  );
  assert.ok(source.includes("/api/v1/bi-daily/daily-report"));
  assert.ok(source.includes("/api/v1/bi-daily/budget-forecast/import"));
  assert.ok(source.includes("/api/v1/bi-daily/export/daily-report"));
  assert.ok(source.includes("/api/v1/bi-daily/collect"));
  // 严禁出现任何执行/写入价格的入口。
  assert.ok(!source.includes("dry-run"));
  assert.ok(!source.includes("execute"));
});

test("backfillStatusText 翻译回填状态口径", () => {
  assert.equal(backfillStatusText("running"), "正在补抓（浏览器逐日读取，耗时数分钟）");
  assert.equal(backfillStatusText("success"), "补抓完成");
  assert.equal(backfillStatusText("partial"), "补抓部分完成");
  assert.equal(backfillStatusText("failed"), "补抓未成功");
  assert.equal(backfillStatusText("idle"), "尚未发起补抓");
  assert.equal(backfillStatusText("whatever"), "whatever");
  assert.equal(backfillStatusText(null), "");
});

test("backfillPlatformLines 汇总每平台进度，运行中带当前日期", () => {
  const running = backfillPlatformLines({
    status: "running",
    platforms: {
      ctrip: { ok: 2, failed: 1, skipped_existing: 5, current_date: "2026-09-28" },
      meituan: { ok: 0, failed: 0 },
    },
  });
  assert.deepEqual(running, [
    "携程：成功 2 天 · 已有 5 天跳过 · 失败 1 天 · 正在读 09-28",
    "美团：成功 0 天 · 失败 0 天",
  ]);
  const done = backfillPlatformLines({
    status: "success",
    platforms: { ctrip: { ok: 3, failed: 0 } },
  });
  assert.deepEqual(done, ["携程：成功 3 天 · 失败 0 天"]);
});

test("backfillFailureSamples 截断失败明细并翻译状态", () => {
  const samples = backfillFailureSamples(
    {
      platforms: {
        ctrip: {
          failures: [
            { date: "2026-09-28", status: "human_required", reason: "需要人工完成登录或安全验证后重试。" },
            { date: "2026-09-27", status: "not_available" },
          ],
        },
        meituan: { failures: [{ date: null, status: "not_ready", reason: "未绑定账号" }] },
      },
    },
    2
  );
  assert.equal(samples.length, 2);
  assert.match(samples[0], /携程 09-28：需人工登录/);
  assert.equal(samples[1], "携程 09-27：页面无该日数据");
});
