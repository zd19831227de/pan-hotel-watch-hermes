import { BrainCircuit, Download, Play, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  askAndExtract,
  checkChatLoginState,
  openChatLogin,
  type LlmChatPlatform,
} from "../services/llmChatApi.ts";
import {
  createBrowserSession,
  deleteBrowserSession,
  listBrowserSessions,
  type BrowserSession,
} from "../services/browserSessionsApi.ts";
import {
  defaultAccountFormForPlatform,
  isLlmChatPlatform,
  llmChatDefaultUrl,
  type AccountPlatform,
} from "./accountPlatformModel.ts";
import {
  createDistillSource,
  deleteDistillSource,
  exportDistillRun,
  fetchDistillMeta,
  fetchDistillRuns,
  fetchDistillSources,
  patchDistillSource,
  triggerDistillRun,
  triggerSourceRun,
  type DistillPlatform,
  type DistillRun,
  type DistillSource,
  type DistillSourceType,
} from "../services/distillApi.ts";

type Notice = { tone: "success" | "error"; text: string } | null;

const SOURCE_TYPE_OPTIONS: Array<{ value: DistillSourceType; label: string }> = [
  { value: "text", label: "文本" },
  { value: "image_url", label: "图片链接" },
  { value: "video_url", label: "视频链接" },
];

function platformLabel(platform: string): string {
  return platform === "qwen" ? "千问" : "豆包";
}

function statusLabel(status: string): string {
  if (status === "success") return "成功入库";
  if (status === "rejected") return "拒绝（准入）";
  if (status === "human_required") return "需要人工（登录/验证）";
  if (status === "config_required") return "未配置对话账号";
  if (status === "parse_failed") return "提取失败";
  return status;
}

