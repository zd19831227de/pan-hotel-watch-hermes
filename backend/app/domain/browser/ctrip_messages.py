from __future__ import annotations

from typing import Any


def _build_feishu_message(
    *,
    status: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    return "\n".join(
        [
            f"Status: {status}",
            f"Page: {data.get('current_url') or '-'}",
            f"Page state: {data.get('page_state') or evidence.get('page_state') or '-'}",
            f"Captured at: {data.get('captured_at') or '-'}",
            "Read-only: yes",
            _top_metric_text(data),
        ]
    )


def _build_overview_feishu_card(
    *,
    status: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return _card_shell(
        title="Ctrip EBK 真实读取",
        status=status,
        lines=[
            f"页面状态: {data.get('page_state') or evidence.get('page_state') or '-'}",
            f"页面标题: {data.get('page_title') or '-'}",
            f"捕获时间: {data.get('captured_at') or '-'}",
            _top_metric_text(data),
        ],
    )


def _build_dataset_feishu_message(
    *,
    status: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    return "\n".join(
        [
            f"Status: {status}",
            f"Dataset: {data.get('dataset_label') or data.get('dataset') or '-'}",
            f"Page: {data.get('current_url') or data.get('url') or '-'}",
            f"Updated at: {data.get('updated_at') or '-'}",
            _top_metric_text(data),
            _warnings_text(data),
        ]
    )


def _build_dataset_feishu_card(
    *,
    status: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return _card_shell(
        title="Ctrip EBK 数据中心",
        status=status,
        lines=[
            f"数据集: {data.get('dataset_label') or data.get('dataset') or '-'}",
            f"页面状态: {data.get('page_state') or evidence.get('page_state') or '-'}",
            f"更新时间: {data.get('updated_at') or '-'}",
            _top_metric_text(data),
            _warnings_text(data),
        ],
    )


def _build_monitor_feishu_message(
    *,
    status: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    statuses = evidence.get("dataset_statuses") if isinstance(evidence, dict) else {}
    lines = [
        f"Status: {status}",
        f"Captured at: {data.get('captured_at') or '-'}",
        "Datasets:",
    ]
    if isinstance(statuses, dict) and statuses:
        lines.extend(f"- {dataset_id}: {dataset_status}" for dataset_id, dataset_status in statuses.items())
    else:
        lines.append("- no dataset statuses")
    return "\n".join(lines)


def _build_monitor_feishu_card(
    *,
    status: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    statuses = evidence.get("dataset_statuses") if isinstance(evidence, dict) else {}
    lines = [f"捕获时间: {data.get('captured_at') or '-'}"]
    if isinstance(statuses, dict) and statuses:
        lines.extend(f"{dataset_id}: {dataset_status}" for dataset_id, dataset_status in statuses.items())
    return _card_shell(title="Ctrip EBK 数据中心盯盘", status=status, lines=lines)


def _build_comment_monitor_feishu_message(
    *,
    status: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    monitor = data.get("comment_monitor") if isinstance(data.get("comment_monitor"), dict) else {}
    review_overview = monitor.get("review_overview") if isinstance(monitor, dict) else {}
    reply_overview = monitor.get("reply_overview") if isinstance(monitor, dict) else {}
    return "\n".join(
        [
            f"Status: {status}",
            f"Page: {data.get('current_url') or '-'}",
            f"Review score: {_metric_value(review_overview, 'overall_score')}",
            f"Review count: {_metric_value(review_overview, 'total_comment_count')}",
            f"Yesterday positives: {_metric_value(review_overview, 'positive_reviews_yesterday')}",
            f"Yesterday negatives: {_metric_value(review_overview, 'negative_reviews_yesterday')}",
            f"Reply rate: {_metric_value(reply_overview, 'reply_rate')}",
        ]
    )


def _build_comment_monitor_feishu_card(
    *,
    status: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    monitor = data.get("comment_monitor") if isinstance(data.get("comment_monitor"), dict) else {}
    review_overview = monitor.get("review_overview") if isinstance(monitor, dict) else {}
    reply_overview = monitor.get("reply_overview") if isinstance(monitor, dict) else {}
    return _card_shell(
        title="Ctrip EBK 点评分析",
        status=status,
        lines=[
            f"点评分: {_metric_value(review_overview, 'overall_score')}",
            f"点评数: {_metric_value(review_overview, 'total_comment_count')}",
            f"昨日好评: {_metric_value(review_overview, 'positive_reviews_yesterday')}",
            f"昨日差评: {_metric_value(review_overview, 'negative_reviews_yesterday')}",
            f"回复率: {_metric_value(reply_overview, 'reply_rate')}",
            f"捕获时间: {data.get('captured_at') or '-'}",
        ],
    )


def _top_metric_text(data: dict[str, Any]) -> str:
    metrics = data.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return "Metrics: no trusted visible metrics parsed"
    lines = []
    for metric in list(metrics.values())[:8]:
        if not isinstance(metric, dict):
            continue
        label = metric.get("label") or metric.get("key")
        value = metric.get("display_value") or metric.get("value")
        if label and value is not None:
            lines.append(f"- {label}: {value}")
    return "Metrics:\n" + ("\n".join(lines) if lines else "- no trusted visible metrics parsed")


def _warnings_text(data: dict[str, Any]) -> str:
    warnings = data.get("warnings")
    if not isinstance(warnings, list) or not warnings:
        return "Warnings: none"
    return "Warnings:\n" + "\n".join(f"- {item}" for item in warnings[:6])


def _display_rating(value: Any) -> str:
    if isinstance(value, dict):
        score = value.get("score")
        return str(score) if score is not None else "-"
    return "-"


def _metric_value(metrics: Any, key: str) -> str:
    if not isinstance(metrics, dict):
        return "-"
    metric = metrics.get(key)
    if not isinstance(metric, dict):
        return "-"
    value = metric.get("display_value") or metric.get("value")
    return str(value) if value not in {None, ""} else "-"


def _card_shell(*, title: str, status: str, lines: list[str]) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _card_template(status),
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(f"- {_escape_lark_md(line)}" for line in lines if line),
                },
            }
        ],
    }


def _card_template(status: str) -> str:
    if status == "success":
        return "green"
    if status == "human_required":
        return "orange"
    if status in {"needs_review", "page_structure_changed", "partial"}:
        return "yellow"
    return "red"


def _escape_lark_md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_")
