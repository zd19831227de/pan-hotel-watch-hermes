import { RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  cleanupBrowserCache,
  cleanupScreenshots,
  getBrowserCacheAudit,
  getScreenshotAudit,
  type BrowserCacheAudit,
  type BrowserCacheCleanupResult,
  type ScreenshotAudit,
  type ScreenshotCleanupResult,
} from "../services/configApi";

type BrowserCachePanelProps = {
  apiKey: string;
};

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "缓存审计/清理失败。";
}

export function BrowserCachePanel({ apiKey }: BrowserCachePanelProps) {
  // --- browser web-cache (login state + Chromium models are never cleared) ---
  const [audit, setAudit] = useState<BrowserCacheAudit | null>(null);
  const [loading, setLoading] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [result, setResult] = useState<BrowserCacheCleanupResult | null>(null);
  // --- screenshots ---
  const [shotAudit, setShotAudit] = useState<ScreenshotAudit | null>(null);
  const [shotRetention, setShotRetention] = useState<number>(30);
  const [shotLoading, setShotLoading] = useState(false);
  const [shotCleaning, setShotCleaning] = useState(false);
  const [shotResult, setShotResult] = useState<ScreenshotCleanupResult | null>(null);

  const [error, setError] = useState<string | null>(null);

  const loadAudit = useCallback(async () => {
    if (!apiKey.trim()) {
      setError("请先填写 Supervisor API Key。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setAudit(await getBrowserCacheAudit(apiKey));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  const loadShotAudit = useCallback(
    async (retention: number) => {
      if (!apiKey.trim()) return;
      setShotLoading(true);
      setError(null);
      try {
        setShotAudit(await getScreenshotAudit(apiKey, retention));
      } catch (err) {
        setError(errorText(err));
      } finally {
        setShotLoading(false);
      }
    },
    [apiKey],
  );

  useEffect(() => {
    if (apiKey.trim() && !audit) {
      void loadAudit();
    }
    if (apiKey.trim() && !shotAudit) {
      void loadShotAudit(shotRetention);
    }
  }, [apiKey, audit, shotAudit, loadAudit, loadShotAudit, shotRetention]);

  const cleanNow = useCallback(async () => {
    if (!apiKey.trim()) {
      setError("请先填写 Supervisor API Key。");
      return;
    }
    if (!window.confirm("确认清理浏览器网页缓存？仅清除审计确认的缓存，登录态与模型保留。")) {
      return;
    }
    setCleaning(true);
    setError(null);
    setResult(null);
    try {
      const res = await cleanupBrowserCache(apiKey, { min_age_days: 0, dry_run: false });
      setResult(res);
      await loadAudit();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setCleaning(false);
    }
  }, [apiKey, loadAudit]);

  const cleanScreenshots = useCallback(
    async (retention: number) => {
      if (!apiKey.trim()) {
        setError("请先填写 Supervisor API Key。");
        return;
      }
      const r = retention > 0 ? retention : "全部";
      if (
        !window.confirm(
          `确认清理截图？将删除保留天数（>${r} 天）以外的 OTA 抓取截图（.png），非截图文件保留。`,
        )
      ) {
        return;
      }
      setShotCleaning(true);
      setError(null);
      setShotResult(null);
      try {
        const res = await cleanupScreenshots(apiKey, { retention_days: retention, dry_run: false });
        setShotResult(res);
        await loadShotAudit(retention);
      } catch (err) {
        setError(errorText(err));
      } finally {
        setShotCleaning(false);
      }
    },
    [apiKey, loadShotAudit],
  );

  return (
    <section className="advanced-agent-block">
      <div className="advanced-agent-block-header">
        <h4>缓存管理</h4>
      </div>

      {error ? <p className="advanced-agent-error">{error}</p> : null}

      {/* ---- browser web-cache ---- */}
      <div className="advanced-agent-block-header">
        <h5>浏览器网页缓存</h5>
        <button
          type="button"
          className="advanced-agent-secondary-btn"
          onClick={() => void loadAudit()}
          disabled={loading || cleaning || !apiKey.trim()}
        >
          <RefreshCw size={14} />
          {loading ? "审计中…" : "刷新审计"}
        </button>
      </div>
      <p className="advanced-maintenance-hint">
        仅清理审计确认的网页缓存（HTTP/GPU/Service Worker）。登录态（Cookies/Login Data/Local
        State）与 Chromium 模型属于业务/核心组件，保留不清。定时任务默认每天 03:00 按
        BROWSER_CACHE_RETENTION_DAYS（默认 60 天）清理。
      </p>

      {audit ? (
        <div className="advanced-agent-summary">
          <span>总体积 {formatBytes(audit.total_bytes)}</span>
          <span>
            可清理 {formatBytes(audit.cleanable_bytes)}（{(audit.cleanable_fraction * 100).toFixed(1)}%）
          </span>
        </div>
      ) : null}

      {audit && audit.platforms.length ? (
        <table className="advanced-agent-table">
          <thead>
            <tr>
              <th>平台</th>
              <th>profile 数</th>
              <th>总体积</th>
              <th>可清理</th>
              <th>可清理占比</th>
            </tr>
          </thead>
          <tbody>
            {audit.platforms.map((plat) => {
              const frac = plat.total_bytes > 0 ? plat.cleanable_bytes / plat.total_bytes : 0;
              return (
                <tr key={plat.platform}>
                  <td>{plat.platform}</td>
                  <td>{plat.profile_count}</td>
                  <td>{formatBytes(plat.total_bytes)}</td>
                  <td>{formatBytes(plat.cleanable_bytes)}</td>
                  <td>{(frac * 100).toFixed(1)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}

      <div className="browser-cache-action-row">
        <button
          type="button"
          className="advanced-agent-danger-btn"
          onClick={() => void cleanNow()}
          disabled={cleaning || loading || !apiKey.trim()}
        >
          <Trash2 size={14} />
          {cleaning ? "清理中…" : "立即清理网页缓存"}
        </button>
        {result ? (
          <span className="advanced-maintenance-hint">
            已清理 {result.profiles_cleaned} 个 profile，释放 {result.bytes_freed_human}。
          </span>
        ) : null}
      </div>

      {/* ---- playwright screenshots ---- */}
      <div className="advanced-agent-block-header">
        <h5>OTA 抓取截图</h5>
        <button
          type="button"
          className="advanced-agent-secondary-btn"
          onClick={() => void loadShotAudit(shotRetention)}
          disabled={shotLoading || shotCleaning || !apiKey.trim()}
        >
          <RefreshCw size={14} />
          {shotLoading ? "审计中…" : "按保留天数审计"}
        </button>
      </div>
      <p className="advanced-maintenance-hint">
        OTA 读取时的浏览器截图（output/playwright/*.png），非业务数据。按下方保留天数清理更早的；
        非截图文件保留。定时任务默认每天 03:00 按
        PLAYWRIGHT_SCREENSHOT_RETENTION_DAYS（默认 30 天）清理。
      </p>

      <div className="browser-cache-retention-row">
        <label htmlFor="shot-retention">保留天数</label>
        <input
          id="shot-retention"
          type="number"
          min={0}
          value={shotRetention}
          onChange={(e) => setShotRetention(Math.max(0, Number(e.target.value) || 0))}
          style={{ width: 80 }}
        />
        <span className="advanced-maintenance-hint">（0 = 清理全部截图）</span>
      </div>

      {shotAudit ? (
        <div className="advanced-agent-summary">
          <span>共 {shotAudit.total_files} 张 / {formatBytes(shotAudit.total_bytes)}</span>
          <span>
            可清理（&gt;{shotAudit.retention_days} 天）：{shotAudit.cleanable_files} 张 /{" "}
            {formatBytes(shotAudit.cleanable_bytes)}
          </span>
        </div>
      ) : null}

      <div className="browser-cache-action-row">
        <button
          type="button"
          className="advanced-agent-danger-btn"
          onClick={() => void cleanScreenshots(shotRetention)}
          disabled={shotCleaning || shotLoading || !apiKey.trim()}
        >
          <Trash2 size={14} />
          {shotCleaning
            ? "清理中…"
            : `清理截图（保留 ${shotRetention > 0 ? shotRetention + " 天及以内" : "无"}）`}
        </button>
        {shotResult ? (
          <span className="advanced-maintenance-hint">
            已删除 {shotResult.files_removed} 张，释放 {shotResult.bytes_freed_human}。
          </span>
        ) : null}
      </div>
    </section>
  );
}
