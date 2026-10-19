import assert from "node:assert/strict";
import test from "node:test";

import { buildEchartsOption, toEchartData, type EchartDataPoint } from "./competitorTrendEchart.ts";
import {
  buildPriceAxis,
  buildCompetitorTrendChartGroups,
  formatTrendTooltip,
  type TrendChartModel,
} from "./competitorTrendModel.ts";
import type { CompetitorPriceTrend } from "../services/competitorWatchApi.ts";

test("趋势图纵轴按本图价格范围自动生成可读视窗", () => {
  assert.deepEqual(buildPriceAxis(394, 417), { min: 380, max: 430, interval: 10 });
  assert.deepEqual(buildPriceAxis(459, 481), { min: 440, max: 500, interval: 10 });
  assert.deepEqual(buildPriceAxis(180, 230), { min: 170, max: 240, interval: 10 });
  assert.deepEqual(buildPriceAxis(900, 1450), { min: 800, max: 1600, interval: 100 });
  assert.deepEqual(buildPriceAxis(1200, 1200), { min: 1100, max: 1300, interval: 50 });
});

test("趋势图跨缺失读取时间点连线，但不补假价格点", () => {
  const model: TrendChartModel = {
    sourceLabel: "可信 DOM 房价",
    title: "今日无早价格趋势",
    breakfastKind: "none",
    xLabels: ["06-19 08:00", "06-19 09:00", "06-19 10:00"],
    minPrice: 416,
    maxPrice: 421,
    axisMin: 400,
    axisMax: 440,
    axisInterval: 20,
    series: [
      {
        id: "1|today|none",
        label: "本店 · 高级大床房 · 今日 · 无早",
        hotelLabel: "本店",
        roomType: "高级大床房",
        breakfastLabel: "无早",
        dateLabel: "今日",
        role: "own",
        color: "#2563eb",
        roomTypes: ["高级大床房"],
        roomTypeChanged: false,
        points: [
          {
            x: 0,
            price: 416,
            bucketAt: "2026-06-19T08:00:00",
            label: "06-19 08:00",
            roomType: "高级大床房",
          },
          {
            x: 2,
            price: 421,
            bucketAt: "2026-06-19T10:00:00",
            label: "06-19 10:00",
            roomType: "高级大床房",
          },
        ],
      },
    ],
  };

  const option = buildEchartsOption(model);
  const firstSeries = (Array.isArray(option.series) ? option.series[0] : undefined) as
    | { connectNulls?: boolean }
    | undefined;
  const data = toEchartData(model.series[0], model);

  assert.equal(firstSeries?.connectNulls, true);
  assert.equal(data.length, 3);
  assert.equal((data[0] as EchartDataPoint).value, 416);
  assert.equal(data[1], null);
  assert.equal((data[2] as EchartDataPoint).value, 421);
});

