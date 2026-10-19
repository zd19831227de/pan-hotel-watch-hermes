from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

_SHANGHAI = timezone(timedelta(hours=8))

# Feishu webhook bodies have a server-side size ceiling. V4 hit card 降级 when
# tables carried too many rows; V5 Step 5 fixes report formats and every report
# card is run through ``enforce_card_byte_limit`` so a large report degrades by
# dropping trailing sections instead of failing the whole push.
WEBHOOK_BODY_BYTE_LIMIT = 28000
WEBHOOK_ANALYSIS_CHAR_LIMIT = 600


def _escape_lark_md(value: Any) -> str:
    text = "" if value is None else str(value)
    return html.escape(text, quote=False)


def _card_text(content: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": content}


def _field_table(rows: list[dict[str, Any]], *, page_size: int = 8) -> dict[str, Any]:
    return {
        "tag": "table",
        "page_size": page_size,
        "columns": [
            {"name": "field", "display_name": "项目", "width": "auto"},
            {"name": "value", "display_name": "数值", "width": "auto"},
        ],
        "rows": [
            {
                "field": _escape_lark_md(row.get("field")),
                "value": _escape_lark_md(row.get("value")),
            }
            for row in rows
            if row.get("field") is not None and row.get("value") is not None
        ],
    }


def _competitor_price_mobile_block(row: dict[str, Any]) -> str:
    hotel = row.get("hotel")
    stay = row.get("stay")
    room_type = row.get("room_type")
    price_range = row.get("price_range")
    first_price = row.get("first_price")
    latest_price = row.get("latest_price")
    change = row.get("change")
    if not price_range and first_price and latest_price:
        price_range = f"{first_price} → {latest_price}"
    if hotel and stay and room_type and price_range and change:
        return (
            f"🏪 {_escape_lark_md(hotel)}\n"
            f"▶ 房型：{_escape_lark_md(room_type)}\n"
            f"▶ {_escape_lark_md(stay)} · {_escape_lark_md(price_range)}（{_escape_lark_md(change)}）"
        )
    field = row.get("field")
    value = row.get("value")
    if field is not None and value is not None:
        hotel_name, date_label = str(field), ""
        if " · " in str(field):
            hotel_name, date_label = str(field).split(" · ", 1)
        value_text = str(value)
        parts = [part.strip() for part in value_text.split("/")]
        if len(parts) >= 3:
            room, breakfast, price = parts[0], parts[1], parts[2]
            date_line = f"{date_label} · {room} / {breakfast}" if date_label else f"{room} / {breakfast}"
            return (
                f"🏪 {_escape_lark_md(hotel_name)}\n"
                f"▶ {_escape_lark_md(date_line)}\n"
                f"▶ {_escape_lark_md(price)}"
            )
        return f"🏪 {_escape_lark_md(field)}\n▶ {_escape_lark_md(value)}"
    return "▶ 暂无价格"


def _competitor_mobile_block_elements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        elements.append(_card_text(_competitor_price_mobile_block(row)))
        if index < len(rows) - 1:
            elements.append({"tag": "hr"})
    return elements


def _header_template(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"success", "fresh", "healthy"}:
        return "green"
    if normalized in {"attention_required", "stale", "failed", "missing", "tick_failed"}:
        return "red"
    if normalized in {"human_required", "config_required", "degraded"}:
        return "orange"
    return "blue"


def _format_time(value: Any) -> str:
    if not value:
        return "—"
    text = str(value).strip()
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def build_review_summary_card(
    *,
    review_summary: dict[str, Any],
    captured_at: str | None = None,
) -> dict[str, Any]:
    if not review_summary.get("available"):
        return build_generic_operator_card(
            title="点评数据",
            status="config_required",
            intro=str(review_summary.get("summary") or "暂无点评数据。"),
        )
    rows = [
        {"field": "昨日好评", "value": review_summary.get("positive_reviews_yesterday")},
        {"field": "昨日差评", "value": review_summary.get("negative_reviews_yesterday")},
        {"field": "综合评分", "value": review_summary.get("overall_score")},
        {"field": "评论总数", "value": review_summary.get("total_comment_count")},
    ]
    elements: list[dict[str, Any]] = [
        _card_text(
            f"**读取时间**：{_escape_lark_md(_format_time(captured_at or review_summary.get('captured_at')))}\n"
            f"**口径说明**：{_escape_lark_md(review_summary.get('caliber_note') or '昨日好评/差评，不是今日实时新增。')}"
        ),
        _field_table(rows, page_size=len(rows)),
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "点评概览"},
        },
        "elements": elements,
    }


def build_ebk_latest_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    operator = snapshot.get("operator_summary") if isinstance(snapshot.get("operator_summary"), dict) else {}
    review = operator.get("review_summary") if isinstance(operator.get("review_summary"), dict) else {}
    metrics = operator.get("beneficial_metrics") if isinstance(operator.get("beneficial_metrics"), list) else []
    preferred = str(operator.get("preferred_feishu_card_kind") or "").strip()
    if preferred == "beneficial_data" and metrics:
        return build_beneficial_data_card(snapshot)
    if preferred == "review" and review.get("available"):
        return build_review_summary_card(
            review_summary=review,
            captured_at=operator.get("latest_captured_at"),
        )
    if review.get("available"):
        return build_review_summary_card(
            review_summary=review,
            captured_at=operator.get("latest_captured_at"),
        )
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    status = str(run.get("status") or "unknown")
    datasets = operator.get("dataset_ids_in_latest_run") or []
    rows = [{"field": "数据集", "value": ", ".join(datasets) or "—"}]
    return build_generic_operator_card(
        title="EBK 盯盘结果",
        status=status,
        intro=f"**读取时间**：{_format_time(operator.get('latest_captured_at'))}",
        rows=rows,
    )


