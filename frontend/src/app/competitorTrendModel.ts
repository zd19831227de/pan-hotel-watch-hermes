import type { CompetitorPriceTrend } from "../services/competitorWatchApi.ts";

export type TrendChartPoint = {
  x: number;
  price: number;
  bucketAt: string;
  label: string;
  roomType: string;
};

export type TrendChartSeries = {
  id: string;
  label: string;
  hotelLabel: string;
  roomType: string;
  breakfastLabel: string;
  dateLabel: string;
  role: "own" | "competitor";
  color: string;
  roomTypes: string[];
  roomTypeChanged: boolean;
  points: TrendChartPoint[];
};

export type TrendChartModel = {
  sourceLabel: string;
  title: string;
  breakfastKind: "none" | "included" | "all";
  xLabels: string[];
  minPrice: number;
  maxPrice: number;
  axisMin: number;
  axisMax: number;
  axisInterval: number;
  series: TrendChartSeries[];
};

export type TrendChartGroups = {
  todayNoBreakfast: TrendChartModel;
  todayWithBreakfast: TrendChartModel;
  tomorrowNoBreakfast: TrendChartModel;
  tomorrowWithBreakfast: TrendChartModel;
};

const chartColors = ["#2563eb", "#dc2626", "#059669", "#9333ea", "#ea580c", "#0f766e"];

export function buildCompetitorTrendChartModel(trend: CompetitorPriceTrend): TrendChartModel {
  const axisBuckets = collectTrendBuckets(trend.series);
  return buildTrendChartModel(
    trend,
    trend.series,
    "全部价格趋势",
    "all",
    axisBuckets,
  );
}

export function buildCompetitorTrendChartGroups(trend: CompetitorPriceTrend): TrendChartGroups {
  const axisBuckets = collectTrendBuckets(trend.series);
  return {
    todayNoBreakfast: buildTrendChartModel(
      trend,
      trend.series.filter((series) => series.date_label === "today" && series.breakfast_kind === "none"),
      "今日无早价格趋势",
      "none",
      axisBuckets,
    ),
    todayWithBreakfast: buildTrendChartModel(
      trend,
      trend.series.filter((series) => series.date_label === "today" && series.breakfast_kind === "included"),
      "今日含早价格趋势",
      "included",
      axisBuckets,
    ),
    tomorrowNoBreakfast: buildTrendChartModel(
      trend,
      trend.series.filter((series) => series.date_label === "tomorrow" && series.breakfast_kind === "none"),
      "明日无早价格趋势",
      "none",
      axisBuckets,
    ),
    tomorrowWithBreakfast: buildTrendChartModel(
      trend,
      trend.series.filter((series) => series.date_label === "tomorrow" && series.breakfast_kind === "included"),
      "明日含早价格趋势",
      "included",
      axisBuckets,
    ),
  };
}

export function formatTrendTooltip(series: TrendChartSeries, point: TrendChartPoint): string {
  const lines = [
    series.hotelLabel,
    `房型：${point.roomType}`,
    `早餐：${series.breakfastLabel}`,
    `时间：${point.label}`,
    `价格：¥${point.price}`,
  ];
  if (series.roomTypeChanged) {
    lines.splice(2, 0, "趋势：房型有变化");
  }
  return lines.join("\n");
}

function buildTrendChartModel(
  trend: CompetitorPriceTrend,
  trendSeries: CompetitorPriceTrend["series"],
  title: string,
  breakfastKind: TrendChartModel["breakfastKind"],
  axisBuckets: string[] = collectTrendBuckets(trendSeries),
): TrendChartModel {
  const bucketIndex = new Map(axisBuckets.map((bucket, index) => [bucket, index]));
  const includeSeconds = shouldShowSeconds(axisBuckets);
  const allPrices: number[] = [];
  const seriesByKey = new Map<string, TrendChartSeries>();
  for (const item of trendSeries) {
    const seriesRoomType = cleanText(item.room_type) || "房型未识别";
    const breakfastText = breakfastLabel(item.breakfast_kind);
    const hotelLabel = buildHotelLabel(
      item.hotel_name,
      item.hotel_alias,
      item.hotel_role,
    );
    const displayDateLabel = dateLabel(item.date_label);
    const key = [
      item.hotel_config_id,
      item.date_label,
      item.breakfast_kind,
    ].join("|");
    let target = seriesByKey.get(key);
    if (!target) {
      target = {
        id: key,
        label: `${hotelLabel} · ${seriesRoomType} · ${displayDateLabel} · ${breakfastText}`,
        hotelLabel,
        roomType: seriesRoomType,
        breakfastLabel: breakfastText,
        dateLabel: displayDateLabel,
        role: item.hotel_role,
        color: chartColors[seriesByKey.size % chartColors.length],
        roomTypes: [],
        roomTypeChanged: Boolean(item.room_type_changed),
        points: [],
      };
      seriesByKey.set(key, target);
    }
    const declaredRoomTypes = Array.isArray(item.room_types)
      ? item.room_types.map(cleanText).filter(Boolean)
      : [];
    for (const roomType of declaredRoomTypes) {
      appendUnique(target.roomTypes, roomType);
    }
    for (const point of item.points) {
      if (typeof point.price_amount_yuan !== "number") {
        continue;
      }
      const pointRoomType = cleanText(point.room_type) || seriesRoomType;
      appendUnique(target.roomTypes, pointRoomType);
      const price = Number(point.price_amount_yuan);
      allPrices.push(price);
      const x = bucketIndex.get(point.bucket_at) ?? 0;
      const nextPoint = {
        x,
        price,
        bucketAt: point.bucket_at,
        label: formatTrendBucket(point.captured_at || point.bucket_at, { includeSeconds }),
        roomType: pointRoomType,
      };
      const existingIndex = target.points.findIndex((candidate) => candidate.x === x);
      if (existingIndex >= 0) {
        target.points[existingIndex] = nextPoint;
      } else {
        target.points.push(nextPoint);
      }
    }
  }
  const series = Array.from(seriesByKey.values())
    .map((item) => finalizeSeries(item))
    .filter((item) => item.points.length > 0);
  const minPrice = allPrices.length ? Math.min(...allPrices) : 0;
  const maxPrice = allPrices.length ? Math.max(...allPrices) : 0;
  const axis = buildPriceAxis(minPrice, maxPrice);
  return {
    sourceLabel: trend.trusted_source === "room_rate_dom_display_price" ? "可信 DOM 房价" : "可信房价",
    title,
    breakfastKind,
    xLabels: axisBuckets.map((bucket) => formatTrendBucket(bucket, { includeSeconds })),
    minPrice,
    maxPrice,
    axisMin: axis.min,
    axisMax: axis.max,
    axisInterval: axis.interval,
    series,
  };
}

