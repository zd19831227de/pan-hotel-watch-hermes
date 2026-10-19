import { apiRequest, getRuntimeApiKey } from "./http";

export type DistillSourceType = "text" | "image_url" | "video_url";
export type DistillPlatform = "doubao" | "qwen";

export type DistillSource = {
  id: number;
  source_type: DistillSourceType;
  content: string;
  platform: DistillPlatform;
  title: string;
  distill_prompt: string;
  schedule_interval_minutes: number;
  enabled: boolean;
  last_run_at: string | null;
  [key: string]: unknown;
};

export type DistillRun = {
  id: number;
  source_id: number | null;
  platform: DistillPlatform;
  source_type: DistillSourceType;
  status: string;
  summary: string;
  knowledge_title: string;
  rag_document_id: number | null;
  reject_reason: string | null;
  created_at: string;
  [key: string]: unknown;
};

export type DistillMeta = {
  source_types: DistillSourceType[];
  platforms: DistillPlatform[];
  admission_rule: string;
};

export async function fetchDistillSources(enabledOnly = false): Promise<DistillSource[]> {
  const data = await apiRequest<{ sources: DistillSource[] }>(
    `/api/v1/distill/sources${enabledOnly ? "?enabled_only=true" : ""}`,
  );
  return data.sources ?? [];
}

export async function createDistillSource(input: {
  source_type: DistillSourceType;
  content: string;
  platform: DistillPlatform;
  title?: string;
  distill_prompt?: string;
  schedule_interval_minutes?: number;
  enabled?: boolean;
}): Promise<DistillSource> {
  return apiRequest<DistillSource>("/api/v1/distill/sources", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function patchDistillSource(
  sourceId: number,
  input: { enabled?: boolean; schedule_interval_minutes?: number },
): Promise<DistillSource> {
  return apiRequest<DistillSource>(`/api/v1/distill/sources/${sourceId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteDistillSource(sourceId: number): Promise<void> {
  await apiRequest<{ status: string }>(`/api/v1/distill/sources/${sourceId}`, {
    method: "DELETE",
  });
}

export async function triggerDistillRun(input: {
  source_type: DistillSourceType;
  content: string;
  platform: DistillPlatform;
  distill_prompt?: string;
  title?: string;
}): Promise<DistillRun> {
  return apiRequest<DistillRun>("/api/v1/distill/runs", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function triggerSourceRun(sourceId: number): Promise<DistillRun> {
  return apiRequest<DistillRun>(`/api/v1/distill/sources/${sourceId}/run`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function fetchDistillRuns(limit = 20): Promise<DistillRun[]> {
  const data = await apiRequest<{ runs: DistillRun[] }>(
    `/api/v1/distill/runs?limit=${limit}`,
  );
  return data.runs ?? [];
}

export async function fetchDistillMeta(): Promise<DistillMeta> {
  return apiRequest<DistillMeta>("/api/v1/distill/meta");
}

export async function exportDistillRun(runId: number): Promise<Blob> {
  const headers: Record<string, string> = {};
  const apiKey = getRuntimeApiKey();
  if (apiKey) {
    headers["X-Pan-Hotel-API-Key"] = apiKey;
  }
  const response = await fetch(`/api/v1/distill/runs/${runId}/export`, { headers });
  if (!response.ok) {
    throw new Error(`导出失败（HTTP ${response.status}）`);
  }
  return response.blob();
}
