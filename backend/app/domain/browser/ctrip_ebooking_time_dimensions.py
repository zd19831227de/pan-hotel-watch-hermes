from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TimeDimensionSpec:
    key: str
    label: str
    selectors: tuple[str, ...]
    allow_label_fallback: bool = True
    embedded_section: bool = False


_PRIMARY_KEYS: dict[str, str] = {
    "business_outline": "daily",
    "competition_profile": "realtime",
    "flow_data": "yesterday",
    "loss_analysis": "yesterday",
}

TIME_DIMENSION_SPECS: dict[str, tuple[TimeDimensionSpec, ...]] = {
    "business_outline": (
        TimeDimensionSpec(
            key="realtime",
            embedded_section=True,
            label="实时",
            selectors=(
                "#order-realtime",
                "a[he-click='datacenter_report_marketcontainer_now']",
                ".btn:has-text('实时')",
                "text=实时",
            ),
        ),
        TimeDimensionSpec(
            key="daily",
            embedded_section=True,
            label="按日",
            selectors=(
                "a[he-click='datacenter_report_marketcontainer_day']",
                ".btn:has-text('按日')",
                "text=按日",
            ),
        ),
    ),
    "competition_profile": (
        TimeDimensionSpec(
            key="realtime",
            label="实时",
            selectors=(
                "[he-click='Management_Calendar_RealTime']",
                ".he-trip-ui-radio-button-wrapper:has([he-click='Management_Calendar_RealTime'])",
            ),
            allow_label_fallback=False,
        ),
        TimeDimensionSpec(
            key="yesterday",
            label="昨日",
            selectors=(
                ".he-trip-ui-radio-button-wrapper:has([he-click='Management_Calendar_Yesterday'])",
                "[he-click*='Management_Calendar'][he-click*='Yesterday']",
                "[he-click*='Management_Calendar'][he-click*='Day']",
            ),
            allow_label_fallback=False,
        ),
    ),
    "flow_data": (
        TimeDimensionSpec(
            key="realtime",
            label="实时",
            selectors=(
                "[he-click='datacenter_report_flowdata_now']",
                "[he-click*='FlowData_Calendar_RealTime']",
                ".he-trip-ui-radio-button-wrapper:has([he-click*='FlowData_Calendar_RealTime'])",
            ),
            allow_label_fallback=False,
        ),
        TimeDimensionSpec(
            key="yesterday",
            label="昨日",
            selectors=(
                "[he-click='datacenter_report_flowdata_yes']",
                ".he-trip-ui-radio-button-wrapper:has([he-click='FlowData_Calendar_Yesterday'])",
                "[he-click='FlowData_Calendar_Yesterday']",
            ),
            allow_label_fallback=False,
        ),
    ),
    "loss_analysis": (
        TimeDimensionSpec(
            key="realtime",
            label="实时",
            selectors=(
                "[he-click*='LossOrder_Calendar_RealTime']",
                ".he-trip-ui-radio-button-wrapper:has([he-click*='LossOrder_Calendar_RealTime'])",
            ),
            allow_label_fallback=False,
        ),
        TimeDimensionSpec(
            key="yesterday",
            label="昨日",
            selectors=(
                ".he-trip-ui-radio-button-wrapper:has([he-click='LossOrder_Calendar_Yesterday'])",
                "[he-click='LossOrder_Calendar_Yesterday']",
            ),
            allow_label_fallback=False,
        ),
    ),
}


_BUSINESS_OUTLINE_DIMENSION_METRICS: dict[str, frozenset[str]] = {
    "realtime": frozenset(
        {
            "realtime_booking_orders",
            "realtime_in_house_room_nights",
            "realtime_visitors",
            "realtime_visitor_rank",
        }
    ),
    "daily": frozenset(
        {
            "departure_sales_amount",
            "departure_room_nights",
            "transaction_rate",
            "average_selling_price",
            # 服务概览四指标在页面为昨日口径，随主（daily）维度保留，
            # 否则双维度拆分会把它们清空、简报卡服务概览为空。
            "psi_service_score",
            "hotel_review_score",
            "five_minute_response_rate",
            "hotel_favorites",
        }
    ),
}


def prepare_time_dimension_snapshot(
    *,
    dataset_id: str,
    dimension_key: str,
    parsed: dict[str, Any],
    selected: bool,
) -> dict[str, Any]:
    prepared = dict(parsed)
    prepared["inferred_current"] = False
    if not selected:
        prepared.update(
            {
                "structure_ok": False,
                "metrics": {},
                "tables": [],
                "rankings": [],
                "metric_groups": {},
                "warnings": [],
                "raw_snippets": [],
            }
        )
        notes = list(parsed.get("data_quality_notes") or [])
        if "time_dimension_control_not_found" not in notes:
            notes.append("time_dimension_control_not_found")
        prepared["data_quality_notes"] = notes
        return prepared

    metric_keys = _BUSINESS_OUTLINE_DIMENSION_METRICS.get(dimension_key)
    if dataset_id != "business_outline" or metric_keys is None:
        return prepared

    metrics = {
        key: value
        for key, value in (parsed.get("metrics") or {}).items()
        if key in metric_keys
    }
    metric_labels = {
        str(value.get("label") or "")
        for value in metrics.values()
        if isinstance(value, dict) and value.get("label")
    }
    prepared["metrics"] = metrics
    prepared["metric_groups"] = {"core": list(metrics.values())}
    prepared["tables"] = []
    prepared["rankings"] = []
    prepared["raw_snippets"] = [
        line
        for line in (parsed.get("raw_snippets") or [])
        if any(label in str(line) for label in metric_labels)
    ]
    prepared["structure_ok"] = bool(metrics)
    return prepared


