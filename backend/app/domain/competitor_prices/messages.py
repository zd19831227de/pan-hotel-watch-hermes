from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.browser.ctrip_room_rate_rules import (
    BreakfastKind,
    classify_breakfast_kind,
    normalize_breakfast_text,
    rate_price_amount,
    room_type_comparison_key,
)

TRUSTED_PRICE_SOURCE = "room_rate_dom_display_price"
FEISHU_DETAIL_TABLE_PAGE_SIZE = 10
FEISHU_MAX_TABLES_PER_CARD = 5
FEISHU_MAX_HOTEL_TABLES_IN_MERGED_CARD = 4
DETAIL_TABLE_CONSOLE_HINT_THRESHOLD = 30
# Align with calendar price matrix legend — avoid orange/red alert tones.
COMPETITOR_HOTEL_LINE_COLORS = ("turquoise", "purple", "wathet", "violet", "lime")


def _date_labels_for_push_scope(push_date_scope: str | None) -> tuple[str, ...]:
    """推送日期范围：today_only 只拼今日，其余取值（含缺省）保持今日+明日。"""
    if str(push_date_scope or "").strip() == "today_only":
        return ("今日",)
    return ("今日", "明日")


def build_competitor_price_feishu_message(
    *,
    status: str,
    data: dict[str, Any],
    push_date_scope: str = "today_tomorrow",
) -> str:
    date_labels = _date_labels_for_push_scope(push_date_scope)
    snapshots = _snapshots(data)
    rows = _competitor_rows(snapshots)
    today_rows = [row for row in rows if row["date_label"] == "今日"]
    tomorrow_rows = (
        [row for row in rows if row["date_label"] == "明日"]
        if "明日" in date_labels
        else []
    )
    lines: list[str] = []
    if today_rows:
        lines.extend(_plain_date_section(today_rows, fallback="今日"))
    if tomorrow_rows:
        if lines:
            lines.append("")
        lines.extend(_plain_date_section(tomorrow_rows, fallback="明日"))
    summary = f"简析：{_format_compact_summary(data, today_rows)}"
    if lines:
        lines.extend(["", summary])
    else:
        lines.append(summary)
    return "\n".join(lines)


def build_competitor_price_feishu_card(
    *,
    status: str,
    data: dict[str, Any],
    title: str = "携程竞品价格盯盘",
    layout: str = "mobile_blocks",
    push_date_scope: str = "today_tomorrow",
) -> dict[str, Any]:
    normalized_layout = _normalize_layout(layout)
    if normalized_layout == "desktop_table":
        return _build_competitor_price_feishu_card_single(
            status=status,
            data=data,
            title=title,
            layout=normalized_layout,
            push_date_scope=push_date_scope,
        )
    cards = build_competitor_price_feishu_mobile_cards(
        status=status,
        data=data,
        title=title,
        push_date_scope=push_date_scope,
    )
    if cards:
        return cards[0]
    return _build_competitor_price_feishu_card_single(
        status=status,
        data=data,
        title=title,
        layout=normalized_layout,
        push_date_scope=push_date_scope,
    )


def build_competitor_price_feishu_mobile_cards(
    *,
    status: str,
    data: dict[str, Any],
    title: str = "携程竞品价格盯盘",
    push_date_scope: str = "today_tomorrow",
) -> list[dict[str, Any]]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    normalized_status = str(status or "").strip() or "unknown"
    snapshots = _snapshots(data)
    rows = _competitor_rows(snapshots)
    cards: list[dict[str, Any]] = []
    status_elements: list[dict[str, Any]] = []
    if normalized_status in {"needs_review", "human_required", "failed"}:
        status_elements.append(
            _text(
                f"**本轮状态**：{_escape_lark_md(_status_label(normalized_status))}，"
                "请人工复核携程页面。下列为本次读取结果（可能不完整）。"
            )
        )

    for date_label in _date_labels_for_push_scope(push_date_scope):
        detail_rows = _desktop_rate_detail_rows_for_date(snapshots, date_label=date_label)
        section_rows = [row for row in rows if row["date_label"] == date_label]
        hotel_groups = _group_detail_rows_by_hotel(detail_rows)
        if not hotel_groups:
            continue
        elements: list[dict[str, Any]] = []
        if status_elements and not cards:
            elements.extend(status_elements)
            status_elements = []
        if section_rows:
            elements.append(_text(_section_header(section_rows, fallback=date_label)))
        else:
            elements.append(_text(f"📅 {date_label}全量"))
        for hotel_display, hotel_rows in hotel_groups:
            elements.extend(
                _mobile_hotel_full_capture_elements(
                    hotel_display,
                    hotel_rows,
                    _summary_row_for_hotel(
                        section_rows,
                        hotel_display,
                        hotel_rows=hotel_rows,
                    ),
                )
            )
        card_label = _mobile_card_label(section_rows, date_label)
        elements.extend(
            [
                {"tag": "hr"},
                _text(
                    f"**📝 简析**\n"
                    f"{_escape_lark_md(_format_summary_from_rows(section_rows))}"
                ),
            ]
        )
        cards.append(
            _competitor_detail_card_shell(
                title=title,
                status=normalized_status,
                elements=elements,
                now=now,
                card_label=card_label,
            )
        )

    if not cards:
        return [
            _build_competitor_price_feishu_card_single(
                status=status,
                data=data,
                title=title,
                layout="mobile_blocks",
            )
        ]

    return cards