def build_beneficial_data_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    operator = snapshot.get("operator_summary") if isinstance(snapshot.get("operator_summary"), dict) else {}
    metrics = operator.get("beneficial_metrics") if isinstance(operator.get("beneficial_metrics"), list) else []
    if not metrics:
        return build_generic_operator_card(
            title="经营数据",
            status="config_required",
            intro=str(operator.get("beneficial_summary") or "最新 EBK 结果中暂无销售/出租率数据。"),
        )
    return build_generic_operator_card(
        title="经营数据",
        status=str(operator.get("latest_run_status") or "success"),
        intro=f"**读取时间**：{_format_time(operator.get('latest_captured_at'))}",
        rows=metrics,
    )


def build_ebk_datasets_read_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    operator = snapshot.get("operator_summary") if isinstance(snapshot.get("operator_summary"), dict) else {}
    rows = operator.get("dataset_read_rows") if isinstance(operator.get("dataset_read_rows"), list) else []
    summaries = operator.get("dataset_summaries") if isinstance(operator.get("dataset_summaries"), dict) else {}
    module_labels = [
        str(item.get("label") or key)
        for key, item in summaries.items()
        if isinstance(item, dict) and item.get("available")
    ]
    title = "EBK 数据"
    if len(module_labels) == 1:
        title = f"EBK {module_labels[0]}"
    elif len(module_labels) > 1:
        title = "EBK " + " · ".join(module_labels[:4])
    intro_lines = [f"**读取时间**：{_format_time(operator.get('latest_captured_at'))}"]
    if len(module_labels) > 1:
        intro_lines.append(f"**本次模块**：{'、'.join(module_labels)}")
    return build_generic_operator_card(
        title=title[:80],
        status=str(operator.get("latest_run_status") or "success"),
        intro="\n".join(intro_lines),
        rows=rows,
    )


def _metric_dict_lines(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, dict):
        return []
    lines: list[str] = []
    for key, raw in list(value.items())[:limit]:
        key_text = str(key)
        if not re.search(r"[\u3400-\u9fff]", key_text):
            translated = _CTRIP_METRIC_LABELS.get(key_text)
            if not translated:
                continue
            key_text = translated
        if isinstance(raw, dict):
            display = raw.get("value") if raw.get("value") is not None else raw.get("display_value")
            if display is None:
                continue
            lines.append(f"- {_escape_lark_md(key_text)}：{_escape_lark_md(display)}")
        elif raw is not None and not isinstance(raw, list):
            lines.append(f"- {_escape_lark_md(key_text)}：{_escape_lark_md(raw)}")
    return lines


def _append_metric_section(
    elements: list[dict[str, Any]],
    title: str,
    metrics: Any,
    *,
    limit: int = 8,
    use_table: bool = False,
) -> None:
    rows = _metric_table_rows(metrics, limit=limit)
    if rows and use_table:
        elements.append(_card_text(f"**{_escape_lark_md(title)}**"))
        elements.append(_field_table(rows, page_size=min(len(rows), 8)))
    elif rows:
        lines = [f"- {_escape_lark_md(row['field'])}：{_escape_lark_md(row['value'])}" for row in rows]
        elements.append(_card_text(f"**{_escape_lark_md(title)}**\n" + "\n".join(lines)))
    else:
        elements.append(_card_text(f"**{_escape_lark_md(title)}**\n- 暂无可展示指标"))


