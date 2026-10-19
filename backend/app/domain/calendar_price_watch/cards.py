from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.calendar_price_watch.types import (
    DEFAULT_HIGH_GAP_THRESHOLD_YUAN,
    DEFAULT_LOW_GAP_THRESHOLD_YUAN,
)

def calendar_price_alert_title(source: str | None) -> str:
    text = str(source or "").strip()
    return text if text.endswith("日历价监测") else f"{text or '携程'}日历价监测"


def build_calendar_price_alert_feishu_card(
    result: dict[str, Any],
    *,
    title: str = "日历价监测",
) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    future_days = int(result.get("future_days") or 15)
    alerts = [item for item in result.get("alerts", []) if isinstance(item, dict)]
    high_alerts = [item for item in alerts if item.get("alert_type") == "own_price_high"]
    low_alerts = [item for item in alerts if item.get("alert_type") == "own_price_low"]
    elements: list[dict[str, Any]] = [
        _plain_text(f"未来 {future_days} 天日历价预警"),
    ]
    if high_alerts:
        elements.append(_markdown_text("**本店偏高**"))
        for item in high_alerts[:12]:
            elements.append(_markdown_text(_high_alert_line(item)))
    if low_alerts:
        elements.append(_markdown_text("**本店明显低于竞对**"))
        for item in low_alerts[:12]:
            elements.append(_markdown_text(_low_alert_line(item)))
    elements.append(
        _plain_text("建议人工查看房型、早餐、促销和库存差异。")
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {
                "tag": "plain_text",
                "content": f"{calendar_price_alert_title(title)} · {now:%H:%M}",
            },
        },
        "elements": elements,
    }


def build_calendar_price_alert_fallback_text(result: dict[str, Any]) -> str:
    lines = [str(result.get("summary") or "日历价预警")]
    for item in result.get("alerts", []):
        if not isinstance(item, dict):
            continue
        lines.append(str(item.get("summary") or item.get("checkin_date") or "预警日期"))
    lines.append("请人工查看控制台中的日历价矩阵与预警详情。")
    return "\n".join(lines)


MATRIX_HOTEL_OWN_COLOR = "blue"
# Avoid orange/red in competitor legend — operators read those as alert tones.
MATRIX_COMPETITOR_COLORS = ("turquoise", "purple", "wathet", "violet", "lime")


def calendar_price_matrix_title(source: str | None) -> str:
    text = str(source or "").strip()
    return text if text.endswith("日历价矩阵") else f"{text or '携程'}日历价矩阵"


def build_calendar_price_matrix_feishu_cards(
    matrix: dict[str, Any],
    *,
    title: str = "日历价矩阵",
    slot_label: str = "矩阵简报",
) -> list[dict[str, Any]]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    dates = [item for item in matrix.get("dates", []) if isinstance(item, dict)]
    if not dates:
        return [
            _matrix_brief_card_shell(
                title=title,
                slot_label=slot_label,
                now=now,
                elements=[
                    _markdown_text("本次未读取到未来日历价，请确认竞品组酒店配置与浏览器登录状态。"),
                ],
                card_suffix="",
            )
        ]

    future_days = int(matrix.get("future_days") or len(dates))
    captured_at = _format_captured_at(matrix.get("run"))
    alert_count = _matrix_alert_count(dates)
    hotels = _matrix_hotel_legend(matrix, dates)
    high_gap = int(matrix.get("high_gap_threshold_yuan") or DEFAULT_HIGH_GAP_THRESHOLD_YUAN)
    low_gap = int(matrix.get("low_gap_threshold_yuan") or DEFAULT_LOW_GAP_THRESHOLD_YUAN)

    elements: list[dict[str, Any]] = [
        _markdown_text(
            f"未来 **{future_days}** 天日历价矩阵"
            + (f" · 读取于 {_escape_markdown(captured_at)}" if captured_at else "")
        )
    ]
    if alert_count:
        elements.append(
            _markdown_text(
                f"**预警**：{alert_count} 个日期本店价格偏离竞对最低价，详见控制台预警列表。"
            )
        )
    elements.append(_markdown_text(_matrix_legend_markdown(hotels)))
    # Feishu webhook cards drop collapsible_panel when it nests a table; keep
    # table as a top-level element (same pattern as competitor desktop cards).
    elements.extend(
        _matrix_comparison_section(
            dates=dates,
            hotels=hotels,
            high_gap_threshold_yuan=high_gap,
            low_gap_threshold_yuan=low_gap,
        )
    )
    elements.extend(
        [
            {"tag": "hr"},
            _markdown_text(f"**📝 简析**\n{_escape_markdown(_matrix_brief_summary(dates))}"),
            _markdown_text("数据来源：携程公共页日历 DOM，仅供参考，请人工核对房型与促销。"),
        ]
    )
    return [
        _matrix_brief_card_shell(
            title=title,
            slot_label=slot_label,
            now=now,
            elements=elements,
            card_suffix="",
        )
    ]


