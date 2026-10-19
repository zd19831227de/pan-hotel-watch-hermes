import type { EChartsOption, TooltipComponentFormatterCallbackParams } from "echarts";

import {
  formatTrendTooltip,
  type TrendChartModel,
  type TrendChartPoint,
  type TrendChartSeries,
} from "./competitorTrendModel.ts";

export type EchartDataPoint = {
  value: number;
  seriesMeta: TrendChartSeries;
  point: TrendChartPoint;
};

export function buildEchartsOption(model: TrendChartModel): EChartsOption {
  return {
    animationDuration: 260,
    color: model.series.map((series) => series.color),
    grid: { left: 48, right: 20, top: 60, bottom: 54, containLabel: true },
    legend: {
      top: 8,
      left: 8,
      type: "scroll",
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: "#263348", fontSize: 12 },
      formatter: (name: string) => shortLegendName(name),
    },
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: "#172033",
      borderColor: "#172033",
      textStyle: { color: "#fff", fontSize: 12 },
      formatter: (params: TooltipComponentFormatterCallbackParams) => {
        const point = Array.isArray(params) ? params[0] : params;
        const data = point?.data as EchartDataPoint | undefined;
        if (!data) {
          return "";
        }
        return toTooltipHtml(formatTrendTooltip(data.seriesMeta, data.point));
      },
    },
    xAxis: {
      type: "category",
      data: model.xLabels,
      boundaryGap: false,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#cbd5e1" } },
      axisLabel: { color: "#647184", fontSize: 11 },
    },
    yAxis: {
      type: "value",
      min: model.axisMin,
      max: model.axisMax,
      interval: model.axisInterval,
      axisLabel: {
        color: "#647184",
        formatter: (value: number) => `¥${value}`,
      },
      splitLine: { lineStyle: { color: "#e5eaf0" } },
    },
    series: model.series.map((series) => ({
      name: series.label,
      type: "line",
      smooth: false,
      connectNulls: true,
      symbol: "circle",
      symbolSize: 8,
      lineStyle: { width: 2.5 },
      label: {
        show: true,
        formatter: (params: { data?: unknown }) => {
          const data = asEchartDataPoint(params.data);
          return data ? `¥${data.value}` : "";
        },
        color: "#172033",
        fontSize: 11,
      },
      emphasis: { focus: "series" },
      data: toEchartData(series, model),
    })),
  };
}

export function toEchartData(series: TrendChartSeries, model: TrendChartModel): Array<EchartDataPoint | null> {
  const byX = new Map(series.points.map((point) => [point.x, point]));
  return model.xLabels.map((_, index) => {
    const point = byX.get(index);
    if (!point) {
      return null;
    }
    return {
      value: point.price,
      seriesMeta: series,
      point,
    };
  });
}

function asEchartDataPoint(value: unknown): EchartDataPoint | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const candidate = value as Partial<EchartDataPoint>;
  return typeof candidate.value === "number" && candidate.seriesMeta && candidate.point
    ? (candidate as EchartDataPoint)
    : null;
}

function toTooltipHtml(value: string): string {
  return value
    .split("\n")
    .map((line) =>
      line
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;"),
    )
    .join("<br/>");
}

function shortLegendName(value: string): string {
  const parts = value.split(" · ");
  const hotel = parts[0] ?? value;
  const room = parts[1] ?? "";
  const date = parts[2] ?? "";
  const label = [hotel, room, date].filter(Boolean).join(" · ");
  return label.length > 32 ? `${label.slice(0, 31)}…` : label;
}
