// 携程 EBK「经营数据」面板模型（2026-09-10）：
// 取代旧「数据简报（PSI 服务概览 + 点评）」——全局推送接管后
// comment_monitor 不再读取，点评两块永远为空；经营数据与 EBK 全局推送
// 携程经营卡同源同口径（business_outline / competition_profile / flow_data）。
//
// 指标口径与 backend ebk_global_push/adapters.py 一致：
// - 收益概览：预订销售额（competition_profile 实时）+ 实时预订订单 + 实时在店间夜
// - 经营对比：competition_profile 实时六项（含竞争圈排名）
// - 流量曝光：实时访客/排名/起价（flow_data 优先，business_outline 兜底）+ App 访客/转化率

export type BusinessMetric = {
  key: string;
  label: string;
  value: string;
  rank?: string;
};

export type CtripBusinessData = {
  status: "ready" | "data_required";
  readTime?: string;
  sourceLabel?: string;
  revenueOverview: BusinessMetric[];
  businessComparison: BusinessMetric[];
  trafficExposure: BusinessMetric[];
  emptyText?: string;
  // 本次读取中状态非 success 的数据集（page_structure_changed / human_required 等），
  // 面板必须显式展示，不允许「读取成功」掩盖异常。
  anomalyCount: number;
  anomalies: Array<{ datasetId: string; status: string }>;
};

export type BusinessDatasetResult = {
  dataset_id?: string | null;
  status?: string | null;
  captured_at?: string | null;
  data?: {
    dataset?: string | null;
    updated_at?: string | null;
    metrics?: Record<string, unknown> | null;
    time_dimensions?: Record<string, unknown> | null;
  } | null;
};

export type BusinessLatestSnapshot = {
  run?: {
    id?: number;
    status?: string | null;
    captured_at?: string | null;
  } | null;
  dataset_results?: BusinessDatasetResult[];
};

const COMPARISON_LABELS: Record<string, string> = {
  booking_orders: "预订订单",
  booking_sales: "预订销售额",
  in_house_room_nights: "在店间夜",
  occupancy_rate: "满房率",
  ctrip_app_visitors: "携程App访客",
  ctrip_app_conversion_rate: "携程App转化率",
};

const TRAFFIC_LABELS: Record<string, string> = {
  realtime_visitors: "实时访客",
  realtime_visitor_rank: "实时访客排名",
  realtime_starting_price: "实时起价",
  ctrip_app_visitors: "携程App访客",
  ctrip_app_conversion_rate: "携程App转化率",
};

export function buildCtripBusinessData(latest: BusinessLatestSnapshot | null): CtripBusinessData {
  const results = Array.isArray(latest?.dataset_results) ? latest!.dataset_results! : [];
  const anomalies = results
    .filter((result) => str(result.status) && str(result.status) !== "success")
    .map((result) => ({
      datasetId: str(result.dataset_id) || "unknown",
      status: str(result.status),
    }));
  const outline = findDataset(results, "business_outline");
  const profile = findDataset(results, "competition_profile");
  const flow = findDataset(results, "flow_data");

  const outlineRealtime = realtimeMetricsOf(outline);
  const profileRealtime = realtimeMetricsOf(profile);
  const flowMetrics = asRecord(flow?.data?.metrics) ?? {};

  const readTime =
    latest?.run?.captured_at ??
    outline?.captured_at ??
    profile?.captured_at ??
    flow?.captured_at ??
    undefined;

  // 收益概览：预订销售额来自 competition_profile 实时维度（与全局推送一致）。
  const revenueOverview: BusinessMetric[] = [];
  pushMetric(revenueOverview, "booking_sales", profileRealtime.booking_sales, "预订销售额");
  pushMetric(
    revenueOverview,
    "realtime_booking_orders",
    outlineRealtime.realtime_booking_orders,
    "实时预订订单",
  );
  pushMetric(
    revenueOverview,
    "realtime_in_house_room_nights",
    outlineRealtime.realtime_in_house_room_nights,
    "实时在店间夜",
  );

  // 经营对比：competition_profile 实时六项。
  const businessComparison: BusinessMetric[] = [];
  for (const [key, fallbackLabel] of Object.entries(COMPARISON_LABELS)) {
    pushMetric(businessComparison, key, profileRealtime[key], fallbackLabel);
  }

  // 流量曝光：flow_data 优先，business_outline 实时维度兜底（同现时以 flow_data 为准）。
  const trafficExposure: BusinessMetric[] = [];
  const trafficKeys = ["realtime_visitors", "realtime_visitor_rank", "realtime_starting_price"];
  for (const key of trafficKeys) {
    const metric = flowMetrics[key] ?? outlineRealtime[key];
    pushMetric(trafficExposure, key, metric, TRAFFIC_LABELS[key]);
  }
  pushMetric(trafficExposure, "ctrip_app_visitors", profileRealtime.ctrip_app_visitors);
  pushMetric(trafficExposure, "ctrip_app_conversion_rate", profileRealtime.ctrip_app_conversion_rate);

  const anyMetric = revenueOverview.length + businessComparison.length + trafficExposure.length;
  if (!anyMetric) {
    return {
      status: "data_required",
      readTime,
      revenueOverview,
      businessComparison,
      trafficExposure,
      anomalyCount: anomalies.length,
      anomalies,
      emptyText:
        "最近一次读取没有经营数据（经营概览/竞争圈/流量曝光）。" +
        "请先在「EBK 操作台 → 全局定时推送」或本页「立即刷新」完成一次读取。",
    };
  }
  return {
    status: "ready",
    readTime,
    sourceLabel: textOrUndefined(outline?.data?.updated_at),
    revenueOverview,
    businessComparison,
    trafficExposure,
    anomalyCount: anomalies.length,
    anomalies,
  };
}

function findDataset(
  results: BusinessDatasetResult[],
  datasetId: string,
): BusinessDatasetResult | null {
  return (
    results.find(
      (result) =>
        result.dataset_id === datasetId || result.data?.dataset === datasetId,
    ) ?? null
  );
}

function realtimeMetricsOf(dataset: BusinessDatasetResult | null): Record<string, unknown> {
  const dimensions = asRecord(dataset?.data?.time_dimensions);
  const realtime = asRecord(dimensions?.realtime);
  if (str(realtime?.status) !== "success") {
    return {};
  }
  return asRecord(realtime?.metrics) ?? {};
}

function pushMetric(
  target: BusinessMetric[],
  key: string,
  rawMetric: unknown,
  fallbackLabel?: string,
): void {
  const metric = asRecord(rawMetric);
  if (!metric) {
    return;
  }
  const value = textOrUndefined(metric.display_value) ?? textOrUndefined(metric.value);
  if (!value) {
    return;
  }
  target.push({
    key,
    label: textOrUndefined(metric.label) ?? fallbackLabel ?? key,
    value,
    rank: textOrUndefined(metric.competitive_rank),
  });
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function str(value: unknown): string {
  return String(value ?? "").trim();
}

function textOrUndefined(value: unknown): string | undefined {
  if (value === null || value === undefined) {
    return undefined;
  }
  const text = String(value).trim();
  return text || undefined;
}
