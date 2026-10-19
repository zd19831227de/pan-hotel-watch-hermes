import { apiRequest } from "./http";

export type OperatorRecentActivity = {
  [key: string]: unknown;
};

export async function fetchOperatorRecentActivity(options: {
  ebkWatchGroupKey?: string;
  competitorWatchGroupKey?: string;
  signal?: AbortSignal;
} = {}): Promise<OperatorRecentActivity> {
  const params = new URLSearchParams();
  if (options.ebkWatchGroupKey) {
    params.set("ebk_watch_group_key", options.ebkWatchGroupKey);
  }
  if (options.competitorWatchGroupKey) {
    params.set("competitor_watch_group_key", options.competitorWatchGroupKey);
  }
  const query = params.toString();
  return apiRequest<OperatorRecentActivity>(
    `/api/v1/ops/operator-recent-activity${query ? `?${query}` : ""}`,
    { signal: options.signal },
  );
}
