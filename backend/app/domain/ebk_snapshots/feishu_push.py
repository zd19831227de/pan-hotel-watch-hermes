from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")

from app.domain.ctrip_ebooking_watch.repository import get_data_center_watch_group_runtime
from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook
from app.domain.notifications.feishu_card_tables import (
    render_metric_group_pages,
    split_value_parts,
)
from app.domain.operator_payload import sanitize_operator_payload

logger = logging.getLogger(__name__)

_PLATFORM_TITLES = {
    "meituan": "美团 EBK 读取结果",
    "fliggy": "飞猪 EBK 读取结果",
}

_DATASET_LABELS = {
    "workbench_home": "工作台",
    "data_center_home": "数据中心·首页",
    "data_center_trade": "数据中心·交易",
    "data_center_traffic": "数据中心·流量",
    "data_center_service": "数据中心·服务",
    "promotion_coupon": "神券活动",
    "comment_manage_home": "评价管理",
    "home_v1": "首页·今日/昨日",
    "diagnose_report": "诊断报告",
    "operating_data": "经营数据",
    "promotion_agent": "已参加活动",
}

_DATASET_ICONS = {
    "workbench_home": "🧭",
    "data_center_home": "📊",
    "data_center_trade": "💰",
    "data_center_traffic": "📈",
    "data_center_service": "🛎️",
    "promotion_coupon": "🎟️",
    "comment_manage_home": "💬",
    "home_v1": "🏠",
    "diagnose_report": "🩺",
    "operating_data": "📊",
    "promotion_agent": "🎯",
}
_DATASET_ICON_FALLBACK = "📋"

FEISHU_MAX_TABLES_PER_CARD = 5


async def push_platform_snapshot_to_ctrip_webhook(
    *,
    platform: str,
    platform_result: dict[str, Any],
    feishu_watch_group_key: str | None,
) -> dict[str, Any]:
    """Push a Meituan/Fliggy snapshot summary via the selected Ctrip EBK webhook."""

    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform not in _PLATFORM_TITLES:
        return {
            "status": "skipped",
            "summary": f"平台 {platform} 不支持通过携程 webhook 推送。",
        }

    watch_group_key = str(feishu_watch_group_key or "").strip()
    if not watch_group_key:
        return {
            "status": "skipped",
            "summary": "未指定携程 EBK 数据中心监控组，已跳过飞书推送。",
            "next_action": "在 EBK 操作台选中携程数据中心监控组后再读取并保存。",
        }

    group = await get_data_center_watch_group_runtime(watch_group_key=watch_group_key)
    if group is None:
        return {
            "status": "config_required",
            "summary": f"未找到携程 EBK 监控组：{watch_group_key}",
            "watch_group_key": watch_group_key,
            "next_action": "请确认操作台当前选中的携程数据中心监控组仍存在。",
        }

    webhook_url = str(getattr(group, "feishu_webhook_url", None) or "").strip()
    if not webhook_url:
        return {
            "status": "config_required",
            "summary": "当前携程 EBK 监控组未配置飞书机器人地址，已保存读取结果。",
            "watch_group_key": watch_group_key,
            "next_action": "在携程 EBK 数据中心监控组中配置飞书机器人地址。",
        }

    payload = platform_result.get("payload") if isinstance(platform_result.get("payload"), dict) else {}
    datasets = payload.get("datasets") if isinstance(payload.get("datasets"), dict) else {}
    status = str(platform_result.get("status") or payload.get("status") or "unknown")
    summary = str(platform_result.get("summary") or payload.get("summary") or "")
    captured_at = payload.get("read_at")
    title = _PLATFORM_TITLES[normalized_platform]

    cards = build_platform_snapshot_feishu_card(
        platform=normalized_platform,
        watch_group_key=watch_group_key,
        status=status,
        summary=summary,
        captured_at=captured_at if isinstance(captured_at, (str, datetime)) else None,
        datasets=datasets,
        store_name=group.name,
    )
    fallback_text = build_platform_snapshot_feishu_text(
        platform=normalized_platform,
        watch_group_key=watch_group_key,
        status=status,
        summary=summary,
        captured_at=captured_at if isinstance(captured_at, (str, datetime)) else None,
        datasets=datasets,
        include_title=False,
        store_name=group.name,
    )

    try:
        # 多卡连续推送（同竞品盯盘机制）：逐卡发送互不中断，任一成功即算
        # 推送成功；全部失败才用第一张卡降级重发纯文本。
        card_results = [
            await push_interactive_card_to_feishu_webhook(
                title=title,
                card=card,
                webhook_url=webhook_url,
                fallback_text=None,
            )
            for card in cards
        ]
        if any(item.status == "success" for item in card_results):
            result = card_results[0]
            payload_out = result.model_dump(mode="json")
            payload_out["status"] = "success"
            succeeded = sum(1 for item in card_results if item.status == "success")
            payload_out["summary"] = (
                f"已发送 {succeeded}/{len(card_results)} 张{normalized_platform}盯盘卡片。"
            )
        else:
            result = await push_interactive_card_to_feishu_webhook(
                title=title,
                card=cards[0],
                webhook_url=webhook_url,
                fallback_text=fallback_text,
            )
            payload_out = result.model_dump(mode="json")
    except Exception as exc:
        logger.exception("%s EBK Feishu push failed", normalized_platform)
        return sanitize_operator_payload(
            {
                "status": "failed",
                "summary": f"飞书推送失败：{type(exc).__name__}: {str(exc)[:240]}",
                "watch_group_key": watch_group_key,
                "delivery": "webhook",
            }
        )

    payload_out["watch_group_key"] = watch_group_key
    payload_out["delivery"] = "webhook"
    payload_out["platform"] = normalized_platform
    payload_out["card_count"] = len(card_results)
    payload_out["card_results"] = [
        {
            "status": item.status,
            "feishu_code": item.data.get("feishu_code"),
            "feishu_msg": item.data.get("feishu_msg"),
            "msg_type": item.data.get("msg_type"),
        }
        for item in card_results
    ]
    return sanitize_operator_payload(payload_out)


