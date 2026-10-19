import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const panelUrl = new URL("./AdvancedAgentConfigPanel.tsx", import.meta.url);
const panelSource = existsSync(panelUrl) ? readFileSync(panelUrl, "utf8") : "";
const admissionPanelUrl = new URL("./FeishuAdmissionPanel.tsx", import.meta.url);
const admissionPanelSource = existsSync(admissionPanelUrl)
  ? readFileSync(admissionPanelUrl, "utf8")
  : "";
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const configApiSource = readFileSync(new URL("../services/configApi.ts", import.meta.url), "utf8");

test("高级配置面板展示 Agent 到群和监控组的六个运营区", () => {
  assert.ok(panelSource.includes("飞书 AI 助手"));
  assert.ok(panelSource.includes("运营问答上下文群"));
  assert.ok(panelSource.includes("出站推送地址"));
  assert.ok(panelSource.includes("携程调价"));
  assert.ok(panelSource.includes("权限管理"));
  assert.ok(panelSource.includes("飞书准入设置"));
  assert.ok(panelSource.includes("supervisor"));
  assert.ok(panelSource.includes("watch_groups"));
  assert.ok(panelSource.includes("pricing_browser_session_id"));
  assert.match(panelSource, /aria-label=\{`\$\{binding\.chat_id_masked\} Agent profile`\}/);
});

test("飞书准入面板支持手工 open_id 管理且不暴露受控凭据", () => {
  assert.ok(panelSource.includes("FeishuAdmissionPanel"));
  assert.ok(admissionPanelSource.includes("getFeishuProfileAdmission"));
  assert.ok(admissionPanelSource.includes("patchFeishuProfileAdmission"));
  assert.ok(admissionPanelSource.includes("addFeishuAdmissionUser"));
  assert.ok(admissionPanelSource.includes("removeFeishuAdmissionUser"));
  assert.ok(admissionPanelSource.includes("require_mention"));
  assert.ok(admissionPanelSource.includes("allowed_users"));
  assert.ok(admissionPanelSource.includes("open_id"));
  assert.doesNotMatch(
    admissionPanelSource,
    /app_secret|FEISHU_APP_SECRET|admission-rejected|自动捕获|待审核用户/i,
  );
});

test("飞书准入 API 使用 supervisor 请求头并定位单个 profile", () => {
  assert.ok(configApiSource.includes("export type FeishuProfileAdmission"));
  assert.ok(configApiSource.includes("getFeishuProfileAdmission"));
  assert.ok(configApiSource.includes("patchFeishuProfileAdmission"));
  assert.ok(configApiSource.includes("addFeishuAdmissionUser"));
  assert.ok(configApiSource.includes("removeFeishuAdmissionUser"));
  assert.match(configApiSource, /feishu\/profiles\/\$\{encodeURIComponent\(profileKey\)\}\/admission/);
  assert.ok(configApiSource.includes("supervisorInit(apiKey"));
});

test("运营上下文群使用原始 chat ID 精确关联监控组", () => {
  assert.match(panelSource, /group\.feishu_chat_id === chatId/);
  assert.doesNotMatch(panelSource, /group\.feishu_chat_id_masked === chatIdMasked/);
});

test("高级配置面板凭据输入用 password 控件，响应不含 App Secret", () => {
  assert.match(panelSource, /credential_configured/);
  assert.ok(panelSource.includes("凭据已配置") || panelSource.includes("凭据未配置"));
  // supervisor 鉴权后可凭据输入：secret 必须用 type="password"（不显示）
  assert.match(panelSource, /type="password"/);
  // 面板源码不得出现明文 App Secret 文案/受控密钥字面量
  assert.doesNotMatch(panelSource, /FEISHU_APP_SECRET/);
});

test("高级配置面板不为非携程平台提供调价入口", () => {
  assert.doesNotMatch(panelSource, /美团.{0,24}调价|调价.{0,24}美团|飞猪.{0,24}调价|调价.{0,24}飞猪/);
});

test("高级入口保留现有模型配置和主页面操作入口", () => {
  assert.ok(appSource.includes("ModelConfigPanel"));
  assert.ok(appSource.includes("AccountsPanel"));
  assert.ok(appSource.includes("EbkOpsConsole"));
  assert.ok(appSource.includes("CompetitorPanel"));
  assert.ok(appSource.includes("AdvancedAgentConfigPanel"));
});

