export type MeituanMetricView = {
  key: string;
  label: string;
  value: string;
};

export type MeituanDataCenterTabView = {
  key: string;
  label: string;
  available: boolean;
  status: string | null;
  metrics: MeituanMetricView[];
};

const MEITUAN_DATA_CENTER_TABS: Array<{ key: string; label: string }> = [
  { key: "data_center_home", label: "首页" },
  { key: "data_center_trade", label: "交易分析" },
  { key: "data_center_traffic", label: "流量分析" },
  { key: "data_center_service", label: "服务分析" },
];

export type MeituanSnapshotLike = {
  platform?: string;
  platform_account_id?: string;
  status?: string;
  created_at?: string | null;
  payload_json?: {
    platform?: string;
    platform_account_id?: string;
    read_at?: string;
    status?: string;
    summary?: string;
    datasets?: Record<string, unknown>;
  } | null;
} | null;

export type MeituanEbkDashboardView = {
  status: string;
  readAt: string | null;
  summary: string | null;
  emptyText: string;
  workbench: {
    available: boolean;
    status: string | null;
    realtime: MeituanMetricView[];
    yesterday: MeituanMetricView[];
    hosScore: string | null;
  };
  dataCenter: {
    available: boolean;
    status: string | null;
    metrics: MeituanMetricView[];
    tabs: MeituanDataCenterTabView[];
    successCount: number;
    totalCount: number;
  };
  promotion: {
    available: boolean;
    status: string | null;
    activities: Array<{
      name: string;
      status: string;
      metrics: MeituanMetricView[];
    }>;
  };
  reviews: {
    available: boolean;
    status: string | null;
    score: string | null;
    total: string | null;
    pendingReplyCount: string | null;
    situation: MeituanMetricView[];
  };
};

export function buildMeituanEbkDashboard(snapshot: MeituanSnapshotLike): MeituanEbkDashboardView {
  if (!snapshot) {
    return emptyView("missing", "暂无读取。请先完成美团登录，再读取并保存 snapshot。");
  }

  const payload = snapshot.payload_json ?? {};
  const datasets = isRecord(payload.datasets) ? payload.datasets : {};
  const workbenchRaw = asDataset(datasets.workbench_home);
  const promoRaw = asDataset(datasets.promotion_coupon);
  const reviewRaw = asDataset(datasets.comment_manage_home);

  const workbenchData = isRecord(workbenchRaw.data) ? workbenchRaw.data : {};
  const dataCenterTabs = buildDataCenterTabs(datasets);
  const dataCenterHome = dataCenterTabs[0] ?? null;
  const promoData = isRecord(promoRaw.data) ? promoRaw.data : {};
  const reviewData = isRecord(reviewRaw.data) ? reviewRaw.data : {};

  const activitiesRaw = Array.isArray(promoData.coupon_activities) ? promoData.coupon_activities : [];
  const hos = isRecord(workbenchData.hos_score) ? workbenchData.hos_score : {};

  return {
    status: String(snapshot.status || payload.status || "unknown"),
    readAt: typeof payload.read_at === "string" ? payload.read_at : snapshot.created_at ?? null,
    summary: typeof payload.summary === "string" ? payload.summary : null,
    emptyText: "暂无读取",
    workbench: {
      available: workbenchRaw.status === "success",
      status: workbenchRaw.status,
      realtime: metricsFromMap(workbenchData.realtime_data, "realtime"),
      yesterday: metricsFromMap(workbenchData.yesterday_data, "yesterday"),
      hosScore: displayValue(hos.value ?? hos.score),
    },
    dataCenter: {
      available: dataCenterTabs.some((tab) => tab.available),
      status: aggregateStatus(dataCenterTabs.map((tab) => tab.status)),
      metrics: dataCenterHome?.metrics ?? [],
      tabs: dataCenterTabs,
      successCount: dataCenterTabs.filter((tab) => tab.available).length,
      totalCount: dataCenterTabs.length,
    },
    promotion: {
      available: promoRaw.status === "success",
      status: promoRaw.status,
      activities: activitiesRaw
        .filter((item): item is Record<string, unknown> => isRecord(item))
        .map((item, index) => ({
          name: String(item.activity_name ?? `活动 ${index + 1}`),
          status: String(item.status ?? "未知"),
          metrics: metricsFromMap(item.activity_metrics, `promo-${index}`),
        })),
    },
    reviews: {
      available: reviewRaw.status === "success",
      status: reviewRaw.status,
      score: displayValue(reviewData.review_score),
      total: displayValue(reviewData.review_total),
      pendingReplyCount: displayValue(reviewData.pending_reply_count),
      situation: reviewSituationMetricsFromData(reviewData),
    },
  };
}

