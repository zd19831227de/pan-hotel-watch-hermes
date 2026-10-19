/** 经营 BI 日报前端纯模型：状态口径翻译、矩阵行装配、质量面板文案。 */

export type BiMetricBlock = {
  value: number | string | null;
  status: "available" | "unavailable";
  reason: string | null;
};

export type BiCompetitorHotel = {
  hotel_id: number;
  alias: string;
  role: string;
  price: number | null;
  availability: string;
  room_rates: Array<{
    room_type: string | null;
    breakfast_kind: string;
    price: number | null;
    availability: string;
  }>;
};

export type BiForwardPriorYearRef = {
  mapped_date: string | null;
  matching_rule?: string | null;
  actual:
    | { booked_room_nights: number | null; occupancy_rate?: number; adr?: number }
    | null;
};

export type BiForwardRow = {
  stay_date: string;
  weekday: number;
  own: Record<string, BiMetricBlock>;
  prior_year?: BiForwardPriorYearRef;
  circle?: {
    status: string;
    my_occupancy_rate?: number | null;
    competitor_occupancy_rate?: number | null;
    my_booked_rooms?: number | null;
    competitor_booked_rooms?: number | null;
    occupancy_verdict?: string;
    verdict_label?: string;
    competitor_price_trend?: number | null;
    price_position?: {
      own_min_price: number;
      competitor_min_price: number;
      competitor_median_price: number | null;
      position: string;
      position_label: string;
    };
    reason?: string;
  };
  market?: {
    status: string;
    city_heat?: { value: number | null; label?: string | null };
    zone_heat?: { value: number | null; label?: string | null };
    travel_heat?: { value: number | null };
    travel_heat_last_year?: { value: number | null };
    competitor_price_trend?: number | null;
    events?: string[];
    reason?: string;
  };
  competitor: {
    status: string;
    prices: { min: number; max: number; count: number } | null;
    hotels: BiCompetitorHotel[];
    reason?: string;
    not_available_metrics?: string[];
  };
};

export type BiPerformanceRow = {
  stay_date: string;
  weekday: number;
  platforms: Array<{
    platform: string;
    booked_room_nights: number | null;
    room_revenue: number | null;
    adr: number | null;
    occupancy_rate?: number | null;
  }>;
  totals: { booked_room_nights: number | null; room_revenue: number | null };
  prior_year: {
    mapped_date: string | null;
    matching_rule: string | null;
    actual:
      | {
          booked_room_nights: number | null;
          occupancy_rate?: number | null;
          adr?: number | null;
        }
      | null;
    completion: BiMetricBlock;
    revenue_completion?: BiMetricBlock;
    adr_ratio?: BiMetricBlock;
    occupancy_delta_pp?: number | null;
  };
  budget: { version: string | null; budget_room_nights: number | null; completion: BiMetricBlock };
  forecast: { demand_forecast_room_nights: number | null; completion: BiMetricBlock };
};

export type BiWeekdayProfileRow = {
  weekday: number;
  weekday_label: string;
  current: {
    days: number;
    nights_sum: number;
    revenue_sum: number;
    occupancy_avg: number | null;
    adr_avg: number | null;
  };
  prior_year: {
    days: number;
    nights_sum: number;
    revenue_sum: number;
    occupancy_avg: number | null;
    adr_avg: number | null;
  };
  completion?: BiMetricBlock;
  revenue_completion?: BiMetricBlock;
  occupancy_delta_pp?: number | null;
  adr_ratio?: BiMetricBlock;
};

export type BiDailyReport = {
  shop_key: string;
  as_of: string;
  horizon_days: number;
  forward: BiForwardRow[];
  performance: BiPerformanceRow[];
  weekday_profile?: BiWeekdayProfileRow[];
  quality: {
    coverage: {
      dimensions?: Record<string, Record<string, unknown>>;
    };
    generated_at: string;
    metric_notes?: string[];
  };
};

const WEEKDAY_LABELS: Record<number, string> = {
  1: "周一",
  2: "周二",
  3: "周三",
  4: "周四",
  5: "周五",
  6: "周六",
  7: "周日",
};

const REASON_TEXT: Record<string, string> = {
  no_on_the_books_source: "暂无在手预订数据源",
  no_inventory_source: "暂无库存数据源",
  actual_missing: "缺当日实际数据",
  prior_year_baseline_missing: "缺去年同期数据",
  budget_baseline_missing: "未导入预算",
  forecast_baseline_missing: "未导入预测",
  budget_baseline_zero: "预算为 0，无法计算",
  forecast_baseline_zero: "预测为 0，无法计算",
};

