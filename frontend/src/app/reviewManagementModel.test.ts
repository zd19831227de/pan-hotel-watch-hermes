import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  autoReplyModeLabel,
  categoryFromScore,
  categoryLabel,
  emptyTemplateForm,
  renderTemplatePreview,
  reviewConfigFormError,
  reviewConfigFormFromConfig,
  reviewConfigPatchFromForm,
  replyStatusLabel,
  templateFormFromTemplate,
  templatePayloadFromForm,
  validateReplyContent,
  defaultReviewConfigForm,
  autoUpdateResultText,
  autoUpdateTemplatesConfirmText,
  platformMountLabel,
  templateSourceLabel,
} from "./reviewManagementModel.ts";

test("review config form falls back to safe defaults when config is missing", () => {
  const form = reviewConfigFormFromConfig(null);
  assert.equal(form.auto_reply_mode, "off");
  assert.equal(form.auto_fetch_enabled, false);
  assert.equal(form.auto_process_time, "23:00");
  assert.deepEqual(defaultReviewConfigForm, form);
});

test("评价管理一级=店铺+未分组，平台 tab 带绑定徽标（店铺树 §3.2）", () => {
  const panelSource = readFileSync(new URL("./ReviewManagementPanel.tsx", import.meta.url), "utf8");
  // 一级店铺 tab + 未分组计数 tab；二级平台 tab 带绑定徽标
  assert.ok(panelSource.includes('aria-label="评价店铺切换"'));
  assert.ok(panelSource.includes("未分组监控组("));
  assert.ok(panelSource.includes("已绑定"));
  assert.ok(panelSource.includes("未绑定"));
  // 未分组 legacy 条目可选可用，提示补绑后归位
  assert.ok(panelSource.includes("使用该组"));
  assert.ok(panelSource.includes("自动归入对应店铺"));
  // 「评价店铺」select 被一级 tab 取代（原 select 直接绑定 props.groupKey）
  assert.ok(!panelSource.includes("value={props.groupKey}"));
  assert.ok(!panelSource.includes("reviewGroupOptions"));
  // 安全契约：不出现写价入口
  assert.doesNotMatch(panelSource, /立即调价|写入携程|自动执行|确认按此价格调价/);
});

test("review config patch passes through scheduled time and clears blank chat id", () => {
  const patch = reviewConfigPatchFromForm({
    ...defaultReviewConfigForm,
    auto_reply_mode: "semi_auto",
    approval_chat_id: "  ",
    auto_process_time: "23:00",
  });
  assert.equal(patch.approval_chat_id, null);
  assert.equal(patch.auto_reply_mode, "semi_auto");
  assert.equal(patch.auto_process_time, "23:00");
});

test("review config form rejects invalid scheduled time and restores bad config values", () => {
  assert.match(
    reviewConfigFormError({ ...defaultReviewConfigForm, auto_process_time: "24:00" }) ?? "",
    /HH:MM/,
  );
  assert.match(
    reviewConfigFormError({ ...defaultReviewConfigForm, auto_process_time: "" }) ?? "",
    /HH:MM/,
  );
  // 后端返回的异常时间回落默认，不在表单里保留脏值。
  const form = reviewConfigFormFromConfig({ auto_process_time: "25:99" });
  assert.equal(form.auto_process_time, "23:00");
  const configured = reviewConfigFormFromConfig({ auto_process_time: "09:30" });
  assert.equal(configured.auto_process_time, "09:30");
});

test("template payload treats blank priority as default 100, not 1", () => {
  const { payload, error } = templatePayloadFromForm({
    ...emptyTemplateForm("ctrip", "good"),
    name: "好评通用",
    content: "感谢您 {user_nickname}",
    priority: "",
  });
  assert.equal(error, null);
  assert.equal(payload.priority, 100);
});

test("semi auto mode does not require a hand-typed feishu group", () => {
  // 飞书群从监控组继承（后端兜底解析），不手填也能保存半自动。
  assert.equal(
    reviewConfigFormError({ ...defaultReviewConfigForm, auto_reply_mode: "semi_auto" }),
    null,
  );
  assert.equal(reviewConfigFormError(defaultReviewConfigForm), null);
});

test("category thresholds mirror the backend good/medium/bad split", () => {
  assert.equal(categoryFromScore(5), "good");
  assert.equal(categoryFromScore(4), "good");
  assert.equal(categoryFromScore(3.9), "medium");
  assert.equal(categoryFromScore(3), "medium");
  assert.equal(categoryFromScore(2.9), "bad");
  assert.equal(categoryFromScore(null), "medium");
  assert.equal(categoryLabel("bad"), "差评");
  assert.equal(replyStatusLabel("pending"), "未回复");
});

