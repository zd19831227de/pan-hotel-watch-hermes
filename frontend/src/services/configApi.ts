import { apiRequest } from "./http";

export type FeishuAgentProfile = {
  profile_key: string;
  display_name: string;
  enabled: boolean;
  credential_configured: boolean;
  app_id_fingerprint?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type FeishuChatAgentBinding = {
  id: number;
  profile_key: string;
  display_name: string;
  chat_id?: string | null;
  enabled: boolean;
  chat_id_masked: string;
  chat_id_fingerprint: string;
  // 仅创建/更新/删除响应携带：本次操作是否同步了 Hermes 准入名单。
  admission_synced?: boolean;
  admission_warning?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type FeishuRole = "viewer" | "operator" | "supervisor";

export type FeishuRoleAssignment = {
  id: number;
  subject_type: "profile" | "api_key";
  subject_ref: string;
  role: FeishuRole;
  granted_by: string;
  created_at?: string;
};

export type SupervisorWatchGroup = {
  id?: number;
  group_key: string;
  name: string;
  feishu_chat_id?: string | null;
  enabled?: boolean;
  feishu_chat_id_configured?: boolean;
  feishu_chat_id_masked?: string | null;
  feishu_webhook_configured?: boolean;
  feishu_webhook_masked?: string | null;
  feishu_webhook_url?: string | null;
  feishu_mobile_webhook_configured?: boolean;
  feishu_mobile_webhook_masked?: string | null;
  feishu_mobile_webhook_url?: string | null;
  feishu_desktop_webhook_configured?: boolean;
  feishu_desktop_webhook_masked?: string | null;
  feishu_desktop_webhook_url?: string | null;
  pricing_platform_account_id?: string | null;
  pricing_browser_session_id?: string | null;
  pricing_account_configured?: boolean;
  pricing_connection_mode?: string | null;
  pricing_can_auto_write_price?: boolean;
  pricing_write_policy_message?: string | null;
  browser_session_id?: string | null;
  platform_account_id?: string | null;
};

export type SupervisorOverview = {
  profiles: FeishuAgentProfile[];
  bindings: FeishuChatAgentBinding[];
  role_assignments: FeishuRoleAssignment[];
  watch_groups: {
    ebk: SupervisorWatchGroup[];
    competitor: SupervisorWatchGroup[];
  };
};

export type FeishuAgentProfilePatch = {
  display_name?: string;
  enabled?: boolean;
};

export type FeishuChatAgentBindingPatch = {
  profile_key?: string;
  display_name?: string;
  enabled?: boolean;
};

export type FeishuRoleAssignmentInput = {
  subject_type: "profile" | "api_key";
  subject_ref?: string;
  role: FeishuRole;
  granted_by: string;
};

export type FeishuRoleAssignmentPatch = {
  role?: FeishuRole;
  granted_by?: string;
};

export type FeishuAdmissionPolicy = "open" | "allowlist" | "disabled";

export type FeishuAdmissionGroup = {
  group_key: string;
  chat_id_masked: string;
  policy: FeishuAdmissionPolicy;
  allowed_users: string[];
  require_mention: boolean;
};

export type FeishuProfileAdmission = {
  profile_key: string;
  display_name: string;
  groups: FeishuAdmissionGroup[];
};

export type FeishuAdmissionPatch = {
  group_key: string;
  policy?: FeishuAdmissionPolicy;
  allowed_users?: string[];
  require_mention?: boolean;
};

function supervisorHeaders(apiKey: string): HeadersInit {
  return { "X-Pan-Hotel-API-Key": apiKey.trim() };
}

function supervisorInit(apiKey: string, init?: RequestInit): RequestInit {
  return { ...init, headers: { ...supervisorHeaders(apiKey), ...(init?.headers ?? {}) } };
}

export type IntegrationConfigSnapshot = {
  llm_provider: string;
  llm_base_url: string;
  llm_model: string;
  llm_api_key_configured: boolean;
  llm_api_key_masked: string | null;
};

export type IntegrationConfigUpdate = {
  llm_provider?: string;
  llm_base_url?: string;
  llm_model?: string;
  llm_api_key?: string;
};

export type LlmModelsRequest = IntegrationConfigUpdate & {
  model_config_id?: number;
};

export type LlmConnectionTestResult = {
  status: string;
  summary: string;
  provider: string;
  base_url: string;
  model: string;
  api_key_configured: boolean;
  data: {
    http_status?: number;
    reply_preview?: string;
    usage?: Record<string, unknown> | null;
  };
  evidence: Record<string, unknown>;
  next_action: string | null;
};

export type LlmModelsResult = {
  status: string;
  summary: string;
  provider: string;
  base_url: string;
  api_key_configured: boolean;
  models: string[];
  evidence: Record<string, unknown>;
  next_action: string | null;
};

export async function getIntegrationConfig(): Promise<IntegrationConfigSnapshot> {
  return apiRequest<IntegrationConfigSnapshot>("/api/v1/config/integrations");
}

export async function updateIntegrationConfig(
  update: IntegrationConfigUpdate,
): Promise<IntegrationConfigSnapshot> {
  return apiRequest<IntegrationConfigSnapshot>("/api/v1/config/integrations", {
    method: "POST",
    body: JSON.stringify(update),
  });
}

export async function testLlmConnection(
  update?: LlmModelsRequest,
  apiKey?: string,
): Promise<LlmConnectionTestResult> {
  const init: RequestInit = { method: "POST", body: JSON.stringify(update ?? {}) };
  return apiRequest<LlmConnectionTestResult>(
    "/api/v1/config/integrations/llm/test",
    apiKey ? supervisorInit(apiKey, init) : init,
  );
}

export async function listLlmModels(
  update?: LlmModelsRequest,
  apiKey?: string,
): Promise<LlmModelsResult> {
  const init: RequestInit = { method: "POST", body: JSON.stringify(update ?? {}) };
  return apiRequest<LlmModelsResult>(
    "/api/v1/config/integrations/llm/models",
    apiKey ? supervisorInit(apiKey, init) : init,
  );
}

// --- V5 Step 1: multi-model LLM fallback chain ---

export type LlmModelConfig = {
  id: number;
  provider: string;
  model_name: string;
  base_url: string;
  api_key_configured: boolean;
  api_key_masked: string | null;
  priority: number;
  enabled: boolean;
  last_health_status: string | null;
  last_health_summary: string | null;
  last_health_checked_at: string | null;
};

export type LlmModelConfigInput = {
  provider: string;
  model_name: string;
  base_url: string;
  api_key?: string;
  priority?: number;
  enabled?: boolean;
};

export type LlmModelConfigPatch = {
  provider?: string;
  model_name?: string;
  base_url?: string;
  api_key?: string;
  priority?: number;
  enabled?: boolean;
};

export type HermesLlmSyncResult = {
  status: "synced" | "not_configured" | "failed";
  summary: string;
  profile_keys: string[];
  primary_model: string | null;
  fallback_count: number;
};

export type LlmModelMutationResult = {
  model: LlmModelConfig;
  hermes_sync: HermesLlmSyncResult;
};

export type LlmModelListMutationResult = {
  models: LlmModelConfig[];
  hermes_sync: HermesLlmSyncResult;
};

export type LlmModelDeleteResult = {
  status: string;
  hermes_sync: HermesLlmSyncResult;
};

export async function listLlmModelConfigs(apiKey?: string): Promise<LlmModelConfig[]> {
  const payload = await apiRequest<{ models: LlmModelConfig[] }>(
    "/api/v1/config/integrations/llm/model-configs",
    apiKey ? supervisorInit(apiKey) : undefined,
  );
  return payload.models;
}

export async function createLlmModelConfig(
  input: LlmModelConfigInput,
  apiKey?: string,
): Promise<LlmModelMutationResult> {
  return apiRequest<LlmModelMutationResult>(
    "/api/v1/config/integrations/llm/model-configs",
    apiKey ? supervisorInit(apiKey, { method: "POST", body: JSON.stringify(input) }) : { method: "POST", body: JSON.stringify(input) },
  );
}

export async function updateLlmModelConfig(
  id: number,
  patch: LlmModelConfigPatch,
  apiKey?: string,
): Promise<LlmModelMutationResult> {
  return apiRequest<LlmModelMutationResult>(
    `/api/v1/config/integrations/llm/model-configs/${id}`,
    apiKey ? supervisorInit(apiKey, { method: "PUT", body: JSON.stringify(patch) }) : { method: "PUT", body: JSON.stringify(patch) },
  );
}

export async function deleteLlmModelConfig(
  id: number,
  apiKey?: string,
): Promise<LlmModelDeleteResult> {
  return apiRequest<LlmModelDeleteResult>(
    `/api/v1/config/integrations/llm/model-configs/${id}`,
    apiKey ? supervisorInit(apiKey, { method: "DELETE" }) : { method: "DELETE" },
  );
}

export async function reorderLlmModelConfigs(
  orderedIds: number[],
  apiKey?: string,
): Promise<LlmModelListMutationResult> {
  return apiRequest<LlmModelListMutationResult>(
    "/api/v1/config/integrations/llm/model-configs/reorder",
    apiKey
      ? supervisorInit(apiKey, { method: "POST", body: JSON.stringify({ ordered_ids: orderedIds }) })
      : { method: "POST", body: JSON.stringify({ ordered_ids: orderedIds }) },
  );
}

export async function syncLlmModelConfigsToHermes(
  apiKey?: string,
): Promise<HermesLlmSyncResult> {
  const payload = await apiRequest<{ hermes_sync: HermesLlmSyncResult }>(
    "/api/v1/config/integrations/llm/model-configs/sync-hermes",
    apiKey ? supervisorInit(apiKey, { method: "POST" }) : { method: "POST" },
  );
  return payload.hermes_sync;
}

export async function healthCheckLlmModelConfigs(
  apiKey?: string,
): Promise<LlmModelConfig[]> {
  const payload = await apiRequest<{ models: LlmModelConfig[] }>(
    "/api/v1/config/integrations/llm/model-configs/health",
    apiKey ? supervisorInit(apiKey, { method: "POST" }) : { method: "POST" },
  );
  return payload.models;
}

// --- V5 Step 3: Skill management ---

export type SkillSummary = {
  name: string;
  description: string;
  source: string;
  references: string[];
  templates: string[];
  size_bytes: number;
  updated_at: string | null;
};

export type SkillDetail = {
  name: string;
  description: string;
  source: string;
  content: string;
  references: Record<string, string>;
  templates: Record<string, string>;
  updated_at: string | null;
};

export type SkillCreateInput = {
  name: string;
  description?: string;
  content?: string;
};

export type SkillUpdateInput = {
  description?: string;
  content?: string;
};

export async function listSkills(apiKey: string): Promise<SkillSummary[]> {
  const payload = await apiRequest<{ skills: SkillSummary[] }>(
    "/api/v1/skills",
    supervisorInit(apiKey),
  );
  return payload.skills;
}

export async function getSkill(apiKey: string, name: string): Promise<SkillDetail> {
  const payload = await apiRequest<{ skill: SkillDetail }>(
    `/api/v1/skills/${encodeURIComponent(name)}`,
    supervisorInit(apiKey),
  );
  return payload.skill;
}

export async function createSkill(
  apiKey: string,
  input: SkillCreateInput,
): Promise<SkillDetail> {
  const payload = await apiRequest<{ skill: SkillDetail }>(
    "/api/v1/skills",
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify(input) }),
  );
  return payload.skill;
}

