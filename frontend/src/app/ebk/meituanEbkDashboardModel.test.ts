import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMeituanEbkDashboard,
  type MeituanSnapshotLike,
} from "./meituanEbkDashboardModel.ts";

test("meituan dashboard model maps four operator sections from snapshot", () => {
  const snapshot: MeituanSnapshotLike = {
    platform: "meituan",
    platform_account_id: "meituan_account_01",
    status: "success",
    created_at: "2026-07-10T09:35:00",
    payload_json: {
      platform: "meituan",
      platform_account_id: "meituan_account_01",
      read_at: "2026-07-10T09:35:00+08:00",
      status: "success",
      summary: "美团 EBK 已读出",
      datasets: {
        workbench_home: {
          status: "success",
          data: {
            realtime_data: { 支付订单数: { value: "3" } },
            yesterday_data: { 支付订单数: { value: "7" } },
            hos_score: {
              value: "4.56",
              grade_tip: "再接再厉",
              message: "HOS分与昨日持平！继续提分，冲刺金冠权益，提转化涨订单",
            },
          },
        },
        data_center_home: {
          status: "success",
          data: {
            basic_operation_metrics: {
              销售额: { value: "2780.00 元" },
            },
          },
        },
        data_center_trade: {
          status: "success",
          data: {
            tab_label: "交易分析",
            business_performance: {
              销售额: { value: "2780.00 元" },
              支付订单数: { value: "7单" },
            },
            metrics: {
              满房率: { value: "56.92%" },
            },
          },
        },
        data_center_traffic: {
          status: "success",
          data: {
            tab_label: "流量分析",
            traffic_trend: {
              曝光量: { value: "1542" },
              浏览人数: { value: "270" },
            },
            loss_analysis: {
              流失订单量: { value: "3" },
              流失间夜量: { value: "5" },
            },
            metrics: {
              流失订单金额: { value: "228.15 元" },
            },
          },
        },
        data_center_service: {
          status: "success",
          data: {
            tab_label: "服务分析",
            service_metrics: {
              服务分: { value: "4.9" },
              评价分: { value: "4.8" },
              信息分: { value: "4.8" },
            },
          },
        },
        promotion_coupon: {
          status: "success",
          data: {
            coupon_activities: [
              {
                activity_name: "美团神券",
                status: "报名成功",
                activity_metrics: { 曝光量: { value: "120" } },
              },
            ],
          },
        },
        comment_manage_home: {
          status: "success",
          data: {
            review_score: "4.7",
            review_total: "128",
            review_status: {
              好评率: "96%",
              待回复评价: "2",
            },
            review_dimensions: {
              服务: "4.8",
            },
            review_periods: {
              "30天": {
                新增有效评价数: "33条",
                差评率: "6.1%",
              },
              "180天": {
                差评率表现: "中等",
              },
            },
            pending_reply_count: "2",
          },
        },
      },
    },
  };

  const view = buildMeituanEbkDashboard(snapshot);
  assert.equal(view.status, "success");
  assert.equal(view.readAt, "2026-07-10T09:35:00+08:00");
  assert.equal(view.workbench.available, true);
  assert.ok(view.workbench.realtime.some((m) => m.label === "支付订单数" && m.value === "3"));
  assert.ok(view.workbench.yesterday.some((m) => m.label === "支付订单数" && m.value === "7"));
  assert.equal(view.workbench.hosScore, "4.56");
  assert.ok(view.dataCenter.metrics.some((m) => m.label === "销售额" && m.value === "2780.00 元"));
  assert.equal(view.dataCenter.tabs.length, 4);
  assert.equal(view.dataCenter.successCount, 4);
  assert.equal(view.dataCenter.tabs[1]?.label, "交易分析");
  assert.equal(view.dataCenter.tabs[2]?.label, "流量分析");
  assert.equal(view.dataCenter.tabs[3]?.label, "服务分析");
  assert.ok(view.dataCenter.tabs[1]?.metrics.some((m) => m.label === "销售额" && m.value === "2780.00 元"));
  assert.equal(view.promotion.activities[0]?.name, "美团神券");
  assert.equal(view.reviews.score, "4.7");
  assert.equal(view.reviews.pendingReplyCount, "2");
  assert.ok(view.reviews.situation.some((m) => m.label === "好评率" && m.value === "96%"));
  assert.ok(view.reviews.situation.some((m) => m.label === "30天·新增有效评价数" && m.value === "33条"));
  assert.ok(view.reviews.situation.some((m) => m.label === "30天·差评率" && m.value === "6.1%"));
});

test("meituan dashboard model returns empty sections when snapshot missing", () => {
  const view = buildMeituanEbkDashboard(null);
  assert.equal(view.status, "missing");
  assert.equal(view.workbench.available, false);
  assert.equal(view.dataCenter.available, false);
  assert.equal(view.dataCenter.tabs.length, 4);
  assert.equal(view.dataCenter.successCount, 0);
  assert.equal(view.promotion.available, false);
  assert.equal(view.reviews.available, false);
  assert.equal(view.reviews.situation.length, 0);
  assert.ok(view.emptyText.includes("暂无读取"));
});
