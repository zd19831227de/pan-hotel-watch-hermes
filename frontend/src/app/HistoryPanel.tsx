import { History, Download } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { listDataCenterGroups, listDataCenterWatchRuns } from "../services/ctripEbookingWatchApi.ts";
import { fetchOperatorRecentActivity } from "../services/opsApi.ts";
import {
  exportCtripEbkRunsCsv,
  exportEbkBusinessData,
  exportPriceChangeAlertsCsv,
  exportPricingStrategyHistory,
  cleanupEbkBusinessData,
  fetchEbkRetentionConfig,
  type ExportFormat,
} from "../services/exportsApi.ts";
import { listCalendarPriceAlerts } from "../services/calendarPriceWatchApi.ts";
import { listPriceChangeAlerts } from "../services/priceChangeAlertsApi.ts";

type DataCenterRun = Record<string, unknown>;
type PriceAlert = Record<string, unknown>;
type EbkExportRange = "today" | "week" | "month" | "custom" | "days";

const RETENTION_STORAGE_KEY = "historyRetentionDays";
const DEFAULT_RETENTION_DAYS = 180;
const limitMaximum = 5000;

function loadRetentionDays(): number {
  const stored = window.localStorage.getItem(RETENTION_STORAGE_KEY);
  const parsed = Number(stored);
  return Number.isFinite(parsed) && parsed >= 1 && parsed <= 3650 ? parsed : DEFAULT_RETENTION_DAYS;
}

function runStatusText(value: unknown): string {
  const status = String(value ?? "");
  if (status === "success") return "成功";
  if (status === "partial") return "部分成功";
  if (status === "failed") return "失败";
  if (status === "human_required") return "需要人工";
  return status || "未知";
}

function asList(value: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(value)) return value.filter((item) => !!item && typeof item === "object");
  return [];
}

