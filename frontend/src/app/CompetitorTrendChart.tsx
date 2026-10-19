import * as echarts from "echarts/core";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { LineChart } from "echarts/charts";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";

import type { CompetitorPriceTrend } from "../services/competitorWatchApi";
import { buildEchartsOption } from "./competitorTrendEchart";
import { buildCompetitorTrendChartGroups, type TrendChartModel } from "./competitorTrendModel";

echarts.use([GridComponent, LegendComponent, TooltipComponent, LineChart, CanvasRenderer]);

export function CompetitorTrendChart({ trend }: { trend: CompetitorPriceTrend }) {
  const groups = useMemo(() => buildCompetitorTrendChartGroups(trend), [trend]);
  return (
    <div className="trend-chart-shell">
      <div className="trend-chart-meta">
        <span>可信 DOM 房价</span>
        <span>近 {trend.days} 天</span>
        <span>仅展示第一款可信房型</span>
      </div>
      <div className="trend-chart-grid">
        <TrendEChart model={groups.todayNoBreakfast} />
        <TrendEChart model={groups.todayWithBreakfast} />
        <TrendEChart model={groups.tomorrowNoBreakfast} />
        <TrendEChart model={groups.tomorrowWithBreakfast} />
      </div>
    </div>
  );
}

function TrendEChart({ model }: { model: TrendChartModel }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const option = useMemo(() => buildEchartsOption(model), [model]);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }
    const chart = echarts.init(containerRef.current);
    chart.setOption(option, true);
    const resizeObserver = new ResizeObserver(() => chart.resize());
    resizeObserver.observe(containerRef.current);
    return () => {
      resizeObserver.disconnect();
      chart.dispose();
    };
  }, [option]);

  if (!model.series.length) {
    return (
      <div className="trend-echart-pane">
        <div className="trend-echart-title">
          <h4>{model.title}</h4>
        </div>
        <p className="empty">暂无{model.title.replace("价格趋势", "")}可信价格点。</p>
      </div>
    );
  }
  return (
    <div className="trend-echart-pane">
      <div className="trend-echart-title">
        <h4>{model.title}</h4>
        <span>{model.series.length} 条曲线</span>
      </div>
      <div
        className="trend-echart"
        data-axis-interval={model.axisInterval}
        data-axis-max={model.axisMax}
        data-axis-min={model.axisMin}
        data-axis-labels={model.xLabels.join("|")}
        data-breakfast-kind={model.breakfastKind}
        data-series-count={model.series.length}
        ref={containerRef}
      />
    </div>
  );
}
