import { AlertTriangle, Bell, CheckCircle2, PauseCircle, PlayCircle, Save, Search } from "lucide-react";
import { useEffect, useState } from "react";

import {
  alertStatusText,
  alertTone,
  breakfastText,
  percent,
  primarySuggestion,
  signedYuan,
  strategyFormFromConfig,
  strategyPatchFromForm,
  type PricingStrategyForm,
  yuan,
} from "./priceChangeAlertModel";
import type {
  PriceChangeAlert,
  PricingStrategyConfig,
} from "../services/priceChangeAlertsApi";

export function PriceChangeAlertPanel(props: {
  strategy: PricingStrategyConfig | null;
  alert: PriceChangeAlert | null;
  busy: string | null;
  onFormChange: (form: PricingStrategyForm) => void;
  onSaveStrategy: () => void;
  onEvaluate: () => void;
  onToggleEnabled: (enabled: boolean) => void;
}) {
  const [form, setForm] = useState<PricingStrategyForm>(strategyFormFromConfig(props.strategy));
  useEffect(() => {
    setForm(strategyFormFromConfig(props.strategy));
  }, [props.strategy]);

  const suggestion = primarySuggestion(props.alert);
  const changeItem = suggestion ?? changeItemFromAlert(props.alert);
  const hasThresholdChange = (props.alert?.changed_count ?? 0) > 0;
  const tone = alertTone(props.alert?.status);
  const Icon = props.alert?.status === "no_trigger" ? CheckCircle2 : AlertTriangle;
  const writePolicyMessage =
    props.alert?.write_policy_message ??
    suggestion?.write_policy_message ??
    "账号类型未确认，默认按直连处理，仅生成调价建议，不会自动改价。";

  function updateForm(next: PricingStrategyForm) {
    setForm(next);
    props.onFormChange(next);
  }

  return (
    <div className="price-alert-grid">
      <div className={`price-alert-summary ${tone}`}>
        <div className="panel-title-row">
          <h3>价格变化提醒</h3>
          <Icon size={18} />
        </div>
        <div className="metric-grid">
          <Metric label="当前状态" value={alertStatusText(props.alert?.status)} />
          <Metric label="超过阈值" value={hasThresholdChange ? "是" : "否"} />
          <Metric label="变化项" value={String(props.alert?.changed_count ?? 0)} />
          <Metric label="建议项" value={String(props.alert?.suggestion_count ?? 0)} />
        </div>
        <p className="policy-note">{writePolicyMessage}</p>
        {changeItem ? (
          <div className="suggestion-block">
            <strong>{changeItem.hotel_name || "竞品酒店"} · {dateText(changeItem.date_label, changeItem.checkin_date)}</strong>
            <span>{changeItem.room_type || "-"} · {breakfastText(changeItem.breakfast_kind)}</span>
            <div className="suggestion-metrics">
              {suggestion ? <Metric label="本店当前价" value={yuan(suggestion.current_own_price_yuan)} /> : null}
              <Metric
                label="竞品变化"
                value={`${yuan(changeItem.competitor_previous_price_yuan)} → ${yuan(changeItem.competitor_current_price_yuan)}`}
              />
              <Metric
                label="变化幅度"
                value={`${signedYuan(changeItem.change_amount_yuan)} / ${percent(changeItem.change_percent)}`}
              />
              {suggestion ? (
                <>
                  <Metric label="建议本店降价" value={signedYuan(suggestion.recommended_adjustment_yuan)} />
                  <Metric label="建议后价格" value={yuan(suggestion.recommended_price_yuan)} />
                  <Metric label="人工关注" value={suggestion.needs_human_attention ? "是" : "否"} />
                </>
              ) : (
                <Metric label="处理方式" value="仅提醒竞品涨价，不建议本店涨价" />
              )}
            </div>
            <p>{suggestion?.trigger_reason || props.alert?.summary || "价格变化已触发。"}</p>
          </div>
        ) : (
          <p className="empty">{props.alert?.summary || "完成两次竞品价格读取后，可检测价格变化并生成规则化建议。"}</p>
        )}
      </div>

      <div className="price-alert-config">
        <div className="panel-title-row">
          <h3>提醒规则</h3>
          <Bell size={18} />
        </div>
        <div className="form-grid">
          <label>
            触发方式
            <select
              value={form.change_trigger_mode}
              onChange={(event) => updateForm({ ...form, change_trigger_mode: event.target.value as "percent" | "amount" })}
            >
              <option value="percent">按百分比</option>
              <option value="amount">按金额</option>
            </select>
          </label>
          <label>
            百分比阈值
            <input value={form.change_trigger_percent} inputMode="decimal" onChange={(event) => updateForm({ ...form, change_trigger_percent: event.target.value })} />
          </label>
          <label>
            最小变化金额
            <input value={form.min_delta_yuan} inputMode="numeric" onChange={(event) => updateForm({ ...form, min_delta_yuan: event.target.value })} />
          </label>
          <label>
            最大变化金额
            <input value={form.max_delta_yuan} inputMode="numeric" onChange={(event) => updateForm({ ...form, max_delta_yuan: event.target.value })} />
          </label>
          <label>
            建议降价按多少元取整
            <input value={form.recommendation_step_yuan} inputMode="numeric" onChange={(event) => updateForm({ ...form, recommendation_step_yuan: event.target.value })} />
            <span className="field-hint">例如填 10，系统给出的本店降价金额会按 10 元取整。</span>
          </label>
          <label>
            单次建议上限
            <input value={form.max_suggested_change_yuan} inputMode="numeric" onChange={(event) => updateForm({ ...form, max_suggested_change_yuan: event.target.value })} />
          </label>
        </div>
        <div className="toggle-row">
          <label>
            <input
              type="checkbox"
              checked={form.allow_downward_suggestion}
              onChange={(event) => updateForm({ ...form, allow_downward_suggestion: event.target.checked })}
            />
            竞对降价时给出本店降价金额
            <span className="field-hint">关闭后只提醒竞对降价，暂停自动给出降价金额。</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(event) => updateForm({ ...form, enabled: event.target.checked })}
            />
            启用提醒
          </label>
        </div>
      </div>

      <div className="price-alert-actions">
        <button
          className="secondary-button"
          type="button"
          onClick={() => props.onToggleEnabled(!form.enabled)}
          disabled={props.busy === "toggle-price-strategy"}
        >
          {form.enabled ? <PauseCircle size={16} /> : <PlayCircle size={16} />}
          {form.enabled ? "停止提醒" : "恢复提醒"}
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={props.onEvaluate}
          disabled={props.busy === "evaluate-price-change"}
        >
          <Search size={16} /> 检测价格变化
        </button>
        <button
          className="primary-button"
          type="button"
          onClick={props.onSaveStrategy}
          disabled={props.busy === "save-price-strategy"}
        >
          <Save size={16} /> 保存提醒规则
        </button>
      </div>
    </div>
  );
}

