import { apiRequest } from "./http";

export type EbkGlobalPushConfig = {
  id: number;
  ctrip_watch_group_key: string;
  // 店铺归属（后端从锚点携程组派生，展示用；读取解析以锚点组为准）。
  shop_key?: string | null;
  name: string;
  enabled: boolean;
  enabled_platforms: string[];
  schedule_mode: "fixed_slots" | "last_run_interval";
  push_times: string[];
  interval_minutes: number;
  window_start_time: string;
  window_end_time: string;
  card_title_business: string | null;
  card_title_promotion: string | null;
  card_title_advertising: string | null;
  last_attempted_at: string | null;
  last_successful_push_at: string | null;
  next_run_at: string | null;
  consecutive_failure_count: number;
};

export type EbkGlobalPushRun = {
  id: number;
  config_id: number;
  trigger: string;
  schedule_key: string;
  status: string;
  read_finished_at: string | null;
  push_finished_at: string | null;
  platform_results: Record<string, { status?: string; summary?: string }>;
  card_results: Record<string, unknown>;
  error_summary: string | null;
};

export type EbkGlobalPushPlatformTarget = {
  platform: string;
  status: string;
  account_name: string | null;
  summary: string;
};

export async function listEbkGlobalPushConfigs(): Promise<EbkGlobalPushConfig[]> {
  const data = await apiRequest<{ configs: EbkGlobalPushConfig[] }>(
    "/api/v1/ebk-global-push/configs",
  );
  return data.configs ?? [];
}

export async function getEbkGlobalPushConfigDetail(configId: number): Promise<{
  config: EbkGlobalPushConfig;
  platform_targets: EbkGlobalPushPlatformTarget[];
}> {
  return apiRequest(`/api/v1/ebk-global-push/configs/${configId}`);
}

export async function createEbkGlobalPushConfig(body: Record<string, unknown>): Promise<EbkGlobalPushConfig> {
  const data = await apiRequest<{ config: EbkGlobalPushConfig }>(
    "/api/v1/ebk-global-push/configs",
    { method: "POST", body: JSON.stringify(body) },
  );
  return data.config;
}

export async function patchEbkGlobalPushConfig(
  configId: number,
  patch: Record<string, unknown>,
): Promise<EbkGlobalPushConfig> {
  const data = await apiRequest<{ config: EbkGlobalPushConfig }>(
    `/api/v1/ebk-global-push/configs/${configId}`,
    { method: "PATCH", body: JSON.stringify(patch) },
  );
  return data.config;
}

export async function softDeleteEbkGlobalPushConfig(configId: number): Promise<void> {
  await apiRequest(`/api/v1/ebk-global-push/configs/${configId}`, { method: "DELETE" });
}

export async function runEbkGlobalPushNow(configId: number): Promise<{
  run_id: number;
  status: string;
  summary: string;
}> {
  return apiRequest(`/api/v1/ebk-global-push/configs/${configId}/run-now`, {
    method: "POST",
  });
}

export async function listEbkGlobalPushRuns(configId: number, limit = 10): Promise<EbkGlobalPushRun[]> {
  const data = await apiRequest<{ runs: EbkGlobalPushRun[] }>(
    `/api/v1/ebk-global-push/configs/${configId}/runs?limit=${limit}`,
  );
  return data.runs ?? [];
}

export async function getEbkGlobalPushSchedulerStatus(): Promise<{
  name: string;
  enabled: boolean;
  status: string;
  last_heartbeat_at: string | null;
}> {
  return apiRequest("/api/v1/ebk-global-push/scheduler-status");
}

// 每日 09:30 昨日快照日终轮次的运行记录（「最新运行日志」展示用，只读）。
export type EbkDaySnapshotRun = {
  id: number;
  status: string;
  platform_results: Record<string, { status?: string; detail?: string }>;
  ok_count: number;
  not_available_count: number;
  error_count: number;
  error_summary: string | null;
  triggered_at: string | null;
};

export async function listEbkDaySnapshotRuns(limit = 10): Promise<EbkDaySnapshotRun[]> {
  const data = await apiRequest<{ runs: EbkDaySnapshotRun[] }>(
    `/api/v1/ebk-period-reads/day-snapshot/runs?limit=${limit}`,
  );
  return data.runs ?? [];
}
