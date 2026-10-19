from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")

from app.domain.ctrip_ebooking_watch.operator_text import operator_failure_reason
from app.domain.notifications.feishu_card_tables import (
    metric_block_elements,
    render_metric_group_pages,
)
from app.domain.pyramid_bidding.cards import (
    PYRAMID_BID_FIELD_HEADER,
    PYRAMID_BID_PART_HEADERS,
    pyramid_promotion_table_row,
)

# 数据集图标按模块语义匹配（此前所有数据集统一 📦，无法区分模块）。
_DATASET_ICONS: dict[str, str] = {
    "business_outline": "💰",
    "beneficial_data": "📊",
    "flow_data": "👁️",
    "competition_profile": "⚔️",
    "loss_analysis": "🔻",
    "competition_list": "🏆",
    "promotion_effect": "🎯",
    "promotion_registered": "🎁",
    "ad_pyramid": "🔺",
    "comment_monitor": "💬",
    "pyramid_bid": "💲",
}
_DATASET_ICON_FALLBACK = "📋"

_SERVICE_OVERVIEW_FIELDS: tuple[tuple[str, str], ...] = (
    ("psi_service_score", "PSI服务质量分"),
    ("hotel_review_score", "酒店点评分"),
    ("five_minute_response_rate", "5分钟回复率"),
    ("hotel_favorites", "酒店收藏数"),
)

_DAILY_BRIEF_METRIC_NOTE = (
    "指标说明：城市排名/APP访客/点评分/PSI/APP流量转化为携程数据；"
    "间夜量/平均卖价/销售额为离店维度全平台数据；APP转化率=预订订单数/详情页访客量；"
    "较前一日=今日成功读取与昨日成功读取的差值，两天均有真实读取时才展示。"
)

_DOD_METRIC_KEYS: tuple[str, ...] = ("realtime_visitor_rank", "psi_service_score")
FEISHU_MAX_TABLES_PER_CARD = 5


def build_daily_brief_push_card(
    *,
    watch_group_key: str,
    status: str,
    summary: str,
    dataset_results: list[dict[str, Any]],
    title: str = "EBK 数据简报",
    captured_at: str | datetime | None = None,
    day_over_day: dict[str, Any] | None = None,
    layout: str = "auto",
) -> dict[str, Any]:
    """对标携程官方「数据简报」的每日简报卡。

    分区：城市排名（较前一日）→ 本月经营概览（去年同期/去年同比为页面原文）→
    昨日经营 → 服务概览/点评 → APP流量转化漏斗 → 指标说明尾注。
    数据未就绪的分区整块隐藏，不做任何推算。"""
    day_over_day = day_over_day or {}
    elements: list[dict[str, Any]] = [
        _card_text(
            f"**来源**：{_escape_lark_md(watch_group_key)}\n"
            f"**结果**：{_escape_lark_md(summary)}\n"
            f"**读取时间**：{_format_read_time(captured_at)}"
        ),
    ]
    (
        city_rank_rows,
        monthly_rows,
        yesterday_rows,
        overview_rows,
        review_followup_rows,
        review_overview_rows,
        funnel_rows,
    ) = _daily_brief_section_rows(dataset_results, day_over_day)
    resolved_layout = _resolve_daily_brief_layout(
        layout,
        status=status,
        section_rows=(
            city_rank_rows,
            monthly_rows,
            yesterday_rows,
            overview_rows,
            review_followup_rows,
            review_overview_rows,
            funnel_rows,
        ),
    )
    sections = [
        ("🧭 城市排名", city_rank_rows),
        ("💼 本月经营概览（离店 · 全平台）", monthly_rows),
        ("↩️ 昨日经营", yesterday_rows),
        ("🛎️ 服务概览", overview_rows),
        ("💬 点评跟进", review_followup_rows),
        ("⭐ 点评概览", review_overview_rows),
        ("🚦 APP流量转化漏斗", funnel_rows),
    ]
    if resolved_layout == "dashboard":
        _append_daily_brief_dashboard(elements, sections)
    elif resolved_layout == "balanced":
        _append_daily_brief_balanced(elements, sections)
    else:
        _append_daily_brief_compact(elements, sections)
    elements.extend(
        [
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": _DAILY_BRIEF_METRIC_NOTE},
                ],
            },
        ]
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _card_template(status),
            "title": {"tag": "plain_text", "content": title.strip() or "EBK 数据简报"},
        },
        "elements": elements,
    }


def select_daily_brief_card_layout(
    *,
    status: str,
    dataset_results: list[dict[str, Any]],
    day_over_day: dict[str, Any] | None = None,
) -> str:
    """按运行状态、可见区块数和真实指标行数选择首发模板。"""

    sections = _daily_brief_section_rows(dataset_results, day_over_day or {})
    return _resolve_daily_brief_layout(
        "auto",
        status=status,
        section_rows=sections,
    )


def _daily_brief_section_rows(
    dataset_results: list[dict[str, Any]],
    day_over_day: dict[str, Any],
) -> tuple[list[dict[str, Any]], ...]:
    overview_rows = _brief_summary_rows(dataset_results)
    if overview_rows:
        _append_day_over_day_to_row(overview_rows, "psi_service_score", day_over_day)
    return (
        _daily_brief_city_rank_rows(dataset_results, day_over_day),
        _daily_brief_monthly_rows(dataset_results),
        _daily_brief_yesterday_rows(dataset_results),
        overview_rows,
        _review_followup_rows(dataset_results),
        _review_overview_rows(dataset_results),
        _daily_brief_funnel_rows(dataset_results),
    )