export async function updateSkill(
  apiKey: string,
  name: string,
  input: SkillUpdateInput,
): Promise<SkillDetail> {
  const payload = await apiRequest<{ skill: SkillDetail }>(
    `/api/v1/skills/${encodeURIComponent(name)}`,
    supervisorInit(apiKey, { method: "PUT", body: JSON.stringify(input) }),
  );
  return payload.skill;
}

export async function deleteSkill(apiKey: string, name: string): Promise<void> {
  await apiRequest<{ status: string }>(
    `/api/v1/skills/${encodeURIComponent(name)}`,
    supervisorInit(apiKey, { method: "DELETE" }),
  );
}

// --- V5 Step 4: RAG knowledge base ---

export type EmbeddingConfig = {
  provider: string;
  model_name: string;
  base_url: string;
  api_key_configured: boolean;
  api_key_masked: string | null;
  dimensions: number | null;
};

export type EmbeddingConfigUpdate = {
  provider: string;
  model_name: string;
  base_url: string;
  api_key?: string;
  dimensions?: number;
};

export type EmbeddingConnectionTestResult = {
  status: string;
  summary: string;
  provider: string;
  model_name: string;
  base_url: string;
  api_key_configured: boolean;
  api_key_masked: string | null;
  vector_length?: number;
};

