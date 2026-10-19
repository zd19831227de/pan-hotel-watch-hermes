// 评价管理纯逻辑：与后端 review_management 域的展示/表单/校验规则保持一致。
// 数字输入一律用 string（受控 input），提交前再 clamp/转换。

export type ReviewPlatform = "ctrip" | "meituan" | "fliggy";
export type ReviewCategory = "good" | "medium" | "bad";
export type AutoReplyMode = "off" | "semi_auto" | "full_auto";
export type ReplyStatus = "pending" | "replied" | "ignored";

export const REVIEW_PLATFORMS: ReviewPlatform[] = ["ctrip", "meituan", "fliggy"];
export const REVIEW_REPLY_MAX_LENGTH = 500;

export function platformLabel(platform: string): string {
  if (platform === "ctrip") return "携程";
  if (platform === "meituan") return "美团";
  if (platform === "fliggy") return "飞猪";
  return platform;
}

export function autoReplyModeLabel(mode: string): string {
  if (mode === "semi_auto") return "半自动（数量提醒）";
  if (mode === "full_auto") return "全自动回复";
  return "关闭自动回复";
}

export function autoReplyModeHint(mode: string): string {
  if (mode === "semi_auto") {
    return "每日到点自动抓取并统计未回复数量（含好/中/差明细），推一张数量提醒卡到监控组飞书群，提示去 OTA 后台处理。";
  }
  if (mode === "full_auto") {
    return "每日到点自动抓取，按模板生成回复并直接提交平台；完成后推送「已处理」统计卡（好评/中评/差评数量）到监控组飞书群。";
  }
  return "到点只抓取评价并统计未回复数量，不推送、不自动回复。";
}

export function categoryLabel(category: string): string {
  if (category === "good") return "好评";
  if (category === "medium") return "中评";
  if (category === "bad") return "差评";
  return category;
}

export function replyStatusLabel(status: string): string {
  if (status === "replied") return "已回复";
  if (status === "ignored") return "已忽略";
  return "未回复";
}

export function approvalStatusLabel(status: string): string {
  if (status === "approved") return "已确认";
  if (status === "rejected") return "已忽略";
  if (status === "failed") return "提交失败";
  return "待审批";
}

export function categoryFromScore(score?: number | null): ReviewCategory {
  if (score === null || score === undefined) return "medium";
  if (score >= 4) return "good";
  if (score >= 3) return "medium";
  return "bad";
}

export function scoreText(score?: number | null): string {
  if (score === null || score === undefined) return "未评分";
  return `${score} 分`;
}

export type ReviewAutoConfigLike = {
  auto_reply_mode?: string | null;
  auto_fetch_enabled?: boolean | null;
  approval_chat_id?: string | null;
  auto_process_time?: string | null;
};

export type ReviewConfigForm = {
  auto_reply_mode: AutoReplyMode;
  auto_fetch_enabled: boolean;
  approval_chat_id: string;
  auto_process_time: string;
};

export const DEFAULT_AUTO_PROCESS_TIME = "23:00";
const AUTO_PROCESS_TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

export const defaultReviewConfigForm: ReviewConfigForm = {
  auto_reply_mode: "off",
  auto_fetch_enabled: false,
  approval_chat_id: "",
  auto_process_time: DEFAULT_AUTO_PROCESS_TIME,
};

export function reviewConfigFormFromConfig(config: ReviewAutoConfigLike | null): ReviewConfigForm {
  if (!config) return { ...defaultReviewConfigForm };
  const mode = config.auto_reply_mode;
  const time = (config.auto_process_time ?? "").trim();
  return {
    auto_reply_mode:
      mode === "semi_auto" || mode === "full_auto" ? (mode as AutoReplyMode) : "off",
    auto_fetch_enabled: Boolean(config.auto_fetch_enabled),
    approval_chat_id: config.approval_chat_id ?? "",
    auto_process_time: AUTO_PROCESS_TIME_PATTERN.test(time)
      ? time
      : DEFAULT_AUTO_PROCESS_TIME,
  };
}

export function reviewConfigPatchFromForm(form: ReviewConfigForm): {
  auto_reply_mode: AutoReplyMode;
  auto_fetch_enabled: boolean;
  approval_chat_id: string | null;
  auto_process_time: string;
} {
  return {
    auto_reply_mode: form.auto_reply_mode,
    auto_fetch_enabled: form.auto_fetch_enabled,
    approval_chat_id: form.approval_chat_id.trim() || null,
    auto_process_time: form.auto_process_time.trim() || DEFAULT_AUTO_PROCESS_TIME,
  };
}

export function reviewConfigFormError(form: ReviewConfigForm): string | null {
  const time = form.auto_process_time.trim();
  if (!AUTO_PROCESS_TIME_PATTERN.test(time)) {
    return `定时处理时间必须是 HH:MM，例如 ${DEFAULT_AUTO_PROCESS_TIME}。`;
  }
  // 半自动的飞书推送群从监控组继承（后端缺省时兜底解析监控组飞书 ID），
  // 控制台不再要求手填；保存后若监控组也没配群，后端会返回明确提示。
  return null;
}

export type ReplyTemplateLike = {
  id?: number | null;
  platform: string;
  category: string;
  name: string;
  content: string;
  score_range?: { min?: number | null; max?: number | null } | null;
  priority?: number | null;
  enabled?: boolean | null;
  source?: string | null;
};

export type TemplateForm = {
  id: number | null;
  platform: ReviewPlatform;
  category: ReviewCategory;
  name: string;
  content: string;
  priority: string;
  score_min: string;
  score_max: string;
  enabled: boolean;
};

