"""Ctrip EBK dataset parsers ? extracted from ctrip_ebooking_parsers.py.

Pure functions for parsing EBK data-center datasets: metrics, rankings, tables.
No browser actions, no side effects.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.browser.ctrip_common import _dedupe_keep_order
from app.domain.browser.ctrip_ebooking_parsers import (
    _BUSINESS_VALUE_RE,
    _CTRIP_DATASETS,
    _DATASET_METRIC_LABELS,
    _DATASET_SECTION_STOP_LINES,
    _GENERIC_DATASET_LABELS,
    _HIGHLIGHT_KEYWORDS,
    _clean_dataset_lines,
    _CtripDatasetDefinition,
    _extract_update_time,
    _extract_value_from_metric_line,
    _is_action_or_navigation_line,
    _is_metric_or_section_label,
    _line_matches_metric_label,
    _next_competitive_rank,
    _next_value_line,
    dataset_expected_terms,
)


def _is_numeric_line(value: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d[\d,]*(?:\.\d+)?%?|[+-]?\d+\s*/\s*\d+", value.strip()))

def _slugify_metric_key(value: str) -> str:
    mapped = {
        "销售排名": "sales",
        "流量排名": "traffic",
        "服务排名": "service",
        "预订订单量排名": "booking_order_rank",
        "预订销售额排名": "booking_sales_rank",
        "在店间夜排名": "in_house_room_nights_rank",
        "出租率排名": "occupancy_rate_rank",
        "APP详情页访客量排名": "app_detail_visitor_rank",
        "APP详情页转化率排名": "app_detail_conversion_rank",
        "PSI分排名": "psi_rank",
        "携程点评分": "ctrip_review_rank",
        "去哪儿点评分": "qunar_review_rank",
        "同程点评分": "ly_review_rank",
        "智行点评分": "zx_review_rank",
    }
    normalized = value
    for source, target in mapped.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_").lower()
    return normalized or "metric"

def _extract_dataset_warning_lines(lines: list[str]) -> list[str]:
    warning_terms = (
        "流失",
        "异常",
        "下降",
        "低于",
        "不足",
        "风险",
        "待处理",
        "发现同行",
        "发现同街",
    )
    return [line for line in lines if any(term in line for term in warning_terms)][:20]



def _select_dataset_snippets(lines, expected_terms):
    snippets = [
        line
        for line in lines
        if any(term in line for term in expected_terms)
        or any(keyword in line for keyword in _HIGHLIGHT_KEYWORDS)
    ]
    if snippets:
        return _dedupe_keep_order(snippets)
    return lines[:24]



def _extract_specialized_dataset_payload(
    *,
    dataset_id: str,
    lines: list[str],
    metrics: dict[str, dict[str, Any]],
    tables: list[dict[str, Any]],
    raw_snippets: list[str],
) -> dict[str, Any]:
    from app.domain.browser.ctrip_ebooking_promotion_parsers import (
        _extract_promotion_effect_payload,
        _extract_promotion_registered_payload,
    )
    if dataset_id == "promotion_registered":
        return _extract_promotion_registered_payload(lines, raw_snippets=raw_snippets)
    if dataset_id == "promotion_effect":
        return _extract_promotion_effect_payload(lines, metrics=metrics, tables=tables)
    if dataset_id == "ad_pyramid":
        from app.domain.browser.ctrip_ebooking_ad_parsers import _extract_ad_pyramid_payload

        return _extract_ad_pyramid_payload(lines)
    return {}

def _resolve_dataset_definition(dataset_id: str) -> _CtripDatasetDefinition:
    normalized = dataset_id.strip().lower()
    try:
        return _CTRIP_DATASETS[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(_CTRIP_DATASETS))
        message = f"Unsupported Ctrip dataset_id: {dataset_id}. Supported: {supported}"
        raise KeyError(message) from exc

def _extract_dataset_snapshot(
    *,
    dataset: _CtripDatasetDefinition,
    body_text: str,
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    lines = _clean_dataset_lines(body_text)
    expected_terms = dataset_expected_terms(dataset)
    matched_terms = [term for term in expected_terms if any(term in line for line in lines)]
    metrics = _extract_dataset_metrics(lines, dataset.dataset_id)
    tables = _extract_dataset_tables(lines, dataset.dataset_id)
    rankings = _extract_dataset_rankings(lines)
    warnings = _extract_dataset_warning_lines(lines)
    raw_snippets = _select_dataset_snippets(lines, expected_terms)
    specialized = _extract_specialized_dataset_payload(
        dataset_id=dataset.dataset_id,
        lines=lines,
        metrics=metrics,
        tables=tables,
        raw_snippets=raw_snippets,
    )
    structure_ok = bool(matched_terms) and (bool(metrics) or bool(tables) or bool(rankings))
    if specialized.get("structure_ok") is not None:
        structure_ok = structure_ok or bool(specialized.get("structure_ok"))
    data_quality_notes = specialized.pop("data_quality_notes", [])
    status_hint = specialized.pop("status_hint", None)
    specialized.pop("structure_ok", None)
    payload = {
        "structure_ok": structure_ok,
        "status_hint": status_hint,
        "matched_expected_terms": matched_terms,
        "updated_at": _extract_update_time(lines),
        "metrics": metrics,
        "metric_groups": {
            "core": list(metrics.values())[:16],
            "rankings": rankings[:12],
            "warnings": warnings[:12],
        },
        "tables": tables,
        "rankings": rankings[:20],
        "warnings": warnings[:20],
        "raw_snippets": raw_snippets[:30],
        "data_quality_notes": data_quality_notes,
        "filters": filters or {},
    }
    payload.update(specialized)
    return payload


def _extract_comment_monitor_snapshot(
    *,
    body_text: str,
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    from app.domain.browser.ctrip_ebooking_comment_parsers import (
        _build_comment_daily_scores,
        _comment_daily_scores_complete,
        _extract_comment_dimension_scores,
        _extract_comment_overall_rating,
        _extract_comment_score_rows,
        _extract_comment_total_count,
        _select_comment_snippets,
    )
    lines = _clean_dataset_lines(body_text)
    total_comment_count = _extract_comment_total_count(lines)
    overall_rating = _extract_comment_overall_rating(lines)
    dimension_scores = _extract_comment_dimension_scores(lines)
    comment_scores = _extract_comment_score_rows(lines)
    daily_scores = _build_comment_daily_scores(comment_scores)
    raw_snippets = _select_comment_snippets(lines)
    structure_ok = (
        total_comment_count is not None
        or overall_rating.get("score") is not None
        or bool(comment_scores)
    )
    daily_scores_complete = _comment_daily_scores_complete(
        total_comment_count=total_comment_count,
        comment_scores=comment_scores,
    )
    return {
        "structure_ok": structure_ok,
        "total_comment_count": total_comment_count,
        "overall_rating": overall_rating,
        "dimension_scores": dimension_scores,
        "comment_scores": comment_scores,
        "daily_scores": daily_scores,
        "daily_scores_complete": daily_scores_complete,
        "metrics": {
            "total_comment_count": total_comment_count,
            "overall_score": overall_rating.get("score"),
            "daily_score_group_count": len(daily_scores),
            "parsed_comment_score_count": len(comment_scores),
        },
        "raw_snippets": raw_snippets,
        "filters": filters or {},
    }


def _build_monitor_snapshot(dataset_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    datasets = {payload["dataset_id"]: payload.get("data", {}) for payload in dataset_payloads}
    return {
        "revenue_overview": _monitor_revenue_overview(datasets.get("business_outline", {})),
        "sales_data": _monitor_sales_data(datasets.get("beneficial_data", {})),
        "flow_data": _monitor_flow_data(datasets.get("flow_data", {})),
        "competition_profile": _monitor_competition_profile(
            datasets.get("competition_profile", {})
        ),
        "loss_analysis": _monitor_loss_analysis(datasets.get("loss_analysis", {})),
        "sales_ranking": _monitor_sales_ranking(datasets.get("competition_list", {})),
    }

def _monitor_revenue_overview(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "updated_at": dataset.get("updated_at"),
        "realtime": [
            _monitor_metric(metrics, "realtime_booking_orders"),
            _monitor_metric(metrics, "realtime_in_house_room_nights"),
        ],
        "yesterday": [
            _monitor_metric(metrics, "departure_sales_amount"),
            _monitor_metric(metrics, "departure_room_nights"),
            _monitor_metric(metrics, "transaction_rate"),
            _monitor_metric(metrics, "average_selling_price"),
        ],
    }

def _monitor_sales_data(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "booking": [
            _monitor_metric(metrics, "booking_orders"),
            _monitor_metric(metrics, "room_nights"),
            _monitor_metric(metrics, "sales_amount"),
        ],
        "in_house": [
            _monitor_metric(metrics, "in_house_room_nights"),
            _monitor_metric(metrics, "tightness"),
            _monitor_metric(metrics, "occupancy_rate"),
        ],
        "departure": [
            _monitor_metric(metrics, "departure_room_nights"),
            _monitor_metric(metrics, "departure_sales_amount"),
            _monitor_metric(metrics, "transaction_rate"),
            _monitor_metric(metrics, "average_selling_price"),
        ],
        "warnings": dataset.get("warnings", [])[:4],
    }

def _monitor_flow_data(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "overview": [
            _monitor_metric(metrics, "visitors"),
            _monitor_metric(metrics, "conversion_rate"),
        ]
    }

def _monitor_competition_profile(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "business_compare": [
            _monitor_metric(metrics, "booking_orders"),
            _monitor_metric(metrics, "booking_sales"),
            _monitor_metric(metrics, "in_house_room_nights"),
            _monitor_metric(metrics, "occupancy_rate"),
            _monitor_metric(metrics, "ctrip_app_visitors"),
            _monitor_metric(metrics, "ctrip_app_conversion_rate"),
        ]
    }

def _monitor_loss_analysis(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    tables = dataset.get("tables", [])
    top_rows = tables[0].get("rows", [])[:5] if tables else []
    return {
        "summary": [
            _monitor_metric(metrics, "lost_orders"),
            _monitor_metric(metrics, "lost_room_nights"),
            _monitor_metric(metrics, "lost_order_amount"),
        ],
        "warnings": dataset.get("warnings", [])[:4],
        "top_lost_hotels": top_rows,
    }

def _monitor_sales_ranking(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "my_hotel": [
            _monitor_metric(metrics, "sales_ranking_booking_order_rank"),
            _monitor_metric(metrics, "sales_ranking_booking_sales_rank"),
            _monitor_metric(metrics, "sales_ranking_in_house_room_nights_rank"),
            _monitor_metric(metrics, "sales_ranking_occupancy_rate_rank"),
        ],
        "warnings": dataset.get("warnings", [])[:4],
    }

def _monitor_promotion_effect(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "overview": [
            _monitor_metric(metrics, "participated_promotions"),
            _monitor_metric(metrics, "promotion_room_nights"),
            _monitor_metric(metrics, "promotion_room_night_share"),
            _monitor_metric(metrics, "average_daily_app_visitors"),
        ],
        "warnings": dataset.get("warnings", [])[:4],
        "tables": dataset.get("tables", [])[:2],
    }

def _monitor_metric(metrics: dict[str, Any], key: str) -> dict[str, Any]:
    metric = metrics.get(key, {})
    if not isinstance(metric, dict):
        metric = {}
    return {
        "key": key,
        "label": metric.get("label") or key,
        "value": metric.get("value"),
        "last_week_same_period": metric.get("last_week_same_period"),
        "week_over_week": metric.get("week_over_week"),
        "change": metric.get("change"),
        "competition_average": metric.get("competition_average"),
        "competitive_rank": metric.get("competitive_rank"),
        "competitive_rank_change": metric.get("competitive_rank_change"),
        "last_year_same_period": metric.get("last_year_same_period"),
        "year_over_year": metric.get("year_over_year"),
        "section": metric.get("section"),
        "denominator": metric.get("denominator"),
        "denominator_label": metric.get("denominator_label"),
        "display_value": metric.get("display_value"),
        "related_value": metric.get("related_value"),
        "related_label": metric.get("related_label"),
        "recommendation": metric.get("recommendation"),
    }

def _extract_dataset_metrics(lines: list[str], dataset_id: str) -> dict[str, dict[str, Any]]:
    if dataset_id == "business_outline":
        return _extract_business_outline_dataset_metrics(lines)
    if dataset_id == "beneficial_data":
        return _extract_beneficial_data_dataset_metrics(lines)
    if dataset_id == "flow_data":
        return _extract_flow_data_dataset_metrics(lines)
    if dataset_id == "loss_analysis":
        return _extract_loss_analysis_dataset_metrics(lines)
    if dataset_id == "competition_profile":
        return _extract_competition_profile_dataset_metrics(lines)
    if dataset_id == "competition_list":
        return _extract_competition_list_dataset_metrics(lines)
    if dataset_id == "promotion_effect":
        return _extract_promotion_effect_dataset_metrics(lines)
    if dataset_id == "ad_pyramid":
        from app.domain.browser.ctrip_ebooking_ad_parsers import _extract_ad_pyramid_payload

        payload = _extract_ad_pyramid_payload(lines)
        metrics = payload.get("summary_metrics")
        return metrics if isinstance(metrics, dict) else {}
    labels = _DATASET_METRIC_LABELS.get(dataset_id, _GENERIC_DATASET_LABELS)
    metrics: dict[str, dict[str, Any]] = {}
    for index, line in enumerate(lines):
        for key, label in labels.items():
            if not _line_matches_metric_label(line, label):
                continue
            value = _extract_value_from_metric_line(line, label) or _next_value_line(lines, index)
            if not value:
                continue
            metric = {
                "key": key,
                "label": label,
                "value": value,
                "raw_label": line,
            }
            rank = _next_competitive_rank(lines, index)
            if rank:
                metric["competitive_rank"] = rank
            comparison = _next_comparison_text(lines, index)
            if comparison:
                metric["comparison"] = comparison
            metrics.setdefault(key, metric)
    return metrics

def _extract_business_outline_dataset_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    labels = {
        "realtime_booking_orders": "实时预订订单",
        "realtime_in_house_room_nights": "实时在店间夜",
        "departure_sales_amount": "离店销售额",
        "departure_room_nights": "离店间夜量",
        "transaction_rate": "成交率",
        "average_selling_price": "平均卖价",
        "realtime_visitors": "实时访客量",
        "realtime_visitor_rank": "实时排名",
        "psi_service_score": "PSI服务质量分",
        "hotel_review_score": "酒店点评分",
        "five_minute_response_rate": "5分钟回复率",
        "hotel_favorites": "酒店收藏数",
    }
    return _extract_ordered_metric_cards(lines, labels)


def _extract_business_outline_app_funnel_metrics(
    payload: Any,
) -> dict[str, dict[str, Any]]:
    """Map the outline page's yesterday funnel API to typed metric records."""

    if not isinstance(payload, list):
        return {}
    rows = [row for row in payload if isinstance(row, dict)]
    hotel = next(
        (row for row in rows if _funnel_hotel_id(row.get("hotelId")) > 0),
        None,
    )
    benchmark = next(
        (row for row in rows if _funnel_hotel_id(row.get("hotelId")) == -1),
        None,
    )
    if hotel is None or benchmark is None:
        return {}
    hotel_date = str(hotel.get("date") or "").strip()
    benchmark_date = str(benchmark.get("date") or "").strip()
    if hotel_date and benchmark_date and hotel_date != benchmark_date:
        return {}

    specs = (
        ("list_exposure", "列表页曝光量", "listExposure"),
        ("app_detail_visitors", "详情页访客量", "detailExposure"),
        ("order_page_visitors", "订单页访客量", "orderFillingNum"),
        ("order_submitters", "订单提交人数", "orderSubmitNum"),
    )
    metrics: dict[str, dict[str, Any]] = {}
    for key, label, source_key in specs:
        value = _funnel_integer(hotel.get(source_key))
        average = _funnel_integer(benchmark.get(source_key))
        if value is None or average is None:
            continue
        metrics[key] = {
            "key": key,
            "label": label,
            "value": value,
            "competition_average": average,
            "section": "APP流量转化（昨日）",
            "period": hotel_date or benchmark_date,
            "source_page": "business_outline",
        }
    return metrics


