import { apiRequest } from "./http.ts";

export type EbkPlatform = "ctrip" | "meituan" | "fliggy";

export type EbkSnapshotStatus =
  | "success"
  | "partial"
  | "human_required"
  | "needs_review"
  | "error"
  | string;

export type UnifiedEbkSnapshotPayload = {
  platform: EbkPlatform | string;
  platform_account_id: string;
  browser_session_id?: string;
  read_at?: string;
  status?: EbkSnapshotStatus;
  summary?: string;
  datasets?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  platform_data?: Record<string, unknown>;
};

export type EbkSnapshotRecord = {
  id?: number | string;
  platform: EbkPlatform | string;
  platform_account_id: string;
  browser_session_id?: string | null;
  status: EbkSnapshotStatus;
  dataset_count?: number | null;
  payload_json?: UnifiedEbkSnapshotPayload | null;
  created_at?: string | null;
};

export type EbkSnapshotReadAllResult = {
  status: EbkSnapshotStatus;
  summary?: string;
  results?: {
    ctrip?: { status?: EbkSnapshotStatus; snapshot_id?: number | string | null; summary?: string };
    meituan?: { status?: EbkSnapshotStatus; snapshot_id?: number | string | null; summary?: string };
    fliggy?: { status?: EbkSnapshotStatus; snapshot_id?: number | string | null; summary?: string };
  };
  feishu_push?: Record<string, { status?: string; summary?: string }>;
};

export async function readAllEbkSnapshots(input: {
  ctripPlatformAccountId?: string;
  meituanPlatformAccountId?: string;
  fliggyPlatformAccountId?: string;
  platforms?: EbkPlatform[];
  actorId?: string;
  headless?: boolean;
  pushToFeishu?: boolean;
  feishuWatchGroupKey?: string;
}): Promise<EbkSnapshotReadAllResult> {
  return apiRequest<EbkSnapshotReadAllResult>("/api/v1/ebk-snapshots/read-all", {
    method: "POST",
    body: JSON.stringify({
      actor_id: input.actorId ?? "operator",
      ctrip_platform_account_id: input.ctripPlatformAccountId,
      meituan_platform_account_id: input.meituanPlatformAccountId,
      fliggy_platform_account_id: input.fliggyPlatformAccountId,
      platforms: input.platforms,
      headless: input.headless ?? false,
      push_to_feishu: input.pushToFeishu ?? false,
      feishu_watch_group_key: input.feishuWatchGroupKey,
    }),
  });
}

export async function getLatestEbkSnapshot(input: {
  platform: EbkPlatform;
  platformAccountId: string;
}): Promise<EbkSnapshotRecord> {
  const params = new URLSearchParams({
    platform: input.platform,
    platform_account_id: input.platformAccountId,
  });
  return apiRequest<EbkSnapshotRecord>(`/api/v1/ebk-snapshots/latest?${params.toString()}`);
}
