import {
  Bot,
  Braces,
  CheckCircle2,
  CloudCog,
  Database,
  Edit3,
  KeyRound,
  Loader2,
  Plus,
  Power,
  RefreshCw,
  Save,
  ServerCog,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  createLlmModelConfig,
  deleteLlmModelConfig,
  getEmbeddingConfig,
  healthCheckLlmModelConfigs,
  listLlmModelConfigs,
  listLlmModels,
  reorderLlmModelConfigs,
  syncLlmModelConfigsToHermes,
  testEmbeddingConfig,
  testLlmConnection,
  updateEmbeddingConfig,
  updateLlmModelConfig,
  type EmbeddingConfig,
  type EmbeddingConnectionTestResult,
  type HermesLlmSyncResult,
  type IntegrationConfigSnapshot,
  type LlmConnectionTestResult,
  type LlmModelConfig,
  type LlmModelsResult,
} from "../services/configApi";
import { EmptyState, ResultNotice, statusLabel } from "./ui/opsPrimitives";
import {
  providerTemplate,
  providerTemplatesForKind,
  editFormForChatModel,
  hermesSyncNotice,
  selectedChatModels,
  unifiedModelLibrary,
  type UnifiedModelEntry,
} from "./llmConfigModel.ts";

export type LegacyLlmForm = {
  llm_provider: string;
  llm_base_url: string;
  llm_model: string;
  llm_api_key: string;
};

type ModelKind = "chat" | "embedding";

type ProviderForm = {
  template_id: string;
  provider: string;
  base_url: string;
  api_key: string;
  model: string;
};

type Props = {
  supervisorApiKey: string;
  legacyConfig: IntegrationConfigSnapshot | null;
  legacyForm: LegacyLlmForm;
  legacyModels: string[];
  legacyModelsResult: LlmModelsResult | null;
  legacyTestResult: LlmConnectionTestResult | null;
  legacyBusy: string | null;
  onLegacyFormChange: (form: LegacyLlmForm) => void;
  onLegacySave: () => void;
  onLegacyLoadModels: () => void;
  onLegacyTest: () => void;
};

function defaultProviderForm(kind: ModelKind): ProviderForm {
  const template = providerTemplatesForKind(kind)[0];
  return {
    template_id: template.id,
    provider: template.id,
    base_url: template.base_url,
    api_key: "",
    model: kind === "chat" ? template.chat_models[0] ?? "" : template.embedding_models[0] ?? "",
  };
}