export type RagDocument = {
  id: number;
  title: string;
  source_type: string;
  folder: string | null;
  content_hash: string;
  chunk_count: number;
  created_at: string;
  updated_at: string | null;
};

export type RagDocumentDetail = RagDocument & {
  raw_content: string;
};

export type RagFolder = {
  id: number;
  name: string;
  document_count: number;
  sort_order: number;
};

export type RagFolderSummary = {
  folders: RagFolder[];
  unfiled_count: number;
  total_count: number;
};

export type RagSearchResult = {
  document_id: number;
  title: string;
  chunk_text: string;
  score: number;
};

export async function getEmbeddingConfig(apiKey: string): Promise<EmbeddingConfig | null> {
  const payload = await apiRequest<{ embedding_config: EmbeddingConfig | null }>(
    "/api/v1/rag/embedding-config",
    supervisorInit(apiKey),
  );
  return payload.embedding_config;
}

export async function updateEmbeddingConfig(
  apiKey: string,
  update: EmbeddingConfigUpdate,
): Promise<EmbeddingConfig> {
  const payload = await apiRequest<{ embedding_config: EmbeddingConfig }>(
    "/api/v1/rag/embedding-config",
    supervisorInit(apiKey, { method: "PUT", body: JSON.stringify(update) }),
  );
  return payload.embedding_config;
}

