import { Loader2, Play, RefreshCw, X } from "lucide-react";

import {
  canStartPolling,
  ebkPushSourceText,
  canStopDailyBrief,
  canStopPolling,
  dailyBriefStatusText,
  listActiveEbkScheduleTasks,
  manualEbkRunButtonText,
  otherActiveEbkScheduleSummary,
  pollingStatusText,
  startPollingButtonText,
  stopAllEbkSchedulesButtonText,
  stopDailyBriefButtonText,
  stopPollingButtonText,
} from "../pollingStatus";
import { type DataCenterRun, type DataCenterWatchGroup } from "../../services/ctripEbookingWatchApi";
import { formatTime, Metric, statusLabel } from "../ui/opsPrimitives";

export function EbkPlatformStatusBar(props: {
  platformLabel: string;
  groups: DataCenterWatchGroup[];
  group: DataCenterWatchGroup | null;
  selectedKey: string;
  latest: DataCenterRun | null;
  busy: string | null;
  sessionUnregistered: boolean;
  globalPushEnabled?: boolean;
  onSelectGroup: (groupKey: string) => void;
  onRefreshBrief: () => void;
  onRunDataCenter: () => void;
  onSetPolling: (enabled: boolean) => void;
  onSetDailyBrief: (enabled: boolean) => void;
  onStopAllSchedules: () => void;
}) {
  const readStatus = props.latest ? statusLabel(props.latest.status) : "暂未读取";
  const readTime = props.latest?.captured_at ? formatTime(props.latest.captured_at) : "暂无";
  const polling = pollingStatusText(props.group);
  const pushSource = ebkPushSourceText(props.group, { globalPushEnabled: props.globalPushEnabled });
  const dailyBrief = dailyBriefStatusText(props.group);
  const otherSchedules = otherActiveEbkScheduleSummary(props.groups, props.selectedKey);
  const hasAnySchedule = listActiveEbkScheduleTasks(props.groups).length > 0;
  const nextAction = props.sessionUnregistered
    ? "携程EBK会话未注册，请先在浏览器管理中登录携程EBK账号"
    : props.latest?.status === "human_required"
      ? "需人工：在浏览器完成登录或验证后再读取"
      : !props.group
        ? "先接入携程平台并绑定浏览器账号"
        : "可刷新简报或读取并推送最新数据";

  return (
    <div className="panel ebk-platform-status-bar">
      <div className="panel-title-row">
        <div>
          <h3>{props.platformLabel} · 当前状态</h3>
          <p>优先确认登录与读取结果，再决定下一步安全操作。</p>
        </div>
      </div>
      <div className="metric-grid">
        <Metric label="店铺配置" value={props.group?.name ?? "未接入"} />
        <Metric label="读取状态" value={readStatus} />
        <Metric label="最近读取" value={readTime} />
        <Metric label="数据中心轮询" value={polling} />
        <Metric label="推送方式" value={pushSource} />
        <Metric label="每日简报" value={dailyBrief} />
      </div>
      {props.groups.length > 1 ? (
        <label className="ebk-group-switcher">
          切换监控组
          <select
            value={props.selectedKey}
            onChange={(event) => props.onSelectGroup(event.target.value)}
            disabled={!props.groups.length || props.busy === "select-data-center-group"}
          >
            {props.groups.map((item) => (
              <option key={item.group_key} value={item.group_key}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {otherSchedules ? <p className="field-hint">{otherSchedules}</p> : null}
      <div className="ebk-next-action">
        <strong>下一步</strong>
        <span>{nextAction}</span>
      </div>
      <div className="action-strip">
        <button
          className="secondary-button"
          type="button"
          onClick={props.onRefreshBrief}
          disabled={!props.group || props.busy === "run-daily-brief" || props.sessionUnregistered}
        >
          {props.busy === "run-daily-brief" ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
          立即刷新简报
        </button>
        <button
          className="primary-button"
          type="button"
          onClick={props.onRunDataCenter}
          disabled={!props.group || props.busy === "run-data-center" || props.sessionUnregistered}
        >
          <Play size={16} /> {manualEbkRunButtonText()}
        </button>
        <button
          className="secondary-button"
          type="button"
          disabled={!canStartPolling(props.group) || props.busy === "patch-data-center"}
          onClick={() => props.onSetPolling(true)}
        >
          <Play size={16} /> {startPollingButtonText()}
        </button>
        <button
          className="secondary-button"
          type="button"
          disabled={!canStopPolling(props.group) || props.busy === "patch-data-center"}
          onClick={() => props.onSetPolling(false)}
        >
          <X size={16} /> {stopPollingButtonText()}
        </button>
        <button
          className="secondary-button"
          type="button"
          disabled={!canStopDailyBrief(props.group) || props.busy === "patch-daily-brief"}
          onClick={() => props.onSetDailyBrief(false)}
        >
          <X size={16} /> {stopDailyBriefButtonText()}
        </button>
        <button
          className="secondary-button"
          type="button"
          disabled={!hasAnySchedule || props.busy === "patch-data-center-stop-all"}
          onClick={props.onStopAllSchedules}
        >
          <X size={16} /> {stopAllEbkSchedulesButtonText()}
        </button>
      </div>
      {props.sessionUnregistered ? (
        <p className="field-hint field-hint-warning">
          携程EBK会话未注册，请先在「浏览器管理」中登录携程EBK账号后再读取。
        </p>
      ) : null}
    </div>
  );
}
