import { apiRequest } from "./http.ts";

export type MeituanOpenLoginResult = {
  status: string;
  summary: string;
  platform: string;
  platform_account_id: string;
  browser_session_id: string;
  login_url: string;
  manual_login_required: boolean;
  next_action?: string | null;
};

export async function openMeituanLogin(input: {
  platformAccountId: string;
  browserSessionId?: string;
}): Promise<MeituanOpenLoginResult> {
  return apiRequest<MeituanOpenLoginResult>(
    `/api/v1/meituan-ebooking/sessions/${encodeURIComponent(input.platformAccountId)}/open-login`,
    {
      method: "POST",
      body: JSON.stringify({
        actor_id: "operator",
        browser_session_id: input.browserSessionId || undefined,
        headless: false,
      }),
    },
  );
}

export async function checkMeituanLoginState(input: {
  platformAccountId: string;
  browserSessionId?: string;
}): Promise<MeituanOpenLoginResult> {
  return apiRequest<MeituanOpenLoginResult>(
    `/api/v1/meituan-ebooking/sessions/${encodeURIComponent(input.platformAccountId)}/check-login-state`,
    {
      method: "POST",
      body: JSON.stringify({
        actor_id: "operator",
        browser_session_id: input.browserSessionId || undefined,
        headless: false,
      }),
    },
  );
}

export async function readMeituanDataCenter(input: {
  platformAccountId: string;
  browserSessionId?: string;
}): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>(
    `/api/v1/meituan-ebooking/sessions/${encodeURIComponent(input.platformAccountId)}/read-data-center`,
    {
      method: "POST",
      body: JSON.stringify({
        actor_id: "operator",
        browser_session_id: input.browserSessionId || undefined,
        headless: false,
      }),
    },
  );
}

export async function readMeituanDataset(input: {
  platformAccountId: string;
  dataset: string;
  browserSessionId?: string;
}): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>(
    `/api/v1/meituan-ebooking/sessions/${encodeURIComponent(input.platformAccountId)}/read-dataset`,
    {
      method: "POST",
      body: JSON.stringify({
        actor_id: "operator",
        browser_session_id: input.browserSessionId || undefined,
        headless: false,
        dataset: input.dataset,
      }),
    },
  );
}
