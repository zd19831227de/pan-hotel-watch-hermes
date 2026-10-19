import { Loader2, RefreshCw, Save, X } from "lucide-react";
import { useEffect, useState } from "react";

import { type CtripBusinessData } from "../ctripBusinessDataModel";
import {
  type DataCenterRun,
  type DataCenterWatchGroup,
} from "../../services/ctripEbookingWatchApi";
import { EmptyState, formatTime, Metric, statusLabel } from "../ui/opsPrimitives";

export function DailyBriefPanel(props: {
  businessData: CtripBusinessData;
  latest: DataCenterRun | null;
  groups: DataCenterWatchGroup[];
  group: DataCenterWatchGroup | null;
  busy: string | null;
  variant?: "full" | "configOnly";
  onSelectGroup: (groupKey: string) => void;
  onPatchGroup: (input: Partial<DataCenterWatchGroup> & { feishu_webhook_url?: string }) => void;
  onRun: () => void;
}) {
  const configOnly = props.variant === "configOnly";
  const [webhookDraft, setWebhookDraft] = useState("");
  const [draft, setDraft] = useState({
    title: props.group?.daily_brief_title ?? "",
    pushTime: props.group?.daily_brief_push_time ?? "08:30",
    enabled: Boolean(props.group?.daily_brief_enabled),
  });
  useEffect(() => {
    setDraft({
      title: props.group?.daily_brief_title ?? "",
      pushTime: props.group?.daily_brief_push_time ?? "08:30",
      enabled: Boolean(props.group?.daily_brief_enabled),
    });
    setWebhookDraft("");
  }, [
    props.group?.group_key,
    props.group?.daily_brief_title,
    props.group?.daily_brief_push_time,
    props.group?.daily_brief_enabled,
  ]);
  const selectedGroupKey = props.group?.group_key ?? "";
  const configBlock = (
    <>
      <div className="split-grid">
        <div className="panel">
          <h3>推送配置</h3>
          <div className="form-grid">
            <label>
              绑定监控组
              <select
                value={selectedGroupKey}
                onChange={(event) => props.onSelectGroup(event.target.value)}
                disabled={!props.groups.length || props.busy === "select-daily-brief-group"}
              >
                {props.groups.map((group) => (
                  <option key={group.group_key} value={group.group_key}>{group.name}</option>
                ))}
                {!props.groups.length ? <option value="">未配置</option> : null}
              </select>
            </label>
            <label>
              推送时间
              <input
                type="time"
                value={draft.pushTime}
                onChange={(event) => setDraft({ ...draft, pushTime: event.target.value })}
              />
            </label>
            <label className="form-field-wide">
              简报标题
              <input
                value={draft.title}
                placeholder={props.group?.brief_title || "EBK 数据简报"}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
              />
            </label>
            <label className="form-field-wide">
              飞书机器人地址
              <div className="field-action-row">
                <input
                  value={webhookDraft}
                  placeholder={props.group?.feishu_webhook_configured ? `已配置：${props.group.feishu_webhook_masked ?? ""}` : "粘贴飞书机器人地址"}
                  onChange={(event) => setWebhookDraft(event.target.value)}
                />
                <button
                  className="secondary-button"
                  disabled={!props.group || !webhookDraft.trim() || props.busy === "patch-daily-brief"}
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
                  disabled={!props.group?.feishu_webhook_configured || props.busy === "patch-daily-brief"}
                  onClick={() => {
                    props.onPatchGroup({ feishu_webhook_url: "" });
                    setWebhookDraft("");
                  }}
                  type="button"
                >
                  <X size={16} /> 清除
                </button>
              </div>
            </label>
          </div>
          <div className="toggle-row">
            <label>
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
              />
              启用每日定时推送
            </label>
          </div>
          <button
            className="primary-button"
            disabled={!props.group || props.busy === "patch-daily-brief"}
            onClick={() =>
              props.onPatchGroup({
                daily_brief_title: draft.title.trim(),
                daily_brief_push_time: draft.pushTime,
                daily_brief_enabled: draft.enabled,
              })
            }
            type="button"
          >
            <Save size={16} /> 保存简报设置
          </button>
        </div>
        <div className="panel">
          <h3>定时状态</h3>
          <div className="latest-grid">
            <Metric label="当前状态" value={props.group?.daily_brief_enabled ? "已启用" : "未启用"} />
            <Metric label="推送时间" value={props.group?.daily_brief_push_time ?? "08:30"} />
            <Metric label="上次推送" value={props.group?.daily_brief_last_pushed_at ? formatTime(props.group.daily_brief_last_pushed_at) : "暂无"} />
            <Metric label="机器人地址" value={props.group?.feishu_webhook_configured ? "已配置" : "未配置"} />
          </div>
        </div>
      </div>
    </>
  );

  if (configOnly) {
    return <div className="ebk-config-block">{configBlock}</div>;
  }

  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <h2>EBK 经营数据</h2>
          <p>最新一次读取的携程经营数据（收益概览 / 经营对比 / 流量曝光），与「EBK 全局定时推送」经营卡同源同口径。</p>
        </div>
        <div className="action-strip">
          <button
            className="secondary-button"
            type="button"
            onClick={props.onRun}
            disabled={!props.group || props.busy === "run-daily-brief"}
          >
            {props.busy === "run-daily-brief" ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
            立即刷新简报
          </button>
        </div>
      </div>
      {configBlock}
      <CtripBusinessDataPanel data={props.businessData} />
      <div className="panel">
        <h3>简报来源</h3>
        <div className="latest-grid">
          <Metric label="来源监控组" value={props.group?.name ?? "未配置"} />
          <Metric label="最近读取" value={props.latest?.captured_at ? formatTime(props.latest.captured_at) : "暂无"} />
          <Metric label="读取结果" value={props.latest ? statusLabel(props.latest.status) : "暂无"} />
          <Metric label="推送结果" value={props.latest?.push_result?.summary ?? "尚未推送"} />
        </div>
      </div>
    </section>
  );
}

