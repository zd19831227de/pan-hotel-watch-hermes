import { getRuntimeApiKey } from "./http.ts";

export type ExportFormat = "csv" | "xlsx";

function timestampTag(): string {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, "");
}

async function downloadCsv(
  path: string,
  fallbackFilename: string,
): Promise<void> {
  const headers: Record<string, string> = {};
  const apiKey = getRuntimeApiKey();
  if (apiKey) {
    headers["X-Pan-Hotel-API-Key"] = apiKey;
  }
  const response = await fetch(path, { headers });
  if (!response.ok) {
    throw new Error(`读取留存配置失败（HTTP ${response.status}）`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fallbackFilename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function exportCompetitorPriceTrendCsv(input: {
  watchGroupKey: string;
  days?: number;
  granularity?: "run" | "hour" | "day";
  dateLabel?: string;
  breakfastKind?: string;
}): Promise<void> {
  const params = new URLSearchParams();
  params.set("key", input.watchGroupKey);
  params.set("days", String(input.days ?? 7));
  params.set("granularity", input.granularity ?? "run");
  if (input.dateLabel) params.set("date_label", input.dateLabel);
  if (input.breakfastKind) params.set("breakfast_kind", input.breakfastKind);
  await downloadCsv(
    `/api/v1/export/competitor-price-trend.csv?${params.toString()}`,
    `competitor-price-trend-${input.watchGroupKey}-${timestampTag()}.csv`,
  );
}

export async function exportPriceChangeAlertsCsv(input?: {
  watchGroupKey?: string;
  limit?: number;
  days?: number;
  format?: ExportFormat;
}): Promise<void> {
  const params = new URLSearchParams();
  if (input?.watchGroupKey) params.set("watch_group_key", input.watchGroupKey);
  params.set("limit", String(input?.limit ?? 100));
  if (input?.days) params.set("days", String(input.days));
  const format = input?.format ?? "csv";
  params.set("format", format);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  await downloadCsv(
    `/api/v1/export/price-change-alerts.csv${suffix}`,
    `price-change-alerts-${timestampTag()}.${format}`,
  );
}

export async function exportCtripEbkRunsCsv(input: {
  watchGroupKey: string;
  limit?: number;
  format?: ExportFormat;
}): Promise<void> {
  const params = new URLSearchParams();
  params.set("key", input.watchGroupKey);
  params.set("limit", String(input.limit ?? 100));
  const format = input.format ?? "csv";
  params.set("format", format);
  await downloadCsv(
    `/api/v1/export/ctrip-ebk-runs.csv?${params.toString()}`,
    `ctrip-ebk-runs-${input.watchGroupKey}-${timestampTag()}.${format}`,
  );
}

export async function exportDistillRunsCsv(
  limit = 100,
  format: ExportFormat = "csv",
): Promise<void> {
  await downloadCsv(
    `/api/v1/export/distill-runs.csv?limit=${limit}&format=${format}`,
    `distill-runs-${timestampTag()}.${format}`,
  );
}

export type EbkExportPlatform = "ctrip" | "meituan" | "fliggy";

export async function exportEbkBusinessData(input?: {
  range?: "today" | "week" | "month" | "custom";
  startDate?: string;
  endDate?: string;
  days?: number;
  limit?: number;
  platforms?: EbkExportPlatform[];
  format?: ExportFormat;
}): Promise<void> {
  const params = new URLSearchParams();
  if (input?.range) {
    params.set("range", input.range);
    if (input.range === "custom") {
      if (input.startDate) params.set("start_date", input.startDate);
      if (input.endDate) params.set("end_date", input.endDate);
    }
  } else {
    params.set("days", String(input?.days ?? 180));
  }
  params.set("limit", String(input?.limit ?? 1000));
  if (input?.platforms?.length) {
    params.set("platforms", input.platforms.join(","));
  }
  const format = input?.format ?? "csv";
  params.set("format", format);
  await downloadCsv(
    `/api/v1/export/ebk-business-data?${params.toString()}`,
    `ebk-business-data-${input?.range ?? `days-${input?.days ?? 180}`}-${timestampTag()}.${format}`,
  );
}

export async function exportPricingStrategyHistory(input?: {
  days?: number;
  limit?: number;
  format?: ExportFormat;
}): Promise<void> {
  const params = new URLSearchParams();
  params.set("days", String(input?.days ?? 180));
  params.set("limit", String(input?.limit ?? 100));
  const format = input?.format ?? "csv";
  params.set("format", format);
  await downloadCsv(
    `/api/v1/export/pricing-strategy-history?${params.toString()}`,
    `pricing-strategy-history-${timestampTag()}.${format}`,
  );
}

export async function downloadBlob(
  blob: Blob,
  filename: string,
): Promise<void> {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export type EbkCleanupResult = {
  status: string;
  retention_days: number;
  deleted_snapshots: number;
  deleted_runs: number;
  deleted_dataset_results: number;
};

export async function cleanupEbkBusinessData(
  retentionDays?: number,
): Promise<EbkCleanupResult> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const apiKey = getRuntimeApiKey();
  if (apiKey) {
    headers["X-Pan-Hotel-API-Key"] = apiKey;
  }
  const response = await fetch("/api/v1/export/ebk-business-data/cleanup", {
    method: "POST",
    headers,
    body: JSON.stringify(
      retentionDays === undefined ? {} : { retention_days: retentionDays },
    ),
  });
  if (!response.ok) {
    throw new Error(`清理请求失败（HTTP ${response.status}）`);
  }
  return (await response.json()) as EbkCleanupResult;
}

export type EbkRetentionConfig = {
  retention_days: number;
  source: string;
};

export async function fetchEbkRetentionConfig(): Promise<EbkRetentionConfig> {
  const headers: Record<string, string> = {};
  const apiKey = getRuntimeApiKey();
  if (apiKey) {
    headers["X-Pan-Hotel-API-Key"] = apiKey;
  }
  const response = await fetch("/api/v1/export/ebk-business-data/retention-config", { headers });
  if (!response.ok) {
    throw new Error(`读取留存配置失败（HTTP ${response.status}）`);
  }
  return (await response.json()) as EbkRetentionConfig;
}
