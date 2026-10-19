import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(
  new URL("./tabs/AccountsPanel.tsx", import.meta.url),
  "utf8",
);
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const accountPlatformModelSource = readFileSync(
  new URL("./accountPlatformModel.ts", import.meta.url),
  "utf8",
);
const browserSessionServiceSource = readFileSync(
  new URL("../services/browserSessionsApi.ts", import.meta.url),
  "utf8",
);

test("created-account rows expose a connection_mode editor that patches the session", () => {
  assert.match(panelSource, /aria-label=\{`\$\{session\.display_name\} 账号类型`\}/);
  assert.match(panelSource, /connectionModeDrafts\[session\.browser_session_id\] \?\? session\.connection_mode/);
  assert.match(panelSource, /props\.onPatchSession\(session, \{ connection_mode: connectionMode \}\)/);
  assert.match(panelSource, /onPatchSession: \(/);
  assert.match(panelSource, /connection_mode\?: BrowserConnectionMode/);
});

test("switching to non_direct requires operator confirmation (platform-neutral)", () => {
  assert.match(panelSource, /window\.confirm\("非直连账号在通过飞书审批后才允许自动化改价，请确认该账号真实为非直连"\)/);
});

test("meituan/fliggy connection_mode is selectable like ctrip (policy: all platforms may price)", () => {
  // 新策略：三平台都允许配置改价，只是不默认/不承诺；可写以 can_auto_write_price 真实显示为准。
  assert.ok(
    !/disabled=\{isReadonlyEbkPlatform\(session\.platform\)\}/.test(panelSource),
    "connection_mode must not be locked for meituan/fliggy",
  );
  assert.match(panelSource, /<option value="direct">直连<\/option>/);
  assert.match(panelSource, /<option value="non_direct">非直连<\/option>/);
  assert.ok(
    !panelSource.includes("该平台当前仅支持数据读取，不支持自动调价，账号类型固定为未确认。"),
    "outdated 'data-read-only' copy must be removed",
  );
});

test("accounts panel is business-only: no llm_chat account creation or login", () => {
  // 修正1：豆包/千问账号移入知识模块，业务面板不再创建/登录 AI 对话账号
  assert.ok(!panelSource.includes('<option value="doubao">'), "doubao must not be creatable here");
  assert.ok(!panelSource.includes('<option value="qwen">'), "qwen must not be creatable here");
  assert.ok(!panelSource.includes('value="llm_chat"'), "llm_chat intended_use must not be offered here");
  assert.ok(!panelSource.includes("onOpenLlmChatLogin"), "llm chat login handlers must not live here");
  assert.ok(!panelSource.includes("chat_url"), "chat_url field must not live here");
  assert.match(panelSource, /isLlmChatPlatform\(.*\.platform\)/);
});

test("App passes the patch input through to the browser-session PATCH api", () => {
  assert.match(appSource, /patchBrowserSession\(session\.browser_session_id, input\)/);
  assert.match(browserSessionServiceSource, /method: "PATCH"/);
});

test("account creation defaults never become non_direct", () => {
  assert.match(accountPlatformModelSource, /connection_mode: "unknown"/);
  assert.ok(
    !accountPlatformModelSource.includes('connection_mode: "non_direct"'),
    "account platform defaults must never be non_direct",
  );
});
