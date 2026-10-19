import { apiRequest } from "./http.ts";

export type RuntimeHealthModule = {
  module_key: string;
  display_name: string;
  freshness_status: string;
  process_confirmed: boolean;
};

export type RuntimeHealth = {
  checked_at: string;
  modules: Record<string, RuntimeHealthModule>;
  summary: {
    status: string;
    attention_modules: string[];
    stale_or_missing_modules: string[];
  };
  evidence: Record<string, unknown>;
};

export async function getRuntimeHealth(): Promise<RuntimeHealth> {
  return apiRequest<RuntimeHealth>("/api/v1/ops/runtime-health");
}
