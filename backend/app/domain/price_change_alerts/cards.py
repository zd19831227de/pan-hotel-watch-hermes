from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.price_change_alerts.types import PriceChangeAlertResult


def build_price_change_alert_feishu_card(
    result: PriceChangeAlertResult,
    *,
    title: str = "价格变化提醒",
) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    elements = [
        *_alert_item_elements(result),
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red" if result.status == "suggestion_ready" else "blue",
            "title": {"tag": "plain_text", "content": f"{price_change_alert_title(title)} · {now:%H:%M}"},
        },
        "elements": elements,
    }


def build_price_change_alert_fallback_text(result: PriceChangeAlertResult) -> str:
    lines = ["价格变化提醒"]
    for item, kind in _fallback_items(result):
        lines.append(_plain_warning_subtitle(item))
        if kind == "suggestion":
            previous_price = item.get("competitor_previous_price_yuan")
            current_price = item.get("competitor_current_price_yuan")
            change_amount = item.get("change_amount_yuan")
            change_percent = item.get("change_percent")
            suggestion_copy = (
                f"，建议本店降价{_signed(item.get('recommended_adjustment_yuan'))}，"
                f"建议后¥{item.get('recommended_price_yuan')}"
            )
        else:
            previous_price = item.get("previous_price_yuan")
            current_price = item.get("current_price_yuan")
            change_amount = item.get("signed_delta_yuan")
            change_percent = item.get("delta_percent")
            suggestion_copy = "，仅提醒竞品涨价，不建议本店涨价"
        lines.append(
            (
                f"{_plain_hotel_name(item)} {_plain_date_label(item)}: "
                f"竞品¥{previous_price}→¥{current_price}，"
                f"变化{_signed(change_amount)} / {_format_percent(change_percent)}"
                f"{suggestion_copy}"
            )
        )
    return "\n".join(lines)


def _alert_item_elements(result: PriceChangeAlertResult) -> list[dict[str, Any]]:
    entries: list[tuple[dict[str, Any], str]] = [
        (_with_result_policy(item, result), "suggestion") for item in result.suggestions
    ]
    suggested_keys = {_suggestion_change_key(item) for item in result.suggestions}
    entries.extend(
        (_with_result_policy(item, result), "change")
        for item in result.changed_items
        if _changed_item_key(item) not in suggested_keys
    )
    elements: list[dict[str, Any]] = []
    for item, kind in sorted(entries, key=_alert_item_sort_key)[:12]:
        if kind == "suggestion":
            elements.append(_text(_suggestion_block(item)))
        else:
            elements.append(_text(_change_block(item)))
    return elements or [_text("暂无超过阈值的价格变化。")]


def _suggestion_block(item: dict[str, Any]) -> str:
    hotel = _escape(item.get("hotel_name") or "竞品酒店")
    date = _date_label(item)
    room = _escape(item.get("room_type") or "-")
    breakfast = _breakfast_label(item.get("breakfast_kind"))
    lines = [
        _warning_subtitle(item),
        f"🏨 {hotel} | {date} | {breakfast}",
        f"房型：{room}",
        (
            f"📉 竞品降价：**¥{item.get('competitor_previous_price_yuan', '-')} → "
            f"¥{item.get('competitor_current_price_yuan', '-')}**"
        ),
        (
            f"变化：{_colored_change_amount(item.get('change_amount_yuan'))} / "
            f"{_format_percent(item.get('change_percent'))}"
        ),
        (
            f"🏠 本店当前价：**¥{item.get('current_own_price_yuan', '-')}**；"
            f"建议本店降价：**{_signed(item.get('recommended_adjustment_yuan'))}**；"
            f"建议后价格：**¥{item.get('recommended_price_yuan', '-')}**"
        ),
        f"📝 原因：{_escape(item.get('trigger_reason') or '-')}",
    ]
    return "\n".join(lines)


def price_change_alert_title(title: str | None) -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    if not text:
        return "价格变化提醒"
    if text.endswith("价格变化提醒"):
        return text
    if text.endswith("简报"):
        text = text[: -len("简报")].strip()
    return f"{text}价格变化提醒"


def _change_block(item: dict[str, Any]) -> str:
    hotel = _escape(_plain_hotel_name(item) or "竞品酒店")
    date = _date_label(item)
    room = _escape(item.get("room_type") or "-")
    breakfast = _breakfast_label(item.get("breakfast_kind"))
    direction = "涨价" if int(item.get("signed_delta_yuan") or 0) > 0 else "降价"
    tail = "仅提醒竞品涨价，不建议本店涨价。" if direction == "涨价" else "本轮未生成本店调价建议。"
    return "\n".join(
        [
            _warning_subtitle(item),
            f"🏨 {hotel} | {date} | {breakfast}",
            f"房型：{room}",
            (
                f"{_change_direction_emoji(direction)} 竞品{direction}：**¥{item.get('previous_price_yuan', '-')} → "
                f"¥{item.get('current_price_yuan', '-')}**"
            ),
            (
                f"变化：{_colored_change_amount(item.get('signed_delta_yuan'))} / "
                f"{_format_percent(item.get('delta_percent'))}"
            ),
            tail,
        ]
    )