const PLATFORM_LABELS: Record<string, string> = {
  ctrip: "携程",
  meituan: "美团",
  fliggy: "飞猪",
};

const COMPETITOR_STATUS_TEXT: Record<string, string> = {
  fresh: "竞品价格正常更新",
  stale: "竞品价格盯盘可能掉线，请检查",
  no_group: "这家店还没绑定竞品监控组，绑定后才有竞品价格",
  empty: "已绑定竞品监控组，暂未采集到竞品价格",
  unbound: "存在未绑店的竞品监控组（不计入本店）",
};

export function weekdayLabel(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? String(weekday);
}

export function shortDateLabel(stayDate: string): string {
  return stayDate.length >= 10 ? stayDate.slice(5) : stayDate;
}

export function reasonText(reason: string | null): string {
  if (!reason) return "";
  return REASON_TEXT[reason] ?? reason;
}

export function metricText(block: BiMetricBlock | undefined): string {
  if (!block) return "";
  if (block.status === "available" && block.value !== null) {
    return String(block.value);
  }
  return reasonText(block.reason) || "暂无数据";
}

export function ratePercent(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  return `${Math.round(value * 100)}%`;
}

export function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] ?? platform;
}

export function competitorStatusText(status: string): string {
  return COMPETITOR_STATUS_TEXT[status] ?? status;
}

export function ownPriceOf(row: BiForwardRow): number | null {
  const own = (row.competitor.hotels || []).find((hotel) => hotel.role === "own");
  return own ? own.price : null;
}

export type ForwardMatrixRow = {
  dateLabel: string;
  weekday: string;
  competitorPriceBand: string;
  ownPrice: string;
  ownNights: string;
  ownInventory: string;
  ownOccupancy: string;
  preSetPrice: string;
  circleVerdict: string;
  circleOccupancy: string;
  cityHeat: string;
  events: string;
  priorYearRef: string;
};