def build_platform_snapshot_feishu_card(
    *,
    platform: str,
    watch_group_key: str,
    status: str,
    summary: str,
    captured_at: str | datetime | None,
    datasets: dict[str, Any],
    store_name: str | None = None,
) -> list[dict[str, Any]]:
    """平台推送卡（多张）：每个数据集独立小节 + 独立表格，全部数据入表。

    与携程盯盘卡同一套分页渲染：飞书单卡 ≤5 表格、每表 ≤10 行不翻页；
    放不下时拆多张卡连续推送（标题带 1/N 序号），绝不截断。
    """
    title = _PLATFORM_TITLES.get(platform, f"{platform} EBK 读取结果")
    header_lines = []
    if store_name:
        header_lines.append(f"**🏨 店铺**：{_escape_lark_md(store_name)}")
    status_icon = _status_icon(status)
    header_lines.extend(
        [
            f"**🧭 来源监控组**：{_escape_lark_md(watch_group_key)}",
            f"**📌 结果**：{_escape_lark_md(summary)}",
            f"**🕒 读取时间**：{_format_read_time(captured_at)}",
            f"**{status_icon} 状态**：{_status_label(status)}",
        ]
    )
    header_text = "\n".join(header_lines)
    pages = render_metric_group_pages(_platform_card_groups(platform, datasets))
    if not pages:
        return [
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": _card_template(status),
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": [_card_text(header_text)],
            }
        ]
    cards: list[dict[str, Any]] = []
    total = len(pages)
    for index, page in enumerate(pages):
        card_title = title
        if total > 1:
            card_title += f"（{index + 1}/{total}）"
        cards.append(
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": _card_template(status),
                    "title": {"tag": "plain_text", "content": card_title},
                },
                "elements": [_card_text(header_text), *page],
            }
        )
    return cards


def _platform_card_groups(
    platform: str,
    datasets: dict[str, Any],
) -> list[dict[str, Any]]:
    """每个数据集独立成组（不跨板块合并）；标题带图标与读取状态。"""

    groups: list[dict[str, Any]] = []
    for dataset_key, raw in list(datasets.items())[:10]:
        if not isinstance(raw, dict):
            continue
        dataset_status = str(raw.get("status") or "unknown")
        label = _platform_dataset_label(dataset_key, raw)
        icon = _DATASET_ICONS.get(dataset_key, _DATASET_ICON_FALLBACK)
        title = (
            f"{icon} {_escape_lark_md(label)}："
            f"{_status_icon(dataset_status)} {_status_label(dataset_status)}"
        )
        groups.append({"title": title, "rows": _platform_member_rows(platform, dataset_key, raw)})
    return groups


def _platform_member_rows(
    platform: str,
    dataset_key: str,
    raw: dict[str, Any],
) -> list[dict[str, Any]]:
    """数据集 → 表格行（数据行 + 读取状态行，全部入表）。"""

    rows = _platform_dataset_rows(
        platform=platform, dataset_key=dataset_key, data=raw.get("data")
    )
    dataset_status = str(raw.get("status") or "unknown")
    if dataset_status != "success":
        parts = [_status_label(dataset_status)]
        if dataset_status == "human_required":
            parts.append("下一步：人工完成登录或安全验证后重试")
        rows.append({"field": "读取状态", "parts": parts})
    elif not rows:
        rows.append({"field": "读取状态", "parts": ["已读取，但未识别出可展示指标"]})
    return rows