export function HistoryPanel() {
  const [groups, setGroups] = useState<Array<{ group_key: string; title?: string; auto_poll_enabled?: boolean }>>([]);
  const [selectedGroup, setSelectedGroup] = useState("");
  const [ebkRuns, setEbkRuns] = useState<DataCenterRun[]>([]);
  const [activity, setActivity] = useState<Record<string, unknown> | null>(null);
  const [priceAlerts, setPriceAlerts] = useState<PriceAlert[]>([]);
  const [calendarAlerts, setCalendarAlerts] = useState<PriceAlert[]>([]);
  const [loading, setLoading] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [retentionDays, setRetentionDays] = useState<number>(loadRetentionDays);
  const [exportFormat, setExportFormat] = useState<ExportFormat>("xlsx");
  const [exportRange, setExportRange] = useState<EbkExportRange>("days");
  const [exportDays, setExportDays] = useState(7);
  const [cleaning, setCleaning] = useState(false);
  const [cleanupDays, setCleanupDays] = useState(180);
  const [cleanupMessage, setCleanupMessage] = useState<string | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [exportLimit, setExportLimit] = useState(1000);

  const customRangeError =
    exportRange === "custom" && !startDate && !endDate
      ? "自定义范围必须填写开始日期和结束日期"
      : exportRange === "custom" && (!startDate || !endDate)
        ? "自定义范围必须填写开始日期和结束日期"
        : exportRange === "custom" && startDate > endDate
          ? "开始日期不能晚于结束日期"
          : null;
  const longRangeHint =
    (exportRange === "month" || exportRange === "custom") &&
    groups.some((group) => group.auto_poll_enabled);

  useEffect(() => {
    window.localStorage.setItem(RETENTION_STORAGE_KEY, String(retentionDays));
  }, [retentionDays]);

  useEffect(() => {
    fetchEbkRetentionConfig()
      .then((config) => setRetentionDays(config.retention_days))
      .catch(() => undefined);
  }, []);

  const runExport = useCallback((action: () => Promise<void>) => {
    setExportError(null);
    action().catch((cause) => {
      setExportError(cause instanceof Error ? cause.message : "导出失败。");
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const groupList = await listDataCenterGroups();
        if (cancelled) return;
        setGroups(
          groupList.map((group) => ({
            group_key: group.group_key,
            title: group.name,
            auto_poll_enabled: group.auto_poll_enabled,
          })),
        );
        setSelectedGroup((current) => current || groupList[0]?.group_key || "");
      } catch {
        if (!cancelled) setGroups([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    const [recent, alerts, calAlerts] = await Promise.all([
      fetchOperatorRecentActivity().catch(() => null),
      selectedGroup ? listPriceChangeAlerts(selectedGroup).catch(() => [] as PriceAlert[]) : Promise.resolve([] as PriceAlert[]),
      selectedGroup ? listCalendarPriceAlerts(selectedGroup).catch(() => [] as PriceAlert[]) : Promise.resolve([] as PriceAlert[]),
    ]);
    setActivity(recent);
    setPriceAlerts(alerts);
    setCalendarAlerts(calAlerts);
    setLoading(false);
  }, [selectedGroup]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!selectedGroup) {
      setEbkRuns([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      const runs = await listDataCenterWatchRuns(selectedGroup, 20).catch(() => [] as DataCenterRun[]);
      if (!cancelled) setEbkRuns(runs);
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedGroup]);

  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <h2>历史记录</h2>
          <p>EBK 监控运行、操作记录与价格告警的只读历史查询与导出。</p>
        </div>
        {loading ? <span className="field-hint">加载中…</span> : null}
      </div>

      <div className="panel">
        <div className="section-head">
          <h3>导出设置</h3>
        </div>
        <div className="form-grid">
          <label>
            导出天数（N）
            <input
              value={exportDays}
              inputMode="numeric"
              onChange={(event) => {
                const next = Number(event.target.value) || 7;
                setExportDays(Math.max(1, Math.min(3650, next)));
              }}
            />
            <span className="field-hint">最近 N 天的数据；同时驱动经营数据、历史策略、价格告警、运行记录导出。</span>
          </label>
          <label>
            经营数据导出范围
            <select value={exportRange} onChange={(event) => setExportRange(event.target.value as EbkExportRange)}>
              <option value="today">当天</option>
              <option value="week">本周</option>
              <option value="month">本月</option>
              <option value="custom">自定义</option>
              <option value="days">按天数</option>
            </select>
          </label>
          <label>
            导出格式
            <select
              value={exportFormat}
              onChange={(event) => setExportFormat(event.target.value as ExportFormat)}
            >
              <option value="xlsx">Excel（.xlsx）</option>
              <option value="csv">CSV（Excel 可开）</option>
            </select>
          </label>
          {exportRange === "custom" ? (
            <>
              <label>
                开始日期
                <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
              </label>
              <label>
                结束日期
                <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
              </label>
            </>
          ) : null}
          <label>
            导出条数上限
            <input
              value={exportLimit}
              inputMode="numeric"
              onChange={(event) => {
                const next = Number(event.target.value) || 1000;
                setExportLimit(Math.max(1, Math.min(limitMaximum, next)));
              }}
            />
            <span className="field-hint">默认 1000，最多 {limitMaximum} 条。</span>
          </label>
        </div>
        {customRangeError ? <p className="notice-error">{customRangeError}</p> : null}
        {longRangeHint ? <p className="field-hint">数据较多，可将上限调至 {limitMaximum}。</p> : null}
        <div className="action-strip">
          <button
            className="secondary-button"
            type="button"
            disabled={Boolean(customRangeError)}
            onClick={() =>
              runExport(() =>
                exportRange === "days"
                  ? exportEbkBusinessData({ days: exportDays, limit: exportLimit, format: exportFormat })
                  : exportEbkBusinessData({
                      range: exportRange,
                      startDate: exportRange === "custom" ? startDate : undefined,
                      endDate: exportRange === "custom" ? endDate : undefined,
                      limit: exportLimit,
                      format: exportFormat,
                    }),
              )
            }
          >
            <Download size={16} /> 导出经营数据（携程/美团/飞猪）
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() => runExport(() => exportPricingStrategyHistory({ days: exportDays, format: exportFormat }))}
          >
            <Download size={16} /> 导出历史策略
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() =>
              runExport(() =>
                exportPriceChangeAlertsCsv(
                  selectedGroup
                    ? { watchGroupKey: selectedGroup, limit: 100, format: exportFormat, days: exportDays }
                    : { limit: 100, format: exportFormat, days: exportDays },
                ),
              )
            }
          >
            <Download size={16} /> 导出价格告警
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!selectedGroup}
            onClick={() =>
              runExport(() =>
                exportCtripEbkRunsCsv({
                  watchGroupKey: selectedGroup,
                  limit: 100,
                  format: exportFormat,
                }),
              )
            }
          >
            <Download size={16} /> 导出 EBK 运行记录
          </button>
        </div>
        {exportError ? <p className="notice-error">{exportError}</p> : null}
      </div>

      <div className="panel">
        <div className="section-head">
          <h3>数据留存与清理</h3>
        </div>
        <p className="field-hint">服务端自动清理阈值<strong>{retentionDays}</strong> 天：每天凌晨自动清理比此更早的数据；由服务端配置 EBK_BUSINESS_RETENTION_DAYS 决定，默认 180。</p>
        <div className="form-grid">
          <label>
            手动清理天数（0 = 清空全部）
            <input
              value={cleanupDays}
              inputMode="numeric"
              onChange={(event) => {
                const next = Number(event.target.value);
                setCleanupDays(Number.isFinite(next) ? Math.max(0, Math.min(3650, next)) : 0);
              }}
            />
            <span className="field-hint">输入 0 表示清空全部历史；输入 N 表示删除 N 天前的数据。与上面自动清理阈值无关。</span>
          </label>
        </div>
        <div className="action-strip">
          <button
            className="secondary-button"
            type="button"
            disabled={cleaning}
            onClick={() => {
              const target = cleanupDays === 0 ? "全部历史数据" : `${cleanupDays} 天前的经营历史数据`;
              if (!window.confirm(`将删除${target}（快照/运行记录），不可恢复。继续？`)) {
                return;
              }
              setCleaning(true);
              setCleanupMessage(null);
              cleanupEbkBusinessData(cleanupDays)
                .then((result) => {
                  setCleanupMessage(
                    `已清理：快照 ${result.deleted_snapshots} 条、运行 ${result.deleted_runs} 条、明细 ${result.deleted_dataset_results} 条。`,
                  );
                })
                .catch((cause) => {
                  setCleanupMessage(cause instanceof Error ? cause.message : "清理失败。");
                })
                .finally(() => setCleaning(false));
            }}
          >
            立即清理经营历史数据
          </button>
          <span className="field-hint">按上面天数立即执行一次性清理；不影响自动清理的留存阈值。</span>
        </div>
        {cleanupMessage ? <p className="field-hint">{cleanupMessage}</p> : null}
      </div>

      <div className="history-panels-grid">
      <div className="panel">
        <div className="section-head">
          <h3>EBK 监控运行历史</h3>
          <select value={selectedGroup} onChange={(event) => setSelectedGroup(event.target.value)}>
            {!groups.length ? <option value="">暂无监控组</option> : null}
            {groups.map((group) => (
              <option key={group.group_key} value={group.group_key}>
                {group.title || group.group_key}
              </option>
            ))}
          </select>
        </div>
        <div className="table-list">
          {ebkRuns.map((run, index) => (
            <div className="account-row" key={String(run.run_id ?? run.id ?? index)}>
              <div>
                <strong>{String(run.started_at ?? run.created_at ?? `运行 ${index + 1}`)}</strong>
                <span>
                  状态：{runStatusText(run.status ?? run.overall_status)} · 数据集：{String(run.dataset_count ?? run.datasets ?? "-")}
                </span>
                {run.summary ? <span className="field-hint">{String(run.summary)}</span> : null}
              </div>
            </div>
          ))}
          {!ebkRuns.length ? <p className="field-hint">还没有该监控组的运行记录。</p> : null}
        </div>
      </div>

      <div className="panel">
        <h3>操作记录</h3>
        <div className="table-list">
          {asList(activity?.items ?? activity?.activities ?? activity?.records).map((item, index) => (
            <div className="account-row" key={index}>
              <div>
                <strong>{String(item.title ?? item.action ?? item.summary ?? "操作")}</strong>
                <span>
                  {String(item.time ?? item.created_at ?? item.at ?? "")}
                  {item.status ? ` · ${runStatusText(item.status)}` : ""}
                </span>
              </div>
            </div>
          ))}
          {!asList(activity?.items ?? activity?.activities ?? activity?.records).length ? (
            <p className="field-hint">暂无操作记录。</p>
          ) : null}
        </div>
      </div>

      <div className="panel">
        <h3>价格与日历价告警</h3>
        <div className="table-list">
          {priceAlerts.map((alert, index) => (
            <div className="account-row" key={`price-${index}`}>
              <div>
                <strong>价格变化提醒</strong>
                <span>
                  {String(alert.created_at ?? alert.triggered_at ?? "")} · {String(alert.summary ?? alert.alert_summary ?? "")}
                </span>
              </div>
            </div>
          ))}
          {calendarAlerts.map((alert, index) => (
            <div className="account-row" key={`calendar-${index}`}>
              <div>
                <strong>日历价提醒</strong>
                <span>
                  {String(alert.created_at ?? alert.alert_at ?? "")} · {String(alert.summary ?? alert.message ?? "")}
                </span>
              </div>
            </div>
          ))}
          {!priceAlerts.length && !calendarAlerts.length ? (
            <p className="field-hint">暂无价格与日历价告警记录。</p>
          ) : null}
        </div>
      </div>

      </div>
      <p className="field-hint">
        <History size={14} /> 所有历史区块均为只读展示，不提供任何执行操作。
      </p>
    </section>
  );
}
