import { Play, Plus, Save, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { canBindToCtripDataCenter } from "../accountPlatformModel.ts";
import {
  canStartPolling,
  canStopPolling,
  manualEbkRunButtonText,
  startPollingButtonText,
  stopPollingButtonText,
} from "../pollingStatus";
import {
  type BrowserSession,
} from "../../services/browserSessionsApi";
import {
  type DataCenterDataset,
  type DataCenterRun,
  type DataCenterWatchGroup,
} from "../../services/ctripEbookingWatchApi";
import { listShops, type Shop } from "../../services/shopsApi";
import { EmptyState, formatTime, Metric, operatorReason, statusLabel } from "../ui/opsPrimitives";
import { boundedNumber } from "../priceChangeAlertModel";

export function DataCenterPanel(props: {
  groups: DataCenterWatchGroup[];
  group: DataCenterWatchGroup;
  datasets: DataCenterDataset[];
  latest: DataCenterRun | null;
  sessions: BrowserSession[];
  busy: string | null;
  selectedKey: string;
  variant?: "full" | "configOnly";
  onSelectGroup: (groupKey: string) => void;
  onCreateGroup: () => void;
  onDeleteGroup: () => void;
  onSetPolling: (enabled: boolean) => void;
  onPatchGroup: (input: Partial<DataCenterWatchGroup> & { feishu_chat_id?: string | null; feishu_webhook_url?: string }) => void;
  onPatchDataset: (dataset: DataCenterDataset, input: Partial<DataCenterDataset>) => void;
  onRun: () => void;
}) {
  const configOnly = props.variant === "configOnly";
  const ebkSessions = props.sessions.filter((session) => canBindToCtripDataCenter(session));
  const [groupDraft, setGroupDraft] = useState({
    name: props.group.name,
    brief_title: props.group.brief_title ?? "",
  });
  const [chatIdDraft, setChatIdDraft] = useState("");
  const [webhookDraft, setWebhookDraft] = useState("");
  // v2：监控组归属店铺下拉的数据源；组件内聚加载（只读店铺列表）。
  const [shops, setShops] = useState<Shop[]>([]);
  useEffect(() => {
    let cancelled = false;
    void listShops()
      .then((payload) => {
        if (!cancelled) {
          setShops(payload);
        }
      })
      .catch(() => {
        // 店铺列表加载失败时保留空列表（下拉仅显示「未归属」）。
      });
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    setGroupDraft({
      name: props.group.name,
      brief_title: props.group.brief_title ?? "",
    });
    setChatIdDraft("");
    setWebhookDraft("");
  }, [props.group.group_key, props.group.name, props.group.brief_title]);
  const selectedSessionIsAvailable = ebkSessions.some(
    (session) => session.browser_session_id === props.group.browser_session_id,
  );
  const browserSelectValue = selectedSessionIsAvailable ? props.group.browser_session_id ?? "" : "";
  const latestPush = props.latest?.push_result ?? null;
  const body = (
    <>
      <div className="split-grid">
        <div className="panel">
          <div className="panel-title-row">
            <h3>监控组</h3>
            <div className="inline-actions">
              <button type="button" onClick={props.onCreateGroup} title="新建监控组"><Plus size={16} /></button>
              <button
                type="button"
                onClick={props.onDeleteGroup}
                title="删除当前监控组"
                disabled={props.busy === "delete-data-center-group"}
              >
                <Trash2 size={16} />
              </button>
            </div>
          </div>
          <label>
            当前组
            <select
              value={props.selectedKey}
              onChange={(event) => props.onSelectGroup(event.target.value)}
              disabled={!props.groups.length || props.busy === "select-data-center-group"}
            >
              {props.groups.map((item) => (
                <option key={item.group_key} value={item.group_key}>{item.name}</option>
              ))}
            </select>
          </label>
          <div className="form-grid">
            <label>
              名称
              <input
                value={groupDraft.name}
                onChange={(event) => setGroupDraft({ ...groupDraft, name: event.target.value })}
              />
            </label>
            <label>
              归属店铺
              <select
                value={props.group.shop_key ?? ""}
                onChange={(event) => props.onPatchGroup({ shop_key: event.target.value || null })}
              >
                <option value="">未归属（全局监控组）</option>
                {shops.map((shop) => (
                  <option key={shop.shop_key} value={shop.shop_key}>
                    {shop.short_name?.trim() || shop.name}
                  </option>
                ))}
              </select>
              <span className="field-hint">每店至多一个携程监控组；美团/飞猪监控组各自独立归属店铺，不依赖本组。</span>
            </label>
            <label>
              推送卡片标题
              <input
                value={groupDraft.brief_title}
                placeholder={groupDraft.name || props.group.name}
                onChange={(event) => setGroupDraft({ ...groupDraft, brief_title: event.target.value })}
              />
            </label>
            <label>
              浏览器账号
              <select value={browserSelectValue} onChange={(event) => {
                const session = ebkSessions.find((item) => item.browser_session_id === event.target.value);
                if (!session) {
                  return;
                }
                props.onPatchGroup({
                  browser_session_id: event.target.value,
                  platform_account_id: session.platform_account_id,
                });
              }} disabled={!ebkSessions.length}>
                <option value="" disabled hidden>请选择 EBK 后台账号</option>
                {ebkSessions.map((session) => (
                  <option key={session.browser_session_id} value={session.browser_session_id}>{session.display_name}</option>
                ))}
              </select>
              {!ebkSessions.length ? <span className="field-hint">请先在账号与浏览器里新增 EBK 后台账号。</span> : null}
            </label>
            <label className="form-field-wide">
              EBK 数据中心监控组的运营问答上下文群
              <div className="field-action-row">
                <input
                  value={chatIdDraft}
                  placeholder={props.group.feishu_chat_id_configured ? `已绑定：${props.group.feishu_chat_id_masked ?? ""}` : "oc_ 开头的飞书群 ID"}
                  onChange={(event) => setChatIdDraft(event.target.value)}
                />
                <button
                  className="secondary-button"
                  disabled={!chatIdDraft.trim() || props.busy === "patch-data-center"}
                  onClick={() => {
                    props.onPatchGroup({ feishu_chat_id: chatIdDraft.trim() });
                    setChatIdDraft("");
                  }}
                  type="button"
                >
                  <Save size={16} /> 保存上下文群
                </button>
                <button
                  className="secondary-button"
                  disabled={!props.group.feishu_chat_id_configured || props.busy === "patch-data-center"}
                  onClick={() => {
                    props.onPatchGroup({ feishu_chat_id: "" });
                    setChatIdDraft("");
                  }}
                  type="button"
                >
                  <X size={16} /> 清除上下文群
                </button>
              </div>
              <span className="field-hint">今日简报、跑一次 EBK 和经营问答会用它识别本 EBK 监控组；同一个飞书群 ID 也要填到对应竞品监控组。它不是手动调价指令群。</span>
            </label>
            <label className="form-field-wide">
              飞书机器人地址
              <div className="field-action-row">
                <input
                  value={webhookDraft}
                  placeholder={props.group.feishu_webhook_configured ? `已配置：${props.group.feishu_webhook_masked ?? ""}` : "粘贴飞书机器人地址"}
                  onChange={(event) => setWebhookDraft(event.target.value)}
                />
                <button
                  className="secondary-button"
                  disabled={!webhookDraft.trim() || props.busy === "patch-data-center"}
                  onClick={() => {
                    props.onPatchGroup({ feishu_webhook_url: webhookDraft.trim() });
                    setWebhookDraft("");
                  }}
                  type="button"
                >
                  <Save size={16} /> 保存
                </button>
                <button
                  className="secondary-button"
                  disabled={!props.group.feishu_webhook_configured || props.busy === "patch-data-center"}
                  onClick={() => {
                    props.onPatchGroup({ feishu_webhook_url: "" });
                    setWebhookDraft("");
                  }}
                  type="button"
                >
                  <X size={16} /> 清除
                </button>
              </div>
              <span className="field-hint">只显示脱敏地址；粘贴新地址后点击保存即可覆盖。</span>
            </label>
            <label>
              间隔分钟
              <input
                defaultValue={props.group.query_interval_minutes}
                inputMode="numeric"
                onBlur={(event) => props.onPatchGroup({
                  query_interval_minutes: boundedNumber(
                    event.target.value,
                    1,
                    10080,
                    props.group.query_interval_minutes,
                  ),
                })}
              />
            </label>
            <label>
              保留天数
              <input
                defaultValue={props.group.retention_days}
                inputMode="numeric"
                onBlur={(event) => props.onPatchGroup({
                  retention_days: boundedNumber(
                    event.target.value,
                    1,
                    3650,
                    props.group.retention_days,
                  ),
                })}
              />
            </label>
          </div>
          <div className="toggle-row">
            <label><input type="checkbox" checked={props.group.enabled} onChange={(event) => props.onPatchGroup({ enabled: event.target.checked })} /> 启用监控组</label>
          </div>
          <button
            className="primary-button"
            disabled={props.busy === "patch-data-center"}
            onClick={() => props.onPatchGroup({
              name: groupDraft.name.trim(),
              brief_title: groupDraft.brief_title.trim(),
            })}
            type="button"
          >
            <Save size={16} /> 保存当前设置
          </button>
        </div>
        <div className="panel">
          <h3>最近一次读取</h3>
          {props.latest ? (
            <div className="metric-grid">
              <Metric label="状态" value={statusLabel(props.latest.status)} />
              <Metric label="数据集" value={String(props.latest.dataset_count)} />
              <Metric label="成功" value={String(props.latest.success_count)} />
              <Metric label="需人工" value={String(props.latest.human_required_count)} />
              <Metric label="推送" value={latestPush ? statusLabel(String(latestPush.status ?? "")) : (props.group.feishu_webhook_configured ? "待发送" : "未配置")} />
              <Metric label="时间" value={formatTime(props.latest.captured_at)} />
            </div>
          ) : (
            <EmptyState text="还没有读取记录。" />
          )}
        </div>
      </div>
      <div className="panel">
        <h3>数据集</h3>
        <div className="dataset-grid">
          {props.datasets.map((dataset) => (
            <div className="dataset-row" key={dataset.dataset_id}>
              <div>
                <strong>{dataset.dataset_label}</strong>
                <span>{statusLabel(dataset.last_status ?? "未读取")}{dataset.last_failure_reason ? ` · ${operatorReason(dataset.last_failure_reason, dataset.last_status ?? undefined)}` : ""}</span>
              </div>
              <label><input type="checkbox" checked={dataset.enabled} onChange={(event) => props.onPatchDataset(dataset, { enabled: event.target.checked })} /> 读取</label>
              <label><input type="checkbox" checked={dataset.push_enabled} onChange={(event) => props.onPatchDataset(dataset, { push_enabled: event.target.checked })} /> 推送</label>
            </div>
          ))}
        </div>
      </div>
    </>
  );

  if (configOnly) {
    return <div className="ebk-config-block">{body}</div>;
  }

  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <h2>EBK 数据中心</h2>
          <p>读取携程 EBK 数据中心可见数据，按数据集保存结果。遇到登录或验证返回需要人工处理。自动轮询已由「EBK 操作台 · 全局定时推送」接管：迁移门店请保持自动轮询关闭，未迁移门店仍可使用。</p>
        </div>
        <div className="action-strip">
          <button className="secondary-button" type="button" disabled={!canStartPolling(props.group) || props.busy === "patch-data-center"} onClick={() => props.onSetPolling(true)}>
            <Play size={16} /> {startPollingButtonText()}
          </button>
          <button className="secondary-button" type="button" disabled={!canStopPolling(props.group) || props.busy === "patch-data-center"} onClick={() => props.onSetPolling(false)}>
            <X size={16} /> {stopPollingButtonText()}
          </button>
          <button className="primary-button" type="button" disabled={props.busy === "run-data-center"} onClick={props.onRun}>
            <Play size={16} /> {manualEbkRunButtonText()}
          </button>
        </div>
      </div>
      {body}
    </section>
  );
}

