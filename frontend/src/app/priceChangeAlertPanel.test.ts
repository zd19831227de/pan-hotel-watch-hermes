import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./PriceChangeAlertPanel.tsx", import.meta.url), "utf8");
const modelSource = readFileSync(new URL("./priceChangeAlertModel.ts", import.meta.url), "utf8");
const serviceSource = readFileSync(new URL("../services/priceChangeAlertsApi.ts", import.meta.url), "utf8");
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const accountPlatformModelSource = readFileSync(new URL("./accountPlatformModel.ts", import.meta.url), "utf8");
const browserSessionServiceSource = readFileSync(new URL("../services/browserSessionsApi.ts", import.meta.url), "utf8");

test("price-change alert service uses suggestion-only endpoints", () => {
  assert.match(serviceSource, /\/api\/v1\/pricing-strategy\/config/);
  assert.match(serviceSource, /\/api\/v1\/price-change-alerts\/evaluate/);
  assert.match(serviceSource, /\/api\/v1\/price-change-alerts\/latest/);
  assert.match(serviceSource, /push_to_feishu/);
});

test("price-change panel shows operator-facing suggestion fields and trusted source", () => {
  assert.match(panelSource, /当前状态/);
  assert.match(panelSource, /超过阈值/);
  assert.match(panelSource, /建议本店降价/);
  assert.match(panelSource, /建议后价格/);
  assert.match(panelSource, /人工关注/);
  assert.match(panelSource, /建议降价按多少元取整/);
  assert.match(panelSource, /暂停自动给出降价金额/);
  assert.match(panelSource, /今日入住/);
  assert.match(panelSource, /明日入住/);
  assert.match(panelSource, /仅提醒竞品涨价，不建议本店涨价/);
  assert.ok(!panelSource.includes("room_rate_dom_display_price"));
  assert.ok(!panelSource.includes("DOM 房价"));
  assert.match(panelSource, /仅生成调价建议，不会自动改价/);
  assert.match(serviceSource, /connection_mode/);
  assert.match(serviceSource, /can_auto_write_price/);
  assert.match(serviceSource, /write_policy_message/);
});

test("price-change panel treats change-detected alerts as threshold changes without approval copy", () => {
  assert.match(panelSource, /hasThresholdChange/);
  assert.match(panelSource, /changed_count \?\? 0/);
  assert.match(modelSource, /change_detected/);
  assert.match(panelSource, /价格变化已触发/);
  assert.match(panelSource, /changeItemFromAlert/);
  assert.match(panelSource, /suggestion \?\? changeItemFromAlert/);
});

test("price-change evaluate and save actions are grouped together", () => {
  const actionBlockStart = panelSource.indexOf('className="price-alert-actions"');
  assert.ok(actionBlockStart > 0);
  const actionBlock = panelSource.slice(actionBlockStart);
  assert.ok(actionBlock.indexOf("检测价格变化") > 0);
  assert.ok(actionBlock.indexOf("保存提醒规则") > actionBlock.indexOf("检测价格变化"));
  assert.ok(actionBlock.includes("停止提醒"));
  assert.ok(actionBlock.includes("恢复提醒"));
  assert.match(panelSource, /onToggleEnabled/);
  assert.match(appSource, /handleTogglePriceChangeStrategy/);
  assert.match(appSource, /enabled: nextEnabled/);
});

test("price-change alert is a top-level operations tab", () => {
  assert.match(
    appSource,
    /type TabKey = "accounts" \| "ebk_ops" \| "competitor" \| "calendar_prices" \| "price_alerts"/,
  );
  assert.match(appSource, /label="价格变化提醒"/);
  assert.match(appSource, /function PriceChangePanel/);
  assert.match(appSource, /价格变化提醒推送标题/);
  assert.match(appSource, /用于区分不同监控组的价格变化推送/);
  assert.match(appSource, /onSaveAlertTitle/);
  assert.match(appSource, /竞对降价超过阈值时生成本店降价建议/);
  assert.match(appSource, /竞对涨价只提醒价格变化/);
  assert.match(appSource, /非直连账号才会继续发送审批卡/);
  assert.match(appSource, /只比较当前成功读取和上一轮成功读取/);
  assert.match(appSource, /最近可信房价基础/);
});

test("competitor price panel no longer owns the price-change alert panel", () => {
  const competitorPanelStart = appSource.indexOf("function CompetitorPanel");
  const priceChangePanelStart = appSource.indexOf("function PriceChangePanel");
  assert.ok(competitorPanelStart > 0);
  assert.ok(priceChangePanelStart > competitorPanelStart);
  const competitorPanelSource = appSource.slice(competitorPanelStart, priceChangePanelStart);
  assert.ok(!competitorPanelSource.includes("PriceChangeAlertPanel"));
});

test("price-change panel does not expose approval or OTA write actions", () => {
  for (const forbidden of ["通过建议", "立即执行调价", "自动执行", "写入携程", "提交到 OTA", "dry_run", "execute"]) {
    assert.ok(!panelSource.includes(forbidden), forbidden);
  }
});

test("account binding exposes connection mode without defaulting to non-direct", () => {
  const accountsSource = readFileSync(new URL("./tabs/AccountsPanel.tsx", import.meta.url), "utf8");
  assert.match(browserSessionServiceSource, /BrowserConnectionMode/);
  assert.match(accountsSource, /账号类型/);
  assert.match(accountsSource, /直连账号：只生成调价建议，不允许自动改价/);
  assert.match(accountsSource, /非直连账号：经 Feishu 审批后可继续调价流程/);
  assert.match(accountsSource, /允许审批后写价能力：/);
  assert.match(accountPlatformModelSource, /connection_mode: "unknown"/);
  assert.ok(!accountPlatformModelSource.includes('connection_mode: "non_direct"'));
});