def build_calendar_price_matrix_fallback_text(matrix: dict[str, Any]) -> str:
    dates = [item for item in matrix.get("dates", []) if isinstance(item, dict)]
    lines = [
        str(matrix.get("summary") or "日历价矩阵简报"),
        f"未来 {int(matrix.get('future_days') or len(dates) or 0)} 天",
    ]
    for item in dates[:10]:
        own = item.get("own") if isinstance(item.get("own"), dict) else {}
        price = own.get("price_amount_yuan")
        price_text = f"¥{price}" if isinstance(price, int) else "待复核"
        lines.append(f"{item.get('checkin_date')} 本店 {price_text}")
    lines.append("请在手机端展开对照明细或登录控制台查看完整矩阵。")
    return "\n".join(lines)


def build_calendar_price_matrix_feishu_card(
    matrix: dict[str, Any],
    *,
    title: str = "日历价矩阵",
    slot_label: str = "矩阵简报",
) -> dict[str, Any]:
    cards = build_calendar_price_matrix_feishu_cards(matrix, title=title, slot_label=slot_label)
    return cards[0]


def _matrix_brief_card_shell(
    *,
    title: str,
    slot_label: str,
    now: datetime,
    elements: list[dict[str, Any]],
    card_suffix: str,
) -> dict[str, Any]:
    suffix = str(card_suffix or "").strip()
    header_title = f"{calendar_price_matrix_title(title)} · {slot_label} · {now:%H:%M}"
    if suffix and suffix not in header_title:
        header_title = f"{header_title} · {suffix}"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": header_title[:120]},
        },
        "elements": elements,
    }


def _matrix_hotel_legend(matrix: dict[str, Any], dates: list[dict[str, Any]]) -> list[dict[str, str]]:
    own_alias = ""
    for item in dates:
        own = item.get("own") if isinstance(item.get("own"), dict) else {}
        alias = str(own.get("hotel_alias") or "").strip()
        if alias:
            own_alias = alias
            break
    own_short = str(matrix.get("own_short_alias") or "").strip() or "本店"
    hotels = [
        {
            "code": own_short,
            "alias": own_alias or "本店",
            "color": MATRIX_HOTEL_OWN_COLOR,
            "role": "own",
        }
    ]
    for index, hotel in enumerate(_matrix_competitor_hotel_entries(matrix, dates)):
        alias = hotel["alias"]
        short_alias = hotel["short_alias"] or _fallback_short_alias(alias, index)
        hotels.append(
            {
                "code": short_alias,
                "alias": alias,
                "color": MATRIX_COMPETITOR_COLORS[index % len(MATRIX_COMPETITOR_COLORS)],
                "role": "competitor",
            }
        )
    return hotels


def _matrix_competitor_hotel_entries(
    matrix: dict[str, Any],
    dates: list[dict[str, Any]],
) -> list[dict[str, str | None]]:
    entries: list[dict[str, str | None]] = []
    seen: set[str] = set()
    raw = matrix.get("competitor_hotels")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            alias = str(item.get("alias") or "").strip()
            if not alias or alias in seen:
                continue
            seen.add(alias)
            short_alias = str(item.get("short_alias") or "").strip() or None
            entries.append({"alias": alias, "short_alias": short_alias})
    for alias in _matrix_hotel_sections(dates):
        if alias in seen:
            continue
        seen.add(alias)
        entries.append({"alias": alias, "short_alias": None})
    return entries


def _fallback_short_alias(alias: str, index: int) -> str:
    text = str(alias or "").strip()
    if not text:
        return f"店{index + 1}"
    # Prefer a short readable label when operators have not configured short_alias yet.
    if len(text) <= 6:
        return text
    return text[-6:]


