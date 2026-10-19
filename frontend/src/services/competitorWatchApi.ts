import { apiRequest } from "./http";

export type BrowserRotationMode = "fixed" | "pool_round_robin";

export type CompetitorBrowserPoolMember = {
  id?: number;
  browser_session_id: string;
  platform_account_id?: string | null;
  display_name?: string | null;
  intended_use?: string | null;
  login_state?: string | null;
  human_verification_status?: string | null;
  enabled: boolean;
  sort_order: number;
  weight: number;
  last_selected_at?: string | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  last_status?: string | null;
  cooldown_until?: string | null;
  cooldown_active?: boolean;
  consecutive_failures?: number;
  usable?: boolean;
};

export type CompetitorBrowserPool = {
  pool_enabled: boolean;
  rotation_mode?: BrowserRotationMode;
  pool_size: number;
  enabled_pool_size?: number;
  usable_pool_size: number;
  failover_enabled?: boolean;
  cooldown_minutes?: number;
  members: CompetitorBrowserPoolMember[];
};

export type CompetitorBrowserPoolMemberInput = {
  browser_session_id: string;
  enabled?: boolean;
  sort_order?: number;
  weight?: number;
};

export type CompetitorWatchGroup = {
  id: number;
  group_key: string;
  name: string;
  brief_title?: string | null;
  price_change_alert_title?: string | null;
  platform_account_id: string;
  browser_session_id?: string | null;
  browser_rotation_mode?: BrowserRotationMode;
  pool_failover_enabled?: boolean;
  pool_cooldown_minutes?: number;
  browser_pool?: CompetitorBrowserPool;
  pricing_platform_account_id?: string | null;
  pricing_browser_session_id?: string | null;
  pricing_account_configured?: boolean;
  pricing_connection_mode?: "direct" | "non_direct" | "unknown";
  pricing_can_auto_write_price?: boolean;
  pricing_write_policy_message?: string | null;
  feishu_chat_id?: string | null;
  feishu_webhook_configured: boolean;
  feishu_webhook_masked?: string | null;
  feishu_mobile_webhook_configured?: boolean;
  feishu_mobile_webhook_masked?: string | null;
  feishu_desktop_webhook_configured?: boolean;
  feishu_desktop_webhook_masked?: string | null;
  feishu_chat_id_configured?: boolean;
  feishu_chat_id_masked?: string | null;
  feishu_card_layout?: "mobile_blocks" | "desktop_table";
  push_date_scope?: "today_only" | "today_tomorrow";
  retention_days: number;
  query_interval_minutes: number;
  schedule_mode?: "fixed_slots" | "last_run_interval";
  schedule_start_time?: string;
  schedule_end_time?: string;
  schedule_interval_minutes?: number;
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
  auto_poll_enabled: boolean;
  enabled: boolean;
  scheduler_status?: CompetitorSchedulerStatus | null;
  last_polled_at?: string | null;
  enabled_own_hotel_count?: number;
  enabled_competitor_hotel_count?: number;
  runtime_config_status?: string;
  latest_run?: {
    id: number;
    status: string;
    summary?: string | null;
    captured_at: string;
    source: string;
  } | null;
};

export type CompetitorSchedulerStatus = {
  status?: string | null;
  process_confirmed?: boolean | null;
  updated_at?: string | null;
  last_heartbeat_at?: string | null;
  heartbeat_age_seconds?: number | null;
  last_due_group_count?: number | null;
  last_result_count?: number | null;
  last_success_count?: number | null;
  last_failed_count?: number | null;
};

export type CompetitorHotel = {
  id: number;
  group_id: number;
  role: "own" | "competitor";
  sort_order: number;
  alias: string;
  short_alias?: string | null;
  hotel_name?: string | null;
  ctrip_hotel_id: string;
  detail_url: string;
  enabled: boolean;
  last_captured_at?: string | null;
  last_status?: string | null;
};

export type CompetitorLatestSnapshot = {
  watch_group_key: string;
  run: {
    id: number;
    status: string;
    summary?: string | null;
    captured_at: string;
  };
  hotels: Array<{
    hotel_config: CompetitorHotel;
    snapshots: Record<string, {
      status: string;
      checkin_date?: string | null;
      checkout_date?: string | null;
      review_reason?: string | null;
      rates: Array<{
        room_type?: string | null;
        breakfast_kind: string;
        price_amount_yuan?: number | null;
        availability_status: string;
        availability_text?: string | null;
        is_trusted: boolean;
        source?: string | null;
      }>;
    }>;
  }>;
  trusted_source: string;
};

export type CompetitorPriceTrendPoint = {
  series_id: string;
  bucket_at: string;
  captured_at: string;
  captured_date: string;
  run_id: number;
  hotel_config_id: number;
  hotel_role: "own" | "competitor";
  sort_order: number;
  hotel_alias: string;
  hotel_name: string;
  ctrip_hotel_id: string;
  date_label: string;
  checkin_date?: string | null;
  checkout_date?: string | null;
  breakfast_kind: string;
  breakfast_text?: string | null;
  room_type?: string | null;
  min_price_amount_yuan?: number | null;
  max_price_amount_yuan?: number | null;
  trusted_rate_count: number;
  room_type_count: number;
  availability_statuses: string[];
  availability_texts: string[];
};

