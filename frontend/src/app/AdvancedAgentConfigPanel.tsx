import { Plus, RefreshCw, Save, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { BrowserSession } from "../services/browserSessionsApi";
import type { CompetitorWatchGroup } from "../services/competitorWatchApi";
import type {
  FeishuAgentProfile,
  FeishuChatAgentBinding,
  FeishuChatAgentBindingPatch,
  FeishuRole,
  FeishuRoleAssignment,
  SupervisorOverview,
  SupervisorWatchGroup,
} from "../services/configApi";
import { FeishuAdmissionPanel } from "./FeishuAdmissionPanel";

type RoleSaveInput = {
  profileKey: string;
  assignment: FeishuRoleAssignment | undefined;
  role: FeishuRole;
  grantedBy: string;
};

export type ChatBindingCreateInput = {
  chatId: string;
  displayName: string;
  profileKey: string;
  ebkGroupKey: string;
  competitorGroupKey: string;
};

export type AdvancedAgentConfigPanelProps = {
  overview: SupervisorOverview | null;
  supervisorApiKey: string;
  loading: boolean;
  busy: string | null;
  error: string | null;
  actionNotice: { tone: "success" | "error" | "info"; text: string } | null;
  // 每次总览刷新 +1；准入面板据此重读 Hermes YAML，新增绑定后无需刷新页面。
  admissionRevision: number;
  sessions: BrowserSession[];
  competitorGroups: CompetitorWatchGroup[];
  onApiKeyChange: (value: string) => void;
  onLoad: () => void;
  onPatchProfile: (profile: FeishuAgentProfile, enabled: boolean) => void;
  onCreateProfile: (input: { profileKey: string; displayName: string; enabled: boolean }) => void;
  onDeleteProfile: (profile: FeishuAgentProfile) => void;
  onSetProfileCredentials: (profile: FeishuAgentProfile, input: { appId: string; appSecret: string; displayName: string | null }) => void;
  onPatchBinding: (binding: FeishuChatAgentBinding, input: FeishuChatAgentBindingPatch) => void;
  onCreateChatBinding: (input: ChatBindingCreateInput) => void;
  onDeleteBinding: (binding: FeishuChatAgentBinding) => void;
  onSaveManualPricingChat: (session: BrowserSession, chatId: string | null) => void;
  onSaveCompetitorPricingAccount: (group: CompetitorWatchGroup, browserSessionId: string | null) => void;
  onBindEbkGroupChat: (groupKey: string, chatId: string | null) => void;
  onBindCompetitorGroupChat: (groupKey: string, chatId: string | null) => void;
  onSaveRole: (input: RoleSaveInput) => void;
};

const roleLabels: Record<FeishuRole, string> = {
  viewer: "只读",
  operator: "运营员",
  supervisor: "主管",
};

function groupLabelsForChat(
  chatId: string | null | undefined,
  groups: SupervisorWatchGroup[],
): string {
  const labels = groups
    .filter((group) => Boolean(chatId) && group.feishu_chat_id === chatId)
    .map((group) => group.name || group.group_key);
  return labels.length ? labels.join("、") : "未绑定监控组";
}

function primaryWebhookAddress(group: SupervisorWatchGroup): string {
  const rawUrl =
    group.feishu_webhook_url ??
    group.feishu_mobile_webhook_url ??
    group.feishu_desktop_webhook_url;
  if (rawUrl) {
    return rawUrl;
  }
  const configured =
    group.feishu_webhook_configured ??
    group.feishu_mobile_webhook_configured ??
    group.feishu_desktop_webhook_configured;
  if (configured) {
    return (
      group.feishu_webhook_masked ??
      group.feishu_mobile_webhook_masked ??
      group.feishu_desktop_webhook_masked ??
      "已配置（地址已隐藏）"
    );
  }
  return "未配置";
}

export function AdvancedAgentConfigPanel(props: AdvancedAgentConfigPanelProps) {
  const [manualPricingDrafts, setManualPricingDrafts] = useState<Record<string, string>>({});
  const [profileDraft, setProfileDraft] = useState({ profileKey: "", displayName: "", enabled: false });
  const [credProfileKey, setCredProfileKey] = useState<string | null>(null);
  const [credDraft, setCredDraft] = useState({ appId: "", appSecret: "", displayName: "" });
  const [grantedBy, setGrantedBy] = useState("operator-console");
  const [createDraft, setCreateDraft] = useState({
    chatId: "",
    displayName: "",
    profileKey: "",
    ebkGroupKey: "",
    competitorGroupKey: "",
  });

  const ebkGroups = props.overview?.watch_groups.ebk ?? [];
  const competitorOverviewGroups = props.overview?.watch_groups.competitor ?? [];
  const ebkPricingSessions = useMemo(
    () => props.sessions.filter((session) => session.intended_use === "ebk"),
    [props.sessions],
  );
  const competitorPricingByKey = useMemo(
    () => new Map(props.competitorGroups.map((group) => [group.group_key, group])),
    [props.competitorGroups],
  );

  return (
    <section className="advanced-agent-config" aria-label="高级 Agent 配置">
      <div className="advanced-agent-config-header">
        <div>
          <h2>高级配置</h2>
          <p>集中查看 Agent、飞书群、酒店监控组和携程调价绑定。主页面的日常操作保持不变。</p>
        </div>
        <div className="supervisor-access-form">
          <label>
            Supervisor 访问密钥
            <input
              type="password"
              value={props.supervisorApiKey}
              onChange={(event) => props.onApiKeyChange(event.target.value)}
              placeholder="仅本次页面会话使用"
              autoComplete="off"
            />
          </label>
          <button type="button" onClick={props.onLoad} disabled={props.loading || !props.supervisorApiKey.trim()}>
            <RefreshCw size={15} />
            {props.loading ? "加载中" : "加载配置总览"}
          </button>
        </div>
      </div>

      {props.error ? <p className="advanced-agent-error">{props.error}</p> : null}
      {props.actionNotice ? (
        <p className={`notice ${props.actionNotice.tone} advanced-agent-action-notice`}>
          {props.actionNotice.text}
        </p>
      ) : null}
      {!props.overview ? (
        <p className="empty">请输入 Supervisor 访问密钥后加载配置总览。访问密钥不会写入浏览器存储。</p>
      ) : (
        <div className="advanced-agent-sections">
          <section className="panel advanced-agent-section">
            <div className="section-head">
              <div>
                <h3>飞书 AI 助手</h3>
                <p>新增助手后点击「配置凭据」即可在前端填写飞书 App ID/Secret 并部署；部署会自动初始化整套网关运行环境（MCP 工具、卡片插件、技能、SOUL、北京时间、大模型），无需改任何代码或环境变量文件。密钥不回显、不落日志，可随时更新或删除助手。</p>
              </div>
              <ShieldCheck size={18} />
            </div>
            <div className="advanced-agent-create-form">
              <div className="advanced-agent-create-grid">
                <label>
                  profile key（小写字母/数字/连字符）
                  <input
                    value={profileDraft.profileKey}
                    placeholder="例如：manxin-ai"
                    onChange={(event) => setProfileDraft({ ...profileDraft, profileKey: event.target.value })}
                  />
                </label>
                <label>
                  助手名称
                  <input
                    value={profileDraft.displayName}
                    placeholder="例如：曼芯酒店 AI 助手"
                    onChange={(event) => setProfileDraft({ ...profileDraft, displayName: event.target.value })}
                  />
                </label>
                <label className="compact-checkbox">
                  <input
                    type="checkbox"
                    checked={profileDraft.enabled}
                    onChange={(event) => setProfileDraft({ ...profileDraft, enabled: event.target.checked })}
                  />
                  创建即启用（需 admin 已为该 key 预置凭据）
                </label>
              </div>
              <button
                type="button"
                className="primary-button"
                disabled={
                  props.busy === "create-agent-profile" ||
                  !profileDraft.profileKey.trim() ||
                  !profileDraft.displayName.trim()
                }
                onClick={() => {
                  props.onCreateProfile({
                    profileKey: profileDraft.profileKey.trim(),
                    displayName: profileDraft.displayName.trim(),
                    enabled: profileDraft.enabled,
                  });
                  setProfileDraft({ profileKey: "", displayName: "", enabled: false });
                }}
              >
                <Plus size={14} />
                {props.busy === "create-agent-profile" ? "创建中" : "新增 AI 助手"}
              </button>
              <p className="advanced-agent-create-hint">
                创建后点击该 profile 行的「配置凭据」输入飞书 App ID/Secret 并部署
                （自动生成 MCP/插件/技能/SOUL/时区/模型整套配置，与第一个助手格式一致），
                再在此处勾选启用，最后到「飞书准入设置」完成准入名单部署。
              </p>
            </div>
            <div className="advanced-agent-list">
              {props.overview.profiles.map((profile) => {
                const allowedGroupCount = props.overview!.bindings.filter(
                  (binding) => binding.profile_key === profile.profile_key && binding.enabled,
                ).length;
                return (
                  <div className="advanced-agent-row" key={profile.profile_key}>
                    <div>
                      <strong>{profile.display_name}</strong>
                      <small>{profile.profile_key}</small>
                    </div>
                    <span className={profile.enabled ? "status-chip success" : "status-chip muted"}>
                      {profile.enabled ? "运行中" : "已停用"}
                    </span>
                    <span className="status-chip">{profile.credential_configured ? "凭据已配置" : "凭据未配置"}</span>
                    <span className="advanced-agent-meta">允许群 {allowedGroupCount} 个</span>
                    <label className="compact-checkbox">
                      <input
                        type="checkbox"
                        checked={profile.enabled}
                        onChange={(event) => props.onPatchProfile(profile, event.target.checked)}
                        disabled={props.busy === `profile-${profile.profile_key}`}
                      />
                      启用
                    </label>
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={props.busy === `profile-creds-${profile.profile_key}`}
                      onClick={() => {
                        if (credProfileKey === profile.profile_key) {
                          setCredProfileKey(null);
                        } else {
                          setCredProfileKey(profile.profile_key);
                          setCredDraft({ appId: "", appSecret: "", displayName: profile.display_name ?? "" });
                        }
                      }}
                    >
                      {profile.credential_configured ? "更新凭据" : "配置凭据"}
                    </button>
                    {credProfileKey === profile.profile_key ? (
                      <div className="advanced-agent-cred-form">
                        <input
                          value={credDraft.appId}
                          placeholder="飞书 App ID（cli_ 开头）"
                          onChange={(event) => setCredDraft({ ...credDraft, appId: event.target.value })}
                        />
                        <input
                          type="password"
                          value={credDraft.appSecret}
                          placeholder="飞书 App Secret"
                          onChange={(event) => setCredDraft({ ...credDraft, appSecret: event.target.value })}
                        />
                        <input
                          value={credDraft.displayName}
                          placeholder="助手名称（可选）"
                          onChange={(event) => setCredDraft({ ...credDraft, displayName: event.target.value })}
                        />
                        <button
                          type="button"
                          className="primary-button"
                          disabled={
                            props.busy === `profile-creds-${profile.profile_key}` ||
                            !credDraft.appId.trim() ||
                            !credDraft.appSecret.trim()
                          }
                          onClick={() => {
                            props.onSetProfileCredentials(profile, {
                              appId: credDraft.appId.trim(),
                              appSecret: credDraft.appSecret.trim(),
                              displayName: credDraft.displayName.trim() || null,
                            });
                            setCredProfileKey(null);
                            setCredDraft({ appId: "", appSecret: "", displayName: "" });
                          }}
                        >
                          {props.busy === `profile-creds-${profile.profile_key}` ? "部署中…" : "保存并部署"}
                        </button>
                      </div>
                    ) : null}
                    <button
                      type="button"
                      className="secondary-button danger"
                      disabled={props.busy === `profile-delete-${profile.profile_key}`}
                      title="删除 AI 助手"
                      onClick={() => {
                        if (
                          window.confirm(
                            `确定删除 AI 助手「${profile.display_name}」？仍被业务群绑定时无法删除。`,
                          )
                        ) {
                          props.onDeleteProfile(profile);
                        }
                      }}
                    >
                      <Trash2 size={14} />
                      删除
                    </button>
                  </div>
                );
              })}
              {!props.overview.profiles.length ? <p className="empty">尚未配置飞书 AI profile。</p> : null}
            </div>
          </section>

          <FeishuAdmissionPanel
            heading="飞书准入设置"
            apiKey={props.supervisorApiKey}
            profiles={props.overview.profiles}
            admissionRevision={props.admissionRevision}
          />

          <section className="panel advanced-agent-section">
            <div className="section-head">
              <div>
                <h3>运营问答上下文群</h3>
                <p>按单个 chat ID 查看 Agent、EBK 和竞品监控组之间的关系；新增店铺从这里接入。</p>
              </div>
            </div>
            <div className="advanced-agent-create-form">
              <div className="advanced-agent-create-grid">
                <label>
                  飞书群 chat ID
                  <input
                    value={createDraft.chatId}
                    placeholder="oc_ 开头的群 ID"
                    onChange={(event) => setCreateDraft({ ...createDraft, chatId: event.target.value })}
                  />
                </label>
                <label>
                  群备注名（店铺名）
                  <input
                    value={createDraft.displayName}
                    placeholder="例如：曼芯酒店运营群"
                    onChange={(event) => setCreateDraft({ ...createDraft, displayName: event.target.value })}
                  />
                </label>
                <label>
                  Agent profile
                  <select
                    value={createDraft.profileKey}
                    onChange={(event) => setCreateDraft({ ...createDraft, profileKey: event.target.value })}
                  >
                    <option value="">选择 profile</option>
                    {(props.overview?.profiles ?? []).map((profile) => (
                      <option value={profile.profile_key} key={profile.profile_key}>{profile.display_name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  自动绑定 EBK 监控组（可选）
                  <select
                    value={createDraft.ebkGroupKey}
                    onChange={(event) => setCreateDraft({ ...createDraft, ebkGroupKey: event.target.value })}
                  >
                    <option value="">稍后手动绑定</option>
                    {ebkGroups.map((group) => (
                      <option value={group.group_key} key={group.group_key}>{group.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  自动绑定竞品监控组（可选）
                  <select
                    value={createDraft.competitorGroupKey}
                    onChange={(event) => setCreateDraft({ ...createDraft, competitorGroupKey: event.target.value })}
                  >
                    <option value="">稍后手动绑定</option>
                    {competitorOverviewGroups.map((group) => (
                      <option value={group.group_key} key={group.group_key}>{group.name}</option>
                    ))}
                  </select>
                </label>
              </div>
              <button
                type="button"
                className="primary-button"
                disabled={
                  props.busy === "create-chat-binding" ||
                  !createDraft.chatId.trim() ||
                  !createDraft.profileKey
                }
                onClick={() => {
                  props.onCreateChatBinding({
                    chatId: createDraft.chatId.trim(),
                    displayName: createDraft.displayName.trim(),
                    profileKey: createDraft.profileKey,
                    ebkGroupKey: createDraft.ebkGroupKey,
                    competitorGroupKey: createDraft.competitorGroupKey,
                  });
                  setCreateDraft({
                    chatId: "",
                    displayName: "",
                    profileKey: createDraft.profileKey,
                    ebkGroupKey: "",
                    competitorGroupKey: "",
                  });
                }}
              >
                <Plus size={14} />
                {props.busy === "create-chat-binding" ? "创建中" : "新增群绑定"}
              </button>
              <p className="advanced-agent-create-hint">
                创建后系统会自动把该群加入所选 AI 助手的飞书准入名单并部署
                （助手需已启用并配置凭据），机器人即可在该群应答；
                EBK/竞品监控组选择后会在创建时自动绑定该群。
              </p>
            </div>
            <div className="advanced-agent-list">
              {props.overview.bindings.map((binding) => {
                // Which EBK group (if any) currently delivers to this chat?
                // Single chat <-> single EBK group: at most one match.
                const boundEbkGroupKey =
                  ebkGroups.find((group) => group.feishu_chat_id === binding.chat_id)?.group_key ?? "";
                const ebkBindingBusy = Boolean(
                  boundEbkGroupKey && props.busy === `ebk-binding-${boundEbkGroupKey}`,
                );
                const boundCompetitorGroupKey =
                  competitorOverviewGroups.find(
                    (group) => group.feishu_chat_id === binding.chat_id,
                  )?.group_key ?? "";
                return (
                <div className="advanced-agent-context-row" key={binding.id}>
                      <div>
                        <strong>{binding.chat_id_masked}</strong>
                        <small>{binding.display_name}</small>
                      </div>
                      <select
                        aria-label={`${binding.chat_id_masked} Agent profile`}
                        value={binding.profile_key}
                        onChange={(event) => props.onPatchBinding(binding, { profile_key: event.target.value })}
                        disabled={props.busy === `binding-${binding.id}`}
                      >
                        {(props.overview?.profiles ?? []).map((profile) => (
                          <option value={profile.profile_key} key={profile.profile_key}>{profile.display_name}</option>
                        ))}
                      </select>
                  <div className="advanced-agent-context-groups">
                    <span>EBK：{groupLabelsForChat(binding.chat_id, ebkGroups)}</span>
                    <span>竞品：{groupLabelsForChat(binding.chat_id, competitorOverviewGroups)}</span>
                    <label className="ebk-context-binding">
                      绑定 EBK 监控组
                      <select
                        aria-label={`${binding.chat_id_masked} EBK 监控组绑定`}
                        value={boundEbkGroupKey}
                        onChange={(event) => {
                          const nextKey = event.target.value;
                          if (boundEbkGroupKey && nextKey && boundEbkGroupKey !== nextKey) {
                            // Switching groups: clear the old group first so the
                            // chat stays bound to at most one EBK group.
                            props.onBindEbkGroupChat(boundEbkGroupKey, null);
                          }
                          props.onBindEbkGroupChat(
                            nextKey || boundEbkGroupKey,
                            nextKey ? binding.chat_id ?? null : null,
                          );
                        }}
                        disabled={ebkBindingBusy}
                      >
                        <option value="">不绑定</option>
                        {ebkGroups.map((group) => (
                          <option value={group.group_key} key={group.group_key}>{group.name}</option>
                        ))}
                      </select>
                    </label>
                    <label className="ebk-context-binding">
                      绑定竞品监控组
                      <select
                        aria-label={`${binding.chat_id_masked} 竞品监控组绑定`}
                        value={boundCompetitorGroupKey}
                        onChange={(event) => {
                          const nextKey = event.target.value;
                          if (
                            boundCompetitorGroupKey &&
                            nextKey &&
                            boundCompetitorGroupKey !== nextKey
                          ) {
                            // 与 EBK 绑定同规则：换组先清旧组，一群至多绑一个竞品组。
                            props.onBindCompetitorGroupChat(boundCompetitorGroupKey, null);
                          }
                          props.onBindCompetitorGroupChat(
                            nextKey || boundCompetitorGroupKey,
                            nextKey ? binding.chat_id ?? null : null,
                          );
                        }}
                        disabled={Boolean(
                          boundCompetitorGroupKey &&
                            props.busy === `competitor-binding-${boundCompetitorGroupKey}`,
                        )}
                      >
                        <option value="">不绑定</option>
                        {competitorOverviewGroups.map((group) => (
                          <option value={group.group_key} key={group.group_key}>{group.name}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <span className="advanced-agent-meta">
                    {groupLabelsForChat(binding.chat_id, [...ebkGroups, ...competitorOverviewGroups]) === "未绑定监控组"
                      ? "配置不完整"
                      : "配置已关联"}
                  </span>
                  <label className="compact-checkbox">
                    <input
                      type="checkbox"
                      checked={binding.enabled}
                      onChange={(event) => props.onPatchBinding(binding, { enabled: event.target.checked })}
                      disabled={props.busy === `binding-${binding.id}`}
                    />
                    启用
                  </label>
                  <button
                    type="button"
                    className="secondary-button danger"
                    disabled={props.busy === `binding-delete-${binding.id}`}
                    title="删除群绑定"
                    onClick={() => {
                      if (
                        window.confirm(
                          "确定删除该群的 Agent 绑定？删除后机器人不再在该群应答，且该群会移出准入名单。",
                        )
                      ) {
                        props.onDeleteBinding(binding);
                      }
                    }}
                  >
                    <Trash2 size={14} />
                    删除
                  </button>
                </div>
                );
              })}
              {!props.overview.bindings.length ? <p className="empty">尚未绑定运营问答上下文群。</p> : null}
            </div>
          </section>

          <section className="panel advanced-agent-section">
            <div className="section-head">
              <div>
                <h3>出站推送地址</h3>
                <p>推送地址只用于发送运营卡片，不参与 Agent 对话身份。</p>
              </div>
            </div>
            <div className="advanced-agent-webhook-grid">
              {[...ebkGroups.map((group) => ({ kind: "EBK", group })), ...competitorOverviewGroups.map((group) => ({ kind: "竞品", group }))].map(
                ({ kind, group }) => (
                  <div className="advanced-agent-webhook-row" key={`${kind}-${group.group_key}`}>
                    <div>
                      <strong>{group.name}</strong>
                      <small>{kind} · {group.group_key}</small>
                    </div>
                    <span className="advanced-agent-webhook-url" title={primaryWebhookAddress(group)}>
                      {primaryWebhookAddress(group)}
                    </span>
                    <span>{group.feishu_chat_id_configured ? `投递群 ${group.feishu_chat_id ?? group.feishu_chat_id_masked ?? "已绑定"}` : "未绑定投递群"}</span>
                  </div>
                ),
              )}
              {!ebkGroups.length && !competitorOverviewGroups.length ? <p className="empty">尚未配置推送监控组。</p> : null}
            </div>
          </section>

          <section className="panel advanced-agent-section">
            <div className="section-head">
              <div>
                <h3>携程调价</h3>
                <p>仅展示携程 EBK 的手动调价群和竞品组调价账号；实际写价仍需经过 Feishu 审批链路。</p>
              </div>
            </div>
            <div className="advanced-agent-pricing-grid">
              <div>
                <h4>手动调价群</h4>
                {ebkPricingSessions.map((session) => {
                  const draft = manualPricingDrafts[session.browser_session_id] ?? "";
                  return (
                    <div className="advanced-agent-pricing-row" key={session.browser_session_id}>
                      <div>
                        <strong>{session.display_name}</strong>
                        <small>{session.pricing_feishu_chat_id_configured ? `已绑定 ${session.pricing_feishu_chat_id_masked ?? ""}` : "未绑定"}</small>
                      </div>
                      <input
                        value={draft}
                        onChange={(event) => setManualPricingDrafts((current) => ({ ...current, [session.browser_session_id]: event.target.value }))}
                        placeholder={
                          session.pricing_feishu_chat_id_configured
                            ? `已绑定：${session.pricing_feishu_chat_id_masked ?? ""}，输入新群 ID 覆盖`
                            : "oc_ 开头的飞书群 ID"
                        }
                      />
                      <button
                        type="button"
                        onClick={() => {
                          props.onSaveManualPricingChat(session, draft.trim() || null);
                          setManualPricingDrafts((current) => {
                            const next = { ...current };
                            delete next[session.browser_session_id];
                            return next;
                          });
                        }}
                        disabled={props.busy === `pricing-chat-${session.browser_session_id}` || !draft.trim()}
                      >
                        <Save size={14} /> 保存
                      </button>
                      <button
                        type="button"
                        onClick={() => props.onSaveManualPricingChat(session, null)}
                        disabled={!session.pricing_feishu_chat_id_configured || props.busy === `pricing-chat-${session.browser_session_id}`}
                      >
                        清除
                      </button>
                    </div>
                  );
                })}
                {!ebkPricingSessions.length ? <p className="empty">尚未配置携程 EBK 账号。</p> : null}
              </div>
              <div>
                <h4>竞品组调价账号</h4>
                {competitorOverviewGroups.map((overviewGroup) => {
                  const group = competitorPricingByKey.get(overviewGroup.group_key);
                  const currentPricingSessionId = group?.pricing_browser_session_id ?? overviewGroup.pricing_browser_session_id ?? "";
                  return (
                    <div className="advanced-agent-pricing-row" key={overviewGroup.group_key}>
                      <div>
                        <strong>{overviewGroup.name}</strong>
                        <small>{overviewGroup.pricing_account_configured ? `已绑定 ${overviewGroup.pricing_connection_mode ?? "EBK"}` : "未绑定"}</small>
                      </div>
                      <select
                        value={currentPricingSessionId}
                        onChange={(event) => group && props.onSaveCompetitorPricingAccount(group, event.target.value || null)}
                        disabled={!group || props.busy === `pricing-account-${overviewGroup.group_key}`}
                      >
                        <option value="">不绑定</option>
                        {ebkPricingSessions.map((session) => (
                          <option value={session.browser_session_id} key={session.browser_session_id}>{session.display_name}</option>
                        ))}
                      </select>
                    </div>
                  );
                })}
                {!competitorOverviewGroups.length ? <p className="empty">尚未配置竞品监控组。</p> : null}
              </div>
            </div>
          </section>

          <section className="panel advanced-agent-section">
            <div className="section-head">
              <div>
                <h3>权限管理</h3>
                <p>Supervisor 可查看并分配 profile 角色；未显式分配的 profile 按运营员处理。</p>
              </div>
            </div>
            <label className="advanced-agent-granted-by">
              操作人标识
              <input value={grantedBy} onChange={(event) => setGrantedBy(event.target.value)} />
            </label>
            <div className="advanced-agent-list">
              {props.overview.profiles.map((profile) => {
                const assignment = props.overview!.role_assignments.find(
                  (item) => item.subject_type === "profile" && item.subject_ref === profile.profile_key,
                );
                const role = assignment?.role ?? "operator";
                return (
                  <div className="advanced-agent-row" key={`role-${profile.profile_key}`}>
                    <div>
                      <strong>{profile.display_name}</strong>
                      <small>{assignment ? `最近由 ${assignment.granted_by} 分配` : "未显式分配"}</small>
                    </div>
                    <select
                      value={role}
                      onChange={(event) => props.onSaveRole({
                        profileKey: profile.profile_key,
                        assignment,
                        role: event.target.value as FeishuRole,
                        grantedBy: grantedBy.trim() || "operator-console",
                      })}
                      disabled={props.busy === `role-${profile.profile_key}`}
                    >
                      {(Object.keys(roleLabels) as FeishuRole[]).map((option) => (
                        <option value={option} key={option}>{roleLabels[option]}</option>
                      ))}
                    </select>
                  </div>
                );
              })}
              {props.overview.role_assignments.filter((assignment) => assignment.subject_type === "api_key").map((assignment) => (
                <div className="advanced-agent-row" key={`api-key-${assignment.id}`}>
                  <div>
                    <strong>API key 权限</strong>
                    <small>{assignment.subject_ref.slice(0, 12)}…</small>
                  </div>
                  <span className="status-chip">{roleLabels[assignment.role]}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