def _platform_dataset_label(dataset_key: str, raw: dict[str, Any]) -> str:
    label = _DATASET_LABELS.get(dataset_key)
    if label:
        return label
    return str(raw.get("label") or dataset_key)


def build_platform_snapshot_feishu_text(
    *,
    platform: str,
    watch_group_key: str,
    status: str,
    summary: str,
    captured_at: str | datetime | None,
    datasets: dict[str, Any],
    include_title: bool = True,
    store_name: str | None = None,
) -> str:
    title = _PLATFORM_TITLES.get(platform, f"{platform} EBK 读取结果")
    lines: list[str] = []
    if include_title:
        lines.append(title)
    if store_name:
        lines.append(f"🏨 店铺：{store_name}")
    lines.extend(
        [
            f"🧭 来源监控组：{watch_group_key}",
            f"🕒 读取时间：{_format_read_time(captured_at)}",
            f"{_status_icon(status)} 状态：{_status_label(status)}",
            f"📌 结果：{summary}",
        ]
    )
    for dataset_key, raw in list(datasets.items())[:10]:
        if not isinstance(raw, dict):
            continue
        dataset_status = str(raw.get("status") or "unknown")
        label = _DATASET_LABELS.get(dataset_key, str(raw.get("label") or dataset_key))
        icon = _DATASET_ICONS.get(dataset_key, _DATASET_ICON_FALLBACK)
        lines.append(f"{icon} {label}：{_status_icon(dataset_status)} {_status_label(dataset_status)}")
        for metric in _dataset_metric_lines(platform=platform, dataset_key=dataset_key, data=raw.get("data"))[:20]:
            lines.append(f"  {metric}")
    return "\n".join(lines)


def build_platform_failure_alert_card(
    *,
    platform: str,
    store_name: str | None,
    watch_group_name: str,
    status: str,
    summary: str,
) -> dict[str, Any]:
    """失败态异常提醒卡（P0：登录失效/读取失败按冷却窗口每小时最多一张）。"""

    platform_label = _PLATFORM_TITLES.get(platform, f"{platform} EBK").replace(
        " EBK 读取结果", " EBK"
    )
    lines = [
        f"**店铺**：{_escape_lark_md(store_name or '未命名门店')}",
        f"**监控组**：{_escape_lark_md(watch_group_name)}",
        f"**状态**：{_status_label(status)}",
        f"**结果**：{_escape_lark_md(summary)}",
        "**下一步**：请到「账号与浏览器」确认登录态，处理后重试定时读取；冷却窗口内不重复提醒。",
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": f"{platform_label} 定时读取异常提醒"},
        },
        "elements": [_card_text("\n".join(lines))],
    }


def build_platform_failure_alert_text(
    *,
    platform: str,
    store_name: str | None,
    watch_group_name: str,
    status: str,
    summary: str,
) -> str:
    platform_label = _PLATFORM_TITLES.get(platform, f"{platform} EBK").replace(
        " EBK 读取结果", " EBK"
    )
    return "\n".join(
        [
            f"{platform_label} 定时读取异常提醒",
            f"店铺：{store_name or '未命名门店'}",
            f"监控组：{watch_group_name}",
            f"状态：{_status_label(status)}",
            f"结果：{summary}",
        ]
    )


def _dataset_metric_lines(*, platform: str, dataset_key: str, data: Any) -> list[str]:
    """纯文本兜底的行来源：由结构化行派生，避免两套口径。"""

    lines: list[str] = []
    for row in _platform_dataset_rows(platform=platform, dataset_key=dataset_key, data=data):
        label = (
            f"{row.get('dimension')}·{row['field']}" if row.get("dimension") else row["field"]
        )
        lines.append(f"{label}：{' / '.join(row['parts'])}")
    return lines


