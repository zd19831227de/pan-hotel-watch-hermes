"""金字塔调价飞书卡片：出价列表卡、审批卡、执行结果/阻断卡。"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.pricing_safety import (
    account_policy_allows_write,
    normalized_account_policy,
)
from app.domain.notifications.feishu_card_tables import render_metric_group_pages

# 列表卡/降级文本展示上限：读取多少展示多少，超上限截断并注明（2026-09-08）。
_LIST_CARD_MAX_PROMOTIONS = 20
_FALLBACK_MAX_PROMOTIONS = 10

# 金字塔出价表格列契约（2026-09-10 表格化）：指令卡、携程独立推送、全局推送
# 广告卡共用，避免三处各写一套列头/格式。
PYRAMID_BID_FIELD_HEADER = "推广计划"
PYRAMID_BID_PART_HEADERS = ("点击出价", "今日预算", "今日花费", "状态")


def pyramid_promotion_table_row(item: dict[str, Any]) -> dict[str, Any]:
    """单条推广计划 -> 共享表格渲染器行（field + 4 个数据列）。"""

    return {
        "field": str(item.get("campaign_name") or "-"),
        "parts": [
            _money_part(item.get("click_bid_cny")),
            _money_part(item.get("daily_budget_cny")),
            _money_part(item.get("today_spend_cny")),
            str(item.get("campaign_status") or "-"),
        ],
    }


def _money_part(value: Any) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"¥{parsed:g}"


def build_pyramid_bid_list_card(
    read_result: dict[str, Any],
    *,
    read_status: str = "success",
) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    promotions = read_result.get("promotions") if isinstance(read_result.get("promotions"), list) else []
    elements: list[dict[str, Any]] = []
    if read_status != "success" or not promotions:
        elements.append(
            _text(
                str(read_result.get("summary") or "未读取到金字塔推广计划，请稍后重试或人工登录 EBK 查看。")
            )
        )
    else:
        # 2026-09-10 表格化：与其他推送表格同构（共享渲染器，≤10 行/表不翻页）。
        # 2026-09-08 线上问题：读取多少展示多少；超上限（20）才截断并注明。
        pages = render_metric_group_pages(
            [
                {
                    "title": "金字塔出价列表",
                    "field_header": PYRAMID_BID_FIELD_HEADER,
                    "part_headers": list(PYRAMID_BID_PART_HEADERS),
                    "rows": [
                        pyramid_promotion_table_row(item)
                        for item in promotions[:_LIST_CARD_MAX_PROMOTIONS]
                        if isinstance(item, dict)
                    ],
                }
            ]
        )
        elements.extend(pages[0] if pages else [])
        hidden = len(promotions) - _LIST_CARD_MAX_PROMOTIONS
        if hidden > 0:
            elements.append(_text(f"另有 {hidden} 个未展示"))
        elements.append(_text("调整示例：金字塔调价 14--16 出价调到6"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": f"Pan Hotel Watch 金字塔出价 · {now:%H:%M}",
            },
        },
        "elements": elements,
    }


def build_pyramid_bid_list_fallback_text(read_result: dict[str, Any]) -> str:
    promotions = read_result.get("promotions") if isinstance(read_result.get("promotions"), list) else []
    lines = ["Pan Hotel Watch 金字塔出价"]
    for item in promotions[:_FALLBACK_MAX_PROMOTIONS]:
        if isinstance(item, dict):
            lines.append(
                f"{item.get('campaign_name') or '-'}：出价 ¥{_display_bid(item.get('click_bid_cny'))}"
            )
    return "\n".join(lines)


def build_pyramid_bid_approval_card(preview: dict[str, Any]) -> dict[str, Any]:
    actions = _preview_actions(preview)
    policy = normalized_account_policy(
        {
            "connection_mode": preview.get("connection_mode"),
            "can_auto_write_price": preview.get("can_auto_write_price"),
            "write_policy_message": preview.get("write_policy_message"),
        }
    )
    can_execute = (
        account_policy_allows_write(policy)
        and str(preview.get("status") or "") == "preview_ready"
        and bool(actions)
    )
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    elements: list[dict[str, Any]] = []
    if can_execute:
        elements.append(
            _text("**金字塔调价审批**：点击「确认调整出价」后进入预检、页面核验和出价写入。")
        )
    if actions:
        for action in actions[:3]:
            elements.append(_text(_approval_action_block(action)))
    else:
        elements.append(_text("暂无可审批的金字塔调价动作。"))
    if not account_policy_allows_write(policy):
        elements.append(
            _text(
                _escape(
                    str(
                        policy.get("write_policy_message")
                        or "当前账号不允许自动调整出价，仅作参考。"
                    )
                )
            )
        )
    if can_execute:
        preview_id = _safe_int(preview.get("preview_id") or preview.get("id")) or 0
        elements.append(
            {
                "tag": "action",
                "actions": [
                    _pyramid_button("确认调整出价", "primary", preview_id, "approve"),
                    _pyramid_button("暂不调整", "danger", preview_id, "reject"),
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange" if can_execute else "blue",
            "title": {
                "tag": "plain_text",
                "content": f"Pan Hotel Watch 金字塔调价审批 · {now:%H:%M}",
            },
        },
        "elements": elements,
    }


def build_pyramid_bid_approval_fallback_text(preview: dict[str, Any]) -> str:
    lines = ["Pan Hotel Watch 金字塔调价审批"]
    for action in _preview_actions(preview)[:3]:
        lines.append(
            f"{action.get('campaign_name') or '-'}：出价 "
            f"¥{_display_bid(action.get('current_bid_cny'))} -> ¥{_display_bid(action.get('proposed_bid_cny'))}"
        )
    return "\n".join(lines)


def build_pyramid_bid_confirmation_card(classification: dict[str, Any]) -> dict[str, Any]:
    summary = str(classification.get("summary") or "金字塔调价信息不完整，请补全后重试。")
    examples = classification.get("examples") or [
        "金字塔调价 14--16 出价调到6",
        "把金字塔22--24的出价调到5.5元",
    ]
    body = "\n".join(
        [
            f"**{_escape(summary)}**",
            "示例：",
            *[f"- {_escape(item)}" for item in examples[:3]],
        ]
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 金字塔调价指令确认"},
        },
        "elements": [_text(body)],
    }


def build_pyramid_execution_result_card(execution_result: dict[str, Any]) -> dict[str, Any]:
    status = str(execution_result.get("status") or "unknown")
    data = execution_result.get("data") if isinstance(execution_result.get("data"), dict) else {}
    campaign = data.get("campaign_read") if isinstance(data.get("campaign_read"), dict) else {}
    fill = data.get("bid_fill_result") if isinstance(data.get("bid_fill_result"), dict) else {}
    readback = data.get("readback_result") if isinstance(data.get("readback_result"), dict) else {}
    lines = [
        f"**状态**：{_escape(_status_label(status))}",
        f"**说明**：{_escape(execution_result.get('summary') or '-')}",
        f"**推广计划**：{_escape(campaign.get('campaign_name') or '-')}",
        f"**填入出价**：{_escape(_display_bid(fill.get('filled_value')))}；"
        f"输入框读回：{_escape(_display_bid(fill.get('verified_value')))}",
        f"**提交后页面出价**：{_escape(_display_bid(readback.get('readback_bid_cny')))}",
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if status in {"executed", "success"} else "orange",
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 金字塔调价执行结果"},
        },
        "elements": [
            _text("\n".join(lines)),
            {"tag": "hr"},
            _text("请以携程 EBK 金字塔页面实际出价为最终确认依据。"),
        ],
    }


def build_pyramid_execution_result_fallback_text(execution_result: dict[str, Any]) -> str:
    status = str(execution_result.get("status") or "unknown")
    return "\n".join(
        [
            "Pan Hotel Watch 金字塔调价执行结果",
            f"状态：{_status_label(status)}",
            f"说明：{execution_result.get('summary') or '-'}",
        ]
    )


def build_pyramid_execution_blocked_card(
    *,
    blocked_step: str,
    blocked_status: str,
    blocked_summary: str,
    next_action: str = "",
    audit_record_id: int | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lines = [
        f"**金字塔调价流程阻断**：{_escape(blocked_step)}",
        f"**状态**：{_escape(_status_label(blocked_status))}",
        f"**原因**：{_escape(blocked_summary)}",
    ]
    if next_action:
        lines.append(f"**建议操作**：{_escape(next_action)}")
    if audit_record_id is not None:
        lines.append(f"**审计记录**：{audit_record_id}")
    safe_evidence = _safe_evidence(evidence or {})
    elements = [_text("\n".join(lines))]
    if safe_evidence:
        elements.append({"tag": "hr"})
        elements.append(
            _text(
                "\n".join(
                    f"- {_escape(key)}：{_escape(value)}"
                    for key, value in list(safe_evidence.items())[:6]
                )
            )
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 金字塔调价流程阻断"},
        },
        "elements": elements,
    }


def build_pyramid_execution_blocked_fallback_text(card: dict[str, Any]) -> str:
    header = card.get("header") if isinstance(card.get("header"), dict) else {}
    title = header.get("title") if isinstance(header.get("title"), dict) else {}
    lines = [str(title.get("content") or "Pan Hotel Watch 金字塔调价流程阻断")]
    for element in card.get("elements") or []:
        if not isinstance(element, dict):
            continue
        text = element.get("text") if isinstance(element.get("text"), dict) else {}
        content = text.get("content")
        if content:
            lines.append(str(content).replace("**", ""))
            break
    return "\n".join(lines)


def _approval_action_block(action: dict[str, Any]) -> str:
    lines = [
        "**金字塔调价**",
        f"推广计划：{_escape(action.get('campaign_name') or '-')}",
        f"计划状态：{_escape(action.get('campaign_status') or '-')}",
        f"**点击出价**：¥{_display_bid(action.get('current_bid_cny'))} -> **¥{_display_bid(action.get('proposed_bid_cny'))}**",
    ]
    budget = action.get("daily_budget_cny")
    if budget is not None:
        lines.append(f"今日预算：¥{_display_bid(budget)}")
    risk_flags = action.get("risk_flags") if isinstance(action.get("risk_flags"), list) else []
    if risk_flags:
        lines.append(f"风险提示：{_escape('、'.join(str(item) for item in risk_flags[:4]))}")
    lines.append("说明：确认后会先打开编辑页核验起价下限与输入框，再写入并回读校验。")
    lines.append(f"原因：{_escape(action.get('trigger_reason') or '-')}")
    return "\n".join(lines)


def _pyramid_button(label: str, button_type: str, preview_id: int, decision: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "value": {
            "workflow": "pyramid_bid_approval",
            "pyramid_bid_preview_record_id": preview_id,
            "preview_id": preview_id,
            "decision": decision,
            "execute_after_feishu_approval": decision == "approve",
        },
    }


def _preview_actions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    candidate = preview.get("preview_candidate") if isinstance(preview.get("preview_candidate"), dict) else {}
    actions = candidate.get("actions")
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _status_label(status: Any) -> str:
    return {
        "executed": "已执行",
        "success": "成功",
        "failed": "失败",
        "human_required": "需要人工处理",
        "needs_review": "需要复核",
        "page_structure_changed": "页面结构变化",
        "data_required": "缺少可信数据",
        "page_write_preview_ready": "页面核验已就绪",
        "preflight_ready": "预检通过",
        "write_not_allowed": "不允许自动调整出价",
        "filled": "已填入",
        "not_approved": "未通过飞书审批",
        "not_preflight_ready": "未完成前置步骤",
        "campaign_not_found": "未找到该推广计划",
        "read_failed": "页面读取失败",
        "no_change_required": "出价已达标，无需调整",
        "mismatch": "回读不一致，需复核",
        "floor_price_violation": "低于页面起价",
        "bid_input_not_located": "未定位出价输入框",
        "submit_failed": "提交未生效",
        "pipeline_failed": "执行链异常",
    }.get(str(status), str(status))


def _safe_evidence(evidence: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in evidence.items():
        lower = str(key).lower()
        if any(
            token in lower
            for token in ("secret", "password", "cookie", "token", "webhook", "screenshot", "profile")
        ):
            continue
        cleaned[str(key)] = str(value)[:120]
    return cleaned


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


def _display_bid(value: Any) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{parsed:g}"


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None
