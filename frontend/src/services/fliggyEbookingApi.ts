import { apiRequest } from "./http.ts";

export type FliggyOpenLoginResult = {
  status: string;
  summary: string;
  platform: string;
  platform_account_id: string;
  browser_session_id: string;
  login_url: string;
  manual_login_required: boolean;
  next_action?: string | null;
};

export async function openFliggyLogin(input: {
  platformAccountId: string;
  browserSessionId?: string;
}): Promise<FliggyOpenLoginResult> {
  return apiRequest<FliggyOpenLoginResult>(
    `/api/v1/fliggy-ebooking/sessions/${encodeURIComponent(input.platformAccountId)}/open-login`,
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

export async function checkFliggyLoginState(input: {
  platformAccountId: string;
  browserSessionId?: string;
}): Promise<FliggyOpenLoginResult> {
  return apiRequest<FliggyOpenLoginResult>(
    `/api/v1/fliggy-ebooking/sessions/${encodeURIComponent(input.platformAccountId)}/check-login-state`,
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

export async function readFliggyDataset(input: {
  platformAccountId: string;
  dataset: string;
  browserSessionId?: string;
}): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>(
    `/api/v1/fliggy-ebooking/sessions/${encodeURIComponent(input.platformAccountId)}/read-dataset`,
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
