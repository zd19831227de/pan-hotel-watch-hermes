import { apiRequest } from "./http";

export type PricingStrategyConfig = {
  id: number;
  strategy_key: string;
  name: string;
  watch_group_key: string;
  change_trigger_mode: "percent" | "amount";
  change_trigger_percent: number;
  min_delta_yuan: number;
  max_delta_yuan: number;
  recommendation_step_yuan: number;
  max_suggested_change_yuan: number;
  allow_downward_suggestion: boolean;
  enabled: boolean;
  summary: string;
  trusted_source_statement: string;
  created_at: string;
  updated_at: string;
};

export type PricingStrategyPatch = Partial<
  Pick<
    PricingStrategyConfig,
    | "name"
    | "watch_group_key"
    | "change_trigger_mode"
    | "change_trigger_percent"
    | "min_delta_yuan"
    | "max_delta_yuan"
    | "recommendation_step_yuan"
    | "max_suggested_change_yuan"
    | "allow_downward_suggestion"
    | "enabled"
  >
>;

export type PriceChangeSuggestion = {
  hotel_config_id?: number;
  hotel_name?: string | null;
  ctrip_hotel_id?: string | null;
  date_label?: string | null;
  checkin_date?: string | null;
  checkout_date?: string | null;
  room_type?: string | null;
  normalized_room_type?: string | null;
  breakfast_kind?: string | null;
  breakfast_text?: string | null;
  current_own_price_yuan?: number | null;
  competitor_previous_price_yuan?: number | null;
  competitor_current_price_yuan?: number | null;
  change_amount_yuan?: number | null;
  change_percent?: number | null;
  recommended_adjustment_yuan?: number | null;
  recommended_price_yuan?: number | null;
  trigger_reason?: string | null;
  needs_human_attention?: boolean;
  trusted_source_statement?: string;
  connection_mode?: "direct" | "non_direct" | "unknown";
  can_auto_write_price?: boolean;
  write_policy_message?: string;
};

export type PriceChangeAlert = {
  id?: number;
  alert_record_id?: number | null;
  watch_group_key: string;
  status: string;
  summary: string;
  connection_mode?: "direct" | "non_direct" | "unknown";
  can_auto_write_price?: boolean;
  write_policy_message?: string;
  current_run_id?: number | null;
  previous_run_id?: number | null;
  changed_count: number;
  suggestion_count: number;
  compared_key_count: number;
  changed_items?: Array<Record<string, unknown>>;
  suggestions: PriceChangeSuggestion[];
  evidence?: Record<string, unknown>;
  feishu_push?: Record<string, unknown> | null;
  created_at?: string;
};

export async function getPricingStrategyConfig(): Promise<PricingStrategyConfig> {
  const payload = await apiRequest<{ strategy: PricingStrategyConfig }>(
    "/api/v1/pricing-strategy/config",
  );
  return payload.strategy;
}

export async function patchPricingStrategyConfig(
  input: PricingStrategyPatch,
  options?: { actorId?: string },
): Promise<PricingStrategyConfig> {
  const payload = await apiRequest<{ strategy: PricingStrategyConfig }>(
    "/api/v1/pricing-strategy/config",
    { method: "PATCH", body: JSON.stringify({ ...input, actor_id: options?.actorId || "operator" }) },
  );
  return payload.strategy;
}

export async function evaluatePriceChangeAlert(input: {
  watch_group_key: string;
  current_run_id?: number | null;
  push_to_feishu?: boolean;
  actor_id?: string;
}): Promise<PriceChangeAlert> {
  const payload = await apiRequest<{ alert: PriceChangeAlert }>(
    "/api/v1/price-change-alerts/evaluate",
    { method: "POST", body: JSON.stringify({ ...input, actor_id: input.actor_id || "operator" }) },
  );
  return payload.alert;
}

export async function getLatestPriceChangeAlert(
  groupKey: string,
  signal?: AbortSignal,
): Promise<PriceChangeAlert | null> {
  try {
    const payload = await apiRequest<{ alert: PriceChangeAlert }>(
      `/api/v1/price-change-alerts/latest?watch_group_key=${groupKey}`,
      { signal },
    );
    return payload.alert;
  } catch (error) {
    if (signal?.aborted) {
      throw error;
    }
    return null;
  }
}

export async function listPriceChangeAlerts(
  groupKey: string,
): Promise<PriceChangeAlert[]> {
  const payload = await apiRequest<{ alerts: PriceChangeAlert[] }>(
    `/api/v1/price-change-alerts?watch_group_key=${groupKey}`,
  );
  return payload.alerts;
}
