import assert from "node:assert/strict";
import test from "node:test";

import {
  competitorGroupPatchFromForm,
  groupFormFromWatchGroup,
  hotelFormFromHotel,
  nextGroupKeyAfterDelete,
  resetHotelFormForGroup,
} from "./competitorWatchForms.ts";
import type { BrowserSession } from "../services/browserSessionsApi.ts";
import type { CompetitorHotel, CompetitorWatchGroup } from "../services/competitorWatchApi.ts";

test("监控组表单切换时使用当前组配置生成草稿并忽略旧桌面表格布局", () => {
  const group = {
    id: 1,
    group_key: "watch_a",
    name: "澜江路泉吉酒店",
    brief_title: "澜江路泉吉酒店竞对盯盘简报",
    platform_account_id: "ctrip-public-1",
    browser_session_id: "browser-ctrp-1",
    browser_rotation_mode: "fixed",
    pool_member_browser_session_ids: [],
    pool_failover_enabled: true,
    pool_cooldown_minutes: 60,
    pricing_browser_session_id: "browser-ebk-1",
    pricing_account_configured: true,
    pricing_connection_mode: "non_direct",
    pricing_can_auto_write_price: true,
    query_interval_minutes: 120,
    schedule_mode: "fixed_slots",
    schedule_start_time: "06:00",
    schedule_end_time: "01:00",
    schedule_interval_minutes: 90,
    feishu_webhook_configured: false,
    feishu_card_layout: "desktop_table",
    push_date_scope: "today_only",
    retention_days: 30,
    enabled: true,
    auto_poll_enabled: false,
  } as CompetitorWatchGroup;

  assert.deepEqual(groupFormFromWatchGroup(group), {
    name: "澜江路泉吉酒店",
    brief_title: "澜江路泉吉酒店竞对盯盘简报",
    browser_session_id: "browser-ctrp-1",
    browser_rotation_mode: "fixed",
    pool_member_browser_session_ids: [],
    pool_failover_enabled: true,
    pool_cooldown_minutes: "60",
    pricing_browser_session_id: "browser-ebk-1",
    schedule_mode: "fixed_slots",
    schedule_start_time: "06:00",
    schedule_end_time: "01:00",
    schedule_interval_minutes: "90",
    feishu_card_layout: "mobile_blocks",
    push_date_scope: "today_only",
    feishu_chat_id: "",
    enabled: true,
    auto_poll_enabled: false,
  });
});

test("酒店编辑时回填当前酒店配置，切换监控组时重置新增表单", () => {
  const hotel = {
    role: "own",
    alias: "本店",
    short_alias: "本店",
    detail_url: "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
    sort_order: 0,
    enabled: true,
  } as CompetitorHotel;

  assert.deepEqual(hotelFormFromHotel(hotel), {
    role: "own",
    alias: "本店",
    short_alias: "本店",
    detail_url: "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
    sort_order: "0",
    enabled: true,
  });
  assert.deepEqual(resetHotelFormForGroup(), {
    role: "competitor",
    alias: "",
    short_alias: "",
    detail_url: "",
    sort_order: "10",
    enabled: true,
  });
});

test("保存监控组时只绑定已选择的携程官网浏览器账号，空选择会清除绑定", () => {
  const sessions = [
    {
      browser_session_id: "browser-competitor-1",
      platform_account_id: "ctrip-public-1",
      display_name: "携程官网号",
      intended_use: "competitor_price",
    },
    {
      browser_session_id: "browser-ebk-1",
      platform_account_id: "ctrip-ebk-1",
      display_name: "EBK pricing account",
      intended_use: "ebk",
      connection_mode: "non_direct",
    },
  ] as BrowserSession[];

  const patch = competitorGroupPatchFromForm(
    {
      name: "澜江路泉吉",
      brief_title: "澜江路泉吉酒店竞对盯盘简报",
      browser_session_id: "browser-competitor-1",
      browser_rotation_mode: "fixed",
      pool_member_browser_session_ids: [],
      pool_failover_enabled: true,
      pool_cooldown_minutes: "60",
      pricing_browser_session_id: "browser-ebk-1",
      schedule_mode: "fixed_slots",
      schedule_start_time: "06:00",
      schedule_end_time: "01:00",
      schedule_interval_minutes: "120",
      feishu_card_layout: "mobile_blocks",
      push_date_scope: "today_only",
      feishu_chat_id: "oc_group",
      enabled: true,
      auto_poll_enabled: true,
    },
    sessions,
  );

  assert.equal(patch.browser_session_id, "browser-competitor-1");
  assert.equal(patch.platform_account_id, "ctrip-public-1");
  assert.equal(patch.clear_binding, false);
  assert.equal(patch.pricing_browser_session_id, "browser-ebk-1");
  assert.equal(patch.pricing_platform_account_id, "ctrip-ebk-1");
  assert.equal(patch.clear_pricing_binding, false);
  assert.equal(patch.query_interval_minutes, 120);
  assert.equal(patch.schedule_interval_minutes, 120);
  assert.equal(patch.push_date_scope, "today_only");
  assert.equal(patch.feishu_chat_id, "oc_group");

  const clearPatch = competitorGroupPatchFromForm(
    {
      ...groupFormFromWatchGroup(null),
      browser_session_id: "",
      pricing_browser_session_id: "",
    },
    sessions,
  );
  assert.equal(clearPatch.browser_session_id, undefined);
  assert.equal(clearPatch.platform_account_id, undefined);
  assert.equal(clearPatch.clear_binding, true);
  assert.equal(clearPatch.pricing_browser_session_id, undefined);
  assert.equal(clearPatch.pricing_platform_account_id, undefined);
  assert.equal(clearPatch.clear_pricing_binding, true);

  const unknownBindingPatch = competitorGroupPatchFromForm(
    {
      ...groupFormFromWatchGroup(null),
      browser_session_id: "browser-not-in-list",
      pricing_browser_session_id: "browser-not-in-list",
    },
    sessions,
  );
  assert.equal(unknownBindingPatch.browser_session_id, undefined);
  assert.equal(unknownBindingPatch.platform_account_id, undefined);
  assert.equal(unknownBindingPatch.clear_binding, false);
  assert.equal(unknownBindingPatch.pricing_browser_session_id, undefined);
  assert.equal(unknownBindingPatch.pricing_platform_account_id, undefined);
  assert.equal(unknownBindingPatch.clear_pricing_binding, false);
});