export { strategyPatchFromForm };

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

type DisplayChangeItem = {
  hotel_name?: string | null;
  date_label?: string | null;
  checkin_date?: string | null;
  room_type?: string | null;
  breakfast_kind?: string | null;
  competitor_previous_price_yuan?: number | null;
  competitor_current_price_yuan?: number | null;
  change_amount_yuan?: number | null;
  change_percent?: number | null;
};

function changeItemFromAlert(alert: PriceChangeAlert | null): DisplayChangeItem | null {
  const raw = alert?.changed_items?.[0];
  if (!raw) {
    return null;
  }
  const hotel = typeof raw.hotel === "object" && raw.hotel !== null ? raw.hotel as Record<string, unknown> : {};
  return {
    hotel_name: stringValue(raw.hotel_name) ?? stringValue(hotel.alias) ?? stringValue(hotel.hotel_name),
    date_label: stringValue(raw.date_label),
    checkin_date: stringValue(raw.checkin_date),
    room_type: stringValue(raw.room_type),
    breakfast_kind: stringValue(raw.breakfast_kind),
    competitor_previous_price_yuan: numberValue(raw.previous_price_yuan),
    competitor_current_price_yuan: numberValue(raw.current_price_yuan),
    change_amount_yuan: numberValue(raw.signed_delta_yuan),
    change_percent: numberValue(raw.delta_percent),
  };
}

function dateText(dateLabel?: string | null, checkinDate?: string | null): string {
  const label = dateLabel === "today" || dateLabel === "今日"
    ? "今日入住"
    : dateLabel === "tomorrow" || dateLabel === "明日"
      ? "明日入住"
      : "";
  if (label && checkinDate) {
    return `${label} · ${checkinDate}`;
  }
  return label || checkinDate || "-";
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
