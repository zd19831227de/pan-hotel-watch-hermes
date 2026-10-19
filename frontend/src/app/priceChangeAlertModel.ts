import type {
  PriceChangeAlert,
  PriceChangeSuggestion,
  PricingStrategyConfig,
} from "../services/priceChangeAlertsApi";

export type PricingStrategyForm = {
  change_trigger_mode: "percent" | "amount";
  change_trigger_percent: string;
  min_delta_yuan: string;
  max_delta_yuan: string;
  recommendation_step_yuan: string;
  max_suggested_change_yuan: string;
  allow_downward_suggestion: boolean;
  enabled: boolean;
};

export function strategyFormFromConfig(
  strategy: PricingStrategyConfig | null,
): PricingStrategyForm {
  return {
    change_trigger_mode: strategy?.change_trigger_mode ?? "percent",
    change_trigger_percent: String(strategy?.change_trigger_percent ?? 5),
    min_delta_yuan: String(strategy?.min_delta_yuan ?? 20),
    max_delta_yuan: String(strategy?.max_delta_yuan ?? 10000),
    recommendation_step_yuan: String(strategy?.recommendation_step_yuan ?? 10),
    max_suggested_change_yuan: String(strategy?.max_suggested_change_yuan ?? 30),
    allow_downward_suggestion: Boolean(strategy?.allow_downward_suggestion),
    enabled: strategy?.enabled ?? true,
  };
}

export function strategyPatchFromForm(form: PricingStrategyForm) {
  return {
    change_trigger_mode: form.change_trigger_mode,
    change_trigger_percent: boundedNumber(form.change_trigger_percent, 0, 100, 5),
    min_delta_yuan: boundedNumber(form.min_delta_yuan, 0, 10000, 20),
    max_delta_yuan: boundedNumber(form.max_delta_yuan, 0, 10000, 10000),
    recommendation_step_yuan: boundedNumber(form.recommendation_step_yuan, 1, 1000, 10),
    max_suggested_change_yuan: boundedNumber(form.max_suggested_change_yuan, 0, 10000, 30),
    allow_downward_suggestion: form.allow_downward_suggestion,
    enabled: form.enabled,
  };
}

export function alertStatusText(status?: string | null): string {
  const labels: Record<string, string> = {
    suggestion_ready: "已生成规则化建议",
    change_detected: "价格变化已触发",
    no_trigger: "未超过阈值",
    data_required: "缺少可信数据",
    needs_review: "需要复核",
    config_required: "需要配置",
    failed: "检测失败",
  };
  return labels[String(status ?? "")] ?? "暂无检测";
}

export function alertTone(status?: string | null): "good" | "warn" | "muted" {
  if (status === "suggestion_ready" || status === "change_detected") {
    return "warn";
  }
  if (status === "no_trigger") {
    return "good";
  }
  return "muted";
}

export function primarySuggestion(alert: PriceChangeAlert | null): PriceChangeSuggestion | null {
  return alert?.suggestions?.[0] ?? null;
}

export function yuan(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return `¥${Math.round(value)}`;
}

export function signedYuan(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  if (value > 0) {
    return `+¥${Math.round(value)}`;
  }
  if (value < 0) {
    return `-¥${Math.abs(Math.round(value))}`;
  }
  return "¥0";
}

export function percent(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(2)}%`;
}

export function breakfastText(value?: string | null): string {
  if (value === "none") {
    return "无早";
  }
  if (value === "included") {
    return "含早";
  }
  return "早餐未知";
}

export function boundedNumber(
  value: string,
  minimum: number,
  maximum: number,
  fallback: number,
): number {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.max(minimum, Math.min(maximum, number));
}