def _metric_table_rows(value: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, raw in list(value.items())[:limit]:
        key_text = str(key)
        if not re.search(r"[\u3400-\u9fff]", key_text):
            translated = _CTRIP_METRIC_LABELS.get(key_text)
            if not translated:
                continue
            key_text = translated
        if isinstance(raw, dict):
            display = raw.get("value") if raw.get("value") is not None else raw.get("display_value")
        elif isinstance(raw, list):
            continue
        else:
            display = raw
        if display is None or not str(display).strip():
            continue
        rows.append({"field": key_text, "value": display})
    return rows


_CTRIP_METRIC_LABELS = {
    "realtime_booking_orders": "实时预订订单",
    "realtime_in_house_room_nights": "实时在店间夜",
    "departure_sales_amount": "昨日离店销售额",
    "departure_room_nights": "昨日离店间夜",
    "transaction_rate": "成交率",
    "average_selling_price": "均价",
    "booking_orders": "预订订单",
    "room_nights": "间夜",
    "sales_amount": "销售额",
    "in_house_room_nights": "在店间夜",
    "occupancy_rate": "出租率",
    "tightness": "紧张度",
}


def _labeled_metrics_from_items(items: Any) -> dict[str, Any]:
    if isinstance(items, dict):
        cleaned: dict[str, Any] = {}
        for key, raw in items.items():
            if key in {"available", "status", "updated_at", "captured_at"}:
                continue
            label = _CTRIP_METRIC_LABELS.get(str(key), str(key))
            if isinstance(raw, dict):
                value = raw.get("display_value")
                if value is None:
                    value = raw.get("value")
                nested_label = str(raw.get("label") or "").strip()
                if nested_label:
                    label = nested_label
                if value is not None and str(value).strip():
                    cleaned[label] = value
            elif raw is not None and not isinstance(raw, (list, dict)):
                cleaned[label] = raw
        return cleaned
    if not isinstance(items, list):
        return {}
    cleaned = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip() or _CTRIP_METRIC_LABELS.get(key, key)
        value = item.get("display_value")
        if value is None:
            value = item.get("value")
        if label and value is not None and str(value).strip():
            cleaned[label] = value
    return cleaned


def _append_ctrip_brief_elements(
    elements: list[dict[str, Any]],
    operator: dict[str, Any],
) -> None:
    ctrip = operator.get("ctrip_ebk") if isinstance(operator.get("ctrip_ebk"), dict) else {}
    monitor = ctrip.get("monitor") if isinstance(ctrip.get("monitor"), dict) else {}
    before = len(elements)

    revenue = monitor.get("revenue_overview") if isinstance(monitor.get("revenue_overview"), dict) else {}
    realtime = _labeled_metrics_from_items(revenue.get("realtime"))
    yesterday = _labeled_metrics_from_items(revenue.get("yesterday"))
    if realtime:
        _append_metric_section(elements, "⏱️ 实时数据（今日）", realtime, limit=6)
    if yesterday:
        _append_metric_section(elements, "↩️ 昨日数据（对比参考）", yesterday, limit=8)

    sales = monitor.get("sales_data") if isinstance(monitor.get("sales_data"), dict) else {}
    booking = _labeled_metrics_from_items(sales.get("booking"))
    in_house = _labeled_metrics_from_items(sales.get("in_house"))
    if booking:
        _append_metric_section(elements, "🛒 预订概况", booking, limit=6)
    if in_house:
        _append_metric_section(elements, "🛏️ 在店概况", in_house, limit=6)

    if len(elements) == before:
        flat = {
            key: value
            for key, value in monitor.items()
            if key not in {"revenue_overview", "sales_data", "flow_data", "available", "status"}
            and not isinstance(value, (dict, list))
        }
        if flat:
            _append_metric_section(elements, "🧮 关键指标", flat, limit=8)
        else:
            elements.append(_card_text("暂无可展示的关键指标。"))


def _append_fliggy_brief_elements(
    elements: list[dict[str, Any]],
    operator: dict[str, Any],
) -> None:
    home = operator.get("fliggy_home") if isinstance(operator.get("fliggy_home"), dict) else {}
    if home.get("hotel_name"):
        elements.append(_card_text(f"**酒店**：{_escape_lark_md(home.get('hotel_name'))}"))
    today = home.get("today_data") if isinstance(home.get("today_data"), dict) else {}
    yesterday = home.get("yesterday_data") if isinstance(home.get("yesterday_data"), dict) else {}
    if today:
        _append_metric_section(elements, "⏱️ 实时数据（今日）", today, limit=8)
    if yesterday:
        _append_metric_section(elements, "↩️ 昨日数据（对比参考）", yesterday, limit=8)

    diagnose = operator.get("fliggy_diagnose") if isinstance(operator.get("fliggy_diagnose"), dict) else {}
    trade = diagnose.get("trade_overview") if isinstance(diagnose.get("trade_overview"), dict) else {}
    diagnose_metrics = dict(trade)
    if diagnose.get("guest_review_score") is not None:
        diagnose_metrics["客评分"] = diagnose.get("guest_review_score")
    if diagnose.get("mci_index") is not None:
        diagnose_metrics["MCI"] = diagnose.get("mci_index")
    if diagnose_metrics:
        _append_metric_section(elements, "🎯 诊断概况", diagnose_metrics, limit=8)

    operating = operator.get("fliggy_operating") if isinstance(operator.get("fliggy_operating"), dict) else {}
    for title, key in (
        ("🛏️ 在店数据", "in_house_data"),
        ("🛒 预订数据", "booking_data"),
        ("🚦 流量数据", "flow_data"),
    ):
        section = operating.get(key) if isinstance(operating.get(key), dict) else {}
        if section:
            _append_metric_section(elements, title, section, limit=6)

    if not any(
        (
            today,
            yesterday,
            diagnose_metrics,
            operating.get("in_house_data"),
            operating.get("booking_data"),
            operating.get("flow_data"),
        )
    ):
        elements.append(_card_text("暂无可展示的关键指标。"))


def _append_joint_insight_elements(
    elements: list[dict[str, Any]],
    operator: dict[str, Any],
    *,
    fallback_summary: str = "",
) -> None:
    """QnA joint card: 1 conclusion, ≤3 findings, ≤2 actions."""
    conclusion = str(operator.get("conclusion") or "").strip() or fallback_summary.strip()
    if conclusion:
        elements.append(_card_text(f"**✅ 结论**\n{_escape_lark_md(conclusion)}"))

    findings = operator.get("findings") if isinstance(operator.get("findings"), list) else []
    finding_lines: list[str] = []
    for item in findings[:3]:
        text = item.get("text") if isinstance(item, dict) else item
        if text is None or not str(text).strip():
            continue
        finding_lines.append(f"- {_escape_lark_md(text)}")
    if finding_lines:
        elements.extend([{"tag": "hr"}, _card_text("**🔎 关键发现**\n" + "\n".join(finding_lines))])

    actions = operator.get("actions") if isinstance(operator.get("actions"), list) else []
    action_lines: list[str] = []
    for item in actions[:2]:
        text = item.get("text") if isinstance(item, dict) else item
        if text is None or not str(text).strip():
            continue
        action_lines.append(f"- {_escape_lark_md(text)}")
    if action_lines:
        elements.extend([{"tag": "hr"}, _card_text("**👉 建议**\n" + "\n".join(action_lines))])


def _merge_metric_dicts(*parts: Any, limit: int = 3) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        labeled = _labeled_metrics_from_items(part) if not isinstance(part, dict) else {}
        source = labeled or (part if isinstance(part, dict) else {})
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if len(merged) >= limit:
                return merged
            key_text = str(key)
            if key_text in merged or key_text in {"available", "status", "captured_at", "updated_at"}:
                continue
            if not re.search(r"[\u3400-\u9fff]", key_text):
                continue
            display: Any = value
            if isinstance(value, dict):
                display = value.get("value")
                if display is None:
                    display = value.get("display_value")
            elif isinstance(value, list):
                continue
            if display is None or not str(display).strip():
                continue
            merged[key_text] = display
    return merged


def _slim_platform_highlight_metrics(platform: str, operator: dict[str, Any]) -> dict[str, Any]:
    if platform == "meituan":
        workbench = operator.get("meituan_workbench") if isinstance(operator.get("meituan_workbench"), dict) else {}
        return _merge_metric_dicts(
            workbench.get("realtime_data"),
            workbench.get("yesterday_data"),
            limit=3,
        )
    if platform == "ctrip":
        ctrip = operator.get("ctrip_ebk") if isinstance(operator.get("ctrip_ebk"), dict) else {}
        monitor = ctrip.get("monitor") if isinstance(ctrip.get("monitor"), dict) else {}
        revenue = monitor.get("revenue_overview") if isinstance(monitor.get("revenue_overview"), dict) else {}
        sales = monitor.get("sales_data") if isinstance(monitor.get("sales_data"), dict) else {}
        return _merge_metric_dicts(
            _labeled_metrics_from_items(revenue.get("realtime")),
            _labeled_metrics_from_items(revenue.get("yesterday")),
            _labeled_metrics_from_items(sales.get("in_house")),
            limit=3,
        )
    home = operator.get("fliggy_home") if isinstance(operator.get("fliggy_home"), dict) else {}
    return _merge_metric_dicts(home.get("today_data"), home.get("yesterday_data"), limit=3)


def _append_meituan_brief_elements(
    elements: list[dict[str, Any]],
    operator: dict[str, Any],
) -> None:
    workbench = operator.get("meituan_workbench") if isinstance(operator.get("meituan_workbench"), dict) else {}
    _append_metric_section(elements, "⏱️ 实时数据（今日）", workbench.get("realtime_data"), limit=10)
    _append_metric_section(elements, "↩️ 昨日数据（对比参考）", workbench.get("yesterday_data"), limit=10)

    hos = workbench.get("hos_score") if isinstance(workbench.get("hos_score"), dict) else {}
    hos_lines: list[str] = []
    if hos.get("value"):
        hos_lines.append(f"- 当前分：{_escape_lark_md(hos.get('value'))}")
    if hos.get("grade_tip"):
        hos_lines.append(f"- {_escape_lark_md(hos.get('grade_tip'))}")
    if hos.get("message"):
        hos_lines.append(f"- {_escape_lark_md(hos.get('message'))}")
    if hos_lines:
        elements.append(_card_text("**⭐ HOS 评分**\n" + "\n".join(hos_lines)))

    reviews = operator.get("meituan_reviews") if isinstance(operator.get("meituan_reviews"), dict) else {}
    review_lines: list[str] = []
    if reviews.get("review_score"):
        review_lines.append(f"- 综合评分：{_escape_lark_md(reviews.get('review_score'))}")
    if reviews.get("review_total"):
        review_lines.append(f"- 总评价数：{_escape_lark_md(reviews.get('review_total'))}")
    dimensions = reviews.get("review_dimensions") if isinstance(reviews.get("review_dimensions"), dict) else {}
    if dimensions:
        dim_text = " / ".join(f"{k} {v}" for k, v in list(dimensions.items())[:6])
        review_lines.append(f"- 维度：{_escape_lark_md(dim_text)}")
    periods = reviews.get("review_periods") if isinstance(reviews.get("review_periods"), dict) else {}
    for period_label, period_data in list(periods.items())[:2]:
        if not isinstance(period_data, dict):
            continue
        parts = [f"{k} {v}" for k, v in list(period_data.items())[:4]]
        if parts:
            review_lines.append(f"- {_escape_lark_md(period_label)}：{_escape_lark_md('，'.join(parts))}")
    if reviews.get("pending_reply_count") is not None:
        review_lines.append(f"- 待回复：{_escape_lark_md(reviews.get('pending_reply_count'))}")
    if review_lines:
        elements.append(_card_text("**📝 点评概况**\n" + "\n".join(review_lines)))

    data_center = operator.get("meituan_data_center") if isinstance(operator.get("meituan_data_center"), dict) else {}
    basic = data_center.get("basic_operation_metrics") if isinstance(data_center.get("basic_operation_metrics"), dict) else {}
    if basic:
        _append_metric_section(elements, "🎯 核心经营指标", basic, limit=8)
    tabs = data_center.get("tabs") if isinstance(data_center.get("tabs"), dict) else {}
    for tab_id, tab in list(tabs.items())[:3]:
        if not isinstance(tab, dict) or tab.get("available") is not True:
            continue
        metrics = tab.get("metrics") if isinstance(tab.get("metrics"), dict) else {}
        label = str(tab.get("label") or tab_id)
        if metrics:
            _append_metric_section(elements, f"📌 {label}", metrics, limit=6)


def _brief_metric_rows(
    value: Any,
    *,
    prefix: str = "",
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    key_labels = {
        "realtime_data": "实时数据",
        "hos_score": "HOS 评分",
        "review_score": "点评评分",
        "review_status": "点评状态",
        "review_periods": "点评周期",
        "summary": "摘要",
        "datasets": "数据集",
        "monitor": "监测摘要",
        "yesterday_data": "昨日数据",
        "guest_review_score": "客评分",
        "flow_data": "流量数据",
        "joined_activities": "已参加活动",
    }

    def visit(item: Any, label: str) -> None:
        if len(rows) >= limit:
            return
        if isinstance(item, dict):
            if "value" in item and not isinstance(item.get("value"), (dict, list)):
                rows.append({"field": label or "指标", "value": item.get("value")})
                return
            for key, child in item.items():
                if key in {"available", "status", "captured_at", "updated_at"}:
                    continue
                readable_key = key_labels.get(str(key), str(key))
                child_label = f"{label} · {readable_key}" if label else readable_key
                visit(child, child_label)
                if len(rows) >= limit:
                    return
            return
        if isinstance(item, list):
            for index, child in enumerate(item[:3], start=1):
                child_label = f"{label} {index}" if label else str(index)
                visit(child, child_label)
                if len(rows) >= limit:
                    return
            return
        if item is not None and str(item).strip():
            rows.append({"field": label or "指标", "value": item})

    visit(value, prefix)
    return rows


def build_ebk_joint_brief_card(payload: dict[str, Any]) -> dict[str, Any]:
    """QnA joint/detail card: conclusion + ≤3 evidence + ≤2 actions + slim platform highlights."""
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    analysis_mode = str(payload.get("analysis_mode") or "joint").strip().lower()
    store_label = str(operator.get("store_label") or payload.get("store_label") or "门店").strip()
    title_label = {
        "meituan": "美团今日经营简报",
        "fliggy": "飞猪今日经营简报",
        "ctrip": "携程今日经营简报",
        "joint": "三端今日经营简报",
    }.get(analysis_mode, "三端今日经营简报")
    read_times = operator.get("read_times") if isinstance(operator.get("read_times"), dict) else {}
    platforms = operator.get("platforms") if isinstance(operator.get("platforms"), dict) else {}
    platform_specs = {
        "ctrip": "携程",
        "meituan": "美团",
        "fliggy": "飞猪",
    }
    selected = [analysis_mode] if analysis_mode in platform_specs else ["ctrip", "meituan", "fliggy"]
    elements: list[dict[str, Any]] = []
    summary = str(payload.get("summary") or "").strip()
    _append_joint_insight_elements(elements, operator, fallback_summary=summary)
    caliber_note = str(operator.get("comparison_caliber_note") or "").strip()
    if caliber_note:
        elements.append(_card_text(f"**口径说明**\n{_escape_lark_md(caliber_note)}"))

    for platform in selected:
        label = platform_specs[platform]
        platform_state = platforms.get(platform) if isinstance(platforms.get(platform), dict) else {}
        platform_status = str(platform_state.get("status") or "available")
        status_line = "✅ 数据可用" if platform_status == "available" else f"🔴 {_escape_lark_md(platform_status)}"
        heading = (
            f"**🏠 {label}**\n"
            f"{status_line} · 🕒 {_escape_lark_md(_format_time(read_times.get(platform)))}"
        )
        elements.extend([{"tag": "hr"}, _card_text(heading)])
        highlights = _slim_platform_highlight_metrics(platform, operator)
        if highlights:
            _append_metric_section(
                elements,
                "🧮 关键指标",
                highlights,
                limit=3,
                use_table=analysis_mode == "meituan",
            )
        else:
            elements.append(_card_text("暂无可展示的关键指标。"))

    missing_data = operator.get("missing_data")
    if isinstance(missing_data, list) and missing_data:
        elements.append(
            _card_text(
                "**缺失数据**\n"
                + "\n".join(f"- {_escape_lark_md(item)}" for item in missing_data[:3])
            )
        )
    usage_note = str(operator.get("usage_note") or "只读简报，不触发 OTA 写入。").strip()
    if usage_note:
        elements.append(_card_text(f"**说明**：{_escape_lark_md(usage_note)}"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _header_template(str(payload.get("status") or "unknown")),
            "title": {
                "tag": "plain_text",
                "content": f"{store_label} · {title_label}"[:80],
            },
        },
        "elements": elements,
    }


def build_competitor_latest_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    operator = snapshot.get("operator_summary") if isinstance(snapshot.get("operator_summary"), dict) else {}
    rows = operator.get("price_rows") if isinstance(operator.get("price_rows"), list) else []
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    status = str(run.get("status") or "unknown")
    status_icon = "✅" if status in {"success", "fresh", "healthy"} else ("🔴" if status in {"failed", "attention_required"} else "🟡")
    status_label = {
        "success": "成功",
        "fresh": "最新",
        "healthy": "正常",
        "partial": "部分成功",
        "failed": "读取失败",
        "attention_required": "需要关注",
        "human_required": "需要人工处理",
    }.get(status, status)
    intro_lines = [
        f"**🕒 读取时间**：{_format_time(operator.get('latest_captured_at'))}",
        f"**{status_icon} 状态**：{_escape_lark_md(status_label)}",
        f"**📌 结果**：{_escape_lark_md(run.get('summary') or '—')}",
    ]
    operator_note = operator.get("operator_note")
    if operator_note:
        intro_lines.append(f"**💡 说明**：{_escape_lark_md(operator_note)}")
    if operator.get("latest_poll_run_status") and operator.get("latest_poll_run_status") != status:
        intro_lines.append(
            f"**🔄 最新轮询**：{_escape_lark_md(_format_time(operator.get('latest_poll_captured_at')))} "
            f"（{_escape_lark_md(operator.get('latest_poll_run_status'))}）"
        )
    intro = "\n".join(intro_lines)
    elements: list[dict[str, Any]] = [_card_text(intro)]
    if rows:
        elements.extend(_competitor_mobile_block_elements(rows[:12]))
    else:
        elements.append(_card_text("暂无可信 DOM 价格。"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _header_template(status),
            "title": {"tag": "plain_text", "content": "竞品价格盯盘"},
        },
        "elements": elements,
    }


def build_competitor_price_trend_card(payload: dict[str, Any]) -> dict[str, Any]:
    """QnA trend card: chart + window/time + ≤3 key change blocks; no config essays."""
    import base64

    from client.competitor_trend_blocks import build_trend_grouped_mobile_blocks
    from client.competitor_trend_chart import try_build_competitor_trend_chart_png

    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    series = payload.get("series") if isinstance(payload.get("series"), list) else []
    days = int(operator.get("days") or payload.get("days") or 3)
    intro_lines = [
        _escape_lark_md(operator.get("intro") or "竞品价格趋势"),
        f"**时间范围**：近{days}天",
    ]
    if str(payload.get("metric") or "") == "min":
        # min 对比口径必须显式标注：每店一条「各时段最低价」线（跨早次/房型
        # 取最低），与标准视图的「按早次拆面板」不同，避免运营看图误解口径。
        intro_lines.append("**口径**：每家酒店各时段最低价（不分早）")
    data_time = operator.get("latest_captured_at") or operator.get("data_captured_at_display")
    if data_time:
        intro_lines.append(f"**数据时间**：{_escape_lark_md(_format_time(data_time))}")
    elements: list[dict[str, Any]] = [_card_text("\n".join(intro_lines))]

    grouped_blocks = build_trend_grouped_mobile_blocks(series)[:3]
    fallback_elements: list[dict[str, Any]] = []
    if grouped_blocks:
        fallback_elements.append(_card_text("\n\n".join(grouped_blocks)))
    else:
        fallback_elements.append(_card_text("暂无可展示的历史价格点。"))

    chart_png = try_build_competitor_trend_chart_png(series, days=days)
    card: dict[str, Any] = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": (
                    f"各时段最低价对比 · 近{days}天"
                    if str(payload.get("metric") or "") == "min"
                    else f"竞品价格趋势 · 近{days}天"
                ),
            },
        },
        "elements": elements,
        "_feishu_fallback_elements": fallback_elements,
    }
    if chart_png:
        card["_feishu_image_uploads"] = [
            {
                "alt": "竞品价格趋势折线图",
                "data_base64": base64.b64encode(chart_png).decode("ascii"),
                "position": 1,
            }
        ]
    else:
        card["elements"].extend(fallback_elements)
    return card


