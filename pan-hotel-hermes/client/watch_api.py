from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import uuid
from typing import Any, ClassVar

import httpx

from client.feishu_cards import attach_ebk_intent_card, attach_feishu_card, build_generic_operator_card
from client.feishu_chat_context import enrich_feishu_chat_context
from client.intent_routing import (
    build_ebk_review_misroute_response,
    enrich_ebk_run_response,
    is_ebk_review_only_dataset_read,
)
from client.manual_pricing import validate_manual_pricing_fields
from client.operator_pending_action import (
    attach_pending_operator_action,
    clear_pending_operator_action,
    register_stale_immediate_run_offer,
)
from client.operator_summary_contract import normalize_operator_summary
from client.mobile_event_summary import build_alert_mobile_summary, build_status_mobile_summary
from client.operator_recent_activity import enrich_operator_recent_activity
from client.operator_watch_config import (
    attach_chat_binding_to_payload,
    attach_runtime_health_to_payload,
    build_chat_binding_summary,
    build_operator_watch_configuration,
    build_scoped_operator_watch_configuration,
    enrich_operator_watch_configuration,
)
from client.operator_analysis_orchestrator import build_operator_analysis_context
from client.weekly_briefing import enrich_weekly_briefing
from client.watch_groups import (
    DEFAULT_COMPETITOR_GROUP,
    DEFAULT_EBK_GROUP,
    WATCH_KIND_COMPETITOR,
    WATCH_KIND_EBK,
    build_watch_group_catalog,
    normalize_competitor_watch_group_key,
    normalize_ebk_watch_group_key,
    prepare_watch_group_key,
    watch_kind_label,
)

# Backward-compatible re-exports for tests/imports.
LEGACY_EBK_GROUP_ALIASES = {
    "default_data_center_watch": DEFAULT_EBK_GROUP,
}

STALE_COMPETITOR_DATA_HOURS = 6
STALE_EBK_DATA_HOURS = 6
DEFAULT_API_TIMEOUT = 120.0
# Browser run-now calls can crawl multiple Ctrip pages; keep this above Hermes gateway defaults.
BROWSER_RUN_TIMEOUT = float(os.environ.get("WATCH_API_BROWSER_TIMEOUT", "300"))


def _beijing_today() -> _dt.date:
    """业务时区 Asia/Shanghai 的今天（周/月预设换算口径）。"""
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).date()


def _range_preset_dates(preset: str) -> tuple[str, str]:
    """上周=上个周一~周日；上月=上个自然月整月（Asia/Shanghai 口径）。"""
    today = _beijing_today()
    if preset == "last_week":
        monday = today - _dt.timedelta(days=today.weekday() + 7)
        return monday.isoformat(), (monday + _dt.timedelta(days=6)).isoformat()
    last_month_end = today.replace(day=1) - _dt.timedelta(days=1)
    return last_month_end.replace(day=1).isoformat(), last_month_end.isoformat()
EBK_DATASET_CATALOG: dict[str, str] = {
    "business_outline": "收益概览",
    "beneficial_data": "销售数据",
    "flow_data": "流量数据",
    "competition_profile": "经营对比",
    "loss_analysis": "流失订单",
    "competition_list": "销售排名",
    "promotion_effect": "促销效果",
    "promotion_registered": "已参加促销",
    "ad_pyramid": "广告金字塔",
    # 金字塔推广计划列表（计划名=投放时段；今日花费/曝光/点击/出价），
    # 2026-09-22 起并入按需单数据集现读。
    "pyramid_bid": "金字塔推广计划",
    "comment_monitor": "点评分析",
}
EBK_DATASET_IDS = frozenset(EBK_DATASET_CATALOG)
EBK_READABLE_DATASET_IDS = frozenset(item for item in EBK_DATASET_IDS if item != "comment_monitor")
_EBK_DATASET_ALIASES: dict[str, str] = {
    **{label: dataset_id for dataset_id, label in EBK_DATASET_CATALOG.items()},
    **{dataset_id: dataset_id for dataset_id in EBK_DATASET_CATALOG},
    "经营报告": "business_outline",
    "经营概览": "business_outline",
    "收益": "business_outline",
    "销售": "beneficial_data",
    "出租率": "beneficial_data",
    "入住率": "beneficial_data",
    "间夜": "beneficial_data",
    "订单": "beneficial_data",
    "访客": "flow_data",
    "访客量": "flow_data",
    "流量": "flow_data",
    "转化率": "flow_data",
    "竞争圈": "competition_profile",
    "竞争对比": "competition_profile",
    "流失": "loss_analysis",
    "排名": "competition_list",
    "销售排名榜": "competition_list",
    "促销情况": "promotion_effect",
    "参加促销": "promotion_registered",
    "促销活动": "promotion_registered",
    "已报名促销": "promotion_registered",
    "金字塔": "ad_pyramid",
    "广告": "ad_pyramid",
    "广告投放": "ad_pyramid",
    # 金字塔推广计划（计划名=时段）的口语别名；「金字塔出价」历史上就是
    # 计划出价指令的说法，指向计划列表而非数据报告页。
    "金字塔推广计划": "pyramid_bid",
    "推广计划": "pyramid_bid",
    "金字塔出价": "pyramid_bid",
    "金字塔时段": "pyramid_bid",
    "pyramid_campaigns": "pyramid_bid",
    "点评": "comment_monitor",
    "订单点评": "comment_monitor",
    "好评": "comment_monitor",
    "差评": "comment_monitor",
    # Common English aliases / LLM hallucinations
    "revenue_overview": "business_outline",
    "sales_data": "beneficial_data",
    "traffic": "flow_data",
    "competition": "competition_profile",
    "competition_ranking": "competition_list",
    "lost_orders": "loss_analysis",
    "promotion_effectiveness": "promotion_effect",
    "promotion_registered_promotions": "promotion_registered",
    "pyramid": "ad_pyramid",
    "ad_pyramid_today": "ad_pyramid",
    "reviews": "comment_monitor",
    "comments": "comment_monitor",
}
_PROMOTION_DATASET_IDS = frozenset({"promotion_effect", "promotion_registered", "ad_pyramid"})
# 金字塔问法带时段词时自动附带推广计划数据集（计划名=投放时段）。
_PYRAMID_TIME_SLOT_HINTS = ("时段", "分时", "每小时", "小时", "每个计划", "各计划")


def expand_pyramid_bid_dataset_ids(
    dataset_ids: list[str] | None,
    *,
    user_text: str | None,
) -> list[str] | None:
    """ad_pyramid 已在请求且问法带时段词 → 确定性补 pyramid_bid。

    2026-09-22 组合分析实测：agent 只传 ad_pyramid 漏掉 pyramid_bid，回复误称
    「平台不提供分时花费」——实际推广计划列表有按时段（计划名）的今日花费/
    曝光/点击。工具层补齐，不依赖 LLM 记得带。
    """
    if not dataset_ids or "pyramid_bid" in dataset_ids or "ad_pyramid" not in dataset_ids:
        return dataset_ids
    text = str(user_text or "")
    if not any(hint in text for hint in _PYRAMID_TIME_SLOT_HINTS):
        return dataset_ids
    expanded = list(dataset_ids)
    expanded.append("pyramid_bid")
    return expanded
_METRIC_DATASET_HINTS: dict[str, str] = {
    "occupancy_rate": "beneficial_data",
    "adr": "beneficial_data",
    "revpar": "beneficial_data",
    "room_nights": "beneficial_data",
    "revenue": "beneficial_data",
    "booking_orders": "beneficial_data",
    "sales_amount": "beneficial_data",
    "出租率": "beneficial_data",
    "入住率": "beneficial_data",
    "平均卖价": "beneficial_data",
    "平均房价": "beneficial_data",
    "均价": "beneficial_data",
    "transaction_rate": "business_outline",
    "成交率": "business_outline",
    "realtime_booking_orders": "business_outline",
    "visitors": "flow_data",
    "conversion_rate": "flow_data",
    "访客量": "flow_data",
    "转化率": "flow_data",
    "lost_orders": "loss_analysis",
    "流失订单": "loss_analysis",
    "ranking": "competition_list",
    "booking_order_rank": "competition_list",
    "roas": "ad_pyramid",
    "clicks": "ad_pyramid",
    "impressions": "ad_pyramid",
}
EBK_DATASET_CATALOG_NOTE = (
    "可用 dataset_id："
    + "；".join(f"{dataset_id}={label}" for dataset_id, label in EBK_DATASET_CATALOG.items())
    + "。一次可传多个；问好评/差评用 get_ebk_latest_result，不要用 read_ebk_datasets(comment_monitor)。"
)


