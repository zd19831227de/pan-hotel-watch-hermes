import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildFliggyEbkDashboard } from "./fliggyEbkDashboardModel.ts";

const dashboardSource = readFileSync(new URL("./FliggyEbkDashboard.tsx", import.meta.url), "utf8");
const appSource = readFileSync(new URL("../App.tsx", import.meta.url), "utf8");

test("fliggy dashboard model maps four operator sections from snapshot", () => {
  const view = buildFliggyEbkDashboard({
    platform: "fliggy",
    platform_account_id: "fliggy_1",
    status: "success",
    payload_json: {
      platform: "fliggy",
      platform_account_id: "fliggy_1",
      read_at: "2026-07-15T08:00:00+08:00",
      status: "success",
      summary: "飞猪 EBK 已读出",
      datasets: {
        home_v1: {
          status: "success",
          data: {
            today_data: { 销售额: "1280" },
            yesterday_data: { 订单数: "4" },
          },
        },
        diagnose_report: {
          status: "success",
          data: {
            trade_overview: { 销售额: "2780" },
            guest_review_score: "4.8",
            mci_index: "86",
          },
        },
        operating_data: {
          status: "success",
          data: {
            in_house_data: { 在店间夜: "12" },
            booking_data: { 预订单数: "8" },
          },
        },
        promotion_agent: {
          status: "success",
          data: {
            joined_activities: [{ name: "暑期特惠", status: "进行中" }],
          },
        },
      },
    },
  });

  assert.equal(view.home.today[0]?.value, "1280");
  assert.equal(view.home.yesterday[0]?.value, "4");
  assert.equal(view.diagnose.guestReviewScore, "4.8");
  assert.equal(view.diagnose.mciIndex, "86");
  assert.equal(view.operating.inHouse[0]?.value, "12");
  assert.equal(view.promotion.activities[0]?.name, "暑期特惠");
  assert.equal(view.successCount, 4);
});

test("fliggy dashboard exposes read-and-save without write-price actions", () => {
  assert.ok(dashboardSource.includes("读取并保存"));
  assert.ok(dashboardSource.includes("onReadAndSave"));
  assert.doesNotMatch(dashboardSource, /立即调价|写入携程|自动执行|确认按此价格调价/);
});

test("app wires fliggy read-and-save to ebk snapshots read-all", () => {
  assert.ok(appSource.includes("fliggyPlatformAccountId"));
  assert.ok(appSource.includes('platforms: ["fliggy"]'));
  assert.ok(appSource.includes("handleFliggyReadAndSave"));
  assert.ok(appSource.includes("pushToFeishu: true"));
  assert.ok(appSource.includes("feishuWatchGroupKey: dataCenterGroup?.group_key"));
});