def build_scheduler_status_card(health: dict[str, Any]) -> dict[str, Any]:
    operator = health.get("operator_summary") if isinstance(health.get("operator_summary"), dict) else {}
    rows = operator.get("module_rows") if isinstance(operator.get("module_rows"), list) else []
    status = str(operator.get("overall_status") or "unknown")
    intro = f"**检查时间**：{_format_time(health.get('checked_at'))}"
    if operator.get("attention_modules"):
        intro += f"\n**需关注**：{_escape_lark_md(', '.join(operator['attention_modules']))}"
    elements: list[dict[str, Any]] = [_card_text(intro)]
    if rows:
        elements.append(_field_table(rows, page_size=len(rows)))
    notes = operator.get("notes") if isinstance(operator.get("notes"), list) else []
    if notes:
        elements.append(_card_text("**说明**\n" + "\n".join(f"- {_escape_lark_md(note)}" for note in notes[:4])))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _header_template(status),
            "title": {"tag": "plain_text", "content": "盯盘服务状态"},
        },
        "elements": elements,
    }


def build_price_change_card(payload: dict[str, Any]) -> dict[str, Any]:
    alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else payload
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    rows = operator.get("change_rows") if isinstance(operator.get("change_rows"), list) else []
    status = str(alert.get("status") or operator.get("alert_status") or "unknown")
    intro = _escape_lark_md(operator.get("summary") or alert.get("summary") or "价格变化分析")
    elements: list[dict[str, Any]] = [_card_text(f"**摘要**\n{intro}")]
    if rows:
        elements.append(_field_table(rows, page_size=min(len(rows), 10)))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _header_template(status),
            "title": {"tag": "plain_text", "content": "竞品价格变化"},
        },
        "elements": elements,
    }


