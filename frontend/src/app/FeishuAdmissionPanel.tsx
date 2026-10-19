import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  addFeishuAdmissionUser,
  getActiveOpenIds,
  getFeishuProfileAdmission,
  patchFeishuProfileAdmission,
  removeFeishuAdmissionUser,
  type ActiveOpenId,
  type FeishuAdmissionGroup,
  type FeishuAdmissionPolicy,
  type FeishuAgentProfile,
  type FeishuProfileAdmission,
} from "../services/configApi";

type FeishuAdmissionPanelProps = {
  heading: string;
  apiKey: string;
  profiles: FeishuAgentProfile[];
  // 每次高级配置总览刷新 +1：新增群绑定/凭据部署会改写 Hermes 准入 YAML，
  // 面板据此自动重读，无需刷新整页。
  admissionRevision: number;
};

const policyLabels: Record<FeishuAdmissionPolicy, string> = {
  open: "所有成员",
  allowlist: "仅白名单",
  disabled: "暂停接收",
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "飞书准入设置操作失败。";
}

export function FeishuAdmissionPanel({
  heading,
  apiKey,
  profiles,
  admissionRevision,
}: FeishuAdmissionPanelProps) {
  const profileKeys = useMemo(
    () => profiles.map((profile) => profile.profile_key),
    [profiles],
  );
  const [selectedProfile, setSelectedProfile] = useState(profileKeys[0] ?? "");
  const [admission, setAdmission] = useState<FeishuProfileAdmission | null>(null);
  const [activeOpenIds, setActiveOpenIds] = useState<ActiveOpenId[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!profileKeys.includes(selectedProfile)) {
      setSelectedProfile(profileKeys[0] ?? "");
    }
  }, [profileKeys, selectedProfile]);

  const loadAdmission = useCallback(async () => {
    if (!apiKey.trim() || !selectedProfile) {
      setAdmission(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [admissionData, activeData] = await Promise.all([
        getFeishuProfileAdmission(apiKey, selectedProfile),
        getActiveOpenIds(apiKey, selectedProfile).catch(() => [] as ActiveOpenId[]),
      ]);
      setAdmission(admissionData);
      setActiveOpenIds(activeData);
    } catch (loadError) {
      setAdmission(null);
      setError(errorText(loadError));
    } finally {
      setLoading(false);
    }
    // admissionRevision 不直接参与请求，仅作为外部变更信号触发重读。
  }, [apiKey, selectedProfile]);

  useEffect(() => {
    void loadAdmission();
  }, [loadAdmission, admissionRevision]);

  async function updateGroup(
    group: FeishuAdmissionGroup,
    input: { policy?: FeishuAdmissionPolicy; require_mention?: boolean },
  ) {
    setBusy(`group-${group.group_key}`);
    setError(null);
    try {
      setAdmission(
        await patchFeishuProfileAdmission(apiKey, selectedProfile, {
          group_key: group.group_key,
          ...input,
        }),
      );
    } catch (updateError) {
      setError(errorText(updateError));
    } finally {
      setBusy(null);
    }
  }

  async function addUser(group: FeishuAdmissionGroup) {
    const openId = (drafts[group.group_key] ?? "").trim();
    if (!/^ou_[A-Za-z0-9_-]+$/.test(openId)) {
      setError("请输入有效的飞书 open_id。");
      return;
    }
    setBusy(`user-${group.group_key}`);
    setError(null);
    try {
      setAdmission(
        await addFeishuAdmissionUser(
          apiKey,
          selectedProfile,
          group.group_key,
          openId,
        ),
      );
      setDrafts((current) => ({ ...current, [group.group_key]: "" }));
    } catch (addError) {
      setError(errorText(addError));
    } finally {
      setBusy(null);
    }
  }

  async function removeUser(group: FeishuAdmissionGroup, openId: string) {
    setBusy(`user-${group.group_key}-${openId}`);
    setError(null);
    try {
      setAdmission(
        await removeFeishuAdmissionUser(
          apiKey,
          selectedProfile,
          group.group_key,
          openId,
        ),
      );
    } catch (removeError) {
      setError(errorText(removeError));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="advanced-agent-section feishu-admission-section" aria-label={heading}>
      <div className="section-head">
        <div>
          <h3>{heading}</h3>
        </div>
        <div className="feishu-admission-toolbar">
          <label>
            Agent
            <select
              value={selectedProfile}
              onChange={(event) => setSelectedProfile(event.target.value)}
            >
              {profiles.map((profile) => (
                <option value={profile.profile_key} key={profile.profile_key}>
                  {profile.display_name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="icon-button"
            onClick={() => void loadAdmission()}
            disabled={loading || !selectedProfile}
            title="刷新准入规则"
            aria-label="刷新准入规则"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {error ? <p className="advanced-agent-error">{error}</p> : null}
      {loading ? <p className="empty">正在读取准入设置…</p> : null}
      {!loading && admission ? (
        <div className="feishu-admission-groups">
          {admission.groups.map((group) => {
            const groupBusy = busy?.includes(group.group_key) ?? false;
            const draft = drafts[group.group_key] ?? "";
            return (
              <div className="feishu-admission-group" key={group.group_key}>
                <div className="feishu-admission-group-head">
                  <strong>{group.chat_id_masked}</strong>
                  <label>
                    准入策略
                    <select
                      value={group.policy}
                      onChange={(event) =>
                        void updateGroup(group, {
                          policy: event.target.value as FeishuAdmissionPolicy,
                        })
                      }
                      disabled={groupBusy}
                    >
                      {(Object.keys(policyLabels) as FeishuAdmissionPolicy[]).map(
                        (policy) => (
                          <option value={policy} key={policy}>
                            {policyLabels[policy]}
                          </option>
                        ),
                      )}
                    </select>
                  </label>
                  <label className="compact-checkbox">
                    <input
                      type="checkbox"
                      checked={group.require_mention}
                      onChange={(event) =>
                        void updateGroup(group, {
                          require_mention: event.target.checked,
                        })
                      }
                      disabled={groupBusy}
                    />
                    需要 @机器人
                  </label>
                </div>

                <div className="feishu-admission-users">
                  {group.allowed_users.map((openId) => (
                    <div className="feishu-admission-user" key={openId}>
                      <code>{openId}</code>
                      <button
                        type="button"
                        className="icon-button danger"
                        onClick={() => void removeUser(group, openId)}
                        disabled={groupBusy}
                        title="移除 open_id"
                        aria-label={`移除 ${openId}`}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  ))}
                  {!group.allowed_users.length ? (
                    <p className="empty">白名单为空</p>
                  ) : null}
                </div>

                <div className="feishu-admission-add">
                  {activeOpenIds.length ? (
                    <label>
                      最近活跃
                      <select
                        value=""
                        onChange={(event) => {
                          const openId = event.target.value;
                          if (openId) {
                            setDrafts((current) => ({
                              ...current,
                              [group.group_key]: openId,
                            }));
                          }
                        }}
                        disabled={groupBusy}
                      >
                        <option value="">从最近活跃用户选择…</option>
                        {activeOpenIds.map((row) => (
                          <option value={row.open_id} key={row.open_id}>
                            {row.open_id}（{row.chat_id_masked}）
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  <label>
                    open_id
                    <input
                      value={draft}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [group.group_key]: event.target.value,
                        }))
                      }
                      placeholder="ou_..."
                      autoComplete="off"
                    />
                  </label>
                  <button
                    type="button"
                    className="icon-button primary"
                    onClick={() => void addUser(group)}
                    disabled={groupBusy || !draft.trim()}
                    title="添加 open_id"
                    aria-label="添加 open_id"
                  >
                    <Plus size={16} />
                  </button>
                </div>
              </div>
            );
          })}
          {!admission.groups.length ? <p className="empty">未配置准入群</p> : null}
        </div>
      ) : null}
    </section>
  );
}
