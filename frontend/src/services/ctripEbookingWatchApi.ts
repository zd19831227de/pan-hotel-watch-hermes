import { apiRequest } from "./http";

export type DataCenterDataset = {
  id: number;
  dataset_id: string;
  dataset_label: string;
  enabled: boolean;
  push_enabled: boolean;
  last_status?: string | null;
  last_failure_reason?: string | null;
  last_captured_at?: string | null;
};

export type DataCenterWatchGroup = {
  id?: number;
  group_key: string;
  name: string;
  brief_title?: string | null;
  // 店铺归属（P4）：每店最多一个主携程锚点；null=未归属。
  shop_key?: string | null;
  daily_brief_title?: string | null;
  daily_brief_push_time?: string | null;
  daily_brief_enabled?: boolean | null;
  daily_brief_last_pushed_at?: string | null;
  platform_account_id: string;
  browser_session_id?: string | null;
  session_status?: "registered" | "unregistered" | "none";
  feishu_chat_id_configured?: boolean;
  feishu_chat_id_masked?: string | null;
  feishu_webhook_configured: boolean;
  feishu_webhook_masked?: string | null;
  retention_days: number;
  query_interval_minutes: number;
  auto_poll_enabled: boolean;
  enabled: boolean;
  scheduler_status?: DataCenterSchedulerStatus | null;
  last_polled_at?: string | null;
  datasets?: DataCenterDataset[];
  latest_run?: DataCenterRun | null;
};

export type DataCenterSchedulerStatus = {
  status?: string | null;
  process_confirmed?: boolean | null;
  updated_at?: string | null;
  last_heartbeat_at?: string | null;
  heartbeat_age_seconds?: number | null;
  last_due_group_count?: number | null;
  last_due_daily_brief_count?: number | null;
  last_result_count?: number | null;
  last_success_count?: number | null;
  last_failed_count?: number | null;
};

export type DataCenterRun = {
  id: number;
  status: string;
  summary?: string | null;
  dataset_count: number;
  success_count: number;
  failed_count: number;
  human_required_count: number;
  captured_at: string;
  push_result?: DataCenterPushResult | null;
};

export type DataCenterDatasetResult = {
  dataset_id?: string | null;
  dataset_label?: string | null;
  status?: string | null;
  summary?: string | null;
  failure_reason?: string | null;
  captured_at?: string | null;
  data?: {
    dataset?: string | null;
    updated_at?: string | null;
    metrics?: Record<string, unknown> | null;
  } | null;
};

export type DataCenterLatestSnapshot = {
  watch_group_key?: string;
  run: DataCenterRun | null;
  dataset_results: DataCenterDatasetResult[];
};

export type DataCenterPushResult = {
  status?: string | null;
  summary?: string | null;
};

export type DataCenterRunNowResult = {
  status?: string | null;
  summary?: string | null;
  feishu_push?: DataCenterPushResult | null;
};

export async function listDataCenterGroups(): Promise<DataCenterWatchGroup[]> {
  const payload = await apiRequest<{ watch_groups: DataCenterWatchGroup[] }>(
    "/api/v1/ctrip-ebooking/data-center-watch-groups",
  );
  return payload.watch_groups;
}

export async function createDataCenterGroup(input: {
  group_key: string;
  name: string;
  brief_title?: string | null;
  daily_brief_title?: string | null;
  daily_brief_push_time?: string | null;
  daily_brief_enabled?: boolean | null;
  platform_account_id?: string;
  browser_session_id?: string;
  feishu_chat_id?: string | null;
  feishu_webhook_url?: string;
  shop_key?: string | null;
}): Promise<DataCenterWatchGroup> {
  const payload = await apiRequest<{ watch_group: DataCenterWatchGroup }>(
    "/api/v1/ctrip-ebooking/data-center-watch-groups",
    { method: "POST", body: JSON.stringify(input) },
  );
  return payload.watch_group;
}

export async function patchDataCenterGroup(
  groupKey: string,
  input: Partial<DataCenterWatchGroup> & { feishu_chat_id?: string | null; feishu_webhook_url?: string },
): Promise<DataCenterWatchGroup> {
  const payload = await apiRequest<{ watch_group: DataCenterWatchGroup }>(
    `/api/v1/ctrip-ebooking/data-center-watch-groups/${groupKey}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
  return payload.watch_group;
}

export async function deleteDataCenterGroup(groupKey: string): Promise<DataCenterWatchGroup> {
  const payload = await apiRequest<{ watch_group: DataCenterWatchGroup }>(
    `/api/v1/ctrip-ebooking/data-center-watch-groups/${groupKey}`,
    { method: "DELETE" },
  );
  return payload.watch_group;
}

export async function listDataCenterDatasets(
  groupKey: string,
  signal?: AbortSignal,
): Promise<DataCenterDataset[]> {
  const payload = await apiRequest<{ datasets: DataCenterDataset[] }>(
    `/api/v1/ctrip-ebooking/data-center-watch-groups/${groupKey}/datasets`,
    { signal },
  );
  return payload.datasets;
}

export async function patchDataCenterDataset(
  groupKey: string,
  datasetId: string,
  input: Partial<DataCenterDataset>,
): Promise<DataCenterDataset> {
  const payload = await apiRequest<{ dataset: DataCenterDataset }>(
    `/api/v1/ctrip-ebooking/data-center-watch-groups/${groupKey}/datasets/${datasetId}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
  return payload.dataset;
}

export async function runDataCenterNow(
  groupKey: string,
  options?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<DataCenterRunNowResult> {
  return apiRequest<DataCenterRunNowResult>(
    `/api/v1/ctrip-ebooking/data-center-watch-groups/${groupKey}/run-now`,
    { method: "POST", ...options },
  );
}

export async function runDailyBriefNow(groupKey: string): Promise<DataCenterRunNowResult> {
  return apiRequest<DataCenterRunNowResult>(
    `/api/v1/ctrip-ebooking/data-center-watch-groups/${groupKey}/daily-brief/run-now`,
    { method: "POST" },
  );
}

export async function listDataCenterWatchRuns(
  groupKey: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<Array<Record<string, unknown>>> {
  const payload = await apiRequest<{ runs?: Array<Record<string, unknown>> }>(
    `/api/v1/ctrip-ebooking/data-center-watch-groups/${groupKey}/runs?limit=${limit}`,
    { signal },
  );
  return payload.runs ?? [];
}

export async function getLatestDataCenterRun(
  groupKey: string,
  signal?: AbortSignal,
): Promise<DataCenterLatestSnapshot> {
  return apiRequest(
    `/api/v1/ctrip-ebooking/data-center-watch-groups/${groupKey}/latest`,
    { signal },
  );
}