def build_manual_price_confirmation_card(classification: dict[str, Any]) -> dict[str, Any]:
    partial = classification.get("partial") if isinstance(classification.get("partial"), dict) else {}
    missing = classification.get("missing_fields")
    missing_fields = missing if isinstance(missing, list) else []
    labels = {
        "room_type": "房型",
        "date_label": "入住日期（今天/明天）",
        "target_price": "目标售价（元）",
    }
    known_lines: list[str] = []
    if partial.get("room_type"):
        known_lines.append(f"- 房型：{_escape_lark_md(partial['room_type'])}")
    if partial.get("date_label"):
        known_lines.append(
            f"- 日期：{'今天' if partial['date_label'] == 'today' else '明天'}"
        )
    if partial.get("target_price"):
        known_lines.append(f"- 目标价：¥{_escape_lark_md(partial['target_price'])}")
    missing_lines = [
        f"- 还缺：{_escape_lark_md(labels.get(field, field))}"
        for field in missing_fields
        if field in labels
    ]
    examples = classification.get("examples")
    example_lines = (
        [f"- {_escape_lark_md(str(item))}" for item in examples[:2]]
        if isinstance(examples, list)
        else ["- 把今天高级大床房调到420"]
    )
    body = "\n".join(
        [
            "**手动调价信息不完整**",
            _escape_lark_md(str(classification.get("summary") or "请补全房型、日期和目标价。")),
            "",
            "**已识别**",
            *(known_lines or ["- 暂无"]),
            "",
            "**待补充**",
            *(missing_lines or ["- 请按示例重新发送完整指令"]),
            "",
            "**示例（补全后只会生成审批卡，不会直接写价）**",
            *example_lines,
        ]
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "请补充调价信息"},
        },
        "elements": [_card_text(body)],
    }


