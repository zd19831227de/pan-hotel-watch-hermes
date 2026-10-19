import { apiRequest } from "./http.ts";

export type PageHealthAlert = {
  id: number;
  platform: string;
  target: string;
  problem_key: string;
  level: "hard_fail" | "partial_fail" | "layout_changed";
  summary: string;
  created_at: string | null;
  [key: string]: unknown;
};

export async function fetchPageHealthAlerts(input?: {
  platform?: string;
  level?: "hard_fail" | "partial_fail" | "layout_changed";
  limit?: number;
}): Promise<PageHealthAlert[]> {
  const params = new URLSearchParams();
  if (input?.platform) params.set("platform", input.platform);
  if (input?.level) params.set("level", input.level);
  if (input?.limit) params.set("limit", String(input.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const data = await apiRequest<{ alerts: PageHealthAlert[] }>(
    `/api/v1/page-health/alerts${suffix}`,
  );
  return data.alerts ?? [];
}

export async function deletePageHealthAlert(alertId: number): Promise<void> {
  await apiRequest<{ status: string; deleted_id: number }>(
    `/api/v1/page-health/alerts/${alertId}`,
    { method: "DELETE" },
  );
}