def _resolve_daily_brief_layout(
    layout: str,
    *,
    status: str,
    section_rows: tuple[list[dict[str, Any]], ...],
) -> str:
    normalized = str(layout or "auto").strip().lower()
    if normalized not in {"auto", "dashboard", "balanced", "compact"}:
        raise ValueError(f"unsupported daily brief card layout: {layout}")
    if normalized != "auto":
        return normalized
    if status != "success":
        return "compact"
    visible_section_count = sum(bool(rows) for rows in section_rows)
    visible_row_count = sum(len(rows) for rows in section_rows)
    if visible_section_count >= 5 and visible_row_count >= 12:
        return "dashboard"
    return "balanced"


def _append_daily_brief_dashboard(
    elements: list[dict[str, Any]],
    sections: list[tuple[str, list[dict[str, Any]]]],
) -> None:
    groups = [
        ("🏨 经营核心", sections[:3]),
        ("🛎️ 服务与点评", sections[3:6]),
        ("🚦 APP流量转化", sections[6:]),
    ]
    for title, grouped_sections in groups:
        rows = _prefixed_section_rows(grouped_sections)
        if not rows:
            continue
        subtitle = " · ".join(
            _section_plain_title(section_title)
            for section_title, section_rows in grouped_sections
            if section_rows
        )
        elements.append({"tag": "hr"})
        elements.append(_card_text(f"**{title}**\n{subtitle}"))
        elements.extend(_field_value_table(rows))


def _append_daily_brief_balanced(
    elements: list[dict[str, Any]],
    sections: list[tuple[str, list[dict[str, Any]]]],
) -> None:
    core_rows = _prefixed_section_rows(sections[:4])
    if core_rows:
        subtitle = " · ".join(
            _section_plain_title(title) for title, rows in sections[:4] if rows
        )
        elements.append({"tag": "hr"})
        elements.append(_card_text(f"**🏨 经营与服务速览**\n{subtitle}"))
        elements.extend(_field_value_table(core_rows))
    for title, rows in sections[4:]:
        _append_daily_brief_markdown_section(elements, title, rows)


def _append_daily_brief_compact(
    elements: list[dict[str, Any]],
    sections: list[tuple[str, list[dict[str, Any]]]],
) -> None:
    for title, rows in sections:
        _append_daily_brief_markdown_section(elements, title, rows)


