import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const llmChatApiSource = readFileSync(
  new URL("../services/llmChatApi.ts", import.meta.url),
  "utf8",
);
const distillApiSource = readFileSync(
  new URL("../services/distillApi.ts", import.meta.url),
  "utf8",
);
const accountPlatformModelSource = readFileSync(
  new URL("./accountPlatformModel.ts", import.meta.url),
  "utf8",
);
const distillPanelSource = readFileSync(
  new URL("./DistillPanel.tsx", import.meta.url),
  "utf8",
);
const browserSessionServiceSource = readFileSync(
  new URL("../services/browserSessionsApi.ts", import.meta.url),
  "utf8",
);
const accountsPanelSource = readFileSync(
  new URL("./tabs/AccountsPanel.tsx", import.meta.url),
  "utf8",
);
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const distillPanelPath = new URL("./DistillPanel.tsx", import.meta.url);

test("llmChatApi wraps doubao/qwen chat endpoints", () => {
  assert.match(llmChatApiSource, /\$\{platform\}-chat/);
  assert.match(llmChatApiSource, /open-login/);
  assert.match(llmChatApiSource, /check-login-state/);
  assert.match(llmChatApiSource, /ask-and-extract/);
  assert.match(llmChatApiSource, /"doubao" \| "qwen"/);
});

test("distillApi covers all /distill endpoints", () => {
  for (const fn of [
    "fetchDistillSources",
    "createDistillSource",
    "patchDistillSource",
    "deleteDistillSource",
    "triggerDistillRun",
    "triggerSourceRun",
    "fetchDistillRuns",
    "fetchDistillMeta",
    "exportDistillRun",
  ]) {
    assert.ok(distillApiSource.includes(`export async function ${fn}`), fn);
  }
  assert.match(distillApiSource, /\/api\/v1\/distill\/sources/);
  assert.match(distillApiSource, /\/api\/v1\/distill\/runs/);
  assert.match(distillApiSource, /\/api\/v1\/distill\/meta/);
});

test("account platform model supports doubao/qwen llm_chat accounts", () => {
  assert.match(accountPlatformModelSource, /"ctrip" \| "meituan" \| "fliggy" \| "doubao" \| "qwen"/);
  assert.match(accountPlatformModelSource, /export function isLlmChatPlatform/);
  assert.match(accountPlatformModelSource, /intended_use: "llm_chat"/);
  assert.match(distillPanelSource, /AI 对话账号/);
  assert.match(browserSessionServiceSource, /"ebk" \| "competitor_price" \| "llm_chat"/);
});

test("accounts panel no longer hosts llm_chat account creation (moved to distill panel)", () => {
  // 修正1：豆包/千问账号移入知识模块，业务面板不再创建/登录 AI 对话账号
  assert.ok(!accountsPanelSource.includes('<option value="doubao">'), "doubao must not be in accounts panel");
  assert.ok(!accountsPanelSource.includes('<option value="qwen">'), "qwen must not be in accounts panel");
  assert.ok(!accountsPanelSource.includes('value="llm_chat"'), "llm_chat must not be in accounts panel");
  assert.ok(!accountsPanelSource.includes("chat_url"), "chat_url must not be in accounts panel");
  assert.ok(!accountsPanelSource.includes("onOpenLlmChatLogin"), "llm handlers must not be in accounts panel");
});

test("distill panel creates llm_chat accounts with chat url inside knowledge module", () => {
  const panelSource = readFileSync(distillPanelPath, "utf8");
  assert.match(panelSource, /intended_use: "llm_chat"/);
  assert.match(panelSource, /chat_url/);
  assert.match(panelSource, /createBrowserSession/);
});

test("distill panel tab exists with required blocks", () => {
  assert.ok(existsSync(distillPanelPath), "DistillPanel.tsx must exist");
  const panelSource = readFileSync(distillPanelPath, "utf8");
  for (const marker of [
    "数据源",
    "一次性蒸馏",
    "运行记录",
    "准入规则",
    "human_required",
    "知识管理",
    "对话账号",
    "导出 JSON",
  ]) {
    assert.ok(panelSource.includes(marker), marker);
  }
  assert.match(panelSource, /triggerSourceRun/);
  assert.match(panelSource, /fetchDistillMeta/);
});

test("distill source form defaults to 120 minutes with minimum hint", () => {
  const panelSource = readFileSync(distillPanelPath, "utf8");
  assert.match(panelSource, /schedule_interval_minutes: 120/);
  assert.match(panelSource, /最小 30 分钟，0=仅手动/);
});

test("distill panel is supervisor-gated like knowledge management", () => {
  const panelSource = readFileSync(distillPanelPath, "utf8");
  assert.match(panelSource, /supervisorApiKey/);
  assert.match(panelSource, /需要 supervisor API 密钥/);
});

test("distill history is behind an in-panel toggle button", () => {
  const panelSource = readFileSync(distillPanelPath, "utf8");
  assert.match(panelSource, /查看历史记录/);
  assert.match(panelSource, /showHistory/);
});

test("distill panel manages llm_chat accounts (moved out of accounts panel)", () => {
  const panelSource = readFileSync(distillPanelPath, "utf8");
  assert.match(panelSource, /listBrowserSessions/);
  assert.match(panelSource, /createBrowserSession/);
  assert.match(panelSource, /intended_use: "llm_chat"/);
  assert.match(panelSource, /对话账号管理/);
  assert.match(panelSource, /isLlmChatPlatform/);
});

test("app integrates distill panel into knowledge management, not a business tab", () => {
  // 修正1：自学习不属于业务模块——不允许出现业务级 distill Tab
  assert.doesNotMatch(appSource, /activeTab === "distill"/);
  assert.doesNotMatch(appSource, /setActiveTab\("distill"\)/);
  // 知识管理视图内提供自学习入口（Supervisor 密钥同栏）
  assert.match(appSource, /type KnowledgeTab = "skills" \| "rag" \| "distill"/);
  assert.match(appSource, /tab === "distill" \? <DistillPanel supervisorApiKey=\{props\.supervisorApiKey\} \/> : null/);
});