function fmtPct(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value * 100)}%`
    : "—";
}

export function forwardMatrixRows(report: BiDailyReport): ForwardMatrixRow[] {
  return (report.forward || []).map((row) => {
    const prices = row.competitor?.prices ?? null;
    const ownPrice = ownPriceOf(row);
    const nightsBlock = row.own?.booked_room_nights;
    const inventoryBlock = row.own?.available_inventory;
    const occupancyBlock = row.own?.occupancy_rate;
    const preSetBlock = row.own?.pre_set_price;
    const nightsValue =
      nightsBlock && nightsBlock.status === "available" ? metricText(nightsBlock) : "—";
    const inventoryValue = inventoryBlock?.value;
    return {
      dateLabel: shortDateLabel(row.stay_date),
      weekday: weekdayLabel(row.weekday),
      competitorPriceBand: prices ? `¥${prices.min} – ¥${prices.max}` : "暂无",
      ownPrice: ownPrice !== null ? `¥${ownPrice}` : "暂无",
      ownNights: nightsValue,
      ownInventory:
        nightsValue !== "—" && typeof inventoryValue === "number"
          ? `${nightsValue}/${inventoryValue}`
          : "—",
      ownOccupancy:
        occupancyBlock && occupancyBlock.status === "available"
          ? fmtPct(occupancyBlock.value as number | null)
          : "—",
      preSetPrice:
        preSetBlock && preSetBlock.status === "available"
          ? `¥${metricText(preSetBlock)}`
          : "—",
      circleVerdict:
        row.circle?.status === "available"
          ? row.circle.verdict_label ?? "已采集"
          : "暂无竞争圈数据",
      circleOccupancy:
        row.circle?.status === "available"
          ? `我 ${fmtPct(row.circle.my_occupancy_rate)} / 圈 ${fmtPct(
              row.circle.competitor_occupancy_rate
            )}`
          : "—",
      cityHeat:
        row.market?.status === "available"
          ? row.market.city_heat?.value != null
            ? `${row.market.city_heat.value}${
                row.market.travel_heat_last_year?.value != null
                  ? `（去年 ${row.market.travel_heat_last_year.value}）`
                  : ""
              }`
            : "—"
          : "—",
      events:
        row.market?.status === "available" && (row.market.events?.length ?? 0) > 0
          ? row.market!.events!.join("、")
          : "",
      priorYearRef: priorYearRefText(row.prior_year),
    };
  });
}

export type BiForwardPriorYearRefAlias = BiForwardPriorYearRef;

function priorYearRefText(ref: BiForwardPriorYearRef | undefined): string {
  if (!ref?.mapped_date) return "—";
  const actual = ref.actual;
  if (!actual) return ref.mapped_date;
  if (actual.occupancy_rate != null || actual.adr != null) {
    const parts: string[] = [];
    if (actual.occupancy_rate != null) parts.push(`出租率 ${fmtPct(actual.occupancy_rate)}`);
    if (actual.adr != null) parts.push(`均价 ¥${actual.adr}`);
    return `${ref.mapped_date.slice(5)}（${parts.join(" · ")}）`;
  }
  return `${ref.mapped_date.slice(5)}（间夜 ${actual.booked_room_nights ?? "—"}）`;
}

export type WeekdayProfileMatrixRow = {
  weekdayLabel: string;
  currentSummary: string;
  priorSummary: string;
  nightsCompletion: string;
  occupancyDelta: string;
  adrRatio: string;
};

export function weekdayProfileRows(report: BiDailyReport): WeekdayProfileMatrixRow[] {
  return (report.weekday_profile || []).map((row) => ({
    weekdayLabel: row.weekday_label ?? weekdayLabel(row.weekday),
    currentSummary: `${row.current.days}天 · 间夜 ${row.current.nights_sum}${
      row.current.occupancy_avg != null ? ` · 出租率 ${fmtPct(row.current.occupancy_avg)}` : ""
    }${row.current.adr_avg != null ? ` · 均价 ¥${row.current.adr_avg}` : ""}`,
    priorSummary:
      row.prior_year.days > 0
        ? `去年同星期 ${row.prior_year.days}天 · 间夜 ${row.prior_year.nights_sum}${
            row.prior_year.occupancy_avg != null
              ? ` · 出租率 ${fmtPct(row.prior_year.occupancy_avg)}`
              : ""
          }${row.prior_year.adr_avg != null ? ` · 均价 ¥${row.prior_year.adr_avg}` : ""}`
        : "缺去年同期数据",
    nightsCompletion: row.completion ? ratePercent(row.completion.value) || "—" : "—",
    occupancyDelta:
      typeof row.occupancy_delta_pp === "number"
        ? `${row.occupancy_delta_pp > 0 ? "+" : ""}${row.occupancy_delta_pp}pp`
        : "—",
    adrRatio: row.adr_ratio ? ratePercent(row.adr_ratio.value) || "—" : "—",
  }));
}

export type PerformanceMatrixRow = {
  dateLabel: string;
  weekday: string;
  nights: string;
  priorYear: string;
  budget: string;
  forecast: string;
  occupancyDelta: string;
  adrRatio: string;
};

export function performanceMatrixRows(report: BiDailyReport): PerformanceMatrixRow[] {
  return (report.performance || []).map((day) => ({
    dateLabel: shortDateLabel(day.stay_date),
    weekday: weekdayLabel(day.weekday),
    nights:
      day.totals?.booked_room_nights !== null && day.totals?.booked_room_nights !== undefined
        ? String(day.totals.booked_room_nights)
        : "—",
    // 缺数列统一「—」：缺失原因集中在表底一行说明，避免满屏「缺/未导入」。
    priorYear: day.prior_year?.completion ? ratePercent(day.prior_year.completion.value) || "—" : "—",
    budget: day.budget?.completion ? ratePercent(day.budget.completion.value) || "—" : "—",
    forecast: day.forecast?.completion ? ratePercent(day.forecast.completion.value) || "—" : "—",
    occupancyDelta:
      typeof day.prior_year?.occupancy_delta_pp === "number"
        ? `${day.prior_year.occupancy_delta_pp > 0 ? "+" : ""}${day.prior_year.occupancy_delta_pp}pp`
        : "—",
    adrRatio: day.prior_year?.adr_ratio
      ? ratePercent(day.prior_year.adr_ratio.value) || "—"
      : "—",
  }));
}

export type QualityLine = { label: string; text: string };

export function qualityLines(report: BiDailyReport): QualityLine[] {
  const dimensions = report.quality?.coverage?.dimensions ?? {};
  const competitor = dimensions.competitor_prices ?? {};
  const budget = dimensions.budget_forecast ?? {};
  const actuals = dimensions.own_actuals ?? {};
  const lines: QualityLine[] = [];
  lines.push({
    label: "竞品价格",
    text: competitorStatusText(String(competitor.status ?? "empty")),
  });
  if (competitor.last_captured_at) {
    lines[lines.length - 1].text += `（最新 ${String(competitor.last_captured_at).slice(0, 16)}）`;
  }
  lines.push({
    label: "预算与预测",
    text:
      budget.status === "imported"
        ? `已导入版本 ${String(budget.latest_version ?? "")}`
        : "未导入；导入预算文件后展示完成率",
  });
  lines.push({
    label: "本店经营实际",
    text:
      actuals.status === "covered"
        ? `最新到 ${String(actuals.latest_business_date ?? "")}`
        : "暂无历史经营数据",
  });
  const onBooks = dimensions.own_on_the_books ?? {};
  lines.push({
    label: "本店在手预订",
    text:
      onBooks.status === "covered"
        ? `携程房价房态日历已采（最新 ${String(onBooks.last_captured_at ?? "").slice(0, 16)}）`
        : "暂无数据源：未来已售间夜/剩余库存先不展示，不会当作 0 处理",
  });
  return lines;
}

export function priorYearMappingText(day: BiPerformanceRow): string {
  const prior = day.prior_year;
  if (!prior || !prior.mapped_date) {
    return "无映射";
  }
  const ruleText = prior.matching_rule === "minus_364" ? "同星期（52 周前）" : "同星期（53 周前）";
  return `${shortDateLabel(prior.mapped_date)} · ${ruleText}`;
}

export type BiBackfillPlatformState = {
  ok?: number;
  failed?: number;
  skipped_existing?: number;
  current_date?: string | null;
  failures?: Array<{ date?: string | null; status?: string; reason?: string }>;
};

export type BiBackfillStatusView = {
  status?: string;
  shop_key?: string;
  date_from?: string;
  date_to?: string;
  summary?: string;
  facts_written?: number;
  platforms?: Record<string, BiBackfillPlatformState>;
};

const BACKFILL_STATUS_TEXT: Record<string, string> = {
  idle: "尚未发起补抓",
  running: "正在补抓（浏览器逐日读取，耗时数分钟）",
  success: "补抓完成",
  partial: "补抓部分完成",
  failed: "补抓未成功",
};

const BACKFILL_FAILURE_STATUS_TEXT: Record<string, string> = {
  human_required: "需人工登录",
  not_available: "页面无该日数据",
  no_metrics: "未解析到指标",
  error: "读取失败",
  persist_error: "落库失败",
  skipped: "已跳过",
};

export function backfillStatusText(status: string | null | undefined): string {
  if (!status) return "";
  return BACKFILL_STATUS_TEXT[status] ?? status;
}

export function backfillPlatformLines(view: BiBackfillStatusView): string[] {
  const platforms = view.platforms ?? {};
  return Object.entries(platforms).map(([platform, state]) => {
    const parts = [`成功 ${state.ok ?? 0} 天`];
    if ((state.skipped_existing ?? 0) > 0) {
      parts.push(`已有 ${state.skipped_existing} 天跳过`);
    }
    parts.push(`失败 ${state.failed ?? 0} 天`);
    if (view.status === "running" && state.current_date) {
      parts.push(`正在读 ${shortDateLabel(state.current_date)}`);
    }
    return `${platformLabel(platform)}：${parts.join(" · ")}`;
  });
}

export function backfillFailureSamples(view: BiBackfillStatusView, limit = 3): string[] {
  const platforms = view.platforms ?? {};
  const samples: string[] = [];
  for (const [platform, state] of Object.entries(platforms)) {
    for (const failure of state.failures ?? []) {
      if (samples.length >= limit) return samples;
      const statusText =
        BACKFILL_FAILURE_STATUS_TEXT[failure.status ?? ""] ?? failure.status ?? "未知";
      const dateLabel = failure.date ? shortDateLabel(failure.date) : "整体";
      const reason = failure.reason ? `（${failure.reason.slice(0, 60)}）` : "";
      samples.push(`${platformLabel(platform)} ${dateLabel}：${statusText}${reason}`);
    }
  }
  return samples;
}