export async function testEmbeddingConfig(
  apiKey: string,
  update: EmbeddingConfigUpdate,
): Promise<EmbeddingConnectionTestResult> {
  return apiRequest<EmbeddingConnectionTestResult>(
    "/api/v1/rag/embedding-config/test",
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify(update) }),
  );
}

export async function listRagDocuments(apiKey: string): Promise<RagDocument[]> {
  const payload = await apiRequest<{ documents: RagDocument[] }>(
    "/api/v1/rag/documents",
    supervisorInit(apiKey),
  );
  return payload.documents;
}

export async function createRagDocument(
  apiKey: string,
  input: { title: string; source_type?: string; folder?: string; content: string },
): Promise<RagDocument> {
  const payload = await apiRequest<{ document: RagDocument }>(
    "/api/v1/rag/documents",
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify(input) }),
  );
  return payload.document;
}

export async function getRagDocument(apiKey: string, id: number): Promise<RagDocumentDetail> {
  const payload = await apiRequest<{ document: RagDocumentDetail }>(
    `/api/v1/rag/documents/${id}`,
    supervisorInit(apiKey),
  );
  return payload.document;
}

export async function updateRagDocument(
  apiKey: string,
  id: number,
  update: { title?: string; folder?: string; content?: string },
): Promise<RagDocumentDetail> {
  const payload = await apiRequest<{ document: RagDocumentDetail }>(
    `/api/v1/rag/documents/${id}`,
    supervisorInit(apiKey, { method: "PATCH", body: JSON.stringify(update) }),
  );
  return payload.document;
}

export async function deleteRagDocument(apiKey: string, id: number): Promise<void> {
  await apiRequest<{ status: string }>(
    `/api/v1/rag/documents/${id}`,
    supervisorInit(apiKey, { method: "DELETE" }),
  );
}

export async function searchRag(
  apiKey: string,
  query: string,
  topK = 5,
): Promise<RagSearchResult[]> {
  const payload = await apiRequest<{ results: RagSearchResult[] }>(
    "/api/v1/rag/search",
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify({ query, top_k: topK }) }),
  );
  return payload.results;
}

export async function listRagFolders(apiKey: string): Promise<RagFolderSummary> {
  return apiRequest<RagFolderSummary>(
    "/api/v1/rag/folders",
    supervisorInit(apiKey),
  );
}

export async function createRagFolder(apiKey: string, name: string): Promise<RagFolder> {
  const payload = await apiRequest<{ folder: RagFolder }>(
    "/api/v1/rag/folders",
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify({ name }) }),
  );
  return payload.folder;
}