export function emptyTemplateForm(platform: ReviewPlatform, category: ReviewCategory): TemplateForm {
  return {
    id: null,
    platform,
    category,
    name: "",
    content: "",
    priority: "100",
    score_min: "",
    score_max: "",
    enabled: true,
  };
}

export function templateFormFromTemplate(template: ReplyTemplateLike): TemplateForm {
  return {
    id: template.id ?? null,
    platform: (template.platform as ReviewPlatform) ?? "ctrip",
    category: categoryFromTemplate(template.category),
    name: template.name,
    content: template.content,
    priority: String(template.priority ?? 100),
    score_min: template.score_range?.min === null || template.score_range?.min === undefined ? "" : String(template.score_range.min),
    score_max: template.score_range?.max === null || template.score_range?.max === undefined ? "" : String(template.score_range.max),
    enabled: template.enabled !== false,
  };
}

function categoryFromTemplate(category: string): ReviewCategory {
  return category === "good" || category === "bad" ? category : "medium";
}

export type TemplatePayload = {
  id?: number | null;
  platform: ReviewPlatform;
  category: ReviewCategory;
  name: string;
  content: string;
  score_range: { min?: number; max?: number } | null;
  priority: number;
  enabled: boolean;
};

export function templatePayloadFromForm(form: TemplateForm): { payload: TemplatePayload; error: string | null } {
  const name = form.name.trim();
  const content = form.content.trim();
  if (!name) return { payload: null as unknown as TemplatePayload, error: "请填写模板名称。" };
  if (!content) return { payload: null as unknown as TemplatePayload, error: "请填写模板内容。" };
  if (content.length > 2000) {
    return { payload: null as unknown as TemplatePayload, error: "模板内容超过 2000 字。" };
  }
  const min = form.score_min.trim();
  const max = form.score_max.trim();
  let score_range: { min?: number; max?: number } | null = null;
  if (min || max) {
    const minValue = Number(min);
    const maxValue = Number(max);
    if (min && !Number.isFinite(minValue)) {
      return { payload: null as unknown as TemplatePayload, error: "分数区间下限必须是数字。" };
    }
    if (max && !Number.isFinite(maxValue)) {
      return { payload: null as unknown as TemplatePayload, error: "分数区间上限必须是数字。" };
    }
    if (min && max && minValue > maxValue) {
      return { payload: null as unknown as TemplatePayload, error: "分数区间下限不能大于上限。" };
    }
    score_range = {
      ...(min ? { min: clampScore(minValue) } : {}),
      ...(max ? { max: clampScore(maxValue) } : {}),
    };
  }
  const priorityRaw = form.priority.trim();
  const priority = priorityRaw === "" ? 100 : Number(priorityRaw);
  return {
    payload: {
      id: form.id,
      platform: form.platform,
      category: form.category,
      name,
      content,
      score_range,
      priority: Math.max(1, Math.min(1000, Number.isFinite(priority) ? priority : 100)),
      enabled: form.enabled,
    },
    error: null,
  };
}

function clampScore(value: number): number {
  return Math.max(0, Math.min(5, value));
}

export function renderTemplatePreview(content: string, nickname?: string | null): string {
  return content.replace(/\{user_nickname\}/g, (nickname ?? "").trim() || "客人");
}

export function templateSourceLabel(source?: string | null): string {
  if (source === "auto") return "自动生成";
  if (source === "imported") return "历史导入";
  return "手动创建";
}

/** 子平台绑定状态文案：未绑定时提示去「账号与浏览器」按店铺接入。 */
export function platformMountLabel(mounted: boolean, platform: string): string {
  if (mounted) return "已绑定";
  const label = platformLabel(platform);
  return `${label}账号未绑定到该店铺，请先在「账号与浏览器」把${label} EBK 账号归入该店铺`;
}

/** 「自动更新评价」二次确认文案：明确会覆盖该店铺全部评价模板。 */
export function autoUpdateTemplatesConfirmText(storeLabel?: string | null): string {
  const scope = storeLabel?.trim() ? `店铺「${storeLabel.trim()}」` : "该店铺";
  return (
    `确认自动更新${scope}的评价模板？\n\n` +
    "将抓取该店铺浏览器中已回复的评价（好/中/差），" +
    "提交 AI 分析整理后覆盖更新该店铺的全部评价模板；现有店铺模板将被替换，此操作不可撤销。"
  );
}

export function autoUpdateResultText(result: {
  status: string;
  generator?: string;
  templates_created?: number;
  by_category?: Record<string, number>;
  history_reviews?: number;
  error?: string | null;
}): string {
  if (result.status !== "ok") {
    return result.error || "自动更新未完成，请稍后重试。";
  }
  const byCategory = result.by_category ?? {};
  const generator = result.generator === "rule_based" ? "（AI 不可用，已按规则去重）" : "";
  return (
    `自动更新完成：整理已回复评价 ${result.history_reviews ?? 0} 条，` +
    `重建模板 ${result.templates_created ?? 0} 条` +
    `（好评 ${byCategory.good ?? 0}、中评 ${byCategory.medium ?? 0}、差评 ${byCategory.bad ?? 0}）${generator}。`
  );
}

export function validateReplyContent(content: string, maxLength = REVIEW_REPLY_MAX_LENGTH): { ok: boolean; error?: string } {
  const trimmed = content.trim();
  if (!trimmed) return { ok: false, error: "回复内容不能为空。" };
  if (trimmed.length > maxLength) {
    return { ok: false, error: `回复内容超过平台限制（${maxLength} 字），当前 ${trimmed.length} 字。` };
  }
  return { ok: true };
}