def dataset_time_dimension_specs(dataset_id: str) -> tuple[TimeDimensionSpec, ...]:
    return TIME_DIMENSION_SPECS.get(dataset_id, ())


def primary_time_dimension_key(dataset_id: str) -> str | None:
    return _PRIMARY_KEYS.get(dataset_id)


def resolve_dual_dimension_status(dimension_statuses: list[str]) -> str:
    statuses = [str(item or "") for item in dimension_statuses if item]
    if not statuses:
        return "page_structure_changed"
    if any(item == "human_required" for item in statuses):
        return "human_required"
    if all(item == "success" for item in statuses):
        return "success"
    if any(item == "success" for item in statuses):
        return "partial"
    if any(item == "needs_review" for item in statuses):
        return "needs_review"
    if any(item == "filter_partial" for item in statuses):
        return "partial"
    return statuses[0]


def resolve_dataset_status(
    dataset_id: str,
    dimensions: dict[str, dict[str, Any]],
) -> str:
    """Resolve the dataset status from per-dimension statuses.

    A secondary dimension whose control is absent on the page
    (filter_partial) must not downgrade the dataset when the configured
    primary dimension read the business data completely — e.g. the
    loss-analysis page exposes 昨日 but no 实时 control. The miss stays
    recorded on that dimension's own status/data_quality_notes.
    """
    primary_key = primary_time_dimension_key(dataset_id)
    statuses = [
        str(payload.get("status") or "")
        for payload in dimensions.values()
        if isinstance(payload, dict)
    ]
    if (
        primary_key
        and str((dimensions.get(primary_key) or {}).get("status") or "") == "success"
    ):
        non_primary = [status for status in statuses if status != "success"]
        if all(status == "filter_partial" for status in non_primary):
            return "success"
    return resolve_dual_dimension_status(statuses)


def assemble_time_dimensioned_snapshot(
    *,
    dataset_id: str,
    dimensions: dict[str, dict[str, Any]],
    page_filter_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    primary_key = primary_time_dimension_key(dataset_id)
    primary = dimensions.get(primary_key or "", {}) if primary_key else {}
    if primary.get("status") != "success":
        primary = {}
    if not primary:
        # fall back to first successful dimension
        for key, payload in dimensions.items():
            if payload.get("status") == "success":
                primary = payload
                primary_key = key
                break
        if not primary and dimensions:
            primary_key, primary = next(iter(dimensions.items()))

    time_dimensions: dict[str, Any] = {}
    for key, payload in dimensions.items():
        time_dimensions[key] = {
            "label": payload.get("label") or key,
            "status": payload.get("status") or "unknown",
            "metrics": payload.get("metrics") or {},
            "tables": payload.get("tables") or [],
            "rankings": payload.get("rankings") or [],
            "updated_at": payload.get("updated_at"),
            "data_quality_notes": payload.get("data_quality_notes") or [],
            "raw_snippets": payload.get("raw_snippets") or [],
            "warnings": payload.get("warnings") or [],
            "metric_groups": payload.get("metric_groups") or {},
            "inferred_current": bool(payload.get("inferred_current")),
        }

    return {
        "time_dimensions": time_dimensions,
        "page_filter": {"time_dimensions": list(page_filter_entries)},
        "metrics": primary.get("metrics") or {},
        "tables": primary.get("tables") or [],
        "rankings": primary.get("rankings") or [],
        "metric_groups": primary.get("metric_groups") or {},
        "warnings": primary.get("warnings") or [],
        "raw_snippets": primary.get("raw_snippets") or [],
        "data_quality_notes": primary.get("data_quality_notes") or [],
        "updated_at": primary.get("updated_at"),
        "primary_time_dimension": primary_key,
        "structure_ok": any(bool(item.get("structure_ok")) for item in dimensions.values()),
        "status_hint": resolve_dataset_status(dataset_id, dimensions),
    }


def parse_ebk_time_dimension_intent(text: str) -> str | None:
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    # Chinese kept as-is; lower() only affects latin
    source = str(text or "")
    if any(token in source for token in ("实时", "此刻")):
        return "realtime"
    if "按日" in source:
        return "daily"
    if any(token in source for token in ("昨日", "昨天")):
        return "yesterday"
    return None


def project_dataset_data_for_time_dimension(
    data: dict[str, Any],
    time_dimension: str | None,
) -> dict[str, Any]:
    if not time_dimension:
        return dict(data)
    dims = data.get("time_dimensions")
    if not isinstance(dims, dict):
        return dict(data)
    key = time_dimension
    if key == "daily" and key not in dims and "yesterday" in dims:
        key = "yesterday"
    if key == "yesterday" and key not in dims and "daily" in dims:
        key = "daily"
    chosen = dims.get(key)
    if not isinstance(chosen, dict):
        projected = dict(data)
        projected["projected_time_dimension"] = time_dimension
        projected["projection_status"] = "missing_dimension"
        return projected
    projected = dict(data)
    projected["metrics"] = chosen.get("metrics") or {}
    projected["tables"] = chosen.get("tables") or []
    projected["rankings"] = chosen.get("rankings") or []
    projected["metric_groups"] = chosen.get("metric_groups") or {}
    projected["warnings"] = chosen.get("warnings") or []
    projected["updated_at"] = chosen.get("updated_at") or data.get("updated_at")
    projected["projected_time_dimension"] = key
    projected["projection_status"] = chosen.get("status") or "unknown"
    # Keep only the projected dim for answer clarity
    projected["time_dimensions"] = {key: chosen}
    return projected