def _funnel_hotel_id(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _funnel_integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)

def _extract_beneficial_data_dataset_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    metrics.update(
        _extract_section_metric_cards(
            lines,
            section_label="预订",
            stop_labels=("在店", "离店"),
            labels={
                "booking_orders": "预订订单数",
                "room_nights": "间夜量",
                "sales_amount": "销售额",
            },
        )
    )
    metrics.update(
        _extract_section_metric_cards(
            lines,
            section_label="在店",
            stop_labels=("离店",),
            labels={
                "in_house_room_nights": "间夜量",
                "tightness": "紧张度",
                "occupancy_rate": "出租率",
            },
        )
    )
    metrics.update(
        _extract_section_metric_cards(
            lines,
            section_label="离店",
            stop_labels=("隐私政策规则中心",),
            labels={
                "departure_room_nights": "间夜量",
                "departure_sales_amount": "销售额",
                "transaction_rate": "成交率",
                "average_selling_price": "平均卖价",
            },
        )
    )
    if not metrics:
        metrics = _extract_ordered_metric_cards(lines, _DATASET_METRIC_LABELS["beneficial_data"])
    return metrics

_MONTHLY_METRIC_SPECS = {
    "monthly_departure_room_nights": "间夜量",
    "monthly_departure_sales_amount": "销售额",
    "monthly_transaction_rate": "成交率",
    "monthly_average_selling_price": "平均卖价",
}
_MONTHLY_PERIOD_RE = re.compile(r"\d{4}年\d{1,2}月")


