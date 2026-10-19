import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCtripBusinessData,
  type BusinessLatestSnapshot,
} from "./ctripBusinessDataModel.ts";

function latestWithMetrics(): BusinessLatestSnapshot {
  return {
    run: { id: 88, status: "success", captured_at: "2026-09-10T08:10:00+08:00" },
    dataset_results: [
      {
        dataset_id: "business_outline",
        status: "success",
        captured_at: "2026-09-10T08:10:00+08:00",
        data: {
          updated_at: "刚刚更新",
          time_dimensions: {
            realtime: {
              status: "success",
              metrics: {
                realtime_booking_orders: { label: "实时预订订单", value: "12" },
                realtime_in_house_room_nights: { label: "实时在店间夜", value: "25" },
                realtime_visitors: { label: "实时访客", value: "130" },
              },
            },
          },
        },
      },
      {
        dataset_id: "competition_profile",
        status: "success",
        captured_at: "2026-09-10T08:10:00+08:00",
        data: {
          time_dimensions: {
            realtime: {
              status: "success",
              metrics: {
                booking_sales: { label: "预订销售额", value: "¥8,600", competitive_rank: "3/12" },
                booking_orders: { label: "预订订单量", value: "18", competitive_rank: "4/12" },
                in_house_room_nights: { label: "在店间夜", value: "26", competitive_rank: "2/12" },
                occupancy_rate: { label: "满房率", value: "72%", competitive_rank: "5/12" },
                ctrip_app_visitors: { label: "携程App访客", value: "310", competitive_rank: "6/12" },
                ctrip_app_conversion_rate: { label: "携程App转化率", value: "3.2%", competitive_rank: "7/12" },
              },
            },
          },
        },
      },
      {
        dataset_id: "flow_data",
        status: "success",
        captured_at: "2026-09-10T08:10:00+08:00",
        data: {
          metrics: {
            realtime_visitors: { label: "实时访客量", value: "135" },
            realtime_visitor_rank: { label: "实时排名", value: "4" },
            realtime_starting_price: { label: "实时起价", value: "¥328" },
          },
        },
      },
    ],
  };
}

test("经营数据面板：收益/经营对比/流量曝光三组指标与全局推送同口径", () => {
  const data = buildCtripBusinessData(latestWithMetrics());
  assert.equal(data.status, "ready");
  assert.equal(data.readTime, "2026-09-10T08:10:00+08:00");
  assert.deepEqual(
    data.revenueOverview.map((metric) => `${metric.label}=${metric.value}`),
    ["预订销售额=¥8,600", "实时预订订单=12", "实时在店间夜=25"],
  );
  assert.equal(data.businessComparison.length, 6);
  const bookingSales = data.businessComparison.find((metric) => metric.key === "booking_sales");
  assert.equal(bookingSales?.rank, "3/12");
  // 流量曝光以 flow_data 为准（135 覆盖 business_outline 的 130），App 流量来自竞争圈。
  const visitors = data.trafficExposure.find((metric) => metric.key === "realtime_visitors");
  assert.equal(visitors?.value, "135");
  const appVisitors = data.trafficExposure.find((metric) => metric.key === "ctrip_app_visitors");
  assert.equal(appVisitors?.value, "310");
});

test("经营数据面板：读取完成但有数据集异常时给出异常汇总（状态可见不静默）", () => {
  const latest = latestWithMetrics();
  latest.dataset_results!.push(
    { dataset_id: "marketing", status: "page_structure_changed", captured_at: "2026-09-10T08:10:00+08:00" },
    { dataset_id: "review_monitor", status: "human_required", captured_at: null },
  );

  const data = buildCtripBusinessData(latest);
  assert.equal(data.status, "ready");
  assert.equal(data.anomalyCount, 2);
  assert.deepEqual(
    data.anomalies.map((item) => `${item.datasetId}:${item.status}`),
    ["marketing:page_structure_changed", "review_monitor:human_required"],
  );
});

test("经营数据面板：全部数据集成功时异常汇总为空", () => {
  const data = buildCtripBusinessData(latestWithMetrics());
  assert.equal(data.anomalyCount, 0);
  assert.deepEqual(data.anomalies, []);
});

test("经营数据面板：没有任何指标时给出明确空态提示", () => {
  const data = buildCtripBusinessData({ run: null, dataset_results: [] });
  assert.equal(data.status, "data_required");
  assert.match(data.emptyText ?? "", /经营数据/);
});

test("经营数据面板：realtime 维度未成功时不会把指标当实时值展示", () => {
  const latest: BusinessLatestSnapshot = {
    dataset_results: [
      {
        dataset_id: "business_outline",
        status: "success",
        data: {
          time_dimensions: {
            realtime: {
              status: "structure_changed",
              metrics: { realtime_booking_orders: { label: "实时预订订单", value: "99" } },
            },
          },
        },
      },
    ],
  };
  const data = buildCtripBusinessData(latest);
  assert.equal(data.status, "data_required");
});
