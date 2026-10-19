import { apiRequest } from "./http";

export type LlmChatPlatform = "doubao" | "qwen";

export type LlmChatLoginStateResult = {
  status: string;
  summary: string;
  login_state?: string;
  [key: string]: unknown;
};

export type LlmChatAskResult = {
  status: string;
  summary: string;
  answer_text?: string;
  [key: string]: unknown;
};

export async function openChatLogin(
  platform: LlmChatPlatform,
  platformAccountId: string,
  options: { browser_session_id?: string } = {},
): Promise<LlmChatLoginStateResult> {
  return apiRequest<LlmChatLoginStateResult>(
    `/api/v1/${platform}-chat/sessions/${encodeURIComponent(platformAccountId)}/open-login`,
    {
      method: "POST",
      body: JSON.stringify({ browser_session_id: options.browser_session_id ?? null }),
    },
  );
}

export async function checkChatLoginState(
  platform: LlmChatPlatform,
  platformAccountId: string,
  options: { browser_session_id?: string } = {},
): Promise<LlmChatLoginStateResult> {
  return apiRequest<LlmChatLoginStateResult>(
    `/api/v1/${platform}-chat/sessions/${encodeURIComponent(platformAccountId)}/check-login-state`,
    {
      method: "POST",
      body: JSON.stringify({ browser_session_id: options.browser_session_id ?? null }),
    },
  );
}

export async function askAndExtract(
  platform: LlmChatPlatform,
  platformAccountId: string,
  input: { prompt: string; max_wait_seconds?: number; browser_session_id?: string },
): Promise<LlmChatAskResult> {
  return apiRequest<LlmChatAskResult>(
    `/api/v1/${platform}-chat/sessions/${encodeURIComponent(platformAccountId)}/ask-and-extract`,
    {
      method: "POST",
      body: JSON.stringify({
        prompt: input.prompt,
        max_wait_seconds: input.max_wait_seconds ?? 120,
        browser_session_id: input.browser_session_id ?? null,
      }),
    },
  );
}