export function DistillPanel(props: { supervisorApiKey: string }) {
  const [sources, setSources] = useState<DistillSource[]>([]);
  const [runs, setRuns] = useState<DistillRun[]>([]);
  const [metaRule, setMetaRule] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const [form, setForm] = useState({
    source_type: "text" as DistillSourceType,
    content: "",
    platform: "doubao" as DistillPlatform,
    title: "",
    distill_prompt: "",
    schedule_interval_minutes: 120,
  });
  const [onceForm, setOnceForm] = useState({
    source_type: "text" as DistillSourceType,
    content: "",
    platform: "doubao" as DistillPlatform,
    distill_prompt: "",
  });
  const [chatForm, setChatForm] = useState({
    platform: "doubao" as LlmChatPlatform,
    platform_account_id: "",
    prompt: "",
  });
  const [chatResult, setChatResult] = useState<string>("");
  const [llmSessions, setLlmSessions] = useState<BrowserSession[]>([]);
  const [selectedLlmId, setSelectedLlmId] = useState<string>("");
  const [llmAccountForm, setLlmAccountForm] = useState(() => ({
    platform_account_id: "",
    chat_url: "",
    ...defaultAccountFormForPlatform("doubao"),
  }));

  const reload = useCallback(async () => {
    if (!props.supervisorApiKey) {
      setNotice({
        tone: "error",
        text: "需要 supervisor API 密钥才能使用自学习。请先在上方输入访问密钥。",
      });
      return;
    }
    setBusy("reload");
    try {
      const [sourceList, runList, meta, sessions] = await Promise.all([
        fetchDistillSources(),
        fetchDistillRuns(20),
        fetchDistillMeta(),
        listBrowserSessions().catch(() => [] as BrowserSession[]),
      ]);
      setSources(sourceList);
      setRuns(runList);
      setMetaRule(meta.admission_rule);
      const llmOnly = sessions.filter((session) => isLlmChatPlatform(session.platform));
      setLlmSessions(llmOnly);
      setSelectedLlmId((current) => current || llmOnly[0]?.browser_session_id || "");
    } catch (error) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "加载自学习数据失败。",
      });
    } finally {
      setBusy(null);
    }
  }, [props.supervisorApiKey]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const run = async (key: string, action: () => Promise<string>) => {
    setBusy(key);
    setNotice(null);
    try {
      setNotice({ tone: "success", text: await action() });
      await reload();
    } catch (error) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "操作失败。",
      });
    } finally {
      setBusy(null);
    }
  };

  const downloadRunsJson = () => {
    const blob = new Blob([JSON.stringify(runs, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `distill-runs-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const downloadRunCsv = async (runId: number) => {
    try {
      const blob = await exportDistillRun(runId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `distill-run-${runId}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "导出失败。",
      });
    }
  };

  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <h2>自学习蒸馏</h2>
          <p>
            把文本/图片/视频素材经豆包/千问对话提炼为知识并入库，供运营问答检索。
            属知识管理模块，需 supervisor 访问密钥；先在「账号与浏览器」新增豆包/千问 AI 对话账号并登录。
          </p>
        </div>
        <div className="inline-edit-row">
          <button
            className="secondary-button"
            type="button"
            disabled={!props.supervisorApiKey}
            onClick={() => setShowHistory((open) => !open)}
          >
            {showHistory ? "收起历史记录" : "查看历史记录"}
          </button>
          <button className="secondary-button" type="button" disabled={busy === "reload" || !props.supervisorApiKey} onClick={() => void reload()}>
            <RefreshCw size={16} /> 刷新
          </button>
        </div>
      </div>
      {notice ? <p className={notice.tone === "success" ? "notice-success" : "notice-error"}>{notice.text}</p> : null}

      <div className="panel">
        <h3>数据源（定时学习）</h3>
        <div className="form-grid">
          <label>
            类型
            <select
              value={form.source_type}
              onChange={(event) => setForm({ ...form, source_type: event.target.value as DistillSourceType })}
            >
              {SOURCE_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label>
            平台
            <select
              value={form.platform}
              onChange={(event) => setForm({ ...form, platform: event.target.value as DistillPlatform })}
            >
              <option value="doubao">豆包</option>
              <option value="qwen">千问</option>
            </select>
          </label>
          <label>
            标题（可选）
            <input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} />
          </label>
          <label>
            定时间隔（分钟，0=仅手动）
            <input
              value={form.schedule_interval_minutes}
              inputMode="numeric"
              onChange={(event) => setForm({ ...form, schedule_interval_minutes: Number(event.target.value) || 0 })}
            />
            <span className="field-hint">最小 30 分钟，0=仅手动</span>
          </label>
          <label>
            内容 / 链接
            <input
              value={form.content}
              placeholder="知识点文本，或 http(s) 图片/视频链接"
              onChange={(event) => setForm({ ...form, content: event.target.value })}
            />
          </label>
          <label>
            自定义提炼提示词（可选）
            <input
              value={form.distill_prompt}
              onChange={(event) => setForm({ ...form, distill_prompt: event.target.value })}
            />
          </label>
        </div>
        <button
          className="primary-button"
          type="button"
          disabled={busy === "create-source" || !form.content.trim()}
          onClick={() =>
            void run("create-source", async () => {
              await createDistillSource({ ...form, content: form.content.trim() });
              setForm({ ...form, content: "", title: "" });
              return "数据源已创建。";
            })
          }
        >
          <Plus size={16} /> 新增数据源
        </button>

        <div className="table-list">
          {sources.map((source) => (
            <div className="account-row" key={source.id}>
              <div>
                <strong>{source.title || source.content.slice(0, 40)}</strong>
                <span>
                  {platformLabel(source.platform)} · {SOURCE_TYPE_OPTIONS.find((o) => o.value === source.source_type)?.label ?? source.source_type}
                  {source.enabled ? "" : " · 已停用"}
                </span>
                <span>
                  定时间隔 {source.schedule_interval_minutes > 0 ? `${source.schedule_interval_minutes} 分钟` : "仅手动"}
                  {source.last_run_at ? ` · 上次 ${source.last_run_at}` : " · 从未运行"}
                </span>
              </div>
              <div className="row-actions">
                <button
                  type="button"
                  title="立即运行"
                  disabled={busy === `run-source-${source.id}`}
                  onClick={() =>
                    void run(`run-source-${source.id}`, async () => {
                      const result = await triggerSourceRun(source.id);
                      return `已触发：${statusLabel(result.status)}${result.reject_reason ? `（${result.reject_reason}）` : ""}`;
                    })
                  }
                >
                  <Play size={16} />
                </button>
                <button
                  type="button"
                  title={source.enabled ? "停用" : "启用"}
                  disabled={busy === `toggle-source-${source.id}`}
                  onClick={() =>
                    void run(`toggle-source-${source.id}`, async () => {
                      await patchDistillSource(source.id, { enabled: !source.enabled });
                      return source.enabled ? "已停用。" : "已启用。";
                    })
                  }
                >
                  <RefreshCw size={16} />
                </button>
                <button
                  type="button"
                  title="删除"
                  disabled={busy === `delete-source-${source.id}`}
                  onClick={() =>
                    void run(`delete-source-${source.id}`, async () => {
                      await deleteDistillSource(source.id);
                      return "数据源已删除。";
                    })
                  }
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
          {!sources.length ? <p className="field-hint">还没有数据源。上方创建后可手动或定时蒸馏。</p> : null}
        </div>
      </div>

      <div className="panel">
        <h3>一次性蒸馏（人工投喂，不落数据源）</h3>
        <div className="form-grid">
          <label>
            类型
            <select
              value={onceForm.source_type}
              onChange={(event) => setOnceForm({ ...onceForm, source_type: event.target.value as DistillSourceType })}
            >
              {SOURCE_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label>
            平台
            <select
              value={onceForm.platform}
              onChange={(event) => setOnceForm({ ...onceForm, platform: event.target.value as DistillPlatform })}
            >
              <option value="doubao">豆包</option>
              <option value="qwen">千问</option>
            </select>
          </label>
          <label>
            内容 / 链接
            <input
              value={onceForm.content}
              placeholder="要学习的文本或 http(s) 链接"
              onChange={(event) => setOnceForm({ ...onceForm, content: event.target.value })}
            />
          </label>
        </div>
        <button
          className="primary-button"
          type="button"
          disabled={busy === "run-once" || !onceForm.content.trim()}
          onClick={() =>
            void run("run-once", async () => {
              const result = await triggerDistillRun({ ...onceForm, content: onceForm.content.trim() });
              if (result.status === "human_required") {
                return "对话账号登录已失效：请到「账号与浏览器」打开豆包/千问登录窗口。";
              }
              if (result.status === "config_required") {
                return "未找到对话账号：请先在「账号与浏览器」新增豆包/千问 AI 对话账号并登录。";
              }
              return `蒸馏完成：${statusLabel(result.status)}${result.knowledge_title ? ` · ${result.knowledge_title}` : ""}${result.reject_reason ? `（${result.reject_reason}）` : ""}`;
            })
          }
        >
          <BrainCircuit size={16} /> 立即蒸馏
        </button>
      </div>

      <div className="panel">
        <h3>对话账号管理</h3>
        <p className="field-hint">
          豆包/千问 AI 对话账号随自学习一并管理（属知识模块，需 supervisor 密钥）。先新增账号并登录，再用于蒸馏/试问。
        </p>
        <div className="table-list">
          {llmSessions.map((session) => (
            <div className="account-row" key={session.browser_session_id}>
              <div>
                <strong>{session.display_name || session.platform_account_id}</strong>
                <span>
                  {session.platform === "qwen" ? "千问" : "豆包"} · {session.platform_account_id} · 端口 {session.session_port}
                </span>
                <span>登录态：{session.login_state ?? "未知"}{session.chat_url ? ` · Chat 地址：${session.chat_url}` : ""}</span>
              </div>
              <div className="row-actions">
                <button
                  className="account-text-button"
                  type="button"
                  title="打开登录"
                  disabled={busy === `llm-login-${session.browser_session_id}`}
                  onClick={() =>
                    void run(`llm-login-${session.browser_session_id}`, async () => {
                      const platform = (session.platform === "qwen" ? "qwen" : "doubao") as LlmChatPlatform;
                      const result = await openChatLogin(platform, session.platform_account_id, {
                        browser_session_id: session.browser_session_id,
                      });
                      return `已打开登录窗口：${result.summary || result.status}`;
                    })
                  }
                >
                  打开登录
                </button>
                <button
                  className="account-text-button"
                  type="button"
                  title="检查登录态"
                  disabled={busy === `llm-check-${session.browser_session_id}`}
                  onClick={() =>
                    void run(`llm-check-${session.browser_session_id}`, async () => {
                      const platform = (session.platform === "qwen" ? "qwen" : "doubao") as LlmChatPlatform;
                      const result = await checkChatLoginState(platform, session.platform_account_id, {
                        browser_session_id: session.browser_session_id,
                      });
                      return `登录态：${result.login_state ?? result.status}（${result.summary || ""}）`;
                    })
                  }
                >
                  检查登录态
                </button>
                <button
                  type="button"
                  title="删除"
                  disabled={busy === `llm-delete-${session.browser_session_id}`}
                  onClick={() =>
                    void run(`llm-delete-${session.browser_session_id}`, async () => {
                      await deleteBrowserSession(session.browser_session_id);
                      return "对话账号已删除。";
                    })
                  }
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
          {!llmSessions.length ? <p className="field-hint">还没有对话账号。下方新增后登录即可用于蒸馏。</p> : null}
        </div>

        <div className="form-grid">
          <label>
            平台
            <select
              value={llmAccountForm.platform}
              onChange={(event) => {
                const platform = event.target.value as AccountPlatform;
                setLlmAccountForm({
                  ...llmAccountForm,
                  ...defaultAccountFormForPlatform(platform),
                  platform_account_id: llmAccountForm.platform_account_id,
                  chat_url: llmAccountForm.chat_url,
                });
              }}
            >
              <option value="doubao">豆包</option>
              <option value="qwen">千问</option>
            </select>
          </label>
          <label>
            平台账号 ID
            <input
              value={llmAccountForm.platform_account_id}
              placeholder="如 doubao_main"
              onChange={(event) => setLlmAccountForm({ ...llmAccountForm, platform_account_id: event.target.value })}
            />
          </label>
          <label>
            Chat 页面地址（可选）
            <input
              value={llmAccountForm.chat_url}
              placeholder={llmChatDefaultUrl(llmAccountForm.platform)}
              onChange={(event) => setLlmAccountForm({ ...llmAccountForm, chat_url: event.target.value })}
            />
            <span className="field-hint">留空用平台默认地址（{llmChatDefaultUrl(llmAccountForm.platform)}）。</span>
          </label>
        </div>
        <button
          className="primary-button"
          type="button"
          disabled={busy === "create-llm-account" || !llmAccountForm.platform_account_id.trim()}
          onClick={() =>
            void run("create-llm-account", async () => {
              await createBrowserSession({
                platform_account_id: llmAccountForm.platform_account_id.trim(),
                display_name: llmAccountForm.platform_account_id.trim(),
                platform: llmAccountForm.platform,
                intended_use: "llm_chat",
                connection_mode: "unknown",
                session_port: Number(llmAccountForm.session_port),
                chat_url: llmAccountForm.chat_url.trim() || undefined,
              });
              setLlmAccountForm({
                platform_account_id: "",
                chat_url: "",
                ...defaultAccountFormForPlatform(llmAccountForm.platform),
              });
              return "对话账号已创建。";
            })
          }
        >
          <Plus size={16} /> 新增对话账号
        </button>
      </div>

      <div className="panel">
        <h3>试问（验证提取链路）</h3>
        <div className="form-grid">
          <label>
            对话账号
            <select value={selectedLlmId} onChange={(event) => setSelectedLlmId(event.target.value)}>
              {!llmSessions.length ? <option value="">暂无对话账号</option> : null}
              {llmSessions.map((session) => (
                <option key={session.browser_session_id} value={session.browser_session_id}>
                  {session.display_name || session.platform_account_id}
                </option>
              ))}
            </select>
          </label>
          <label>
            问题
            <input
              value={chatForm.prompt}
              placeholder="输入一句话问题"
              onChange={(event) => setChatForm({ ...chatForm, prompt: event.target.value })}
            />
          </label>
        </div>
        <button
          className="secondary-button"
          type="button"
          disabled={busy === "chat-ask" || !chatForm.prompt.trim() || !selectedLlmId}
          onClick={() => {
            const target = llmSessions.find((session) => session.browser_session_id === selectedLlmId);
            if (!target) {
              setNotice({ tone: "error", text: "请先选择对话账号。" });
              return;
            }
            const platform = (target.platform === "qwen" ? "qwen" : "doubao") as LlmChatPlatform;
            void run("chat-ask", async () => {
              const result = await askAndExtract(platform, target.platform_account_id, {
                browser_session_id: target.browser_session_id,
                prompt: chatForm.prompt.trim(),
              });
              setChatResult(String(result.answer_text ?? result.summary ?? ""));
              return "试问完成。";
            });
          }}
        >
          提问并提取
        </button>
        {chatResult ? <pre className="code-block">{chatResult}</pre> : null}
      </div>

      {showHistory ? (
        <>
      <div className="panel">
        <div className="section-head">
          <h3>运行记录</h3>
          <button className="secondary-button" type="button" onClick={downloadRunsJson}>
            <Download size={16} /> 导出 JSON
          </button>
        </div>
        <div className="table-list">
          {runs.map((item) => (
            <div className="account-row" key={item.id}>
              <div>
                <strong>{item.knowledge_title || `运行 #${item.id}`}</strong>
                <span>
                  {platformLabel(item.platform)} · {SOURCE_TYPE_OPTIONS.find((o) => o.value === item.source_type)?.label ?? item.source_type}
                  · {statusLabel(item.status)}
                </span>
                <span>{item.created_at}{item.rag_document_id ? ` · RAG 文档 #${item.rag_document_id}` : ""}</span>
                {item.reject_reason ? <span className="field-hint">拒绝原因：{item.reject_reason}</span> : null}
              </div>
              <div className="row-actions">
                <button
                  type="button"
                  title="导出本次运行（CSV）"
                  disabled={busy === `export-run-${item.id}`}
                  onClick={() => {
                    setBusy(`export-run-${item.id}`);
                    void downloadRunCsv(item.id).finally(() => setBusy(null));
                  }}
                >
                  <Download size={16} />
                </button>
              </div>
            </div>
          ))}
          {!runs.length ? <p className="field-hint">还没有运行记录。</p> : null}
        </div>
        <p className="field-hint">
          蒸馏成功的知识会进入「高级维护 → 知识管理」的 RAG 文档列表，供飞书问答检索。
        </p>
      </div>

      <div className="panel">
        <h3>准入规则</h3>
        <p className="field-hint">
          {metaRule || "蒸馏入库只允许知识类内容；实时数据（价格/房态/经营数字）一律拒绝。"}
        </p>
      </div>
        </>
      ) : null}
    </section>
  );
}
