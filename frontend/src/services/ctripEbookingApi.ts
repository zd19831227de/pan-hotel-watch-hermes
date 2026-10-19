import { apiRequest } from "./http";

export type CtripOpenLoginResult = {
  status: string;
  summary: string;
  platform_account_id: string;
  browser_session_id: string;
  login_url: string;
  manual_login_required: boolean;
  next_action?: string | null;
};

export async function openCtripLogin(input: {
  platformAccountId: string;
  browserSessionId?: string;
  loginContext: "ebk" | "competitor_price";
}): Promise<CtripOpenLoginResult> {
  return apiRequest<CtripOpenLoginResult>(
    `/api/v1/ctrip-ebooking/sessions/${encodeURIComponent(input.platformAccountId)}/open-login`,
    {
      method: "POST",
      body: JSON.stringify({
        actor_id: "operator",
        login_context: input.loginContext,
        browser_session_id: input.browserSessionId || undefined,
        headless: false,
      }),
    },
  );
}