test("auto reply mode labels keep operator wording", () => {
  assert.equal(autoReplyModeLabel("off"), "关闭自动回复");
  assert.equal(autoReplyModeLabel("semi_auto"), "半自动（数量提醒）");
  assert.equal(autoReplyModeLabel("full_auto"), "全自动回复");
});

test("template form round-trips score range into payload", () => {
  const form = templateFormFromTemplate({
    id: 7,
    platform: "ctrip",
    category: "good",
    name: "好评通用",
    content: "感谢您 {user_nickname}",
    score_range: { min: 4, max: 5 },
    priority: 20,
    enabled: true,
  });
  assert.equal(form.score_min, "4");
  assert.equal(form.score_max, "5");
  const { payload, error } = templatePayloadFromForm(form);
  assert.equal(error, null);
  assert.deepEqual(payload.score_range, { min: 4, max: 5 });
  assert.equal(payload.priority, 20);
});

test("template payload validates empty fields and bad ranges", () => {
  const empty = templatePayloadFromForm(emptyTemplateForm("ctrip", "good"));
  assert.match(empty.error ?? "", /模板名称/);
  const badRange = templatePayloadFromForm({
    ...emptyTemplateForm("ctrip", "good"),
    name: "x",
    content: "y",
    score_min: "4.5",
    score_max: "4",
  });
  assert.match(badRange.error ?? "", /下限不能大于上限/);
});

test("template preview substitutes nickname placeholder", () => {
  assert.equal(renderTemplatePreview("感谢您 {user_nickname} 的认可", "张三"), "感谢您 张三 的认可");
  assert.equal(renderTemplatePreview("感谢您 {user_nickname} 的认可", null), "感谢您 客人 的认可");
  assert.equal(renderTemplatePreview("感谢入住", "张三"), "感谢入住");
});

test("reply content validation enforces non-empty and length cap", () => {
  assert.equal(validateReplyContent("  ").ok, false);
  assert.equal(validateReplyContent("好的").ok, true);
  const tooLong = validateReplyContent("长".repeat(501));
  assert.equal(tooLong.ok, false);
  assert.match(tooLong.error ?? "", /500/);
});

test("template source label covers auto/imported/manual", () => {
  assert.equal(templateSourceLabel("auto"), "自动生成");
  assert.equal(templateSourceLabel("imported"), "历史导入");
  assert.equal(templateSourceLabel("manual"), "手动创建");
  assert.equal(templateSourceLabel(null), "手动创建");
});

test("platform mount label describes mounted vs needs-mounting", () => {
  assert.equal(platformMountLabel(true, "ctrip"), "已绑定");
  assert.match(platformMountLabel(false, "meituan"), /美团账号未绑定到该店铺/);
  assert.match(platformMountLabel(false, "fliggy"), /账号与浏览器/);
});

test("auto update confirm text warns about overwriting store templates", () => {
  const text = autoUpdateTemplatesConfirmText("店铺A（acct_a）");
  assert.match(text, /店铺「店铺A（acct_a）」/);
  assert.match(text, /覆盖/);
  assert.match(text, /好\/中\/差/);
  assert.match(text, /不可撤销/);
  // 未选店铺时退回「该店铺」口径，不留空括号。
  assert.match(autoUpdateTemplatesConfirmText("  "), /该店铺/);
});

test("auto update result text summarizes categories and rule fallback", () => {
  const okText = autoUpdateResultText({
    status: "ok",
    generator: "llm",
    templates_created: 3,
    by_category: { good: 1, medium: 1, bad: 1 },
    history_reviews: 8,
  });
  assert.match(okText, /已回复评价 8 条/);
  assert.match(okText, /好评 1、中评 1、差评 1/);
  assert.doesNotMatch(okText, /规则去重/);

  const fallbackText = autoUpdateResultText({
    status: "ok",
    generator: "rule_based",
    templates_created: 2,
    by_category: { good: 1, bad: 1 },
    history_reviews: 3,
  });
  assert.match(fallbackText, /规则去重/);

  assert.match(
    autoUpdateResultText({ status: "no_history", error: "该店铺浏览器内没有已回复的评价，未更新模板。" }),
    /没有已回复的评价/,
  );
});