def _extract_beneficial_monthly_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    """解析销售数据页「离店·按月」区块：本月值 + 页面原文的去年同期/去年同比。

    只认紧邻「去年 同期」上下文的卡片，避免误读默认按日视图的同名指标。"""
    metrics: dict[str, dict[str, Any]] = {}
    stop_lines = (*_MONTHLY_METRIC_SPECS.values(), "下载数据", "隐私政策规则中心")
    for index, line in enumerate(lines):
        for key, label in _MONTHLY_METRIC_SPECS.items():
            if line != label or key in metrics:
                continue
            window = lines[index + 1 : index + 7]
            if not any("去年同期" in re.sub(r"\s+", "", item) for item in window):
                continue
            value_index = _next_metric_value_index(lines, index)
            if value_index is None:
                continue
            metric: dict[str, Any] = {
                "key": key,
                "label": f"本月{label}",
                "value": _normalize_metric_value(lines[value_index]),
                "raw_label": line,
                "section": "本月离店（按月）",
            }
            for cursor in range(value_index + 1, min(len(lines), value_index + 8)):
                candidate = lines[cursor]
                if candidate in stop_lines:
                    break
                _apply_metric_context(lines, cursor, metric)
            metrics[key] = metric
    period_line = next(
        (item for item in lines if _MONTHLY_PERIOD_RE.fullmatch(item)),
        None,
    )
    if metrics and period_line:
        metrics["monthly_period_label"] = {
            "key": "monthly_period_label",
            "label": "统计周期",
            "value": period_line,
            "section": "本月离店（按月）",
        }
    return metrics