export async function renameRagFolder(apiKey: string, id: number, name: string): Promise<RagFolder> {
  const payload = await apiRequest<{ folder: RagFolder }>(
    `/api/v1/rag/folders/${id}`,
    supervisorInit(apiKey, { method: "PATCH", body: JSON.stringify({ name }) }),
  );
  return payload.folder;
}

export async function deleteRagFolder(apiKey: string, id: number): Promise<void> {
  await apiRequest<{ status: string }>(
    `/api/v1/rag/folders/${id}`,
    supervisorInit(apiKey, { method: "DELETE" }),
  );
}

export async function getSupervisorOverview(apiKey: string): Promise<SupervisorOverview> {
  return apiRequest<SupervisorOverview>(
    "/api/v1/config/supervisor-overview",
    supervisorInit(apiKey),
  );
}

export async function listFeishuAgentProfiles(apiKey: string): Promise<FeishuAgentProfile[]> {
  const payload = await apiRequest<{ profiles: FeishuAgentProfile[] }>(
    "/api/v1/config/feishu-agent-profiles",
    supervisorInit(apiKey),
  );
  return payload.profiles;
}

export async function patchFeishuAgentProfile(
  apiKey: string,
  profileKey: string,
  input: FeishuAgentProfilePatch,
): Promise<FeishuAgentProfile> {
  const payload = await apiRequest<{ profile: FeishuAgentProfile }>(
    `/api/v1/config/feishu-agent-profiles/${encodeURIComponent(profileKey)}`,
    supervisorInit(apiKey, { method: "PATCH", body: JSON.stringify(input) }),
  );
  return payload.profile;
}

export type FeishuAgentProfileCreate = {
  profile_key: string;
  display_name: string;
  enabled?: boolean;
};

// 新增飞书 AI 助手 profile：App Secret 不在前端输入，由 admin 通过
// FEISHU_APP_CREDENTIALS_JSON 按 profile_key 预置；创建时若 enabled=true
// 但该 key 未配凭据，后端会 422 报错（前端提示先配凭据或先建未启用再启用）。
export async function createFeishuAgentProfile(
  apiKey: string,
  input: FeishuAgentProfileCreate,
): Promise<FeishuAgentProfile> {
  const payload = await apiRequest<{ profile: FeishuAgentProfile }>(
    "/api/v1/config/feishu-agent-profiles",
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify(input) }),
  );
  return payload.profile;
}

export type FeishuProfileCredentialsInput = {
  app_id: string;
  app_secret: string;
  display_name?: string | null;
};

// 前端配置飞书应用凭据：后端写 YAML → 更新 .env → 部署 + 重启 Gateway，
// 全程不把 app_secret 写进日志或 API 响应（响应只含凭据状态与指纹）。
export async function setFeishuProfileCredentials(
  apiKey: string,
  profileKey: string,
  input: FeishuProfileCredentialsInput,
): Promise<FeishuAgentProfile> {
  const payload = await apiRequest<{ profile: FeishuAgentProfile }>(
    `/api/v1/config/feishu-agent-profiles/${encodeURIComponent(profileKey)}/credentials`,
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify(input) }),
  );
  return payload.profile;
}

// 删除 AI 助手 profile：仍被业务群绑定时后端 409 拒绝。
// 已部署凭据的 profile 删除时后端会顺带回收 manifest/后端 .env/网关；
// cleanup_warning 非空时需要按提示人工收尾。
export type DeletedFeishuAgentProfile = FeishuAgentProfile & {
  manifest_cleaned?: boolean;
  cleanup_warning?: string | null;
};

export async function deleteFeishuAgentProfile(
  apiKey: string,
  profileKey: string,
): Promise<DeletedFeishuAgentProfile> {
  const payload = await apiRequest<{ profile: DeletedFeishuAgentProfile }>(
    `/api/v1/config/feishu-agent-profiles/${encodeURIComponent(profileKey)}`,
    supervisorInit(apiKey, { method: "DELETE" }),
  );
  return payload.profile;
}

export type FeishuChatAgentBindingCreate = {
  chat_id: string;
  profile_key: string;
  display_name?: string | null;
  enabled?: boolean;
};