def build_hotel_overview_card(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    rows = operator.get("overview_rows") if isinstance(operator.get("overview_rows"), list) else []
    status = str(operator.get("overall_status") or "unknown")
    intro = str(operator.get("intro") or f"**检查时间**：{_format_time(payload.get('checked_at'))}")
    elements: list[dict[str, Any]] = [_card_text(intro)]
    if rows:
        elements.append(_field_table(rows, page_size=min(len(rows), 10)))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _header_template(status),
            "title": {"tag": "plain_text", "content": "酒店今日概览"},
        },
        "elements": elements,
    }


def build_mobile_summary_card(summary: dict[str, Any]) -> dict[str, Any]:
    nested = summary.get("mobile_summary")
    source = nested if isinstance(nested, dict) else summary
    findings = source.get("findings") if isinstance(source.get("findings"), list) else []
    actions = source.get("actions") if isinstance(source.get("actions"), list) else []

    finding_lines = [
        f"{index}. {_escape_lark_md(item.get('text'))}"
        for index, item in enumerate(findings[:3], start=1)
        if isinstance(item, dict) and item.get("text")
    ]
    action_lines = [
        f"{index}. {_escape_lark_md(item.get('text'))}"
        for index, item in enumerate(actions[:2], start=1)
        if isinstance(item, dict) and item.get("text")
    ]
    freshness_note = {
        "fresh": "数据较新",
        "stale": "数据可能已过期",
    }.get(str(source.get("freshness") or "").strip().lower(), "数据时效未知")
    data_notes = [
        f"数据时间：{_escape_lark_md(source.get('data_time') or '未提供明确读取时间')}",
        freshness_note,
    ]
    missing_data = source.get("missing_data")
    if isinstance(missing_data, list) and missing_data:
        data_notes.append(f"数据缺口：{_escape_lark_md(missing_data[0])}")
    risk_flags = source.get("risk_flags")
    if isinstance(risk_flags, list) and risk_flags:
        data_notes.append(f"风险提示：{_escape_lark_md(risk_flags[0])}")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _header_template(str(source.get("status") or "unknown")),
            "title": {"tag": "plain_text", "content": "经营摘要"},
        },
        "elements": [
            _card_text(f"**结论**\n{_escape_lark_md(source.get('conclusion') or '暂无明确结论。')}"),
            _card_text("**关键发现**\n" + "\n".join(finding_lines or ["暂无关键发现。"])),
            _card_text("**建议行动**\n" + "\n".join(action_lines or ["暂无建议行动。"])),
            _card_text(" · ".join(data_notes)),
        ],
    }


def build_generic_operator_card(
    *,
    title: str,
    status: str,
    intro: str,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [_card_text(intro)]
    if rows:
        elements.append(_field_table(rows, page_size=min(len(rows), 10)))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _header_template(status),
            "title": {"tag": "plain_text", "content": title[:80]},
        },
        "elements": elements,
    }