def _extract_flow_data_dataset_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    labels = {
        "realtime_visitors": "实时访客量",
        "realtime_booking_conversion_rate": "实时下单转化率(APP)",
        "realtime_visitor_rank": "实时排名",
        "realtime_starting_price": "实时起价",
        "visitors": "访客量",
        "conversion_rate": "转化率",
        "exposure_conversion_rate": "曝光转化率",
        "booking_conversion_rate": "下单转化率",
        "transaction_conversion_rate": "成交转化率",
    }
    realtime_labels = ("实时访客量", "实时下单转化率(APP)")
    normalized_lines = [
        next((label for label in realtime_labels if line.startswith(label)), line)
        for line in lines
    ]
    metrics = _extract_ordered_metric_cards(normalized_lines, labels)
    _pair_grouped_competition_averages(normalized_lines, metrics)
    return metrics


def _pair_grouped_competition_averages(lines: list[str], metrics: dict[str, dict[str, Any]]) -> None:
    """「我的酒店」与「竞争圈平均」两组各按 标签→值 排列，需按标签一一配对，
    避免 `_apply_metric_context` 把组内第一个值错配给上一个指标。"""
    label_to_key = {
        metric.get("label"): key for key, metric in metrics.items() if metric.get("label")
    }
    index = 0
    while index < len(lines):
        if lines[index] != "竞争圈平均":
            index += 1
            continue
        cursor = index + 1
        while cursor < len(lines):
            key = label_to_key.get(lines[cursor])
            if key is None:
                break
            value_index = _next_metric_value_index(lines, cursor)
            if value_index is None:
                break
            metrics[key]["competition_average"] = _normalize_metric_value(
                lines[value_index]
            )
            cursor = value_index + 1
        index = max(cursor, index + 1)

