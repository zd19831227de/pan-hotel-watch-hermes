import { apiRequest } from "./http";

export type CalendarMatrixBriefScheduleSlot = {
  push_time: string;
  last_pushed_at?: string | null;
  next_at?: string | null;
  due?: boolean;
};

export type CalendarMatrixBriefScheduleState = {
  push_times?: string[];
  slots?: CalendarMatrixBriefScheduleSlot[];
  morning_push_time?: string;
  evening_push_time?: string;
  morning_last_pushed_at?: string | null;
  evening_last_pushed_at?: string | null;
  morning_next_at?: string | null;
  evening_next_at?: string | null;
  morning_due?: boolean;
  evening_due?: boolean;
};

export type CalendarPriceWatchConfig = {
  watch_group_key: string;
  future_days: number;
  high_gap_threshold_yuan: number;
  low_gap_threshold_yuan: number;
  auto_poll_enabled: boolean;
  schedule_interval_minutes: number;
  schedule_mode: string;
  schedule_start_time: string;
  schedule_end_time: string;
  schedule_source: "competitor_watch_group";
  schedule_state?: {
    timezone?: string;
    mode?: string;
    start_time?: string;
    end_time?: string;
    interval_minutes?: number;
    in_window?: boolean;
    due?: boolean;
    next_run_at?: string | null;
    last_polled_at?: string | null;
  };
  browser_binding_source: "calendar_price_watch_config";
  browser_session_id?: string | null;
  browser_session_configured: boolean;
  platform_account_id?: string | null;
  feishu_webhook_configured: boolean;
  feishu_webhook_masked?: string | null;
  alert_title?: string | null;
  last_polled_at?: string | null;
  matrix_brief_enabled: boolean;
  matrix_brief_title?: string | null;
  matrix_brief_push_times?: string[];
  matrix_brief_morning_push_time: string;
  matrix_brief_evening_push_time: string;
  matrix_brief_schedule_state?: CalendarMatrixBriefScheduleState;
};

export type CalendarPriceMatrixDate = {
  checkin_date: string;
  own?: {
    hotel_alias: string;
    price_amount_yuan?: number | null;
    status: string;
  } | null;
  competitors: Array<{
    hotel_alias: string;
    price_amount_yuan?: number | null;
    status: string;
  }>;
  alerts: Array<{
    alert_type: string;
    gap_yuan?: number;
  }>;
};

export type CalendarPriceMatrixHotel = {
  alias: string;
  short_alias?: string | null;
};

export type CalendarPriceMatrix = {
  status: string;
  watch_group_key: string;
  future_days: number;
  trusted_source: string;
  own_short_alias?: string | null;
  competitor_hotels?: CalendarPriceMatrixHotel[];
  run?: {
    id: number;
    status: string;
    captured_at: string;
  } | null;
  dates: CalendarPriceMatrixDate[];
  alert_count?: number;
};

export type CalendarPriceAlert = {
  id: number;
  alert_type: string;
  checkin_date: string;
  own_price_yuan: number;
  reference_price_yuan: number;
  gap_yuan: number;
  reference_hotel_alias?: string | null;
  summary: string;
  created_at: string;
};

export type CalendarSchedulerStatus = {
  status?: string | null;
  process_confirmed?: boolean | null;
  updated_at?: string | null;
  last_heartbeat_at?: string | null;
  heartbeat_age_seconds?: number | null;
  last_due_group_count?: number | null;
  last_success_count?: number | null;
  last_failed_count?: number | null;
  last_due_matrix_brief_count?: number | null;
  last_matrix_brief_success_count?: number | null;
  last_matrix_brief_failed_count?: number | null;
};

export async function getCalendarPriceConfig(
  groupKey: string,
  signal?: AbortSignal,
): Promise<CalendarPriceWatchConfig> {
  const response = await apiRequest<{ config: CalendarPriceWatchConfig }>(
    `/api/v1/calendar-price-watch/${groupKey}/config`,
    { signal },
  );
  return response.config;
}

export async function patchCalendarPriceConfig(
  groupKey: string,
  input: Partial<CalendarPriceWatchConfig>,
): Promise<CalendarPriceWatchConfig> {
  const response = await apiRequest<{ config: CalendarPriceWatchConfig }>(
    `/api/v1/calendar-price-watch/${groupKey}/config`,
    {
      method: "PATCH",
      body: JSON.stringify(input),
    },
  );
  return response.config;
}

export async function runCalendarPriceNow(
  groupKey: string,
  pushToFeishu: boolean,
): Promise<Record<string, unknown>> {
  const response = await apiRequest<{ result: Record<string, unknown> }>(
    `/api/v1/calendar-price-watch/${groupKey}/run-now?push_to_feishu=${pushToFeishu ? "true" : "false"}`,
    { method: "POST", body: JSON.stringify({ actor_id: "operator" }) },
  );
  return response.result;
}

export async function pushCalendarMatrixBriefNow(
  groupKey: string,
): Promise<Record<string, unknown>> {
  const response = await apiRequest<{ result: Record<string, unknown> }>(
    `/api/v1/calendar-price-watch/${groupKey}/matrix-brief/push-now`,
    { method: "POST", body: JSON.stringify({ actor_id: "operator" }) },
  );
  return response.result;
}

export async function getLatestCalendarPriceMatrix(
  groupKey: string,
  signal?: AbortSignal,
): Promise<CalendarPriceMatrix> {
  const response = await apiRequest<{ matrix: CalendarPriceMatrix }>(
    `/api/v1/calendar-price-watch/${groupKey}/latest`,
    { signal },
  );
  return response.matrix;
}

export async function listCalendarPriceAlerts(
  groupKey: string,
  signal?: AbortSignal,
): Promise<CalendarPriceAlert[]> {
  const response = await apiRequest<{ alerts: CalendarPriceAlert[] }>(
    `/api/v1/calendar-price-watch/${groupKey}/alerts`,
    { signal },
  );
  return response.alerts;
}

export async function getCalendarSchedulerStatus(
  signal?: AbortSignal,
): Promise<CalendarSchedulerStatus> {
  const response = await apiRequest<{ scheduler: CalendarSchedulerStatus }>(
    "/api/v1/calendar-price-watch/scheduler-status",
    { signal },
  );
  return response.scheduler;
}

// 全部竞品组的日历价监控配置摘要（顶部状态条聚合展示用，只读）。
export type CalendarConfigSummary = {
  watch_group_key: string;
  group_name: string | null;
  group_enabled: boolean;
  auto_poll_enabled: boolean;
  matrix_brief_enabled: boolean;
  last_polled_at: string | null;
  config_present: boolean;
};

export async function listCalendarConfigSummaries(
  signal?: AbortSignal,
): Promise<CalendarConfigSummary[]> {
  const response = await apiRequest<{ configs: CalendarConfigSummary[] }>(
    "/api/v1/calendar-price-watch/configs",
    { signal },
  );
  return response.configs ?? [];
}