def _status_label(status: str) -> str:
    return {
        "suggestion_ready": "已生成规则化建议",
        "change_detected": "仅提醒价格变化",
        "no_trigger": "未超过阈值",
        "data_required": "缺少可信数据",
        "needs_review": "需要复核",
        "config_required": "需要配置",
        "failed": "检测失败",
    }.get(status, status)


def _breakfast_label(value: Any) -> str:
    return {"none": "无早", "included": "含早", "unknown": "早餐未知"}.get(str(value or ""), "-")


def _date_label(item: dict[str, Any]) -> str:
    label = _relative_date_label(item.get("date_label"))
    date = _escape(item.get("checkin_date") or "")
    if label and date:
        return f"{label}入住 · {date}"
    if label:
        return f"{label}入住"
    return date or "-"


def _plain_date_label(item: dict[str, Any]) -> str:
    label = _relative_date_label(item.get("date_label"))
    date = str(item.get("checkin_date") or "").strip()
    if label and date:
        return f"{label}入住 {date}"
    if label:
        return f"{label}入住"
    return date or "-"


def _relative_date_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    return {"today": "今日", "tomorrow": "明日", "今日": "今日", "明日": "明日"}.get(text, "")


def _plain_hotel_name(item: dict[str, Any]) -> str:
    hotel = item.get("hotel") if isinstance(item.get("hotel"), dict) else {}
    return str(item.get("hotel_name") or hotel.get("alias") or hotel.get("hotel_name") or "竞品酒店")


def _fallback_items(result: PriceChangeAlertResult) -> list[tuple[dict[str, Any], str]]:
    suggested_keys = {_suggestion_change_key(item) for item in result.suggestions}
    items: list[tuple[dict[str, Any], str]] = [
        (_with_result_policy(item, result), "suggestion") for item in result.suggestions[:8]
    ]
    for item in result.changed_items:
        if len(items) >= 8:
            break
        if _changed_item_key(item) not in suggested_keys:
            items.append((_with_result_policy(item, result), "change"))
    return items


def _with_result_policy(item: dict[str, Any], result: PriceChangeAlertResult) -> dict[str, Any]:
    enriched = dict(item)
    if "connection_mode" not in enriched:
        enriched["connection_mode"] = result.connection_mode
    if "write_policy_message" not in enriched:
        enriched["write_policy_message"] = result.write_policy_message
    return enriched


def _alert_item_sort_key(entry: tuple[dict[str, Any], str]) -> tuple[int, str, str, str]:
    item, kind = entry
    return (
        _date_sort_order(item.get("date_label")),
        str(item.get("checkin_date") or ""),
        _plain_hotel_name(item),
        "0" if kind == "suggestion" else "1",
    )


def _date_sort_order(value: Any) -> int:
    label = _relative_date_label(value)
    if label == "今日":
        return 0
    if label == "明日":
        return 1
    return 2


def _warning_subtitle(item: dict[str, Any]) -> str:
    label = _relative_date_label(item.get("date_label"))
    if label == "今日":
        return "**今日预警**"
    if label == "明日":
        return "**明日预警**"
    return "**价格预警**"


def _plain_warning_subtitle(item: dict[str, Any]) -> str:
    label = _relative_date_label(item.get("date_label"))
    if label == "今日":
        return "今日预警"
    if label == "明日":
        return "明日预警"
    return "价格预警"


def _non_auto_write_warning(item: dict[str, Any]) -> str | None:
    # 盯盘价格变化提醒卡自身不再展示该提示（2026-08-22 运营反馈）；
    # 调价审批链路的卡片仍复用本函数展示拦截说明。
    mode = str(item.get("connection_mode") or "").strip().lower()
    if mode in {"direct", "unknown", "直连", "未确认"}:
        return "**<font color='red'>直连/未确认门店无法自动调价</font>**"
    policy = str(item.get("write_policy_message") or "").strip()
    if "直连" in policy or "未确认" in policy:
        return "**<font color='red'>直连/未确认门店无法自动调价</font>**"
    return None

def _suggestion_change_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("hotel_config_id") or ""),
        str(item.get("checkin_date") or item.get("date_label") or ""),
        str(item.get("breakfast_kind") or ""),
        str(item.get("normalized_room_type") or item.get("room_type") or ""),
    )


def _changed_item_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    hotel = item.get("hotel") if isinstance(item.get("hotel"), dict) else {}
    return (
        str(hotel.get("config_id") or item.get("hotel_config_id") or ""),
        str(item.get("checkin_date") or item.get("date_label") or ""),
        str(item.get("breakfast_kind") or ""),
        str(item.get("room_type_match_key") or item.get("normalized_room_type") or item.get("room_type") or ""),
    )


def _signed(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "-"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "-"
    if number > 0:
        return f"+¥{number}"
    if number < 0:
        return f"-¥{abs(number)}"
    return "¥0"


def _colored_change_amount(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "-"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "-"
    if number > 0:
        return f"<font color='red'>↑{number} 元</font>"
    if number < 0:
        return f"<font color='green'>↓{abs(number)} 元</font>"
    return "0 元"


def _change_direction_emoji(direction: str) -> str:
    return "📈" if direction == "涨价" else "📉"


def _format_percent(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "-"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


def _text(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _escape(value: Any) -> str:
    text = html.escape(str(value), quote=False)
    return (
        text.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