def build_chat_context_card(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    rows = operator.get("overview_rows") if isinstance(operator.get("overview_rows"), list) else []
    runtime_rows = (
        operator.get("runtime_module_rows")
        if isinstance(operator.get("runtime_module_rows"), list)
        else []
    )
    selection_rows = (
        operator.get("store_selection_rows")
        if isinstance(operator.get("store_selection_rows"), list)
        else []
    )
    intro = str(operator.get("intro") or "运营问答上下文群。")
    binding_mode = str(payload.get("binding_mode") or "unknown")
    resolution = payload.get("resolution") if isinstance(payload.get("resolution"), dict) else {}
    configured = bool(payload.get("chat_id_configured"))
    elements: list[dict[str, Any]] = [_card_text(intro)]
    if rows:
        elements.append(_field_table(rows, page_size=min(len(rows), 12)))
    if runtime_rows:
        elements.append(_field_table(runtime_rows, page_size=min(len(runtime_rows), 8)))
    policy_rows = (
        operator.get("pricing_reference_policy_rows")
        if isinstance(operator.get("pricing_reference_policy_rows"), list)
        else []
    )
    if policy_rows:
        elements.append(_field_table(policy_rows, page_size=min(len(policy_rows), 8)))
    if selection_rows:
        elements.append(_field_table(selection_rows, page_size=min(len(selection_rows), 8)))
    notes = operator.get("usage_note") or payload.get("usage_note")
    if notes:
        elements.append(_card_text(f"说明\n{_escape_lark_md(notes)}"))
    if resolution.get("status") == "ambiguous":
        elements.append(
            _card_text(
                "请在问题里带上店名，例如：\n"
                "• 静里 上周经营简报\n"
                "• 澜江路泉吉 竞品价格"
            )
        )
    title = {
        "single_store": "运营问答上下文群（单店）",
        "multi_store": "运营问答上下文群（多店）",
        "unbound": "运营问答上下文群（未绑定）",
    }.get(binding_mode, "运营问答上下文群")
    header_template = "blue" if configured and resolution.get("status") == "resolved" else (
        "orange" if configured else "grey"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def build_weekly_briefing_card(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    rows = operator.get("overview_rows") if isinstance(operator.get("overview_rows"), list) else []
    intro = str(operator.get("intro") or "上周经营简报。")
    store_label = str(payload.get("store_label") or "").strip()
    status = str(payload.get("status") or "unknown")
    title_store = store_label or "门店"
    now = datetime.now(_SHANGHAI)
    elements: list[dict[str, Any]] = [_card_text(intro)]
    if rows:
        elements.append(_field_table(rows, page_size=min(len(rows), 14)))
    elements.append({"tag": "hr"})
    summary_lines = [f"**📝 简析**\n{_escape_lark_md(intro.split('。')[0] if intro else '上周经营数据摘要')}"]
    caliber = operator.get("caliber_note")
    if caliber:
        summary_lines.append(_escape_lark_md(str(caliber)))
    elements.append(_card_text("\n".join(summary_lines)))
    gaps = payload.get("data_gaps")
    if isinstance(gaps, list) and gaps:
        elements.append(_card_text("暂无数据项\n" + _escape_lark_md("；".join(str(item) for item in gaps))))
    header_template = "green" if status == "success" else "orange"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_template,
            "title": {
                "tag": "plain_text",
                "content": f"{title_store} · 上周简报 · {now:%H:%M}"[:80],
            },
        },
        "elements": elements,
    }


def build_recent_activity_card(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    rows = operator.get("overview_rows") if isinstance(operator.get("overview_rows"), list) else []
    intro = str(operator.get("intro") or "最近盯盘活动摘要。")
    elements: list[dict[str, Any]] = [_card_text(intro)]
    if rows:
        elements.append(_field_table(rows, page_size=min(len(rows), 8)))
    notes = operator.get("usage_note")
    if notes:
        elements.append(_card_text(f"说明\n{_escape_lark_md(notes)}"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "最近盯盘活动"},
        },
        "elements": elements,
    }


def build_watch_configuration_card(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    rows = operator.get("overview_rows") if isinstance(operator.get("overview_rows"), list) else []
    chat_rows = (
        operator.get("chat_binding_rows")
        if isinstance(operator.get("chat_binding_rows"), list)
        else []
    )
    runtime_rows = (
        operator.get("runtime_module_rows")
        if isinstance(operator.get("runtime_module_rows"), list)
        else []
    )
    intro = str(operator.get("intro") or "当前 EBK 与竞品盯盘配置。")
    elements: list[dict[str, Any]] = [_card_text(intro)]
    if chat_rows:
        elements.append(_field_table(chat_rows, page_size=min(len(chat_rows), 8)))
    if runtime_rows:
        elements.append(_field_table(runtime_rows, page_size=min(len(runtime_rows), 8)))
    if rows:
        elements.append(_field_table(rows, page_size=min(len(rows), 12)))
    notes = operator.get("usage_note")
    if notes:
        elements.append(_card_text(f"说明\n{_escape_lark_md(notes)}"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "盯盘配置总览"},
        },
        "elements": elements,
    }


def pick_ebk_feishu_card_kind(snapshot: dict[str, Any]) -> str | None:
    operator = snapshot.get("operator_summary") if isinstance(snapshot.get("operator_summary"), dict) else {}
    preferred = str(operator.get("preferred_feishu_card_kind") or "").strip()
    beneficial = operator.get("beneficial_metrics") if isinstance(operator.get("beneficial_metrics"), list) else []
    review = operator.get("review_summary") if isinstance(operator.get("review_summary"), dict) else {}
    if preferred == "beneficial_data":
        return "beneficial_data" if beneficial else None
    if preferred == "ebk_datasets_read":
        dataset_rows = operator.get("dataset_read_rows") if isinstance(operator.get("dataset_read_rows"), list) else []
        return "ebk_datasets_read" if dataset_rows else None
    if preferred == "review":
        return "ebk_latest" if review.get("available") else None
    return None


def attach_ebk_intent_card(payload: dict[str, Any], *, card_kind: str) -> dict[str, Any]:
    """Attach EBK card only when caller sets explicit intent (review vs beneficial)."""
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    enriched = {
        **payload,
        "operator_summary": {
            **operator,
            "preferred_feishu_card_kind": card_kind,
        },
    }
    resolved = pick_ebk_feishu_card_kind(enriched)
    if resolved:
        return attach_feishu_card(enriched, resolved)
    fallback = "ebk_latest" if card_kind == "review" else "beneficial_data"
    return attach_feishu_card(enriched, fallback)


def enforce_card_byte_limit(card: dict[str, Any], *, limit: int = WEBHOOK_BODY_BYTE_LIMIT) -> dict[str, Any]:
    """Keep a Feishu card body under the webhook byte ceiling.

    Drops trailing elements (the least-important sections) until the serialized
    card fits. The header and the first element (the lead/intro) are always
    preserved so the push never becomes empty. This is the V5 Step 5 guard that
    prevents the V4-style whole-card 降级 when a report is large.
    """

    def _size(candidate: dict[str, Any]) -> int:
        return len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))

    candidate = card
    if _size(candidate) <= limit:
        return candidate
    elements = list(card.get("elements") or [])
    # Always keep the header + at least the first element (the lead/intro).
    while len(elements) > 1 and _size({**card, "elements": elements}) > limit:
        elements.pop()
    trimmed = {**card, "elements": elements}
    if _size(trimmed) <= limit:
        return trimmed
    # Last resort: truncate the single remaining element's text content.
    only = elements[0] if elements else {"tag": "markdown", "content": ""}
    content = str(only.get("content") or "")
    while content and _size({**card, "elements": [{**only, "content": content}]}) > limit:
        content = content[:-120]
    return {**card, "elements": [{**only, "content": content or "报表内容过大，已截断。"}]}


