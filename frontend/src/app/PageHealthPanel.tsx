import { Activity, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  deletePageHealthAlert,
  fetchPageHealthAlerts,
  type PageHealthAlert,
} from "../services/pageHealthApi.ts";

type LevelFilter = "" | "hard_fail" | "partial_fail" | "layout_changed";

const LEVEL_META: Record<
  PageHealthAlert["level"],
  { label: string; badgeClass: string; dotClass: string }
> = {
  hard_fail: {
    label: "严重（selector 失效）",
    badgeClass: "level-badge level-critical",
    dotClass: "level-dot level-critical",
  },
  partial_fail: {
    label: "部分缺失（降级）",
    badgeClass: "level-badge level-warning",
    dotClass: "level-dot level-warning",
  },
  layout_changed: {
    label: "结构变化（已恢复读取）",
    badgeClass: "level-badge level-info",
    dotClass: "level-dot level-info",
  },
};

function platformLabel(platform: string): string {
  if (platform === "ctrip") return "携程";
  if (platform === "meituan") return "美团";
  if (platform === "fliggy") return "飞猪";
  return platform;
}

export function PageHealthPanel() {
  const [alerts, setAlerts] = useState<PageHealthAlert[]>([]);
  const [platformFilter, setPlatformFilter] = useState("");
  const [levelFilter, setLevelFilter] = useState<LevelFilter>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await fetchPageHealthAlerts({
        platform: platformFilter || undefined,
        level: levelFilter || undefined,
        limit: 50,
      });
      setAlerts(list);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载页面健康告警失败。");
    } finally {
      setLoading(false);
    }
  }, [platformFilter, levelFilter]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <section className="panel page-health-panel">
      <div className="section-head">
        <div>
          <h3>
            <Activity size={16} /> 页面健康
          </h3>
          <p>最近页面结构/selector 告警。红色=严重，橙色=部分缺失，蓝色=结构变化；确认已处理可删除。</p>
        </div>
        <button className="secondary-button" type="button" disabled={loading} onClick={() => void reload()}>
          <RefreshCw size={16} /> 刷新
        </button>
      </div>

      <div className="form-grid">
        <label>
          平台
          <select value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value)}>
            <option value="">全部平台</option>
            <option value="ctrip">携程</option>
            <option value="meituan">美团</option>
            <option value="fliggy">飞猪</option>
          </select>
        </label>
        <label>
          级别
          <select
            value={levelFilter}
            onChange={(event) => setLevelFilter(event.target.value as LevelFilter)}
          >
            <option value="">全部级别</option>
            <option value="hard_fail">严重（红）</option>
            <option value="partial_fail">部分缺失（橙）</option>
            <option value="layout_changed">结构变化（蓝）</option>
          </select>
        </label>
      </div>

      {error ? <p className="notice-error">{error}</p> : null}

      <div className="table-list">
        {alerts.map((alert) => {
          const meta = LEVEL_META[alert.level] ?? LEVEL_MISC;
          return (
            <div className="page-health-alert-row" key={alert.id}>
              <span className={meta.dotClass} aria-hidden="true" />
              <div className="page-health-alert-content">
                <strong>
                  <span className={meta.badgeClass}>{levelShortLabel(alert.level)}</span>
                  {platformLabel(alert.platform)} · {alert.target}
                </strong>
                <span>{alert.summary}</span>
                <span>{alert.created_at ?? ""}</span>
              </div>
              <button
                type="button"
                className="page-health-alert-delete"
                title="确认已处理，删除此告警"
                disabled={deletingId === alert.id}
                onClick={() => {
                  setDeletingId(alert.id);
                  deletePageHealthAlert(alert.id)
                    .then(() => setAlerts((current) => current.filter((item) => item.id !== alert.id)))
                    .catch((cause) =>
                      setError(cause instanceof Error ? cause.message : "删除失败。"),
                    )
                    .finally(() => setDeletingId(null));
                }}
              >
                <Trash2 size={15} />
                <span className="sr-only">删除</span>
              </button>
            </div>
          );
        })}
        {!alerts.length && !loading ? (
          <p className="field-hint">暂无页面健康告警。采集链路 selector 正常。</p>
        ) : null}
        {loading ? <p className="field-hint">加载中…</p> : null}
      </div>
    </section>
  );
}

const LEVEL_MISC = {
  label: "未知",
  badgeClass: "level-badge level-info",
  dotClass: "level-dot level-info",
};

function levelShortLabel(level: string): string {
  if (level === "hard_fail") return "严重";
  if (level === "partial_fail") return "部分缺失";
  if (level === "layout_changed") return "结构变化";
  return level;
}
