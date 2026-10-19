from __future__ import annotations

import re
from typing import Any


CURRENT_SOURCE_DEFINITIONS = {
    "ctrip_ebk": "携程 EBK",
    "ctrip_competitor": "携程竞品公开价",
    "operator_recent_activity": "最近读取与告警",
    "calendar_price": "日历价",
}

FUTURE_PROVIDER_SLOTS = [
    {
        "provider_key": "meituan_ebk",
        "provider_label": "美团 EBK",
        "status": "snapshot_backed",
        "usage_note": "通过 get_ebk_joint_analysis_context 读取已保存 snapshot；是否有数据以 joint 工具返回为准。",
    },
    {
        "provider_key": "fliggy_ebk",
        "provider_label": "飞猪 EBK",
        "status": "snapshot_backed",
        "usage_note": "通过 get_ebk_joint_analysis_context 读取已保存 snapshot；是否有数据以 joint 工具返回为准。",
    },
]

_VALUE_SPLIT_RE = re.compile(r"^([-+]?\d+(?:\.\d+)?)(.*)$")


def _operator_summary(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary")
    return operator if isinstance(operator, dict) else {}


def _operator_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _operator_summary(payload).get("overview_rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)][:12]


def _row_source_key(row: dict[str, Any], *, default: str | None = None) -> str | None:
    explicit = str(row.get("source") or row.get("source_key") or "").strip()
    if explicit:
        return explicit
    text = f"{row.get('field') or ''} {row.get('value') or ''}"
    if any(token in text for token in ("EBK", "出租", "入住", "订单", "ADR", "RevPAR", "收益", "销售", "好评", "差评")):
        return "ctrip_ebk"
    if any(token in text for token in ("竞品", "竞对", "房价", "价格", "¥", "元")):
        return "ctrip_competitor"
    return default


def _source_status(
    *,
    evidence_rows: list[dict[str, Any]],
    recent_rows: list[dict[str, Any]],
    extra_sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    counts = {key: 0 for key in CURRENT_SOURCE_DEFINITIONS}
    for row in evidence_rows:
        source_key = _row_source_key(row)
        if source_key in counts:
            counts[source_key] += 1
    for row in recent_rows:
        source_key = _row_source_key(row, default="operator_recent_activity")
        if source_key in counts:
            counts[source_key] += 1
    for key in extra_sources or []:
        if key in counts:
            counts[key] += 1
    return [
        {
            "source_key": key,
            "source_label": label,
            "status": "available" if counts[key] else "missing",
            "evidence_count": counts[key],
        }
        for key, label in CURRENT_SOURCE_DEFINITIONS.items()
    ] + [
        {
            "source_key": slot["provider_key"],
            "source_label": slot["provider_label"],
            "status": slot["status"],
            "evidence_count": 0,
            "usage_note": slot["usage_note"],
        }
        for slot in FUTURE_PROVIDER_SLOTS
    ]


def _freshness_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if "时间" in str(row.get("field") or "") or "读取" in str(row.get("field") or "")
    ][:4]


def _hypothesis_prompts(sources: list[dict[str, Any]], freshness_rows: list[dict[str, Any]]) -> list[str]:
    prompts = [
        "可以基于已证实数据提出可能原因，但必须标注为可能、倾向判断或需要验证。",
        "如果 EBK 指标和竞品价格同时异常，优先从流量、低价房型转化、价格带和促销露出解释。",
    ]
    if freshness_rows:
        prompts.append("如数据时间偏旧，先建议刷新对应后台，再做强结论。")
    missing_current = [
        str(item.get("source_label"))
        for item in sources
        if item.get("status") == "missing" and item.get("source_key") in CURRENT_SOURCE_DEFINITIONS
    ]
    if missing_current:
        prompts.append(f"当前缺少{ '、'.join(missing_current) }证据时，只能给弱判断和下一步取数建议。")
    return prompts


def _split_value_unit(raw: Any) -> tuple[str, str]:
    text = str(raw or "").strip()
    match = _VALUE_SPLIT_RE.match(text.replace("¥", "").replace(",", ""))
    if not match:
        return text, ""
    return match.group(1), match.group(2).strip()


def _captured_at_from(operator: dict[str, Any], row: dict[str, Any] | None = None) -> str | None:
    if isinstance(row, dict):
        for key in ("captured_at", "latest_captured_at", "data_captured_at"):
            value = row.get(key)
            if value:
                return str(value)
    for key in ("latest_captured_at", "captured_at", "data_captured_at"):
        value = operator.get(key)
        if value:
            return str(value)
    data_time = operator.get("data_time")
    if isinstance(data_time, dict) and data_time.get("captured_at"):
        return str(data_time.get("captured_at"))
    return None


def _freshness_state(operator: dict[str, Any], captured_at: str | None) -> str:
    freshness = operator.get("freshness")
    if isinstance(freshness, dict) and freshness.get("state"):
        return str(freshness.get("state"))
    if operator.get("data_is_stale") is True:
        return "stale"
    if captured_at:
        return "fresh"
    return "unknown"


def _make_evidence_item(
    *,
    prefix: str,
    index: int,
    source: str,
    label: str,
    value: Any,
    captured_at: str | None,
    freshness: str,
    trust: str = "trusted",
    priority: int = 50,
    unit: str | None = None,
    delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    split_value, split_unit = _split_value_unit(value)
    item = {
        "evidence_ref": f"{prefix}_{index}",
        "source": source,
        "label": str(label or "指标"),
        "value": split_value if split_value else str(value or ""),
        "unit": unit if unit is not None else split_unit,
        "captured_at": captured_at,
        "freshness": freshness,
        "trust": trust,
        "priority": int(priority),
    }
    if delta:
        item["delta"] = delta
    return item


def _rows_to_evidence(
    rows: list[dict[str, Any]],
    *,
    prefix: str,
    default_source: str,
    operator: dict[str, Any],
    base_priority: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:8], start=1):
        if not isinstance(row, dict):
            continue
        label = str(row.get("field") or row.get("label") or row.get("name") or "指标")
        if "时间" in label or "读取" in label:
            continue
        value = row.get("value") or row.get("display_value") or row.get("summary")
        if value is None or str(value).strip() == "":
            continue
        source = _row_source_key(row, default=default_source) or default_source
        captured_at = _captured_at_from(operator, row)
        items.append(
            _make_evidence_item(
                prefix=prefix,
                index=index,
                source=source,
                label=label,
                value=value,
                captured_at=captured_at,
                freshness=_freshness_state(operator, captured_at),
                priority=base_priority + index,
            )
        )
    return items


def _build_evidence_items(
    *,
    overview: dict[str, Any],
    recent_activity: dict[str, Any],
    calendar_latest: dict[str, Any] | None,
    competitor_trend: dict[str, Any] | None,
    price_change_alert: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    overview_operator = _operator_summary(overview)
    recent_operator = _operator_summary(recent_activity)
    items: list[dict[str, Any]] = []
    items.extend(
        _rows_to_evidence(
            _operator_rows(overview),
            prefix="ev_overview",
            default_source="ctrip_ebk",
            operator=overview_operator,
            base_priority=10,
        )
    )
    items.extend(
        _rows_to_evidence(
            _operator_rows(recent_activity),
            prefix="ev_recent",
            default_source="operator_recent_activity",
            operator=recent_operator,
            base_priority=40,
        )
    )
    if isinstance(calendar_latest, dict):
        calendar_operator = _operator_summary(calendar_latest)
        calendar_rows = calendar_operator.get("calendar_rows")
        if isinstance(calendar_rows, list):
            items.extend(
                _rows_to_evidence(
                    [row for row in calendar_rows if isinstance(row, dict)],
                    prefix="ev_calendar",
                    default_source="calendar_price",
                    operator=calendar_operator,
                    base_priority=30,
                )
            )
    if isinstance(competitor_trend, dict):
        trend_operator = _operator_summary(competitor_trend)
        trend_rows = trend_operator.get("trend_rows")
        if isinstance(trend_rows, list):
            items.extend(
                _rows_to_evidence(
                    [row for row in trend_rows if isinstance(row, dict)],
                    prefix="ev_trend",
                    default_source="ctrip_competitor",
                    operator=trend_operator,
                    base_priority=20,
                )
            )
    if isinstance(price_change_alert, dict):
        alert = price_change_alert.get("alert") if isinstance(price_change_alert.get("alert"), dict) else price_change_alert
        alert_operator = _operator_summary(price_change_alert)
        direction = str(alert.get("alert_direction") or alert.get("direction") or "").strip().lower()
        summary = str(alert.get("summary") or alert.get("headline") or "竞品价格变化")
        captured_at = _captured_at_from(alert_operator)
        priority = 5 if direction in {"increase", "decrease"} else 25
        items.append(
            _make_evidence_item(
                prefix="ev_alert",
                index=1,
                source="ctrip_competitor",
                label="竞品价格变化",
                value=summary,
                unit="",
                captured_at=captured_at,
                freshness=_freshness_state(alert_operator, captured_at),
                priority=priority,
                delta={"kind": "alert_direction", "value": direction} if direction else None,
            )
        )
    return sorted(items, key=lambda item: int(item.get("priority") or 100))


def _candidate_actions(*, risk_flags: list[str], missing_data: list[str], alert_direction: str) -> list[str]:
    actions: list[str] = []
    if missing_data:
        actions.append("先刷新最关键的持久化数据后再判断")
    if alert_direction == "increase":
        actions.append("竞品上行仅提醒，不做本店跟价建议")
    elif alert_direction == "decrease":
        actions.append("人工确认目标价后再走飞书审批，不直接写价")
    if risk_flags and "竞品涨价仅提醒" not in " ".join(actions):
        actions.append("核对异常提醒并决定是否现读")
    if not actions:
        actions.append("如需完整表格请说「查看明细」")
    return actions[:2]


def build_operator_analysis_context(
    *,
    chat_context: dict[str, Any],
    overview: dict[str, Any],
    recent_activity: dict[str, Any],
    calendar_latest: dict[str, Any] | None = None,
    competitor_trend: dict[str, Any] | None = None,
    price_change_alert: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolution = chat_context.get("resolution") if isinstance(chat_context.get("resolution"), dict) else {}
    overview_operator = _operator_summary(overview)
    overview_rows = _operator_rows(overview)
    recent_rows = _operator_rows(recent_activity)
    next_actions = (
        overview_operator.get("next_actions") if isinstance(overview_operator.get("next_actions"), list) else []
    )
    missing_data: list[str] = []
    if not overview_rows:
        missing_data.append("今日概览暂无可用关键数据。")
    if not recent_rows:
        missing_data.append("最近活动暂无可用摘要。")
    freshness = _freshness_rows(overview_rows)
    mobile_evidence = _build_evidence_items(
        overview=overview,
        recent_activity=recent_activity,
        calendar_latest=calendar_latest,
        competitor_trend=competitor_trend,
        price_change_alert=price_change_alert,
    )
    extra_sources = [str(item.get("source")) for item in mobile_evidence if item.get("source")]
    sources = _source_status(
        evidence_rows=overview_rows,
        recent_rows=recent_rows,
        extra_sources=extra_sources,
    )
    risk_flags: list[str] = []
    alert_direction = ""
    if isinstance(price_change_alert, dict):
        alert = price_change_alert.get("alert") if isinstance(price_change_alert.get("alert"), dict) else price_change_alert
        alert_direction = str(alert.get("alert_direction") or alert.get("direction") or "").strip().lower()
        if alert_direction == "increase":
            risk_flags.append("竞品涨价仅提醒")
    candidate_actions = _candidate_actions(
        risk_flags=risk_flags,
        missing_data=missing_data,
        alert_direction=alert_direction,
    )
    return {
        "status": "success",
        "source": "hermes_read_only_analysis_context",
        "binding_mode": chat_context.get("binding_mode"),
        "resolution": resolution,
        "response_mode": "summary",
        "mobile_evidence": mobile_evidence,
        "detail_intents": ["查看明细", "展开竞品价格"],
        "operator_summary": {
            "intro": "已整理经营分析上下文，可用于回答今日简报、原因分析和下一步建议。",
            "store_name": resolution.get("store_name") or resolution.get("store_label"),
            "analysis_sections": ["结论", "已证实数据", "可能原因", "建议动作", "数据缺口"],
            "sources": sources,
            "evidence_rows": overview_rows,
            "recent_activity_rows": recent_rows,
            "data_freshness_rows": freshness,
            "hypothesis_prompts": _hypothesis_prompts(sources, freshness),
            "recommended_next_reads": next_actions[:5],
            "missing_data": missing_data,
            "risk_flags": risk_flags,
            "candidate_actions": candidate_actions,
            "safety_boundary": "read_only",
            "mobile_evidence": mobile_evidence,
            "detail_intents": ["查看明细", "展开竞品价格"],
            "future_provider_slots": [dict(slot) for slot in FUTURE_PROVIDER_SLOTS],
            "safety_note": (
                "本上下文只读；允许经营分析和假设，但禁止编造经营数字，禁止生成可执行调价。"
            ),
        },
    }
