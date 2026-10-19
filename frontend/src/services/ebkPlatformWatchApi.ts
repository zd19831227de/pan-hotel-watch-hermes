import { apiRequest } from "./http";

export type EbkPlatformWatchGroup = {
  id?: number;
  group_key: string;
  name: string;
  brief_title?: string | null;
  platform_account_id: string;
  browser_session_id?: string | null;
  session_status?: "registered" | "unregistered" | "none";
  shop_key?: string | null;
  ctrip_watch_group_key?: string | null;
  anchor_status?: "anchored" | "legacy_unanchored";
  feishu_chat_id?: string | null;
  feishu_webhook_url_configured?: boolean;
  timezone?: string;
  schedule_mode: string;
  schedule_start_time: string;
  schedule_end_time: string;
  schedule_interval_minutes: number;
  auto_poll_enabled: boolean;
  enabled: boolean;
  last_polled_at?: string | null;
  scheduler_status?: string | null;
  scheduler_heartbeat_age_seconds?: number | null;
};

export type EbkPlatformSchedulerStatus = {
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

export type EbkPlatformWatchGroupPatch = {
  name?: string;
  brief_title?: string | null;
  browser_session_id?: string;
  shop_key?: string;
  ctrip_watch_group_key?: string;
  feishu_chat_id?: string;
  feishu_webhook_url?: string;
  timezone?: string;
  schedule_mode?: string;
  schedule_start_time?: string;
  schedule_end_time?: string;
  schedule_interval_minutes?: number;
  auto_poll_enabled?: boolean;
  enabled?: boolean;
};

export type EbkPlatformRunNowResult = {
  watch_group_key: string;
  status: string;
  summary?: string | null;
};

export type EbkPlatformWatchApi = {
  listWatchGroups(): Promise<EbkPlatformWatchGroup[]>;
  createWatchGroup(input: EbkPlatformWatchGroupCreate): Promise<EbkPlatformWatchGroup>;
  patchWatchGroup(
    groupKey: string,
    patch: EbkPlatformWatchGroupPatch,
  ): Promise<EbkPlatformWatchGroup>;
  deleteWatchGroup(groupKey: string): Promise<EbkPlatformWatchGroup>;
  runWatchGroupNow(
    groupKey: string,
    options?: { pushToFeishu?: boolean },
  ): Promise<EbkPlatformRunNowResult>;
  schedulerStatus(): Promise<EbkPlatformSchedulerStatus>;
};

export type EbkPlatformWatchGroupCreate = {
  group_key: string;
  name: string;
  brief_title?: string | null;
  shop_key?: string;
  ctrip_watch_group_key?: string;
  feishu_chat_id?: string;
  feishu_webhook_url?: string;
  browser_session_id?: string;
  timezone?: string;
  schedule_mode?: string;
  schedule_start_time?: string;
  schedule_end_time?: string;
  schedule_interval_minutes?: number;
  auto_poll_enabled?: boolean;
  enabled?: boolean;
};

export function createEbkPlatformWatchApi(
  platform: "meituan" | "fliggy",
): EbkPlatformWatchApi {
  const base = `/api/v1/${platform}-ebk-watch`;
  return {
    listWatchGroups: () =>
      apiRequest<{ watch_groups: EbkPlatformWatchGroup[] }>(`${base}/watch-groups`).then(
        (payload) => payload.watch_groups ?? [],
      ),
    createWatchGroup: (input) =>
      apiRequest<{ watch_group: EbkPlatformWatchGroup }>(`${base}/watch-groups`, {
        method: "POST",
        body: JSON.stringify(input),
      }).then((payload) => payload.watch_group),
    patchWatchGroup: (groupKey, patch) =>
      apiRequest<{ watch_group: EbkPlatformWatchGroup }>(
        `${base}/watch-groups/${groupKey}`,
        { method: "PATCH", body: JSON.stringify(patch) },
      ).then((payload) => payload.watch_group),
    deleteWatchGroup: (groupKey) =>
      apiRequest<{ watch_group: EbkPlatformWatchGroup }>(
        `${base}/watch-groups/${groupKey}`,
        { method: "DELETE" },
      ).then((payload) => payload.watch_group),
    runWatchGroupNow: (groupKey, options) =>
      apiRequest<EbkPlatformRunNowResult>(
        `${base}/watch-groups/${groupKey}/run-now?push_to_feishu=${
          options?.pushToFeishu ?? true
        }`,
        { method: "POST" },
      ),
    schedulerStatus: () => apiRequest<EbkPlatformSchedulerStatus>(`${base}/scheduler-status`),
  };
}