test("运营上下文群支持把当前 chat 绑定到单个 EBK 监控组", () => {
  // The panel must expose a binding callback for EBK group feishu_chat_id.
  assert.ok(
    panelSource.includes("onBindEbkGroupChat"),
    "panel should accept an onBindEbkGroupChat callback",
  );
  // The context row must render a select that maps to ebk group_key values,
  // with an explicit 不绑定 (unbind) option and a per-binding aria label.
  assert.ok(
    panelSource.includes("不绑定"),
    "binding dropdown should offer an unbind option",
  );
  assert.match(
    panelSource,
    /aria-label=\{`\$\{binding\.chat_id_masked\} EBK 监控组绑定?`\}/,
  );
  // The bound value must come from comparing ebk group feishu_chat_id to the
  // binding chat_id (single chat <-> single EBK group).
  assert.match(panelSource, /group\.feishu_chat_id === binding\.chat_id/);
});

test("飞书 AI 助手区提供新增 profile 入口", () => {
  // 后端有 POST /feishu-agent-profiles，前端此前无创建入口。
  assert.ok(configApiSource.includes("createFeishuAgentProfile"));
  assert.match(
    configApiSource,
    /feishu-agent-profiles",\s*supervisorInit\(apiKey, \{ method: "POST"/,
  );
  assert.ok(panelSource.includes("新增 AI 助手"));
  assert.ok(panelSource.includes("onCreateProfile"));
  // App 侧创建链路：调 createFeishuAgentProfile 后刷新总览。
  assert.ok(appSource.includes("handleCreateAgentProfile"));
  assert.match(appSource, /createFeishuAgentProfile\(supervisorApiKey/);
});

test("运营上下文群提供新增群绑定表单（新增整套店铺接入入口）", () => {
  // 后端早有 POST /config/feishu-chat-agent-bindings，此前前端没有入口。
  assert.ok(configApiSource.includes("createFeishuChatAgentBinding"));
  assert.match(configApiSource, /feishu-chat-agent-bindings",\s*supervisorInit\(apiKey, \{ method: "POST"/);
  assert.ok(panelSource.includes("新增群绑定"));
  assert.ok(panelSource.includes("onCreateChatBinding"));
  // 创建时可自动绑定 EBK/竞品监控组（自动识别监控组），也可稍后手动绑定。
  assert.ok(panelSource.includes("自动绑定 EBK 监控组"));
  assert.ok(panelSource.includes("自动绑定竞品监控组"));
  // App 侧创建链路：先建绑定，再按选择挂组，最后刷新总览。
  assert.ok(appSource.includes("handleCreateChatBinding"));
  assert.match(
    appSource,
    /createFeishuChatAgentBinding\(supervisorApiKey/,
  );
});

test("运营上下文群支持把当前 chat 绑定到单个竞品监控组", () => {
  assert.ok(panelSource.includes("onBindCompetitorGroupChat"));
  assert.match(
    panelSource,
    /aria-label=\{`\$\{binding\.chat_id_masked\} 竞品监控组绑定`\}/,
  );
  assert.ok(appSource.includes("handleBindCompetitorGroupChat"));
  assert.match(
    appSource,
    /patchCompetitorGroup\(groupKey,\s*\{\s*feishu_chat_id:\s*chatId\s*\}\)/,
  );
});

test("App.tsx 实现 EBK 监控组绑定回调用 patchDataCenterGroup", () => {
  assert.ok(
    appSource.includes("onBindEbkGroupChat"),
    "App.tsx should wire onBindEbkGroupChat into the advanced panel",
  );
  assert.ok(
    appSource.includes("handleBindEbkGroupChat"),
    "App.tsx should define the handleBindEbkGroupChat handler",
  );
  // The handler must reuse the existing EBK patch API to write feishu_chat_id.
  // chatId is string | null: the same call binds (non-null) or unbinds (null).
  assert.match(appSource, /patchDataCenterGroup\(groupKey,\s*\{\s*feishu_chat_id:\s*chatId\s*\}\)/);
  // Binding changes must refresh the supervisor overview so the dropdown
  // reflects the new relationship without a manual reload.
  assert.match(appSource, /reloadSupervisorOverview\(\)/);
});

test("supervisor 出站推送地址在鉴权后显示原始 webhook URL，否则回退脱敏文案", () => {
  // The panel must consume the raw feishu_webhook_url field returned by the
  // supervisor overview for authenticated admins.
  assert.ok(
    panelSource.includes("feishu_webhook_url"),
    "panel should read the raw feishu_webhook_url field",
  );
  // When the raw URL is present it must be shown; otherwise fall back to the
  // masked/configured wording (no address hidden behind a fixed label).
  assert.ok(
    panelSource.includes("已配置（地址已隐藏）"),
    "non-supervisor context must keep the masked fallback wording",
  );
});

test("supervisor 出站推送地址在鉴权后显示原始投递群 chat_id，否则回退脱敏", () => {
  // The panel must prefer the raw feishu_chat_id over the masked value so
  // authenticated admins can verify the exact delivery group.
  assert.ok(
    panelSource.includes("投递群"),
    "panel should render the delivery-group label",
  );
  // The 投递群 line must reference the raw feishu_chat_id (with masked fallback).
  assert.match(
    panelSource,
    /投递群 \$\{group\.feishu_chat_id \?\? group\.feishu_chat_id_masked/,
  );
});

test("高级配置提供就地操作反馈，不再只依赖页面顶部 notice", () => {
  // 2026-09-11 反馈：高级配置在页面底部，错误/成功提示只出现在页面顶部，
  // 运营在面板里操作后「毫无反应」。操作反馈必须就地渲染。
  assert.ok(panelSource.includes("actionNotice"), "panel should render actionNotice");
  assert.match(
    panelSource,
    /className=\{`notice \$\{props\.actionNotice\.tone\} advanced-agent-action-notice`\}/,
  );
  assert.ok(appSource.includes("runAdvancedAction"), "App should route advanced actions through a scoped runner");
  assert.ok(appSource.includes("notifyAdvanced"), "App should mirror notices into the advanced panel");
});

test("AI 助手与群绑定支持删除（创建后可管理，不是一次性配置）", () => {
  assert.ok(configApiSource.includes("deleteFeishuAgentProfile"));
  assert.ok(configApiSource.includes("deleteFeishuChatAgentBinding"));
  assert.match(
    configApiSource,
    /feishu-agent-profiles\/\$\{encodeURIComponent\(profileKey\)\}`,\s*supervisorInit\(apiKey, \{ method: "DELETE" \}\)/,
  );
  assert.ok(panelSource.includes("onDeleteProfile"), "profile rows need a delete callback");
  assert.ok(panelSource.includes("onDeleteBinding"), "binding rows need a delete callback");
  assert.ok(appSource.includes("handleDeleteAgentProfile"));
  assert.ok(appSource.includes("handleDeleteChatBinding"));
});

test("群绑定创建后自动同步飞书准入并反馈同步结果", () => {
  // 后端在创建/启停/换 profile/删除时同步 Hermes 准入名单；前端把
  // admission_synced / admission_warning 体现在就地通知里。
  assert.ok(configApiSource.includes("admission_synced"));
  assert.ok(configApiSource.includes("admission_warning"));
  assert.match(appSource, /created\.admission_warning/);
  assert.match(appSource, /created\.admission_synced/);
  // 创建提示不再要求运营手动去准入面板补部署。
  assert.ok(panelSource.includes("自动把该群加入所选 AI 助手的飞书准入名单"));
});

test("手动调价指令群输入半显示已绑定信息并可清除", () => {
  assert.match(
    panelSource,
    /已绑定：\$\{session\.pricing_feishu_chat_id_masked \?\? ""\}，输入新群 ID 覆盖/,
  );
});

test("飞书准入面板随高级配置操作自动刷新", () => {
  assert.ok(panelSource.includes("admissionRevision={props.admissionRevision}"));
  assert.match(admissionPanelSource, /\[loadAdmission, admissionRevision\]/);
  assert.ok(appSource.includes("setAdmissionRevision"));
});

test("账号页绑定监控组改为就地选择目标组，不再依赖其他页的选中态", () => {
  const accountsSource = readFileSync(
    new URL("./tabs/AccountsPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(accountsSource, /dataCenterGroups: DataCenterWatchGroup\[\]/);
  assert.match(accountsSource, /competitorGroups: CompetitorWatchGroup\[\]/);
  assert.match(accountsSource, /onBindDataCenter: \(session: BrowserSession, groupKey: string\) => void/);
  assert.match(accountsSource, /onBindCompetitor: \(session: BrowserSession, groupKey: string\) => void/);
  assert.ok(accountsSource.includes("选择 EBK 监控组…"));
  assert.ok(accountsSource.includes("选择竞品监控组…"));
  assert.ok(appSource.includes("handleBindDataCenter(session: BrowserSession, groupKey: string)"));
  assert.ok(appSource.includes("handleBindCompetitor(session: BrowserSession, groupKey: string)"));
});




