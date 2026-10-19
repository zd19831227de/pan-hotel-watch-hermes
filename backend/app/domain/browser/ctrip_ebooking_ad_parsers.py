from __future__ import annotations

import re
from typing import Any

_NUMBER_VALUE_RE = re.compile(r"[+-]?(?:[¥￥]\s*)?\d[\d,]*(?:\.\d+)?%?")

_AD_PYRAMID_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "key": "traffic",
        "label": "流量",
        "primary": ("clicks", "点击(次)"),
        "metrics": (
            ("clicks", "点击(次)"),
            ("impressions", "曝光(次)"),
            ("avg_impression_rank", "平均曝光位次"),
            ("avg_click_rank", "平均点击位次"),
        ),
    },
    {
        "key": "spend",
        "label": "花费",
        "primary": ("total_spend_cny", "总花费(CNY)"),
        "metrics": (
            ("total_spend_cny", "总花费(CNY)"),
            ("avg_click_price", "点击均价"),
            ("cash_spend", "现金花费"),
            ("red_packet_spend", "红包花费"),
        ),
    },
    {
        "key": "revenue",
        "label": "收益",
        "primary": ("booking_amount_cny", "预订金额(CNY)"),
        "metrics": (
            ("booking_amount_cny", "预订金额(CNY)"),
            ("booking_orders", "预订订单"),
            ("booking_room_nights", "预订间夜"),
        ),
    },
    {
        "key": "effect",
        "label": "效果",
        "primary": ("roas", "广告投产比(ROAS)"),
        "metrics": (
            ("roas", "广告投产比(ROAS)"),
            ("click_rate", "点击率"),
            ("conversion_rate", "转化率"),
        ),
    },
)

_AD_PYRAMID_LABELS: dict[str, tuple[str, ...]] = {
    "clicks": ("点击(次)", "点击（次）", "点击次数"),
    "impressions": ("曝光(次)", "曝光（次）", "曝光次数"),
    "avg_impression_rank": ("平均曝光位次",),
    "avg_click_rank": ("平均点击位次",),
    "total_spend_cny": ("总花费(CNY)", "总花费（CNY）", "总花费"),
    "avg_click_price": ("点击均价",),
    "cash_spend": ("现金花费",),
    "red_packet_spend": ("红包花费",),
    "booking_amount_cny": ("预订金额(CNY)", "预订金额（CNY）", "预订金额"),
    "booking_orders": ("预订订单",),
    "booking_room_nights": ("预订间夜",),
    "roas": ("广告投产比(ROAS)", "广告投产比（ROAS）", "广告投产比", "ROAS"),
    "click_rate": ("点击率",),
    "conversion_rate": ("转化率",),
}

_AD_PYRAMID_EXPECTED_KEYS = tuple(_AD_PYRAMID_LABELS)
_AD_PYRAMID_SECTION_LABELS = tuple(str(group["label"]) for group in _AD_PYRAMID_GROUPS)


def _extract_ad_pyramid_payload(lines: list[str]) -> dict[str, Any]:
    metrics = _extract_ad_pyramid_metrics(lines)
    metric_groups = _build_ad_pyramid_metric_groups(metrics)
    table = _build_ad_pyramid_table(metric_groups)
    missing_labels = [
        _AD_PYRAMID_LABELS[key][0] for key in _AD_PYRAMID_EXPECTED_KEYS if key not in metrics
    ]
    return {
        "structure_ok": bool(metrics) and len(metrics) >= 6,
        "status_hint": None if not missing_labels else "needs_review",
        "summary": {
            "date_preset": "今日",
            "visible_metric_count": len(metrics),
            "expected_metric_count": len(_AD_PYRAMID_EXPECTED_KEYS),
            "missing_metrics": missing_labels,
        },
        "summary_metrics": metrics,
        "ad_pyramid": {
            "date_preset": "今日",
            "groups": metric_groups,
            "table": table,
        },
        "tables": [table] if table["rows"] else [],
        "data_quality_notes": (
            [f"广告金字塔数据报告缺少可确认字段：{', '.join(missing_labels)}"]
            if missing_labels
            else []
        ),
        "raw_snippets": _select_ad_pyramid_snippets(lines),
    }