export async function listFeishuChatAgentBindings(apiKey: string): Promise<FeishuChatAgentBinding[]> {
  const payload = await apiRequest<{ bindings: FeishuChatAgentBinding[] }>(
    "/api/v1/config/feishu-chat-agent-bindings",
    supervisorInit(apiKey),
  );
  return payload.bindings;
}

// 新增整套店铺配置的第一步：把飞书群绑定到 Agent profile（新增店铺时
// EBK/竞品监控组都要求目标 chat 已有启用的 binding，先建绑定再绑组）。
export async function createFeishuChatAgentBinding(
  apiKey: string,
  input: FeishuChatAgentBindingCreate,
): Promise<FeishuChatAgentBinding> {
  const payload = await apiRequest<{ binding: FeishuChatAgentBinding }>(
    "/api/v1/config/feishu-chat-agent-bindings",
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify(input) }),
  );
  return payload.binding;
}

export async function patchFeishuChatAgentBinding(
  apiKey: string,
  bindingId: number,
  input: FeishuChatAgentBindingPatch,
): Promise<FeishuChatAgentBinding> {
  const payload = await apiRequest<{ binding: FeishuChatAgentBinding }>(
    `/api/v1/config/feishu-chat-agent-bindings/${bindingId}`,
    supervisorInit(apiKey, { method: "PATCH", body: JSON.stringify(input) }),
  );
  return payload.binding;
}

// 删除业务群 Agent 绑定：后端同步把该群移出 Hermes 准入名单并热重载。
export async function deleteFeishuChatAgentBinding(
  apiKey: string,
  bindingId: number,
): Promise<FeishuChatAgentBinding> {
  const payload = await apiRequest<{ binding: FeishuChatAgentBinding }>(
    `/api/v1/config/feishu-chat-agent-bindings/${bindingId}`,
    supervisorInit(apiKey, { method: "DELETE" }),
  );
  return payload.binding;
}

export async function listFeishuRoleAssignments(apiKey: string): Promise<FeishuRoleAssignment[]> {
  const payload = await apiRequest<{ assignments: FeishuRoleAssignment[] }>(
    "/api/v1/config/feishu-role-assignments",
    supervisorInit(apiKey),
  );
  return payload.assignments;
}

export async function createFeishuRoleAssignment(
  apiKey: string,
  input: FeishuRoleAssignmentInput,
): Promise<FeishuRoleAssignment> {
  const payload = await apiRequest<{ assignment: FeishuRoleAssignment }>(
    "/api/v1/config/feishu-role-assignments",
    supervisorInit(apiKey, { method: "POST", body: JSON.stringify(input) }),
  );
  return payload.assignment;
}

export async function patchFeishuRoleAssignment(
  apiKey: string,
  assignmentId: number,
  input: FeishuRoleAssignmentPatch,
): Promise<FeishuRoleAssignment> {
  const payload = await apiRequest<{ assignment: FeishuRoleAssignment }>(
    `/api/v1/config/feishu-role-assignments/${assignmentId}`,
    supervisorInit(apiKey, { method: "PATCH", body: JSON.stringify(input) }),
  );
  return payload.assignment;
}

export async function getFeishuProfileAdmission(
  apiKey: string,
  profileKey: string,
): Promise<FeishuProfileAdmission> {
  return apiRequest<FeishuProfileAdmission>(
    `/api/v1/config/feishu/profiles/${encodeURIComponent(profileKey)}/admission`,
    supervisorInit(apiKey),
  );
}

export async function patchFeishuProfileAdmission(
  apiKey: string,
  profileKey: string,
  input: FeishuAdmissionPatch,
): Promise<FeishuProfileAdmission> {
  return apiRequest<FeishuProfileAdmission>(
    `/api/v1/config/feishu/profiles/${encodeURIComponent(profileKey)}/admission`,
    supervisorInit(apiKey, { method: "PATCH", body: JSON.stringify(input) }),
  );
}

export async function addFeishuAdmissionUser(
  apiKey: string,
  profileKey: string,
  groupKey: string,
  openId: string,
): Promise<FeishuProfileAdmission> {
  return apiRequest<FeishuProfileAdmission>(
    `/api/v1/config/feishu/profiles/${encodeURIComponent(profileKey)}/admission/users`,
    supervisorInit(apiKey, {
      method: "POST",
      body: JSON.stringify({ group_key: groupKey, open_id: openId }),
    }),
  );
}