test("删除监控组后选择相邻监控组，删空时清空选择", () => {
  const groups = [
    { group_key: "watch_a" },
    { group_key: "watch_b" },
    { group_key: "watch_c" },
  ] as CompetitorWatchGroup[];

  assert.equal(nextGroupKeyAfterDelete(groups, "watch_b"), "watch_c");
  assert.equal(nextGroupKeyAfterDelete(groups, "watch_c"), "watch_b");
  assert.equal(nextGroupKeyAfterDelete([{ group_key: "watch_a" }] as CompetitorWatchGroup[], "watch_a"), "");
});

test("competitor watch group form preserves browser pool settings from backend", () => {
  const form = groupFormFromWatchGroup({
    id: 2,
    group_key: "watch_pool",
    name: "pool watch",
    brief_title: "pool brief",
    platform_account_id: "trip-a",
    browser_rotation_mode: "pool_round_robin",
    pool_failover_enabled: false,
    pool_cooldown_minutes: 45,
    browser_pool: {
      pool_enabled: true,
      pool_size: 2,
      usable_pool_size: 1,
      members: [
        {
          browser_session_id: "browser-trip-a",
          platform_account_id: "trip-a",
          display_name: "Trip A",
          enabled: true,
          sort_order: 0,
          weight: 1,
        },
        {
          browser_session_id: "browser-trip-b",
          platform_account_id: "trip-b",
          display_name: "Trip B",
          enabled: false,
          sort_order: 1,
          weight: 1,
        },
      ],
    },
    feishu_webhook_configured: false,
    retention_days: 30,
    query_interval_minutes: 120,
    enabled: true,
    auto_poll_enabled: false,
  } as CompetitorWatchGroup);

  assert.equal(form.browser_rotation_mode, "pool_round_robin");
  assert.equal(form.pool_failover_enabled, false);
  assert.equal(form.pool_cooldown_minutes, "45");
  assert.deepEqual(form.pool_member_browser_session_ids, ["browser-trip-a"]);
});

test("competitor browser pool form submits only competitor-price accounts and keeps EBK pricing binding separate", () => {
  const sessions = [
    {
      browser_session_id: "browser-trip-a",
      platform_account_id: "trip-a",
      display_name: "Trip A",
      intended_use: "competitor_price",
    },
    {
      browser_session_id: "browser-ebk-a",
      platform_account_id: "ebk-a",
      display_name: "EBK A",
      intended_use: "ebk",
    },
  ] as BrowserSession[];

  const patch = competitorGroupPatchFromForm(
    {
      name: "pool watch",
      brief_title: "pool brief",
      browser_session_id: "",
      pricing_browser_session_id: "browser-ebk-a",
      browser_rotation_mode: "pool_round_robin",
      pool_member_browser_session_ids: ["browser-trip-a", "browser-ebk-a"],
      pool_failover_enabled: true,
      pool_cooldown_minutes: "30",
      schedule_mode: "fixed_slots",
      schedule_start_time: "06:00",
      schedule_end_time: "01:00",
      schedule_interval_minutes: "60",
      feishu_card_layout: "mobile_blocks",
      push_date_scope: "today_tomorrow",
      feishu_chat_id: "",
      enabled: true,
      auto_poll_enabled: true,
    },
    sessions,
  );

  assert.equal(patch.browser_rotation_mode, "pool_round_robin");
  assert.equal(patch.pricing_browser_session_id, "browser-ebk-a");
  assert.equal(patch.pricing_platform_account_id, "ebk-a");
  assert.deepEqual(patch.pool_member_browser_session_ids, ["browser-trip-a"]);
});
