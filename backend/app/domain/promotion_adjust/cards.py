"""活动促销调整飞书卡片：已参加促销列表卡、审批卡、执行结果/阻断卡。"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.pricing_safety import (
    account_policy_allows_write,
    normalized_account_policy,
)

# 列表卡/降级文本展示上限：读取多少展示多少，超上限截断并注明。
_LIST_CARD_MAX_PROMOTIONS = 20
_FALLBACK_MAX_PROMOTIONS = 10
# 飞书表格组件单页行数上限（超过会自动分页，控制在 10 内最稳）。
_LIST_TABLE_PAGE_SIZE = 10

# 审批卡风险提示：已知内部标记 -> 口语化说明；未列出的内部标记不展示。
_RISK_FLAG_HINTS = {
    "requires_preflight_revalidation": "执行前会重新核验页面与当前立减，页面不一致会自动停止",
    "current_discount_readback_missing": "刚才没读到当前立减，执行前会以页面实际立减为准",
}

# 阻断卡证据白名单：只展示运营能看懂的字段，其余内部审计标记一律隐藏。
_EVIDENCE_FIELD_LABELS = {
    "page_state": "页面状态",
    "current_page_discount_percent": "页面当前立减",
    "approved_current_discount_percent": "审批时立减",
    "promotion_count": "读取到的促销数",
}
_PAGE_STATE_LABELS = {
    "login_required": "需要重新登录",
    "verification_required": "需要人工验证",
    "authenticated": "已登录",
    "unknown": "未知",
}


def build_promotion_adjust_list_card(
    read_result: dict[str, Any],
    *,
    read_status: str = "success",
) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    promotions = read_result.get("promotions") if isinstance(read_result.get("promotions"), list) else []
    if read_status != "success" or not promotions:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {
                    "tag": "plain_text",
                    "content": f"📋 活动促销列表 · {now:%H:%M}",
                },
            },
            "elements": [
                _text(
                    _escape(
                        str(
                            read_result.get("summary")
                            or "未读取到已参加促销，请稍后重试或人工登录 EBK 促销中心查看。"
                        )
                    )
                )
            ],
        }
    elements: list[dict[str, Any]] = [_promotion_table(promotions[:_LIST_CARD_MAX_PROMOTIONS])]
    notes: list[str] = []
    if any(item.get("multi_rule") for item in promotions if isinstance(item, dict)):
        notes.append("标注「多规则」的促销包含多条规则（不同时段/条件优惠不同），暂不支持自动调整，请在 EBK 手动处理。")
    hidden = len(promotions) - _LIST_CARD_MAX_PROMOTIONS
    if hidden > 0:
        notes.append(f"另有 {hidden} 个未展示")
    notes.append("调整示例：促销调整 天天特价 立减调到5")
    elements.append(_text("\n".join(f"- {note}" for note in notes)))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {
                "tag": "plain_text",
                "content": f"📋 活动促销列表 · 共 {len(promotions)} 个 · {now:%H:%M}",
            },
        },
        "elements": elements,
    }


def _promotion_table(promotions: list[Any]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for item in promotions:
        if not isinstance(item, dict):
            continue
        discount = str(item.get("discount_text") or "-")
        if item.get("multi_rule"):
            discount = f"{discount}（多规则）"
        rows.append(
            {
                "name": _escape(item.get("promotion_name") or "-"),
                "discount": _escape(discount),
                "date": _escape(_short_date_text(item.get("date_text"))),
            }
        )
    return {
        "tag": "table",
        "page_size": min(max(len(rows), 1), _LIST_TABLE_PAGE_SIZE),
        "row_height": "low",
        # 不冻结首列：整表（含促销名称列）一起横向滑动，避免手机端后两列被挤没空间。
        "freeze_first_column": False,
        "header_style": {
            "text_align": "left",
            "background_style": "none",
            "text_size": "normal",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": [
            _table_column("name", "促销名称", width="190px"),
            _table_column("discount", "立减优惠", width="170px"),
            _table_column("date", "促销时间", width="210px"),
        ],
        "rows": rows,
    }


def _table_column(data_key: str, display_name: str, *, width: str) -> dict[str, Any]:
    return {
        "name": data_key,
        "display_name": display_name,
        "data_type": "lark_md",
        "width": width,
        "horizontal_align": "left",
        "vertical_align": "top",
    }


def build_promotion_adjust_list_fallback_text(read_result: dict[str, Any]) -> str:
    promotions = read_result.get("promotions") if isinstance(read_result.get("promotions"), list) else []
    lines = ["Pan Hotel Watch 活动促销"]
    for item in promotions[:_FALLBACK_MAX_PROMOTIONS]:
        if isinstance(item, dict):
            lines.append(
                f"{item.get('promotion_name') or '-'}：{item.get('discount_text') or '-'}"
            )
    return "\n".join(lines)


def build_promotion_adjust_approval_card(preview: dict[str, Any]) -> dict[str, Any]:
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
            _text("**促销调整审批**：点击「确认调整促销」后进入预检、页面核验和折扣写入。")
        )
    if actions:
        for action in actions[:3]:
            elements.append(_text(_approval_action_block(action)))
    else:
        elements.append(_text("暂无可审批的促销调整动作。"))
    if not account_policy_allows_write(policy):
        elements.append(
            _text(
                _escape(
                    str(
                        policy.get("write_policy_message")
                        or "当前账号不允许自动调整促销，仅作参考。"
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
                    _promotion_button("确认调整促销", "primary", preview_id, "approve"),
                    _promotion_button("暂不调整", "danger", preview_id, "reject"),
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange" if can_execute else "blue",
            "title": {
                "tag": "plain_text",
                "content": f"Pan Hotel Watch 促销调整审批 · {now:%H:%M}",
            },
        },
        "elements": elements,
    }


def build_promotion_adjust_approval_fallback_text(preview: dict[str, Any]) -> str:
    lines = ["Pan Hotel Watch 促销调整审批"]
    for action in _preview_actions(preview)[:3]:
        lines.append(
            f"{action.get('promotion_name') or '-'}：立减 "
            f"{_display_percent(action.get('current_discount_percent'))} -> "
            f"{_display_percent(action.get('proposed_discount_percent'))}"
        )
    return "\n".join(lines)


def build_promotion_adjust_confirmation_card(classification: dict[str, Any]) -> dict[str, Any]:
    summary = str(classification.get("summary") or "促销调整信息不完整，请补全后重试。")
    examples = classification.get("examples") or [
        "促销调整 天天特价 立减调到5",
        "把促销门店新客立减的立减调到35%",
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
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 促销调整指令确认"},
        },
        "elements": [_text(body)],
    }


def build_promotion_execution_result_card(execution_result: dict[str, Any]) -> dict[str, Any]:
    status = str(execution_result.get("status") or "unknown")
    data = execution_result.get("data") if isinstance(execution_result.get("data"), dict) else {}
    promotion = data.get("promotion_read") if isinstance(data.get("promotion_read"), dict) else {}
    fill = data.get("discount_fill_result") if isinstance(data.get("discount_fill_result"), dict) else {}
    readback = data.get("readback_result") if isinstance(data.get("readback_result"), dict) else {}
    lines = [
        f"**状态**：{_escape(_status_label(status))}",
        f"**说明**：{_escape(execution_result.get('summary') or '-')}",
        f"**促销活动**：{_escape(promotion.get('promotion_name') or '-')}",
        f"**填入立减**：{_escape(_display_percent_raw(fill.get('filled_value')))}；"
        f"输入框读回：{_escape(_display_percent_raw(fill.get('verified_value')))}",
        f"**保存后页面立减**：{_escape(_display_percent(readback.get('readback_discount_percent')))}",
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if status in {"executed", "success"} else "orange",
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 促销调整执行结果"},
        },
        "elements": [
            _text("\n".join(lines)),
            {"tag": "hr"},
            _text("请以携程 EBK 促销中心实际立减为最终确认依据。"),
        ],
    }


def build_promotion_execution_result_fallback_text(execution_result: dict[str, Any]) -> str:
    status = str(execution_result.get("status") or "unknown")
    return "\n".join(
        [
            "Pan Hotel Watch 促销调整执行结果",
            f"状态：{_status_label(status)}",
            f"说明：{execution_result.get('summary') or '-'}",
        ]
    )


def build_promotion_execution_blocked_card(
    *,
    blocked_step: str,
    blocked_status: str,
    blocked_summary: str,
    next_action: str = "",
    audit_record_id: int | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lines = [
        f"**促销调整流程阻断**：{_escape(blocked_step)}",
        f"**状态**：{_escape(_status_label(blocked_status))}",
        f"**原因**：{_escape(blocked_summary)}",
    ]
    if next_action:
        lines.append(f"**建议操作**：{_escape(next_action)}")
    if audit_record_id is not None:
        lines.append(f"**审计记录**：{audit_record_id}")
    friendly_evidence = _friendly_evidence(evidence or {})
    elements = [_text("\n".join(lines))]
    if friendly_evidence:
        elements.append({"tag": "hr"})
        elements.append(_text("\n".join(f"- {line}" for line in friendly_evidence)))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 促销调整流程阻断"},
        },
        "elements": elements,
    }


def build_promotion_execution_blocked_fallback_text(card: dict[str, Any]) -> str:
    header = card.get("header") if isinstance(card.get("header"), dict) else {}
    title = header.get("title") if isinstance(header.get("title"), dict) else {}
    lines = [str(title.get("content") or "Pan Hotel Watch 促销调整流程阻断")]
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
        "**促销调整**",
        f"促销活动：{_escape(action.get('promotion_name') or '-')}",
        f"促销时间：{_escape(_short_date_text(action.get('date_text')))}",
        f"**立减**：{_display_percent(action.get('current_discount_percent'))} -> "
        f"**{_display_percent(action.get('proposed_discount_percent'))}**",
    ]
    hints = _risk_flag_hints(action.get("risk_flags"))
    if hints:
        lines.append("风险提示：" + "；".join(hints))
    lines.append("说明：确认后会先打开促销编辑页核验名称与折扣输入框，再写入并回读校验。")
    lines.append(f"原因：{_escape(action.get('trigger_reason') or '-')}")
    return "\n".join(lines)


def _risk_flag_hints(risk_flags: Any) -> list[str]:
    """已知内部风险标记翻译成口语；未识别的内部标记不展示给运营。"""
    if not isinstance(risk_flags, list):
        return []
    return [
        _RISK_FLAG_HINTS[str(flag)]
        for flag in risk_flags[:6]
        if str(flag) in _RISK_FLAG_HINTS
    ]


def _promotion_button(label: str, button_type: str, preview_id: int, decision: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "value": {
            "workflow": "promotion_adjust_approval",
            "promotion_adjust_preview_record_id": preview_id,
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
        "write_not_allowed": "不允许自动调整促销",
        "not_approved": "未通过飞书审批",
        "not_preflight_ready": "未完成前置步骤",
        "promotion_not_found": "未找到该促销",
        "multi_rule_promotion": "多条促销规则，需人工调整",
        "discount_input_not_located": "未定位折扣输入框",
        "read_failed": "页面读取失败",
        "no_change_required": "立减已达标，无需调整",
        "mismatch": "回读不一致，需复核",
        "submit_failed": "提交未生效",
        "pipeline_failed": "执行链异常",
    }.get(str(status), str(status))


def _short_date_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if text else "-"


def _friendly_evidence(evidence: dict[str, Any]) -> list[str]:
    """阻断卡只展示白名单字段并翻译成运营语言；内部审计标记（如
    browser_write_invoked、ota_submit_invoked、execute_triggered）不展示。"""
    lines: list[str] = []
    for key, label in _EVIDENCE_FIELD_LABELS.items():
        if key not in evidence or evidence[key] is None:
            continue
        value = evidence[key]
        if key == "page_state":
            rendered = _PAGE_STATE_LABELS.get(str(value), str(value))
        elif "percent" in key:
            rendered = _display_percent(value)
        else:
            rendered = str(value)
        lines.append(f"{label}：{_escape(rendered)}")
    return lines


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


def _display_percent(value: Any) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{parsed:g}%"


def _display_percent_raw(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    parsed = _safe_float(text)
    return f"{parsed:g}%" if parsed is not None else text


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


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