def _platform_dataset_rows(*, platform: str, dataset_key: str, data: Any) -> list[dict[str, Any]]:
    """数据集 → 结构化表格行；子板块（今日/昨日、在店/预定/流量…）进维度列。"""

    if not isinstance(data, dict):
        return []
    rows: list[dict[str, Any]] = []

    if platform == "meituan":
        if dataset_key == "workbench_home":
            rows.extend(_metric_dict_rows("今日", data.get("realtime_data")))
            rows.extend(_metric_dict_rows("昨日", data.get("yesterday_data")))
            hos = data.get("hos_score") if isinstance(data.get("hos_score"), dict) else {}
            hos_value = hos.get("value") or hos.get("score")
            if hos_value:
                rows.append({"field": "HOS", "parts": [str(hos_value)]})
            # 避免与 今日/明日 已展示的 浏览人数/销售额 重复。
            if not data.get("realtime_data") and not data.get("yesterday_data"):
                rows.extend(_metric_dict_rows("", data.get("business_summary")))
            rows.extend(_metric_dict_rows("待办", data.get("pending_cards")))
        elif dataset_key.startswith("data_center"):
            metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
            if not metrics:
                metrics = (
                    data.get("basic_operation_metrics")
                    if isinstance(data.get("basic_operation_metrics"), dict)
                    else {}
                )
            rows.extend(_metric_dict_rows("", metrics))
        elif dataset_key == "promotion_coupon":
            activities = data.get("coupon_activities")
            if isinstance(activities, list):
                for item in activities:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("activity_name") or item.get("name") or "活动")
                    status = str(item.get("status") or "未知")
                    rows.append({"field": name, "parts": [status]})
                    rows.extend(_metric_dict_rows(name, item.get("activity_metrics")))
        elif dataset_key == "comment_manage_home":
            for field, key in (
                ("点评分", "review_score"),
                ("点评总数", "review_total"),
                ("待回复", "pending_reply_count"),
            ):
                if data.get(key) is not None:
                    rows.append({"field": field, "parts": [str(data.get(key))]})

    if platform == "fliggy":
        if dataset_key == "home_v1":
            rows.extend(_metric_dict_rows("今日", data.get("today_data")))
            rows.extend(_metric_dict_rows("昨日", data.get("yesterday_data")))
        elif dataset_key == "diagnose_report":
            rows.extend(_metric_dict_rows("交易", data.get("trade_overview")))
            if data.get("guest_review_score") is not None:
                rows.append(
                    {
                        "field": "客人点评得分",
                        "parts": [str(data.get("guest_review_score"))],
                        "dimension": "点评",
                    }
                )
            rows.extend(_metric_dict_rows("点评", data.get("review_extras")))
            if data.get("mci_index") is not None:
                rows.append(
                    {
                        "field": "MCI酒店竞争力指数",
                        "parts": [str(data.get("mci_index"))],
                        "dimension": "竞争",
                    }
                )
        elif dataset_key == "operating_data":
            rows.extend(_metric_dict_rows("在店", data.get("in_house_data")))
            rows.extend(_metric_dict_rows("预定", data.get("booking_data")))
            rows.extend(_metric_dict_rows("流量", data.get("flow_data")))
        elif dataset_key == "promotion_agent":
            activities = data.get("joined_activities")
            if isinstance(activities, list):
                if not activities:
                    rows.append({"field": "已参加活动", "parts": ["暂无"]})
                for item in activities:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or item.get("activity_type") or "活动")
                    rows.append(
                        {
                            "field": name,
                            "parts": [
                                f"房型产品 {item.get('room_product') or '-'}",
                                f"有效期 {item.get('valid_period') or '-'}",
                                f"力度 {item.get('strength') or '-'}",
                                str(item.get("status") or "未知"),
                            ],
                        }
                    )

    return rows


def _metric_dict_rows(
    dimension: str,
    value: Any,
) -> list[dict[str, Any]]:
    """指标字典 → 行（全量，不截断）；周同比并入值文本；dimension 非空时作为「板块」列值。"""

    if not isinstance(value, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, raw in value.items():
        field = str(key)
        if isinstance(raw, dict):
            display = raw.get("value") if raw.get("value") is not None else raw.get("display_value")
            if display is None:
                continue
            week_over_week = raw.get("week_over_week")
            if week_over_week is not None and str(week_over_week).strip():
                display = f"{display}（较上周 {week_over_week}）"
        elif raw is not None and not isinstance(raw, list):
            display = raw
        else:
            continue
        # 值内换行 / 竖线拆成并列多列（与旧行解析一致），长文本再按列宽切块。
        parts = split_value_parts(display)
        if not parts:
            continue
        row = {"field": field, "parts": parts}
        if dimension:
            row["dimension"] = dimension
        rows.append(row)
    return rows


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
        return text[:16]


def _escape_lark_md(value: Any) -> str:
    text = html.escape(str(value or ""), quote=False)
    return re.sub(r"([*_`~\[\]])", r"\\\1", text)


def _status_label(status: str) -> str:
    mapping = {
        "success": "成功",
        "partial": "部分成功",
        "human_required": "需人工处理",
        "needs_review": "需复核",
        "error": "失败",
        "failed": "失败",
        "skipped": "已跳过",
        "config_required": "需配置",
    }
    return mapping.get(str(status or "").strip().lower(), str(status or "未知"))


def _status_icon(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "success":
        return "✅"
    if normalized in {"partial", "human_required", "needs_review"}:
        return "⚠️"
    if normalized in {"error", "failed"}:
        return "❌"
    if normalized == "skipped":
        return "⏭️"
    return "ℹ️"


def _card_template(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "success":
        return "green"
    if normalized in {"human_required", "needs_review", "partial"}:
        return "orange"
    if normalized in {"error", "failed"}:
        return "red"
    return "blue"