def _extract_loss_analysis_dataset_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    return _extract_ordered_metric_cards(
        lines,
        {
            "lost_orders": "流失订单量",
            "lost_room_nights": "流失间夜量",
            "lost_order_amount": "流失订单金额",
        },
    )

def _extract_competition_profile_dataset_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    return _extract_ordered_metric_cards(
        lines,
        {
            "booking_orders": "预订订单量",
            "booking_sales": "预订销售额",
            "in_house_room_nights": "在店间夜",
            "occupancy_rate": "出租率",
            "ctrip_app_visitors": "携程APP访客",
            "ctrip_app_conversion_rate": "携程APP转化率",
            "list_exposure": "列表页曝光",
            "app_detail_visitors": "APP详情页访客",
            "order_page_visitors": "订单页访客",
            "list_exposure_conversion_rate": "列表页曝光转化",
            "app_booking_conversion_rate": "App下单转化",
            "psi": "PSI",
            "ctrip_review_score": "携程点评分",
        },
    )

def _extract_competition_list_dataset_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for table in _extract_competition_list_tables(lines):
        my_hotel = next(
            (row for row in table.get("rows", []) if row.get("hotel_name") == "我的酒店"),
            None,
        )
        if not my_hotel:
            continue
        for column, value in my_hotel.get("rankings", {}).items():
            key = _slugify_metric_key(f"{table['name']}_{column}")
            metrics[key] = {
                "key": key,
                "label": f"{table['label']} {column}",
                "value": value,
                "section": table["label"],
            }
    return metrics

def _extract_promotion_effect_dataset_metrics(lines: list[str]) -> dict[str, dict[str, Any]]:
    metric_specs = {
        "participated_promotions": (("参加促销",), "总促销数"),
        "promotion_room_nights": (("含促销间夜",), "总间夜"),
        "promotion_room_night_share": (("含促销间夜占比",), None),
        "average_daily_app_visitors": (("平均每日 访客量（APP）", "访客量（APP）"), None),
    }
    metric_labels = tuple(
        label for aliases, _ in metric_specs.values() for label in aliases
    )
    metrics: dict[str, dict[str, Any]] = {}
    for key, (labels, denominator_label) in metric_specs.items():
        matched_label = ""
        label_index = None
        for label in labels:
            label_index = _find_line_index(lines, label)
            if label_index is None:
                label_index = _find_line_containing(lines, label)
            if label_index is not None:
                matched_label = label
                break
        if label_index is None or not matched_label:
            continue
        metric = _extract_promotion_metric_card(
            lines,
            label_index=label_index,
            key=key,
            label=matched_label,
            denominator_label=denominator_label,
            stop_labels=tuple(other for other in metric_labels if other != matched_label),
        )
        if metric:
            metrics[key] = metric

    app_visitors = metrics.get("average_daily_app_visitors")
    if app_visitors:
        raw_value = str(app_visitors.get("value") or "")
        if "/" in raw_value:
            left, right = raw_value.split("/", 1)
            app_visitors["value"] = left.strip()
            app_visitors["related_label"] = "去哪儿平均每日访客量"
            app_visitors["related_value"] = right.strip()
            extra_averages = app_visitors.get("extra_competition_averages")
            if isinstance(extra_averages, list) and extra_averages:
                app_visitors["related_competition_average"] = extra_averages[0]

    # 2026-08-28 实测（T1.3）：页面顶部「今日实时数据」区块只包含参加促销/总促销数；
    # 其余指标受「数据来源」下拉（昨日/过去7天/过去30天）控制。区块头紧邻指标标签
    # 出现，据此把该指标标记为 realtime，其余保持无时间口径（不进实时卡）。
    realtime_header_index = next(
        (index for index, line in enumerate(lines) if "今日实时数据" in line),
        None,
    )
    if realtime_header_index is not None and "participated_promotions" in metrics:
        participated_label_index = next(
            (
                index
                for index, line in enumerate(lines)
                if "参加促销" in re.sub(r"\s+", "", line)
                and "已参加促销" not in re.sub(r"\s+", "", line)
            ),
            None,
        )
        if (
            participated_label_index is not None
            and 0 < participated_label_index - realtime_header_index <= 3
        ):
            metrics["participated_promotions"]["time_scope"] = "realtime"
            metrics["participated_promotions"]["realtime_section_header"] = "今日实时数据"
    return metrics

