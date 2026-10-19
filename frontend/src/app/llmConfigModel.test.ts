import assert from "node:assert/strict";
import test from "node:test";

import {
  PROVIDER_TEMPLATES,
  editFormForChatModel,
  hermesSyncNotice,
  providerTemplatesForKind,
  providerTemplate,
  selectedChatModels,
  unifiedModelLibrary,
} from "./llmConfigModel.ts";
import type { EmbeddingConfig, LlmModelConfig } from "../services/configApi";

const chat: LlmModelConfig[] = [
  {
    id: 1,
    provider: "deepseek",
    model_name: "deepseek-v4-flash",
    base_url: "https://api.deepseek.com",
    api_key_configured: true,
    api_key_masked: "deep...-key",
    priority: 0,
    enabled: true,
    last_health_status: "ok",
    last_health_summary: null,
    last_health_checked_at: null,
  },
  {
    id: 2,
    provider: "openai",
    model_name: "gpt-4.1-mini",
    base_url: "https://api.openai.com/v1",
    api_key_configured: true,
    api_key_masked: "open...-key",
    priority: 1,
    enabled: true,
    last_health_status: null,
    last_health_summary: null,
    last_health_checked_at: null,
  },
];

const embedding: EmbeddingConfig = {
  provider: "openai",
  model_name: "text-embedding-3-small",
  base_url: "https://api.openai.com/v1",
  api_key_configured: true,
  api_key_masked: "open...-key",
  dimensions: 1536,
};

test("provider templates expose chat and vector defaults", () => {
  const deepseek = providerTemplate("deepseek");
  assert.equal(deepseek.base_url, "https://api.deepseek.com");
  assert.ok(deepseek.chat_models.includes("deepseek-v4-flash"));

  const openai = providerTemplate("openai");
  assert.ok(openai.chat_models.includes("gpt-4.1-mini"));
  assert.ok(openai.embedding_models.includes("text-embedding-3-small"));
  assert.ok(PROVIDER_TEMPLATES.length >= 4);
});

test("provider templates are filtered by model kind", () => {
  const chat = providerTemplatesForKind("chat");
  const embedding = providerTemplatesForKind("embedding");

  assert.ok(chat.some((template) => template.id === "deepseek"));
  assert.ok(embedding.some((template) => template.id === "openai"));
  assert.ok(!embedding.some((template) => template.id === "deepseek"));
});

test("unified model library merges chat chain and vector model", () => {
  const library = unifiedModelLibrary(chat, embedding);
  assert.equal(library.length, 3);
  assert.deepEqual(
    library.map((item) => item.kind),
    ["chat", "chat", "embedding"],
  );
  assert.equal(library[2].model_name, "text-embedding-3-small");
});

test("selected models map first enabled chat model to primary", () => {
  const selected = selectedChatModels(chat);
  assert.equal(selected.primary?.id, 1);
  assert.equal(selected.fallback?.id, 2);
});

test("saved chat model maps to an edit form without reusing its masked key", () => {
  assert.deepEqual(editFormForChatModel(chat[0]), {
    template_id: "deepseek",
    provider: "deepseek",
    base_url: "https://api.deepseek.com",
    api_key: "",
    model: "deepseek-v4-flash",
  });
});

test("Hermes sync notices distinguish success, missing profiles, and failure", () => {
  assert.deepEqual(
    hermesSyncNotice({
      status: "synced",
      summary: "ok",
      profile_keys: ["jingli-ai"],
      primary_model: "deepseek-v4-flash",
      fallback_count: 0,
    }),
    { status: "success", detail: "已覆盖：jingli-ai" },
  );
  assert.deepEqual(
    hermesSyncNotice({
      status: "not_configured",
      summary: "missing",
      profile_keys: [],
      primary_model: null,
      fallback_count: 0,
    }),
    { status: "warning", detail: "尚未发现可同步的 Hermes profile" },
  );
  assert.deepEqual(
    hermesSyncNotice({
      status: "failed",
      summary: "rolled back",
      profile_keys: [],
      primary_model: "deepseek-v4-flash",
      fallback_count: 0,
    }),
    { status: "failed", detail: "未完成任何 Hermes profile 同步" },
  );
});
