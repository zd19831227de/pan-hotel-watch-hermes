import { apiRequest } from "./http";

export type ReviewPlatform = "ctrip" | "meituan" | "fliggy";
export type ReviewCategory = "good" | "medium" | "bad";
export type AutoReplyMode = "off" | "semi_auto" | "full_auto";
export type ReplyStatus = "pending" | "replied" | "ignored";

export type ReviewItem = {
  id: number;
  platform: string;
  hotel_id: string;
  review_id: string;
  user_nickname?: string | null;
  score?: number | null;
  content?: string | null;
  review_time?: string | null;
  reply_status: string;
  reply_content?: string | null;
  replied_at?: string | null;
  category: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ReplyTemplate = {
  id: number;
  platform: string;
  browser_session_id?: string | null;
  category: string;
  name: string;
  content: string;
  score_range?: { min?: number | null; max?: number | null } | null;
  priority: number;
  enabled: boolean;
  source: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ReviewAutoConfig = {
  platform: string;
  browser_session_id?: string | null;
  platform_account_id?: string | null;
  display_name?: string | null;
  auto_reply_mode: string;
  auto_fetch_enabled: boolean;
  fetch_interval_minutes: number;
  auto_process_time?: string | null;
  last_auto_processed_at?: string | null;
  approval_chat_id?: string | null;
  feishu_chat_id?: string | null;
  last_fetched_at?: string | null;
  updated_at?: string | null;
};

export type ReviewStore = {
  browser_session_id: string;
  platform_account_id: string;
  display_name: string;
  configured: boolean;
  auto_reply_mode?: string;
  auto_fetch_enabled?: boolean;
  feishu_chat_id_configured?: boolean;
  last_fetched_at?: string | null;
};

export type ReviewGroupPlatform = {
  mounted: boolean;
  browser_session_id: string | null;
};

export type ReviewGroup = {
  group_key: string;
  name: string;
  // 店铺条目 shop_key 非空（group_key=shop_key）；null=未归属店铺的旧携程组。
  shop_key?: string | null;
  ctrip_watch_group_key?: string | null;
  feishu_chat_id_configured: boolean;
  platforms: {
    ctrip: ReviewGroupPlatform;
    meituan: ReviewGroupPlatform;
    fliggy: ReviewGroupPlatform;
  };
};

export type ReviewTask = {
  id: number;
  platform: string;
  task_type: string;
  status: string;
  review_count: number;
  result?: Record<string, unknown> | null;
  error_msg?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
};

export type ReviewCatalog = {
  platforms: string[];
  categories: string[];
  auto_reply_modes: string[];
  reply_statuses: string[];
  reply_max_length: number;
  adapter_ready_platforms: string[];
};

export type FetchReviewsResult = {
  fetch: {
    status: string;
    pending_count?: number;
    inserted?: number;
    updated?: number;
    total?: number;
    error?: string | null;
  };
  process: {
    status: string;
    mode?: string;
    pushed_cards?: number;
    pending_count?: number;
    approvals?: number[];
    error?: string | null;
  } | null;
};

export type TemplateSavePayload = {
  id?: number | null;
  platform: string;
  browser_session_id?: string | null;
  category: string;
  name: string;
  content: string;
  score_range?: { min?: number; max?: number } | null;
  priority: number;
  enabled: boolean;
};

export async function listReviews(
  platform: string,
  options?: {
    reply_status?: string;
    category?: string;
    browser_session_id?: string;
    limit?: number;
    signal?: AbortSignal;
  },
): Promise<{ reviews: ReviewItem[]; catalog: ReviewCatalog | null }> {
  const params = new URLSearchParams({ platform });
  if (options?.reply_status) params.set("reply_status", options.reply_status);
  if (options?.category) params.set("category", options.category);
  if (options?.browser_session_id) params.set("browser_session_id", options.browser_session_id);
  // 后端 limit 默认 100：不显式传满量时，超过 100 条的评价会被截断且总数失真。
  params.set("limit", String(options?.limit ?? 500));
  const response = await apiRequest<{ reviews: ReviewItem[]; catalog: ReviewCatalog | null }>(
    `/api/v1/reviews?${params.toString()}`,
    { signal: options?.signal },
  );
  return response;
}

export async function listReviewStores(platform: string, signal?: AbortSignal): Promise<ReviewStore[]> {
  const params = new URLSearchParams({ platform });
  const response = await apiRequest<{ stores: ReviewStore[] }>(
    `/api/v1/reviews/stores?${params.toString()}`,
    { signal },
  );
  return response.stores;
}

export async function listReviewGroups(signal?: AbortSignal): Promise<ReviewGroup[]> {
  const response = await apiRequest<{ groups: ReviewGroup[] }>(
    "/api/v1/reviews/groups",
    { signal },
  );
  return response.groups;
}

export async function fetchReviewsNow(
  platform: string,
  browserSessionId?: string | null,
): Promise<FetchReviewsResult> {
  return apiRequest<FetchReviewsResult>("/api/v1/reviews/fetch", {
    method: "POST",
    body: JSON.stringify({ platform, browser_session_id: browserSessionId || null }),
  });
}

export async function listTemplates(
  platform: string,
  options?: { category?: string; browser_session_id?: string; signal?: AbortSignal },
): Promise<ReplyTemplate[]> {
  const params = new URLSearchParams({ platform });
  if (options?.category) params.set("category", options.category);
  if (options?.browser_session_id) params.set("browser_session_id", options.browser_session_id);
  const response = await apiRequest<{ templates: ReplyTemplate[] }>(
    `/api/v1/reviews/templates?${params.toString()}`,
    { signal: options?.signal },
  );
  return response.templates;
}

export async function saveTemplate(payload: TemplateSavePayload): Promise<ReplyTemplate> {
  const response = await apiRequest<{ template: ReplyTemplate }>("/api/v1/reviews/templates", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return response.template;
}

export async function deleteTemplate(templateId: number): Promise<{ deleted: boolean }> {
  return apiRequest(`/api/v1/reviews/templates/${templateId}`, { method: "DELETE" });
}

export async function importHistory(
  platform: string,
  browserSessionId?: string | null,
): Promise<{
  result: { status: string; templates_created?: number; by_category?: Record<string, number>; error?: string | null };
}> {
  return apiRequest("/api/v1/reviews/import-history", {
    method: "POST",
    body: JSON.stringify({ platform, browser_session_id: browserSessionId || null }),
  });
}

export async function autoUpdateStoreTemplates(
  platform: string,
  browserSessionId: string,
): Promise<{
  result: {
    status: string;
    generator?: string;
    templates_removed?: number;
    templates_created?: number;
    by_category?: Record<string, number>;
    history_reviews?: number;
    error?: string | null;
  };
}> {
  return apiRequest("/api/v1/reviews/auto-update-templates", {
    method: "POST",
    body: JSON.stringify({ platform, browser_session_id: browserSessionId }),
  });
}

export async function processPending(
  platform: string,
  browserSessionId?: string | null,
): Promise<{
  process: {
    status: string;
    mode?: string;
    pushed_cards?: number;
    pending_count?: number;
    submitted?: number;
    by_category?: Record<string, number>;
    summary_push?: { status?: string; chat_id?: string | null };
    error?: string | null;
  };
}> {
  return apiRequest("/api/v1/reviews/process-pending", {
    method: "POST",
    body: JSON.stringify({ platform, browser_session_id: browserSessionId || null }),
  });
}

export async function getAutoConfig(
  platform: string,
  browserSessionId?: string | null,
  signal?: AbortSignal,
): Promise<ReviewAutoConfig> {
  const params = new URLSearchParams({ platform });
  if (browserSessionId) params.set("browser_session_id", browserSessionId);
  const response = await apiRequest<{ config: ReviewAutoConfig }>(
    `/api/v1/reviews/auto-config?${params.toString()}`,
    { signal },
  );
  return response.config;
}

export async function updateAutoConfig(
  payload: Partial<{
    auto_reply_mode: string;
    auto_fetch_enabled: boolean;
    auto_process_time: string;
    approval_chat_id: string | null;
  }> & {
    platform: string;
    browser_session_id?: string | null;
    ctrip_watch_group_key?: string | null;
  },
): Promise<ReviewAutoConfig> {
  const response = await apiRequest<{ config: ReviewAutoConfig }>("/api/v1/reviews/auto-config", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return response.config;
}

export async function listTasks(platform: string, signal?: AbortSignal): Promise<ReviewTask[]> {
  const params = new URLSearchParams({ platform });
  const response = await apiRequest<{ tasks: ReviewTask[] }>(
    `/api/v1/reviews/tasks?${params.toString()}`,
    { signal },
  );
  return response.tasks;
}
