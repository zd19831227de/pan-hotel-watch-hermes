export type FliggyMetricView = {
  key: string;
  label: string;
  value: string;
};

export type FliggySnapshotLike = {
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

export type FliggyEbkDashboardView = {
  status: string;
  readAt: string | null;
  summary: string | null;
  emptyText: string;
  home: {
    available: boolean;
    status: string | null;
    today: FliggyMetricView[];
    yesterday: FliggyMetricView[];
  };
  diagnose: {
    available: boolean;
    status: string | null;
    trade: FliggyMetricView[];
    guestReviewScore: string | null;
    mciIndex: string | null;
  };
  operating: {
    available: boolean;
    status: string | null;
    inHouse: FliggyMetricView[];
    booking: FliggyMetricView[];
  };
  promotion: {
    available: boolean;
    status: string | null;
    activities: Array<{ name: string; status: string }>;
  };
  successCount: number;
  totalCount: number;
};

const DATASET_KEYS = ["home_v1", "diagnose_report", "operating_data", "promotion_agent"] as const;

export function buildFliggyEbkDashboard(snapshot: FliggySnapshotLike): FliggyEbkDashboardView {
  if (!snapshot) {
    return emptyView("missing", "暂无读取。请先完成飞猪登录，再读取并保存 snapshot。");
  }

  const payload = snapshot.payload_json ?? {};
  const datasets = isRecord(payload.datasets) ? payload.datasets : {};
  const homeRaw = asDataset(datasets.home_v1);
  const diagnoseRaw = asDataset(datasets.diagnose_report);
  const operatingRaw = asDataset(datasets.operating_data);
  const promoRaw = asDataset(datasets.promotion_agent);

  const homeData = isRecord(homeRaw.data) ? homeRaw.data : {};
  const diagnoseData = isRecord(diagnoseRaw.data) ? diagnoseRaw.data : {};
  const operatingData = isRecord(operatingRaw.data) ? operatingRaw.data : {};
  const promoData = isRecord(promoRaw.data) ? promoRaw.data : {};
  const activitiesRaw = Array.isArray(promoData.joined_activities) ? promoData.joined_activities : [];

  const tabs = DATASET_KEYS.map((key) => asDataset(datasets[key]));
  const successCount = tabs.filter((item) => item.status === "success").length;

  return {
    status: String(snapshot.status || payload.status || "unknown"),
    readAt: typeof payload.read_at === "string" ? payload.read_at : snapshot.created_at ?? null,
    summary: typeof payload.summary === "string" ? payload.summary : null,
    emptyText: "暂无读取",
    home: {
      available: homeRaw.status === "success",
      status: homeRaw.status,
      today: metricsFromMap(homeData.today_data, "today"),
      yesterday: metricsFromMap(homeData.yesterday_data, "yesterday"),
    },
    diagnose: {
      available: diagnoseRaw.status === "success",
      status: diagnoseRaw.status,
      trade: metricsFromMap(diagnoseData.trade_overview, "trade"),
      guestReviewScore: displayValue(diagnoseData.guest_review_score),
      mciIndex: displayValue(diagnoseData.mci_index),
    },
    operating: {
      available: operatingRaw.status === "success",
      status: operatingRaw.status,
      inHouse: metricsFromMap(operatingData.in_house_data, "inhouse"),
      booking: metricsFromMap(operatingData.booking_data, "booking"),
    },
    promotion: {
      available: promoRaw.status === "success",
      status: promoRaw.status,
      activities: activitiesRaw
        .filter((item): item is Record<string, unknown> => isRecord(item))
        .map((item) => ({
          name: String(item.name ?? "活动"),
          status: String(item.status ?? "未知"),
        })),
    },
    successCount,
    totalCount: DATASET_KEYS.length,
  };
}

function emptyView(status: string, emptyText: string): FliggyEbkDashboardView {
  return {
    status,
    readAt: null,
    summary: null,
    emptyText,
    home: { available: false, status: null, today: [], yesterday: [] },
    diagnose: {
      available: false,
      status: null,
      trade: [],
      guestReviewScore: null,
      mciIndex: null,
    },
    operating: { available: false, status: null, inHouse: [], booking: [] },
    promotion: { available: false, status: null, activities: [] },
    successCount: 0,
    totalCount: DATASET_KEYS.length,
  };
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

function metricsFromMap(value: unknown, prefix: string): FliggyMetricView[] {
  if (!isRecord(value)) {
    return [];
  }
  return Object.entries(value).map(([label, raw], index) => ({
    key: `${prefix}-${index}-${label}`,
    label,
    value: displayValue(raw) ?? "—",
  }));
}

function displayValue(value: unknown): string | null {
  if (value == null) {
    return null;
  }
  const text = String(value).trim();
  return text || null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
