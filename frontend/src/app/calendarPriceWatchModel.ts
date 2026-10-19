import type { CalendarPriceWatchConfig } from "../services/calendarPriceWatchApi";

export const DEFAULT_MATRIX_BRIEF_PUSH_TIMES = ["10:00", "15:00", "21:00"] as const;

export type CalendarPriceConfigForm = {
  future_days: string;
  high_gap_threshold_yuan: string;
  low_gap_threshold_yuan: string;
  schedule_interval_minutes: string;
  alert_title: string;
  auto_poll_enabled: boolean;
  matrix_brief_enabled: boolean;
  matrix_brief_title: string;
  matrix_brief_push_times: string[];
  browser_session_id: string;
};

export const defaultCalendarPriceConfigForm: CalendarPriceConfigForm = {
  future_days: "15",
  high_gap_threshold_yuan: "10",
  low_gap_threshold_yuan: "20",
  schedule_interval_minutes: "120",
  alert_title: "",
  auto_poll_enabled: false,
  matrix_brief_enabled: false,
  matrix_brief_title: "",
  matrix_brief_push_times: [...DEFAULT_MATRIX_BRIEF_PUSH_TIMES],
  browser_session_id: "",
};

export function calendarConfigFormFromConfig(
  config: CalendarPriceWatchConfig | null,
): CalendarPriceConfigForm {
  if (!config) {
    return { ...defaultCalendarPriceConfigForm, matrix_brief_push_times: [...DEFAULT_MATRIX_BRIEF_PUSH_TIMES] };
  }
  return {
    future_days: String(config.future_days ?? 15),
    high_gap_threshold_yuan: String(config.high_gap_threshold_yuan ?? 10),
    low_gap_threshold_yuan: String(config.low_gap_threshold_yuan ?? 20),
    schedule_interval_minutes: String(config.schedule_interval_minutes ?? 120),
    alert_title: config.alert_title ?? "",
    auto_poll_enabled: Boolean(config.auto_poll_enabled),
    matrix_brief_enabled: Boolean(config.matrix_brief_enabled),
    matrix_brief_title: config.matrix_brief_title ?? "",
    matrix_brief_push_times: normalizeMatrixBriefPushTimes(
      config.matrix_brief_push_times?.length
        ? config.matrix_brief_push_times
        : [
            config.matrix_brief_morning_push_time,
            config.matrix_brief_evening_push_time,
          ],
    ),
    browser_session_id: config.browser_session_id ?? "",
  };
}

export function calendarConfigPatchFromForm(form: CalendarPriceConfigForm) {
  const pushTimes = normalizeMatrixBriefPushTimes(form.matrix_brief_push_times);
  return {
    future_days: Math.max(1, Math.min(30, Number(form.future_days) || 15)),
    high_gap_threshold_yuan: Math.max(0, Number(form.high_gap_threshold_yuan) || 10),
    low_gap_threshold_yuan: Math.max(0, Number(form.low_gap_threshold_yuan) || 20),
    schedule_interval_minutes: Math.max(1, Number(form.schedule_interval_minutes) || 120),
    alert_title: form.alert_title.trim() || null,
    auto_poll_enabled: form.auto_poll_enabled,
    matrix_brief_enabled: form.matrix_brief_enabled,
    matrix_brief_title: form.matrix_brief_title.trim() || null,
    matrix_brief_push_times: pushTimes,
    matrix_brief_morning_push_time: pushTimes[0],
    matrix_brief_evening_push_time: pushTimes[pushTimes.length - 1],
    browser_session_id: form.browser_session_id.trim() || null,
  };
}

export function calendarScheduleModeLabel(value?: string | null): string {
  if (value === "last_run_interval") {
    return "按上次读取间隔";
  }
  return "固定时间点";
}

export function matrixBriefStatusLabel(enabled?: boolean): string {
  return enabled ? "已开启" : "未开启";
}

export function matrixBriefSlotLabel(slotTime: string): string {
  return `${normalizeTimeOfDay(slotTime, "10:00")} 简报`;
}

export function matrixBriefTimesLabel(times?: readonly string[] | null): string {
  const normalized = normalizeMatrixBriefPushTimes(times ?? DEFAULT_MATRIX_BRIEF_PUSH_TIMES);
  return normalized.join(" / ");
}

export function isValidTimeOfDay(value: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(value.trim());
}

export function normalizeTimeOfDay(value: string, fallback: string): string {
  const trimmed = value.trim();
  const short = trimmed.length >= 5 ? trimmed.slice(0, 5) : trimmed;
  return isValidTimeOfDay(short) ? short : fallback;
}

export function normalizeMatrixBriefPushTimes(values: readonly (string | null | undefined)[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    if (!value) {
      continue;
    }
    const slot = normalizeTimeOfDay(value, "");
    if (!slot || seen.has(slot)) {
      continue;
    }
    seen.add(slot);
    result.push(slot);
  }
  if (result.length === 0) {
    return [...DEFAULT_MATRIX_BRIEF_PUSH_TIMES];
  }
  return result.sort();
}

export function addMatrixBriefPushTime(times: string[], value: string): string[] {
  return normalizeMatrixBriefPushTimes([...times, value]);
}

export function removeMatrixBriefPushTime(times: string[], value: string): string[] {
  const next = times.filter((item) => item !== value);
  return next.length > 0 ? normalizeMatrixBriefPushTimes(next) : [...times];
}

export function calendarAlertLabel(value: string): string {
  if (value === "own_price_high") {
    return "本店偏高";
  }
  if (value === "own_price_low") {
    return "本店明显低于竞对";
  }
  return value || "未分类预警";
}

export function calendarMatrixSummary(input: {
  status?: string;
  alert_count?: number;
  future_days?: number;
}): string {
  const count = Number(input.alert_count ?? 0);
  const days = Number(input.future_days ?? 15);
  if (input.status === "alert" && count > 0) {
    return `未来 ${days} 天有 ${count} 个日期触发日历价预警，建议人工查看差异。`;
  }
  if (input.status === "needs_review") {
    return "部分日期缺少可信日历价，请人工复核页面结构。";
  }
  return `未来 ${days} 天日历价读取正常，当前无预警。`;
}

export function calendarPriceDisplay(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "待复核";
  }
  return `¥${Math.round(value)}`;
}

export function calendarDateStatusLabel(status?: string | null): string {
  if (status === "success") {
    return "正常";
  }
  if (status === "needs_review") {
    return "待复核";
  }
  if (status === "human_required") {
    return "需人工";
  }
  return status || "未知";
}