def _matrix_legend_markdown(hotels: list[dict[str, str]]) -> str:
    lines = ["**图例**"]
    for hotel in hotels:
        lines.append(
            f"<font color='{hotel['color']}'>{_escape_markdown(hotel['code'])}</font>"
            f" · <font color='{hotel['color']}'>{_escape_markdown(hotel['alias'])}</font>"
        )
    return "\n".join(lines)


def _matrix_comparison_section(
    *,
    dates: list[dict[str, Any]],
    hotels: list[dict[str, str]],
    high_gap_threshold_yuan: int,
    low_gap_threshold_yuan: int,
) -> list[dict[str, Any]]:
    """Top-level hint + table.

    Feishu webhook interactive cards drop ``collapsible_panel`` when it nests a
    ``table``; competitor desktop cards already put ``table`` at card root.
    """
    day_count = len(dates)
    return [
        _markdown_text(f"**📋 {day_count}天对照** · 左右滑动查看"),
        _matrix_comparison_table(
            dates=dates,
            hotels=hotels,
            high_gap_threshold_yuan=high_gap_threshold_yuan,
            low_gap_threshold_yuan=low_gap_threshold_yuan,
        ),
    ]


def _matrix_comparison_table(
    *,
    dates: list[dict[str, Any]],
    hotels: list[dict[str, str]],
    high_gap_threshold_yuan: int,
    low_gap_threshold_yuan: int,
) -> dict[str, Any]:
    hotel_keys: list[tuple[str, dict[str, str]]] = []
    columns: list[dict[str, Any]] = [
        _matrix_table_column("date", "日期", width="70px", data_type="text"),
    ]
    for index, hotel in enumerate(hotels):
        key = "own" if hotel["role"] == "own" else f"comp_{index}"
        hotel_keys.append((key, hotel))
        columns.append(
            _matrix_table_column(key, hotel["code"], width="80px", data_type="lark_md")
        )
    rows = [
        _matrix_comparison_table_row(
            item,
            hotel_keys=hotel_keys,
            high_gap_threshold_yuan=high_gap_threshold_yuan,
            low_gap_threshold_yuan=low_gap_threshold_yuan,
        )
        for item in dates
    ]
    return {
        "tag": "table",
        "page_size": min(max(len(rows), 1), 10),
        "row_height": "low",
        "freeze_first_column": True,
        "header_style": {
            "text_align": "left",
            "background_style": "none",
            "text_size": "normal",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": rows,
    }


def _matrix_table_column(
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
        "vertical_align": "center",
    }


def _matrix_comparison_table_row(
    item: dict[str, Any],
    *,
    hotel_keys: list[tuple[str, dict[str, str]]],
    high_gap_threshold_yuan: int,
    low_gap_threshold_yuan: int,
) -> dict[str, str]:
    row: dict[str, str] = {"date": _short_date(item.get("checkin_date"))}
    for key, hotel in hotel_keys:
        if hotel["role"] == "own":
            own = item.get("own") if isinstance(item.get("own"), dict) else {}
            row[key] = _matrix_own_price_markdown(
                item,
                own=own,
                high_gap_threshold_yuan=high_gap_threshold_yuan,
                low_gap_threshold_yuan=low_gap_threshold_yuan,
            )
            continue
        competitor = next(
            (
                entry
                for entry in item.get("competitors", [])
                if isinstance(entry, dict) and entry.get("hotel_alias") == hotel["alias"]
            ),
            None,
        )
        if competitor is None:
            label = "待复核"
        else:
            label = _matrix_price_label(
                competitor.get("price_amount_yuan"), competitor.get("status")
            )
        row[key] = f"**{_escape_markdown(label)}**"
    return row


def _matrix_own_price_markdown(
    item: dict[str, Any],
    *,
    own: dict[str, Any],
    high_gap_threshold_yuan: int,
    low_gap_threshold_yuan: int,
) -> str:
    label = _matrix_price_label(own.get("price_amount_yuan"), own.get("status"))
    bold = f"**{_escape_markdown(label)}**"
    tone = _matrix_own_price_tone(
        item,
        own=own,
        high_gap_threshold_yuan=high_gap_threshold_yuan,
        low_gap_threshold_yuan=low_gap_threshold_yuan,
    )
    if tone:
        return f"<font color='{tone}'>{bold}</font>"
    return bold


def _matrix_own_price_tone(
    item: dict[str, Any],
    *,
    own: dict[str, Any],
    high_gap_threshold_yuan: int,
    low_gap_threshold_yuan: int,
) -> str | None:
    alerts = item.get("alerts") if isinstance(item.get("alerts"), list) else []
    types = {
        str(alert.get("alert_type") or "")
        for alert in alerts
        if isinstance(alert, dict)
    }
    if "own_price_high" in types:
        return "red"
    if "own_price_low" in types:
        return "green"

    own_price = own.get("price_amount_yuan")
    if not isinstance(own_price, int):
        return None
    lowest = _matrix_lowest_competitor_price(item)
    if lowest is None:
        return None
    if own_price - lowest >= high_gap_threshold_yuan:
        return "red"
    if lowest - own_price >= low_gap_threshold_yuan:
        return "green"
    return None


def _matrix_lowest_competitor_price(item: dict[str, Any]) -> int | None:
    prices = [
        int(row["price_amount_yuan"])
        for row in item.get("competitors", [])
        if isinstance(row, dict) and isinstance(row.get("price_amount_yuan"), int)
    ]
    return min(prices) if prices else None


def _matrix_hotel_sections(dates: list[dict[str, Any]]) -> list[str]:
    aliases: list[str] = []
    for item in dates:
        for competitor in item.get("competitors", []):
            if not isinstance(competitor, dict):
                continue
            alias = str(competitor.get("hotel_alias") or "").strip()
            if alias and alias not in aliases:
                aliases.append(alias)
    return aliases


def _matrix_alert_count(dates: list[dict[str, Any]]) -> int:
    return sum(1 for item in dates if _date_has_high_alert(item))


def _date_has_high_alert(item: dict[str, Any]) -> bool:
    alerts = item.get("alerts") if isinstance(item.get("alerts"), list) else []
    return any(
        isinstance(alert, dict) and alert.get("alert_type") == "own_price_high" for alert in alerts
    )


def _matrix_brief_summary(dates: list[dict[str, Any]]) -> str:
    high_count = _matrix_alert_count(dates)
    review_count = sum(
        1
        for item in dates
        if str((item.get("own") or {}).get("status") or "") in {"needs_review", "human_required"}
    )
    parts = [f"共 {len(dates)} 天"]
    if high_count:
        parts.append(f"本店偏高 {high_count} 天")
    if review_count:
        parts.append(f"待复核 {review_count} 天")
    if len(parts) == 1:
        parts.append("当前无预警")
    return "，".join(parts)


def _matrix_price_label(price: Any, status: Any) -> str:
    if isinstance(price, int):
        return f"¥{price}"
    if str(status or "") in {"needs_review", "human_required"}:
        return "待复核"
    return "暂无"


def _short_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[5:]
    return text


def _format_captured_at(run: Any) -> str | None:
    if not isinstance(run, dict):
        return None
    captured_at = str(run.get("captured_at") or "").strip()
    if not captured_at:
        return None
    try:
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        else:
            parsed = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
        return parsed.strftime("%m-%d %H:%M")
    except ValueError:
        return captured_at[:16]


def _high_alert_line(item: dict[str, Any]) -> str:
    alias = _escape_markdown(item.get("reference_hotel_alias") or "竞对")
    return (
        f"{_bold_date(item.get('checkin_date'))} 本店 "
        f"{_bold_colored_price(item.get('own_price_yuan'), 'red')}，"
        f"比 {alias} {_bold_price(item.get('reference_price_yuan'))} "
        f"高 {_bold_colored_price(item.get('gap_yuan'), 'red')}"
    )


def _low_alert_line(item: dict[str, Any]) -> str:
    alias = _escape_markdown(item.get("reference_hotel_alias") or "竞对")
    return (
        f"{_bold_date(item.get('checkin_date'))} 本店 "
        f"{_bold_colored_price(item.get('own_price_yuan'), 'green')}，"
        f"比 {alias} {_bold_price(item.get('reference_price_yuan'))} "
        f"低 {_bold_colored_price(item.get('gap_yuan'), 'green')}"
    )


def _plain_text(content: str) -> dict[str, Any]:
    return _markdown_text(_escape_markdown(content))


def _markdown_text(content: str) -> dict[str, Any]:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": content},
    }


def _bold_date(value: Any) -> str:
    return f"**{_escape_markdown(value)}**"


def _bold_price(value: Any) -> str:
    return f"**¥{_escape_markdown(value)}**"


def _bold_colored_price(value: Any, color: str) -> str:
    return f"**<font color='{color}'>¥{_escape_markdown(value)}</font>**"


def _escape_markdown(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("*", "\\*").replace("_", "\\_")
