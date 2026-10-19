import { Play } from "lucide-react";

import { EbkAdvancedDetails } from "./EbkAdvancedDetails";
import { EbkWatchSchedulePanel } from "./EbkWatchSchedulePanel";
import { buildFliggyEbkDashboard } from "./fliggyEbkDashboardModel";
import { type BrowserSession } from "../../services/browserSessionsApi";
import { type EbkSnapshotRecord } from "../../services/ebkSnapshotsApi";
import { EmptyState, formatTime, Metric, statusLabel } from "../ui/opsPrimitives";

export function FliggyEbkDashboard(props: {
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
  const fliggySessions = props.sessions.filter(
    (session) => session.platform === "fliggy" && session.intended_use === "ebk",
  );
  const view = buildFliggyEbkDashboard(props.snapshot);

  return (
    <div className="ebk-ops-platform-body fliggy-ebk-dashboard">
      <div className="panel ebk-platform-status-bar">
        <div className="panel-title-row">
          <div>
            <h3>飞猪 EBK · 当前状态</h3>
            <p>只读展示已落库 snapshot；登录请在「账号与浏览器」处理。</p>
          </div>
        </div>

        <div className="form-grid">
          <label>
            飞猪账号
            <select
              value={props.selectedAccountId}
              onChange={(event) => props.onSelectAccount(event.target.value)}
              disabled={!fliggySessions.length}
            >
              {!fliggySessions.length ? <option value="">未配置飞猪账号</option> : null}
              {fliggySessions.map((session) => (
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
            label="数据集"
            value={`${view.successCount}/${view.totalCount} 已读`}
            detail="首页、诊断、经营、促销"
          />
          <Metric label="登录入口" value="账号与浏览器" detail="打开飞猪登录 / 确认登录态" />
        </div>

        {props.loadError ? <p className="field-hint">{props.loadError}</p> : null}
        {props.readHint ? <p className="field-hint">{props.readHint}</p> : null}

        <div className="action-strip">
          <button
            className="primary-button"
            type="button"
            disabled={!props.selectedAccountId || props.busy === "fliggy-read-all"}
            onClick={props.onReadAndSave}
          >
            <Play size={16} />
            读取并保存
          </button>
        </div>
      </div>

      {!props.snapshot ? <EmptyState text={view.emptyText} /> : null}

      <EbkWatchSchedulePanel platform="fliggy" platformLabel="飞猪" sessions={props.sessions} shopKey={props.shopKey} />

      <section className="panel">
        <h3>首页 · 今日 / 昨日</h3>
        {!view.home.available ? (
          <EmptyState text="首页暂未读取" />
        ) : (
          <>
            <h4>今日数据</h4>
            <MetricList metrics={view.home.today} />
            <h4>昨日数据</h4>
            <MetricList metrics={view.home.yesterday} />
          </>
        )}
      </section>

      <section className="panel">
        <h3>诊断报告</h3>
        {!view.diagnose.available ? (
          <EmptyState text="诊断报告暂未读取" />
        ) : (
          <>
            <h4>交易数据概览</h4>
            <MetricList metrics={view.diagnose.trade} />
            <div className="latest-grid">
              <Metric label="客人点评得分" value={view.diagnose.guestReviewScore ?? "—"} />
              <Metric label="MCI 酒店竞争力指数" value={view.diagnose.mciIndex ?? "—"} />
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <h3>经营数据</h3>
        {!view.operating.available ? (
          <EmptyState text="经营数据暂未读取" />
        ) : (
          <>
            <h4>在店数据</h4>
            <MetricList metrics={view.operating.inHouse} />
            <h4>预定数据</h4>
            <MetricList metrics={view.operating.booking} />
          </>
        )}
      </section>

      <EbkAdvancedDetails title="更多飞猪模块：已参加活动">
        <section className="panel">
          <h3>已参加活动</h3>
          {!view.promotion.available ? (
            <EmptyState text="促销活动暂未读取" />
          ) : view.promotion.activities.length ? (
            <div className="table-list">
              {view.promotion.activities.map((activity) => (
                <div className="account-row" key={`${activity.name}-${activity.status}`}>
                  <div>
                    <strong>{activity.name}</strong>
                    <span>{activity.status}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="暂无已参加活动" />
          )}
        </section>
      </EbkAdvancedDetails>
    </div>
  );
}

function MetricList(props: { metrics: Array<{ key: string; label: string; value: string }> }) {
  if (!props.metrics.length) {
    return <EmptyState text="暂无指标" />;
  }
  return (
    <div className="latest-grid">
      {props.metrics.map((metric) => (
        <Metric key={metric.key} label={metric.label} value={metric.value} />
      ))}
    </div>
  );
}
