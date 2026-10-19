/** 经营 BI 日报 API 客户端：只读报表 + operator 导入/采集 + 导出下载。 */

import { apiRequest, extractFastApiDetail, getRuntimeApiKey } from "./http.ts";

export type BiDailyReportPayload = Record<string, unknown>;

export async function fetchBiDailyReport(
  shopKey: string,
  options?: { signal?: AbortSignal }
): Promise<BiDailyReportPayload> {
  return apiRequest<BiDailyReportPayload>(
    `/api/v1/bi-daily/daily-report?shop_key=${encodeURIComponent(shopKey)}`,
    { signal: options?.signal }
  );
}

export async function triggerBiCollect(): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>("/api/v1/bi-daily/collect", {
    method: "POST",
  });
}

export type BiBackfillStatus = {
  status?: string;
  shop_key?: string;
  date_from?: string;
  date_to?: string;
  summary?: string;
  facts_written?: number;
  platforms?: Record<
    string,
    {
      ok?: number;
      failed?: number;
      skipped_existing?: number;
      current_date?: string | null;
      failures?: Array<{ date?: string | null; status?: string; reason?: string }>;
    }
  >;
};

export async function startBiBackfill(input: {
  shopKey: string;
  dateFrom: string;
  dateTo: string;
}): Promise<BiBackfillStatus> {
  return apiRequest<BiBackfillStatus>("/api/v1/bi-daily/backfill-actuals", {
    method: "POST",
    body: JSON.stringify({
      shop_key: input.shopKey,
      date_from: input.dateFrom,
      date_to: input.dateTo,
    }),
  });
}

export async function fetchBiBackfillStatus(): Promise<BiBackfillStatus> {
  return apiRequest<BiBackfillStatus>("/api/v1/bi-daily/backfill-actuals/status");
}

export async function importBiBudgetForecast(input: {
  shopKey: string;
  forecastVersion: string;
  validFrom: string;
  validTo: string;
  file: File;
}): Promise<Record<string, unknown>> {
  const form = new FormData();
  form.append("shop_key", input.shopKey);
  form.append("forecast_version", input.forecastVersion);
  if (input.validFrom) form.append("valid_from", input.validFrom);
  if (input.validTo) form.append("valid_to", input.validTo);
  form.append("file", input.file);
  // multipart 不能带手写 Content-Type（浏览器需要自动生成 boundary），
  // 因此导入走原生 fetch，鉴权头与错误口径与 apiRequest 保持一致。
  const headers: Record<string, string> = {};
  const key = getRuntimeApiKey();
  if (key) headers["X-Pan-Hotel-API-Key"] = key;
  const response = await fetch("/api/v1/bi-daily/budget-forecast/import", {
    method: "POST",
    headers,
    body: form,
  });
  if (!response.ok) {
    const rawDetail = await response.text();
    const detail = extractFastApiDetail(rawDetail);
    throw new Error(detail || `导入失败：${response.status}`);
  }
  return (await response.json()) as Record<string, unknown>;
}

async function downloadExport(
  shopKey: string,
  format: "csv" | "xlsx",
  fallbackFilename: string
): Promise<void> {
  const headers: Record<string, string> = {};
  const key = getRuntimeApiKey();
  if (key) headers["X-Pan-Hotel-API-Key"] = key;
  const response = await fetch(
    `/api/v1/bi-daily/export/daily-report?shop_key=${encodeURIComponent(shopKey)}&format=${format}`,
    { headers }
  );
  if (!response.ok) {
    throw new Error(`导出失败（${response.status}），请稍后重试。`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fallbackFilename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function exportBiDailyCsv(shopKey: string): Promise<void> {
  return downloadExport(shopKey, "csv", "经营BI日报.csv");
}

export function exportBiDailyXlsx(shopKey: string): Promise<void> {
  return downloadExport(shopKey, "xlsx", "经营BI日报.xlsx");
}