def _build_competitor_price_feishu_card_single(
    *,
    status: str,
    data: dict[str, Any],
    title: str,
    layout: str,
    push_date_scope: str = "today_tomorrow",
) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    normalized_status = str(status or "").strip() or "unknown"
    date_labels = _date_labels_for_push_scope(push_date_scope)
    snapshots = _snapshots(data)
    rows = [row for row in _competitor_rows(snapshots) if row["date_label"] in date_labels]
    elements: list[dict[str, Any]] = []
    if normalized_status in {"needs_review", "human_required", "failed"}:
        elements.append(
            _text(
                f"**本轮状态**：{_escape_lark_md(_status_label(normalized_status))}，"
                "请人工复核携程页面。下列为本次读取结果（可能不完整）。"
            )
        )
    elements.extend(
        _card_price_elements(
            rows,
            layout=_normalize_layout(layout),
            snapshots=snapshots,
            date_labels=date_labels,
        )
    )
    elements.extend(
        [
            {"tag": "hr"},
            _text(f"**📝 简析**\n{_escape_lark_md(_format_compact_summary(data, rows))}"),
        ]
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _competitor_card_header_template(normalized_status),
            "title": {"tag": "plain_text", "content": f"{title} · {now:%H:%M}"},
        },
        "elements": elements,
    }


def _mobile_card_label(
    section_rows: list[dict[str, str]],
    date_label: str,
) -> str:
    check_in = next((row.get("check_in") for row in section_rows if row.get("check_in")), "")
    base = check_in or date_label
    return f"{base}手机全量"


def build_competitor_price_detail_table_card(
    *,
    status: str,
    data: dict[str, Any],
    title: str = "携程竞品价格明细",
    push_date_scope: str = "today_tomorrow",
) -> dict[str, Any]:
    cards = build_competitor_price_detail_table_cards(
        status=status,
        data=data,
        title=title,
        push_date_scope=push_date_scope,
    )
    if cards:
        return cards[0]
    return build_competitor_price_feishu_card(
        status=status,
        data=data,
        title=title,
        layout="desktop_table",
        push_date_scope=push_date_scope,
    )


def build_competitor_price_detail_table_cards(
    *,
    status: str,
    data: dict[str, Any],
    title: str = "携程竞品价格明细",
    push_date_scope: str = "today_tomorrow",
) -> list[dict[str, Any]]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    normalized_status = str(status or "").strip() or "unknown"
    date_labels = _date_labels_for_push_scope(push_date_scope)
    snapshots = _snapshots(data)
    rows = _competitor_rows(snapshots)
    include_tomorrow = "明日" in date_labels
    today_detail = _desktop_rate_detail_rows_for_date(snapshots, date_label="今日")
    tomorrow_detail = (
        _desktop_rate_detail_rows_for_date(snapshots, date_label="明日")
        if include_tomorrow
        else []
    )
    today_section = [row for row in rows if row["date_label"] == "今日"]
    tomorrow_section = (
        [row for row in rows if row["date_label"] == "明日"] if include_tomorrow else []
    )
    today_groups = _group_detail_rows_by_hotel(today_detail)

    cards: list[dict[str, Any]] = []
    merged = _build_merged_today_full_tomorrow_summary_card(
        title=title,
        status=normalized_status,
        now=now,
        today_groups=today_groups,
        today_section=today_section,
        tomorrow_section=tomorrow_section,
        tomorrow_detail=tomorrow_detail,
    )
    if merged is not None:
        cards.append(merged)
    else:
        cards.extend(
            _build_full_detail_cards_for_date(
                title=title,
                status=normalized_status,
                now=now,
                date_label="今日",
                detail_rows=today_detail,
                section_rows=today_section,
            )
        )
        if include_tomorrow:
            cards.extend(
                _build_full_detail_cards_for_date(
                    title=title,
                    status=normalized_status,
                    now=now,
                    date_label="明日",
                    detail_rows=tomorrow_detail,
                    section_rows=tomorrow_section,
                )
            )

    if not cards:
        summary_elements = _card_price_elements(rows, layout="desktop_table")
        cards.append(
            _competitor_detail_card_shell(
                title=title,
                status=normalized_status,
                elements=summary_elements,
                now=now,
                card_label="摘要",
            )
        )
    else:
        total_rows = len(today_detail) + len(tomorrow_detail)
        if total_rows > DETAIL_TABLE_CONSOLE_HINT_THRESHOLD:
            cards[-1]["elements"].extend(
                [
                    {"tag": "hr"},
                    _text("明细较多，完整历史与趋势请在运营控制台「竞品盯盘」查看。"),
                ]
            )
    return cards