export async function removeFeishuAdmissionUser(
  apiKey: string,
  profileKey: string,
  groupKey: string,
  openId: string,
): Promise<FeishuProfileAdmission> {
  const query = new URLSearchParams({ group_key: groupKey });
  return apiRequest<FeishuProfileAdmission>(
    `/api/v1/config/feishu/profiles/${encodeURIComponent(profileKey)}/admission/users/${encodeURIComponent(openId)}?${query}`,
    supervisorInit(apiKey, { method: "DELETE" }),
  );
}

export type ActiveOpenId = {
  open_id: string;
  chat_id_masked: string;
  last_seen: string;
};

export async function getActiveOpenIds(
  apiKey: string,
  profileKey: string,
): Promise<ActiveOpenId[]> {
  const payload = await apiRequest<{ open_ids: ActiveOpenId[] }>(
    `/api/v1/config/feishu/profiles/${encodeURIComponent(profileKey)}/admission/active-open-ids`,
    supervisorInit(apiKey),
  );
  return payload.open_ids ?? [];
}

// --- Browser cache audit & cleanup (V4-Step4) ------------------------------

export type BrowserCacheProfile = {
  profile_id: string;
  total_bytes: number;
  cleanable_bytes: number;
  cleanable_fraction: number;
  login_state_bytes: number;
  non_cache_bytes: number;
  unknown_bytes: number;
};

export type BrowserCachePlatform = {
  platform: string;
  profile_count: number;
  total_bytes: number;
  cleanable_bytes: number;
  profiles: BrowserCacheProfile[];
};

export type BrowserCacheAudit = {
  root: string;
  total_bytes: number;
  cleanable_bytes: number;
  cleanable_fraction: number;
  platforms: BrowserCachePlatform[];
};

export type BrowserCacheCleanupResult = {
  profiles_cleaned: number;
  bytes_freed: number;
  bytes_freed_human: string;
  dry_run: boolean;
};

export async function getBrowserCacheAudit(apiKey: string): Promise<BrowserCacheAudit> {
  return apiRequest<BrowserCacheAudit>(
    "/api/v1/browser-sessions/cache-audit",
    supervisorInit(apiKey),
  );
}

export async function cleanupBrowserCache(
  apiKey: string,
  options: { min_age_days?: number | null; dry_run?: boolean } = {},
): Promise<BrowserCacheCleanupResult> {
  return apiRequest<BrowserCacheCleanupResult>(
    "/api/v1/browser-sessions/cleanup",
    supervisorInit(apiKey, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        min_age_days: options.min_age_days ?? 0,
        dry_run: options.dry_run ?? false,
      }),
    }),
  );
}

// --- Playwright screenshot audit & cleanup (V4-Step4 follow-up) ------------

export type ScreenshotAudit = {
  root: string;
  retention_days: number;
  total_files: number;
  total_bytes: number;
  cleanable_files: number;
  cleanable_bytes: number;
};

export type ScreenshotCleanupResult = {
  root: string;
  retention_days: number;
  files_removed: number;
  bytes_freed: number;
  bytes_freed_human: string;
  dry_run: boolean;
};

export async function getScreenshotAudit(
  apiKey: string,
  retentionDays?: number,
): Promise<ScreenshotAudit> {
  const qs =
    retentionDays != null ? `?retention_days=${encodeURIComponent(retentionDays)}` : "";
  return apiRequest<ScreenshotAudit>(
    `/api/v1/browser-sessions/screenshot-audit${qs}`,
    supervisorInit(apiKey),
  );
}

export async function cleanupScreenshots(
  apiKey: string,
  options: { retention_days?: number | null; dry_run?: boolean } = {},
): Promise<ScreenshotCleanupResult> {
  return apiRequest<ScreenshotCleanupResult>(
    "/api/v1/browser-sessions/cleanup-screenshots",
    supervisorInit(apiKey, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        retention_days: options.retention_days ?? null,
        dry_run: options.dry_run ?? false,
      }),
    }),
  );
}