def _append_daily_brief_markdown_section(
    elements: list[dict[str, Any]],
    title: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    lines = [
        f"- **{_escape_lark_md(row.get('field') or '-')}**："
        f"{_escape_lark_md(_display_value(row.get('value')))}"
        for row in rows
        if _display_value(row.get("value")) != "-"
    ]
    if not lines:
        return
    elements.extend([{"tag": "hr"}, _card_text(f"**{title}**\n" + "\n".join(lines))])


def _prefixed_section_rows(
    sections: list[tuple[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for title, section_rows in sections:
        section_label = _section_plain_title(title).split("（", 1)[0]
        for row in section_rows:
            rows.append(
                {
                    "field": f"{section_label} · {row.get('field') or '-'}",
                    "value": row.get("value"),
                }
            )
    return rows


def _section_plain_title(title: str) -> str:
    return re.sub(r"^[^\w\u3400-\u9fff]+\s*", "", title)


def compute_daily_brief_day_over_day(
    *,
    current_results: list[dict[str, Any]],
    previous_run: dict[str, Any] | None,
) -> dict[str, Any]:
    """今日成功读取 vs 前一次成功读取的同指标差值（仅城市排名/PSI）。

    两天数值都解析成功才产出；否则省略对应指标，不推算、不填 0。"""
    if not isinstance(previous_run, dict):
        return {}
    current_metrics = _business_outline_metrics(current_results)
    previous_data = previous_run.get("data") if isinstance(previous_run.get("data"), dict) else {}
    previous_metrics = _merged_business_outline_metrics(previous_data)
    day_over_day: dict[str, Any] = {}
    for key in _DOD_METRIC_KEYS:
        current_metric = current_metrics.get(key)
        previous_metric = previous_metrics.get(key)
        current_value = _plain_metric_number(current_metric)
        previous_value = _plain_metric_number(previous_metric)
        if current_value is None or previous_value is None:
            continue
        if key == "realtime_visitor_rank":
            diff_text = f"{int(round(current_value - previous_value)):+d}"
        else:
            diff_text = f"{current_value - previous_value:+.2f}"
        day_over_day[key] = {
            "previous": _metric_raw_value(previous_metric),
            "current": _metric_raw_value(current_metric),
            "diff": diff_text,
        }
    return day_over_day


def _daily_brief_city_rank_rows(
    dataset_results: list[dict[str, Any]],
    day_over_day: dict[str, Any],
) -> list[dict[str, Any]]:
    metric = _business_outline_metrics(dataset_results).get("realtime_visitor_rank")
    if not isinstance(metric, dict):
        return []
    value = metric.get("display_value") or metric.get("value")
    if value is None:
        return []
    parts = [str(value)]
    rank = metric.get("competitive_rank")
    if rank:
        parts.append(f"竞争圈排名 {rank}")
    dod = day_over_day.get("realtime_visitor_rank")
    if isinstance(dod, dict) and dod.get("diff") is not None:
        parts.append(f"较前一日 {dod.get('diff')}")
    return [{"field": "城市排名（实时）", "value": "\n".join(parts)}]


def _daily_brief_monthly_rows(dataset_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = _beneficial_monthly_metrics(dataset_results)
    rows: list[dict[str, Any]] = []
    for key in (
        "monthly_departure_room_nights",
        "monthly_departure_sales_amount",
        "monthly_average_selling_price",
    ):
        metric = metrics.get(key)
        if not isinstance(metric, dict):
            continue
        value = metric.get("display_value") or metric.get("value")
        if value is None:
            continue
        parts = [str(value)]
        if metric.get("last_year_same_period"):
            parts.append(f"去年同期 {metric.get('last_year_same_period')}")
        if metric.get("year_over_year"):
            parts.append(f"去年同比 {metric.get('year_over_year')}")
        rows.append(
            {"field": str(metric.get("label") or key).replace("本月", "", 1), "value": "\n".join(parts)}
        )
    return rows


def _daily_brief_yesterday_rows(dataset_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    outline_metrics = _business_outline_metrics(dataset_results)
    for key, fallback_label in (
        ("departure_sales_amount", "离店销售额"),
        ("departure_room_nights", "离店间夜量"),
    ):
        rows.extend(
            _metric_with_context_row(outline_metrics.get(key), fallback_label)
        )
    beneficial_metrics = _dataset_metrics(dataset_results, "beneficial_data")
    rows.extend(_metric_with_context_row(beneficial_metrics.get("booking_orders"), "预订订单数"))
    profile_metrics = _dataset_metrics(dataset_results, "competition_profile")
    for key, fallback_label in (
        ("ctrip_app_visitors", "携程APP访客"),
        ("ctrip_app_conversion_rate", "携程APP转化率"),
    ):
        rows.extend(_metric_with_context_row(profile_metrics.get(key), fallback_label))
    return rows


def _daily_brief_funnel_rows(dataset_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    profile_metrics = _dataset_metrics(dataset_results, "competition_profile")
    for key, fallback_label in (
        ("list_exposure", "列表页曝光量"),
        ("app_detail_visitors", "详情页访客量"),
        ("order_page_visitors", "订单页访客量"),
    ):
        metric = profile_metrics.get(key)
        if not isinstance(metric, dict):
            continue
        value = metric.get("display_value") or metric.get("value")
        if value is None:
            continue
        parts = [str(value)]
        if metric.get("competition_average"):
            parts.append(f"竞争圈平均 {metric.get('competition_average')}")
        if metric.get("change"):
            parts.append(str(metric.get("change")))
        rows.append({"field": fallback_label, "value": "\n".join(parts)})
    flow_metrics = _dataset_metrics(dataset_results, "flow_data")
    for key, fallback_label in (
        ("exposure_conversion_rate", "曝光转化率"),
        ("booking_conversion_rate", "下单转化率"),
        ("transaction_conversion_rate", "成交转化率"),
    ):
        metric = flow_metrics.get(key)
        if not isinstance(metric, dict):
            continue
        value = metric.get("display_value") or metric.get("value")
        if value is None:
            continue
        parts = [str(value)]
        if metric.get("competition_average"):
            parts.append(f"竞争圈平均 {metric.get('competition_average')}")
        rows.append({"field": fallback_label, "value": "\n".join(parts)})
    return rows


def _metric_with_context_row(
    metric: Any,
    fallback_label: str,
) -> list[dict[str, Any]]:
    if not isinstance(metric, dict):
        return []
    value = metric.get("display_value") or metric.get("value")
    if value is None:
        return []
    parts = [str(value)]
    if metric.get("competition_average"):
        parts.append(f"竞争圈平均 {metric.get('competition_average')}")
    if metric.get("competitive_rank"):
        parts.append(f"竞争圈排名 {metric.get('competitive_rank')}")
    label = metric.get("label") or fallback_label
    return [{"field": str(label), "value": "\n".join(parts)}]


def _append_day_over_day_to_row(
    rows: list[dict[str, Any]],
    metric_key: str,
    day_over_day: dict[str, Any],
) -> None:
    dod = day_over_day.get(metric_key)
    if not isinstance(dod, dict) or dod.get("diff") is None:
        return
    for row in rows:
        if metric_key == "psi_service_score" and "PSI" not in str(row.get("field") or ""):
            continue
        row["value"] = f"{row.get('value')}\n较前一日 {dod.get('diff')}"
        return


def _beneficial_monthly_metrics(dataset_results: list[dict[str, Any]]) -> dict[str, Any]:
    for result in dataset_results:
        if str(result.get("dataset_id") or "") != "beneficial_data":
            continue
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        monthly = data.get("monthly") if isinstance(data.get("monthly"), dict) else {}
        if monthly.get("status") != "success":
            return {}
        metrics = monthly.get("metrics")
        return metrics if isinstance(metrics, dict) else {}
    return {}


def _dataset_metrics(dataset_results: list[dict[str, Any]], dataset_id: str) -> dict[str, Any]:
    for result in dataset_results:
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if str(result.get("dataset_id") or "") != dataset_id and str(data.get("dataset") or "") != dataset_id:
            continue
        metrics = data.get("metrics")
        if isinstance(metrics, dict):
            return metrics
    return {}


def _plain_metric_number(metric: Any) -> float | None:
    if not isinstance(metric, dict):
        return None
    value = metric.get("value")
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _metric_raw_value(metric: Any) -> str | None:
    if not isinstance(metric, dict):
        return None
    value = metric.get("value")
    return str(value) if value is not None else None


def build_data_center_watch_push_text(
    *,
    watch_group_key: str,
    status: str,
    summary: str,
    dataset_results: list[dict[str, Any]],
    title: str = "携程 EBK 数据中心盯盘",
    captured_at: str | datetime | None = None,
    include_title: bool = True,
) -> str:
    success_count = _success_count(dataset_results)
    total_count = len(dataset_results)
    lines = []
    if include_title:
        lines.append(title.strip() or "携程 EBK 数据中心盯盘")
    lines.extend(
        [
        f"读取时间：{_format_read_time(captured_at)}",
        f"状态：{_status_label(status)}",
        f"数据集：{total_count}，成功：{success_count}",
        ]
    )
    brief_lines = _brief_summary_lines(dataset_results)
    if brief_lines:
        lines.append("服务概览：")
        lines.extend(f"  {line}" for line in brief_lines)
    review_followup_lines = _review_followup_lines(dataset_results)
    if review_followup_lines:
        lines.append("点评跟进：")
        lines.extend(f"  {line}" for line in review_followup_lines)
    review_overview_lines = _review_overview_lines(dataset_results)
    if review_overview_lines:
        lines.append("点评概览：")
        lines.extend(f"  {line}" for line in review_overview_lines)
    for result in dataset_results:
        label = result.get("dataset_label") or result.get("dataset_id") or "数据集"
        result_status = str(result.get("status") or "unknown")
        lines.append(f"- {label}：{_status_label(result_status)}")
        lines.extend(
            f"  {line}"
            for line in _trim_metric_lines(result, _metric_lines(result), default_limit=6)
        )
        failure_reason = operator_failure_reason(
            result.get("failure_reason"),
            status=result_status,
        )
        if failure_reason and result_status != "success":
            lines.append(f"  原因：{failure_reason}")
        if result_status == "human_required":
            lines.append("  下一步：人工完成登录、验证码、短信、二维码或风控确认后重试。")
    return "\n".join(lines)


def build_data_center_watch_push_card(
    *,
    watch_group_key: str,
    status: str,
    summary: str,
    dataset_results: list[dict[str, Any]],
    title: str = "携程 EBK 数据中心盯盘",
    captured_at: str | datetime | None = None,
) -> list[dict[str, Any]]:
    """盯盘推送卡（多张）：每个数据集独立小节 + 独立表格，全部数据入表。

    飞书单卡最多 5 个表格组件、每表 ≤10 行不翻页；数据集多、行数多时
    按页拆成多张卡片连续推送（标题带 1/N 序号），绝不截断、不回退纯文本。
    返回卡片列表（至少一张：无数据集时也返回带头部的空态卡）。
    """
    success_count = _success_count(dataset_results)
    status_icon = _status_icon(status)
    header_text = (
        f"**🏠 来源**：{_escape_lark_md(watch_group_key)}\n"
        f"**{status_icon} 状态**：{_status_label(status)} · {success_count}/{len(dataset_results)} 个数据集成功\n"
        f"**🕒 读取时间**：{_format_read_time(captured_at)}\n"
        f"**📌 结果**：{_escape_lark_md(summary)}"
    )
    pages = render_metric_group_pages(_watch_card_metric_groups(dataset_results))
    if not pages:
        return [
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": _card_template(status),
                    "title": {"tag": "plain_text", "content": "携程 EBK 数据中心盯盘"},
                },
                "elements": [_card_text(header_text)],
            }
        ]
    cards: list[dict[str, Any]] = []
    total = len(pages)
    for index, page in enumerate(pages):
        card_title = "携程 EBK 数据中心盯盘"
        if total > 1:
            card_title += f"（{index + 1}/{total}）"
        elements = [_card_text(header_text), *page]
        cards.append(
            _cap_table_elements(
                {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": _card_template(status),
                        "title": {"tag": "plain_text", "content": card_title},
                    },
                    "elements": elements,
                }
            )
        )
    return cards


def _watch_card_metric_groups(
    dataset_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """每个数据集独立成组（不跨板块合并）；标题带图标与读取状态。"""

    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    # 2026-09-10：数据集扩到 11 个（新增金字塔出价），上限同步放宽；
    # 行数/表格数仍由 render_metric_group_pages 按每卡 ≤5 表分页兜底。
    for result in dataset_results[:12]:
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        dataset_id = str(data.get("dataset") or result.get("dataset_id") or "")
        if not dataset_id or dataset_id in seen:
            continue
        seen.add(dataset_id)
        result_status = str(result.get("status") or "unknown")
        label = _dataset_label(result, dataset_id)
        icon = _DATASET_ICONS.get(dataset_id, _DATASET_ICON_FALLBACK)
        title = (
            f"{icon} {_escape_lark_md(label)}："
            f"{_status_icon(result_status)} {_status_label(result_status)}"
        )
        rows = _dataset_card_rows(result)
        if not rows:
            # 成功但解析不出任何指标：保留小节并如实说明，不让板块消失。
            rows = [{"field": "读取状态", "parts": ["已读取，但未识别出可展示指标"]}]
        group = {"title": title, "rows": rows}
        if dataset_id == "pyramid_bid" and result_status in {"success", "partial"}:
            # 金字塔出价表列契约与指令「金字塔出价」卡一致（共享常量）；
            # 仅成功态应用——失败态行是「读取状态」，不套出价列头。
            group["field_header"] = PYRAMID_BID_FIELD_HEADER
            group["part_headers"] = list(PYRAMID_BID_PART_HEADERS)
        groups.append(group)
    return groups


def _dataset_label(result: dict[str, Any], dataset_id: str) -> str:
    label = str(result.get("dataset_label") or "").strip()
    if label:
        return label
    from app.domain.ctrip_ebooking_watch.repository import DATASET_LABELS

    return str(DATASET_LABELS.get(dataset_id) or dataset_id)


def _dataset_card_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """数据集 → 表格行（字段只保留条目名；时间维度进「板块」列；状态入表）。"""

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    dataset_id = str(data.get("dataset") or result.get("dataset_id") or "")
    result_status = str(result.get("status") or "unknown")
    rows: list[dict[str, Any]] = []
    dims = data.get("time_dimensions")
    if isinstance(dims, dict) and dims and not data.get("projected_time_dimension"):
        dimension_keys: set[str] = set()
        for key, payload in dims.items():
            if not isinstance(payload, dict):
                continue
            dim_label = str(payload.get("label") or key)
            dim_status = str(payload.get("status") or "")
            if dim_status not in {"success", "partial", "needs_review"}:
                rows.append(
                    {
                        "field": f"{dim_label}档未能读取",
                        "parts": [dim_status or "unknown"],
                        "dimension": dim_label,
                    }
                )
                continue
            metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
            for metric_key, metric in metrics.items():
                row = _metric_row(metric)
                if row:
                    dimension_keys.add(metric_key)
                    row["dimension"] = dim_label
                    rows.append(row)
        # 顶层 metrics（如 PSI/点评分）不属于任何维度档，作为独立行补齐。
        top_metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        for metric_key, metric in top_metrics.items():
            if metric_key in dimension_keys:
                continue
            row = _metric_row(metric)
            if row:
                rows.append(row)
    elif dataset_id == "promotion_effect":
        rows = _promotion_effect_rows(data)
    elif dataset_id == "promotion_registered":
        rows = _promotion_registered_rows(data)
    elif dataset_id == "ad_pyramid":
        rows = _ad_pyramid_table_rows(data)
    elif dataset_id == "pyramid_bid":
        rows = _pyramid_bid_table_rows(data)
        if not rows and result_status == "success":
            # 读取成功但没有推广计划：如实展示空态，不虚构行。
            rows = [{"field": "推广计划", "parts": ["暂无金字塔推广计划"]}]
    elif str(data.get("query_type") or "") in {
        "ctrip_ebooking_comment_monitor",
        "ctrip_ebooking_review_analysis",
    }:
        rows = _comment_monitor_rows(data)
    else:
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        for metric in metrics.values():
            row = _metric_row(metric)
            if row:
                rows.append(row)
    if result_status != "success":
        parts = [_status_label(result_status)]
        failure_reason = operator_failure_reason(
            result.get("failure_reason"),
            status=result_status,
        )
        if failure_reason:
            parts.append(failure_reason)
        if result_status == "human_required":
            parts.append("下一步：人工完成登录、验证码、短信、二维码或风控确认后重试")
        rows.append({"field": "读取状态", "parts": parts})
    return rows


def _metric_row(metric: Any) -> dict[str, Any] | None:
    if not isinstance(metric, dict):
        return None
    label = metric.get("label") or metric.get("key")
    value = metric.get("display_value") or metric.get("value")
    if not label or value is None:
        return None
    parts = [str(value)]
    if metric.get("competition_average") is not None:
        parts.append(f"竞争圈平均 {metric.get('competition_average')}")
    if metric.get("competitive_rank"):
        parts.append(f"竞争圈排名 {metric.get('competitive_rank')}")
    change = metric.get("change") or metric.get("week_over_week")
    if change:
        parts.append(str(change))
    return {"field": str(label), "parts": parts}


def _comment_monitor_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """点评分析：评论概览 + 回复概览全量指标（速览合并组取消后全在此表）。"""

    monitor = data.get("comment_monitor") if isinstance(data.get("comment_monitor"), dict) else {}
    review_overview = monitor.get("review_overview") if isinstance(monitor.get("review_overview"), dict) else {}
    reply_overview = monitor.get("reply_overview") if isinstance(monitor.get("reply_overview"), dict) else {}
    rows: list[dict[str, Any]] = []
    for key in (
        "overall_score",
        "total_comment_count",
        "positive_rate",
        "image_review_rate",
        "positive_reviews_yesterday",
        "negative_reviews_yesterday",
        "positive_reviews_30d",
        "negative_reviews_30d",
    ):
        row = _metric_row(review_overview.get(key))
        if row:
            rows.append(row)
    for key in (
        "reply_rate",
        "negative_reply_rate",
        "pending_negative_reviews",
    ):
        row = _metric_row(reply_overview.get(key))
        if row:
            rows.append(row)
    return rows


def _cap_table_elements(card: dict[str, Any]) -> dict[str, Any]:
    """Keep native table elements within Feishu's card limit."""
    elements = card.get("elements") if isinstance(card.get("elements"), list) else []
    result: list[dict[str, Any]] = []
    table_count = 0
    for element in elements:
        if element.get("tag") != "table":
            result.append(element)
            continue
        table_count += 1
        if table_count <= FEISHU_MAX_TABLES_PER_CARD:
            result.append(element)
        else:
            result.append(_card_text(_table_fallback_text(element)))
    card["elements"] = result
    return card


def _table_fallback_text(table: dict[str, Any]) -> str:
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    names = [str(column.get("display_name") or column.get("name") or "") for column in columns if isinstance(column, dict)]
    lines = ["📋 " + " | ".join(names)] if names else ["📋 数据明细"]
    keys = [str(column.get("name") or "") for column in columns if isinstance(column, dict)]
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    for row in rows[:30]:
        if isinstance(row, dict):
            lines.append(" | ".join(str(row.get(key) or "") for key in keys))
    return _escape_lark_md("\n".join(lines))


def _promotion_effect_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metrics = data.get("summary_metrics")
    if not isinstance(metrics, dict):
        metrics = data.get("metrics")
    if isinstance(metrics, dict):
        for metric in metrics.values():
            if not isinstance(metric, dict):
                continue
            label = metric.get("label") or metric.get("key")
            value = metric.get("display_value") or metric.get("value")
            if not label or value is None:
                continue
            parts = [str(value)]
            if metric.get("competition_average") is not None:
                parts.append(f"竞争圈平均 {metric.get('competition_average')}")
            if metric.get("recommendation"):
                parts.append(str(metric["recommendation"]))
            rows.append({"field": str(label), "parts": parts})

    promotion_metrics = data.get("promotion_metrics") if isinstance(data.get("promotion_metrics"), list) else []
    for row in promotion_metrics:
        if not isinstance(row, dict):
            continue
        row_metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        rows.append(
            {
                "field": str(row.get("name") or "promotion"),
                "parts": [
                    f"间夜 {_display_value(row_metrics.get('room_nights'))}",
                    f"占比 {_display_value(row_metrics.get('room_night_share'))}",
                    f"收益 {_display_value(row_metrics.get('revenue'))}",
                    f"优惠 {_display_value(row_metrics.get('discount_amount'))}",
                ],
            }
        )
    return rows


def _promotion_registered_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    promotions = data.get("promotions") if isinstance(data.get("promotions"), list) else []
    visible_count = summary.get("visible_promotion_count")
    visible_count_text = visible_count if visible_count is not None else len(promotions)
    rows: list[dict[str, Any]] = [{"field": "可见活动", "parts": [str(visible_count_text)]}]
    for promotion in promotions:
        if not isinstance(promotion, dict):
            continue
        group = str(promotion.get("promotion_group") or "")
        name = str(promotion.get("name") or "未命名促销")
        title = f"{group} / {name}" if group else name
        promotion_time = promotion.get("promotion_time") or promotion.get("date_scope") or "-"
        if promotion.get("auto_extend") is True and promotion_time != "-":
            promotion_time = f"{promotion_time}（到期前自动延期）"
        elif promotion.get("auto_extend") is True:
            promotion_time = "到期前自动延期"
        parts = [
            f"房型 {_display_value(promotion.get('room_types') or promotion.get('room_scope'))}",
            f"时间 {promotion_time}",
            f"优惠 {_display_value(promotion.get('promotion_offer'))}",
        ]
        if promotion.get("room_quota") is not None:
            parts.append(f"房量 {_display_value(promotion.get('room_quota'))}")
        if promotion.get("order_reserve_fund") is not None:
            parts.append(f"订单储备金 {_display_value(promotion.get('order_reserve_fund'))}")
        rows.append({"field": title, "parts": parts})
    return rows


def _field_value_table(
    rows: list[dict[str, Any]],
    *,
    page_size: int | None = None,
    single_column: bool = False,
    remaining_tables: int | None = None,
) -> list[dict[str, Any]]:
    """指标块统一渲染（共享 feishu_card_tables），返回元素列表：

    - 超过 10 行拆成多个 ≤10 行的表格纵向堆叠（每个都不翻页）；
    - 整表横向滑动（不冻结首列）；长字段切块铺多列；
    - ``remaining_tables`` 配额耗尽后剩余行才回退一列文本。
    ``page_size`` / ``single_column`` 旧参数保留兼容、不再生效。
    """

    return metric_block_elements(
        rows,
        field_header="字段",
        value_header="内容",
        remaining_tables=remaining_tables,
        empty_text="- 暂无核心指标",
    )


def _brief_summary_lines(dataset_results: list[dict[str, Any]]) -> list[str]:
    return [
        _brief_summary_line(row)
        for row in _brief_summary_rows(dataset_results)
        if _brief_summary_line(row)
    ]


def _brief_summary_line(row: dict[str, Any]) -> str:
    field = _display_value(row.get("field"))
    value = _display_value(row.get("value"))
    if field == "-" or value == "-":
        return ""
    return f"{field}：{value}"


def _brief_summary_rows(dataset_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = _business_outline_metrics(dataset_results)
    rows: list[dict[str, Any]] = []
    for key, fallback_label in _SERVICE_OVERVIEW_FIELDS:
        metric = metrics.get(key)
        if not isinstance(metric, dict):
            continue
        label = metric.get("label") or fallback_label
        value = metric.get("display_value") or metric.get("value")
        if not label or value is None:
            continue
        parts = [str(value)]
        rank = metric.get("competitive_rank")
        if rank:
            parts.append(f"竞争圈排名 {rank}")
        rows.append({"field": str(label), "value": "\n".join(parts)})
    return rows


def _review_followup_lines(dataset_results: list[dict[str, Any]]) -> list[str]:
    return [
        _brief_summary_line(row)
        for row in _review_followup_rows(dataset_results)
        if _brief_summary_line(row)
    ]


def _review_overview_lines(dataset_results: list[dict[str, Any]]) -> list[str]:
    return [
        _brief_summary_line(row)
        for row in _review_overview_rows(dataset_results)
        if _brief_summary_line(row)
    ]


def _review_followup_rows(dataset_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    review_overview, _reply_overview = _comment_review_payloads(dataset_results)
    rows: list[dict[str, Any]] = []
    for key in ("positive_reviews_yesterday", "negative_reviews_yesterday"):
        metric = review_overview.get(key)
        if isinstance(metric, dict):
            rows.append(
                {
                    "field": metric.get("label") or key,
                    "value": metric.get("display_value") or metric.get("value"),
                }
            )
    return rows


def _review_overview_rows(dataset_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    review_overview, _ = _comment_review_payloads(dataset_results)
    rows: list[dict[str, Any]] = []
    for key in (
        "overall_score",
        "total_comment_count",
        "positive_rate",
        "image_review_rate",
        "positive_reviews_30d",
        "negative_reviews_30d",
    ):
        metric = review_overview.get(key)
        if not isinstance(metric, dict):
            continue
        value = metric.get("display_value") or metric.get("value")
        if key == "overall_score" and metric.get("competitive_rank"):
            value = f"{value}\n{metric.get('competitive_rank')}"
        rows.append({"field": metric.get("label") or key, "value": value})
    return rows


def _comment_review_payloads(
    dataset_results: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    for result in dataset_results:
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if data.get("query_type") not in {"ctrip_ebooking_review_analysis", "ctrip_ebooking_comment_monitor"}:
            continue
        monitor = data.get("comment_monitor") if isinstance(data.get("comment_monitor"), dict) else {}
        review_overview = (
            monitor.get("review_overview") if isinstance(monitor.get("review_overview"), dict) else {}
        )
        reply_overview = (
            monitor.get("reply_overview") if isinstance(monitor.get("reply_overview"), dict) else {}
        )
        return review_overview, reply_overview
    return {}, {}


def _business_outline_metrics(dataset_results: list[dict[str, Any]]) -> dict[str, Any]:
    for result in dataset_results:
        dataset_id = str(result.get("dataset_id") or "")
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if dataset_id != "business_outline" and str(data.get("dataset") or "") != "business_outline":
            continue
        metrics = data.get("metrics")
        if isinstance(metrics, dict):
            return _merged_business_outline_metrics(data)
    return {}


def _merged_business_outline_metrics(data: dict[str, Any]) -> dict[str, Any]:
    """顶层（daily 主维度）+ realtime 维度指标合并视图。

    生产结构里实时排名等 realtime 指标位于 time_dimensions.realtime.metrics，
    PSI/点评分等服务概览指标位于顶层 metrics，读取方需要统一视图。"""
    metrics = data.get("metrics")
    merged: dict[str, Any] = dict(metrics) if isinstance(metrics, dict) else {}
    dims = data.get("time_dimensions")
    if isinstance(dims, dict):
        realtime = dims.get("realtime") if isinstance(dims.get("realtime"), dict) else {}
        realtime_metrics = realtime.get("metrics")
        if isinstance(realtime_metrics, dict):
            for key, value in realtime_metrics.items():
                merged.setdefault(key, value)
    return merged


def _has_dual_time_dimensions(result: dict[str, Any]) -> bool:
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    dims = data.get("time_dimensions")
    return isinstance(dims, dict) and bool(dims) and not data.get("projected_time_dimension")


def _trim_metric_lines(
    result: dict[str, Any],
    lines: list[str],
    *,
    default_limit: int,
) -> list[str]:
    if _has_dual_time_dimensions(result):
        return lines
    return lines[:default_limit]


def _metric_lines(result: dict[str, Any]) -> list[str]:
    data = result.get("data")
    if not isinstance(data, dict):
        return []
    if data.get("query_type") in {"ctrip_ebooking_comment_monitor", "ctrip_ebooking_review_analysis"}:
        monitor = data.get("comment_monitor")
        if not isinstance(monitor, dict):
            return []
        lines = []
        review_overview = monitor.get("review_overview") if isinstance(monitor.get("review_overview"), dict) else {}
        reply_overview = monitor.get("reply_overview") if isinstance(monitor.get("reply_overview"), dict) else {}
        for key in (
            "overall_score",
            "total_comment_count",
            "positive_reviews_yesterday",
            "negative_reviews_yesterday",
        ):
            metric = review_overview.get(key)
            if isinstance(metric, dict) and metric.get("value") is not None:
                lines.append(f"{metric.get('label') or key}：{metric.get('value')}")
        for key in ("reply_rate", "pending_negative_reviews"):
            metric = reply_overview.get(key)
            if isinstance(metric, dict) and metric.get("value") is not None:
                lines.append(f"{metric.get('label') or key}：{metric.get('value')}")
        return lines
    dataset_id = str(data.get("dataset") or result.get("dataset_id") or "")
    if dataset_id == "promotion_registered":
        return _promotion_registered_lines(data)
    if dataset_id == "promotion_effect":
        return _promotion_effect_lines(data)
    if dataset_id == "ad_pyramid":
        return _ad_pyramid_lines(data)
    if dataset_id == "pyramid_bid":
        return _pyramid_bid_lines(data)
    dims = data.get("time_dimensions")
    if isinstance(dims, dict) and dims and not data.get("projected_time_dimension"):
        lines: list[str] = []
        for key, payload in dims.items():
            if not isinstance(payload, dict):
                continue
            label = payload.get("label") or key
            status = payload.get("status") or ""
            lines.append(f"【{label}】")
            if status not in {"success", "partial", "needs_review"}:
                lines.append(f"{label}档未能读取（{status}）")
                continue
            metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
            metric_count = 0
            for metric in metrics.values():
                if not isinstance(metric, dict):
                    continue
                m_label = metric.get("label") or metric.get("key")
                value = metric.get("display_value") or metric.get("value")
                if m_label and value is not None:
                    lines.append(f"{m_label}：{value}")
                    metric_count += 1
            if metric_count == 0:
                lines.append(f"{label}档暂无指标")
        return lines
    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        monitor = data.get("monitor")
        return _monitor_lines(monitor) if isinstance(monitor, dict) else []
    lines = []
    for metric in metrics.values():
        if not isinstance(metric, dict):
            continue
        label = metric.get("label") or metric.get("key")
        value = metric.get("display_value") or metric.get("value")
        if label and value is not None:
            lines.append(f"{label}：{value}")
    return lines


def _promotion_registered_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    promotions = data.get("promotions") if isinstance(data.get("promotions"), list) else []
    visible_count = summary.get("visible_promotion_count")
    lines.append(f"可见活动：{visible_count if visible_count is not None else len(promotions)}")
    for promotion in promotions[:3]:
        if not isinstance(promotion, dict):
            continue
        parts = [str(promotion.get("name") or "未命名促销")]
        group = promotion.get("promotion_group")
        if group:
            parts.append(str(group))
        status = promotion.get("status") or promotion.get("signup_status")
        if status:
            parts.append(str(status))
        room_types = promotion.get("room_types") or promotion.get("room_scope")
        if room_types:
            parts.append(f"参与房型 {room_types}")
        promotion_time = promotion.get("promotion_time") or promotion.get("date_scope")
        if not promotion_time:
            start = promotion.get("start_date")
            end = promotion.get("end_date")
            if start or end:
                promotion_time = f"{start or '?'} 至 {end or '?'}"
        if promotion_time:
            parts.append(f"促销时间 {promotion_time}")
        if promotion.get("auto_extend") is True:
            parts.append("到期前自动延期")
        if promotion.get("room_quota"):
            parts.append(f"促销房量 {promotion.get('room_quota')}")
        if promotion.get("promotion_offer"):
            parts.append(f"促销优惠 {promotion.get('promotion_offer')}")
        if promotion.get("order_reserve_fund"):
            parts.append(f"订单储备金 {promotion.get('order_reserve_fund')}")
        lines.append(" / ".join(parts))
    warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
    prompts = data.get("page_prompts") if isinstance(data.get("page_prompts"), list) else []
    for item in _operator_prompt_lines([*warnings, *prompts])[:2]:
        lines.append(f"提示：{item}")
    return lines


def _promotion_effect_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
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
                lines.append(f"{label}：{value}{suffix}")
    promotion_metrics = data.get("promotion_metrics") if isinstance(data.get("promotion_metrics"), list) else []
    for row in promotion_metrics[:2]:
        if not isinstance(row, dict):
            continue
        row_metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        parts = [str(row.get("name") or "未识别促销")]
        for key, label in (
            ("orders", "订单"),
            ("sales_amount", "销售额"),
            ("roi", "ROI"),
            ("exposure", "曝光"),
            ("room_nights", "含促销间夜"),
            ("room_night_share", "含促销间夜占比"),
            ("revenue", "含促销收益"),
            ("revenue_share", "含促销收益占比"),
            ("discount_amount", "优惠金额"),
        ):
            if row_metrics.get(key):
                parts.append(f"{label} {row_metrics[key]}")
        lines.append(" / ".join(parts))
    suggestions = data.get("suggestions") if isinstance(data.get("suggestions"), list) else []
    prompts = data.get("page_prompts") if isinstance(data.get("page_prompts"), list) else []
    for item in _operator_prompt_lines([*suggestions, *prompts])[:2]:
        lines.append(f"提示：{item}")
    return lines


def _operator_prompt_lines(items: list[Any]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text or _is_operator_prompt_noise(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        lines.append(text)
    return lines


def _is_operator_prompt_noise(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(
        re.fullmatch(
            r"(全部|生效中|进行中|已生效|待生效|未开始|已结束|已过期|已暂停|已退出|审核中|报名成功|已报名)\d+",
            compact,
        )
    )


def _ad_pyramid_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for group in _ad_pyramid_groups(data):
        label = str(group.get("label") or "-")
        metrics = group.get("metrics") if isinstance(group.get("metrics"), list) else []
        parts = [
            f"{metric.get('label') or metric.get('key')} {metric.get('value')}"
            for metric in metrics
            if isinstance(metric, dict)
            and metric.get("value") is not None
            and (metric.get("label") or metric.get("key"))
        ]
        if parts:
            lines.append(f"{label}：" + " / ".join(parts))
    return lines


def _ad_pyramid_table_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """广告金字塔：层级（流量/花费/收益…）进「板块」维度列，字段只留指标名。"""

    rows: list[dict[str, Any]] = []
    for group in _ad_pyramid_groups(data):
        group_label = str(group.get("label") or "-")
        metrics = group.get("metrics") if isinstance(group.get("metrics"), list) else []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            label = metric.get("label") or metric.get("key")
            value = metric.get("value")
            if not label or value is None:
                continue
            rows.append(
                {"field": str(label), "parts": [str(value)], "dimension": group_label}
            )
    return rows


def _pyramid_bid_table_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """金字塔出价：一行一个推广计划（列契约与指令「金字塔出价」卡共享）。"""

    promotions = (
        data.get("promotions") if isinstance(data.get("promotions"), list) else []
    )
    return [
        pyramid_promotion_table_row(item)
        for item in promotions
        if isinstance(item, dict)
    ]


def _pyramid_bid_lines(data: dict[str, Any]) -> list[str]:
    """文本兜底版：一行一个计划（卡片不可用时的 webhook fallback）。"""

    promotions = (
        data.get("promotions") if isinstance(data.get("promotions"), list) else []
    )
    lines: list[str] = []
    for item in promotions:
        if not isinstance(item, dict):
            continue
        row = pyramid_promotion_table_row(item)
        parts = row["parts"]
        lines.append(
            f"{row['field']}：出价 {parts[0]}；今日预算 {parts[1]}"
            f"（已花 {parts[2]}）；状态：{parts[3]}"
        )
    return lines


def _ad_pyramid_groups(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = data.get("ad_pyramid") if isinstance(data.get("ad_pyramid"), dict) else {}
    groups = payload.get("groups") if isinstance(payload, dict) else None
    if isinstance(groups, list):
        return [group for group in groups if isinstance(group, dict)]
    return []


def _monitor_lines(monitor: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for value in monitor.values():
        if isinstance(value, dict):
            lines.extend(_monitor_lines(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("label") and item.get("value") is not None:
                    lines.append(f"{item['label']}：{item['value']}")
    return lines


def _success_count(dataset_results: list[dict[str, Any]]) -> int:
    return sum(1 for result in dataset_results if result.get("status") == "success")


def _card_text(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _format_read_time(value: str | datetime | None) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_SHANGHAI)
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value or "").strip()
    if not text:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(_SHANGHAI)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16].replace("T", " ")


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _escape_lark_md(value: Any) -> str:
    text = html.escape(str(value), quote=False)
    return (
        text.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _status_label(status: str) -> str:
    return {
        "success": "成功",
        "partial": "部分成功",
        "failed": "读取失败",
        "human_required": "需要人工处理",
        "needs_review": "需复核",
        "page_structure_changed": "页面结构变化",
        "skipped": "已跳过",
        "config_required": "需要配置",
    }.get(status, status)


def _status_icon(status: str) -> str:
    return {
        "success": "✅",
        "partial": "🟡",
        "failed": "🔴",
        "human_required": "🟠",
        "needs_review": "🟡",
        "page_structure_changed": "🟠",
        "config_required": "⚙️",
    }.get(str(status or "").strip(), "🔵")


def _card_template(status: str) -> str:
    if status == "success":
        return "green"
    if status == "human_required":
        return "orange"
    if status in {"partial", "needs_review", "page_structure_changed"}:
        return "yellow"
    return "red"