def _extract_ad_pyramid_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    compact_lines = [_compact(line) for line in lines]
    _apply_group_primary_metrics(lines, compact_lines, metrics)
    for key, labels in _AD_PYRAMID_LABELS.items():
        if key in metrics:
            continue
        match = _find_ad_metric_value(lines, compact_lines, labels)
        if match is None:
            continue
        label, value, raw_label = match
        metrics[key] = {
            "key": key,
            "label": label,
            "value": value,
            "raw_label": raw_label,
        }
    return metrics


def _apply_group_primary_metrics(
    lines: list[str],
    compact_lines: list[str],
    metrics: dict[str, dict[str, Any]],
) -> None:
    for group in _AD_PYRAMID_GROUPS:
        group_label = str(group["label"])
        group_index = _find_compact_line(compact_lines, group_label)
        if group_index is None:
            continue
        primary_key, primary_label = group["primary"]
        if primary_key in metrics:
            continue
        value = _first_value_after_group_label(lines, group_index)
        if value is None:
            continue
        metrics[primary_key] = {
            "key": primary_key,
            "label": primary_label,
            "value": value,
            "raw_label": lines[group_index],
        }


def _find_ad_metric_value(
    lines: list[str],
    compact_lines: list[str],
    labels: tuple[str, ...],
) -> tuple[str, str, str] | None:
    for index, compact_line in enumerate(compact_lines):
        for label in labels:
            compact_label = _compact(label)
            if compact_line == compact_label:
                value = _next_ad_metric_value(lines, index)
                if value is not None:
                    return label, value, lines[index]
            if compact_line.startswith(compact_label):
                suffix = compact_line[len(compact_label) :]
                value = _first_number(suffix)
                if value is not None:
                    return label, value, lines[index]
            if compact_label in compact_line:
                after_label = compact_line.split(compact_label, 1)[1]
                value = _first_number(after_label)
                if value is not None:
                    return label, value, lines[index]
    return None


def _next_ad_metric_value(lines: list[str], index: int) -> str | None:
    for cursor in range(index + 1, min(len(lines), index + 5)):
        candidate = lines[cursor].strip()
        if not candidate:
            continue
        if _is_ad_metric_boundary(candidate):
            return None
        value = _first_number(candidate)
        if value is not None:
            return value
    return None


def _is_ad_metric_boundary(line: str) -> bool:
    compact = _compact(line)
    if compact in {_compact(label) for label in _AD_PYRAMID_SECTION_LABELS}:
        return True
    return any(
        compact == _compact(label)
        for labels in _AD_PYRAMID_LABELS.values()
        for label in labels
    )


def _find_compact_line(compact_lines: list[str], label: str) -> int | None:
    compact_label = _compact(label)
    for index, line in enumerate(compact_lines):
        if line == compact_label:
            return index
    return None


def _first_value_after_group_label(lines: list[str], index: int) -> str | None:
    for candidate in lines[index + 1 : index + 5]:
        value = _first_number(candidate)
        if value is not None:
            return value
    return None


def _build_ad_pyramid_metric_groups(
    metrics: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for spec in _AD_PYRAMID_GROUPS:
        metric_rows = [
            metrics[key]
            for key, _label in spec["metrics"]
            if key in metrics
        ]
        primary_key, primary_label = spec["primary"]
        primary = metrics.get(primary_key)
        groups.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "primary_metric_key": primary_key,
                "primary_label": primary_label,
                "primary_value": primary.get("value") if primary else None,
                "metrics": metric_rows,
            }
        )
    return groups


def _build_ad_pyramid_table(groups: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        metrics = group.get("metrics") if isinstance(group.get("metrics"), list) else []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            rows.append(
                {
                    "group": group.get("label"),
                    "metric_key": metric.get("key"),
                    "metric": metric.get("label"),
                    "value": metric.get("value"),
                }
            )
    return {
        "name": "ad_pyramid_today_report",
        "label": "广告营销金字塔今日数据报告",
        "columns": ["分组", "指标", "数值"],
        "rows": rows,
    }


def _select_ad_pyramid_snippets(lines: list[str]) -> list[str]:
    terms = {
        "数据概览",
        "今日",
        "过去7天",
        "过去30天",
        "自定义",
        "流量",
        "花费",
        "收益",
        "效果",
        *[labels[0] for labels in _AD_PYRAMID_LABELS.values()],
    }
    snippets = [line for line in lines if any(term in line for term in terms)]
    return snippets[:40]


def _compact(value: str) -> str:
    return re.sub(r"[\s:：?？ⓘ]+", "", value)


def _first_number(value: str) -> str | None:
    match = _NUMBER_VALUE_RE.search(value)
    return match.group(0).strip() if match else None
