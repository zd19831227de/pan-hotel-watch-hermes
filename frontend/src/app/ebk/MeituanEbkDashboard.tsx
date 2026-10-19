import { Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { EbkAdvancedDetails } from "./EbkAdvancedDetails";
import { EbkWatchSchedulePanel } from "./EbkWatchSchedulePanel";
import { buildMeituanEbkDashboard } from "./meituanEbkDashboardModel";
import { type BrowserSession } from "../../services/browserSessionsApi";
import { type EbkSnapshotRecord } from "../../services/ebkSnapshotsApi";
import { EmptyState, formatTime, Metric, statusLabel } from "../ui/opsPrimitives";

export function MeituanEbkDashboard(props: {
  sessions: BrowserSession[];
  selectedAccountId: string;
  snapshot: EbkSnapshotRecord | null;
  loadError: string | null;
  busy: string | null;
  onSelectAccount: (platformAccountId: string) => void;
  onReadAndSave: () => void;
  readHint?: string | null;
  shopKey?: string;
}) {
  const meituanSessions = props.sessions.filter(
    (session) => session.platform === "meituan" && session.intended_use === "ebk",
  );
  const view = buildMeituanEbkDashboard(props.snapshot);
  const [selectedDataCenterTab, setSelectedDataCenterTab] = useState("data_center_home");
  const dataCenterTabKeys = useMemo(
    () => view.dataCenter.tabs.map((tab) => tab.key).join("|"),
    [view.dataCenter.tabs],
  );

  useEffect(() => {
    if (!view.dataCenter.tabs.some((tab) => tab.key === selectedDataCenterTab)) {
      setSelectedDataCenterTab(view.dataCenter.tabs[0]?.key ?? "data_center_home");
    }
  }, [dataCenterTabKeys, selectedDataCenterTab, view.dataCenter.tabs]);

  const selectedTab =
    view.dataCenter.tabs.find((tab) => tab.key === selectedDataCenterTab) ??
    view.dataCenter.tabs[0] ??
    null;

  return (
    <div className="ebk-ops-platform-body meituan-ebk-dashboard">
      <div className="panel ebk-platform-status-bar">
        <div className="panel-title-row">
          <div>
            <h3>美团 EBK · 当前状态</h3>
            <p>只读展示已落库 snapshot；登录请在「账号与浏览器」处理。</p>
          </div>
        </div>

        <div className="form-grid">
          <label>
            美团账号
            <select
              value={props.selectedAccountId}
              onChange={(event) => props.onSelectAccount(event.target.value)}
              disabled={!meituanSessions.length}
            >
              {!meituanSessions.length ? <option value="">未配置美团账号</option> : null}
              {meituanSessions.map((session) => (
                <option key={session.browser_session_id} value={session.platform_account_id}>
                  {session.display_name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="metric-grid">
          <Metric label="读取状态" value={view.status === "missing" ? "暂无读取" : statusLabel(view.status)} />
          <Metric label="最近读取" value={view.readAt ? formatTime(view.readAt) : "暂无"} />
          <Metric
            label="数据中心"
            value={`${view.dataCenter.successCount}/${view.dataCenter.totalCount} 已读`}
            detail="首页、交易、流量、服务"
          />
          <Metric label="登录入口" value="账号与浏览器" detail="打开美团登录 / 确认登录态" />
        </div>

        {props.loadError ? <p className="field-hint">{props.loadError}</p> : null}
        {props.readHint ? <p className="field-hint">{props.readHint}</p> : null}

        <div className="action-strip">
          <button
            className="primary-button"
            type="button"
            disabled={!props.selectedAccountId || props.busy === "meituan-read-all"}
            onClick={props.onReadAndSave}
          >
            <Play size={16} />
            读取并保存
          </button>
        </div>
      </div>

      {!props.snapshot ? <EmptyState text={view.emptyText} /> : null}

      <EbkWatchSchedulePanel platform="meituan" platformLabel="美团" sessions={props.sessions} shopKey={props.shopKey} />

      <section className="panel">
        <h3>工作台</h3>
        {!view.workbench.available ? (
          <EmptyState text="工作台暂未读取" />
        ) : (
          <>
            <h4>今日实时</h4>
            <MetricList metrics={view.workbench.realtime} />
            <h4>昨日</h4>
            <MetricList metrics={view.workbench.yesterday} />
            <div className="latest-grid">
              <Metric label="HOS" value={view.workbench.hosScore ?? "—"} />
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <h3>数据中心</h3>
        {!view.dataCenter.available ? (
          <EmptyState text="数据中心暂未读取" />
        ) : (
          <>
            <div className="ebk-ops-platform-tabs meituan-data-center-tabs" role="tablist" aria-label="美团数据中心标签">
              {view.dataCenter.tabs.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  title={tab.status ? statusLabel(tab.status) : "暂无读取"}
                  aria-selected={selectedDataCenterTab === tab.key}
                  className={
                    selectedDataCenterTab === tab.key
                      ? "ebk-ops-platform-tab active"
                      : "ebk-ops-platform-tab"
                  }
                  onClick={() => setSelectedDataCenterTab(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            {selectedTab?.available ? (
              <MetricList metrics={selectedTab.metrics} />
            ) : (
              <EmptyState text={`${selectedTab?.label ?? "数据中心"}暂无读取`} />
            )}
          </>
        )}
      </section>

      <EbkAdvancedDetails title="更多美团模块：神券活动与评价管理">
        <section className="panel">
          <h3>神券活动</h3>
          {!view.promotion.available ? (
            <EmptyState text="神券活动暂未读取" />
          ) : view.promotion.activities.length ? (
            <div className="table-list">
              {view.promotion.activities.map((activity) => (
                <div className="account-row" key={`${activity.name}-${activity.status}`}>
                  <div>
                    <strong>{activity.name}</strong>
                    <span>{activity.status}</span>
                    {activity.metrics.map((metric) => (
                      <span key={metric.key}>
                        {metric.label}：{metric.value}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="暂无神券活动" />
          )}
        </section>

        <section className="panel">
          <h3>评价管理</h3>
          {!view.reviews.available ? (
            <EmptyState text="评价管理暂未读取" />
          ) : (
            <>
              <div className="latest-grid">
                <Metric label="评分" value={view.reviews.score ?? "—"} />
                <Metric label="评价总数" value={view.reviews.total ?? "—"} />
                <Metric label="待回复" value={view.reviews.pendingReplyCount ?? "—"} />
              </div>
              <h4>评价情况</h4>
              <MetricList metrics={view.reviews.situation} />
            </>
          )}
        </section>
      </EbkAdvancedDetails>
    </div>
  );
}

function MetricList({ metrics }: { metrics: Array<{ key: string; label: string; value: string }> }) {
  if (!metrics.length) {
    return <EmptyState text="暂无指标" />;
  }

  return (
    <div className="brief-metric-grid">
      {metrics.map((metric) => (
        <div className="brief-metric" key={metric.key}>
          <div className="brief-metric-title">
            <span>{metric.label}</span>
          </div>
          <strong>{metric.value}</strong>
        </div>
      ))}
    </div>
  );
}