export function CtripBusinessDataPanel({ data }: { data: CtripBusinessData }) {
  return (
    <div className="panel data-brief-panel">
      <div className="panel-title-row">
        <div>
          <h3>经营数据</h3>
          <p>{data.sourceLabel || "最近一次 EBK 经营数据读取"}</p>
        </div>
        <span>{data.readTime ? formatTime(data.readTime) : "待读取"}</span>
      </div>
      {data.anomalyCount > 0 ? (
        <p className="field-hint" role="status">
          本次读取 {data.anomalyCount} 个数据集异常：
          {data.anomalies.map((item) => `${item.datasetId}（${item.status}）`).join("、")}
        </p>
      ) : null}
      {data.status === "ready" ? (
        <>
          <h4>收益概览</h4>
          <div className="brief-metric-grid">
            {data.revenueOverview.map((metric) => (
              <div className="brief-metric" key={metric.key}>
                <div className="brief-metric-title">
                  <span>{metric.label}</span>
                </div>
                <strong>{metric.value}</strong>
              </div>
            ))}
          </div>
          <h4>经营对比（竞争圈实时）</h4>
          <div className="brief-metric-grid">
            {data.businessComparison.map((metric) => (
              <div className="brief-metric" key={metric.key}>
                <div className="brief-metric-title">
                  <span>{metric.label}</span>
                  {metric.rank ? <small>竞争圈排名 {metric.rank}</small> : null}
                </div>
                <strong>{metric.value}</strong>
              </div>
            ))}
          </div>
          <h4>流量曝光</h4>
          <div className="brief-metric-grid">
            {data.trafficExposure.map((metric) => (
              <div className="brief-metric" key={metric.key}>
                <div className="brief-metric-title">
                  <span>{metric.label}</span>
                  {metric.rank ? <small>排名 {metric.rank}</small> : null}
                </div>
                <strong>{metric.value}</strong>
              </div>
            ))}
          </div>
        </>
      ) : (
        <EmptyState text={data.emptyText ?? "暂无可展示的经营数据。"} />
      )}
    </div>
  );
}
