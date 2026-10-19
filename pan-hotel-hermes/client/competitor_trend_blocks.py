from __future__ import annotations

import html
import re
from typing import Any

from client.competitor_trend_model import extract_series_timeline, summarize_price_process


def _escape_lark_md(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def _date_label_text(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {"today": "今日", "tomorrow": "明日", "今日": "今日", "明日": "明日"}
    return mapping.get(text, text or "日期")


def _breakfast_label(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "none": "无早",
        "no_breakfast": "无早",
        "included": "含早",
        "with_breakfast": "含早",
        "breakfast": "含早",
    }
    return mapping.get(text, text or "价格")


def _short_hotel_name(name: str, *, max_length: int = 28) -> str:
    cleaned = re.sub(r"\s+", "", str(name or "")).strip()
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 1] + "…"


def _hotel_icon(role: str) -> str:
    return "🏠" if role == "own" else "🏪"


def build_trend_grouped_mobile_blocks(series_list: list[dict[str, Any]]) -> list[str]:
    """按酒店+入住日落组；文字描述连续读价过程，不做区间首尾两天对比。"""
    grouped: dict[tuple[Any, Any], dict[str, Any]] = {}
    for series in series_list:
        if not isinstance(series, dict):
            continue
        key = (series.get("hotel_config_id"), series.get("date_label"))
        bucket = grouped.setdefault(
            key,
            {
                "hotel_name": series.get("hotel_alias") or series.get("hotel_name") or "未命名酒店",
                "hotel_role": str(series.get("hotel_role") or ""),
                "date_label": series.get("date_label"),
                "sort_order": series.get("sort_order", 0),
                "room_type": series.get("room_type") or "",
                "room_types": [],
                "breakfast_process": {},
            },
        )
        for candidate in series.get("room_types") or []:
            text = str(candidate or "").strip()
            if text and text not in bucket["room_types"]:
                bucket["room_types"].append(text)
        breakfast_kind = str(series.get("breakfast_kind") or "")
        timeline = extract_series_timeline(series)
        if not timeline:
            continue
        bucket["breakfast_process"][breakfast_kind] = summarize_price_process(timeline)
        room_type = str(series.get("room_type") or "").strip()
        if room_type and (not bucket["room_type"] or room_type != "房型有变化"):
            bucket["room_type"] = room_type

    blocks: list[str] = []
    sorted_groups = sorted(
        grouped.values(),
        key=lambda item: (
            item.get("hotel_role") != "own",
            item.get("sort_order", 0) if isinstance(item.get("sort_order"), int) else 0,
            str(item.get("hotel_name") or ""),
            str(item.get("date_label") or ""),
        ),
    )
    for item in sorted_groups:
        breakfast_process = item.get("breakfast_process") if isinstance(item.get("breakfast_process"), dict) else {}
        if not breakfast_process:
            continue
        hotel = _short_hotel_name(str(item.get("hotel_name") or ""))
        if item.get("hotel_role") == "own":
            hotel = f"{hotel}（本店）"
        date_text = _date_label_text(item.get("date_label"))
        # 「房型有变化」是后端多房型时的内部标记，不直接展示给店长：
        # 回退到真实房型列表（多个时标注数量），没有列表则显示 -。
        raw_room_type = str(item.get("room_type") or "").strip()
        room_type_list = item.get("room_types") if isinstance(item.get("room_types"), list) else []
        if not raw_room_type or raw_room_type == "房型有变化":
            if room_type_list:
                first = str(room_type_list[0])
                raw_room_type = first if len(room_type_list) == 1 else f"{first} 等{len(room_type_list)}个房型"
            else:
                raw_room_type = "-"
        room_type = _short_hotel_name(raw_room_type, max_length=28)
        parts: list[str] = []
        for kind in ("none", "included", "no_breakfast", "with_breakfast"):
            if kind not in breakfast_process:
                continue
            parts.append(f"{_breakfast_label(kind)} {_escape_lark_md(breakfast_process[kind])}")
        if not parts:
            continue
        blocks.append(
            f"{_hotel_icon(str(item.get('hotel_role') or ''))} {_escape_lark_md(hotel)}\n"
            f"📅 {_escape_lark_md(date_text)}入住\n"
            f"▶ 房型：{_escape_lark_md(room_type)}\n"
            f"▶ {' | '.join(parts)}"
        )
    return blocks[:10]