def _extract_promotion_metric_card(
    lines: list[str],
    *,
    label_index: int,
    key: str,
    label: str,
    denominator_label: str | None,
    stop_labels: tuple[str, ...],
) -> dict[str, Any] | None:
    value_index = _next_metric_value_index(lines, label_index)
    raw_value = _extract_inline_metric_value(lines[label_index], label)
    if raw_value is None and value_index is not None:
        raw_value = lines[value_index]
    if raw_value is None:
        return None
    value = _normalize_metric_value(raw_value)
    metric: dict[str, Any] = {
        "key": key,
        "label": label,
        "value": value,
        "raw_label": lines[label_index],
    }
    if denominator_label and value and "/" in value:
        numerator, denominator = value.split("/", 1)
        metric["value"] = numerator.strip()
        metric["denominator_label"] = denominator_label
        metric["denominator"] = denominator.strip()
        metric["display_value"] = f"{metric['value']}/{metric['denominator']}"
    elif denominator_label and value_index is not None and value_index + 1 < len(lines):
        denominator = _normalize_metric_value(lines[value_index + 1])
        if denominator and _BUSINESS_VALUE_RE.fullmatch(lines[value_index + 1]):
            metric["denominator_label"] = denominator_label
            metric["denominator"] = denominator
            metric["display_value"] = f"{value}/{denominator}"
    context_start = value_index + 1 if value_index is not None else label_index + 1
    for cursor in range(context_start, min(len(lines), context_start + 12)):
        candidate = lines[cursor]
        if candidate in {"数据来源", "促销报名", "已参加促销", "促销效果", "隐私政策规则中心"}:
            break
        if any(_line_contains_metric_label(candidate, stop_label) for stop_label in stop_labels):
            break
        if candidate.startswith("竞争圈平均"):
            competition_average = _extract_inline_or_next_value(
                lines,
                cursor,
                "竞争圈平均",
            )
            if not competition_average:
                continue
            if not metric.get("competition_average"):
                metric["competition_average"] = competition_average
            else:
                extra_averages = metric.setdefault("extra_competition_averages", [])
                if isinstance(extra_averages, list):
                    extra_averages.append(competition_average)
        elif candidate.startswith("建议"):
            metric["recommendation"] = candidate
    return metric

def _extract_inline_metric_value(line: str, label: str) -> str | None:
    suffix = re.sub(r"\s+", "", line.replace(label, "", 1))
    match = _BUSINESS_VALUE_RE.search(suffix)
    return match.group(0) if match else None

def _line_contains_metric_label(line: str, label: str) -> bool:
    return re.sub(r"\s+", "", label) in re.sub(r"\s+", "", line)

def _extract_section_metric_cards(
    lines: list[str],
    *,
    section_label: str,
    stop_labels: tuple[str, ...],
    labels: dict[str, str],
) -> dict[str, dict[str, Any]]:
    start = _find_line_index(lines, section_label)
    if start is None:
        return {}
    stop = len(lines)
    for stop_label in stop_labels:
        stop_index = _find_line_index(lines[start + 1 :], stop_label)
        if stop_index is not None:
            stop = min(stop, start + 1 + stop_index)
    section_lines = lines[start:stop]
    metrics = _extract_ordered_metric_cards(section_lines, labels)
    for metric in metrics.values():
        metric["section"] = section_label
    return metrics