function emptyView(status: string, emptyText: string): MeituanEbkDashboardView {
  return {
    status,
    readAt: null,
    summary: null,
    emptyText,
    workbench: { available: false, status: null, realtime: [], yesterday: [], hosScore: null },
    dataCenter: {
      available: false,
      status: null,
      metrics: [],
      tabs: buildDataCenterTabs({}),
      successCount: 0,
      totalCount: MEITUAN_DATA_CENTER_TABS.length,
    },
    promotion: { available: false, status: null, activities: [] },
    reviews: {
      available: false,
      status: null,
      score: null,
      total: null,
      pendingReplyCount: null,
      situation: [],
    },
  };
}

function buildDataCenterTabs(datasets: Record<string, unknown>): MeituanDataCenterTabView[] {
  return MEITUAN_DATA_CENTER_TABS.map((tab) => {
    const raw = asDataset(datasets[tab.key]);
    const data = isRecord(raw.data) ? raw.data : {};
    return {
      key: tab.key,
      label: tab.label,
      available: raw.status === "success",
      status: raw.status,
      metrics: dataCenterMetricsFromData(data, tab.key),
    };
  });
}

function dataCenterMetricsFromData(
  data: Record<string, unknown>,
  datasetKey: string,
): MeituanMetricView[] {
  if (datasetKey === "data_center_home") {
    return metricsFromMap(firstRecord(data.basic_operation_metrics, data.metrics), datasetKey);
  }
  if (datasetKey === "data_center_trade") {
    return metricsFromMap(firstRecord(data.business_performance, data.metrics, data.basic_operation_metrics), datasetKey);
  }
  if (datasetKey === "data_center_traffic") {
    return metricsFromMap(mergeRecords(data.traffic_trend, data.loss_analysis, data.metrics), datasetKey);
  }
  if (datasetKey === "data_center_service") {
    return metricsFromMap(firstRecord(data.service_metrics, data.metrics, data.basic_operation_metrics), datasetKey);
  }
  return metricsFromMap(firstRecord(data.basic_operation_metrics, data.metrics), datasetKey);
}

function reviewSituationMetricsFromData(data: Record<string, unknown>): MeituanMetricView[] {
  const metrics: MeituanMetricView[] = [];
  metrics.push(...metricsFromMap(data.review_status, "review-status"));
  metrics.push(...metricsFromMap(data.review_dimensions, "review-dimensions"));

  if (isRecord(data.review_periods)) {
    Object.entries(data.review_periods).forEach(([periodLabel, periodMetrics]) => {
      metrics.push(...metricsFromMap(periodMetrics, `review-period-${periodLabel}`, periodLabel));
    });
  }

  metrics.push(...metricsFromMap(data.review_filters, "review-filters"));
  return metrics;
}

function aggregateStatus(statuses: Array<string | null>): string | null {
  const present = statuses.filter((status): status is string => Boolean(status));
  if (!present.length) {
    return null;
  }
  if (present.every((status) => status === "success")) {
    return "success";
  }
  if (present.some((status) => status === "success")) {
    return "partial";
  }
  return present[0];
}

function asDataset(value: unknown): { status: string | null; data: unknown } {
  if (!isRecord(value)) {
    return { status: null, data: null };
  }
  return {
    status: typeof value.status === "string" ? value.status : null,
    data: value.data,
  };
}

function metricsFromMap(
  value: unknown,
  prefix: string,
  labelPrefix?: string,
): MeituanMetricView[] {
  if (!isRecord(value)) {
    return [];
  }
  return Object.entries(value).map(([label, raw], index) => {
    const nested = isRecord(raw) ? raw : null;
    return {
      key: `${prefix}-${index}-${label}`,
      label: labelPrefix ? `${labelPrefix}·${label}` : label,
      value: displayValue(nested?.value ?? raw) ?? "—",
    };
  });
}

function firstRecord(...values: unknown[]): Record<string, unknown> | null {
  return values.find((value): value is Record<string, unknown> => isRecord(value)) ?? null;
}

function mergeRecords(...values: unknown[]): Record<string, unknown> | null {
  const merged: Record<string, unknown> = {};
  let hasValues = false;
  values.forEach((value) => {
    if (!isRecord(value)) {
      return;
    }
    Object.assign(merged, value);
    hasValues = true;
  });
  return hasValues ? merged : null;
}

function displayValue(value: unknown): string | null {
  if (value == null || value === "") {
    return null;
  }
  return String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
