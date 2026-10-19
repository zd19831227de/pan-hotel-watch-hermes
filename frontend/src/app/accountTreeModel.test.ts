import assert from "node:assert/strict";
import test from "node:test";

import { buildAccountTree, SHOP_PLATFORM_ORDER } from "./accountTreeModel.ts";
import type { Shop } from "../services/shopsApi.ts";
import type { BrowserSession } from "../services/browserSessionsApi.ts";

const shops: Shop[] = [
  shop("shop-a", "A 店铺", 0),
  shop("shop-b", "B 店铺", 1),
];

test("competitor accounts land in the global competitor area, never under shops", () => {
  const tree = buildAccountTree(shops, [
    ebk("ctrip-ebk", "ctrip", "shop-a"),
    competitor("ctrip-public"),
  ]);
  assert.deepEqual(
    tree.competitorAccounts.map((item) => item.platform_account_id),
    ["ctrip-public"],
  );
  assert.equal(tree.shopNodes[0].platforms.flatMap((p) => p.sessions.map((s) => s.platform_account_id)).length, 1);
});

test("EBK sessions are grouped under their shop by shop_key", () => {
  const tree = buildAccountTree(shops, [
    ebk("ctrip-a", "ctrip", "shop-a"),
    ebk("meituan-b", "meituan", "shop-b"),
    ebk("fliggy-b", "fliggy", "shop-b"),
  ]);
  assert.deepEqual(
    tree.shopNodes.map((node) => node.shop.shop_key),
    ["shop-a", "shop-b"],
  );
  const shopA = tree.shopNodes[0];
  assert.deepEqual(shopA.platforms.map((p) => [p.platform, p.sessions.length]), [
    ["ctrip", 1],
    ["meituan", 0],
    ["fliggy", 0],
  ]);
  const shopB = tree.shopNodes[1];
  assert.deepEqual(shopB.platforms.map((p) => [p.platform, p.sessions.map((s) => s.platform_account_id)]), [
    ["ctrip", []],
    ["meituan", ["meituan-b"]],
    ["fliggy", ["fliggy-b"]],
  ]);
});

test("every shop exposes the three fixed platform slots in canonical order", () => {
  assert.deepEqual(SHOP_PLATFORM_ORDER, ["ctrip", "meituan", "fliggy"]);
  const tree = buildAccountTree(shops, []);
  for (const node of tree.shopNodes) {
    assert.deepEqual(
      node.platforms.map((p) => p.platform),
      SHOP_PLATFORM_ORDER,
    );
  }
});

test("EBK sessions without shop_key fall into unassigned", () => {
  const tree = buildAccountTree(shops, [
    ebk("old-ctrip", "ctrip", ""),
    ebk("old-meituan", "meituan", null),
  ]);
  assert.deepEqual(
    tree.unassignedSessions.map((item) => item.platform_account_id),
    ["old-ctrip", "old-meituan"],
  );
  assert.equal(tree.shopNodes.every((node) => node.platforms.every((p) => p.sessions.length === 0)), true);
});

test("EBK sessions pointing at a missing shop also fall into unassigned", () => {
  const tree = buildAccountTree(shops, [ebk("orphan", "ctrip", "shop-gone")]);
  assert.deepEqual(
    tree.unassignedSessions.map((item) => item.platform_account_id),
    ["orphan"],
  );
});

test("llm_chat sessions are ignored entirely", () => {
  const tree = buildAccountTree(shops, [
    llm("d1", "doubao"),
    competitor("ctrip-public"),
  ]);
  assert.equal(tree.competitorAccounts.length, 1);
  assert.equal(tree.unassignedSessions.length, 0);
  assert.equal(tree.shopNodes.every((node) => node.platforms.every((p) => p.sessions.length === 0)), true);
});

function shop(shop_key: string, name: string, sort_order: number): Shop {
  return { shop_key, name, short_name: null, sort_order };
}

function ebk(platform_account_id: string, platform: string, shopKey: string | null): BrowserSession {
  return session(platform_account_id, platform, "ebk", shopKey);
}

function competitor(platform_account_id: string): BrowserSession {
  return session(platform_account_id, "ctrip", "competitor_price", null);
}

function llm(platform_account_id: string, platform: string): BrowserSession {
  return session(platform_account_id, platform, "llm_chat", null);
}

function session(
  platform_account_id: string,
  platform: string,
  intended_use: BrowserSession["intended_use"],
  shopKey: string | null,
): BrowserSession {
  return {
    browser_session_id: `browser-${platform_account_id}`,
    platform_account_id,
    display_name: platform_account_id,
    platform,
    session_port: 9221,
    intended_use,
    connection_mode: "unknown",
    can_auto_write_price: false,
    shop_key: shopKey,
    login_state: "logged_out",
    human_verification_status: "required",
    last_heartbeat_at: "2026-09-29T00:00:00+08:00",
    last_action: "Test session.",
    risk_flags: [],
  };
}
