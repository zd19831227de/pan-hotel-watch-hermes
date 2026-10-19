// run-now 是串行多数据集长请求：单个数据集后端预算 60s 上限，
// 前端超时按「启用数据集数 × 单集预算 + 余量」计算，不写死统一 180s。
export const DATA_CENTER_SINGLE_DATASET_BUDGET_MS = 60_000;
export const DATA_CENTER_RUN_NOW_MARGIN_MS = 30_000;
export const DATA_CENTER_RUN_NOW_MIN_TIMEOUT_MS = 90_000;

export function dataCenterRunNowTimeoutMs(enabledDatasetCount: number): number {
  const count =
    Number.isFinite(enabledDatasetCount) && enabledDatasetCount > 0
      ? Math.floor(enabledDatasetCount)
      : 0;
  return Math.max(
    DATA_CENTER_RUN_NOW_MIN_TIMEOUT_MS,
    count * DATA_CENTER_SINGLE_DATASET_BUDGET_MS + DATA_CENTER_RUN_NOW_MARGIN_MS,
  );
}
