import type {
  EmbeddingConfig,
  HermesLlmSyncResult,
  LlmModelConfig,
} from "../services/configApi";

export type ProviderTemplate = {
  id: string;
  label: string;
  base_url: string;
  chat_models: string[];
  embedding_models: string[];
};

export type LlmProviderForm = {
  template_id: string;
  provider: string;
  base_url: string;
  api_key: string;
  model: string;
};

export const PROVIDER_TEMPLATES: ProviderTemplate[] = [
  {
    id: "deepseek",
    label: "DeepSeek",
    base_url: "https://api.deepseek.com",
    chat_models: ["deepseek-v4-flash", "deepseek-v4-thinking"],
    embedding_models: [],
  },
  {
    id: "openai",
    label: "OpenAI",
    base_url: "https://api.openai.com/v1",
    chat_models: ["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"],
    embedding_models: ["text-embedding-3-small", "text-embedding-3-large"],
  },
  {
    id: "siliconflow",
    label: "SiliconFlow",
    base_url: "https://api.siliconflow.cn/v1",
    chat_models: [
      "Qwen/Qwen3-32B",
      "deepseek-ai/DeepSeek-V3",
    ],
    embedding_models: ["BAAI/bge-m3"],
  },
  {
    id: "openai-compatible",
    label: "OpenAI Compatible",
    base_url: "",
    chat_models: [],
    embedding_models: [],
  },
];

export function providerTemplate(id: string): ProviderTemplate {
  return (
    PROVIDER_TEMPLATES.find((template) => template.id === id) ??
    PROVIDER_TEMPLATES[PROVIDER_TEMPLATES.length - 1]
  );
}

export function editFormForChatModel(model: LlmModelConfig): LlmProviderForm {
  const matchingTemplate = PROVIDER_TEMPLATES.find(
    (template) =>
      template.id === model.provider && template.base_url === model.base_url,
  );
  return {
    template_id: matchingTemplate?.id ?? "openai-compatible",
    provider: model.provider,
    base_url: model.base_url,
    api_key: "",
    model: model.model_name,
  };
}

export function hermesSyncNotice(result: HermesLlmSyncResult): {
  status: "success" | "warning" | "failed";
  detail: string;
} {
  if (result.status === "synced") {
    return {
      status: "success",
      detail: `已覆盖：${result.profile_keys.join("、")}`,
    };
  }
  if (result.status === "not_configured") {
    return {
      status: "warning",
      detail: "尚未发现可同步的 Hermes profile",
    };
  }
  return {
    status: "failed",
    detail: "未完成任何 Hermes profile 同步",
  };
}

export function providerTemplatesForKind(
  kind: "chat" | "embedding",
): ProviderTemplate[] {
  return PROVIDER_TEMPLATES.filter((template) => {
    if (kind === "chat") {
      return template.chat_models.length > 0 || template.id === "openai-compatible";
    }
    return template.embedding_models.length > 0 || template.id === "openai-compatible";
  });
}

export type UnifiedModelEntry = {
  key: string;
  kind: "chat" | "embedding";
  provider: string;
  model_name: string;
  base_url: string;
  enabled: boolean;
  priority?: number;
  health_status?: string | null;
};

export function unifiedModelLibrary(
  chatModels: LlmModelConfig[],
  embedding: EmbeddingConfig | null,
): UnifiedModelEntry[] {
  const chat = chatModels.map((model) => ({
    key: `chat-${model.id}`,
    kind: "chat" as const,
    provider: model.provider,
    model_name: model.model_name,
    base_url: model.base_url,
    enabled: model.enabled,
    priority: model.priority,
    health_status: model.last_health_status,
  }));
  return embedding
    ? [
        ...chat,
        {
          key: "embedding",
          kind: "embedding" as const,
          provider: embedding.provider,
          model_name: embedding.model_name,
          base_url: embedding.base_url,
          enabled: true,
          health_status: null,
        },
      ]
    : chat;
}

export function selectedChatModels(models: LlmModelConfig[]): {
  primary: LlmModelConfig | null;
  fallback: LlmModelConfig | null;
} {
  const enabled = [...models]
    .filter((model) => model.enabled)
    .sort((left, right) => left.priority - right.priority || left.id - right.id);
  return {
    primary: enabled[0] ?? null,
    fallback: enabled[1] ?? null,
  };
}