def _build_merged_today_full_tomorrow_summary_card(
    *,
    title: str,
    status: str,
    now: datetime,
    today_groups: list[tuple[str, list[dict[str, str]]]],
    today_section: list[dict[str, str]],
    tomorrow_section: list[dict[str, str]],
    tomorrow_detail: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not today_groups or not tomorrow_section:
        return None
    if len(today_groups) > FEISHU_MAX_HOTEL_TABLES_IN_MERGED_CARD:
        return None
    elements: list[dict[str, Any]] = [_text(_section_header(today_section, fallback="今日"))]
    for hotel_index, (hotel_display, hotel_rows) in enumerate(today_groups):
        if hotel_index:
            elements.append({"tag": "hr"})
        elements.append(_text(f"**{_escape_lark_md(_strip_leading_icon(hotel_display))}**"))
        elements.append(_hotel_rate_detail_table(hotel_rows))
    elements.extend(
        [
            {"tag": "hr"},
            _text(f"{_section_header(tomorrow_section, fallback='明日')}\n**各店最低价对照**"),
            _desktop_price_table(tomorrow_section),
        ]
    )
    if tomorrow_detail:
        elements.append(
            _text("明日各房型全量明细请在运营控制台「竞品盯盘」查看。")
        )
    check_in = next((row.get("check_in") for row in today_section if row.get("check_in")), "")
    check_out = next((row.get("check_out") for row in tomorrow_section if row.get("check_out")), "")
    card_label = f"{check_in}~{check_out}" if check_in and check_out else "全量明细"
    return _competitor_detail_card_shell(
        title=title,
        status=status,
        elements=elements,
        now=now,
        card_label=card_label,
    )


def _build_full_detail_cards_for_date(
    *,
    title: str,
    status: str,
    now: datetime,
    date_label: str,
    detail_rows: list[dict[str, str]],
    section_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    hotel_groups = _group_detail_rows_by_hotel(detail_rows)
    if not hotel_groups:
        return []
    cards: list[dict[str, Any]] = []
    part_count = (len(hotel_groups) + FEISHU_MAX_TABLES_PER_CARD - 1) // FEISHU_MAX_TABLES_PER_CARD
    for part_index, chunk_start in enumerate(
        range(0, len(hotel_groups), FEISHU_MAX_TABLES_PER_CARD)
    ):
        chunk = hotel_groups[chunk_start : chunk_start + FEISHU_MAX_TABLES_PER_CARD]
        elements: list[dict[str, Any]] = [_text(_section_header(section_rows, fallback=date_label))]
        if part_count > 1:
            elements.append(_text(f"**{date_label}全量 {part_index + 1}/{part_count}**"))
        for hotel_index, (hotel_display, hotel_rows) in enumerate(chunk):
            if hotel_index:
                elements.append({"tag": "hr"})
            elements.append(_text(f"**{_escape_lark_md(_strip_leading_icon(hotel_display))}**"))
            elements.append(_hotel_rate_detail_table(hotel_rows))
        card_label = _detail_card_label(section_rows, date_label, part_index + 1, part_count)
        cards.append(
            _competitor_detail_card_shell(
                title=title,
                status=status,
                elements=elements,
                now=now,
                card_label=card_label,
            )
        )
    return cards


def _detail_card_label(
    section_rows: list[dict[str, str]],
    date_label: str,
    part_index: int,
    part_count: int,
) -> str:
    check_in = next((row.get("check_in") for row in section_rows if row.get("check_in")), "")
    base = check_in or date_label
    if part_count <= 1:
        return f"{base}全量"
    return f"{base}全量 {part_index}/{part_count}"


def _competitor_detail_card_shell(
    *,
    title: str,
    status: str,
    elements: list[dict[str, Any]],
    now: datetime,
    card_label: str,
) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _competitor_card_header_template(status),
            "title": {
                "tag": "plain_text",
                "content": f"{title} · {card_label} · {now:%H:%M}",
            },
        },
        "elements": elements,
    }


def build_competitor_price_detail_table_message(
    *,
    status: str,
    data: dict[str, Any],
    push_date_scope: str = "today_tomorrow",
) -> str:
    snapshots = _snapshots(data)
    rows = _competitor_rows(snapshots)
    lines: list[str] = []
    for date_label in _date_labels_for_push_scope(push_date_scope):
        detail_rows = _desktop_rate_detail_rows_for_date(snapshots, date_label=date_label)
        if not detail_rows:
            continue
        if lines:
            lines.append("")
        section_rows = [row for row in rows if row["date_label"] == date_label]
        lines.append(_strip_md(_section_header(section_rows, fallback=date_label)))
        lines.extend(_plain_rate_detail_lines(detail_rows, date_label=date_label))
    if not lines:
        return build_competitor_price_feishu_message(
            status=status,
            data=data,
            push_date_scope=push_date_scope,
        )
    return "\n".join(lines)


def _competitor_card_header_template(status: str) -> str:
    if status == "success":
        return "green"
    if status == "human_required":
        return "orange"
    if status in {"needs_review", "partial"}:
        return "yellow"
    if status == "failed":
        return "red"
    return "blue"