test("趋势图只使用真实价格点并保留酒店和房型标签", () => {
  const trend: CompetitorPriceTrend = {
    status: "success",
    summary: "ok",
    watch_group_key: "ops_watch",
    granularity: "run",
    days: 7,
    generated_at: "2026-06-18T08:05:00",
    point_count: 4,
    trusted_source: "room_rate_dom_display_price",
    points: [],
    series: [
      {
        series_id: "1:today:2026-06-18:none:高级大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "today",
        checkin_date: "2026-06-18",
        checkout_date: "2026-06-19",
        breakfast_kind: "none",
        room_type: "高级大床房",
        points: [
          {
            bucket_at: "2026-06-17T08:00:00",
            captured_at: "2026-06-17T08:00:00",
            price_amount_yuan: 416,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
          {
            bucket_at: "2026-06-18T08:00:00",
            captured_at: "2026-06-18T08:00:00",
            price_amount_yuan: 394,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
      {
        series_id: "1:today:2026-06-18:included:高级大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "today",
        checkin_date: "2026-06-18",
        checkout_date: "2026-06-19",
        breakfast_kind: "included",
        room_type: "高级大床房",
        points: [
          {
            bucket_at: "2026-06-17T08:00:00",
            captured_at: "2026-06-17T08:00:00",
            price_amount_yuan: 481,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
      {
        series_id: "1:tomorrow:2026-06-19:none:高级大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "tomorrow",
        checkin_date: "2026-06-19",
        checkout_date: "2026-06-20",
        breakfast_kind: "none",
        room_type: "高级大床房",
        points: [
          {
            bucket_at: "2026-06-17T08:00:00",
            captured_at: "2026-06-17T08:00:00",
            price_amount_yuan: 394,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
      {
        series_id: "1:tomorrow:2026-06-19:included:高级大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "tomorrow",
        checkin_date: "2026-06-19",
        checkout_date: "2026-06-20",
        breakfast_kind: "included",
        room_type: "高级大床房",
        points: [
          {
            bucket_at: "2026-06-17T08:00:00",
            captured_at: "2026-06-17T08:00:00",
            price_amount_yuan: 459,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
      {
        series_id: "2:today:2026-06-18:none:商务大床房",
        hotel_config_id: 2,
        hotel_role: "competitor",
        sort_order: 10,
        hotel_alias: "竞品A",
        hotel_name: "泉吉云州澜江路步行街酒店",
        ctrip_hotel_id: "100002",
        date_label: "today",
        checkin_date: "2026-06-18",
        checkout_date: "2026-06-19",
        breakfast_kind: "none",
        room_type: "商务大床房",
        points: [
          {
            bucket_at: "2026-06-17T08:00:00",
            captured_at: "2026-06-17T08:00:00",
            price_amount_yuan: 415,
            room_type: "商务大床房",
            availability_statuses: ["available"],
          },
          {
            bucket_at: "2026-06-18T08:00:00",
            captured_at: "2026-06-18T08:00:00",
            price_amount_yuan: null,
            room_type: "商务大床房",
            availability_statuses: [],
          },
        ],
      },
      {
        series_id: "2:today:2026-06-19:none:商务大床房",
        hotel_config_id: 2,
        hotel_role: "competitor",
        sort_order: 10,
        hotel_alias: "竞品A",
        hotel_name: "泉吉云州澜江路步行街酒店",
        ctrip_hotel_id: "100002",
        date_label: "today",
        checkin_date: "2026-06-19",
        checkout_date: "2026-06-20",
        breakfast_kind: "none",
        room_type: "商务大床房",
        points: [
          {
            bucket_at: "2026-06-19T08:00:00",
            captured_at: "2026-06-19T08:00:00",
            price_amount_yuan: 417,
            room_type: "商务大床房",
            availability_statuses: ["available"],
          },
        ],
      },
    ],
  };

  const groups = buildCompetitorTrendChartGroups(trend);
  const todayNoBreakfast = groups.todayNoBreakfast;
  const todayWithBreakfast = groups.todayWithBreakfast;
  const tomorrowNoBreakfast = groups.tomorrowNoBreakfast;
  const tomorrowWithBreakfast = groups.tomorrowWithBreakfast;

  assert.equal(todayNoBreakfast.sourceLabel, "可信 DOM 房价");
  assert.equal(todayNoBreakfast.title, "今日无早价格趋势");
  assert.equal(todayWithBreakfast.title, "今日含早价格趋势");
  assert.equal(tomorrowNoBreakfast.title, "明日无早价格趋势");
  assert.equal(tomorrowWithBreakfast.title, "明日含早价格趋势");
  assert.deepEqual(todayNoBreakfast.xLabels, ["06-17 08:00", "06-18 08:00", "06-19 08:00"]);
  assert.deepEqual(todayWithBreakfast.xLabels, ["06-17 08:00", "06-18 08:00", "06-19 08:00"]);
  assert.deepEqual(tomorrowNoBreakfast.xLabels, ["06-17 08:00", "06-18 08:00", "06-19 08:00"]);
  assert.deepEqual(tomorrowWithBreakfast.xLabels, ["06-17 08:00", "06-18 08:00", "06-19 08:00"]);
  assert.equal(todayNoBreakfast.axisMin, 380);
  assert.equal(todayNoBreakfast.axisMax, 430);
  assert.equal(todayNoBreakfast.axisInterval, 10);
  assert.equal(todayWithBreakfast.axisMin, 440);
  assert.equal(todayWithBreakfast.axisMax, 520);
  assert.equal(todayWithBreakfast.axisInterval, 20);
  assert.equal(todayNoBreakfast.series.length, 2);
  assert.equal(todayWithBreakfast.series.length, 1);
  assert.equal(tomorrowNoBreakfast.series.length, 1);
  assert.equal(tomorrowWithBreakfast.series.length, 1);
  assert.equal(
    todayNoBreakfast.series[0].label,
    "泉吉云州澜江路步行街高空江景酒店（本店） · 高级大床房 · 今日 · 无早",
  );
  assert.equal(
    todayNoBreakfast.series[1].label,
    "泉吉云州澜江路步行街酒店（竞品A） · 商务大床房 · 今日 · 无早",
  );
  assert.deepEqual(todayNoBreakfast.series[0].points.map((point) => point.price), [416, 394]);
  assert.deepEqual(todayNoBreakfast.series[1].points.map((point) => point.price), [415, 417]);
  assert.deepEqual(todayNoBreakfast.series[1].points.map((point) => point.x), [0, 2]);
  assert.deepEqual(todayWithBreakfast.series[0].points.map((point) => point.price), [481]);
  assert.deepEqual(tomorrowNoBreakfast.series[0].points.map((point) => point.price), [394]);
  assert.deepEqual(tomorrowWithBreakfast.series[0].points.map((point) => point.price), [459]);

  const tooltip = formatTrendTooltip(todayNoBreakfast.series[0], todayNoBreakfast.series[0].points[0]);
  assert.match(tooltip, /泉吉云州澜江路步行街高空江景酒店/);
  assert.match(tooltip, /高级大床房/);
  assert.match(tooltip, /无早/);
  assert.match(tooltip, /¥416/);
  assert.ok(!tooltip.includes("run"));
  assert.ok(!tooltip.includes("room_rate_dom_display_price"));
});

test("四张趋势图共享可信读取时间点，缺少价格的图也更新横坐标", () => {
  const trend: CompetitorPriceTrend = {
    status: "success",
    summary: "ok",
    watch_group_key: "ops_watch",
    granularity: "run",
    days: 7,
    generated_at: "2026-06-19T09:05:00",
    point_count: 3,
    trusted_source: "room_rate_dom_display_price",
    points: [],
    series: [
      {
        series_id: "1:today:2026-06-19:none:高级大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "today",
        checkin_date: "2026-06-19",
        checkout_date: "2026-06-20",
        breakfast_kind: "none",
        room_type: "高级大床房",
        points: [
          {
            bucket_at: "2026-06-19T08:00:00",
            captured_at: "2026-06-19T08:00:00",
            price_amount_yuan: 416,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
          {
            bucket_at: "2026-06-19T09:00:00",
            captured_at: "2026-06-19T09:00:00",
            price_amount_yuan: 420,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
      {
        series_id: "1:tomorrow:2026-06-20:included:高级大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "tomorrow",
        checkin_date: "2026-06-20",
        checkout_date: "2026-06-21",
        breakfast_kind: "included",
        room_type: "高级大床房",
        points: [
          {
            bucket_at: "2026-06-19T08:00:00",
            captured_at: "2026-06-19T08:00:00",
            price_amount_yuan: 459,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
    ],
  };

  const groups = buildCompetitorTrendChartGroups(trend);

  assert.deepEqual(groups.todayNoBreakfast.xLabels, ["06-19 08:00", "06-19 09:00"]);
  assert.deepEqual(groups.tomorrowWithBreakfast.xLabels, ["06-19 08:00", "06-19 09:00"]);
  assert.deepEqual(groups.tomorrowWithBreakfast.series[0].points.map((point) => point.x), [0]);
});

test("同一分钟内多次读取时横坐标显示秒，避免时间点看起来没有更新", () => {
  const trend: CompetitorPriceTrend = {
    status: "success",
    summary: "ok",
    watch_group_key: "ops_watch",
    granularity: "run",
    days: 7,
    generated_at: "2026-06-19T08:05:50",
    point_count: 2,
    trusted_source: "room_rate_dom_display_price",
    points: [],
    series: [
      {
        series_id: "1:today:2026-06-19:none:高级大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "today",
        checkin_date: "2026-06-19",
        checkout_date: "2026-06-20",
        breakfast_kind: "none",
        room_type: "高级大床房",
        points: [
          {
            bucket_at: "2026-06-19T08:05:10",
            captured_at: "2026-06-19T08:05:10",
            price_amount_yuan: 416,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
          {
            bucket_at: "2026-06-19T08:05:45",
            captured_at: "2026-06-19T08:05:45",
            price_amount_yuan: 421,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
    ],
  };

  const groups = buildCompetitorTrendChartGroups(trend);

  assert.deepEqual(groups.todayNoBreakfast.xLabels, ["06-19 08:05:10", "06-19 08:05:45"]);
});

test("同一家酒店换第一款房型时仍是一条趋势线，并在点位保留房型", () => {
  const trend: CompetitorPriceTrend = {
    status: "success",
    summary: "ok",
    watch_group_key: "ops_watch",
    granularity: "run",
    days: 7,
    generated_at: "2026-06-19T08:05:50",
    point_count: 2,
    trusted_source: "room_rate_dom_display_price",
    points: [],
    series: [
      {
        series_id: "1:today:none",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "today",
        checkin_date: "2026-06-19",
        checkout_date: "2026-06-20",
        breakfast_kind: "none",
        room_type: "房型有变化",
        room_types: ["高级大床房", "商务大床房"],
        room_type_changed: true,
        points: [
          {
            bucket_at: "2026-06-19T08:00:00",
            captured_at: "2026-06-19T08:00:00",
            price_amount_yuan: 416,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
          {
            bucket_at: "2026-06-19T10:00:00",
            captured_at: "2026-06-19T10:00:00",
            price_amount_yuan: 421,
            room_type: "商务大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
    ],
  };

  const groups = buildCompetitorTrendChartGroups(trend);
  const series = groups.todayNoBreakfast.series;

  assert.equal(series.length, 1);
  assert.equal(series[0].roomType, "房型有变化");
  assert.equal(series[0].roomTypeChanged, true);
  assert.deepEqual(series[0].roomTypes, ["高级大床房", "商务大床房"]);
  assert.deepEqual(series[0].points.map((point) => point.roomType), ["高级大床房", "商务大床房"]);
  assert.match(formatTrendTooltip(series[0], series[0].points[1]), /房型：商务大床房/);
});

test("旧接口按房型拆开的同酒店趋势也会在前端合并成一条线", () => {
  const trend: CompetitorPriceTrend = {
    status: "success",
    summary: "ok",
    watch_group_key: "ops_watch",
    granularity: "run",
    days: 7,
    generated_at: "2026-06-19T08:05:50",
    point_count: 2,
    trusted_source: "room_rate_dom_display_price",
    points: [],
    series: [
      {
        series_id: "1:today:2026-06-19:none:高级大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "today",
        checkin_date: "2026-06-19",
        checkout_date: "2026-06-20",
        breakfast_kind: "none",
        room_type: "高级大床房",
        points: [
          {
            bucket_at: "2026-06-19T08:00:00",
            captured_at: "2026-06-19T08:00:00",
            price_amount_yuan: 416,
            room_type: "高级大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
      {
        series_id: "1:today:2026-06-20:none:商务大床房",
        hotel_config_id: 1,
        hotel_role: "own",
        sort_order: 0,
        hotel_alias: "本店",
        hotel_name: "泉吉云州澜江路步行街高空江景酒店",
        ctrip_hotel_id: "100001",
        date_label: "today",
        checkin_date: "2026-06-20",
        checkout_date: "2026-06-21",
        breakfast_kind: "none",
        room_type: "商务大床房",
        points: [
          {
            bucket_at: "2026-06-20T08:00:00",
            captured_at: "2026-06-20T08:00:00",
            price_amount_yuan: 421,
            room_type: "商务大床房",
            availability_statuses: ["tight"],
          },
        ],
      },
    ],
  };

  const series = buildCompetitorTrendChartGroups(trend).todayNoBreakfast.series;

  assert.equal(series.length, 1);
  assert.equal(series[0].roomType, "房型有变化");
  assert.deepEqual(series[0].points.map((point) => point.price), [416, 421]);
});
