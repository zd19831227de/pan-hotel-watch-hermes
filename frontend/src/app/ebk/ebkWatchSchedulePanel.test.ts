import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./EbkWatchSchedulePanel.tsx", import.meta.url), "utf8");
const meituanSource = readFileSync(new URL("./MeituanEbkDashboard.tsx", import.meta.url), "utf8");
const fliggySource = readFileSync(new URL("./FliggyEbkDashboard.tsx", import.meta.url), "utf8");
const apiSource = readFileSync(
  new URL("../../services/ebkPlatformWatchApi.ts", import.meta.url),
  "utf8",
);

test("schedule panel covers schedule mode, window, interval and run-now", () => {
  assert.ok(panelSource.includes("schedule_mode") || panelSource.includes("scheduleMode"));
  assert.ok(panelSource.includes("createWatchGroup"));
  assert.ok(panelSource.includes("deleteWatchGroup"));
  assert.ok(panelSource.includes('type="time"'));
  assert.ok(panelSource.includes("间隔（分钟）"));
  assert.ok(panelSource.includes("保存定时配置"));
  assert.ok(panelSource.includes("立即执行"));
  assert.ok(panelSource.includes("自动轮询"));
});

test("schedule panel binds shop directly with own webhook (v2 §2)", () => {
  // v2 §2：监控组直连店铺；投递用本组自己的飞书机器人地址，携程锚点退役。
  assert.ok(panelSource.includes("listShops"));
  assert.ok(panelSource.includes("shop_key"));
  assert.ok(panelSource.includes("归属店铺"));
  assert.ok(panelSource.includes("飞书机器人地址"));
  assert.ok(panelSource.includes("浏览器账号"));
  assert.ok(!panelSource.includes("挂载的携程监控组"));
  assert.ok(!panelSource.includes("listDataCenterGroups"));
  assert.ok(panelSource.includes("不再依赖携程监控组"));
});

test("schedule panel reuses shared polling heartbeat display", () => {
  assert.ok(panelSource.includes("pollingStatusText"));
  assert.ok(panelSource.includes("schedulerDetailText"));
  assert.ok(panelSource.includes("startPollingButtonText"));
  assert.ok(panelSource.includes("stopPollingButtonText"));
});

test("schedule panel surfaces empty and error states without inventing data", () => {
  assert.ok(panelSource.includes("尚未创建"));
  assert.ok(panelSource.includes("读取定时配置失败"));
  assert.ok(panelSource.includes("EmptyState"));
  assert.ok(panelSource.includes("尚未归属店铺"));
});

test("schedule panel exposes no write-price actions", () => {
  assert.doesNotMatch(panelSource, /立即调价|写入携程|自动执行|确认按此价格调价/);
  assert.doesNotMatch(meituanSource, /立即调价|写入携程|自动执行/);
  assert.doesNotMatch(fliggySource, /立即调价|写入携程|自动执行/);
});

test("meituan and fliggy dashboards embed the shared schedule panel", () => {
  assert.ok(meituanSource.includes('<EbkWatchSchedulePanel platform="meituan"'));
  assert.ok(fliggySource.includes('<EbkWatchSchedulePanel platform="fliggy"'));
});

test("platform watch api targets per-platform watch endpoints", () => {
  assert.ok(apiSource.includes("${platform}-ebk-watch"));
  assert.ok(apiSource.includes("/watch-groups"));
  assert.ok(apiSource.includes("/run-now"));
  assert.ok(apiSource.includes("/scheduler-status"));
  assert.ok(apiSource.includes('"PATCH"'));
  assert.ok(apiSource.includes('"POST"'));
  assert.ok(apiSource.includes('"DELETE"'));
  assert.ok(apiSource.includes("createWatchGroup"));
  assert.ok(apiSource.includes("deleteWatchGroup"));
});

test("platform watch api contract carries shop binding and own delivery (v2 §2)", () => {
  // v2 §2：shop_key 直连 + 本组自有投递字段；ctrip_watch_group_key 仅 legacy 可选。
  assert.ok(apiSource.includes("shop_key"));
  assert.ok(apiSource.includes("feishu_webhook_url"));
  assert.ok(apiSource.includes("ctrip_watch_group_key?: string"));
});

test("schedule panel scopes groups and creation to shopKey branch (shop tree §3.1)", () => {
  // 店铺分支传入 shopKey 时：组选择/空态/编辑只看该店；新建锁定本店，防止跨店误建。
  assert.ok(panelSource.includes("shopKey?: string"));
  assert.ok(panelSource.includes("scopedGroups"));
  assert.ok(panelSource.includes("该店铺尚未接入"));
  assert.match(panelSource, /props\.shopKey \|\| newShopKey/);
});

test("schedule panel speaks shop-first onboarding inside a shop branch (shop-direct)", () => {
  // 店铺直连口径：店内用「接入」话术，组标识对操作员隐藏并自动生成唯一键。
  assert.ok(panelSource.includes("接入定时推送"));
  assert.ok(panelSource.includes("defaultConfigName"));
  assert.ok(panelSource.includes("${props.platform}_ebk_${Date.now()}"));
  assert.match(panelSource, /\{!props\.shopKey \? \(\s*<label>监控组标识/);
  assert.ok(!panelSource.includes("该店铺尚未创建"));
});

test("meituan and fliggy dashboards pass shopKey through to schedule panel", () => {
  // 店铺树 §3.1：dashboard 收到 shopKey 时必须透传，店铺分支内定时面板只看本店。
  assert.match(meituanSource, /<EbkWatchSchedulePanel[\s\S]*?shopKey=\{props\.shopKey\}/);
  assert.match(fliggySource, /<EbkWatchSchedulePanel[\s\S]*?shopKey=\{props\.shopKey\}/);
});

test("schedule panel marks auto polling as taken over by global push", () => {
  // 设计 §10.2：旧自动轮询开关标注接管，避免误开双通道
  assert.ok(panelSource.includes("已由「EBK 操作台 · 全局定时推送」接管"));
});

test("meituan and fliggy actions follow the ctrip button hierarchy", () => {
  assert.ok(panelSource.includes('className="primary-button"'));
  assert.ok(panelSource.includes('className="secondary-button"'));
  assert.match(
    panelSource,
    /className="primary-button"[^>]*onClick=\{\(\) => void runNow\(\)\}[\s\S]*?<Play size=\{16\} \/> 立即执行读取/,
  );
  assert.match(
    panelSource,
    /className="secondary-button"[^>]*onClick=\{toggleAutoPoll\}[\s\S]*?group\.auto_poll_enabled \? <X size=\{16\} \/> : <Play size=\{16\} \/>/,
  );
  assert.match(
    panelSource,
    /className="secondary-button"[^>]*onClick=\{\(\) => void saveGroup\(\)\}[\s\S]*?<Save size=\{16\} \/> 保存定时配置/,
  );
});