export type CompetitorPriceTrend = {
  status: string;
  summary: string;
  watch_group_key: string;
  granularity: "run" | "hour" | "day";
  days: number;
  generated_at: string;
  point_count: number;
  trusted_source: string;
  series: Array<{
    series_id: string;
    hotel_config_id: number;
    hotel_role: "own" | "competitor";
    sort_order: number;
    hotel_alias: string;
    hotel_name: string;
    ctrip_hotel_id: string;
    date_label: string;
    checkin_date?: string | null;
    checkout_date?: string | null;
    breakfast_kind: string;
    room_type?: string | null;
    room_types?: string[];
    room_type_changed?: boolean;
    points: Array<{
      bucket_at: string;
      captured_at: string;
      price_amount_yuan?: number | null;
      room_type?: string | null;
      availability_statuses: string[];
    }>;
  }>;
  points: CompetitorPriceTrendPoint[];
};

export async function listCompetitorGroups(): Promise<CompetitorWatchGroup[]> {
  const payload = await apiRequest<{ watch_groups: CompetitorWatchGroup[] }>(
    "/api/v1/ctrip-ebooking/competitor-watch-groups",
  );
  return payload.watch_groups;
}

export async function createCompetitorGroup(input: {
  group_key: string;
  name: string;
  brief_title?: string | null;
  price_change_alert_title?: string | null;
  platform_account_id?: string;
  browser_session_id?: string;
}): Promise<CompetitorWatchGroup> {
  const payload = await apiRequest<{ watch_group: CompetitorWatchGroup }>(
    "/api/v1/ctrip-ebooking/competitor-watch-groups",
    { method: "POST", body: JSON.stringify(input) },
  );
  return payload.watch_group;
}

export async function patchCompetitorGroup(
  groupKey: string,
  input: Partial<CompetitorWatchGroup> & {
    clear_binding?: boolean;
    clear_pricing_binding?: boolean;
    feishu_webhook_url?: string;
    feishu_mobile_webhook_url?: string;
    feishu_desktop_webhook_url?: string;
    feishu_chat_id?: string | null;
  },
): Promise<CompetitorWatchGroup> {
  const payload = await apiRequest<{ watch_group: CompetitorWatchGroup }>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
  return payload.watch_group;
}

export async function getCompetitorBrowserPool(groupKey: string): Promise<CompetitorBrowserPool> {
  const payload = await apiRequest<{ browser_pool: CompetitorBrowserPool }>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/browser-pool`,
  );
  return payload.browser_pool;
}

export async function putCompetitorBrowserPool(
  groupKey: string,
  members: CompetitorBrowserPoolMemberInput[],
): Promise<CompetitorBrowserPool> {
  const payload = await apiRequest<{ browser_pool: CompetitorBrowserPool }>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/browser-pool`,
    { method: "PUT", body: JSON.stringify({ members }) },
  );
  return payload.browser_pool;
}

export async function deleteCompetitorGroup(groupKey: string): Promise<CompetitorWatchGroup> {
  const payload = await apiRequest<{ watch_group: CompetitorWatchGroup }>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}`,
    { method: "DELETE" },
  );
  return payload.watch_group;
}

export async function listCompetitorHotels(
  groupKey: string,
  options?: { includeDisabled?: boolean; signal?: AbortSignal },
): Promise<CompetitorHotel[]> {
  const includeDisabled = options?.includeDisabled ? "true" : "false";
  const signal = options?.signal;
  const payload = await apiRequest<{ hotels: CompetitorHotel[] }>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/hotels?include_disabled=${includeDisabled}`,
    { signal },
  );
  return payload.hotels;
}

export async function upsertCompetitorHotel(
  groupKey: string,
  input: {
    role: "own" | "competitor";
    alias: string;
    short_alias?: string | null;
    detail_url: string;
    sort_order: number;
    enabled: boolean;
  },
): Promise<CompetitorHotel> {
  const payload = await apiRequest<{ hotel: CompetitorHotel }>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/hotels`,
    { method: "POST", body: JSON.stringify(input) },
  );
  return payload.hotel;
}

export async function patchCompetitorHotel(
  groupKey: string,
  hotelId: number,
  input: Partial<CompetitorHotel>,
): Promise<CompetitorHotel> {
  const payload = await apiRequest<{ hotel: CompetitorHotel }>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/hotels/${hotelId}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
  return payload.hotel;
}

export async function deleteCompetitorHotel(groupKey: string, hotelId: number): Promise<CompetitorHotel> {
  const payload = await apiRequest<{ hotel: CompetitorHotel }>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/hotels/${hotelId}`,
    { method: "DELETE" },
  );
  return payload.hotel;
}

export async function runCompetitorNow(
  groupKey: string,
  pushToFeishu: boolean,
  options?: { actorId?: string },
): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/run-now?push_to_feishu=${pushToFeishu}`,
    { method: "POST", body: JSON.stringify({ actor_id: options?.actorId || "operator" }) },
  );
}

export async function getLatestCompetitorSnapshot(
  groupKey: string,
  options?: { includeDisabled?: boolean; signal?: AbortSignal },
): Promise<CompetitorLatestSnapshot> {
  const includeDisabled = options?.includeDisabled ? "true" : "false";
  const signal = options?.signal;
  return apiRequest<CompetitorLatestSnapshot>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/latest?include_disabled=${includeDisabled}`,
    { signal },
  );
}

export async function getCompetitorPriceTrend(
  groupKey: string,
  options?: {
    includeDisabled?: boolean;
    signal?: AbortSignal;
    days?: number;
    granularity?: "run" | "hour" | "day";
  },
): Promise<CompetitorPriceTrend> {
  const includeDisabled = options?.includeDisabled ? "true" : "false";
  const signal = options?.signal;
  const days = options?.days ?? 1;
  const granularity = options?.granularity ?? "run";
  return apiRequest<CompetitorPriceTrend>(
    `/api/v1/ctrip-ebooking/competitor-watch-groups/${groupKey}/price-trend?days=${days}&granularity=${granularity}&include_disabled=${includeDisabled}`,
    { signal },
  );
}
