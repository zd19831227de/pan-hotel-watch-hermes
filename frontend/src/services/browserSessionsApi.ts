import { apiRequest } from "./http";

export type BrowserIntendedUse = "ebk" | "competitor_price" | "llm_chat";
export type BrowserConnectionMode = "direct" | "non_direct" | "unknown";

export type BrowserSession = {
  browser_session_id: string;
  chat_url?: string | null;
  platform_account_id: string;
  display_name: string;
  platform: string;
  session_port: number;
  intended_use: BrowserIntendedUse;
  connection_mode: BrowserConnectionMode;
  can_auto_write_price: boolean;
  shop_key?: string | null;
  pricing_feishu_chat_id_configured?: boolean;
  pricing_feishu_chat_id_masked?: string | null;
  login_state: string;
  human_verification_status: string;
  last_heartbeat_at: string;
  last_action: string;
  human_required_reason?: string | null;
  risk_flags: string[];
  competitor_pool_usage?: Array<{
    watch_group_key: string;
    watch_group_name?: string | null;
    browser_rotation_mode?: string | null;
    pool_enabled?: boolean;
    member_enabled?: boolean;
    sort_order?: number;
    last_status?: string | null;
  }>;
};

export type BrowserSessionCreate = {
  platform_account_id: string;
  display_name: string;
  platform: string;
  intended_use: BrowserIntendedUse;
  connection_mode: BrowserConnectionMode;
  shop_key?: string | null;
  pricing_feishu_chat_id?: string | null;
  chat_url?: string;
  session_port: number;
  browser_session_id?: string;
};

export async function listBrowserSessions(): Promise<BrowserSession[]> {
  const payload = await apiRequest<{ sessions: BrowserSession[] }>("/api/v1/browser-sessions");
  return payload.sessions;
}

export async function createBrowserSession(input: BrowserSessionCreate): Promise<BrowserSession> {
  return apiRequest<BrowserSession>("/api/v1/browser-sessions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function patchBrowserSession(
  browserSessionId: string,
  input: Partial<BrowserSessionCreate>,
): Promise<BrowserSession> {
  return apiRequest<BrowserSession>(`/api/v1/browser-sessions/${browserSessionId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteBrowserSession(browserSessionId: string): Promise<BrowserSession> {
  return apiRequest<BrowserSession>(`/api/v1/browser-sessions/${browserSessionId}`, {
    method: "DELETE",
  });
}

export async function requestBrowserVerification(browserSessionId: string): Promise<BrowserSession> {
  return apiRequest<BrowserSession>(`/api/v1/browser-sessions/${browserSessionId}/request-verification`, {
    method: "POST",
    body: JSON.stringify({ actor_id: "operator" }),
  });
}