def _snapshots(data: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = data.get("snapshots")
    if not isinstance(snapshots, list):
        return []
    return [snapshot for snapshot in snapshots if isinstance(snapshot, dict)]


def _competitor_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for snapshot in snapshots:
        today = _competitor_row(snapshot, source=snapshot, date_label="今日")
        if today is not None:
            rows.append(today)
        tomorrow = snapshot.get("tomorrow")
        if isinstance(tomorrow, dict):
            row = _competitor_row(snapshot, source=tomorrow, date_label="明日")
            if row is not None:
                rows.append(row)
    return rows


def _competitor_row(
    snapshot: dict[str, Any],
    *,
    source: dict[str, Any],
    date_label: str,
) -> dict[str, str] | None:
    date_context = source.get("date_context")
    if not isinstance(date_context, dict):
        date_context = {}
    if str(source.get("status") or "success") != "success":
        room_type = _compact_room_type(source.get("room_type") or snapshot.get("room_type"))
        return {
            "date_label": date_label,
            "role": str(snapshot.get("role") or ""),
            "hotel": _compact_hotel_name(snapshot),
            "no_breakfast": "需复核",
            "no_breakfast_room_type": room_type,
            "no_breakfast_discount": "",
            "no_breakfast_label": "无早",
            "with_breakfast": "需复核",
            "with_breakfast_room_type": room_type,
            "with_breakfast_discount": "",
            "with_breakfast_label": "含早",
            "availability": _review_status(source),
            "check_in": str(date_context.get("check_in") or ""),
            "check_out": str(date_context.get("check_out") or ""),
        }

    no_breakfast = _breakfast_plan(source, kind="none")
    with_breakfast = _breakfast_plan(source, kind="included")
    if no_breakfast["price"] == "-" and with_breakfast["price"] == "-" and not _trusted_rates(source):
        return None
    return {
        "date_label": date_label,
        "role": str(snapshot.get("role") or ""),
        "hotel": _compact_hotel_name(snapshot),
        "no_breakfast": no_breakfast["price"],
        "no_breakfast_room_type": no_breakfast["room_type"],
        "no_breakfast_discount": no_breakfast["discount"],
        "no_breakfast_label": no_breakfast["label"],
        "with_breakfast": with_breakfast["price"],
        "with_breakfast_room_type": with_breakfast["room_type"],
        "with_breakfast_discount": with_breakfast["discount"],
        "with_breakfast_label": with_breakfast["label"],
        "availability": _compact_availability(source.get("availability")),
        "check_in": str(date_context.get("check_in") or ""),
        "check_out": str(date_context.get("check_out") or ""),
    }


def _card_price_elements(
    rows: list[dict[str, str]],
    *,
    layout: str,
    snapshots: list[dict[str, Any]] | None = None,
    date_labels: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    if not rows and not snapshots:
        return [_text("- 暂无稳定价格")]
    if layout == "desktop_table":
        if not rows:
            return [_text("- 暂无稳定价格")]
        elements: list[dict[str, Any]] = []
        for date_label in date_labels or ("今日", "明日"):
            section_rows = [row for row in rows if row["date_label"] == date_label]
            if not section_rows:
                continue
            if elements:
                elements.append({"tag": "hr"})
            elements.extend(_card_date_section(section_rows, fallback=date_label, layout=layout))
        return elements
    return _mobile_collapsible_detail_elements(snapshots or [], rows, date_labels=date_labels)


def _mobile_collapsible_detail_elements(
    snapshots: list[dict[str, Any]],
    summary_rows: list[dict[str, str]],
    *,
    date_labels: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for date_label in date_labels or ("今日", "明日"):
        detail_rows = _desktop_rate_detail_rows_for_date(snapshots, date_label=date_label)
        section_rows = [row for row in summary_rows if row["date_label"] == date_label]
        if not detail_rows and not section_rows:
            continue
        if elements:
            elements.append({"tag": "hr"})
        if section_rows:
            elements.append(_text(_section_header(section_rows, fallback=date_label)))
        elif detail_rows:
            elements.append(_text(f"📅 {date_label}全量"))
        hotel_groups = _group_detail_rows_by_hotel(detail_rows)
        if hotel_groups:
            for hotel_display, hotel_rows in hotel_groups:
                elements.extend(
                    _mobile_hotel_full_capture_elements(
                        hotel_display,
                        hotel_rows,
                        _summary_row_for_hotel(
                            section_rows,
                            hotel_display,
                            hotel_rows=hotel_rows,
                        ),
                    )
                )
        else:
            elements.extend(_text(_hotel_block(row)) for row in section_rows)
    if not elements:
        return [_text("- 暂无稳定价格")]
    return elements


def _mobile_hotel_full_capture_elements(
    hotel_display: str,
    hotel_rows: list[dict[str, str]],
    summary_row: dict[str, str] | None,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    if summary_row is not None:
        extra_groups = _extra_room_type_groups(hotel_rows, summary_row)
        elements.append(
            _text(_mobile_hotel_primary_summary(summary_row, extra_count=len(extra_groups)))
        )
        if extra_groups:
            elements.append(
                _collapsible_hotel_panel(
                    hotel_display,
                    extra_groups,
                    expanded=False,
                    panel_title=_mobile_extra_room_panel_title(len(extra_groups)),
                    include_hotel_name=False,
                    line_builder=_mobile_extra_room_group_markdown_line,
                    line_separator="\n\n",
                )
            )
        return elements
    if hotel_rows:
        elements.append(
            _collapsible_hotel_panel(
                hotel_display,
                hotel_rows,
                expanded=False,
                panel_title=f"全量房型 · {len(hotel_rows)}条",
            )
        )
    return elements


def _extra_room_detail_rows(
    hotel_rows: list[dict[str, str]],
    summary_row: dict[str, str],
) -> list[dict[str, str]]:
    primary_keys = _primary_room_type_keys_from_summary(summary_row)
    if not primary_keys:
        return hotel_rows
    return [
        row
        for row in hotel_rows
        if room_type_comparison_key(str(row.get("room") or "")) not in primary_keys
    ]


def _primary_room_type_keys_from_summary(summary_row: dict[str, str]) -> set[str]:
    keys = {
        room_type_comparison_key(summary_row.get("no_breakfast_room_type")),
        room_type_comparison_key(summary_row.get("with_breakfast_room_type")),
        room_type_comparison_key(_room_type_for_row(summary_row)),
    }
    return {key for key in keys if key}


def _extra_room_type_groups(
    hotel_rows: list[dict[str, str]],
    summary_row: dict[str, str],
) -> list[dict[str, str]]:
    primary_keys = _primary_room_type_keys_from_summary(summary_row)
    groups: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in hotel_rows:
        key = room_type_comparison_key(str(row.get("room") or ""))
        if primary_keys and key in primary_keys:
            continue
        if not key:
            continue
        if key not in groups:
            groups[key] = {
                "room": _display_room_type(row.get("room")) or "-",
                "no_breakfast": "-",
                "no_breakfast_discount": "",
                "no_breakfast_label": "无早",
                "with_breakfast": "-",
                "with_breakfast_discount": "",
                "with_breakfast_label": "含早",
                "availability": str(row.get("availability") or "正常"),
            }
            order.append(key)
        group = groups[key]
        breakfast_kind = classify_breakfast_kind(str(row.get("breakfast") or ""))
        price = _plain_price_text(row.get("price"))
        discount = str(row.get("discount") or "").strip()
        candidate_room = _display_room_type(row.get("room"), max_length=28)
        if candidate_room and (
            group["room"] == "-"
            or len(candidate_room) < len(group["room"])
        ):
            group["room"] = candidate_room
        if breakfast_kind == "none":
            if _should_replace_group_price(group["no_breakfast"], price):
                group["no_breakfast"] = price
                group["no_breakfast_label"] = _breakfast_display_label(
                    row.get("breakfast"),
                    default="无早",
                )
                group["no_breakfast_discount"] = (
                    discount if discount and discount != "-" else ""
                )
        elif breakfast_kind == "included":
            if _should_replace_group_price(group["with_breakfast"], price):
                group["with_breakfast"] = price
                group["with_breakfast_label"] = _breakfast_display_label(
                    row.get("breakfast"),
                    default="含早",
                )
                group["with_breakfast_discount"] = (
                    discount if discount and discount != "-" else ""
                )
    return [groups[key] for key in order]


def _mobile_hotel_primary_summary(row: dict[str, str], *, extra_count: int) -> str:
    del extra_count
    hotel = _escape_lark_md(_short_hotel_name(row["hotel"], max_length=26))
    hotel_line = f"{_hotel_icon(row)} **{hotel}**"
    hotel_line = _hotel_line_md(row, hotel_line, own_suffix=True)
    room_type = _escape_lark_md(_room_type_for_row(row) or "-")
    inventory = f"{_inventory_icon(row)} {_escape_lark_md(row['availability'])}"
    no_breakfast_label = _escape_lark_md(str(row.get("no_breakfast_label") or "无早"))
    with_breakfast_label = _escape_lark_md(str(row.get("with_breakfast_label") or "含早"))
    return (
        f"{hotel_line}\n"
        f"主房型 {room_type}  {inventory}\n"
        f"{no_breakfast_label} {_price_value(row['no_breakfast'])}"
        f"{_discount_suffix(row.get('no_breakfast_discount', ''))}｜"
        f"{with_breakfast_label} {_price_value(row['with_breakfast'])}"
        f"{_discount_suffix(row.get('with_breakfast_discount', ''))}"
    )


def _mobile_extra_room_panel_title(extra_count: int) -> str:
    return f"其它 {extra_count} 个房型"


def _summary_row_for_hotel(
    section_rows: list[dict[str, str]],
    hotel_display: str,
    *,
    hotel_rows: list[dict[str, str]] | None = None,
) -> dict[str, str] | None:
    role = ""
    if hotel_rows:
        role = str(hotel_rows[0].get("role") or "").strip()
    if role:
        for row in section_rows:
            if row.get("role") == role:
                return row
    hotel_key = _normalize_hotel_match_key(hotel_display)
    for row in section_rows:
        row_hotel = _normalize_hotel_match_key(str(row.get("hotel") or ""))
        if row_hotel == hotel_key:
            return row
    for row in section_rows:
        row_hotel = _normalize_hotel_match_key(str(row.get("hotel") or ""))
        if hotel_key and row_hotel and (hotel_key in row_hotel or row_hotel in hotel_key):
            return row
    return None


def _normalize_hotel_match_key(value: str) -> str:
    text = _strip_leading_icon(value)
    text = text.replace("…", "").replace("（本店）", "").strip()
    return re.sub(r"\s+", "", text)


def _collapsible_hotel_panel(
    hotel_display: str,
    hotel_rows: list[dict[str, str]],
    *,
    expanded: bool,
    panel_title: str | None = None,
    line_builder: Any | None = None,
    include_hotel_name: bool = True,
    line_separator: str = "\n",
) -> dict[str, Any]:
    hotel_name = _escape_lark_md(_strip_leading_icon(hotel_display))
    title = panel_title or f"全量房型 · {len(hotel_rows)}条"
    build_line = line_builder or _mobile_hotel_rate_markdown_line
    header_content = f"**{title}** · {hotel_name}" if include_hotel_name else f"**{title}**"
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": {
                "tag": "markdown",
                "content": header_content,
            },
            "vertical_align": "center",
            "padding": "4px 0px 4px 8px",
            "icon": {
                "tag": "standard_icon",
                "token": "down-small-ccm_outlined",
                "color": "",
                "size": "16px 16px",
            },
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "elements": [
            {
                "tag": "markdown",
                "content": line_separator.join(build_line(row) for row in hotel_rows),
            }
        ],
    }


def _mobile_hotel_rate_markdown_line(row: dict[str, str]) -> str:
    room = _escape_lark_md(_display_room_type(str(row.get("room") or "")))
    breakfast = _escape_lark_md(str(row.get("breakfast") or "未知"))
    price = str(row.get("price") or "-").strip() or "-"
    discount = str(row.get("discount") or "").strip()
    discount_part = f" · {_escape_lark_md(discount)}" if discount and discount != "-" else ""
    inventory = _escape_lark_md(_plain_inventory_text(row))
    return f"· {room} · {breakfast} · {price}{discount_part} · {inventory}"


def _mobile_extra_room_group_markdown_line(row: dict[str, str]) -> str:
    room = _escape_lark_md(_display_room_type(str(row.get("room") or ""), max_length=28))
    no_breakfast = _price_value(str(row.get("no_breakfast") or "-"))
    with_breakfast = _price_value(str(row.get("with_breakfast") or "-"))
    no_discount = _discount_suffix(str(row.get("no_breakfast_discount") or ""))
    with_discount = _discount_suffix(str(row.get("with_breakfast_discount") or ""))
    no_breakfast_label = _escape_lark_md(str(row.get("no_breakfast_label") or "无早"))
    with_breakfast_label = _escape_lark_md(str(row.get("with_breakfast_label") or "含早"))
    availability = str(row.get("availability") or "正常")
    inventory = f"{_inventory_icon(row)} {_escape_lark_md(availability)}"
    return (
        f"{room}  {inventory}\n"
        f"{no_breakfast_label} {no_breakfast}{no_discount}｜"
        f"{with_breakfast_label} {with_breakfast}{with_discount}"
    )


def _card_date_section(
    rows: list[dict[str, str]],
    *,
    fallback: str,
    layout: str,
) -> list[dict[str, Any]]:
    elements = [_text(_section_header(rows, fallback=fallback))]
    if layout == "desktop_table":
        elements.append(_desktop_price_table(rows))
    else:
        elements.extend(_text(_hotel_block(row)) for row in rows)
    return elements


def _plain_date_section(rows: list[dict[str, str]], *, fallback: str) -> list[str]:
    lines = [_strip_md(_section_header(rows, fallback=fallback))]
    for row in rows:
        lines.append(_strip_md(_hotel_block(row)))
    return lines


def _section_header(rows: list[dict[str, str]], *, fallback: str) -> str:
    check_in = next((row["check_in"] for row in rows if row.get("check_in")), "")
    check_out = next((row["check_out"] for row in rows if row.get("check_out")), "")
    prefix = f"**{check_in} ~ {check_out}**\n" if check_in and check_out else ""
    if check_in and check_out:
        return f"{prefix}📅 {fallback}入住 | {check_in} ▶ {check_out} 离店"
    if check_in:
        return f"{prefix}📅 {fallback}入住 | {check_in}"
    return f"📅 {fallback}入住"


def _hotel_block(row: dict[str, str]) -> str:
    hotel = _escape_lark_md(_short_hotel_name(row["hotel"], max_length=30))
    hotel_line = f"{_hotel_icon(row)} {hotel}"
    hotel_line = _hotel_line_md(row, hotel_line, own_suffix=True)
    room_type = _escape_lark_md(_room_type_for_row(row) or "-")
    no_breakfast_label = _escape_lark_md(str(row.get("no_breakfast_label") or "无早"))
    with_breakfast_label = _escape_lark_md(str(row.get("with_breakfast_label") or "含早"))
    return (
        f"{hotel_line}\n"
        f"▶ 房型：{room_type}\n"
        f"▶ {no_breakfast_label} {_price_value(row['no_breakfast'])}"
        f"{_discount_suffix(row.get('no_breakfast_discount', ''))} | "
        f"{with_breakfast_label} {_price_value(row['with_breakfast'])}"
        f"{_discount_suffix(row.get('with_breakfast_discount', ''))}\n"
        f"{_inventory_icon(row)} 库存：**{_escape_lark_md(row['availability'])}**"
    )


def _desktop_price_table(rows: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "tag": "table",
        "page_size": min(max(len(rows), 1), 20),
        "row_height": "middle",
        "freeze_first_column": True,
        "header_style": {
            "text_align": "left",
            "background_style": "none",
            "text_size": "normal",
            "text_color": "grey",
            "bold": True,
        },
        "columns": [
            _table_column("date_label", "日期", width="70px", data_type="text"),
            _table_column("hotel", "酒店", width="180px", data_type="text"),
            _table_column("role_label", "类型", width="70px", data_type="text"),
            _table_column("room", "房型", width="150px", data_type="text"),
            _table_column("no_breakfast", "无早", width="80px", data_type="lark_md"),
            _table_column("with_breakfast", "含早", width="80px", data_type="lark_md"),
            _table_column("inventory", "库存", width="100px", data_type="lark_md"),
        ],
        "rows": [_desktop_table_row(row) for row in rows],
    }


def _table_column(
    data_key: str,
    display_name: str,
    *,
    width: str,
    data_type: str,
) -> dict[str, Any]:
    return {
        "name": data_key,
        "display_name": display_name,
        "data_type": data_type,
        "width": width,
        "horizontal_align": "left",
        "vertical_align": "top",
    }


def _desktop_table_row(row: dict[str, str]) -> dict[str, str]:
    hotel = _short_hotel_name(row["hotel"], max_length=20)
    if row.get("role") == "own":
        hotel = f"{hotel}（本店）"
    return {
        "date_label": row["date_label"],
        "hotel": f"{_hotel_icon(row)} {hotel}",
        "role_label": "本店" if row.get("role") == "own" else "竞品",
        "room": _room_type_for_row(row) or "-",
        "no_breakfast": _price_with_discount(
            row["no_breakfast"],
            row.get("no_breakfast_discount", ""),
        ),
        "with_breakfast": _price_with_discount(
            row["with_breakfast"],
            row.get("with_breakfast_discount", ""),
        ),
        "inventory": f"{_inventory_icon(row)} **{_escape_lark_md(row['availability'])}**",
    }


def _hotel_rate_detail_table(detail_rows: list[dict[str, str]]) -> dict[str, Any]:
    table_rows = [_hotel_rate_table_row(row) for row in detail_rows]
    return {
        "tag": "table",
        "page_size": min(max(len(table_rows), 1), FEISHU_DETAIL_TABLE_PAGE_SIZE),
        "row_height": "low",
        "header_style": {
            "text_align": "left",
            "background_style": "none",
            "text_size": "normal",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": [
            _table_column("room", "房型", width="28%", data_type="text"),
            _table_column("breakfast", "早餐", width="14%", data_type="text"),
            _table_column("price", "价格", width="14%", data_type="text"),
            _table_column("discount", "优惠", width="14%", data_type="text"),
            _table_column("inventory", "库存", width="18%", data_type="text"),
        ],
        "rows": table_rows,
    }


def _hotel_rate_table_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "room": _short_room_type_for_detail(str(row.get("room") or "")),
        "breakfast": str(row.get("breakfast") or "未知"),
        "price": _plain_price_text(row.get("price")),
        "discount": str(row.get("discount") or "-"),
        "inventory": _plain_inventory_text(row),
    }


def _plain_price_text(value: Any) -> str:
    text = str(value or "-").replace("**", "").strip()
    return text or "-"


def _plain_inventory_text(row: dict[str, str]) -> str:
    availability = str(row.get("availability") or "").strip()
    if availability:
        return availability
    return _strip_leading_icon(str(row.get("inventory") or "-").replace("**", "")) or "-"


def _group_detail_rows_by_hotel(
    detail_rows: list[dict[str, str]],
) -> list[tuple[str, list[dict[str, str]]]]:
    groups: list[tuple[str, list[dict[str, str]]]] = []
    current_key = ""
    current_display = ""
    bucket: list[dict[str, str]] = []
    for row in detail_rows:
        key = str(row.get("role") or "").strip() or _strip_leading_icon(row.get("hotel", ""))
        if key != current_key:
            if bucket:
                groups.append((current_display, bucket))
            current_key = key
            current_display = str(row.get("hotel") or "")
            bucket = [row]
        else:
            bucket.append(row)
    if bucket:
        groups.append((current_display, bucket))
    return groups


def _plain_rate_detail_lines(
    detail_rows: list[dict[str, str]],
    *,
    date_label: str,
) -> list[str]:
    lines = [f"{date_label}全量：{len(detail_rows)}条价格"]
    for display, hotel_rows in _group_detail_rows_by_hotel(detail_rows):
        lines.append("")
        lines.append(_strip_leading_icon(display))
        for row in hotel_rows:
            lines.append(_plain_hotel_rate_line(row))
    return lines


def _plain_hotel_rate_line(row: dict[str, str]) -> str:
    return (
        f"  · {_short_room_type_for_detail(str(row.get('room') or ''))}"
        f" · {row.get('breakfast') or '未知'}"
        f" · {_plain_price_text(row.get('price'))}"
        f" · {row.get('discount') or '-'}"
        f" · {_plain_inventory_text(row)}"
    )


def _desktop_rate_detail_rows_for_date(
    snapshots: list[dict[str, Any]],
    *,
    date_label: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for snapshot in snapshots:
        if date_label == "今日":
            source = snapshot
        else:
            tomorrow = snapshot.get("tomorrow")
            if not isinstance(tomorrow, dict):
                continue
            source = tomorrow
        if str(source.get("status") or "success") != "success":
            continue
        date_context = source.get("date_context")
        if not isinstance(date_context, dict):
            date_context = {}
        rows.extend(
            _desktop_rate_detail_rows(
                snapshot,
                source=source,
                date_label=date_label,
                date_context=date_context,
            )
        )
    return rows


def _desktop_rate_detail_rows(
    snapshot: dict[str, Any],
    *,
    source: dict[str, Any],
    date_label: str,
    date_context: dict[str, Any],
) -> list[dict[str, str]]:
    detail_rows: list[dict[str, str]] = []
    role = str(snapshot.get("role") or "")
    hotel_name = _compact_hotel_name(snapshot)
    hotel = _short_hotel_name(hotel_name, max_length=20)
    if role == "own":
        hotel = f"{hotel}（本店）"
    availability = _compact_availability(source.get("availability"))
    base_row = {"role": role, "hotel": hotel_name, "availability": availability}
    for rate in _trusted_rates(source):
        amount = rate_price_amount(rate)
        if amount is None:
            continue
        room = _compact_room_type(
            rate.get("room_type") or source.get("room_type") or snapshot.get("room_type")
        )
        breakfast = _breakfast_display_label(
            rate.get("breakfast") or rate.get("breakfast_text"),
            default="未知",
        )
        rate_availability = _compact_availability(rate.get("availability") or source.get("availability"))
        rate_icon_row = {**base_row, "availability": rate_availability}
        detail_rows.append(
            {
                "date_label": date_label,
                "role": role,
                "hotel": f"{_hotel_icon(base_row)} {hotel}",
                "role_label": "本店" if role == "own" else "竞品",
                "room": room or "-",
                "breakfast": breakfast or "未知",
                "price": _price_value(f"¥{amount}"),
                "discount": _rate_discount_display(rate) or "-",
                "availability": rate_availability,
                "inventory": (
                    f"{_inventory_icon(rate_icon_row)} **{_escape_lark_md(rate_availability)}**"
                ),
                "check_in": str(date_context.get("check_in") or ""),
                "check_out": str(date_context.get("check_out") or ""),
            }
        )
    return detail_rows


def _short_room_type_for_detail(value: str) -> str:
    return _display_room_type(value, max_length=28) or "-"


def _display_room_type(value: Any, *, max_length: int = 24) -> str:
    room_type = _compact_room_type(value)
    if not room_type:
        return ""
    room_type = _room_type_without_ctrip_promo_suffix(room_type)
    return _short_text(room_type, max_length)


def _room_type_without_ctrip_promo_suffix(room_type: str) -> str:
    for delimiter in ("『", "【", "丨", "|"):
        if delimiter not in room_type:
            continue
        primary = room_type.split(delimiter, 1)[0].strip()
        if _looks_like_display_room_type(primary):
            return primary
    return room_type


def _looks_like_display_room_type(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and any(marker in text for marker in ("房", "套", "间"))


def _strip_leading_icon(value: str) -> str:
    return re.sub(r"^[^\w\u4e00-\u9fff]+", "", str(value or "")).strip()


def _breakfast_plan(snapshot: dict[str, Any], *, kind: BreakfastKind) -> dict[str, str]:
    primary_key = _primary_room_type_key(snapshot)
    best_rate: dict[str, Any] | None = None
    best_amount: int | None = None
    for rate in _trusted_rates(snapshot):
        if primary_key and room_type_comparison_key(rate.get("room_type")) != primary_key:
            continue
        if not _rate_matches_breakfast_kind(rate, kind=kind):
            continue
        amount = rate_price_amount(rate)
        if amount is None:
            continue
        if best_amount is None or amount < best_amount:
            best_amount = amount
            best_rate = rate
    if best_rate is None or best_amount is None:
        return {
            "price": "-",
            "room_type": "",
            "discount": "",
            "label": _default_breakfast_label(kind),
        }
    return {
        "price": f"¥{best_amount}",
        "room_type": _compact_room_type(best_rate.get("room_type") or snapshot.get("room_type")),
        "discount": _rate_discount_display(best_rate),
        "label": _breakfast_display_label(
            best_rate.get("breakfast") or best_rate.get("breakfast_text"),
            default=_default_breakfast_label(kind),
        ),
    }


def _primary_room_type_key(snapshot: dict[str, Any]) -> str:
    for rate in _trusted_rates(snapshot):
        key = room_type_comparison_key(rate.get("room_type"))
        if key:
            return key
    return ""


def _trusted_rates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rates = snapshot.get("room_rates")
    if not isinstance(rates, list):
        return []
    return [
        rate
        for rate in rates
        if isinstance(rate, dict) and rate.get("source") == TRUSTED_PRICE_SOURCE
    ]


def _rate_matches_breakfast_kind(rate: dict[str, Any], *, kind: BreakfastKind) -> bool:
    text = str(rate.get("breakfast") or rate.get("breakfast_text") or "")
    return classify_breakfast_kind(text) == kind


def _should_replace_group_price(current: str, candidate: str) -> bool:
    candidate_amount = _price_amount_from_text(candidate)
    if candidate_amount is None:
        return not current or current == "-"
    current_amount = _price_amount_from_text(current)
    return current_amount is None or candidate_amount < current_amount


def _default_breakfast_label(kind: BreakfastKind) -> str:
    return "无早" if kind == "none" else "含早"


def _breakfast_display_label(value: Any, *, default: str) -> str:
    normalized = normalize_breakfast_text(str(value or ""))
    kind = classify_breakfast_kind(normalized)
    if kind == "none":
        return "无早"
    if kind == "included":
        if normalized in {"含单早", "含双早", "含三早"}:
            return normalized
        return "含早"
    return default


def _format_compact_summary(data: dict[str, Any], rows: list[dict[str, str]]) -> str:
    today_rows = [row for row in rows if row.get("date_label") == "今日"] or rows
    return _format_summary_from_rows(today_rows)


def _format_summary_from_rows(rows: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for label, field in (("无早", "no_breakfast"), ("含早", "with_breakfast")):
        own_amount = _price_amount_from_text(
            next((row[field] for row in rows if row.get("role") == "own"), "")
        )
        competitor_prices = [
            amount
            for row in rows
            if row.get("role") != "own"
            for amount in [_price_amount_from_text(row[field])]
            if amount is not None
        ]
        if own_amount is None or not competitor_prices:
            continue
        lowest = min(competitor_prices)
        if own_amount < lowest:
            parts.append(f"{label}：我们最低，领先¥{lowest - own_amount}")
        elif own_amount == lowest:
            parts.append(f"{label}：我们¥{own_amount}，与最低持平")
        else:
            parts.append(f"{label}：我们¥{own_amount}，比最低高¥{own_amount - lowest}")
    return "；".join(parts) if parts else "暂无稳定价差结论"


def _price_amount_from_text(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def _compact_hotel_name(snapshot: dict[str, Any]) -> str:
    return str(
        snapshot.get("configured_alias")
        or snapshot.get("hotel_alias")
        or snapshot.get("hotel_name")
        or snapshot.get("label")
        or "未命名酒店"
    )


def _compact_room_type(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _room_type_for_row(row: dict[str, str]) -> str:
    room_type = row.get("with_breakfast_room_type") or row.get("no_breakfast_room_type") or ""
    return _display_room_type(room_type, max_length=24)


def _compact_availability(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "正常"
    return (
        text.replace("房量紧张", "紧张")
        .replace("剩余", "仅剩")
        .replace(" ", "")
        [:12]
    )


def _review_status(source: dict[str, Any]) -> str:
    status = str(source.get("status") or "")
    reason = str(source.get("review_reason") or "")
    if status == "human_required":
        return "需人工"
    if status == "read_failed" or "failed" in reason:
        return "读取失败"
    return "需复核"


def _hotel_icon(row: dict[str, str]) -> str:
    hotel = row.get("hotel", "")
    if row.get("role") == "own":
        return "🏠"
    if "云滩" in hotel or "江景" in hotel:
        return "🌉"
    return "🏪"


def _hotel_line_md(row: dict[str, str], value: str, *, own_suffix: bool = False) -> str:
    role = str(row.get("role") or "").strip()
    if role == "own":
        suffix = "（本店）" if own_suffix else ""
        return _own_hotel_line_md(f"{value}{suffix}")
    color = _competitor_hotel_line_color(role)
    if color:
        return f"<font color='{color}'>{value}</font>"
    return value


def _competitor_hotel_line_color(role: str) -> str | None:
    match = re.fullmatch(r"competitor_(\d+)", role.strip())
    if match is None:
        return None
    index = int(match.group(1)) - 1
    if index < 0:
        return None
    return COMPETITOR_HOTEL_LINE_COLORS[index % len(COMPETITOR_HOTEL_LINE_COLORS)]


def _own_hotel_line_md(value: str) -> str:
    return f"<font color='blue'>{value}</font>"


def _inventory_icon(row: dict[str, str]) -> str:
    availability = row.get("availability", "")
    if _is_tight_availability(availability):
        return "🔴"
    if availability == "正常":
        return "🟢"
    return "🟡"


def _is_tight_availability(availability: str) -> bool:
    return any(keyword in availability for keyword in ("紧张", "仅剩", "剩余"))


def _price_value(value: str) -> str:
    escaped = _escape_lark_md(value)
    if value == "-" or "需" in value or "失败" in value:
        return escaped
    return f"**{escaped}**"


def _rate_discount_display(rate: dict[str, Any]) -> str:
    display = str(rate.get("discount_display") or "").strip()
    if display:
        return display[:64]
    amount = rate.get("discount_amount_yuan")
    if isinstance(amount, bool) or amount is None:
        return ""
    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        return ""
    if amount_int <= 0:
        return ""
    return f"优惠{amount_int}"


def _price_with_discount(price: str, discount: str) -> str:
    return f"{_price_value(price)}{_discount_suffix(discount)}"


def _discount_suffix(value: str) -> str:
    if not value:
        return ""
    escaped = _escape_lark_md(value)
    emphasized = re.sub(r"(\d+)", r"**\1**", escaped, count=1)
    return f"（{emphasized}）"


def _normalize_layout(value: Any) -> str:
    return "desktop_table" if str(value or "").strip() == "desktop_table" else "mobile_blocks"


def _text(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _short_hotel_name(name: str, *, max_length: int) -> str:
    compact = re.sub(r"\s+", "", name).strip()
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 1]}…"


def _short_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1]}…"


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


def _strip_md(value: str) -> str:
    text = re.sub(r"<font\s+color=['\"][^'\"]+['\"]>(.*?)</font>", r"\1", value)
    return text.replace("**", "")


def _status_label(status: str) -> str:
    labels = {
        "success": "读取成功",
        "needs_review": "需要复核",
        "human_required": "需要人工处理",
        "failed": "读取失败",
        "config_required": "需要配置",
    }
    return labels.get(status, status)