export function LlmConfigConsole(props: Props) {
  const [modelKind, setModelKind] = useState<ModelKind>("chat");
  const [providerForm, setProviderForm] = useState<ProviderForm>(() =>
    defaultProviderForm("chat"),
  );
  const [chatModels, setChatModels] = useState<LlmModelConfig[]>([]);
  const [embedding, setEmbedding] = useState<EmbeddingConfig | null>(null);
  const [fetchedModels, setFetchedModels] = useState<string[]>([]);
  const [fetchResult, setFetchResult] = useState<LlmModelsResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [chatTest, setChatTest] = useState<LlmConnectionTestResult | null>(null);
  const [embeddingTest, setEmbeddingTest] =
    useState<EmbeddingConnectionTestResult | null>(null);
  const [editingChatId, setEditingChatId] = useState<number | null>(null);
  const [hermesSync, setHermesSync] = useState<HermesLlmSyncResult | null>(null);

  const activeAccessKey = props.supervisorApiKey.trim();
  const canManageModels = Boolean(activeAccessKey);

  const loadConsole = useCallback(async (apiKey: string) => {
    if (!apiKey) {
      setError("请在「高级配置」中输入 Supervisor 访问密钥。");
      return;
    }
    setBusy("load-llm-console");
    setError(null);
    try {
      const [models, embeddingConfig] = await Promise.all([
        listLlmModelConfigs(apiKey),
        getEmbeddingConfig(apiKey),
      ]);
      setChatModels(models);
      setEmbedding(embeddingConfig);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载模型库失败。");
    } finally {
      setBusy(null);
    }
  }, []);

  const refresh = useCallback(
    () => loadConsole(activeAccessKey),
    [activeAccessKey, loadConsole],
  );

  useEffect(() => {
    if (props.supervisorApiKey.trim()) {
      void loadConsole(props.supervisorApiKey.trim());
    }
  }, [loadConsole]);

  const template = providerTemplate(providerForm.template_id);
  const modelOptions = Array.from(
    new Set([
      ...(modelKind === "chat" ? template.chat_models : template.embedding_models),
      ...fetchedModels,
    ]),
  );
  const availableTemplates = providerTemplatesForKind(modelKind);

  function switchKind(kind: ModelKind) {
    setModelKind(kind);
    setProviderForm(() => ({
      ...defaultProviderForm(kind),
      api_key: "",
    }));
    setFetchedModels([]);
    setFetchResult(null);
    setChatTest(null);
    setEmbeddingTest(null);
    setEditingChatId(null);
  }

  function applyTemplate(templateId: string) {
    const next = providerTemplate(templateId);
    setProviderForm((current) => ({
      ...current,
      template_id: templateId,
      provider: next.id,
      base_url: next.base_url,
      api_key: "",
      model:
        modelKind === "chat"
          ? next.chat_models[0] ?? ""
          : next.embedding_models[0] ?? "",
    }));
    setFetchedModels([]);
    setFetchResult(null);
    setChatTest(null);
    setEmbeddingTest(null);
  }

  async function handleFetchModels() {
    if (!activeAccessKey) {
      setError("请在「高级配置」中输入 Supervisor 访问密钥后再拉取模型库。");
      return;
    }
    setBusy("fetch-provider-models");
    setError(null);
    setSuccess(null);
    setFetchResult(null);
    try {
      const result = await listLlmModels({
        llm_provider: providerForm.provider,
        llm_base_url: providerForm.base_url,
        llm_model: providerForm.model,
        llm_api_key: providerForm.api_key,
        model_config_id: editingChatId ?? undefined,
      }, activeAccessKey);
      setFetchResult(result);
      setFetchedModels(result.models);
      if (result.status !== "success") {
        setError(result.summary);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "拉取供应商模型库失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleAddModel() {
    if (!providerForm.base_url.trim()) {
      setError("供应商地址不能为空。");
      return;
    }
    if (!providerForm.model.trim()) {
      setError(modelKind === "chat" ? "请选择对话模型。" : "请选择向量模型。");
      return;
    }
    setBusy("add-model");
    setError(null);
    setSuccess(null);
    try {
      if (modelKind === "chat") {
        const result = editingChatId
          ? await updateLlmModelConfig(
              editingChatId,
              {
                provider: providerForm.provider.trim(),
                model_name: providerForm.model.trim(),
                base_url: providerForm.base_url.trim(),
                api_key: providerForm.api_key || undefined,
              },
              activeAccessKey,
            )
          : await createLlmModelConfig(
              {
                provider: providerForm.provider.trim(),
                model_name: providerForm.model.trim(),
                base_url: providerForm.base_url.trim(),
                api_key: providerForm.api_key,
                priority: chatModels.length,
                enabled: true,
              },
              activeAccessKey,
            );
        setHermesSync(result.hermes_sync);
      } else {
        await updateEmbeddingConfig(activeAccessKey, {
          provider: providerForm.provider,
          model_name: providerForm.model.trim(),
          base_url: providerForm.base_url.trim(),
          api_key: providerForm.api_key,
        });
      }
      setSuccess(
        modelKind === "chat"
          ? editingChatId
            ? "对话模型已更新。"
            : "对话模型已加入总模型库。"
          : "向量模型已保存。",
      );
      setProviderForm((current) => ({ ...current, api_key: "" }));
      setEditingChatId(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "添加模型失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleDeleteChat(model: LlmModelConfig) {
    if (!window.confirm(`确认删除对话模型 ${model.provider}/${model.model_name}？`)) {
      return;
    }
    setBusy(`delete-chat-${model.id}`);
    setError(null);
    try {
      const result = await deleteLlmModelConfig(model.id, activeAccessKey);
      setHermesSync(result.hermes_sync);
      if (editingChatId === model.id) {
        cancelChatEdit();
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除对话模型失败。");
    } finally {
      setBusy(null);
    }
  }

  async function reorderChat(nextIds: number[]) {
    setBusy("reorder-chat-models");
    setError(null);
    try {
      const result = await reorderLlmModelConfigs(nextIds, activeAccessKey);
      setChatModels(result.models);
      setHermesSync(result.hermes_sync);
    } catch (err) {
      setError(err instanceof Error ? err.message : "调整对话模型顺序失败。");
    } finally {
      setBusy(null);
    }
  }

  function startChatEdit(model: LlmModelConfig) {
    setModelKind("chat");
    setEditingChatId(model.id);
    setProviderForm(editFormForChatModel(model));
    setFetchedModels([]);
    setFetchResult(null);
    setError(null);
    setSuccess(null);
  }

  function cancelChatEdit() {
    setEditingChatId(null);
    setProviderForm(defaultProviderForm("chat"));
    setFetchedModels([]);
    setFetchResult(null);
  }

  async function toggleChat(model: LlmModelConfig) {
    setBusy(`toggle-chat-${model.id}`);
    setError(null);
    try {
      const result = await updateLlmModelConfig(
        model.id,
        { enabled: !model.enabled },
        activeAccessKey,
      );
      setHermesSync(result.hermes_sync);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新模型状态失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleHermesSync() {
    setBusy("sync-hermes");
    setError(null);
    try {
      setHermesSync(await syncLlmModelConfigsToHermes(activeAccessKey));
    } catch (err) {
      setError(err instanceof Error ? err.message : "同步 Hermes 配置失败。");
    } finally {
      setBusy(null);
    }
  }

  const selection = selectedChatModels(chatModels);

  async function selectPrimary(id: number) {
    const others = chatModels
      .filter((model) => model.id !== id)
      .map((model) => model.id);
    await reorderChat([id, ...others]);
  }

  async function selectFallback(id: number) {
    const primaryId = selection.primary?.id;
    if (!primaryId || primaryId === id) {
      return;
    }
    const others = chatModels
      .filter((model) => model.id !== primaryId && model.id !== id)
      .map((model) => model.id);
    await reorderChat([primaryId, id, ...others]);
  }

  async function handleHealthCheck() {
    setBusy("health-check-chat");
    setError(null);
    try {
      setChatModels(await healthCheckLlmModelConfigs(activeAccessKey));
    } catch (err) {
      setError(err instanceof Error ? err.message : "对话模型健康检查失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleTestProvider() {
    setBusy("test-provider");
    setError(null);
    setSuccess(null);
    setChatTest(null);
    setEmbeddingTest(null);
    try {
      if (modelKind === "chat") {
        setChatTest(
          await testLlmConnection({
            llm_provider: providerForm.provider,
            llm_base_url: providerForm.base_url,
            llm_model: providerForm.model,
            llm_api_key: providerForm.api_key,
            model_config_id: editingChatId ?? undefined,
          }, activeAccessKey),
        );
      } else {
        setEmbeddingTest(
          await testEmbeddingConfig(activeAccessKey, {
            provider: providerForm.provider,
            model_name: providerForm.model,
            base_url: providerForm.base_url,
            api_key: providerForm.api_key,
          }),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "模型测试失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleTestSelectedEmbedding() {
    if (!embedding) {
      return;
    }
    setBusy("test-selected-embedding");
    setError(null);
    setEmbeddingTest(null);
    try {
      setEmbeddingTest(
        await testEmbeddingConfig(activeAccessKey, {
          provider: embedding.provider,
          model_name: embedding.model_name,
          base_url: embedding.base_url,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "向量模型测试失败。");
    } finally {
      setBusy(null);
    }
  }

  const library = unifiedModelLibrary(chatModels, embedding);
  const hermesNotice = hermesSync ? hermesSyncNotice(hermesSync) : null;

  return (
    <div className="llm-config-console">
      <section className="panel llm-access-key-panel">
        <div className="llm-access-key-status">
          {canManageModels ? (
            <span className="field-hint">已使用「高级配置」中的 Supervisor 访问密钥。</span>
          ) : (
            <span className="field-hint">请先在下方「高级配置」中输入 Supervisor 访问密钥，再管理 LLM 配置。</span>
          )}
        </div>
        <button
          className="secondary-button"
          type="button"
          disabled={!canManageModels || busy === "load-llm-console"}
          onClick={() => void refresh()}
        >
          {busy === "load-llm-console" ? (
            <Loader2 className="spin" size={16} />
          ) : (
            <RefreshCw size={16} />
          )}
          加载模型配置
        </button>
      </section>
      <section className="panel llm-supplier-panel">
        <div className="panel-title-row">
          <div>
            <h3>模型配置</h3>
            <p className="muted">先选择模型类型，再添加供应商并拉取具体模型</p>
          </div>
          <ServerCog size={20} />
        </div>

        <div className="llm-kind-switch" role="group" aria-label="模型类型">
          <button
            type="button"
            className={modelKind === "chat" ? "active" : ""}
            onClick={() => switchKind("chat")}
          >
            <Bot size={17} />
            对话模型
          </button>
          <button
            type="button"
            className={modelKind === "embedding" ? "active" : ""}
            onClick={() => switchKind("embedding")}
          >
            <Braces size={17} />
            向量模型
          </button>
        </div>

        <div className="llm-provider-templates">
          {availableTemplates.map((item) => (
            <button
              key={item.id}
              type="button"
              className={providerForm.template_id === item.id ? "active" : ""}
              onClick={() => applyTemplate(item.id)}
            >
              <CloudCog size={16} />
              <strong>{item.label}</strong>
              <small>
                {modelKind === "chat"
                  ? item.chat_models.length
                    ? `${item.chat_models.length} 个默认模型`
                    : "自定义模型"
                  : item.embedding_models.length
                    ? `${item.embedding_models.length} 个默认模型`
                    : "自定义模型"}
              </small>
            </button>
          ))}
        </div>

        <div className="form-grid">
          <label>
            供应商名称
            <input
              value={providerForm.provider}
              onChange={(event) =>
                setProviderForm({
                  ...providerForm,
                  provider: event.target.value,
                  api_key: "",
                })
              }
            />
          </label>
          <label>
            接口地址
            <input
              value={providerForm.base_url}
              onChange={(event) =>
                setProviderForm({
                  ...providerForm,
                  base_url: event.target.value,
                  api_key: "",
                })
              }
              placeholder="https://api.example.com/v1"
            />
          </label>
          <label className="form-field-wide">
            API key
            <input
              type="password"
              value={providerForm.api_key}
              onChange={(event) =>
                setProviderForm({ ...providerForm, api_key: event.target.value })
              }
              placeholder={
                editingChatId ? "留空保留已保存的 API key" : "供应商 API key"
              }
            />
          </label>
        </div>

        <label>
          {modelKind === "chat" ? "对话模型" : "向量模型"}
          <input
            list="llm-provider-model-options"
            value={providerForm.model}
            onChange={(event) =>
              setProviderForm({ ...providerForm, model: event.target.value })
            }
          />
          <datalist id="llm-provider-model-options">
            {modelOptions.map((model) => (
              <option key={model} value={model} />
            ))}
          </datalist>
        </label>

        <div className="action-strip model-config-actions">
          <button
            className="secondary-button"
            type="button"
            disabled={
              busy === "fetch-provider-models" ||
              !providerForm.base_url
            }
            onClick={() => void handleFetchModels()}
          >
            {busy === "fetch-provider-models" ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            拉取模型库
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={
              !canManageModels || busy === "test-provider" || !providerForm.model
            }
            onClick={() => void handleTestProvider()}
          >
            {busy === "test-provider" ? <Loader2 className="spin" size={16} /> : <KeyRound size={16} />}
            {modelKind === "chat" ? "测试对话" : "测试向量"}
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={!canManageModels || busy === "add-model"}
            onClick={() => void handleAddModel()}
          >
            {editingChatId ? <Save size={16} /> : <Plus size={16} />}
            {modelKind === "chat"
              ? editingChatId
                ? "保存模型修改"
                : "添加对话模型"
              : "保存向量模型"}
          </button>
          {editingChatId ? (
            <button
              className="secondary-button"
              type="button"
              disabled={Boolean(busy)}
              onClick={cancelChatEdit}
            >
              取消编辑
            </button>
          ) : null}
        </div>

        {error ? <div className="connection-result warning"><strong>{error}</strong></div> : null}
        {success ? <ResultNotice status="success" summary={success} /> : null}
        {fetchResult ? (
          <ResultNotice
            status={fetchResult.status}
            summary={fetchResult.summary}
            detail={
              fetchResult.models.length
                ? `已返回 ${fetchResult.models.length} 个模型`
                : undefined
            }
          />
        ) : null}
        {chatTest ? (
          <ResultNotice
            status={chatTest.status}
            summary={chatTest.summary}
            detail={`${chatTest.provider} · ${chatTest.model}`}
          />
        ) : null}
        {embeddingTest ? (
          <ResultNotice
            status={embeddingTest.status}
            summary={embeddingTest.summary}
            detail={`${embeddingTest.provider} · ${embeddingTest.model_name}`}
          />
        ) : null}
      </section>

      <section className="panel llm-library-panel">
        <div className="panel-title-row">
          <div>
            <h3>总模型库</h3>
            <p className="muted">已添加的对话模型与向量模型</p>
          </div>
          <button
            className="icon-button"
              type="button"
              title="刷新模型库"
              disabled={!canManageModels || busy === "load-llm-console"}
              onClick={() => void refresh()}
          >
            {busy === "load-llm-console" ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
          </button>
        </div>
        {library.length ? (
          <div className="llm-library-list">
            {library.map((entry) => (
              <LibraryRow
                key={entry.key}
                entry={entry}
                busy={canManageModels ? busy : "access-required"}
                onEdit={
                  entry.kind === "chat"
                    ? () => {
                        const model = chatModels.find(
                          (item) => item.id === Number(entry.key.slice(5)),
                        );
                        if (model) startChatEdit(model);
                      }
                    : undefined
                }
                onToggle={
                  entry.kind === "chat"
                    ? () => {
                        const model = chatModels.find(
                          (item) => item.id === Number(entry.key.slice(5)),
                        );
                        if (model) void toggleChat(model);
                      }
                    : undefined
                }
                onDelete={
                  entry.kind === "chat"
                    ? () =>
                        void handleDeleteChat(
                          chatModels.find(
                            (model) => model.id === Number(entry.key.slice(5)),
                          )!,
                        )
                    : undefined
                }
              />
            ))}
          </div>
        ) : (
          <EmptyState text="还没有模型。添加供应商和模型后会进入总模型库。" />
        )}
      </section>

      <section className="panel llm-selection-panel">
        <div className="panel-title-row">
          <div>
            <h3>模型选择</h3>
            <p className="muted">指定主对话、备用对话和向量模型</p>
          </div>
          <Bot size={20} />
        </div>

        <div className="llm-selection-grid">
          <label>
            主对话模型
            <select
              value={selection.primary?.id ?? ""}
              onChange={(event) => void selectPrimary(Number(event.target.value))}
              disabled={
                !canManageModels || !chatModels.some((model) => model.enabled)
              }
            >
              <option value="">未选择</option>
              {chatModels
                .filter((model) => model.enabled)
                .map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.provider}/{model.model_name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            次对话模型
            <select
              value={selection.fallback?.id ?? ""}
              onChange={(event) => void selectFallback(Number(event.target.value))}
              disabled={
                !selection.primary ||
                !canManageModels ||
                chatModels.filter((model) => model.enabled).length < 2
              }
            >
              <option value="">未选择</option>
              {chatModels
                .filter((model) => model.enabled && model.id !== selection.primary?.id)
                .map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.provider}/{model.model_name}
                  </option>
                ))}
            </select>
          </label>
          <div className="llm-selection-card">
            <Database size={17} />
            <div>
              <strong>向量模型</strong>
              <small>
                {embedding
                  ? `${embedding.provider}/${embedding.model_name}`
                  : "未配置"}
              </small>
            </div>
            <CheckCircle2 className={embedding ? "success" : ""} size={17} />
          </div>
        </div>

        <div className="action-strip model-config-actions">
          <button
            className="secondary-button"
            type="button"
            disabled={
              !canManageModels ||
              busy === "health-check-chat" ||
              chatModels.length === 0
            }
            onClick={() => void handleHealthCheck()}
          >
            <RefreshCw size={16} /> 检查对话模型
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={
              !canManageModels || busy === "test-selected-embedding" || !embedding
            }
            onClick={() => void handleTestSelectedEmbedding()}
          >
            <KeyRound size={16} /> 检查向量模型
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={
              !canManageModels || busy === "sync-hermes"
            }
            onClick={() => void handleHermesSync()}
          >
            {busy === "sync-hermes" ? (
              <Loader2 className="spin" size={16} />
            ) : (
              <RefreshCw size={16} />
            )}
            重新同步 Hermes
          </button>
        </div>
        {hermesSync ? (
          <ResultNotice
            status={hermesNotice!.status}
            summary={hermesSync.summary}
            detail={hermesNotice!.detail}
          />
        ) : null}
      </section>

      <details className="advanced-details llm-legacy-config">
        <summary>旧版兼容配置</summary>
        <div className="panel">
          <div className="panel-title-row">
            <h3>内置 Agent 单模型配置</h3>
            <span className="muted">向后兼容，不再作为主配置入口</span>
          </div>
          <div className="metric-grid">
            <div className="metric">
              <span>当前供应商</span>
              <strong>
                {props.legacyConfig?.llm_provider ?? props.legacyForm.llm_provider}
              </strong>
            </div>
            <div className="metric">
              <span>当前模型</span>
              <strong>
                {props.legacyConfig?.llm_model ?? props.legacyForm.llm_model}
              </strong>
            </div>
          </div>
          <div className="form-grid">
            <label>
              供应商
              <input
                value={props.legacyForm.llm_provider}
                onChange={(event) =>
                  props.onLegacyFormChange({
                    ...props.legacyForm,
                    llm_provider: event.target.value,
                  })
                }
              />
            </label>
            <label>
              模型
              <input
                value={props.legacyForm.llm_model}
                onChange={(event) =>
                  props.onLegacyFormChange({
                    ...props.legacyForm,
                    llm_model: event.target.value,
                  })
                }
              />
            </label>
            <label className="form-field-wide">
              接口地址
              <input
                value={props.legacyForm.llm_base_url}
                onChange={(event) =>
                  props.onLegacyFormChange({
                    ...props.legacyForm,
                    llm_base_url: event.target.value,
                  })
                }
              />
            </label>
            <label className="form-field-wide">
              API key
              <input
                type="password"
                value={props.legacyForm.llm_api_key}
                onChange={(event) =>
                  props.onLegacyFormChange({
                    ...props.legacyForm,
                    llm_api_key: event.target.value,
                  })
                }
              />
            </label>
          </div>
          <div className="action-strip model-config-actions">
            <button
              className="primary-button"
              type="button"
              disabled={props.legacyBusy === "save-integration-config"}
              onClick={props.onLegacySave}
            >
              <Save size={16} /> 保存
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={props.legacyBusy === "load-llm-models"}
              onClick={props.onLegacyLoadModels}
            >
              <RefreshCw size={16} /> 拉取模型
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={props.legacyBusy === "test-llm-connection"}
              onClick={props.onLegacyTest}
            >
              <KeyRound size={16} /> 测试连接
            </button>
          </div>
          {props.legacyTestResult ? (
            <ResultNotice
              status={props.legacyTestResult.status}
              summary={props.legacyTestResult.summary}
              detail={statusLabel(props.legacyTestResult.status)}
            />
          ) : null}
          {props.legacyModelsResult ? (
            <div
              className={`connection-result ${
                props.legacyModelsResult.status === "success" ? "success" : "warning"
              }`}
            >
              <strong>{props.legacyModelsResult.summary}</strong>
              {props.legacyModels.length ? (
                <span>{props.legacyModels.join(" · ")}</span>
              ) : null}
            </div>
          ) : null}
        </div>
      </details>
    </div>
  );
}

function LibraryRow(props: {
  entry: UnifiedModelEntry;
  busy: string | null;
  onEdit?: () => void;
  onToggle?: () => void;
  onDelete?: () => void;
}) {
  const health = healthText(props.entry.health_status);
  return (
    <div className="llm-library-row">
      <div className="llm-library-kind">
        {props.entry.kind === "chat" ? <Bot size={16} /> : <Braces size={16} />}
      </div>
      <div>
        <strong>{props.entry.model_name}</strong>
        <small>
          {props.entry.provider} · {props.entry.base_url}
        </small>
      </div>
      <span className={`tag ${health.tone}`}>{health.text}</span>
      <span className={`status-chip ${props.entry.enabled ? "success" : "muted"}`}>
        {props.entry.kind === "chat"
          ? props.entry.enabled
            ? "已启用"
            : "已停用"
          : "向量"}
      </span>
      {props.onEdit ? (
        <button
          className="icon-button"
          type="button"
          title="编辑模型"
          disabled={Boolean(props.busy)}
          onClick={props.onEdit}
        >
          <Edit3 size={14} />
        </button>
      ) : null}
      {props.onToggle ? (
        <button
          className="icon-button"
          type="button"
          title={props.entry.enabled ? "停用模型" : "启用模型"}
          disabled={Boolean(props.busy)}
          onClick={props.onToggle}
        >
          <Power size={14} />
        </button>
      ) : null}
      {props.onDelete ? (
        <button
          className="icon-button danger"
          type="button"
          title="删除模型"
          disabled={Boolean(props.busy)}
          onClick={props.onDelete}
        >
          <Trash2 size={14} />
        </button>
      ) : null}
    </div>
  );
}

function healthText(
  status: string | null | undefined,
): { text: string; tone: string } {
  if (status === "ok") {
    return { text: "健康", tone: "tag-success" };
  }
  if (status === "fail") {
    return { text: "异常", tone: "tag-warning" };
  }
  return { text: "未检查", tone: "tag-muted" };
}
