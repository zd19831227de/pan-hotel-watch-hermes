import { BarChart3, Download, FileSpreadsheet, History, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  exportBiDailyCsv,
  exportBiDailyXlsx,
  fetchBiBackfillStatus,
  fetchBiDailyReport,
  importBiBudgetForecast,
  startBiBackfill,
  triggerBiCollect,
  type BiBackfillStatus,
} from "../../services/biDailyApi.ts";
import { listShops } from "../../services/shopsApi.ts";
import { EmptyState, Metric, ResultNotice } from "../ui/opsPrimitives.tsx";
import {
  backfillFailureSamples,
  backfillPlatformLines,
  backfillStatusText,
  forwardMatrixRows,
  performanceMatrixRows,
  priorYearMappingText,
  qualityLines,
  weekdayProfileRows,
  type BiDailyReport,
} from "./biDailyModel.ts";

/** 经营 BI 日报面板：只读矩阵 + 完成率 + 数据情况 + 导入/导出。 */

export function BiDailyPanel() {
  const [shops, setShops] = useState<Array<{ shop_key: string; name: string }>>([]);
  const [shopKey, setShopKey] = useState("");
  const [report, setReport] = useState<BiDailyReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [importVersion, setImportVersion] = useState("");
  const [importFrom, setImportFrom] = useState("");
  const [importTo, setImportTo] = useState("");
  const [backfillFrom, setBackfillFrom] = useState("");
  const [backfillTo, setBackfillTo] = useState("");
  const [backfill, setBackfill] = useState<BiBackfillStatus | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const backfillTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshBackfill = useCallback(async (): Promise<boolean> => {
    try {
      const status = await fetchBiBackfillStatus();
      setBackfill(status);
      return String(status.status ?? "idle") === "running";
    } catch {
      return false;
    }
  }, []);

  useEffect(() => {
    listShops()
      .then((list) => {
        setShops(list.map((shop) => ({ shop_key: shop.shop_key, name: shop.name })));
        setShopKey((current) => current || list[0]?.shop_key || "");
      })
      .catch(() => setShops([]));
  }, []);

  const reload = useCallback(async () => {
    if (!shopKey) return;
    setLoading(true);
    setError(null);
    try {
      const payload = (await fetchBiDailyReport(shopKey)) as unknown as BiDailyReport;
      setReport(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "经营 BI 数据加载失败。");
    } finally {
      setLoading(false);
    }
  }, [shopKey]);

  useEffect(() => {
    if (shopKey) void reload();
  }, [shopKey, reload]);

  // 进入页面先看一眼回填状态：别的页签发起的补抓也要能看到进度；运行中每 5 秒轮询。
  useEffect(() => {
    void refreshBackfill();
    return () => {
      if (backfillTimerRef.current) clearInterval(backfillTimerRef.current);
    };
  }, [refreshBackfill]);

  useEffect(() => {
    const running = String(backfill?.status ?? "") === "running";
    if (!running) {
      if (backfillTimerRef.current) {
        clearInterval(backfillTimerRef.current);
        backfillTimerRef.current = null;
      }
      return;
    }
    if (backfillTimerRef.current) return;
    backfillTimerRef.current = setInterval(() => {
      void refreshBackfill().then((stillRunning) => {
        if (!stillRunning) void reload();
      });
    }, 5000);
    return () => {
      if (backfillTimerRef.current) {
        clearInterval(backfillTimerRef.current);
        backfillTimerRef.current = null;
      }
    };
  }, [backfill?.status, refreshBackfill, reload]);

  const runAction = useCallback(
    async (label: string, action: () => Promise<string | void>) => {
      setBusy(label);
      setError(null);
      setNotice(null);
      try {
        const message = await action();
        if (message) setNotice(message);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "操作失败。");
      } finally {
        setBusy(null);
      }
    },
    []
  );

  const forwardRows = report ? forwardMatrixRows(report) : [];
  const performanceRows = report ? performanceMatrixRows(report) : [];
  const weekdayRows = report ? weekdayProfileRows(report) : [];
  const quality = report ? qualityLines(report) : [];

  return (
    <section className="workspace">
      <div className="section-head">
        <div className="section-title">
          <BarChart3 size={18} />
          <span>经营 BI 日报</span>
        </div>
        <div className="action-strip bi-toolbar">
          <select value={shopKey} onChange={(event) => setShopKey(event.target.value)}>
            {shops.map((shop) => (
              <option key={shop.shop_key} value={shop.shop_key}>
                {shop.name}
              </option>
            ))}
          </select>
          <button
            className="secondary-button"
            type="button"
            onClick={() => void reload()}
            disabled={loading || !shopKey}
          >
            <RefreshCw size={14} /> 刷新
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={!shopKey || busy === "collect"}
            onClick={() =>
              void runAction("collect", async () => {
                await triggerBiCollect();
                await reload();
                return "已触发一轮采集；数据情况已更新。";
              })
            }
          >
            立即采集
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!shopKey}
            onClick={() =>
              void runAction("csv", async () => {
                await exportBiDailyCsv(shopKey);
              })
            }
          >
            <Download size={14} /> 导出 CSV
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!shopKey}
            onClick={() =>
              void runAction("xlsx", async () => {
                await exportBiDailyXlsx(shopKey);
              })
            }
          >
            <FileSpreadsheet size={14} /> 导出 XLSX
          </button>
        </div>
      </div>

      {error ? <ResultNotice status="error" summary={error} /> : null}
      {notice ? <ResultNotice status="success" summary={notice} /> : null}

      {!shops.length ? (
        <EmptyState text="还没有店铺；请先在「账号与浏览器」完成店铺配置。" />
      ) : !report ? (
        <EmptyState text={loading ? "正在加载经营 BI 数据…" : "暂无经营 BI 数据。"} />
      ) : (
        <>
          <div className="split-grid">
            <div className="panel">
              <h3>数据情况</h3>
              <div className="metric-grid">
                {quality.map((line) => (
                  <Metric key={line.label} label={line.label} value={line.text} />
                ))}
              </div>
              <p>
                本店在手预订/总房量来自携程「房价房态日历」页（携程渠道已售/房型组共N间）；
                竞争圈在手与出租率、市场热度为携程市场分析页口径，非本系统推断。
              </p>
            </div>
            <div className="panel">
              <h3>预算与预测导入（版本化留痕）</h3>
              <div className="form-grid">
                <label>
                  版本号
                  <input
                    value={importVersion}
                    onChange={(event) => setImportVersion(event.target.value)}
                    placeholder="如 2026-10-v1"
                  />
                </label>
                <label>
                  生效起
                  <input
                    type="date"
                    value={importFrom}
                    onChange={(event) => setImportFrom(event.target.value)}
                  />
                </label>
                <label>
                  生效止
                  <input
                    type="date"
                    value={importTo}
                    onChange={(event) => setImportTo(event.target.value)}
                  />
                </label>
                <div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".csv,.xlsx"
                    style={{ display: "none" }}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (!file) return;
                      if (!importVersion.trim()) {
                        setError("请先填写预算版本号再选择文件。");
                        return;
                      }
                      void runAction("import", async () => {
                        const result = (await importBiBudgetForecast({
                          shopKey,
                          forecastVersion: importVersion.trim(),
                          validFrom: importFrom,
                          validTo: importTo,
                          file,
                        })) as { rows_valid?: number; errors?: string[] };
                        await reload();
                        const errors = result.errors ?? [];
                        return `导入完成：有效 ${result.rows_valid ?? 0} 行${
                          errors.length ? `；问题行示例：${errors.slice(0, 2).join("；")}` : ""
                        }`;
                      });
                    }}
                  />
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={busy === "import" || !shopKey}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    选择 CSV/XLSX 导入
                  </button>
                </div>
              </div>
              <p>
                表头支持「入住日期 / 房型 / 预算间夜 / 预算收入 / 预测间夜 / 预测收入」；同版本重导会覆盖同日期数值，导入记录全部留审计。
              </p>
            </div>
          </div>

          <div className="panel">
            <h3>历史缺数补抓（用已登录浏览器现读）</h3>
              <div className="form-grid">
                <label>
                  补抓起
                  <input
                    type="date"
                    value={backfillFrom}
                    onChange={(event) => setBackfillFrom(event.target.value)}
                  />
                </label>
                <label>
                  补抓止
                  <input
                    type="date"
                    value={backfillTo}
                    onChange={(event) => setBackfillTo(event.target.value)}
                  />
                </label>
                <div>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={
                      !shopKey ||
                      !backfillFrom ||
                      !backfillTo ||
                      String(backfill?.status ?? "") === "running"
                    }
                    onClick={() =>
                      void runAction("backfill", async () => {
                        await startBiBackfill({
                          shopKey,
                          dateFrom: backfillFrom,
                          dateTo: backfillTo,
                        });
                        await refreshBackfill();
                        return "已发起补抓：正在用已登录的 EBK 浏览器逐日读取缺失日期，完成后自动刷新报表。";
                      })
                    }
                  >
                    <History size={14} /> 开始补抓
                  </button>
                </div>
              </div>
              {backfill && String(backfill.status ?? "idle") !== "idle" ? (
                <div className="table-list">
                  <div className="hotel-row">
                    <div>
                      <strong>{backfillStatusText(backfill.status)}</strong>
                      <span>
                        {backfill.date_from} ~ {backfill.date_to}
                        {typeof backfill.facts_written === "number" && backfill.facts_written > 0
                          ? ` · 新增事实 ${backfill.facts_written} 行`
                          : ""}
                      </span>
                    </div>
                    <div>
                      <span>{backfill.summary ?? ""}</span>
                    </div>
                  </div>
                  {backfillPlatformLines(backfill).map((line) => (
                    <div className="hotel-row" key={line}>
                      <div>
                        <span>{line}</span>
                      </div>
                    </div>
                  ))}
                  {backfillFailureSamples(backfill).map((line) => (
                    <div className="hotel-row" key={line}>
                      <div>
                        <span>{line}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
              <p>
                只补缺失的日期（已有数据自动跳过）；逐日读取平台页面耗时数分钟，中途可离开本页。
                单次最多 92 天，日期只能是昨天及更早。
              </p>
            </div>

          <div className="panel">
            <h3>未来 14 天：在手预订 · 竞争圈 · 市场热度</h3>
            {forwardRows.length ? (
              <>
                <div className="table-list">
                  {forwardRows.map((row) => (
                    <div className="hotel-row" key={row.dateLabel}>
                      <div>
                        <strong>
                          {row.dateLabel} {row.weekday}
                        </strong>
                        <span>
                          在手 {row.ownInventory} · 出租率 {row.ownOccupancy} · 预埋价{" "}
                          {row.preSetPrice} · 本店 {row.ownPrice} · 竞品价格带{" "}
                          {row.competitorPriceBand}
                        </span>
                      </div>
                      <div>
                        <span>
                          {row.circleVerdict}（{row.circleOccupancy}）
                          {row.cityHeat !== "—" ? ` · 热度 ${row.cityHeat}` : ""}
                          {row.events ? ` · ${row.events}` : ""}
                        </span>
                        <span>去年同星期 {row.priorYearRef}</span>
                      </div>
                    </div>
                  ))}
                </div>
                <p>
                  在手=携程渠道已售/房型组总房量；「—」=该日未采到（不是 0）。竞争圈判定按
                  携程市场分析的我的出租率 vs 竞争圈出租率；预埋价取无早最低卖价。
                </p>
              </>
            ) : (
              <EmptyState text="暂无未来 14 天数据。" />
            )}
          </div>

          <div className="panel">
            <h3>去年同期（按星期）：出租率 / 过夜价 / 同比完成系数</h3>
            {weekdayRows.length ? (
              <>
                <div className="table-list">
                  {weekdayRows.map((row) => (
                    <div className="hotel-row" key={row.weekdayLabel}>
                      <div>
                        <strong>{row.weekdayLabel}</strong>
                        <span>本期 {row.currentSummary}</span>
                      </div>
                      <div>
                        <span>
                          {row.priorSummary} · 间夜系数 {row.nightsCompletion} · 出租率{" "}
                          {row.occupancyDelta} · 均价系数 {row.adrRatio}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
                <p>
                  去年同期=同星期映射（52 周前优先，跨年 53 周）；出租率/均价取携程离店口径，
                  美团/飞猪无页面出租率时如实缺省。
                </p>
              </>
            ) : (
              <EmptyState text="暂无历史经营数据；采集后会按星期聚合。" />
            )}
          </div>

          <div className="panel">
            <h3>历史表现：同比 / 预算 / 预测完成率</h3>
            {performanceRows.length ? (
              <>
                <div className="table-list">
                  {performanceRows.map((row, index) => (
                    <div className="hotel-row" key={row.dateLabel}>
                      <div>
                        <strong>
                          {row.dateLabel} {row.weekday}
                        </strong>
                        <span>
                          间夜 {row.nights} · 同比 {row.priorYear} · 出租率 {row.occupancyDelta} ·
                          均价 {row.adrRatio} · 预算 {row.budget} · 预测 {row.forecast}
                        </span>
                      </div>
                      <div>
                        <span>对比 {priorYearMappingText(report.performance[index])}</span>
                      </div>
                    </div>
                  ))}
                </div>
                <p>
                  「—」= 暂无该基线：同比需去年同期（同星期）经营数据（可在上方补抓去年同期日期），
                  预算/预测需在上方导入文件后展示完成率；出租率同比单位为百分点（pp）。
                </p>
              </>
            ) : (
              <EmptyState text="暂无历史经营数据；每日采集后会在这里汇总。" />
            )}
          </div>
        </>
      )}
    </section>
  );
}
