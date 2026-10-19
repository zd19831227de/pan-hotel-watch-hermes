import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const serviceSource = readFileSync(new URL("./configApi.ts", import.meta.url), "utf8");

test("LLM 配置服务支持模型拉取和带表单参数的测试", () => {
  assert.match(serviceSource, /export async function listLlmModels\([\s\S]*?apiKey\?: string/);
  assert.match(serviceSource, /\/api\/v1\/config\/integrations\/llm\/models/);
  assert.match(serviceSource, /method: "POST"/);
  assert.match(serviceSource, /body: JSON\.stringify\(update \?\? \{\}\)/);
  assert.match(serviceSource, /testLlmConnection\(\s*update\?/);
});

test("高级配置服务提供带 supervisor API key 的总览与角色管理", () => {
  assert.match(serviceSource, /export type SupervisorOverview/);
  assert.match(serviceSource, /export async function getSupervisorOverview\(apiKey: string\)/);
  assert.match(serviceSource, /\/api\/v1\/config\/supervisor-overview/);
  assert.match(serviceSource, /X-Pan-Hotel-API-Key/);
  assert.match(serviceSource, /export async function listFeishuAgentProfiles\(apiKey: string\)/);
  assert.match(serviceSource, /export async function patchFeishuAgentProfile\(/);
  assert.match(serviceSource, /\/api\/v1\/config\/feishu-agent-profiles\//);
  assert.match(serviceSource, /export async function listFeishuChatAgentBindings\(apiKey: string\)/);
  assert.match(serviceSource, /export async function patchFeishuChatAgentBinding\(/);
  assert.match(serviceSource, /\/api\/v1\/config\/feishu-chat-agent-bindings\//);
  assert.match(serviceSource, /export async function listFeishuRoleAssignments\(apiKey: string\)/);
  assert.match(serviceSource, /export async function createFeishuRoleAssignment\(/);
  assert.match(serviceSource, /export async function patchFeishuRoleAssignment\(/);
  assert.match(serviceSource, /\/api\/v1\/config\/feishu-role-assignments\//);
});

test("高级配置凭据 API 走 supervisor 鉴权且不回显 secret", () => {
  // supervisor 可 POST 凭据：app_secret 只在请求体（加密传输），不在响应。
  assert.match(serviceSource, /setFeishuProfileCredentials/);
  assert.match(serviceSource, /feishu-agent-profiles\/[^/]+\/credentials/);
  // 响应类型不含 secret 字段（只含 credential_configured + fingerprint）
  assert.match(serviceSource, /credential_configured: boolean/);
  assert.doesNotMatch(serviceSource, /FEISHU_APP_SECRET/);
});

test("V5 Step 1 — LLM 多模型回退链管理（CRUD/排序/健康检查）", () => {
  assert.match(serviceSource, /export type LlmModelConfig =/);
  assert.match(serviceSource, /last_health_status: string \| null/);
  assert.match(serviceSource, /export async function listLlmModelConfigs\(apiKey\?: string\)/);
  assert.match(
    serviceSource,
    /\/api\/v1\/config\/integrations\/llm\/model-configs/,
  );
  assert.match(serviceSource, /export async function createLlmModelConfig\(/);
  assert.match(serviceSource, /export async function updateLlmModelConfig\(/);
  assert.match(serviceSource, /export async function deleteLlmModelConfig\(/);
  assert.match(serviceSource, /export async function reorderLlmModelConfigs\(/);
  assert.match(serviceSource, /body: JSON\.stringify\(\{ ordered_ids: orderedIds \}\)/);
  assert.match(serviceSource, /export async function healthCheckLlmModelConfigs\([\s\S]*?apiKey\?: string/);
  assert.match(
    serviceSource,
    /\/api\/v1\/config\/integrations\/llm\/model-configs\/health/,
  );
  assert.match(serviceSource, /export type HermesLlmSyncResult =/);
  assert.match(serviceSource, /export type LlmModelMutationResult =/);
  assert.match(serviceSource, /model_config_id\?: number/);
  assert.match(serviceSource, /export async function syncLlmModelConfigsToHermes/);
  assert.match(
    serviceSource,
    /\/api\/v1\/config\/integrations\/llm\/model-configs\/sync-hermes/,
  );
});

test("V5 Step 3 — Skill 管理 CRUD（supervisor 鉴权）", () => {
  assert.match(serviceSource, /export type SkillSummary =/);
  assert.match(serviceSource, /source: string;/);
  assert.match(serviceSource, /export async function listSkills\(apiKey: string\)/);
  assert.match(serviceSource, /\/api\/v1\/skills/);
  assert.match(serviceSource, /export async function getSkill\(apiKey: string, name: string\)/);
  assert.match(serviceSource, /export async function createSkill\(/);
  assert.match(serviceSource, /export async function updateSkill\(/);
  assert.match(serviceSource, /export async function deleteSkill\(/);
});

test("V5 Step 4 — RAG 知识库（向量模型配置 + 文档 CRUD + 检索）", () => {
  assert.match(serviceSource, /export type EmbeddingConfig =/);
  assert.match(serviceSource, /export type RagDocument =/);
  assert.match(serviceSource, /export async function getEmbeddingConfig\(apiKey: string\)/);
  assert.match(serviceSource, /\/api\/v1\/rag\/embedding-config/);
  assert.match(serviceSource, /export async function listRagDocuments\(apiKey: string\)/);
  assert.match(serviceSource, /export async function createRagDocument\(/);
  assert.match(serviceSource, /export async function deleteRagDocument\(apiKey: string, id: number\)/);
  assert.match(serviceSource, /export async function searchRag\(/);
  assert.match(serviceSource, /\/api\/v1\/rag\/search/);
});
