import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./AccountsPanel.tsx", import.meta.url), "utf8");
const appSource = readFileSync(new URL("../App.tsx", import.meta.url), "utf8");

test("bound Ctrip account rows expose a persistent connection mode editor", () => {
  assert.match(panelSource, /connectionModeDrafts/);
  assert.match(panelSource, /connectionModeDrafts\[session\.browser_session_id\]/);
  assert.match(panelSource, /<option value="unknown">/);
  assert.match(panelSource, /<option value="direct">/);
  assert.match(panelSource, /<option value="non_direct">/);
  assert.match(panelSource, /onPatchSession\(session, \{ connection_mode:/);
  assert.match(panelSource, /保存账号类型/);
});

test("selecting non-direct requires an explicit confirmation (platform-neutral)", () => {
  assert.match(panelSource, /window\.confirm\(/);
  assert.match(panelSource, /非直连账号在通过飞书审批后才允许自动化改价，请确认该账号真实为非直连/);
});

test("Meituan and Fliggy rows expose a selectable account type like Ctrip (new policy)", () => {
  // 新策略：三平台都允许配置改价，只是不默认/不承诺；可写以 can_auto_write_price 真实显示为准。
  assert.ok(
    !/disabled=\{isReadonlyEbkPlatform\(session\.platform\)\}/.test(panelSource),
    "connection_mode must not be locked for meituan/fliggy",
  );
  assert.ok(
    !panelSource.includes("该平台当前仅支持数据读取，不支持自动调价，账号类型固定为未确认"),
    "outdated read-only copy must be removed",
  );
});

test("AccountsPanel PATCH callback accepts connection_mode without changing pricing binding payloads", () => {
  assert.match(panelSource, /connection_mode\?: BrowserConnectionMode/);
  assert.match(appSource, /onPatchSession=\{\(session, input\) =>/);
  assert.match(appSource, /patchBrowserSession\(session\.browser_session_id, input\)/);
  assert.match(panelSource, /pricing_feishu_chat_id/);
});

test("bound accounts render as a shop-first tree (v2 rebuild §3)", () => {
  // 旧「平台×用途」四段视图退役，改为 竞品酒店 → 店铺 → 未分组 的树。
  assert.ok(!panelSource.includes("accountSections"), "old four-section view must be gone");
  assert.match(panelSource, /tree-node-competitor/);
  assert.match(panelSource, /竞品酒店/);
  assert.match(panelSource, /添加竞品账号/);
  assert.match(panelSource, /tree-node-shop/);
  assert.match(panelSource, /监控接入状态/);
  assert.match(panelSource, /tree-node-unassigned/);
  assert.match(panelSource, /归入店铺/);
});

test("shop-first tree is data-driven via buildAccountTree from /shops + session shop_key", () => {
  assert.match(panelSource, /buildAccountTree\(props\.shops, sessions\)/);
  assert.match(panelSource, /props\.shops\.map\(\(shop\) =>/);
  assert.match(panelSource, /onPatchSession\(session, \{ shop_key: shopAssignDraft \}\)/);
});

test("new-account form requires a shop for EBK and pins competitor accounts to the global area", () => {
  assert.match(panelSource, /店铺归属/);
  assert.match(panelSource, /shop_key: event\.target\.value/);
  assert.match(panelSource, /竞品账号（不属店铺）/);
  assert.match(panelSource, /EBK 账号必须归属一个店铺/);
  assert.match(appSource, /onCreateShop=\{\(name\) => void handleCreateShop\(name\)\}/);
  assert.match(appSource, /shop_key:\s*\n?\s*accountForm\.platform === "ctrip" && accountForm\.intended_use === "competitor_price"/);
});