def _metric_display_value(metric: Any) -> str | None:
    if not isinstance(metric, dict):
        return None
    value = metric.get("value")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def enrich_ebk_latest_response(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Add operator-facing summary so Hermes can answer review questions without misreading config flags."""
    dataset_results = snapshot.get("dataset_results")
    rows = dataset_results if isinstance(dataset_results, list) else []
    comment_row: dict[str, Any] | None = None
    for item in rows:
        if isinstance(item, dict) and item.get("dataset_id") == "comment_monitor":
            comment_row = item
            break

    review_summary: dict[str, Any]
    if comment_row is None:
        review_summary = {
            "available": False,
            "summary": "最新一次 EBK 读取结果里没有 comment_monitor 数据集。",
        }
    else:
        data = comment_row.get("data") if isinstance(comment_row.get("data"), dict) else {}
        monitor = data.get("comment_monitor") if isinstance(data.get("comment_monitor"), dict) else {}
        overview = monitor.get("review_overview") if isinstance(monitor.get("review_overview"), dict) else {}
        review_summary = {
            "available": True,
            "positive_reviews_yesterday": _metric_display_value(overview.get("positive_reviews_yesterday")),
            "negative_reviews_yesterday": _metric_display_value(overview.get("negative_reviews_yesterday")),
            "overall_score": _metric_display_value(overview.get("overall_score")),
            "total_comment_count": _metric_display_value(overview.get("total_comment_count")),
            "captured_at": comment_row.get("captured_at"),
            "caliber_note": (
                "positive_reviews_yesterday 表示昨日好评，不是今日实时新增。"
                "用户问「今天新增几个好评」时，应如实说明这是昨日口径。"
            ),
        }

    beneficial_metrics = _extract_beneficial_metrics(rows)
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    has_ebk_data = bool(rows) or review_summary.get("available") or bool(beneficial_metrics)
    freshness = _ebk_freshness_hints(
        captured_at=run.get("captured_at") or review_summary.get("captured_at"),
        has_data=has_ebk_data,
    )
    enriched = {
        **snapshot,
        "operator_summary": {
            "watch_kind": WATCH_KIND_EBK,
            "watch_kind_label": watch_kind_label(WATCH_KIND_EBK),
            "watch_group_key": snapshot.get("watch_group_key") or run.get("watch_group_key"),
            "latest_run_status": run.get("status"),
            "latest_captured_at": run.get("captured_at"),
            "dataset_ids_in_latest_run": [
                str(item.get("dataset_id"))
                for item in rows
                if isinstance(item, dict) and item.get("dataset_id")
            ],
            "review_summary": review_summary,
            "beneficial_metrics": beneficial_metrics,
            "beneficial_summary": (
                "已提取 beneficial_data 核心指标。"
                if beneficial_metrics
                else "最新结果中暂无 beneficial_data 指标。"
            ),
            **freshness,
            "usage_note": (
                "数据集 enabled=false 只影响定时轮询，不影响 /latest 已持久化结果。"
                "问好评/差评/评论时用 get_ebk_latest_result 发点评卡片；"
                "问出租率/经营时用 read_ebk_datasets 发经营卡片；"
                "用户确认要刷新全部 EBK 后再 run_ebk_watch_once（只持久化不推卡）。"
            ),
        },
    }
    return enriched


def _extract_beneficial_metrics(dataset_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in dataset_results:
        if not isinstance(item, dict) or item.get("dataset_id") != "beneficial_data":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        rows: list[dict[str, Any]] = []
        for key in ("occupancy_rate", "adr", "revpar", "room_nights", "revenue"):
            metric = metrics.get(key)
            if not isinstance(metric, dict):
                continue
            label = metric.get("label") or key
            value = metric.get("display_value") or metric.get("value")
            if value is not None:
                rows.append({"field": str(label), "value": str(value)})
        return rows
    return []


def _normalize_ebk_dataset_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text in _EBK_DATASET_ALIASES:
        return _EBK_DATASET_ALIASES[text]
    lowered = text.lower()
    if lowered in EBK_DATASET_CATALOG:
        return lowered
    if lowered in _EBK_DATASET_ALIASES:
        return _EBK_DATASET_ALIASES[lowered]
    return text


def _resolve_ebk_read_dataset_ids(
    *,
    dataset_ids: list[str] | None,
    metric_keys: list[str] | None,
) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    invalid: list[str] = []
    for item in dataset_ids or []:
        dataset_id = _normalize_ebk_dataset_id(item)
        if not dataset_id:
            continue
        if dataset_id in EBK_DATASET_CATALOG:
            if dataset_id not in resolved:
                resolved.append(dataset_id)
        elif dataset_id not in invalid:
            invalid.append(dataset_id)
    if not resolved:
        for key in metric_keys or []:
            dataset_id = _METRIC_DATASET_HINTS.get(str(key or "").strip())
            if dataset_id and dataset_id not in resolved:
                resolved.append(dataset_id)
    if not resolved and not invalid:
        resolved = ["business_outline"]
    return resolved, invalid


def _invalid_ebk_dataset_response(
    *,
    watch_group_key: str,
    invalid_dataset_ids: list[str],
) -> dict[str, Any]:
    return {
        "status": "invalid_request",
        "summary": (
            f"不支持的数据集 ID：{', '.join(invalid_dataset_ids)}。"
            f"{EBK_DATASET_CATALOG_NOTE}"
        ),
        "watch_kind": WATCH_KIND_EBK,
        "watch_group_key": watch_group_key,
        "invalid_dataset_ids": invalid_dataset_ids,
        "supported_dataset_ids": sorted(EBK_READABLE_DATASET_IDS),
        "operator_summary": {
            "watch_kind": WATCH_KIND_EBK,
            "watch_kind_label": watch_kind_label(WATCH_KIND_EBK),
            "watch_group_key": watch_group_key,
            "dataset_read_note": EBK_DATASET_CATALOG_NOTE,
            "usage_note": "请改用 read_ebk_datasets 并传入 supported_dataset_ids 中的合法 dataset_id。",
        },
    }


def _dataset_label(dataset_id: str, configured_datasets: list[dict[str, Any]]) -> str:
    for dataset in configured_datasets:
        if not isinstance(dataset, dict):
            continue
        if dataset.get("dataset_id") == dataset_id:
            label = str(dataset.get("dataset_label") or "").strip()
            if label:
                return label
    return EBK_DATASET_CATALOG.get(dataset_id, dataset_id)


def _metric_rows_from_dataset_results(dataset_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for dataset in dataset_results:
        if not isinstance(dataset, dict):
            continue
        data = dataset.get("data") if isinstance(dataset.get("data"), dict) else {}
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        for key in (
            "occupancy_rate",
            "average_selling_price",
            "average_price",
            "transaction_rate",
            "adr",
            "revpar",
            "room_nights",
            "revenue",
        ):
            metric = metrics.get(key)
            if not isinstance(metric, dict):
                continue
            value = metric.get("display_value") or metric.get("value")
            if value is None:
                continue
            rows.append({"field": str(metric.get("label") or key), "value": str(value)})
    return rows


def _requested_metrics_from_dataset_results(
    dataset_results: list[dict[str, Any]],
    metric_keys: list[str] | None,
) -> dict[str, Any]:
    requested = {str(item or "").strip() for item in metric_keys or [] if str(item or "").strip()}
    if not requested:
        return {}
    found: dict[str, Any] = {}
    for dataset in dataset_results:
        if not isinstance(dataset, dict):
            continue
        data = dataset.get("data") if isinstance(dataset.get("data"), dict) else {}
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        for key in requested:
            metric = metrics.get(key)
            if isinstance(metric, dict) and key not in found:
                found[key] = {
                    **metric,
                    "dataset_id": dataset.get("dataset_id"),
                    "dataset_label": dataset.get("dataset_label"),
                }
    for key in requested:
        found.setdefault(
            key,
            {"key": key, "status": "not_found", "summary": "本次读取结果里没有解析到该指标。"},
        )
    return found


def _read_ebk_datasets_summary(
    *,
    status: str,
    metric_rows: list[dict[str, str]],
    dataset_results: list[dict[str, Any]],
) -> str:
    dataset_read_rows = _dataset_read_rows_from_results(dataset_results)
    if dataset_read_rows:
        parts = [f"{row['field']}：{row['value']}" for row in dataset_read_rows[:6]]
        return "EBK 数据中心现读完成，" + "；".join(parts) + "。"
    if metric_rows:
        parts = [f"{row['field']}：{row['value']}" for row in metric_rows[:5]]
        return "EBK 数据中心现读完成，" + "；".join(parts) + "。"
    success_count = sum(1 for item in dataset_results if item.get("status") == "success")
    return f"EBK 数据中心现读完成：状态 {status}，成功 {success_count}/{len(dataset_results)} 个数据集。"


def _ad_pyramid_metric_groups(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = data.get("ad_pyramid") if isinstance(data.get("ad_pyramid"), dict) else {}
    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    return [group for group in groups if isinstance(group, dict)]


def _promotion_registered_read_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    promotions = data.get("promotions") if isinstance(data.get("promotions"), list) else []
    visible_count = summary.get("visible_promotion_count")
    rows.append(
        {
            "field": "已参加促销 / 可见活动",
            "value": str(visible_count if visible_count is not None else len(promotions)),
        }
    )
    for promotion in promotions[:8]:
        if not isinstance(promotion, dict):
            continue
        name = str(promotion.get("name") or "未命名促销")
        group = str(promotion.get("promotion_group") or "").strip()
        title = f"{group} / {name}" if group else name
        parts = [title]
        room_types = promotion.get("room_types") or promotion.get("room_scope")
        if room_types:
            parts.append(f"房型 {room_types}")
        promotion_time = promotion.get("promotion_time") or promotion.get("date_scope")
        if promotion_time:
            parts.append(f"时间 {promotion_time}")
        if promotion.get("auto_extend") is True:
            parts.append("到期前自动延期")
        if promotion.get("promotion_offer"):
            parts.append(f"优惠 {promotion.get('promotion_offer')}")
        rows.append({"field": "促销活动", "value": " · ".join(parts)})
    if len(promotions) == 0:
        rows.append({"field": "促销活动", "value": "暂无可见促销项"})
    return rows


def _promotion_effect_read_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    metrics = data.get("summary_metrics")
    if not isinstance(metrics, dict):
        metrics = data.get("metrics")
    if isinstance(metrics, dict):
        for metric in metrics.values():
            if not isinstance(metric, dict):
                continue
            label = metric.get("label") or metric.get("key")
            value = metric.get("display_value") or metric.get("value")
            if label and value is not None:
                suffix = ""
                if metric.get("competition_average") is not None:
                    suffix = f"（竞争圈平均 {metric.get('competition_average')}）"
                rows.append({"field": f"促销效果 / {label}", "value": f"{value}{suffix}"})
    promotion_metrics = data.get("promotion_metrics") if isinstance(data.get("promotion_metrics"), list) else []
    for row in promotion_metrics[:6]:
        if not isinstance(row, dict):
            continue
        row_metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        parts = [str(row.get("name") or "未识别促销")]
        for key, label in (
            ("orders", "订单"),
            ("sales_amount", "销售额"),
            ("room_nights", "含促销间夜"),
            ("roi", "ROI"),
        ):
            if row_metrics.get(key):
                parts.append(f"{label} {row_metrics[key]}")
        rows.append({"field": "促销明细", "value": " / ".join(parts)})
    if not rows:
        rows.append({"field": "促销效果", "value": "页面未解析到促销效果指标"})
    return rows


def _ad_pyramid_read_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for group in _ad_pyramid_metric_groups(data):
        group_label = str(group.get("label") or "今日")
        metrics = group.get("metrics") if isinstance(group.get("metrics"), list) else []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            label = metric.get("label") or metric.get("key")
            value = metric.get("value")
            if not label or value is None:
                continue
            rows.append({"field": f"广告金字塔 / {group_label} / {label}", "value": str(value)})
    if not rows:
        rows.append({"field": "广告金字塔", "value": "暂无可见广告指标"})
    return rows


def _generic_metrics_read_rows(data: dict[str, Any], *, prefix: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    for key, metric in metrics.items():
        if not isinstance(metric, dict):
            continue
        label = metric.get("label") or key
        value = metric.get("display_value") or metric.get("value")
        if value is None:
            continue
        extras: list[str] = []
        if metric.get("last_week_same_period") is not None:
            extras.append(f"上周同期 {metric.get('last_week_same_period')}")
        if metric.get("week_over_week") is not None:
            extras.append(f"同比 {metric.get('week_over_week')}")
        if metric.get("competition_average") is not None:
            extras.append(f"竞争圈平均 {metric.get('competition_average')}")
        value_text = str(value)
        if extras:
            value_text = f"{value_text}（{'，'.join(extras)}）"
        rows.append({"field": f"{prefix} / {label}", "value": value_text})
    warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
    prompts = data.get("page_prompts") if isinstance(data.get("page_prompts"), list) else []
    for item in [*warnings, *prompts][:2]:
        text = str(item or "").strip()
        if text:
            rows.append({"field": f"{prefix} / 提示", "value": text})
    if not rows:
        rows.append({"field": prefix, "value": "页面未解析到指标"})
    return rows


def _dataset_rows_for_payload(dataset_id: str, data: dict[str, Any], *, prefix: str) -> list[dict[str, str]]:
    if dataset_id == "promotion_registered":
        return _promotion_registered_read_rows(data)
    if dataset_id == "promotion_effect":
        return _promotion_effect_read_rows(data)
    if dataset_id == "ad_pyramid":
        return _ad_pyramid_read_rows(data)
    return _generic_metrics_read_rows(data, prefix=prefix)


def _dataset_read_rows_from_results(dataset_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in dataset_results:
        if not isinstance(item, dict):
            continue
        dataset_id = str(item.get("dataset_id") or "")
        label = str(item.get("dataset_label") or _dataset_label(dataset_id, []))
        if item.get("status") != "success":
            rows.append(
                {
                    "field": label,
                    "value": str(item.get("summary") or "读取失败"),
                }
            )
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        rows.extend(_dataset_rows_for_payload(dataset_id, data, prefix=label))
    return rows


def _build_dataset_summaries(dataset_results: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for item in dataset_results:
        if not isinstance(item, dict):
            continue
        dataset_id = str(item.get("dataset_id") or "")
        if not dataset_id:
            continue
        label = str(item.get("dataset_label") or _dataset_label(dataset_id, []))
        if item.get("status") != "success":
            summaries[dataset_id] = {
                "available": False,
                "label": label,
                "status": item.get("status"),
                "summary": str(item.get("summary") or "读取失败"),
            }
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        row_items = _dataset_rows_for_payload(dataset_id, data, prefix=label)
        summaries[dataset_id] = {
            "available": True,
            "label": label,
            "rows": row_items,
            "captured_at": item.get("captured_at"),
        }
    return summaries


def _build_promotion_registered_summary(dataset_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in dataset_results:
        if not isinstance(item, dict) or item.get("dataset_id") != "promotion_registered":
            continue
        if item.get("status") != "success":
            return {
                "available": False,
                "status": item.get("status"),
                "summary": str(item.get("summary") or "已参加促销读取失败。"),
            }
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        promotions = data.get("promotions") if isinstance(data.get("promotions"), list) else []
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        visible_count = summary.get("visible_promotion_count")
        return {
            "available": True,
            "visible_promotion_count": visible_count if visible_count is not None else len(promotions),
            "promotions": promotions[:8],
            "lines": [row["value"] for row in _promotion_registered_read_rows(data) if row.get("field") == "促销活动"],
        }
    return {"available": False, "summary": "本次未读取已参加促销数据集。"}


def _build_promotion_effect_summary(dataset_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in dataset_results:
        if not isinstance(item, dict) or item.get("dataset_id") != "promotion_effect":
            continue
        if item.get("status") != "success":
            return {
                "available": False,
                "status": item.get("status"),
                "summary": str(item.get("summary") or "促销效果读取失败。"),
            }
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        return {
            "available": True,
            "rows": _promotion_effect_read_rows(data),
            "promotion_metrics": data.get("promotion_metrics") if isinstance(data.get("promotion_metrics"), list) else [],
        }
    return {"available": False, "summary": "本次未读取促销效果数据集。"}


def _build_ad_pyramid_summary(dataset_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in dataset_results:
        if not isinstance(item, dict) or item.get("dataset_id") != "ad_pyramid":
            continue
        if item.get("status") != "success":
            return {
                "available": False,
                "status": item.get("status"),
                "summary": str(item.get("summary") or "广告金字塔读取失败。"),
            }
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        rows = _ad_pyramid_read_rows(data)
        return {
            "available": True,
            "rows": rows,
            "today_rows": [row for row in rows if "今日" in str(row.get("field") or "")] or rows[:8],
        }
    return {"available": False, "summary": "本次未读取广告金字塔数据集。"}


def _build_pyramid_bid_summary(dataset_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in dataset_results:
        if not isinstance(item, dict) or item.get("dataset_id") != "pyramid_bid":
            continue
        if item.get("status") != "success":
            return {
                "available": False,
                "status": item.get("status"),
                "summary": str(item.get("summary") or "金字塔推广计划读取失败。"),
            }
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        promotions = [item for item in (data.get("promotions") or []) if isinstance(item, dict)]
        rows = [
            {
                "campaign_name": str(promo.get("campaign_name") or ""),
                "campaign_status": promo.get("campaign_status"),
                "today_spend_cny": promo.get("today_spend_cny"),
                "today_impressions": promo.get("today_impressions"),
                "today_clicks": promo.get("today_clicks"),
                "click_bid_cny": promo.get("click_bid_cny"),
            }
            for promo in promotions
        ]
        total_spend = sum(
            float(row["today_spend_cny"])
            for row in rows
            if isinstance(row.get("today_spend_cny"), (int, float))
        )
        return {
            "available": True,
            "campaign_count": len(rows),
            "today_total_spend_cny": round(total_spend, 2),
            "rows": rows,
            "caliber_note": "计划名即投放时段；今日花费/曝光/点击为列表页当前口径，成交金额只在 ad_pyramid 全天总量。",
        }
    return {"available": False, "summary": "本次未读取金字塔推广计划数据集。"}


def enrich_ebk_dataset_read_response(result: dict[str, Any]) -> dict[str, Any]:
    dataset_results = result.get("dataset_results") if isinstance(result.get("dataset_results"), list) else []
    requested_dataset_ids = result.get("requested_dataset_ids") if isinstance(result.get("requested_dataset_ids"), list) else []
    metric_rows = _metric_rows_from_dataset_results(dataset_results)
    dataset_read_rows = _dataset_read_rows_from_results(dataset_results)
    dataset_summaries = _build_dataset_summaries(dataset_results)
    operator = result.get("operator_summary") if isinstance(result.get("operator_summary"), dict) else {}
    multi_dataset = len(requested_dataset_ids) > 1
    card_kind = "ebk_datasets_read" if dataset_read_rows or multi_dataset or len(requested_dataset_ids) != 1 else "beneficial_data"
    if card_kind == "beneficial_data" and not metric_rows:
        card_kind = "ebk_datasets_read" if dataset_read_rows else "beneficial_data"
    analysis_note = None
    if multi_dataset:
        labels = [
            str((dataset_summaries.get(dataset_id) or {}).get("label") or _dataset_label(dataset_id, []))
            for dataset_id in requested_dataset_ids
        ]
        analysis_note = (
            f"用户一次问了 {len(requested_dataset_ids)} 个 EBK 模块（{'、'.join(labels)}）。"
            "请先读完 operator_summary.dataset_summaries 里每个数据集，再综合给结论与建议；"
            "禁止只答其中一个模块或漏掉读取失败的数据集。"
        )
    enriched = {
        **result,
        "operator_summary": {
            **operator,
            "evidence": [
                {"label": row["field"], "value": row["value"], "kind": "metric"}
                for row in metric_rows[:6]
                if row.get("field") and row.get("value") is not None
            ]
            or [
                {"label": row["field"], "value": row["value"], "kind": "metric"}
                for row in dataset_read_rows[:6]
                if row.get("field") and row.get("value") is not None
            ],
            "beneficial_metrics": metric_rows,
            "dataset_read_rows": dataset_read_rows,
            "dataset_summaries": dataset_summaries,
            "promotion_registered_summary": _build_promotion_registered_summary(dataset_results),
            "promotion_effect_summary": _build_promotion_effect_summary(dataset_results),
            "ad_pyramid_summary": _build_ad_pyramid_summary(dataset_results),
            "pyramid_bid_summary": _build_pyramid_bid_summary(dataset_results),
            "preferred_feishu_card_kind": card_kind,
            "dataset_read_note": EBK_DATASET_CATALOG_NOTE,
            "multi_dataset_analysis_note": analysis_note,
            "operator_reply_note": analysis_note,
        },
    }
    return normalize_operator_summary(attach_feishu_card(enriched, card_kind), tool_name="read_ebk_datasets")


def _ebk_platform_account_is_configured(platform_account_id: str | None) -> bool:
    account_id = str(platform_account_id or "").strip()
    return bool(account_id) and not account_id.startswith("__")


def _build_read_ebk_datasets_latest_fallback(
    *,
    key: str,
    snapshot: dict[str, Any],
    metric_keys: list[str] | None,
    browser_read_error: str | None = None,
) -> dict[str, Any] | None:
    enriched = enrich_ebk_latest_response(snapshot)
    operator = enriched.get("operator_summary") if isinstance(enriched.get("operator_summary"), dict) else {}
    beneficial_metrics = [
        row for row in (operator.get("beneficial_metrics") or []) if isinstance(row, dict)
    ]
    if not beneficial_metrics:
        return None

    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    captured_at = run.get("captured_at")
    prefix = ""
    if browser_read_error:
        prefix = f"浏览器现读暂时不可用（{browser_read_error}），"
    metric_text = "；".join(
        f"{row.get('field')}：{row.get('value')}"
        for row in beneficial_metrics[:5]
        if row.get("field") and row.get("value") is not None
    )
    captured_label = str(captured_at or "时间未知")
    return attach_feishu_card(
        {
            "status": "success",
            "source": "persisted_latest_fallback",
            "summary": (
                f"{prefix}本次浏览器现读失败，以下是历史落库快照（抓取时间 {captured_label}，"
                f"非刚现读，可能已过期）：{metric_text}。"
            ),
            "watch_kind": WATCH_KIND_EBK,
            "watch_group_key": key,
            "requested_metrics": metric_keys or [],
            "latest_captured_at": captured_at,
            "operator_summary": {
                **operator,
                "watch_kind": WATCH_KIND_EBK,
                "watch_kind_label": watch_kind_label(WATCH_KIND_EBK),
                "watch_group_key": key,
                "latest_run_status": run.get("status"),
                "latest_captured_at": captured_at,
                "beneficial_metrics": beneficial_metrics,
                "beneficial_summary": (
                    f"{prefix}重要：本数据是历史落库快照（抓取时间 {captured_label}），"
                    "本次浏览器现读未成功，数据可能已过期，回复时必须明确告知用户这是"
                    "「最近一次落库的旧数据（{captured_label}）」，禁止说成「刚现读的」。"
                    "如需最新数据，请先修复浏览器现读后重新触发读取。"
                ),
                "routing_hints": ["run_ebk_watch_once", "get_ebk_latest_result"],
            },
        },
        "beneficial_data",
    )


def _parse_persisted_timestamp(value: Any):
    """解析后端落库时间戳；无时区后缀的按北京墙上时间处理。

    后端 ``local_now()`` 落库的是北京墙上时间（无 tzinfo），只有带
    ``Z``/``+00:00`` 后缀的才是 UTC。历史上无后缀时间被当 UTC，转北京
    时间后凭空多 8 小时（早 09:04 显示成 17:04），必须区分。
    """
    from datetime import datetime, timedelta, timezone

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed


def _persisted_data_is_stale(*, captured_at: Any, has_data: bool, stale_hours: int) -> bool:
    if not has_data:
        return True
    parsed = _parse_persisted_timestamp(captured_at)
    if parsed is None:
        return True
    from datetime import datetime, timedelta, timezone

    age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return age > timedelta(hours=stale_hours)


def _competitor_data_is_stale(*, captured_at: Any, has_prices: bool) -> bool:
    return _persisted_data_is_stale(
        captured_at=captured_at,
        has_data=has_prices,
        stale_hours=STALE_COMPETITOR_DATA_HOURS,
    )


def _persisted_freshness_hints(
    *,
    captured_at: Any,
    has_data: bool,
    stale_hours: int,
    data_label: str,
    run_tool_name: str,
    empty_prompt: str,
) -> dict[str, Any]:
    stale = _persisted_data_is_stale(captured_at=captured_at, has_data=has_data, stale_hours=stale_hours)
    captured_display = _format_overview_time(captured_at)
    if not has_data:
        prompt = empty_prompt
    elif stale:
        prompt = (
            f"持久化{data_label}读取时间为 {captured_display}，可能不是最新。"
            f"如需现读浏览器，请说「要最新的 / 立即读一遍」；确认后调用 {run_tool_name}。"
        )
    else:
        prompt = (
            f"以下为持久化{data_label}，读取时间 {captured_display}。"
            f"若仍要现读浏览器，请明确说「立即读 / 要最新的」。"
        )
    return {
        "data_captured_at_display": captured_display,
        "data_is_stale": stale,
        "suggest_immediate_run": stale,
        "stale_data_prompt": prompt,
    }


def _competitor_freshness_hints(
    *,
    captured_at: Any,
    has_prices: bool,
) -> dict[str, Any]:
    return _persisted_freshness_hints(
        captured_at=captured_at,
        has_data=has_prices,
        stale_hours=STALE_COMPETITOR_DATA_HOURS,
        data_label="竞品价格",
        run_tool_name="run_competitor_watch_once",
        empty_prompt="当前没有可用的持久化竞品价格。如需现读浏览器，请说「立即读竞品价格」或确认后我会执行。",
    )


def _ebk_freshness_hints(
    *,
    captured_at: Any,
    has_data: bool,
) -> dict[str, Any]:
    return _persisted_freshness_hints(
        captured_at=captured_at,
        has_data=has_data,
        stale_hours=STALE_EBK_DATA_HOURS,
        data_label="EBK 数据",
        run_tool_name="run_ebk_watch_once",
        empty_prompt="当前没有可用的持久化 EBK 读取结果。如需现读浏览器，请说「立即读 EBK」或确认后我会执行。",
    )


def _calendar_price_amount_text(value: Any) -> str:
    if value is None:
        return "暂无可信价"
    try:
        return f"{int(value)}元"
    except (TypeError, ValueError):
        return str(value)


def _calendar_hotel_price_text(item: dict[str, Any], *, fallback_name: str) -> str:
    name = item.get("hotel_alias") or item.get("hotel_name") or fallback_name
    price = _calendar_price_amount_text(item.get("price_amount_yuan"))
    status = str(item.get("status") or "").strip()
    status_suffix = f"（{status}）" if status and status != "success" else ""
    return f"{name}: {price}{status_suffix}"


def _compact_calendar_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": run.get("status"),
        "summary": run.get("summary"),
        "source": run.get("source"),
        "captured_at": run.get("captured_at"),
    }


def _calendar_date_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_dates = matrix.get("dates") if isinstance(matrix.get("dates"), list) else []
    for date_item in raw_dates:
        if not isinstance(date_item, dict):
            continue
        checkin_date = str(date_item.get("checkin_date") or "未知日期")
        own = date_item.get("own") if isinstance(date_item.get("own"), dict) else {}
        competitors = [
            item
            for item in date_item.get("competitors", [])
            if isinstance(item, dict)
        ]
        parts = []
        if own:
            parts.append("本店 " + _calendar_hotel_price_text(own, fallback_name="本店"))
        if competitors:
            parts.append(
                "竞品 "
                + "；".join(
                    _calendar_hotel_price_text(item, fallback_name=f"竞品{index + 1}")
                    for index, item in enumerate(competitors[:4])
                )
            )
        alerts = date_item.get("alerts") if isinstance(date_item.get("alerts"), list) else []
        if alerts:
            parts.append(f"提醒 {len(alerts)} 条")
        rows.append(
            {
                "field": f"{checkin_date} 日历价",
                "value": "；".join(parts) if parts else "暂无可信日历价",
            }
        )
    return rows[:15]


def _attach_calendar_price_card(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    status = str(payload.get("status") or run.get("status") or "unknown")
    rows = operator.get("calendar_rows") if isinstance(operator.get("calendar_rows"), list) else []
    intro_lines = [
        f"**读取时间**：{_format_overview_time(operator.get('latest_captured_at') or run.get('captured_at'))}",
        f"**口径**：{operator.get('caliber_note') or '未来日历价监测，只读展示，不触发调价。'}",
    ]
    alert_count = operator.get("alert_count")
    if alert_count is not None:
        intro_lines.append(f"**提醒日期**：{alert_count}")
    return {
        **payload,
        "feishu_card": build_generic_operator_card(
            title="日历价监测",
            status=status,
            intro="\n".join(intro_lines),
            rows=rows,
        ),
        "feishu_card_kind": "calendar_price_latest",
    }


def _calendar_status_text(value: Any) -> str:
    text = str(value or "").strip()
    return text or "unknown"


def _attach_calendar_price_status_card(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    rows = operator.get("status_rows") if isinstance(operator.get("status_rows"), list) else []
    status = str(operator.get("latest_run_status") or payload.get("status") or "success")
    intro = "\n".join(
        [
            f"**监控组**：{operator.get('watch_group_key') or '—'}",
            f"**上次读取**：{_format_overview_time(operator.get('latest_captured_at'))}",
            "**口径**：日历价状态来自 calendar-price-watch 配置、最新读取记录和调度器心跳，只读展示。",
        ]
    )
    return {
        **payload,
        "feishu_card": build_generic_operator_card(
            title="日历价状态",
            status=status,
            intro=intro,
            rows=rows,
        ),
        "feishu_card_kind": "calendar_price_status",
    }


def enrich_calendar_price_watch_status_response(
    *,
    config: dict[str, Any],
    latest_matrix: dict[str, Any] | None,
    scheduler: dict[str, Any],
) -> dict[str, Any]:
    matrix = latest_matrix if isinstance(latest_matrix, dict) else {}
    run = matrix.get("run") if isinstance(matrix.get("run"), dict) else {}
    watch_group_key = config.get("watch_group_key") or matrix.get("watch_group_key")
    latest_captured_at = run.get("captured_at") or config.get("last_polled_at")
    interval = config.get("schedule_interval_minutes")
    future_days = config.get("future_days")
    auto_poll_enabled = bool(config.get("auto_poll_enabled"))
    scheduler_status = _calendar_status_text(scheduler.get("status"))
    heartbeat_status = _calendar_status_text(scheduler.get("heartbeat_status"))
    rows = [
        {"field": "自动轮询", "value": "已开启" if auto_poll_enabled else "未开启"},
        {"field": "轮询间隔", "value": f"{interval} 分钟" if interval else "未配置"},
        {"field": "读取天数", "value": f"未来 {future_days or 15} 天"},
        {"field": "上次读取", "value": _format_overview_time(latest_captured_at)},
        {"field": "调度器", "value": f"{scheduler_status} / {heartbeat_status}"},
    ]
    enriched = {
        "status": run.get("status") or matrix.get("status") or "success",
        "config": config,
        "latest_run": _compact_calendar_run(run),
        "scheduler": scheduler,
        "operator_summary": {
            "watch_kind": WATCH_KIND_COMPETITOR,
            "watch_kind_label": watch_kind_label(WATCH_KIND_COMPETITOR),
            "watch_group_key": watch_group_key,
            "module": "calendar_price_watch",
            "status_kind": "calendar_price_watch_status",
            "auto_poll_enabled": auto_poll_enabled,
            "schedule_interval_minutes": interval,
            "future_days": future_days,
            "latest_run_status": run.get("status") or matrix.get("status"),
            "latest_captured_at": latest_captured_at,
            "scheduler_status": scheduler_status,
            "scheduler_heartbeat_status": heartbeat_status,
            "status_rows": rows,
            "usage_note": (
                "日历价状态问题使用 get_calendar_price_watch_status；读取独立的 "
                "calendar-price-watch 配置，不创建调价审批或 OTA 写入。"
            ),
        },
    }
    return _attach_calendar_price_status_card(enriched)


def enrich_calendar_price_latest_response(matrix: dict[str, Any]) -> dict[str, Any]:
    run = matrix.get("run") if isinstance(matrix.get("run"), dict) else {}
    rows = _calendar_date_rows(matrix)
    raw_dates = matrix.get("dates") if isinstance(matrix.get("dates"), list) else []
    counted_alerts = sum(
        len(item.get("alerts") or [])
        for item in raw_dates
        if isinstance(item, dict) and isinstance(item.get("alerts"), list)
    )
    alert_count = matrix.get("alert_count")
    if not isinstance(alert_count, int):
        alert_count = counted_alerts
    future_days = matrix.get("future_days")
    enriched = {
        **matrix,
        "run": _compact_calendar_run(run),
        "operator_summary": {
            "watch_kind": WATCH_KIND_COMPETITOR,
            "watch_kind_label": watch_kind_label(WATCH_KIND_COMPETITOR),
            "watch_group_key": matrix.get("watch_group_key"),
            "module": "calendar_price_watch",
            "conclusion": "已读取最新日历价矩阵。",
            "latest_run_status": run.get("status") or matrix.get("status"),
            "latest_captured_at": run.get("captured_at"),
            "future_days": future_days,
            "alert_count": alert_count,
            "calendar_rows": rows,
            "trusted_source": matrix.get("trusted_source") or "calendar_dom_cell_price",
            "caliber_note": "日历价来自携程公开页 DOM 日历单元格可信价格，只读展示，不触发调价审批或 OTA 写入。",
            "usage_note": (
                "日历价问题默认读取已持久化最新矩阵；用户明确说要最新、刷新、立即读或跑一次日历价时，"
                "才调用 run_calendar_price_watch_once。该工具默认 push_to_feishu=false，不创建调价审批。"
            ),
        },
    }
    return normalize_operator_summary(_attach_calendar_price_card(enriched), tool_name="get_calendar_price_latest")


def enrich_competitor_latest_response(snapshot: dict[str, Any]) -> dict[str, Any]:
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    price_rows: list[dict[str, Any]] = []
    for hotel in snapshot.get("hotels") if isinstance(snapshot.get("hotels"), list) else []:
        if not isinstance(hotel, dict):
            continue
        hotel_config = hotel.get("hotel_config") if isinstance(hotel.get("hotel_config"), dict) else {}
        hotel_name = hotel_config.get("display_name") or hotel_config.get("alias") or "竞品酒店"
        snapshots = hotel.get("snapshots") if isinstance(hotel.get("snapshots"), dict) else {}
        for date_label, snap in snapshots.items():
            if not isinstance(snap, dict):
                continue
            for rate in snap.get("rates") if isinstance(snap.get("rates"), list) else []:
                if not isinstance(rate, dict) or not rate.get("is_trusted"):
                    continue
                breakfast = rate.get("breakfast_text") or rate.get("breakfast_kind") or "—"
                price_rows.append(
                    {
                        "field": f"{hotel_name} · {date_label}",
                        "value": f"{rate.get('room_type') or '房型'} / {breakfast} / ¥{rate.get('price_amount_yuan')}",
                    }
                )
    freshness = _competitor_freshness_hints(
        captured_at=run.get("captured_at"),
        has_prices=bool(price_rows),
    )
    latest_poll_run = snapshot.get("latest_poll_run") if isinstance(snapshot.get("latest_poll_run"), dict) else None
    operator_note = str(snapshot.get("operator_note") or "").strip()
    enriched = {
        **snapshot,
        "operator_summary": {
            "watch_kind": WATCH_KIND_COMPETITOR,
            "watch_kind_label": watch_kind_label(WATCH_KIND_COMPETITOR),
            "watch_group_key": snapshot.get("watch_group_key"),
            "latest_run_status": run.get("status"),
            "latest_captured_at": run.get("captured_at"),
            "price_rows": price_rows[:12],
            "operator_note": operator_note or None,
            "latest_poll_run_status": latest_poll_run.get("status") if latest_poll_run else None,
            "latest_poll_captured_at": latest_poll_run.get("captured_at") if latest_poll_run else None,
            **freshness,
            "usage_note": (
                "竞品查价默认只读 /latest 持久化结果，不要误用 run_ebk_watch_once。"
                "用户确认要最新价或明确要求「立即读/现爬/跑一次竞品盯盘」时，"
                "再调用 run_competitor_watch_once。"
            ),
        },
    }
    return normalize_operator_summary(attach_feishu_card(enriched, "competitor_latest"), tool_name="get_competitor_latest_prices")


def _trend_series_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("series")
    if not isinstance(raw, list):
        raw = payload.get("trend_series")
    if not isinstance(raw, list):
        raw = payload.get("items")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _trend_point_price(point: dict[str, Any]) -> int | None:
    for key in ("price_amount_yuan", "price", "amount", "value"):
        value = point.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _trend_point_time(point: dict[str, Any]) -> str | None:
    for key in ("captured_at", "read_time", "timestamp", "time"):
        value = point.get(key)
        if value:
            return str(value)
    return None


def _price_trend_label(*, date_label: Any, breakfast_kind: Any) -> str:
    date_text = {"today": "今日", "tomorrow": "明日"}.get(str(date_label or ""), str(date_label or ""))
    breakfast_text = {
        "none": "无早",
        "no_breakfast": "无早",
        "included": "含早",
        "with_breakfast": "含早",
        "breakfast": "含早",
    }.get(str(breakfast_kind or ""), str(breakfast_kind or ""))
    label = "".join(part for part in (date_text, breakfast_text) if part)
    return label or "价格"


def enrich_competitor_price_trend_response(payload: dict[str, Any], *, days: int) -> dict[str, Any]:
    from client.competitor_trend_model import extract_series_timeline, summarize_price_process

    rows: list[dict[str, Any]] = []
    latest_captured_at: str | None = None
    for series in _trend_series_items(payload):
        timeline = extract_series_timeline(series)
        if not timeline:
            continue
        prices = [price for _, price, _ in timeline]
        latest_price = timeline[-1][1]
        latest_label = timeline[-1][2]
        if latest_label:
            latest_captured_at = latest_label
        hotel_name = series.get("hotel_name") or series.get("hotel_display_name") or series.get("display_name") or "竞品酒店"
        room_type = series.get("room_type") or "房型未识别"
        label = _price_trend_label(
            date_label=series.get("date_label"),
            breakfast_kind=series.get("breakfast_kind") or series.get("breakfast"),
        )
        process_text = summarize_price_process(timeline)
        rows.append(
            {
                "hotel": str(hotel_name),
                "stay": label,
                "room_type": str(room_type),
                "observation_count": len(prices),
                "price_min": f"{min(prices)}元",
                "price_max": f"{max(prices)}元",
                "latest_price": f"{latest_price}元",
                "latest_at": latest_label,
                "process": process_text,
                "field": f"{hotel_name} {label}",
                "value": f"{room_type}：{process_text}",
                "latest_captured_at": latest_label,
            }
        )
    point_count = sum(
        len(series.get("points") or [])
        for series in _trend_series_items(payload)
        if isinstance(series.get("points"), list)
    )
    enriched = {
        **payload,
        "operator_summary": {
            "watch_kind": WATCH_KIND_COMPETITOR,
            "watch_kind_label": watch_kind_label(WATCH_KIND_COMPETITOR),
            "watch_group_key": payload.get("watch_group_key"),
            "days": days,
            "intro": f"最近{days}天竞品价格趋势，共 {point_count} 个可信读价点；折线图按每次成功读价时间展示变化过程。",
            "caliber_note": "与控制台「可信 DOM 房价趋势」一致：展示区间内每次读价轨迹，不是把 N 天前与今天做两点对比。",
            "trend_rows": rows[:12],
            "latest_captured_at": latest_captured_at,
            "usage_note": (
                "历史趋势使用竞品 price-trend 接口；用户问最近几天/历史/趋势/曲线时用本工具，"
                "用户问是否触发提醒或有没有明显变化时用 analyze_competitor_price_change。"
            ),
        },
    }
    return attach_feishu_card(enriched, "competitor_price_trend")


def enrich_competitor_run_response(
    run_result: dict[str, Any],
    *,
    latest_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(run_result.get("status") or "unknown")
    summary = str(run_result.get("summary") or "")
    watch_group_key = run_result.get("watch_group_key")
    if status == "success" and isinstance(latest_snapshot, dict):
        enriched_latest = enrich_competitor_latest_response(latest_snapshot)
        operator = enriched_latest.get("operator_summary")
        if isinstance(operator, dict):
            operator = {
                **operator,
                "source": "browser_run_now",
                "data_is_stale": False,
                "suggest_immediate_run": False,
                "stale_data_prompt": None,
                "run_summary": summary or "已完成一次竞品价格读取。",
            }
            enriched_latest["operator_summary"] = operator
        enriched_latest["run_triggered"] = True
        enriched_latest["run_result"] = {
            "status": status,
            "summary": summary,
            "feishu_push": run_result.get("feishu_push"),
        }
        return enriched_latest
    return {
        **run_result,
        "run_triggered": True,
        "operator_summary": {
            "watch_kind": WATCH_KIND_COMPETITOR,
            "watch_kind_label": watch_kind_label(WATCH_KIND_COMPETITOR),
            "watch_group_key": watch_group_key,
            "run_status": status,
            "summary": summary,
            "next_action": run_result.get("next_action"),
            "usage_note": (
                "即时竞品读取失败或未入库时，不要编造价格。"
                "human_required 表示需人工处理浏览器登录或 profile 占用。"
            ),
        },
    }


def _format_module_status(module: dict[str, Any]) -> str:
    parts = [str(module.get("freshness_status") or "unknown")]
    age = module.get("heartbeat_age_seconds")
    if isinstance(age, (int, float)):
        parts.append(f"{int(age)}秒前心跳")
    tick = module.get("last_tick_summary") if isinstance(module.get("last_tick_summary"), dict) else {}
    failed = tick.get("last_failed_count")
    success = tick.get("last_success_count")
    if failed:
        parts.append(f"上次 tick 失败 {failed} 次")
    elif success:
        parts.append(f"上次 tick 成功 {success} 次")
    keys = tick.get("last_watch_group_keys")
    if isinstance(keys, list) and keys:
        parts.append(f"组: {', '.join(str(item) for item in keys[:2])}")
    last_error = module.get("last_error")
    if last_error:
        parts.append(f"错误: {str(last_error)[:60]}")
    managed_by = module.get("managed_by")
    if managed_by:
        parts.append(f"接管: {managed_by}")
    tick_note = module.get("tick_health_note")
    if tick_note:
        parts.append(str(tick_note)[:80])
    return " · ".join(parts)


def enrich_runtime_health_response(health: dict[str, Any]) -> dict[str, Any]:
    summary = health.get("summary") if isinstance(health.get("summary"), dict) else {}
    modules = health.get("modules") if isinstance(health.get("modules"), dict) else {}
    module_rows: list[dict[str, Any]] = []
    notes: list[str] = []
    for module in modules.values():
        if not isinstance(module, dict):
            continue
        module_rows.append(
            {
                "field": module.get("display_name") or module.get("module_key"),
                "value": _format_module_status(module),
            }
        )
        tick = module.get("last_tick_summary") if isinstance(module.get("last_tick_summary"), dict) else {}
        if tick.get("last_failed_count"):
            name = module.get("display_name") or module.get("module_key")
            notes.append(f"{name} 上次 tick 有 {tick.get('last_failed_count')} 次失败，但当前心跳仍可用。")
        tick_note = module.get("tick_health_note")
        if tick_note:
            notes.append(str(tick_note))
    enriched = {
        **health,
        "operator_summary": {
            "overall_status": summary.get("status"),
            "attention_modules": summary.get("attention_modules") or [],
            "module_rows": module_rows,
            "notes": notes,
            "latest_captured_at": health.get("checked_at"),
            "usage_note": (
                "feishu_listener 在 Hermes 模式下为 managed_external，不代表飞书入口异常。"
                "enabled=false 只影响定时轮询，不影响 /latest 已持久化数据。"
            ),
        },
    }
    mobile_summary = build_status_mobile_summary(enriched)
    enriched["mobile_summary"] = mobile_summary
    enriched["mobile_evidence"] = [
        {
            "evidence_ref": "ev_status_ok",
            "source": "ops",
            "label": "服务状态",
            "value": str(mobile_summary.get("conclusion") or summary.get("status") or "正常"),
            "unit": "",
            "captured_at": health.get("checked_at"),
            "freshness": mobile_summary.get("freshness") or "unknown",
            "trust": "trusted",
            "priority": 20,
        }
    ]
    return attach_feishu_card(enriched, "scheduler_status")


def enrich_price_change_response(payload: dict[str, Any]) -> dict[str, Any]:
    alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else payload
    changes = alert.get("changed_items") if isinstance(alert.get("changed_items"), list) else []
    change_rows: list[dict[str, Any]] = []
    for change in changes[:8]:
        if not isinstance(change, dict):
            continue
        hotel = change.get("hotel_name") or change.get("hotel_alias") or "竞品"
        direction = change.get("direction") or change.get("change_direction") or "变化"
        amount = change.get("change_amount_yuan") or change.get("change_amount")
        change_rows.append(
            {
                "field": str(hotel),
                "value": f"{direction} {amount if amount is not None else '—'}",
            }
        )
    existing_operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    enriched = {
        **payload,
        "operator_summary": {
            "alert_status": alert.get("status"),
            "summary": alert.get("summary") or alert.get("headline"),
            "alert_direction": alert.get("alert_direction") or alert.get("direction"),
            "change_rows": change_rows,
            "latest_captured_at": existing_operator.get("latest_captured_at"),
            "risk": {
                "level": "attention" if (alert.get("alert_direction") or alert.get("direction")) == "increase" else "none",
                "flags": ["竞品涨价仅提醒"] if (alert.get("alert_direction") or alert.get("direction")) == "increase" else [],
                "note": "",
            },
            "usage_note": "只读分析记录；对比相邻两次成功竞品轮询快照，不是按任意时段筛选。竞品涨价仅告警，不生成涨价建议或审批卡。",
        },
    }
    mobile_summary = build_alert_mobile_summary(enriched)
    enriched["mobile_summary"] = mobile_summary
    enriched["mobile_evidence"] = [
        {
            "evidence_ref": str(ref),
            "source": "ctrip_competitor",
            "label": "竞品价格变化",
            "value": str(alert.get("summary") or alert.get("headline") or "竞品价格变化"),
            "unit": "",
            "captured_at": enriched["operator_summary"].get("latest_captured_at"),
            "freshness": mobile_summary.get("freshness") or "unknown",
            "trust": "trusted",
            "priority": 5,
        }
        for ref in (mobile_summary.get("evidence_refs") or ["ev_alert_1"])
    ]
    return normalize_operator_summary(attach_feishu_card(enriched, "price_change"), tool_name="analyze_competitor_price_change")


def _overview_next_actions(
    *,
    overall_status: str,
    attention_modules: list[Any],
    ebk_summary: dict[str, Any],
    competitor_summary: dict[str, Any],
    alert_summary: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if overall_status != "fresh":
        actions.append("盯盘服务需关注，可 @机器人 问「服务正常吗」查看详情。")
    review = ebk_summary.get("review_summary") if isinstance(ebk_summary.get("review_summary"), dict) else {}
    if not review.get("available"):
        actions.append("暂无最新点评数据，可在 EBK 页手动读取并推送。")
    if not competitor_summary.get("price_rows"):
        actions.append("暂无最新竞品价格，请确认竞品轮询是否开启。")
    alert_status = str(alert_summary.get("alert_status") or "")
    if alert_status in {"suggestion_ready", "change_detected"}:
        actions.append("竞品价格有变化提醒，请在飞书群查看告警或调价审批卡。")
    if attention_modules:
        actions.append("如有读价失败，请运行 .\\scripts\\restart-worker.ps1 或联系运维。")
    if not actions:
        actions.append("当前数据正常，如需改价可说「把今天某房型调到420」。")
    return actions[:3]


def enrich_hotel_overview_response(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    rows = operator.get("overview_rows") if isinstance(operator.get("overview_rows"), list) else []
    status = str(operator.get("overall_status") or "unknown")
    intro_lines = [
        f"**检查时间**：{_format_overview_time(payload.get('checked_at'))}",
    ]
    next_actions = operator.get("next_actions") if isinstance(operator.get("next_actions"), list) else []
    if next_actions:
        intro_lines.append("**建议下一步**")
        intro_lines.extend(f"- {_escape_overview_line(str(item))}" for item in next_actions[:3])
    enriched = {
        **payload,
        "operator_summary": operator,
    }
    card_payload = {
        **enriched,
        "operator_summary": {
            **operator,
            "intro": "\n".join(intro_lines),
            "overview_rows": rows,
        },
    }
    return normalize_operator_summary(attach_feishu_card(card_payload, "hotel_overview"), tool_name="get_operator_hotel_overview")


def _format_overview_time(value: Any) -> str:
    if not value:
        return "—"
    text = str(value).strip()
    if not text:
        return "—"
    parsed = _parse_persisted_timestamp(text)
    if parsed is None:
        return text
    from datetime import timedelta, timezone

    shanghai = timezone(timedelta(hours=8))
    return parsed.astimezone(shanghai).strftime("%Y-%m-%d %H:%M")


def _escape_overview_line(value: str) -> str:
    import html

    return html.escape(value, quote=False)


def _missing_overview_section(summary: str) -> dict[str, Any]:
    return {
        "operator_summary": {
            "summary": summary,
            "review_summary": {"available": False},
            "beneficial_metrics": [],
            "price_rows": [],
            "alert_status": "config_required",
        },
        "summary": {"status": "config_required"},
    }


def build_hotel_overview_sections(
    *,
    health: dict[str, Any],
    ebk: dict[str, Any],
    competitor: dict[str, Any],
    price_change: dict[str, Any],
) -> dict[str, Any]:
    health_summary = health.get("operator_summary") if isinstance(health.get("operator_summary"), dict) else {}
    ebk_summary = ebk.get("operator_summary") if isinstance(ebk.get("operator_summary"), dict) else {}
    competitor_summary = (
        competitor.get("operator_summary") if isinstance(competitor.get("operator_summary"), dict) else {}
    )
    alert_summary = (
        price_change.get("operator_summary") if isinstance(price_change.get("operator_summary"), dict) else {}
    )
    review = ebk_summary.get("review_summary") if isinstance(ebk_summary.get("review_summary"), dict) else {}
    rows: list[dict[str, Any]] = []
    overall_status = str(health_summary.get("overall_status") or "unknown")
    rows.append({"field": "盯盘状态", "value": overall_status})
    if review.get("available"):
        rows.append({"field": "昨日好评", "value": review.get("positive_reviews_yesterday") or "—"})
        rows.append({"field": "昨日差评", "value": review.get("negative_reviews_yesterday") or "—"})
    else:
        rows.append({"field": "点评", "value": "暂无最新数据"})
    for metric in (ebk_summary.get("beneficial_metrics") or [])[:2]:
        if isinstance(metric, dict) and metric.get("field"):
            rows.append({"field": str(metric["field"]), "value": metric.get("value") or "—"})
    for price_row in (competitor_summary.get("price_rows") or [])[:3]:
        if isinstance(price_row, dict):
            rows.append({"field": str(price_row.get("field") or "竞品"), "value": price_row.get("value") or "—"})
    alert_text = alert_summary.get("summary") or alert_summary.get("alert_status") or "暂无新变化"
    rows.append({"field": "价格变化", "value": str(alert_text)[:120]})
    ebk_at = ebk_summary.get("latest_captured_at")
    comp_at = competitor_summary.get("latest_captured_at")
    if ebk_at or comp_at:
        rows.append(
            {
                "field": "数据时间",
                "value": f"EBK {_format_overview_time(ebk_at)} / 竞品 {_format_overview_time(comp_at)}",
            }
        )
    attention_modules = health_summary.get("attention_modules") if isinstance(health_summary.get("attention_modules"), list) else []
    return {
        "checked_at": health.get("checked_at"),
        "operator_summary": {
            "overall_status": overall_status,
            "attention_modules": attention_modules,
            "overview_rows": rows,
            "next_actions": _overview_next_actions(
                overall_status=overall_status,
                attention_modules=attention_modules,
                ebk_summary=ebk_summary,
                competitor_summary=competitor_summary,
                alert_summary=alert_summary,
            ),
            "usage_note": (
                "本总览只读已持久化数据，不触发浏览器。"
                "好评口径为昨日，不是今日实时新增。"
            ),
        },
    }

def _snapshot_from_read_result(platform: str, result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else None
    if payload is None:
        return None
    return {
        "id": result.get("snapshot_id"),
        "platform": platform,
        "platform_account_id": payload.get("platform_account_id"),
        "browser_session_id": payload.get("browser_session_id"),
        "status": result.get("status") or payload.get("status"),
        "dataset_count": result.get("dataset_count"),
        "created_at": payload.get("read_at"),
        "payload_json": payload,
    }


# P6 收口：resolve_ebk_ota_account_ids（客户端品牌词模糊匹配）已退役——三平台
# 账号解析统一走后端 /ops/feishu-chat-context 的 ota_ebk_accounts（店铺绑定后
# 按 session.shop_key 精确过滤），见 pan_hotel_mcp/server.py 联合分析工具。


_MOUNTED_WATCH_GROUP_ROUTES = {
    "meituan": "/api/v1/meituan-ebk-watch/watch-groups",
    "fliggy": "/api/v1/fliggy-ebk-watch/watch-groups",
}


async def resolve_mounted_ebk_account_ids(
    client: Any,
    *,
    ctrip_watch_group_key: str,
) -> dict[str, str | None]:
    """按携程 EBK 锚点组解析挂载的美团/飞猪监控组账号（推送同款店铺链路）。

    店名品牌词匹配不到平台账号时（如「云滨立瑞美团ebk」不含于店名
    「云州澜江路立瑞酒店」）用此兜底：飞书群绑定的携程 EBK 组即店铺锚点，
    挂载其上的平台监控组就是该店铺的账号。任何请求异常按该平台解析为
    None 返回，不阻断主流程。
    """
    anchor = str(ctrip_watch_group_key or "").strip()
    resolved: dict[str, str | None] = {"meituan": None, "fliggy": None}
    if not anchor:
        return resolved
    for platform, route in _MOUNTED_WATCH_GROUP_ROUTES.items():
        try:
            payload = await client.get_json(route)
        except Exception:  # noqa: BLE001 - 兜底路径失败不影响主解析
            continue
        groups = payload.get("watch_groups") if isinstance(payload, dict) else None
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            if str(group.get("ctrip_watch_group_key") or "").strip() != anchor:
                continue
            account_id = str(group.get("platform_account_id") or "").strip()
            if account_id and not account_id.startswith("__"):
                resolved[platform] = account_id
                break
    return resolved


def _context_watch_group_keys(
    context: dict[str, Any],
    *,
    groups_field: str,
    resolution_field: str,
    store_hint: str | None,
) -> list[str]:
    keys: list[str] = []
    groups = context.get(groups_field) if isinstance(context.get(groups_field), list) else []
    for group in groups:
        if not isinstance(group, dict):
            continue
        key = str(group.get("group_key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    resolved_key = str(resolution.get(resolution_field) or "").strip()
    if resolution.get("status") == "resolved" and resolved_key and resolved_key not in keys:
        keys.append(resolved_key)
    if str(store_hint or "").strip() and resolution.get("status") == "resolved":
        return [resolved_key] if resolved_key else []
    return keys


def _scoped_configuration_error(
    context: dict[str, Any] | None,
    *,
    action: str,
) -> dict[str, Any]:
    source = context if isinstance(context, dict) else {}
    source_operator = (
        source.get("operator_summary")
        if isinstance(source.get("operator_summary"), dict)
        else {}
    )
    source_status = str(source.get("status") or "")
    status = (
        "session_context_required"
        if source_status == "session_context_required" or not source
        else "config_required"
    )
    summary = str(
        source.get("summary")
        or source_operator.get("intro")
        or f"当前飞书群没有可用于{action}的监控组绑定。"
    )
    return {
        "status": status,
        "summary": summary,
        "binding_mode": source.get("binding_mode") or "unknown",
        "operator_summary": {
            "intro": summary,
            "routing_hints": list(source_operator.get("routing_hints") or []),
        },
    }


def _scoped_runtime_health(health: dict[str, Any]) -> dict[str, Any]:
    safe_module_labels = {
        "backend_api": "Backend API",
        "ctrip_ebooking_data_center_scheduler": "EBK 数据中心轮询",
        "competitor_price_scheduler": "竞品价格轮询",
        "feishu_listener": "飞书入口",
    }
    summary = health.get("summary") if isinstance(health.get("summary"), dict) else {}
    operator = (
        health.get("operator_summary")
        if isinstance(health.get("operator_summary"), dict)
        else {}
    )
    overall_status = str(
        operator.get("overall_status") or summary.get("status") or "unknown"
    )
    attention_modules = [
        safe_module_labels[str(item)]
        for item in (operator.get("attention_modules") or summary.get("attention_modules") or [])
        if str(item) in safe_module_labels
    ]
    module_rows: list[dict[str, Any]] = []
    modules = health.get("modules") if isinstance(health.get("modules"), dict) else {}
    for module_key, module in modules.items():
        normalized_module_key = str(module_key)
        if not isinstance(module, dict) or normalized_module_key not in safe_module_labels:
            continue
        module_rows.append(
            {
                "field": safe_module_labels[normalized_module_key],
                "value": str(
                    module.get("freshness_status")
                    or module.get("heartbeat_status")
                    or overall_status
                ),
            }
        )
    if not module_rows:
        source_rows = operator.get("module_rows") if isinstance(operator.get("module_rows"), list) else []
        module_rows = [
            {"field": f"运行模块 {index}", "value": overall_status}
            for index, row in enumerate(source_rows, start=1)
            if isinstance(row, dict)
        ]
    return {
        "checked_at": health.get("checked_at"),
        "summary": {
            "status": overall_status,
            "attention_modules": attention_modules,
        },
        "operator_summary": {
            "overall_status": overall_status,
            "attention_modules": attention_modules,
            "module_rows": module_rows,
        },
    }


class WatchApiClient:
    """Thin HTTP client for pan-hotel-watch public /api/v1 endpoints."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_API_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or os.environ.get("WATCH_API_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.timeout = timeout

    def _client_timeout(self, override: float | None) -> float:
        return override if override is not None else self.timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        service_token = os.environ.get("WATCH_API_SHARED_SECRET", "").strip()
        if service_token:
            headers["X-Pan-Hotel-Service-Token"] = service_token
        # V5 Step 2 — supervisor API key for cross-profile analytics. Only sent
        # when WATCH_API_SUPERVISOR_KEY is configured; the backend
        # /analytics/hotel-overview route is supervisor-gated and re-resolves the
        # per-user scope from the chat_id passed in the query string.
        supervisor_key = os.environ.get("WATCH_API_SUPERVISOR_KEY", "").strip()
        if supervisor_key:
            headers["X-Pan-Hotel-API-Key"] = supervisor_key
        return headers

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._client_timeout(timeout), trust_env=False) as client:
            response = await client.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"data": payload}

    async def post_json(
        self,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._client_timeout(timeout), trust_env=False) as client:
            response = await client.post(
                f"{self.base_url}{path}",
                json=body or {},
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"data": payload}

    async def patch_json(self, path: str, *, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.patch(
                f"{self.base_url}{path}",
                json=body,
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"data": payload}

    async def put_json(
        self,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.put(
                f"{self.base_url}{path}",
                json=body or {},
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"data": payload}

    async def delete_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.delete(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"data": payload}

    async def _finalize_latest_with_pending(
        self,
        payload: dict[str, Any],
        *,
        chat_id: str | None,
        operator_id: str | None,
        watch_kind: str,
        watch_group_key: str | None,
    ) -> dict[str, Any]:
        if payload.get("status") == "invalid_arguments":
            return payload
        await register_stale_immediate_run_offer(
            self,
            payload=payload,
            chat_id=chat_id,
            operator_id=operator_id,
            watch_kind=watch_kind,
            watch_group_key=watch_group_key,
        )
        return await attach_pending_operator_action(
            self,
            payload,
            chat_id=chat_id,
            operator_id=operator_id,
        )

    async def forward_feishu_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/v1/feishu/events", body=payload)

    async def reply_operator_feishu_card(
        self,
        *,
        message_id: str,
        card: dict[str, Any],
        chat_id: str | None = None,
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"message_id": message_id, "card": card}
        if chat_id:
            body["chat_id"] = chat_id
        if fallback_text:
            body["fallback_text"] = fallback_text
        return await self.post_json("/api/v1/feishu/operator-card-replies", body=body)

    async def send_operator_feishu_card(
        self,
        *,
        chat_id: str,
        card: dict[str, Any],
        store_hint: str | None = None,
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"chat_id": chat_id, "card": card}
        if store_hint:
            body["store_hint"] = store_hint
        if fallback_text:
            body["fallback_text"] = fallback_text
        return await self.post_json("/api/v1/feishu/operator-card-sends", body=body)

    async def send_operator_feishu_file(
        self,
        *,
        chat_id: str,
        doc_type: str,
        data_base64: str,
        title: str = "",
        filename: str = "",
        store_hint: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "doc_type": doc_type,
            "data_base64": data_base64,
        }
        if title:
            body["title"] = title
        if filename:
            body["filename"] = filename
        if store_hint:
            body["store_hint"] = store_hint
        return await self.post_json("/api/v1/feishu/operator-file-sends", body=body)

    async def runtime_health(self) -> dict[str, Any]:
        health = await self.get_json("/api/v1/ops/runtime-health")
        return enrich_runtime_health_response(health)

    async def get_operator_hotel_overview(
        self,
        *,
        ebk_watch_group_key: str | None = None,
        competitor_watch_group_key: str | None = None,
        allow_default_groups: bool = True,
    ) -> dict[str, Any]:
        health = await self.runtime_health()
        if allow_default_groups or ebk_watch_group_key:
            ebk = await self.get_ebk_latest(ebk_watch_group_key)
        else:
            ebk = _missing_overview_section("EBK 数据中心监控组未绑定，EBK 经营数据暂不可用。")
        if allow_default_groups or competitor_watch_group_key:
            competitor = await self.get_competitor_latest(competitor_watch_group_key)
            price_change = await self.get_price_change_alert_latest(competitor_watch_group_key)
        else:
            competitor = _missing_overview_section("竞品监控组未绑定，竞品价格暂不可用。")
            price_change = _missing_overview_section("竞品监控组未绑定，价格变化暂不可用。")
        sections = build_hotel_overview_sections(
            health=_coerce_overview_section(health, "盯盘状态"),
            ebk=_coerce_overview_section(ebk, "EBK 数据"),
            competitor=_coerce_overview_section(competitor, "竞品价格"),
            price_change=_coerce_overview_section(price_change, "价格变化"),
        )
        return enrich_hotel_overview_response(sections)

    async def get_operator_analysis_context(
        self,
        *,
        chat_id: str | None,
        store_hint: str | None = None,
        ebk_watch_group_key: str | None = None,
        competitor_watch_group_key: str | None = None,
    ) -> dict[str, Any]:
        from client.feishu_store_resolver import resolve_overview_watch_groups

        keys, blocked = await resolve_overview_watch_groups(
            self,
            chat_id=chat_id,
            store_hint=store_hint,
            ebk_watch_group_key=ebk_watch_group_key,
            competitor_watch_group_key=competitor_watch_group_key,
        )
        if blocked is not None:
            return blocked

        resolved_ebk_key, resolved_competitor_key = keys
        context = await self.get_feishu_chat_context(chat_id or "", store_hint=store_hint)
        resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
        if resolution.get("status") != "resolved" and (resolved_ebk_key or resolved_competitor_key):
            context = {
                **context,
                "resolution": {
                    **resolution,
                    "status": "resolved",
                    "match_reason": "analysis_context_resolved_watch_groups",
                    "store_name": str(store_hint or "").strip() or resolution.get("store_name"),
                    "ebk_watch_group_key": resolved_ebk_key,
                    "competitor_watch_group_key": resolved_competitor_key,
                },
            }

        overview, recent = await asyncio.gather(
            self.get_operator_hotel_overview(
                ebk_watch_group_key=resolved_ebk_key,
                competitor_watch_group_key=resolved_competitor_key,
                allow_default_groups=False,
            ),
            self.get_operator_recent_activity(
                ebk_watch_group_key=resolved_ebk_key,
                competitor_watch_group_key=resolved_competitor_key,
                allow_default_groups=False,
            ),
            return_exceptions=True,
        )
        calendar_latest: dict[str, Any] | None = None
        competitor_trend: dict[str, Any] | None = None
        price_change_alert: dict[str, Any] | None = None
        if resolved_competitor_key:
            calendar_result, trend_result, alert_result = await asyncio.gather(
                self.get_calendar_price_latest(resolved_competitor_key),
                self.get_competitor_price_trend(resolved_competitor_key, days=3),
                self.get_price_change_alert_latest(resolved_competitor_key),
                return_exceptions=True,
            )
            if isinstance(calendar_result, dict):
                calendar_latest = calendar_result
            if isinstance(trend_result, dict):
                competitor_trend = trend_result
            if isinstance(alert_result, dict):
                price_change_alert = alert_result
        return build_operator_analysis_context(
            chat_context=context,
            overview=_coerce_overview_section(overview, "酒店概览"),
            recent_activity=_coerce_overview_section(recent, "最近活动"),
            calendar_latest=calendar_latest,
            competitor_trend=competitor_trend,
            price_change_alert=price_change_alert,
        )

    async def get_watch_group_catalog(
        self,
        *,
        chat_id: str,
        store_hint: str | None = None,
    ) -> dict[str, Any]:
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return _scoped_configuration_error(None, action="列出盯盘组")

        context = await self.get_feishu_chat_context(
            normalized_chat_id,
            store_hint=store_hint,
        )
        ebk_keys = _context_watch_group_keys(
            context,
            groups_field="ebk_groups",
            resolution_field="ebk_watch_group_key",
            store_hint=store_hint,
        )
        competitor_keys = _context_watch_group_keys(
            context,
            groups_field="competitor_groups",
            resolution_field="competitor_watch_group_key",
            store_hint=store_hint,
        )
        if not ebk_keys and not competitor_keys:
            return _scoped_configuration_error(context, action="列出盯盘组")

        context_ebk_groups = (
            context.get("ebk_groups") if isinstance(context.get("ebk_groups"), list) else []
        )
        context_competitor_groups = (
            context.get("competitor_groups")
            if isinstance(context.get("competitor_groups"), list)
            else []
        )
        ebk_by_key = {
            str(item.get("group_key") or "").strip(): item
            for item in context_ebk_groups
            if isinstance(item, dict) and str(item.get("group_key") or "").strip()
        }
        competitor_by_key = {
            str(item.get("group_key") or "").strip(): item
            for item in context_competitor_groups
            if isinstance(item, dict) and str(item.get("group_key") or "").strip()
        }
        payload = build_watch_group_catalog(
            ebk_groups=[ebk_by_key.get(key, {"group_key": key}) for key in ebk_keys],
            competitor_groups=[
                competitor_by_key.get(key, {"group_key": key}) for key in competitor_keys
            ],
        )
        payload.pop("defaults", None)
        payload.update(
            {
                "status": "success",
                "summary": "已返回当前飞书群授权的盯盘组。",
                "usage_note": (
                    "EBK 与竞品是两套独立监控组，key 不可混用；"
                    "仅可使用当前群返回的 group key。"
                ),
            }
        )
        return payload

    async def get_admin_watch_group_catalog(
        self,
        *,
        admin_authorized: bool = False,
    ) -> dict[str, Any]:
        if not admin_authorized:
            return {
                "status": "admin_auth_required",
                "summary": "需要明确的管理员授权才能读取全局盯盘组。",
            }
        ebk_payload = await self.get_json("/api/v1/ctrip-ebooking/data-center-watch-groups")
        competitor_payload = await self.get_json("/api/v1/ctrip-ebooking/competitor-watch-groups")
        ebk_groups = ebk_payload.get("watch_groups") if isinstance(ebk_payload.get("watch_groups"), list) else []
        competitor_groups = (
            competitor_payload.get("watch_groups")
            if isinstance(competitor_payload.get("watch_groups"), list)
            else []
        )
        payload = build_watch_group_catalog(
            ebk_groups=[item for item in ebk_groups if isinstance(item, dict)],
            competitor_groups=[item for item in competitor_groups if isinstance(item, dict)],
        )
        payload.update(
            {
                "status": "success",
                "summary": "已返回管理员全局盯盘组配置。",
            }
        )
        return payload

    async def get_feishu_chat_context(
        self,
        chat_id: str,
        *,
        store_hint: str | None = None,
        include_runtime_health: bool = False,
    ) -> dict[str, Any]:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return {
                "status": "session_context_required",
                "summary": (
                    "缺少飞书群 chat_id，无法解析本群绑定。"
                    "这不是控制台未绑定，请在飞书会话中 @机器人 提问。"
                ),
                "chat_id_configured": None,
                "binding_mode": "unknown",
            }
        params: dict[str, Any] = {"chat_id": normalized}
        hint = str(store_hint or "").strip()
        if hint:
            params["store_hint"] = hint
        payload = await self.get_json(
            "/api/v1/ops/feishu-chat-context",
            params=params,
        )
        enriched = enrich_feishu_chat_context(payload)
        if include_runtime_health:
            binding = build_chat_binding_summary(enriched)
            enriched = attach_chat_binding_to_payload(enriched, binding)
            if binding.get("chat_binding_status") == "resolved":
                enriched = attach_runtime_health_to_payload(enriched, await self.runtime_health())
        return enriched

    async def _shop_key_from_ebk_browser_sessions(self, context: dict[str, Any]) -> str:
        """旧监控组未挂 shop_key 时的兜底：本群 EBK 账号的浏览器会话店铺归属。

        取 EBK 组 browser_session_id → /api/v1/browser-sessions 的 shop_key；
        多账号归属一致才返回（跨店混挂/查不到一律回空，由上层 fail-closed）。
        """
        session_ids = [
            sid
            for sid in (
                str((group or {}).get("browser_session_id") or "").strip()
                for group in (context.get("ebk_groups") or [])
            )
            if sid
        ]
        if not session_ids:
            return ""
        try:
            payload = await self.get_json("/api/v1/browser-sessions")
        except httpx.HTTPStatusError:
            return ""
        sessions = payload.get("sessions") if isinstance(payload, dict) else payload
        if not isinstance(sessions, list):
            return ""
        shop_key_by_session = {
            str(item.get("browser_session_id") or ""): str(item.get("shop_key") or "").strip()
            for item in sessions
            if isinstance(item, dict)
        }
        keys = {shop_key_by_session.get(sid, "") for sid in session_ids}
        keys.discard("")
        return next(iter(keys)) if len(keys) == 1 else ""

    async def get_bi_daily_report(
        self,
        *,
        chat_id: str | None = None,
        store_hint: str | None = None,
        horizon_days: int = 14,
        lookback_days: int = 30,
    ) -> dict[str, Any]:
        """读经营 BI 四板块日报（只读）。chat_id 先解析本群绑定店铺，再调 bi-daily 报告。

        返回 backend 完整报告（section 裁剪在 MCP 侧做）；绑定缺失、店铺不存在
        或后台异常时返回结构化 fail-closed 载荷，不向工具层抛异常。
        """
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return {
                "status": "session_context_required",
                "summary": "缺少飞书群 chat_id，无法定位本店经营 BI 数据。",
            }
        context = await self.get_feishu_chat_context(normalized_chat_id, store_hint=store_hint)
        resolution = context.get("resolution") or {}
        shop_key = str(
            resolution.get("shop_key") or context.get("resolved_shop_key") or ""
        ).strip()
        if not shop_key:
            shop_key = await self._shop_key_from_ebk_browser_sessions(context)
        if not shop_key:
            return {
                "status": "shop_binding_missing",
                "summary": "当前飞书群未绑定店铺，无法读取经营 BI 数据；请先在控制台完成群-店铺绑定。",
            }
        try:
            return await self.get_json(
                "/api/v1/bi-daily/daily-report",
                params={
                    "shop_key": shop_key,
                    "horizon_days": int(horizon_days),
                    "lookback_days": int(lookback_days),
                },
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 404:
                return {
                    "status": "shop_not_found",
                    "summary": "绑定的店铺不存在，无法读取经营 BI 数据。",
                }
            return {
                "status": "failed",
                "summary": f"经营 BI 报告读取失败（HTTP {status_code}），请稍后重试。",
            }

    async def get_hotel_analytics_overview(
        self,
        *,
        chat_id: str,
        scope: str | None = None,
        profile_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """V5 Step 2 — cross-profile analytics overview.

        Calls the supervisor-gated backend endpoint, passing the caller's
        ``chat_id`` so the backend can resolve the per-user role and scope
        (operator → own store, supervisor → all/selected). Returns the backend
        payload as-is on success. On a 401/403/422 it returns a structured
        fail-closed payload instead of raising, so the MCP tool can surface a
        clear message to the operator.
        """

        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return {
                "status": "session_context_required",
                "summary": "缺少飞书群 chat_id，无法判定数据查询范围。",
            }
        if not os.environ.get("WATCH_API_SUPERVISOR_KEY", "").strip():
            return {
                "status": "config_required",
                "summary": "服务端未配置 WATCH_API_SUPERVISOR_KEY，跨店数据分析未启用。",
            }
        params: dict[str, Any] = {"chat_id": normalized_chat_id}
        if scope:
            params["scope"] = scope
        if profile_keys:
            params["profile_keys"] = profile_keys
        try:
            return await self.get_json(
                "/api/v1/analytics/hotel-overview",
                params=params,
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("detail") or "")
            except ValueError:
                detail = response.text[:200]
            status_code = response.status_code
            if status_code == 401:
                return {
                    "status": "config_required",
                    "summary": "supervisor 凭据无效或未配置，无法查询跨店数据。",
                    "detail": detail,
                }
            if status_code == 403:
                return {
                    "status": "permission_denied",
                    "summary": detail or "权限不足：仅 supervisor 可查询跨店数据。",
                }
            return {
                "status": "failed",
                "summary": detail or f"数据分析查询失败（HTTP {status_code}）。",
            }
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": f"数据分析查询请求失败：{type(exc).__name__}",
            }

    async def search_rag_knowledge_base(
        self,
        *,
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """V5 Step 4 — RAG knowledge-base retrieval.

        Searches the supervisor-gated RAG store and returns the top-k matching
        chunks. This is the bridge that makes the knowledge base reach Feishu
        answers: the Hermes agent calls this MCP tool (registered as
        ``rag_search``) and weaves the returned chunks into its reply. Same
        fail-closed contract as analytics (config_required / failed payloads
        instead of raising) so a missing supervisor key or empty store surfaces
        a clear message rather than an empty silent answer.
        """

        normalized_query = str(query or "").strip()
        if not normalized_query:
            return {
                "status": "config_required",
                "summary": "检索查询为空。",
            }
        if not os.environ.get("WATCH_API_SUPERVISOR_KEY", "").strip():
            return {
                "status": "config_required",
                "summary": "服务端未配置 WATCH_API_SUPERVISOR_KEY，RAG 知识库检索未启用。",
            }
        try:
            return await self.post_json(
                "/api/v1/rag/search",
                body={"query": normalized_query, "top_k": top_k},
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except ValueError:
                detail = response.text[:200]
            status_code = response.status_code
            if status_code == 401:
                return {
                    "status": "config_required",
                    "summary": "supervisor 凭据无效或未配置，RAG 检索不可用。",
                    "detail": detail,
                }
            if status_code == 422:
                return {
                    "status": "failed",
                    "summary": detail or "RAG 检索失败：向量模型未配置或查询无效。",
                }
            return {
                "status": "failed",
                "summary": detail or f"RAG 检索失败（HTTP {status_code}）。",
            }
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": f"RAG 检索请求失败：{type(exc).__name__}",
            }

    async def distill_external_source(
        self,
        *,
        source_type: str,
        content: str,
        platform: str,
        distill_prompt: str = "",
    ) -> dict[str, Any]:
        """V6 Step 2 — 触发一次性知识蒸馏（chat 提取 → 准入 → RAG 入库）。

        Hermes 只触发，不做 DOM；浏览器自动化全部封装在后端 browser service。
        """
        normalized_type = str(source_type or "").strip()
        normalized_content = str(content or "").strip()
        normalized_platform = str(platform or "").strip()
        if normalized_type not in {"text", "image_url", "video_url"}:
            return {
                "status": "invalid_arguments",
                "summary": "source_type 仅支持 text / image_url / video_url。",
            }
        if not normalized_content:
            return {"status": "invalid_arguments", "summary": "缺少蒸馏内容。"}
        if normalized_platform not in {"doubao", "qwen"}:
            return {
                "status": "invalid_arguments",
                "summary": "platform 仅支持 doubao / qwen。",
            }
        try:
            return await self.post_json(
                "/api/v1/distill/runs",
                body={
                    "source_type": normalized_type,
                    "content": normalized_content,
                    "platform": normalized_platform,
                    "distill_prompt": str(distill_prompt or "").strip(),
                },
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except ValueError:
                detail = response.text[:200]
            return {
                "status": "failed",
                "summary": f"蒸馏请求失败（HTTP {response.status_code}）：{detail[:200]}",
            }

    async def create_distill_source(
        self,
        *,
        source_type: str,
        content: str,
        platform: str,
        title: str = "",
        distill_prompt: str = "",
        schedule_interval_minutes: int = 120,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """UI-Fix-F4F — 创建定时蒸馏数据源（委托 POST /api/v1/distill/sources）。"""
        normalized_type = str(source_type or "").strip()
        normalized_content = str(content or "").strip()
        normalized_platform = str(platform or "").strip()
        if normalized_type not in {"text", "image_url", "video_url"}:
            return {
                "status": "invalid_arguments",
                "summary": "source_type 仅支持 text / image_url / video_url。",
            }
        if not normalized_content:
            return {"status": "invalid_arguments", "summary": "缺少蒸馏内容。"}
        if normalized_platform not in {"doubao", "qwen"}:
            return {
                "status": "invalid_arguments",
                "summary": "platform 仅支持 doubao / qwen。",
            }
        interval = int(schedule_interval_minutes or 0)
        if interval != 0 and interval < 30:
            # 后端会 422；提前按 30 落，避免运营口语化的过频间隔直接失败
            interval = 30
        try:
            return await self.post_json(
                "/api/v1/distill/sources",
                body={
                    "source_type": normalized_type,
                    "content": normalized_content,
                    "platform": normalized_platform,
                    "title": str(title or "").strip(),
                    "distill_prompt": str(distill_prompt or "").strip(),
                    "schedule_interval_minutes": interval,
                    "enabled": bool(enabled),
                },
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except ValueError:
                detail = response.text[:200]
            return {
                "status": "failed",
                "summary": f"创建学习源失败（HTTP {response.status_code}）：{detail[:200]}",
            }

    async def list_distill_sources(self) -> dict[str, Any]:
        """UI-Fix-F4F — 查询当前蒸馏数据源（委托 GET /api/v1/distill/sources）。"""
        try:
            return await self.get_json("/api/v1/distill/sources")
        except httpx.HTTPStatusError as exc:
            return {
                "status": "failed",
                "summary": f"查询学习源失败（HTTP {exc.response.status_code}）。",
            }
        except httpx.HTTPError:
            return {"status": "failed", "summary": "查询学习源请求失败。"}

    async def rag_search(self, *, query: str, top_k: int = 3) -> dict[str, Any]:
        """Read-only RAG retrieval available to every agent.

        This tool is context-free: it does not require a Feishu chat_id or a
        supervisor API key. The shared service token in
        :meth:`WatchApiClient._headers` authenticates the backend call when
        API auth is enforced.
        """

        normalized_query = str(query or "").strip()
        if not normalized_query:
            return {
                "status": "invalid_arguments",
                "summary": "检索查询为空。",
            }
        try:
            return await self.post_json(
                "/api/v1/rag/search",
                body={"query": normalized_query, "top_k": top_k},
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except ValueError:
                detail = response.text[:200]
            return {
                "status": "failed",
                "summary": detail or f"RAG 检索失败（HTTP {response.status_code}）。",
                "detail": detail,
            }
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": f"RAG 检索请求失败：{type(exc).__name__}",
            }

    async def search_group_archive(
        self,
        *,
        query: str = "",
        date_start: str = "",
        date_end: str = "",
        chat_id: str = "",
        message_type: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """群资料归档查询（每日实施经营数据/文档的永久积累库）。

        Context-free 只读工具：按日期区间/关键词/消息类型/群过滤群里人发
        的归档消息与附件明细；数据永久保留，后端无任何清理。
        """
        params: dict[str, Any] = {"limit": limit}
        if str(query or "").strip():
            params["query"] = str(query).strip()
        if str(date_start or "").strip():
            params["date_start"] = str(date_start).strip()
        if str(date_end or "").strip():
            params["date_end"] = str(date_end).strip()
        if str(chat_id or "").strip():
            params["chat_id"] = str(chat_id).strip()
        if str(message_type or "").strip():
            params["message_type"] = str(message_type).strip()
        try:
            return await self.get_json("/api/v1/group-archive/messages", params=params)
        except httpx.HTTPStatusError as exc:
            response = exc.response
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except ValueError:
                detail = response.text[:200]
            return {
                "status": "failed",
                "summary": detail or f"群归档查询失败（HTTP {response.status_code}）。",
                "detail": detail,
            }
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": f"群归档查询请求失败：{type(exc).__name__}",
            }

    async def get_weekly_operator_briefing(
        self,
        *,
        chat_id: str,
        store_hint: str | None = None,
    ) -> dict[str, Any]:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return {
                "status": "session_context_required",
                "summary": (
                    "缺少飞书群 chat_id，无法生成上周经营简报。"
                    "这不是控制台未绑定，请在飞书会话中 @机器人 提问。"
                ),
            }
        params: dict[str, Any] = {"chat_id": normalized}
        hint = str(store_hint or "").strip()
        if hint:
            params["store_hint"] = hint
        payload = await self.get_json("/api/v1/ops/weekly-briefing", params=params)
        return enrich_weekly_briefing(payload)

    async def get_operator_recent_activity(
        self,
        *,
        ebk_watch_group_key: str | None = None,
        competitor_watch_group_key: str | None = None,
        allow_default_groups: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if ebk_watch_group_key:
            params["ebk_watch_group_key"] = ebk_watch_group_key
        if competitor_watch_group_key:
            params["competitor_watch_group_key"] = competitor_watch_group_key
        if not allow_default_groups:
            params["allow_default_groups"] = False
        payload = await self.get_json("/api/v1/ops/operator-recent-activity", params=params or None)
        return enrich_operator_recent_activity(payload)

    async def get_operator_watch_configuration(
        self,
        *,
        chat_id: str | None = None,
        store_hint: str | None = None,
        include_runtime_health: bool = False,
    ) -> dict[str, Any]:
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return _scoped_configuration_error(None, action="读取盯盘配置")

        chat_context = await self.get_feishu_chat_context(
            normalized_chat_id,
            store_hint=store_hint,
        )
        ebk_keys = _context_watch_group_keys(
            chat_context,
            groups_field="ebk_groups",
            resolution_field="ebk_watch_group_key",
            store_hint=store_hint,
        )
        competitor_keys = _context_watch_group_keys(
            chat_context,
            groups_field="competitor_groups",
            resolution_field="competitor_watch_group_key",
            store_hint=store_hint,
        )
        if not ebk_keys and not competitor_keys:
            return _scoped_configuration_error(chat_context, action="读取盯盘配置")

        ebk_groups: list[dict[str, Any]] = []
        for key in ebk_keys:
            group_payload = await self.get_json(
                f"/api/v1/ctrip-ebooking/data-center-watch-groups/{key}"
            )
            group = (
                group_payload.get("watch_group")
                if isinstance(group_payload.get("watch_group"), dict)
                else {}
            )
            if group:
                ebk_groups.append(group)

        competitor_groups: list[dict[str, Any]] = []
        competitor_hotels_by_key: dict[str, list[dict[str, Any]]] = {}
        for key in competitor_keys:
            group_payload = await self.get_json(
                f"/api/v1/ctrip-ebooking/competitor-watch-groups/{key}"
            )
            group = (
                group_payload.get("watch_group")
                if isinstance(group_payload.get("watch_group"), dict)
                else {}
            )
            if group:
                competitor_groups.append(group)
            hotels_payload = await self.get_json(
                f"/api/v1/ctrip-ebooking/competitor-watch-groups/{key}/hotels"
            )
            hotels = (
                hotels_payload.get("hotels")
                if isinstance(hotels_payload.get("hotels"), list)
                else []
            )
            competitor_hotels_by_key[key] = [
                item for item in hotels if isinstance(item, dict)
            ]

        browser_session_ids: list[str] = []
        for group in [*ebk_groups, *competitor_groups]:
            browser_session_id = str(group.get("browser_session_id") or "").strip()
            if browser_session_id and browser_session_id not in browser_session_ids:
                browser_session_ids.append(browser_session_id)
        browser_sessions: list[dict[str, Any]] = []
        for browser_session_id in browser_session_ids:
            session_payload = await self.get_json(
                f"/api/v1/browser-sessions/{browser_session_id}"
            )
            if isinstance(session_payload, dict):
                browser_sessions.append(session_payload)

        payload = build_scoped_operator_watch_configuration(
            ebk_groups=ebk_groups,
            competitor_groups=competitor_groups,
            competitor_hotels_by_key=competitor_hotels_by_key,
            browser_sessions=browser_sessions,
        )
        binding = build_chat_binding_summary(chat_context)
        payload = attach_chat_binding_to_payload(payload, binding)
        if include_runtime_health:
            health = _scoped_runtime_health(await self.runtime_health())
            payload = attach_runtime_health_to_payload(payload, health)
        return enrich_operator_watch_configuration(payload)

    async def get_admin_operator_watch_configuration(
        self,
        *,
        admin_authorized: bool = False,
        include_runtime_health: bool = False,
    ) -> dict[str, Any]:
        if not admin_authorized:
            return {
                "status": "admin_auth_required",
                "summary": "需要明确的管理员授权才能读取全局盯盘配置。",
            }
        ebk_payload = await self.get_json("/api/v1/ctrip-ebooking/data-center-watch-groups")
        competitor_payload = await self.get_json("/api/v1/ctrip-ebooking/competitor-watch-groups")
        sessions_payload = await self.get_json("/api/v1/browser-sessions")
        ebk_groups = ebk_payload.get("watch_groups") if isinstance(ebk_payload.get("watch_groups"), list) else []
        competitor_groups = (
            competitor_payload.get("watch_groups")
            if isinstance(competitor_payload.get("watch_groups"), list)
            else []
        )
        sessions = sessions_payload.get("sessions") if isinstance(sessions_payload.get("sessions"), list) else []
        competitor_hotels_by_key: dict[str, list[dict[str, Any]]] = {}
        for group in competitor_groups:
            if not isinstance(group, dict):
                continue
            key = str(group.get("group_key") or "").strip()
            if not key:
                continue
            hotels_payload = await self.get_json(
                f"/api/v1/ctrip-ebooking/competitor-watch-groups/{key}/hotels",
            )
            hotels = hotels_payload.get("hotels") if isinstance(hotels_payload.get("hotels"), list) else []
            competitor_hotels_by_key[key] = [item for item in hotels if isinstance(item, dict)]
        payload = build_operator_watch_configuration(
            ebk_groups=[item for item in ebk_groups if isinstance(item, dict)],
            competitor_groups=[item for item in competitor_groups if isinstance(item, dict)],
            competitor_hotels_by_key=competitor_hotels_by_key,
            browser_sessions=[item for item in sessions if isinstance(item, dict)],
        )
        if include_runtime_health:
            payload = attach_runtime_health_to_payload(payload, await self.runtime_health())
        return enrich_operator_watch_configuration(payload)

    async def get_ebk_latest(
        self,
        watch_group_key: str | None,
        *,
        chat_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_EBK)
        if error is not None:
            return error
        assert key is not None
        snapshot = await self.get_json(
            f"/api/v1/ctrip-ebooking/data-center-watch-groups/{key}/latest",
        )
        payload = enrich_ebk_latest_response(snapshot)
        payload = attach_ebk_intent_card(payload, card_kind="review")
        return await self._finalize_latest_with_pending(
            payload,
            chat_id=chat_id,
            operator_id=operator_id,
            watch_kind=WATCH_KIND_EBK,
            watch_group_key=key,
        )

    async def run_ebk_watch_once(
        self,
        watch_group_key: str | None,
        *,
        chat_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_EBK)
        if error is not None:
            return error
        assert key is not None
        result = await self.post_json(
            f"/api/v1/ctrip-ebooking/data-center-watch-groups/{key}/run-now",
            params={"push_to_feishu": False, "actor_id": "hermes"},
            timeout=BROWSER_RUN_TIMEOUT,
        )
        status = str(result.get("status") or "")
        latest_snapshot = None
        if status == "success":
            await clear_pending_operator_action(self, chat_id=chat_id, operator_id=operator_id)
            latest_snapshot = await self.get_json(
                f"/api/v1/ctrip-ebooking/data-center-watch-groups/{key}/latest",
            )
        return enrich_ebk_run_response(result, latest_snapshot=latest_snapshot)

    async def read_ebk_datasets(
        self,
        *,
        watch_group_key: str | None,
        dataset_ids: list[str] | None,
        metric_keys: list[str] | None,
        actor_id: str | None,
        chat_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_EBK)
        if error is not None:
            return error
        assert key is not None
        group_payload = await self.get_json(
            f"/api/v1/ctrip-ebooking/data-center-watch-groups/{key}",
        )
        group = group_payload.get("watch_group") if isinstance(group_payload.get("watch_group"), dict) else {}
        if not group:
            return {"status": "not_found", "summary": "未找到 EBK 数据中心监控组。"}
        if not group.get("enabled", True):
            return {
                "status": "skipped",
                "summary": "EBK 数据中心监控组已停用，未触发读取。",
                "watch_kind": WATCH_KIND_EBK,
                "watch_group_key": key,
            }
        platform_account_id = str(group.get("platform_account_id") or "").strip()
        browser_session_id = str(group.get("browser_session_id") or "").strip()
        if not _ebk_platform_account_is_configured(platform_account_id):
            return {
                "status": "config_required",
                "summary": "EBK 数据中心监控组未绑定 EBK 账号，无法现读经营数据。",
                "watch_kind": WATCH_KIND_EBK,
                "watch_group_key": key,
            }
        requested_dataset_ids, invalid_dataset_ids = _resolve_ebk_read_dataset_ids(
            dataset_ids=dataset_ids,
            metric_keys=metric_keys,
        )
        if invalid_dataset_ids and not requested_dataset_ids:
            return _invalid_ebk_dataset_response(
                watch_group_key=key,
                invalid_dataset_ids=invalid_dataset_ids,
            )
        if is_ebk_review_only_dataset_read(requested_dataset_ids):
            return build_ebk_review_misroute_response(watch_group_key=key)
        configured_datasets = group.get("datasets") if isinstance(group.get("datasets"), list) else []
        dataset_results: list[dict[str, Any]] = []
        browser_read_error: str | None = None
        for dataset_id in requested_dataset_ids:
            body: dict[str, Any] = {
                "actor_id": actor_id or operator_id or "hermes",
                "dataset_id": dataset_id,
                "filters": {"platform": "总平台", "scope": dataset_id},
                "push_to_feishu": False,
            }
            if browser_session_id:
                body["browser_session_id"] = browser_session_id
            try:
                result = await self.post_json(
                    f"/api/v1/ctrip-ebooking/sessions/{platform_account_id}/read-dataset",
                    body=body,
                    timeout=BROWSER_RUN_TIMEOUT,
                )
            except httpx.HTTPError as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    browser_read_error = f"HTTP {exc.response.status_code}"
                else:
                    browser_read_error = type(exc).__name__
                dataset_results.append(
                    {
                        "dataset_id": dataset_id,
                        "dataset_label": _dataset_label(dataset_id, configured_datasets),
                        "status": "failed",
                        "summary": f"浏览器现读失败：{browser_read_error}",
                        "data": {},
                        "evidence": {},
                        "push_enabled": False,
                        "pushed": False,
                        "push_result": {"status": "skipped", "summary": "本次按需读取未发送飞书推送。"},
                    }
                )
                continue
            dataset_results.append(
                {
                    "dataset_id": dataset_id,
                    "dataset_label": _dataset_label(dataset_id, configured_datasets),
                    "status": result.get("status"),
                    "summary": result.get("summary"),
                    "captured_at": result.get("captured_at") or result.get("data", {}).get("captured_at"),
                    "data": result.get("data") if isinstance(result.get("data"), dict) else {},
                    "evidence": result.get("evidence") if isinstance(result.get("evidence"), dict) else {},
                    "push_enabled": False,
                    "pushed": False,
                    "push_result": {"status": "skipped", "summary": "本次按需读取未发送飞书推送。"},
                }
            )
        status = "success" if any(item.get("status") == "success" for item in dataset_results) else "failed"
        if status != "success":
            try:
                latest_snapshot = await self.get_json(
                    f"/api/v1/ctrip-ebooking/data-center-watch-groups/{key}/latest",
                )
                fallback = _build_read_ebk_datasets_latest_fallback(
                    key=key,
                    snapshot=latest_snapshot,
                    metric_keys=metric_keys,
                    browser_read_error=browser_read_error,
                )
            except httpx.HTTPError:
                fallback = None
            if fallback is not None:
                return fallback
        metric_rows = _metric_rows_from_dataset_results(dataset_results)
        requested_metrics = _requested_metrics_from_dataset_results(dataset_results, metric_keys)
        captured_at = next((item.get("captured_at") for item in dataset_results if item.get("captured_at")), None)
        result = {
            "status": status,
            "summary": _read_ebk_datasets_summary(
                status=status,
                metric_rows=metric_rows,
                dataset_results=dataset_results,
            ),
            "watch_kind": WATCH_KIND_EBK,
            "watch_group_key": key,
            "requested_dataset_ids": requested_dataset_ids,
            "requested_metrics": metric_keys or [],
            "requested_metrics_detail": requested_metrics,
            "dataset_results": dataset_results,
            "source": "browser_dataset_read",
            "actor_id": actor_id,
            "operator_summary": {
                "watch_kind": WATCH_KIND_EBK,
                "watch_kind_label": watch_kind_label(WATCH_KIND_EBK),
                "watch_group_key": key,
                "latest_run_status": status,
                "latest_captured_at": captured_at,
                "dataset_ids_in_latest_run": requested_dataset_ids,
                "beneficial_metrics": metric_rows,
                "beneficial_summary": (
                    "已按需现读 EBK 经营数据，未发送飞书推送，也未改变后台定时盯盘配置。"
                ),
                "requested_metrics": requested_metrics,
                "usage_note": (
                    "read_ebk_datasets 是按需现读指定 EBK 数据集；"
                    "它不启用或关闭监控组数据集，不触发 OTA 写价。"
                ),
            },
        }
        return enrich_ebk_dataset_read_response(result)

    async def get_competitor_latest(
        self,
        watch_group_key: str | None,
        *,
        chat_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_COMPETITOR)
        if error is not None:
            return error
        assert key is not None
        snapshot = await self.get_json(
            f"/api/v1/ctrip-ebooking/competitor-watch-groups/{key}/latest",
        )
        payload = enrich_competitor_latest_response(snapshot)
        return await self._finalize_latest_with_pending(
            payload,
            chat_id=chat_id,
            operator_id=operator_id,
            watch_kind=WATCH_KIND_COMPETITOR,
            watch_group_key=key,
        )

    async def get_calendar_price_latest(
        self,
        watch_group_key: str | None,
        *,
        chat_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_COMPETITOR)
        if error is not None:
            return error
        assert key is not None
        response = await self.get_json(
            f"/api/v1/calendar-price-watch/{key}/latest",
        )
        matrix = response.get("matrix") if isinstance(response.get("matrix"), dict) else response
        payload = enrich_calendar_price_latest_response(matrix)
        return await self._finalize_latest_with_pending(
            payload,
            chat_id=chat_id,
            operator_id=operator_id,
            watch_kind=WATCH_KIND_COMPETITOR,
            watch_group_key=key,
        )

    async def get_calendar_price_watch_status(
        self,
        watch_group_key: str | None,
        *,
        chat_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_COMPETITOR)
        if error is not None:
            return error
        assert key is not None
        config_response = await self.get_json(
            f"/api/v1/calendar-price-watch/{key}/config",
        )
        config = (
            config_response.get("config")
            if isinstance(config_response.get("config"), dict)
            else config_response
        )
        latest_matrix: dict[str, Any] | None = None
        try:
            latest_response = await self.get_json(
                f"/api/v1/calendar-price-watch/{key}/latest",
            )
            latest_matrix = (
                latest_response.get("matrix")
                if isinstance(latest_response.get("matrix"), dict)
                else latest_response
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        scheduler_response = await self.get_json("/api/v1/calendar-price-watch/scheduler-status")
        scheduler = (
            scheduler_response.get("scheduler")
            if isinstance(scheduler_response.get("scheduler"), dict)
            else scheduler_response
        )
        payload = enrich_calendar_price_watch_status_response(
            config=config if isinstance(config, dict) else {},
            latest_matrix=latest_matrix,
            scheduler=scheduler if isinstance(scheduler, dict) else {},
        )
        operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
        payload["operator_summary"] = {
            **operator,
            "chat_id": chat_id,
            "operator_id": operator_id,
        }
        return payload

    async def run_calendar_price_watch_once(
        self,
        watch_group_key: str | None,
        *,
        push_to_feishu: bool = False,
        chat_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_COMPETITOR)
        if error is not None:
            return error
        assert key is not None
        response = await self.post_json(
            f"/api/v1/calendar-price-watch/{key}/run-now",
            body={"actor_id": operator_id or "hermes"},
            params={"push_to_feishu": push_to_feishu},
            timeout=BROWSER_RUN_TIMEOUT,
        )
        result = response.get("result") if isinstance(response.get("result"), dict) else response
        status = str(result.get("status") or "")
        if status in {"success", "alert"}:
            await clear_pending_operator_action(self, chat_id=chat_id, operator_id=operator_id)
            latest_response = await self.get_json(
                f"/api/v1/calendar-price-watch/{key}/latest",
            )
            matrix = latest_response.get("matrix") if isinstance(latest_response.get("matrix"), dict) else latest_response
            payload = enrich_calendar_price_latest_response(matrix)
            operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
            payload["operator_summary"] = {
                **operator,
                "source": "browser_run_now",
                "data_is_stale": False,
                "suggest_immediate_run": False,
                "run_summary": result.get("summary"),
            }
            payload["run_triggered"] = True
            payload["run_result"] = {
                "status": status,
                "summary": result.get("summary"),
                "feishu_push": result.get("feishu_push"),
            }
            return payload
        return {
            **result,
            "run_triggered": True,
            "operator_summary": {
                "watch_kind": WATCH_KIND_COMPETITOR,
                "watch_kind_label": watch_kind_label(WATCH_KIND_COMPETITOR),
                "watch_group_key": key,
                "module": "calendar_price_watch",
                "run_status": status or "unknown",
                "summary": result.get("summary"),
                "next_action": result.get("next_action"),
                "usage_note": "日历价现读失败或未入库时不要编造价格；human_required 表示需要人工处理浏览器登录或风控。",
            },
        }

    async def get_competitor_price_trend(
        self,
        watch_group_key: str | None,
        *,
        days: int = 3,
        granularity: str = "run",
        date_label: str | None = None,
        breakfast_kind: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_COMPETITOR)
        if error is not None:
            return error
        assert key is not None
        safe_days = max(1, min(int(days or 3), 3650))
        params: dict[str, Any] = {"days": safe_days, "granularity": granularity or "run"}
        if date_label:
            params["date_label"] = date_label
        if breakfast_kind:
            params["breakfast_kind"] = breakfast_kind
        payload = await self.get_json(
            f"/api/v1/ctrip-ebooking/competitor-watch-groups/{key}/price-trend",
            params=params,
        )
        return enrich_competitor_price_trend_response(payload, days=safe_days)

    async def run_competitor_watch_once(
        self,
        watch_group_key: str | None,
        *,
        push_to_feishu: bool = True,
        chat_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_COMPETITOR)
        if error is not None:
            return error
        assert key is not None
        result = await self.post_json(
            f"/api/v1/ctrip-ebooking/competitor-watch-groups/{key}/run-now",
            params={"push_to_feishu": push_to_feishu},
            timeout=BROWSER_RUN_TIMEOUT,
        )
        status = str(result.get("status") or "")
        latest_snapshot = None
        if status == "success":
            await clear_pending_operator_action(self, chat_id=chat_id, operator_id=operator_id)
            latest_snapshot = await self.get_json(
                f"/api/v1/ctrip-ebooking/competitor-watch-groups/{key}/latest",
            )
        return enrich_competitor_run_response(result, latest_snapshot=latest_snapshot)

    async def get_price_change_alert_latest(self, watch_group_key: str | None) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_COMPETITOR)
        if error is not None:
            return error
        assert key is not None
        payload = await self.get_json(
            "/api/v1/price-change-alerts/latest",
            params={"watch_group_key": key},
        )
        return enrich_price_change_response(payload)

    async def _refresh_all_order_platforms(
        self,
        *,
        fetch_path: str,
        platforms: tuple[str, ...],
        chat_id: str | None,
    ) -> list[str]:
        """三平台并发现抓（尽力而为）；返回失败平台标签，不阻断汇总读取。

        单平台失败不让整个问题失败：后续 GET 仍返回其余平台快照，失败名单
        通过 refresh_fetch_failed 透出，供飞书回复如实说明数据新旧。
        后端抓取失败是 HTTP 200 + result.status=not_ready/error，需按状态判读。
        """

        async def _fetch_one(platform: str) -> str | None:
            response = await self.post_json(
                fetch_path,
                body={"platform": platform, "chat_id": chat_id},
                timeout=BROWSER_RUN_TIMEOUT,
            )
            result = response.get("result") if isinstance(response, dict) else None
            status = str(result.get("status") or "") if isinstance(result, dict) else ""
            if status and status != "ok":
                return f"{platform}: {status}"
            return None

        outcomes = await asyncio.gather(
            *(_fetch_one(platform) for platform in platforms),
            return_exceptions=True,
        )
        failures: list[str] = []
        for platform, outcome in zip(platforms, outcomes):
            if isinstance(outcome, BaseException):
                failures.append(f"{platform}: {type(outcome).__name__}")
            elif outcome:
                failures.append(outcome)
        return failures

    async def get_refund_status(
        self,
        *,
        platform: str | None = None,
        refresh: bool = False,
        chat_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """查询当日退单数量与房型分布（只读）。refresh=true 先触发抓取再读。

        传 date_from/date_to 则按业务日期范围读历史快照（不触发浏览器抓取）。
        platform 省略时并发现抓三平台再返回汇总；404（无记录）转 not_found 友好载荷。
        """
        normalized = str(platform or "").strip().lower() or None
        if normalized and normalized not in {"ctrip", "meituan", "fliggy"}:
            return {
                "status": "invalid_request",
                "summary": "platform 仅支持 ctrip、meituan 或 fliggy。",
            }
        # 历史范围模式：按业务日期范围读持久化快照，绝不触发浏览器抓取。
        if date_from or date_to:
            params: dict[str, Any] = {}
            if normalized:
                params["platform"] = normalized
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to
            return await self.get_json("/api/v1/refunds", params=params or None)
        refresh_failed: list[str] = []
        if refresh and normalized:
            await self.post_json(
                "/api/v1/refunds/fetch",
                body={"platform": normalized, "chat_id": chat_id},
                timeout=BROWSER_RUN_TIMEOUT,
            )
        elif refresh:
            refresh_failed = await self._refresh_all_order_platforms(
                fetch_path="/api/v1/refunds/fetch",
                platforms=("ctrip", "meituan", "fliggy"),
                chat_id=chat_id,
            )
        params: dict[str, Any] = {}
        if normalized:
            params["platform"] = normalized
        try:
            payload = await self.get_json(
                "/api/v1/refunds", params=params or None
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {
                    "status": "not_found",
                    "platform": normalized or "all",
                    "summary": "暂无退单记录；确认后端退款适配器开关已开启，或 refresh=true 触发抓取。",
                }
            raise
        if refresh_failed:
            payload = dict(payload)
            payload["refresh_fetch_failed"] = refresh_failed
        return payload

    async def get_today_reservations(
        self,
        *,
        platform: str | None = None,
        refresh: bool = False,
        chat_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """查询当日今日预定数量与房型分布（只读，@机器人触发）。refresh=true 先抓再读。

        传 date_from/date_to 则按业务日期范围读历史快照（不触发浏览器抓取）。
        platform 省略时并发现抓三平台再返回汇总；404（无记录）转 not_found 友好载荷。
        """
        normalized = str(platform or "").strip().lower() or None
        if normalized and normalized not in {"ctrip", "fliggy", "meituan"}:
            return {
                "status": "invalid_request",
                "summary": "platform 仅支持 ctrip、fliggy 或 meituan。",
            }
        if date_from or date_to:
            params: dict[str, Any] = {}
            if normalized:
                params["platform"] = normalized
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to
            return await self.get_json("/api/v1/reservations", params=params or None)
        refresh_failed: list[str] = []
        if refresh and normalized:
            await self.post_json(
                "/api/v1/reservations/fetch",
                body={"platform": normalized, "chat_id": chat_id},
                timeout=BROWSER_RUN_TIMEOUT,
            )
        elif refresh:
            refresh_failed = await self._refresh_all_order_platforms(
                fetch_path="/api/v1/reservations/fetch",
                platforms=("ctrip", "fliggy", "meituan"),
                chat_id=chat_id,
            )
        params: dict[str, Any] = {}
        if normalized:
            params["platform"] = normalized
        try:
            payload = await self.get_json(
                "/api/v1/reservations", params=params or None
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {
                    "status": "not_found",
                    "platform": normalized or "all",
                    "summary": "暂无今日预定记录；确认后端今日预定适配器开关已开启，或 refresh=true 触发抓取。",
                }
            raise
        if refresh_failed:
            payload = dict(payload)
            payload["refresh_fetch_failed"] = refresh_failed
        return payload

    # 预设口径（上周/上月）路由：按平台×维度选最优口径——
    # - 携程排名：页面有精确的 上周/上月 单选钮 → period 原生口径（自定义日历
    #   仅最近约33天，上月整月起始日选不到，2026-09-16 实测）；
    # - 携程/美团经营与携程金字塔：按周/按月是「本周/本月至今/过去N天滚动」→
    #   自定义区间取上周/上月完整口径；
    # - 美团排名/飞猪：无自定义控件 → 页面预设（近7天/近30天/周报，口径见 caliber）。
    # 显式 date_from/date_to 模式不查此表：全部直发后端，由后端按支持矩阵拦截。
    _PERIOD_PRESET_MODE: ClassVar[dict[str, dict[str, dict[str, str]]]] = {
        "last_week": {
            "ctrip": {
                "business": "custom",
                "ranking": "period",
                "pyramid": "custom",
                "flow": "custom",
            },
            "meituan": {"business": "custom", "ranking": "period"},
            "fliggy": {"business": "custom", "ranking": "period"},
        },
        "last_month": {
            "ctrip": {
                "business": "custom",
                "ranking": "period",
                "pyramid": "custom",
                "flow": "custom",
            },
            "meituan": {"business": "custom", "ranking": "period"},
            "fliggy": {"business": "custom", "ranking": "period"},
        },
    }

    async def get_ebk_period_read(
        self,
        *,
        platform: str | None = None,
        data_kind: str = "business",
        period: str = "week",
        chat_id: str | None = None,
        refresh: bool = False,
        date_from: str | None = None,
        date_to: str | None = None,
        range_preset: str | None = None,
    ) -> dict[str, Any]:
        """查询周/月/日维度经营或排名数据（只读，飞书问答按需触发）。

        先读当日快照；404（无当日数据=过期/未读）自动触发浏览器重读后再读。
        platform 省略时对三平台逐平台执行同一策略（只补读过期平台）。
        refresh=True 跳过快照直接现读。后端抓取失败按 HTTP 200 +
        result.status≠ok 契约判读，失败名单经 refresh_fetch_failed 透出。

        历史区间（2026-09-16 起）：range_preset（上周/上月）或 date_from/date_to
        （具体区间）直接现读平台页「自定义时间」控件，不依赖本地落库快照；
        预设口径在无自定义控件的平台（美团排名/飞猪排名）自动回落页面预设现读，
        回落项带 range_fallback=True 并以 snapshot.caliber 标注实际口径。
        """
        normalized = str(platform or "").strip().lower() or None
        if normalized and normalized not in {"ctrip", "meituan", "fliggy"}:
            return {
                "status": "invalid_request",
                "summary": "platform 仅支持 ctrip、meituan 或 fliggy。",
            }
        kind = str(data_kind or "").strip().lower()
        if kind not in {"business", "ranking", "pyramid", "flow"}:
            return {
                "status": "invalid_request",
                "summary": (
                    "data_kind 仅支持 business（经营数据）、ranking（排名数据）、"
                    "pyramid（携程金字塔投放，仅 ctrip）或 flow（携程流量数据，仅 ctrip）。"
                ),
            }
        period_key = str(period or "").strip().lower()
        if period_key not in {"day", "week", "month"}:
            return {
                "status": "invalid_request",
                "summary": "period 仅支持 day（日）、week（周）或 month（月）。",
            }
        preset = str(range_preset or "").strip().lower() or None
        if preset and preset not in {"last_week", "last_month"}:
            return {
                "status": "invalid_request",
                "summary": "range_preset 仅支持 last_week（上周）或 last_month（上月）。",
            }

        # 历史区间模式：平台页自定义时间现读，不读本地落库。
        range_mode: str | None = None
        start = end = None
        if preset:
            start, end = _range_preset_dates(preset)
            range_mode = f"preset_{preset}"
        elif str(date_from or "").strip() or str(date_to or "").strip():
            start = str(date_from or date_to).strip()
            end = str(date_to or date_from).strip()
            range_mode = "custom"
        if range_mode:
            platforms = (normalized,) if normalized else ("ctrip", "meituan", "fliggy")

            async def _range_one(target: str) -> dict[str, Any]:
                if range_mode == "custom":
                    use_custom = True
                else:
                    use_custom = (
                        self._PERIOD_PRESET_MODE.get(preset or "", {})
                        .get(target, {})
                        .get(kind)
                        == "custom"
                    )
                if range_mode == "custom" or use_custom:
                    body: dict[str, Any] = {
                        "platform": target,
                        "data_kind": kind,
                        "period": period_key,
                        "date_from": start,
                        "date_to": end,
                    }
                    if chat_id:
                        body["chat_id"] = chat_id
                    try:
                        response = await self.post_json(
                            "/api/v1/ebk-period-reads/fetch",
                            body=body,
                            timeout=BROWSER_RUN_TIMEOUT,
                        )
                    except Exception as exc:  # noqa: BLE001 - 单平台失败不阻断汇总
                        return {
                            "platform": target,
                            "status": "error",
                            "reason": f"{type(exc).__name__}: {str(exc)[:120]}",
                        }
                    result = response.get("result") if isinstance(response, dict) else None
                    if not isinstance(result, dict):
                        return {"platform": target, "status": "error", "reason": "后端返回缺失 result。"}
                    return {"platform": target, **result}
                # 预设口径回落：无自定义控件的平台按页面预设（近7天/近30天/周报）现读。
                try:
                    failed = await self._fetch_period_one(target, kind, period_key, chat_id)
                except Exception as exc:  # noqa: BLE001 - 单平台失败不阻断汇总
                    return {
                        "platform": target,
                        "status": "error",
                        "range_fallback": True,
                        "reason": f"{type(exc).__name__}: {str(exc)[:120]}",
                    }
                if failed:
                    return {
                        "platform": target,
                        "status": "error",
                        "range_fallback": True,
                        "reason": failed,
                    }
                read_params: dict[str, Any] = {
                    "platform": target,
                    "data_kind": kind,
                    "period": period_key,
                }
                if chat_id:
                    read_params["chat_id"] = chat_id
                try:
                    payload = await self.get_json("/api/v1/ebk-period-reads", params=read_params)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        return {"platform": target, "status": "not_found", "range_fallback": True}
                    raise
                snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
                if not isinstance(snapshot, dict):
                    return {"platform": target, "status": "not_found", "range_fallback": True}
                return {
                    "platform": target,
                    "status": "ok",
                    "range_fallback": True,
                    "caliber": snapshot.get("caliber"),
                    "summary": f"已按平台预设口径现读（{snapshot.get('caliber') or ''}）。",
                    "sections": (snapshot.get("payload") or {}).get("sections")
                    if isinstance(snapshot.get("payload"), dict)
                    else None,
                }

            outcomes = await asyncio.gather(*(_range_one(t) for t in platforms))
            results = [dict(item) for item in outcomes]
            ok_count = sum(1 for item in results if item.get("status") == "ok")
            overall = (
                "ok" if results and ok_count == len(results)
                else ("partial" if ok_count else "not_available")
            )
            summary = (
                f"已按 {start}~{end} 实时读取平台数据（{ok_count}/{len(results)} 个维度成功）。"
                if ok_count
                else f"{start}~{end} 区间实时读取失败，详见各平台 reason。"
            )
            if any(item.get("range_fallback") for item in results):
                summary += "带 range_fallback 的平台为页面预设口径（无自定义时间控件）。"
            return {
                "status": overall,
                "platform": normalized or "all",
                "data_kind": kind,
                "period": period_key,
                "range_mode": range_mode,
                "date_range": {"from": start, "to": end},
                "results": results,
                "summary": summary,
            }

        platforms: tuple[str, ...] = (
            (normalized,) if normalized else ("ctrip", "meituan", "fliggy")
        )
        snapshots: dict[str, dict[str, Any]] = {}
        stale: list[str] = []

        async def _read_one(target: str) -> None:
            try:
                read_params: dict[str, Any] = {
                    "platform": target,
                    "data_kind": kind,
                    "period": period_key,
                }
                if chat_id:
                    # 多店铺快照并存：按群绑定的店铺账号过滤，避免读到别店数据。
                    read_params["chat_id"] = chat_id
                payload = await self.get_json(
                    "/api/v1/ebk-period-reads",
                    params=read_params,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    stale.append(target)
                    return
                raise
            snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
            if isinstance(snapshot, dict):
                snapshots[target] = snapshot
            else:
                stale.append(target)

        await asyncio.gather(*(_read_one(target) for target in platforms))
        if refresh:
            stale = list(platforms)

        refresh_failed: list[str] = []
        if stale:
            outcomes = await asyncio.gather(
                *(self._fetch_period_one(target, kind, period_key, chat_id) for target in stale),
                return_exceptions=True,
            )
            for target, outcome in zip(stale, outcomes):
                if isinstance(outcome, BaseException):
                    refresh_failed.append(f"{target}: {type(outcome).__name__}")
                elif outcome:
                    refresh_failed.append(outcome)
            for target in list(stale):
                if any(item.startswith(f"{target}:") for item in refresh_failed):
                    continue
                await _read_one(target)

        if normalized:
            payload: dict[str, Any]
            if normalized in snapshots:
                payload = {"snapshot": snapshots[normalized]}
            else:
                payload = {
                    "status": "not_found",
                    "platform": normalized,
                    "data_kind": kind,
                    "period": period_key,
                    "summary": (
                        "该维度暂无当日数据；浏览器重读未成功，请稍后重试或检查账号绑定。"
                    ),
                }
            if refresh_failed:
                payload["refresh_fetch_failed"] = refresh_failed
            return payload

        ordered = [snapshots[target] for target in platforms if target in snapshots]
        result: dict[str, Any] = {"snapshots": ordered}
        if refresh_failed:
            result["refresh_fetch_failed"] = refresh_failed
        return result

    async def _fetch_period_one(
        self,
        platform: str,
        data_kind: str,
        period: str,
        chat_id: str | None,
    ) -> str | None:
        """触发单平台周/月维度现读；返回失败标签（成功为 None）。"""
        response = await self.post_json(
            "/api/v1/ebk-period-reads/fetch",
            body={
                "platform": platform,
                "data_kind": data_kind,
                "period": period,
                "chat_id": chat_id,
            },
            timeout=BROWSER_RUN_TIMEOUT,
        )
        result = response.get("result") if isinstance(response, dict) else None
        status = str(result.get("status") or "") if isinstance(result, dict) else ""
        if status and status != "ok":
            reason = str(result.get("reason") or "").strip()
            return f"{platform}: {status}" + (f"（{reason[:80]}）" if reason else "")
        return None

    async def pending_review_count(
        self,
        *,
        platform: str,
        ctrip_watch_group_key: str | None = None,
        chat_id: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """查询某监控组某子平台未回复评价数量（只读，飞书问答用；不返回评价正文）。

        chat_id 优先透传：后端按飞书群绑定的监控组解析店铺，缺 chat_id 会
        回落平台级空配置导致问答「读取不到数据」。refresh=True 先触发一次真实
        抓取（后端按群解析店铺、拉起浏览器读取，与自动回复模式无关）再查数量；
        抓取失败不阻断（数量回落最近一次抓取记录，fetch 结果附在 refresh_fetch）。
        """
        normalized = str(platform or "").strip().lower()
        if normalized not in {"ctrip", "meituan", "fliggy"}:
            return {"status": "invalid_request", "summary": "platform 仅支持 ctrip、meituan 或 fliggy。"}
        params: dict[str, Any] = {"platform": normalized}
        if ctrip_watch_group_key:
            params["ctrip_watch_group_key"] = str(ctrip_watch_group_key).strip()
        normalized_chat_id = str(chat_id or "").strip()
        if normalized_chat_id:
            params["chat_id"] = normalized_chat_id
        refresh_fetch: dict[str, Any] | None = None
        if refresh and normalized_chat_id:
            try:
                fetch_payload = await self.post_json(
                    "/api/v1/reviews/fetch",
                    body={"platform": normalized, "chat_id": normalized_chat_id},
                    timeout=BROWSER_RUN_TIMEOUT,
                )
                fetch_result = fetch_payload.get("fetch")
                refresh_fetch = (
                    fetch_result
                    if isinstance(fetch_result, dict)
                    else {"status": str(fetch_result or "")}
                )
            except Exception as exc:  # noqa: BLE001 - 刷新失败仍返回最近数量
                refresh_fetch = {"status": "failed", "error_type": type(exc).__name__}
        payload = await self.get_json("/api/v1/reviews/pending-count", params=params)
        if refresh_fetch is not None and isinstance(payload, dict):
            payload = {**payload, "refresh_fetch": refresh_fetch}
        return payload

    async def get_latest_ebk_snapshot(
        self,
        *,
        platform: str,
        platform_account_id: str,
    ) -> dict[str, Any]:
        from client.ebk_snapshot_analysis import sanitize_ebk_analysis_value

        normalized_platform = str(platform or "").strip().lower()
        account_id = str(platform_account_id or "").strip()
        if normalized_platform not in {"ctrip", "meituan", "fliggy"}:
            return {
                "status": "invalid_request",
                "summary": "platform 仅支持 ctrip、meituan 或 fliggy。",
            }
        if not account_id:
            return {
                "status": "invalid_request",
                "summary": "platform_account_id 不能为空。",
            }
        snapshot = await self.get_json(
            "/api/v1/ebk-snapshots/latest",
            params={
                "platform": normalized_platform,
                "platform_account_id": account_id,
            },
        )
        sanitized = sanitize_ebk_analysis_value(snapshot)
        assert isinstance(sanitized, dict)
        sanitized.setdefault("status", snapshot.get("status") or "success")
        sanitized["operator_summary"] = {
            "usage_note": "只读分析与汇报；不会创建或触发 OTA 写价。",
            "platform": normalized_platform,
            "platform_account_id": account_id,
            "read_at": (
                (snapshot.get("payload_json") or {}).get("read_at")
                if isinstance(snapshot.get("payload_json"), dict)
                else None
            )
            or snapshot.get("created_at"),
        }
        return sanitized

    async def get_ebk_joint_analysis_context(
        self,
        *,
        ctrip_platform_account_id: str | None = None,
        meituan_platform_account_id: str | None = None,
        fliggy_platform_account_id: str | None = None,
        platforms: list[str] | None = None,
        refresh_before_analysis: bool = False,
        actor_id: str = "hermes",
        headless: bool = False,
    ) -> dict[str, Any]:
        from client.ebk_snapshot_analysis import build_ebk_joint_analysis_context

        requested = _resolve_ebk_analysis_platforms(
            platforms=platforms,
            ctrip_platform_account_id=ctrip_platform_account_id,
            meituan_platform_account_id=meituan_platform_account_id,
            fliggy_platform_account_id=fliggy_platform_account_id,
        )
        if requested.get("status") == "invalid_request":
            return requested

        analysis_mode = str(requested["analysis_mode"])
        ctrip_snapshot = None
        meituan_snapshot = None
        fliggy_snapshot = None
        ctrip_error = None
        meituan_error = None
        fliggy_error = None

        if refresh_before_analysis:
            read_result = await self.post_json(
                "/api/v1/ebk-snapshots/read-all",
                body={
                    "actor_id": actor_id or "hermes",
                    "ctrip_platform_account_id": requested.get("ctrip_platform_account_id"),
                    "meituan_platform_account_id": requested.get("meituan_platform_account_id"),
                    "fliggy_platform_account_id": requested.get("fliggy_platform_account_id"),
                    "platforms": requested["platforms"],
                    "headless": headless,
                    "trigger_via": "bot_analysis",
                },
                timeout=BROWSER_RUN_TIMEOUT,
            )
            results = read_result.get("results") if isinstance(read_result.get("results"), dict) else {}
            ctrip_snapshot = _snapshot_from_read_result("ctrip", results.get("ctrip"))
            meituan_snapshot = _snapshot_from_read_result("meituan", results.get("meituan"))
            fliggy_snapshot = _snapshot_from_read_result("fliggy", results.get("fliggy"))
            if "ctrip" in requested["platforms"] and ctrip_snapshot is None:
                ctrip_error = (
                    "missing_platform_account_id"
                    if not requested.get("ctrip_platform_account_id")
                    else str((results.get("ctrip") or {}).get("status") or "read_failed")
                    if isinstance(results.get("ctrip"), dict)
                    else "read_missing"
                )
            if "meituan" in requested["platforms"] and meituan_snapshot is None:
                meituan_error = (
                    "missing_platform_account_id"
                    if not requested.get("meituan_platform_account_id")
                    else str((results.get("meituan") or {}).get("status") or "read_failed")
                    if isinstance(results.get("meituan"), dict)
                    else "read_missing"
                )
            if "fliggy" in requested["platforms"] and fliggy_snapshot is None:
                fliggy_error = (
                    "missing_platform_account_id"
                    if not requested.get("fliggy_platform_account_id")
                    else str((results.get("fliggy") or {}).get("status") or "read_failed")
                    if isinstance(results.get("fliggy"), dict)
                    else "read_missing"
                )
            payload = build_ebk_joint_analysis_context(
                ctrip_snapshot=ctrip_snapshot,
                meituan_snapshot=meituan_snapshot,
                fliggy_snapshot=fliggy_snapshot,
                analysis_mode=analysis_mode,
                ctrip_error=ctrip_error,
                meituan_error=meituan_error,
                fliggy_error=fliggy_error,
            )
            operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
            payload["operator_summary"] = {
                **operator,
                "data_source": "immediate_read",
                "read_result_status": read_result.get("status"),
            }
            return payload

        if "ctrip" in requested["platforms"]:
            account_id = str(requested.get("ctrip_platform_account_id") or "").strip()
            if not account_id:
                ctrip_error = "missing_platform_account_id"
            else:
                try:
                    ctrip_snapshot = await self.get_latest_ebk_snapshot(
                        platform="ctrip",
                        platform_account_id=account_id,
                    )
                    if ctrip_snapshot.get("status") == "invalid_request":
                        ctrip_error = str(ctrip_snapshot.get("summary") or "invalid_request")
                        ctrip_snapshot = None
                except Exception as exc:  # noqa: BLE001 - map missing snapshot to partial analysis
                    ctrip_error = type(exc).__name__
                    ctrip_snapshot = None

        if "meituan" in requested["platforms"]:
            account_id = str(requested.get("meituan_platform_account_id") or "").strip()
            if not account_id:
                meituan_error = "missing_platform_account_id"
            else:
                try:
                    meituan_snapshot = await self.get_latest_ebk_snapshot(
                        platform="meituan",
                        platform_account_id=account_id,
                    )
                    if meituan_snapshot.get("status") == "invalid_request":
                        meituan_error = str(meituan_snapshot.get("summary") or "invalid_request")
                        meituan_snapshot = None
                except Exception as exc:  # noqa: BLE001
                    meituan_error = type(exc).__name__
                    meituan_snapshot = None

        if "fliggy" in requested["platforms"]:
            account_id = str(requested.get("fliggy_platform_account_id") or "").strip()
            if not account_id:
                fliggy_error = "missing_platform_account_id"
            else:
                try:
                    fliggy_snapshot = await self.get_latest_ebk_snapshot(
                        platform="fliggy",
                        platform_account_id=account_id,
                    )
                    if fliggy_snapshot.get("status") == "invalid_request":
                        fliggy_error = str(fliggy_snapshot.get("summary") or "invalid_request")
                        fliggy_snapshot = None
                except Exception as exc:  # noqa: BLE001
                    fliggy_error = type(exc).__name__
                    fliggy_snapshot = None

        return build_ebk_joint_analysis_context(
            ctrip_snapshot=ctrip_snapshot,
            meituan_snapshot=meituan_snapshot,
            fliggy_snapshot=fliggy_snapshot,
            analysis_mode=analysis_mode,
            ctrip_error=ctrip_error,
            meituan_error=meituan_error,
            fliggy_error=fliggy_error,
        )


    async def patch_ebk_watch_group(self, watch_group_key: str | None, body: dict[str, Any]) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_EBK)
        if error is not None:
            return error
        assert key is not None
        return await self.patch_json(
            f"/api/v1/ctrip-ebooking/data-center-watch-groups/{key}",
            body=body,
        )

    async def patch_competitor_watch_group(self, watch_group_key: str | None, body: dict[str, Any]) -> dict[str, Any]:
        key, error = prepare_watch_group_key(watch_group_key, watch_kind=WATCH_KIND_COMPETITOR)
        if error is not None:
            return error
        assert key is not None
        return await self.patch_json(
            f"/api/v1/ctrip-ebooking/competitor-watch-groups/{key}",
            body=body,
        )

    async def create_manual_pricing_approval(
        self,
        *,
        room_type: str,
        date_label: str,
        target_price: int,
        watch_group_key: str | None,
        chat_id: str,
        message_id: str,
        open_id: str | None,
        store_hint: str | None = None,
        platform: str | None = None,
        checkin_date: str | None = None,
    ) -> dict[str, Any]:
        validation = validate_manual_pricing_fields(
            room_type=room_type,
            date_label=date_label,
            target_price=target_price,
            platform=platform,
            checkin_date=checkin_date,
        )
        if validation["status"] == "needs_confirmation":
            payload = attach_feishu_card(
                {
                    "status": "needs_confirmation",
                    "summary": validation["summary"],
                    "missing_fields": validation.get("missing_fields") or [],
                    "partial": validation.get("partial") or {},
                    "evidence": {
                        "approval_card_sent": False,
                        "confirmation_card_sent": False,
                        "manual_price_adjustment": True,
                    },
                    "next_action": "请按卡片示例补全房型、今天/明天和目标价后重新发送。",
                },
                "manual_price_confirmation",
            )
            card = payload.get("feishu_card")
            if isinstance(card, dict):
                reply = await self.reply_operator_feishu_card(
                    message_id=message_id,
                    chat_id=chat_id,
                    card=card,
                    fallback_text=str(validation.get("summary") or "请补充调价信息"),
                )
                payload["feishu_card_sent"] = reply.get("status") == "success"
                payload["feishu_push"] = reply
            return payload

        payload = build_manual_pricing_feishu_payload(
            room_type=str(validation["room_type"]),
            date_label=str(validation["date_label"]),
            target_price=int(validation["target_price"]),
            chat_id=chat_id,
            message_id=message_id,
            open_id=open_id,
            store_hint=store_hint,
            platform=validation.get("platform"),
            checkin_date=validation.get("checkin_date"),
        )
        result = await self.forward_feishu_event(payload)
        if watch_group_key:
            result["watch_group_key"] = watch_group_key
        return result


def _coerce_overview_section(payload: Any, label: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {
        "operator_summary": {
            "overall_status" if label == "盯盘状态" else "summary": f"{label}读取失败",
            "review_summary": {"available": False},
            "price_rows": [],
            "alert_status": "unknown",
            "summary": f"{label}暂不可用。",
        },
        "summary": {"status": "failed"},
    }


def _resolve_ebk_analysis_platforms(
    *,
    platforms: list[str] | None,
    ctrip_platform_account_id: str | None,
    meituan_platform_account_id: str | None,
    fliggy_platform_account_id: str | None,
) -> dict[str, Any]:
    ctrip_id = str(ctrip_platform_account_id or "").strip() or None
    meituan_id = str(meituan_platform_account_id or "").strip() or None
    fliggy_id = str(fliggy_platform_account_id or "").strip() or None
    if platforms:
        normalized = []
        for item in platforms:
            value = str(item or "").strip().lower()
            if value in {"both", "joint", "all"}:
                normalized.extend(["ctrip", "meituan", "fliggy"])
            elif value in {"ctrip", "meituan", "fliggy"}:
                normalized.append(value)
            else:
                return {
                    "status": "invalid_request",
                    "summary": f"不支持的 platforms 值：{item}",
                }
        selected = list(dict.fromkeys(normalized))
    elif ctrip_id or meituan_id or fliggy_id:
        selected = [
            platform
            for platform, account_id in (
                ("ctrip", ctrip_id),
                ("meituan", meituan_id),
                ("fliggy", fliggy_id),
            )
            if account_id
        ]
    else:
        return {
            "status": "invalid_request",
            "summary": "请至少提供一个平台的 platform_account_id。",
        }

    analysis_mode = "joint" if len(selected) > 1 else selected[0]
    return {
        "status": "ok",
        "analysis_mode": analysis_mode,
        "platforms": selected,
        "ctrip_platform_account_id": ctrip_id,
        "meituan_platform_account_id": meituan_id,
        "fliggy_platform_account_id": fliggy_id,
    }


def build_manual_pricing_feishu_payload(
    *,
    room_type: str,
    date_label: str,
    target_price: int,
    chat_id: str,
    message_id: str,
    open_id: str | None,
    store_hint: str | None = None,
    platform: str | None = None,
    checkin_date: str | None = None,
) -> dict[str, Any]:
    from client.manual_pricing import manual_price_platform_word

    # specified 日期合成 ISO 文本；后端 manual_price_command 的日期令牌
    # 兼容 2026-09-15 / 9月15日，ISO 不会与房型名里的数字混淆。
    if checkin_date:
        date_text = str(checkin_date).strip()
    else:
        date_text = "今天" if date_label == "today" else "明天"
    platform_word = manual_price_platform_word(platform)
    room_prefix = platform_word or ""
    command = f"把{date_text}{room_prefix}{room_type}调到{target_price}"
    normalized_store_hint = str(store_hint or "").strip() or None
    return {
        "header": {
            "event_id": f"hermes-manual-{uuid.uuid4().hex[:12]}",
            "event_type": "im.message.receive_v1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": open_id or "hermes"}},
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "store_hint": normalized_store_hint,
                "content": json.dumps({"text": command}, ensure_ascii=False),
            },
        },
    }
