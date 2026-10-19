"""「查询房价」结果的飞书卡片（样式对齐 EBK 全局推送卡）。

每平台一组表格（房型 / 卖价 / 底价 / 房态），复用
``feishu_card_tables.render_metric_group_pages`` 的分组分页渲染；
一张卡放不下时按全局推送的分页连发约定输出多张卡。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domain.browser.pricing_room_rates import room_rate_table_rows
from app.domain.notifications.feishu_card_tables import (
    escape_lark_md,
    render_metric_group_pages,
)

_PLATFORM_ICONS = {"ctrip": "✈️", "meituan": "🍱", "fliggy": "🐷"}
_DATE_LABELS = {"today": "今天入住", "tomorrow": "明天入住"}

_STATUS_LABELS = {
    "success": "成功",
    "partial": "部分成功",
    "human_required": "需人工处理",
    "page_structure_changed": "页面结构变化",
    "not_found": "未读取到价格",
    "failed": "失败",
    "data_required": "缺少数据",
    "account_missing": "未绑定账号",
    "skipped": "已跳过",
}

_STATUS_ICONS = {
    "success": "✅",
    "partial": "⚠️",
    "human_required": "⚠️",
    "page_structure_changed": "⚠️",
    "not_found": "⚠️",
    "failed": "❌",
    "data_required": "⚠️",
    "account_missing": "ℹ️",
    "skipped": "⏭️",
}


def build_pricing_query_cards(
    *,
    platform_results: list[dict[str, Any]],
    date_label: str,
    checkin_date: str,
    store_name: str | None = None,
) -> list[dict[str, Any]]:
    """把各平台读取结果组装成一张或多张「房价查询」卡片。"""

    groups: list[dict[str, Any]] = []
    overall_status = "success"
    for result in platform_results:
        if not isinstance(result, dict):
            continue
        platform = str(result.get("platform") or "").strip().lower()
        platform_label = str(result.get("platform_label") or platform or "未知")
        icon = _PLATFORM_ICONS.get(platform, "📋")
        status = str(result.get("status") or "failed")
        if status != "success" and overall_status == "success":
            overall_status = "human_required" if status == "human_required" else "partial"
        rows = room_rate_table_rows(list(result.get("room_rates") or []))
        subtitle = _platform_subtitle(result)
        if not rows:
            groups.append(
                {
                    "title": f"{icon}{platform_label}",
                    "subtitle": subtitle,
                    "rows": [
                        {
                            "field": "读取结果",
                            "parts": [
                                f"{_status_icon(status)} {_status_label(status)}："
                                f"{_escape(result.get('summary') or '-')}"
                            ],
                        }
                    ],
                    "part_headers": ["结果"],
                }
            )
            continue
        groups.append(
            {
                "title": f"{icon}{platform_label}｜{len(rows)} 项",
                "subtitle": subtitle,
                "rows": rows,
                "part_headers": ["卖价", "底价", "房态"],
                "field_header": "房型",
            }
        )

    if not groups:
        return [
            _wrap_card(
                elements=[_card_text("当前群没有可查询的 EBK 账号绑定，请先在高级配置中完成绑定。")],
                overall_status="config_required",
                date_label=date_label,
                checkin_date=checkin_date,
                store_name=store_name,
                page_index=1,
                page_total=1,
            )
        ]

    pages = render_metric_group_pages(groups, max_tables=5)
    cards = [
        _wrap_card(
            elements=page,
            overall_status=overall_status,
            date_label=date_label,
            checkin_date=checkin_date,
            store_name=store_name,
            page_index=index + 1,
            page_total=len(pages),
        )
        for index, page in enumerate(pages)
    ]
    return cards


def build_pricing_query_fallback_text(cards: list[dict[str, Any]]) -> str:
    lines = ["Pan Hotel Watch 房价查询"]
    for card in cards[:1]:
        for element in card.get("elements") or []:
            if not isinstance(element, dict):
                continue
            text = element.get("text") if isinstance(element.get("text"), dict) else {}
            content = text.get("content")
            if content:
                lines.append(str(content).replace("**", ""))
                break
    return "\n".join(lines)


def _platform_subtitle(result: dict[str, Any]) -> str:
    platform = str(result.get("platform") or "").strip().lower()
    status = str(result.get("status") or "failed")
    summary = str(result.get("summary") or "").strip()
    if platform != "ctrip":
        summary = summary or "现价以页面可见数据为准（页面未展示的项如实留空）"
    header = f"{_status_icon(status)} {_status_label(status)}"
    return f"{header}｜{summary}" if summary else header


def _wrap_card(
    *,
    elements: list[dict[str, Any]],
    overall_status: str,
    date_label: str,
    checkin_date: str,
    store_name: str | None,
    page_index: int,
    page_total: int,
) -> dict[str, Any]:
    title_suffix = f"（{page_index}/{page_total}）" if page_total > 1 else ""
    header_lines = []
    if store_name:
        header_lines.append(f"**🏨 门店**：{_escape(store_name)}")
    header_lines.extend(
        [
            f"**📅 口径**：{_DATE_LABELS.get(date_label, date_label)}（{checkin_date}）",
            f"**{_status_icon(overall_status)} 整体状态**：{_status_label(overall_status)}",
            "**💡 提示**：价格为无早口径（单早/双早随无早联动调整）；房型名可直接用于调价指令，例如「把今天<房型名>调到520」。",
        ]
    )
    all_elements = [_card_text(chr(10).join(header_lines)), {"tag": "hr"}, *elements]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _card_template(overall_status),
            "title": {
                "tag": "plain_text",
                "content": f"Pan Hotel Watch 房价查询{title_suffix}",
            },
        },
        "elements": all_elements,
    }


def _card_template(status: str) -> str:
    if status == "success":
        return "green"
    if status in {"human_required", "partial", "page_structure_changed", "not_found"}:
        return "orange"
    if status in {"failed", "error"}:
        return "red"
    return "blue"


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(str(status or "").strip().lower(), str(status or "未知"))


def _status_icon(status: str) -> str:
    return _STATUS_ICONS.get(str(status or "").strip().lower(), "ℹ️")


def _escape(value: Any) -> str:
    return escape_lark_md(value)


def _card_text(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def format_query_read_time(value: str | datetime | None) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value or "").strip()
    if not text:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    return text[:16]


__all__ = [
    "build_pricing_query_cards",
    "build_pricing_query_fallback_text",
    "format_query_read_time",
]