def build_analytics_report_card(payload: dict[str, Any]) -> dict[str, Any]:
    """V5 Step 5 — fixed-format cross-hotel analytics report card.

    The card structure (title, scope, per-hotel status, latest trusted prices,
    EBK latest run) is rendered entirely from the structured ``report`` payload
    coming from the backend analytics endpoint. AI contributes ONLY the
    ``analysis`` text block (escaped + length-capped); it never decides layout.
    The whole card is then bounded by ``enforce_card_byte_limit``.
    """

    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    scope_label = {"own": "本店", "all": "全部店铺", "selected": "指定店铺"}.get(
        str(report.get("scope") or "").strip().lower(), "数据报表"
    )
    generated_at = _format_time(report.get("generated_at") or payload.get("checked_at"))
    elements: list[dict[str, Any]] = [
        _card_text(f"**范围**：{_escape_lark_md(scope_label)}　·　**生成时间**：{generated_at}")
    ]

    hotels = report.get("hotels") if isinstance(report.get("hotels"), list) else []
    for hotel in hotels[:12]:
        if not isinstance(hotel, dict):
            continue
        title = _escape_lark_md(hotel.get("display_name") or hotel.get("profile_key") or "未命名店铺")
        chips: list[str] = []
        competitor_groups = hotel.get("competitor_groups") if isinstance(hotel.get("competitor_groups"), list) else []
        ebk_groups = hotel.get("ebk_groups") if isinstance(hotel.get("ebk_groups"), list) else []
        chips.append(f"竞品组 {len(competitor_groups)}")
        chips.append(f"EBK 组 {len(ebk_groups)}")
        # Latest trusted price sample (own hotel first) from the first competitor group.
        price_line = ""
        for group in competitor_groups:
            trusted = group.get("latest_trusted") if isinstance(group, dict) else None
            if not isinstance(trusted, dict):
                continue
            for entry in trusted.get("hotels", []) if isinstance(trusted.get("hotels"), list) else []:
                if isinstance(entry, dict) and entry.get("role") == "own" and entry.get("min_trusted_price_yuan") is not None:
                    price_line = f"　·　本店最低 ¥{entry['min_trusted_price_yuan']}"
                    break
            if price_line:
                break
        note = hotel.get("note")
        note_line = f"\n　_({_escape_lark_md(note)})_" if note else ""
        elements.append(_card_text(f"**{title}**　{' · '.join(chips)}{price_line}{note_line}"))

    analysis = str(operator.get("analysis") or operator.get("summary") or "").strip()
    if analysis:
        capped = analysis[:WEBHOOK_ANALYSIS_CHAR_LIMIT]
        if len(analysis) > WEBHOOK_ANALYSIS_CHAR_LIMIT:
            capped = capped.rstrip() + "…"
        elements.append(_card_text(f"**📝 简析（AI 内容）**\n{_escape_lark_md(capped)}"))
    elif not hotels:
        elements.append(_card_text("暂无可汇总的店铺数据。"))

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {"tag": "plain_text", "content": "跨店数据报表"},
        },
        "elements": elements,
    }
    return enforce_card_byte_limit(card)


def attach_feishu_card(payload: dict[str, Any], card_kind: str) -> dict[str, Any]:
    builders = {
        "ebk_latest": build_ebk_latest_card,
        "beneficial_data": build_beneficial_data_card,
        "ebk_datasets_read": build_ebk_datasets_read_card,
        "competitor_latest": build_competitor_latest_card,
        "competitor_price_trend": build_competitor_price_trend_card,
        "scheduler_status": build_scheduler_status_card,
        "price_change": build_price_change_card,
        "hotel_overview": build_hotel_overview_card,
        "manual_price_confirmation": build_manual_price_confirmation_card,
        "watch_configuration": build_watch_configuration_card,
        "chat_context": build_chat_context_card,
        "recent_activity": build_recent_activity_card,
        "weekly_briefing": build_weekly_briefing_card,
        "ebk_joint_brief": build_ebk_joint_brief_card,
        "mobile_summary": build_mobile_summary_card,
        "analytics_report": build_analytics_report_card,
    }
    builder = builders.get(card_kind)
    if builder is None:
        return payload
    card = builder(payload)
    return {**payload, "feishu_card": card, "feishu_card_kind": card_kind}