def _extract_ordered_metric_cards(
    lines: list[str],
    labels: dict[str, str],
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    label_values = tuple(labels.values())
    for index, line in enumerate(lines):
        for key, label in labels.items():
            if line == label or _is_label_with_trailing_navigation(line, label):
                metric = _extract_metric_card_from_lines(
                    lines,
                    index,
                    key=key,
                    label=label,
                    stop_labels=label_values,
                )
            elif line.startswith(label) and any(term in line for term in ("您的酒店", "我的酒店")):
                metric = _extract_compact_competition_metric(line, key=key, label=label)
            else:
                continue
            if metric and key not in metrics:
                metrics[key] = metric
    return metrics

_LABEL_TRAILING_NAVIGATION_PREFIXES = ("查看", "去", "立即", "马上", "更多", "下载")
# 流量转化分析等卡片标签后会跟趋势状态词（落后/领先/持平），值在下一行。
_LABEL_TRAILING_STATUS_WORDS = ("落后", "领先", "持平")


def _is_label_with_trailing_navigation(line: str, label: str) -> bool:
    """页面会把导航链接拼进标签行，如「实时排名 查看21天排名」，值在下一行。"""
    if not line.startswith(label):
        return False
    remainder = line[len(label) :].strip()
    if not remainder:
        return False
    return remainder.startswith(
        _LABEL_TRAILING_NAVIGATION_PREFIXES + _LABEL_TRAILING_STATUS_WORDS
    )


def _extract_metric_card_from_lines(
    lines: list[str],
    index: int,
    *,
    key: str,
    label: str,
    stop_labels: tuple[str, ...],
) -> dict[str, Any] | None:
    value_index = _next_metric_value_index(lines, index)
    if value_index is None:
        return None
    metric: dict[str, Any] = {
        "key": key,
        "label": label,
        "value": _normalize_metric_value(lines[value_index]),
        "raw_label": lines[index],
    }
    for cursor in range(value_index + 1, min(len(lines), value_index + 12)):
        candidate = lines[cursor]
        if candidate in stop_labels:
            break
        if candidate in _DATASET_SECTION_STOP_LINES:
            break
        _apply_metric_context(lines, cursor, metric)
    return metric

def _next_metric_value_index(lines: list[str], index: int) -> int | None:
    for cursor in range(index + 1, min(len(lines), index + 5)):
        candidate = lines[cursor]
        if candidate in {"您的酒店", "我的酒店"}:
            continue
        if _is_metric_or_section_label(candidate):
            return None
        if _is_action_or_navigation_line(candidate):
            continue
        if _BUSINESS_VALUE_RE.search(candidate):
            return cursor
    return None

def _apply_metric_context(lines: list[str], index: int, metric: dict[str, Any]) -> None:
    candidate = lines[index]
    compact = re.sub(r"\s+", "", candidate)
    if compact.startswith("上周同期") or compact.startswith("上周同期"):
        metric["last_week_same_period"] = _extract_inline_or_next_value(lines, index, "上周同期")
    elif compact.startswith("上周同比"):
        metric["week_over_week"] = _extract_inline_or_next_value(lines, index, "上周同比")
    elif compact.startswith("去年同期"):
        match = re.search(r"去年同期([+-]?[\d,.]+(?:万|%)?)", compact)
        if match:
            metric["last_year_same_period"] = _normalize_metric_value(match.group(1))
    elif compact.startswith("去年同比"):
        match = re.search(r"去年同比([+-]?[\d,.]+(?:万|%)?)", compact)
        if match:
            metric["year_over_year"] = _normalize_metric_value(match.group(1))
    elif "较上周" in candidate or "较去年同期" in candidate:
        # 竞争圈排名之后的「较上周同期N」是排名变化，不是指标自身环比。
        if metric.get("competitive_rank"):
            match = re.search(r"(?:较上周同期|较去年同期)\s*([+-]?[\d,.]+)", compact)
            if match:
                metric.setdefault(
                    "competitive_rank_change",
                    _normalize_metric_value(match.group(1)),
                )
            else:
                metric.setdefault("competitive_rank_change", _normalize_metric_value(candidate))
        else:
            metric["change"] = _normalize_metric_value(candidate)
    elif "竞争圈排名" in candidate:
        metric["competitive_rank"] = _extract_inline_or_next_value(lines, index, "竞争圈排名")
    elif "竞争圈平均" in candidate:
        metric["competition_average"] = _extract_inline_or_next_value(
            lines,
            index,
            "竞争圈平均",
        )

def _extract_inline_or_next_value(lines: list[str], index: int, label: str) -> str | None:
    line = lines[index]
    suffix = re.sub(r"\s+", "", line.replace(label, "", 1))
    match = _BUSINESS_VALUE_RE.search(suffix)
    if match:
        return _normalize_metric_value(match.group(0))
    for candidate in lines[index + 1 : index + 3]:
        if _BUSINESS_VALUE_RE.search(candidate):
            return _normalize_metric_value(candidate)
    return None

def _extract_compact_competition_metric(
    line: str,
    *,
    key: str,
    label: str,
) -> dict[str, Any] | None:
    numbers = [_normalize_metric_value(value) for value in _BUSINESS_VALUE_RE.findall(line)]
    if not numbers:
        return None
    metric: dict[str, Any] = {
        "key": key,
        "label": label,
        "value": numbers[0],
        "raw_label": line,
    }
    if len(numbers) >= 2:
        metric["competition_average"] = numbers[1]
    if len(numbers) >= 3:
        metric["competitive_rank"] = numbers[2]
    return metric

def _normalize_metric_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    normalized = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", normalized)
    normalized = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", normalized)
    return normalized

def _find_line_index(lines: list[str], value: str) -> int | None:
    for index, line in enumerate(lines):
        if line == value:
            return index
    return None

def _find_line_containing(lines: list[str], value: str) -> int | None:
    compact_value = re.sub(r"\s+", "", value)
    for index, line in enumerate(lines):
        if compact_value in re.sub(r"\s+", "", line):
            return index
    return None

def _next_comparison_text(lines: list[str], index: int) -> str | None:
    for candidate in lines[index + 1 : index + 7]:
        comparison_terms = ("上周同期", "上周同比", "同比", "环比", "竞争圈平均")
        if any(term in candidate for term in comparison_terms):
            return candidate
    return None

def _extract_dataset_rankings(lines: list[str]) -> list[dict[str, str]]:
    rankings: list[dict[str, str]] = []
    for line in lines:
        if re.search(r"\d{4}/\d{1,2}", line):
            continue
        rank_matches = re.findall(r"(?:竞争圈)?(?:排名)?\s*([0-9]+\s*/\s*[0-9]+)", line)
        if not rank_matches:
            continue
        rankings.append({"text": line, "rank": _normalize_metric_value(rank_matches[0]) or ""})
    return rankings

def _extract_dataset_tables(lines: list[str], dataset_id: str) -> list[dict[str, Any]]:
    if dataset_id == "competition_list":
        return _extract_competition_list_tables(lines)
    if dataset_id == "loss_analysis":
        return _extract_loss_analysis_tables(lines)
    if dataset_id == "promotion_effect":
        return _extract_promotion_effect_tables(lines)
    table_keywords = ("我的酒店", "竞争圈平均", "酒店名称", "预订订单量排名", "预订销售额排名")
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not any(keyword in line for keyword in table_keywords):
            continue
        numbers = _BUSINESS_VALUE_RE.findall(line)
        rows.append({"text": line, "values": numbers[:12]})
    return [{"name": "visible_text_rows", "rows": rows[:30]}] if rows else []

def _extract_competition_list_tables(lines: list[str]) -> list[dict[str, Any]]:
    table_specs = (
        (
            "sales_ranking",
            "销售排名",
            ("预订订单量排名", "预订销售额排名", "在店间夜排名", "出租率排名"),
            "流量排名",
        ),
        (
            "traffic_ranking",
            "流量排名",
            ("APP详情页访客量排名", "APP详情页转化率排名"),
            "服务排名",
        ),
        (
            "service_ranking",
            "服务排名",
            ("PSI分排名", "携程点评分", "去哪儿点评分", "同程点评分", "智行点评分"),
            "隐私政策规则中心",
        ),
    )
    tables: list[dict[str, Any]] = []
    for name, label, columns, stop_label in table_specs:
        start = _find_line_index(lines, label)
        if start is None:
            continue
        stop = _find_line_index(lines[start + 1 :], stop_label)
        section = lines[start + 1 : start + 1 + stop] if stop is not None else lines[start + 1 :]
        rows = _extract_rank_table_rows(section, columns)
        tables.append({"name": name, "label": label, "columns": list(columns), "rows": rows})
    return tables

def _extract_rank_table_rows(lines: list[str], columns: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    table_start = _find_line_index(lines, "酒店名称")
    if table_start is None:
        return rows
    cursor = table_start + 1
    while cursor < len(lines) and lines[cursor] in columns:
        cursor += 1
    while cursor < len(lines):
        hotel_name = lines[cursor]
        if hotel_name in {"查看更多", "下载数据"} or hotel_name in _DATASET_SECTION_STOP_LINES:
            break
        if _is_numeric_line(hotel_name):
            cursor += 1
            continue
        cursor += 1
        values: list[str] = []
        while cursor < len(lines) and _is_numeric_line(lines[cursor]):
            values.append(_normalize_metric_value(lines[cursor]) or lines[cursor])
            cursor += 1
        if values:
            rows.append(
                {
                    "hotel_name": hotel_name,
                    "rankings": {
                        column: value for column, value in zip(columns, values, strict=False)
                    },
                    "raw_values": values,
                }
            )
    return rows

def _extract_loss_analysis_tables(lines: list[str]) -> list[dict[str, Any]]:
    start = _find_line_index(lines, "流失酒店TOP40")
    if start is None:
        return []
    rows: list[dict[str, Any]] = []
    cursor = start + 1
    while cursor < len(lines):
        line = lines[cursor]
        if line in {"共同浏览率", "下单转化率", "流失订单数", "操作"}:
            cursor += 1
            continue
        if line in {"隐私政策规则中心", "分享反馈"} or re.fullmatch(r"\d+", line):
            break
        hotel_name = line
        location = lines[cursor + 1] if cursor + 1 < len(lines) else ""
        values_line = lines[cursor + 2] if cursor + 2 < len(lines) else ""
        values = [
            _normalize_metric_value(value) or value
            for value in _BUSINESS_VALUE_RE.findall(values_line)
        ]
        action = lines[cursor + 3] if cursor + 3 < len(lines) else ""
        if values:
            rows.append(
                {
                    "hotel_name": hotel_name,
                    "location": location,
                    "common_browse_rate": values[0] if len(values) > 0 else None,
                    "booking_conversion_rate": values[1] if len(values) > 1 else None,
                    "lost_orders": values[2] if len(values) > 2 else None,
                    "action": action,
                }
            )
            cursor += 4
        else:
            cursor += 1
    return [{"name": "lost_hotel_top40", "label": "流失酒店TOP40", "rows": rows[:40]}]

def _extract_promotion_effect_tables(lines: list[str]) -> list[dict[str, Any]]:
    rows = [
        {"text": line}
        for line in lines
        if line.startswith("建议") or "参加促销可以" in line or "竞争圈酒店" in line
    ]
    return [
        {
            "name": "promotion_effect_recommendations",
            "label": "促销效果建议",
            "rows": rows[:20],
        }
    ] if rows else []