function finalizeSeries(series: TrendChartSeries): TrendChartSeries {
  const roomTypes = series.roomTypes.length ? series.roomTypes : [series.roomType];
  const roomTypeChanged = series.roomTypeChanged || roomTypes.length > 1;
  const roomType = roomTypeChanged ? "房型有变化" : roomTypes[0] ?? series.roomType;
  return {
    ...series,
    label: `${series.hotelLabel} · ${roomType} · ${series.dateLabel} · ${series.breakfastLabel}`,
    roomType,
    roomTypes,
    roomTypeChanged,
    points: series.points.sort((left, right) => left.x - right.x),
  };
}

function appendUnique(values: string[], value: string): void {
  if (value && !values.includes(value)) {
    values.push(value);
  }
}

function collectTrendBuckets(trendSeries: CompetitorPriceTrend["series"]): string[] {
  return Array.from(
    new Set(
      trendSeries.flatMap((series) =>
        series.points
          .filter((point) => typeof point.price_amount_yuan === "number")
          .map((point) => point.bucket_at),
      ),
    ),
  ).sort();
}

export function buildPriceAxis(minPrice: number, maxPrice: number): { min: number; max: number; interval: number } {
  if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice) || maxPrice <= 0) {
    return { min: 0, max: 100, interval: 20 };
  }
  const low = Math.min(minPrice, maxPrice);
  const high = Math.max(minPrice, maxPrice);
  const span = high - low;
  const padding = span > 0 ? Math.max(10, span * 0.1) : Math.max(20, high * 0.08);
  const paddedMin = Math.max(0, low - padding);
  const paddedMax = high + padding;
  const interval = choosePriceAxisInterval(paddedMax - paddedMin);
  let min = Math.floor(paddedMin / interval) * interval;
  let max = Math.ceil(paddedMax / interval) * interval;
  if (max <= min) {
    max = min + interval * 4;
  }
  if (min === 0 && low > interval) {
    min = Math.max(0, Math.floor((low - padding) / interval) * interval);
  }
  return {
    min,
    max,
    interval,
  };
}

function choosePriceAxisInterval(range: number): number {
  const safeRange = Number.isFinite(range) && range > 0 ? range : 100;
  const target = safeRange / 7;
  const exponent = Math.floor(Math.log10(target));
  const magnitude = 10 ** exponent;
  const normalized = target / magnitude;
  const step =
    normalized <= 1
      ? 1
      : normalized <= 2
        ? 2
        : normalized <= 5
          ? 5
          : 10;
  return step * magnitude;
}

function formatTrendBucket(value: string, options?: { includeSeconds?: boolean }): string {
  const date = new Date(value);
  const includeSeconds = Boolean(options?.includeSeconds);
  if (Number.isNaN(date.getTime())) {
    return value.slice(5, includeSeconds ? 19 : 16).replace("T", " ");
  }
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  if (!includeSeconds) {
    return `${month}-${day} ${hour}:${minute}`;
  }
  const second = String(date.getSeconds()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}:${second}`;
}

function shouldShowSeconds(buckets: string[]): boolean {
  const minuteLabels = buckets.map((bucket) => formatTrendBucket(bucket));
  return new Set(minuteLabels).size < minuteLabels.length;
}

function breakfastLabel(value: string): string {
  if (value === "none") {
    return "无早";
  }
  if (value === "included") {
    return "含早";
  }
  return "早餐未知";
}

function dateLabel(value: string): string {
  if (value === "today") {
    return "今日";
  }
  if (value === "tomorrow") {
    return "明日";
  }
  return value || "-";
}

function cleanText(value?: string | null): string {
  return String(value ?? "").trim();
}

function buildHotelLabel(
  hotelName: string | undefined,
  hotelAlias: string,
  hotelRole: "own" | "competitor",
): string {
  const name = cleanText(hotelName) || cleanText(hotelAlias) || "未命名酒店";
  const alias = cleanText(hotelAlias);
  const aliasSuffix = alias && alias !== name && alias !== "本店" ? `（${alias}）` : "";
  const roleSuffix = hotelRole === "own" ? "（本店）" : "";
  return `${name}${aliasSuffix}${roleSuffix}`;
}
