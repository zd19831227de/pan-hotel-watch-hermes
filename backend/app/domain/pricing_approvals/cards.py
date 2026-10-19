from __future__ import annotations

import html
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.price_change_alerts.cards import (
    price_change_alert_title,
    _breakfast_label,
    _date_label,
    _escape,
    _format_percent,
    _non_auto_write_warning,
    _signed,
    _text,
    _warning_subtitle,
)
from app.domain.pricing_safety import (
    account_policy_allows_write,
    normalized_account_policy,
    pricing_platform_from_preview_payload,
    write_not_allowed_reason,
)


def pricing_approval_card_title(title: str | None = None) -> str:
    return price_change_alert_title(title or "").replace("价格变化提醒", "调价审批")


def build_pricing_approval_card(
    preview: dict[str, Any],
    *,
    title: str | None = None,
) -> dict[str, Any]:
    actions = _preview_actions(preview)
    policy = _preview_account_policy(preview)
    can_execute = (
        account_policy_allows_write(policy)
        and str(preview.get("status") or "") == "preview_ready"
        and bool(actions)
    )
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    elements: list[dict[str, Any]] = []
    if can_execute:
        elements.append(
            _text("**非直连审批**：点击「通过」后进入预检、页面空跑和写价。")
        )
    if actions:
        for action in actions[:8]:
            elements.append(_text(_preview_action_block(action, policy)))
    else:
        elements.append(_text("暂无可审批的调价动作。"))
    if can_execute:
        preview_id = _safe_int(preview.get("preview_id") or preview.get("id")) or 0
        elements.append(
            {
                "tag": "action",
                "actions": _approval_buttons(preview_id=preview_id),
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange" if can_execute else "blue",
            "title": {
                "tag": "plain_text",
                "content": f"{pricing_approval_card_title(title)} · {now:%H:%M}",
            },
        },
        "elements": elements,
    }


def build_pricing_approval_fallback_text(preview: dict[str, Any]) -> str:
    policy = _preview_account_policy(preview)
    lines = [pricing_approval_card_title()]
    for action in _preview_actions(preview)[:5]:
        lines.append(
            f"{action.get('checkin_date') or action.get('date_label') or '-'} "
            f"{action.get('room_type') or '-'}："
            f"¥{action.get('current_price_yuan')} → ¥{action.get('proposed_price_yuan')}"
        )
    if not account_policy_allows_write(policy):
        lines.append(
            str(
                policy.get("write_policy_message")
                or write_not_allowed_reason(pricing_platform_from_preview_payload(preview))
            )
        )
    return "\n".join(lines)


def build_pricing_reference_card(
    preview: dict[str, Any],
    *,
    title: str | None = None,
) -> dict[str, Any]:
    actions = _preview_actions(preview)
    policy = _preview_account_policy(preview)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    summary = _pricing_reference_summary(preview)
    elements: list[dict[str, Any]] = [_text(_pricing_reference_summary_block(summary))]
    if actions:
        for action in actions[:8]:
            elements.append(_text(_preview_action_block(action, policy)))
        preview_id = _safe_int(preview.get("preview_id") or preview.get("id"))
        if preview_id is not None and preview_id > 0:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        _custom_price_button(preview_id),
                        _defer_pricing_button(preview_id),
                    ],
                }
            )
        else:
            elements.append(_text("参考卡缺少预览记录，请重新触发竞品盯盘或手动发送调价指令。"))
    else:
        elements.append(_text("暂无可参考的调价动作。"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": f"{pricing_reference_card_title(title)} - {now:%H:%M}",
            },
        },
        "elements": elements,
    }


def build_pricing_reference_fallback_text(preview: dict[str, Any]) -> str:
    lines = [pricing_reference_card_title()]
    for action in _preview_actions(preview)[:5]:
        lines.append(
            f"{action.get('checkin_date') or action.get('date_label') or '-'} "
            f"{action.get('room_type') or '-'}:"
            f"参考价 ¥{action.get('proposed_price_yuan')}"
        )
    lines.append("请由店长点击自定义调价输入或确认目标价。")
    return "\n".join(lines)


def pricing_reference_card_title(title: str | None = None) -> str:
    base = price_change_alert_title(title or "").replace("价格变化提醒", "调价参考")
    if base.endswith("调价参考"):
        return base
    return f"{base}调价参考"



def _pricing_reference_summary(preview: dict[str, Any]) -> dict[str, Any]:
    summary = preview.get("pricing_reference_summary")
    if isinstance(summary, dict):
        return summary
    candidate = preview.get("preview_candidate") if isinstance(preview.get("preview_candidate"), dict) else {}
    nested = candidate.get("pricing_reference_summary") if isinstance(candidate, dict) else None
    return nested if isinstance(nested, dict) else {}


def _pricing_reference_summary_block(summary: dict[str, Any]) -> str:
    text = str(summary.get("summary_cn") or "\u7cfb\u7edf\u5df2\u751f\u6210\u8c03\u4ef7\u53c2\u8003\uff0c\u8bf7\u5e97\u957f\u786e\u8ba4\u76ee\u6807\u4ef7\u3002")
    suggested = _display_yuan(summary.get("suggested_price_yuan"))
    price_range = summary.get("suggested_price_range") if isinstance(summary.get("suggested_price_range"), dict) else {}
    range_text = f"{_display_yuan(price_range.get('min'))}-{_display_yuan(price_range.get('max'))}"
    risk_flags = summary.get("risk_flags") if isinstance(summary.get("risk_flags"), list) else []
    evidence_lines = summary.get("evidence_lines") if isinstance(summary.get("evidence_lines"), list) else []
    lines = [
        "**\u8c03\u4ef7\u53c2\u8003**",
        f"**AI \u6458\u8981**\uff1a{_escape(text)}",
        f"**\u53c2\u8003\u5efa\u8bae**\uff1a\u5efa\u8bae\u76ee\u6807\u4ef7 {suggested}\uff1b\u53c2\u8003\u533a\u95f4 {range_text}",
        "**\u98ce\u9669\u63d0\u793a**\uff1a" + ("\u3001".join(_escape(item) for item in risk_flags) if risk_flags else "\u9700\u5e97\u957f\u4eba\u5de5\u786e\u8ba4"),
        "**\u91cd\u70b9\u53d8\u5316**",
    ]
    lines.extend(f"- {_escape(item)}" for item in evidence_lines[:5])
    if not evidence_lines:
        lines.append("- \u6682\u65e0\u53ef\u5c55\u793a\u7684\u91cd\u70b9\u53d8\u5316")
    return "\n".join(lines)


def _preview_account_policy(preview: dict[str, Any]) -> dict[str, Any]:
    return normalized_account_policy(
        {
            "connection_mode": preview.get("connection_mode"),
            "can_auto_write_price": preview.get("can_auto_write_price"),
            "write_policy_message": preview.get("write_policy_message"),
        }
    )


def _preview_action_block(action: dict[str, Any], policy: dict[str, Any]) -> str:
    item = {
        "date_label": action.get("date_label"),
        "checkin_date": action.get("checkin_date"),
        "room_type": action.get("room_type"),
        "breakfast_kind": action.get("breakfast_kind"),
        "connection_mode": policy.get("connection_mode"),
        "write_policy_message": policy.get("write_policy_message"),
    }
    lines = [
        _warning_subtitle(item),
        f"房型：{_escape(action.get('room_type') or '-')}",
        f"平台：{_platform_label(action.get('platform'))} | {_date_label(item)} | {_breakfast_label(action.get('breakfast_kind'))}",
    ]
    previous_price = action.get("competitor_previous_price_yuan")
    current_price = action.get("competitor_current_price_yuan")
    if previous_price is not None and current_price is not None:
        direction = "涨价" if int(current_price) > int(previous_price) else "降价"
        lines.append(
            f"竞品{direction}：**¥{previous_price} → ¥{current_price}**"
        )
        if action.get("change_amount_yuan") is not None:
            lines.append(
                f"变化：{_signed(action.get('change_amount_yuan'))} / "
                f"{_format_percent(action.get('change_percent'))}"
            )
    adjustment = _safe_int(action.get("recommended_adjustment_yuan"))
    adjust_label = "建议本店降价" if adjustment is not None and adjustment < 0 else "建议调整"
    lines.append(f"**当前卖价/底价**：{_sell_base_price_line(action, sell_key='current_price_yuan', base_key='current_base_price_yuan', before_key='before_price_yuan')}")
    if action.get("current_price_yuan") is None and action.get("before_price_yuan") is None:
        read_reason = _current_rate_read_reason(action)
        if read_reason:
            lines.append(f"现价未读取到：{_escape(read_reason)}")
    if adjustment is not None:
        lines.append(
            f"{adjust_label}：**{_signed(action.get('recommended_adjustment_yuan'))}**"
        )
    lines.append(f"**目标卖价/底价**：{_sell_base_price_line(action, sell_key='proposed_price_yuan', base_key='proposed_base_price_yuan', fallback_sell_key='target_price_yuan')}")
    lines.append(_commission_conversion_note(action))
    lines.append(f"原因：{_escape(action.get('trigger_reason') or action.get('reason') or '-')}")
    warning = _non_auto_write_warning(item)
    if warning:
        lines.append(warning)
    return "\n".join(lines)


def _approval_buttons(preview_id: int) -> list[dict[str, Any]]:
    return [
        _button("通过", "primary", preview_id, "approve"),
        _button("拒绝", "danger", preview_id, "reject"),
        _custom_price_button(preview_id),
    ]


def _button(label: str, button_type: str, preview_id: int, decision: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "value": {
            "workflow": "pricing_approval",
            "pricing_preview_record_id": preview_id,
            "preview_id": preview_id,
            "decision": decision,
            "confirm_operator_target_price": decision == "approve",
            "execute_after_feishu_approval": decision == "approve",
        },
    }


def _custom_price_button(preview_id: int) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "自定义调价"},
        "type": "default",
        "value": {
            "workflow": "pricing_approval",
            "pricing_preview_record_id": preview_id,
            "preview_id": preview_id,
            "action": "custom_price",
            "execute_after_feishu_approval": False,
        },
    }


def _defer_pricing_button(preview_id: int) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "暂不处理"},
        "type": "default",
        "value": {
            "workflow": "pricing_reference",
            "pricing_preview_record_id": preview_id,
            "preview_id": preview_id,
            "action": "defer",
            "execute_after_feishu_approval": False,
        },
    }



def build_operator_target_price_confirmation_card(preview: dict[str, Any]) -> dict[str, Any]:
    preview_id = _safe_int(preview.get("preview_id") or preview.get("id")) or 0
    action = (_preview_actions(preview) or [{}])[0]
    target_price = action.get("proposed_price_yuan") or action.get("target_price_yuan")
    body = "\n".join(
        [
            "**确认按此价格调价**",
            f"房型：{_escape(action.get('room_type') or '-')}",
            f"日期：{_escape(action.get('checkin_date') or action.get('date_label') or '-')}",
            f"**目标卖价/底价**：{_sell_base_price_line(action, sell_key='proposed_price_yuan', base_key='proposed_base_price_yuan', fallback_sell_key='target_price_yuan')}",
            _commission_conversion_note(action),
            "确认后才会进入预检和页面空跑安全校验。",
        ]
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch \u76ee\u6807\u4ef7\u786e\u8ba4"},
        },
        "elements": [
            _markdown(body),
            {
                "tag": "action",
                "actions": [
                    _button("\u786e\u8ba4\u6309\u6b64\u4ef7\u683c\u8c03\u4ef7", "primary", preview_id, "approve"),
                    _button("\u53d6\u6d88", "default", preview_id, "cancel"),
                ],
            },
        ],
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
        known_lines.append(f"- 房型：{_escape(partial['room_type'])}")
    if partial.get("date_label"):
        known_lines.append(
            f"- 日期：{'今天' if partial['date_label'] == 'today' else '明天'}"
        )
    if partial.get("target_price"):
        known_lines.append(f"- 目标价：¥{_escape(partial['target_price'])}")
    missing_lines = [
        f"- 还缺：{_escape(labels.get(field, field))}"
        for field in missing_fields
        if field in labels
    ]
    examples = classification.get("examples")
    example_lines = (
        [f"- {_escape(item)}" for item in examples[:2]]
        if isinstance(examples, list)
        else ["- 把今天高级大床房调到420"]
    )
    body = "\n".join(
        [
            "**手动调价信息不完整**",
            str(classification.get("summary") or "请补全房型、日期和目标价。"),
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
        "elements": [_markdown(body)],
    }


def build_manual_price_confirmation_fallback_text(classification: dict[str, Any]) -> str:
    lines = [
        "手动调价信息不完整，请补全后再发起审批。",
        str(classification.get("summary") or ""),
        "示例：把今天高级大床房调到420",
    ]
    return "\n".join(line for line in lines if line)


def build_custom_price_form_card(preview: dict[str, Any]) -> dict[str, Any]:
    preview_id = _safe_int(preview.get("preview_id") or preview.get("id")) or 0
    action = (_preview_actions(preview) or [{}])[0]
    suggested_price = action.get("proposed_price_yuan") or action.get("target_price_yuan")
    suggested_base = action.get("proposed_base_price_yuan")
    date_line = (
        f"{action.get('checkin_date') or action.get('date_label') or '-'} "
        f"{action.get('room_type') or '-'}"
    ).strip()
    return {
        "config": {
            "update_multi": True,
            "enable_forward": False,
            "wide_screen_mode": True,
        },
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 调价审批"},
        },
        "elements": [
            _markdown(
                "\n".join(
                    [
                        "**自定义调价**",
                        f"**范围**：{_escape(date_line)}",
                        f"**当前建议卖价/底价**：{_sell_base_price_line(action, sell_key='proposed_price_yuan', base_key='proposed_base_price_yuan', fallback_sell_key='target_price_yuan')}",
                        "请填写携程对外**售卖价（划线价）**；系统写入 EBK 页面时会自动换算为**底价**。",
                        _commission_conversion_note(action),
                        "提交后会在当前卡片内更新为新的调价审批内容；仍需点击通过后才会进入预检、页面空跑和写价。",
                    ]
                )
            ),
            {
                "tag": "form",
                "name": "pricing_custom_price_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "target_price",
                        "required": True,
                        "placeholder": {
                            "tag": "plain_text",
                            "content": "请输入新的售卖价，如 960",
                        },
                        "label": {"tag": "plain_text", "content": "目标售卖价（元）"},
                        "default_value": str(suggested_price or ""),
                    },
                    {
                        "tag": "button",
                        "name": "submit_custom_price",
                        "type": "primary",
                        "action_type": "form_submit",
                        "text": {"tag": "plain_text", "content": "确定调价"},
                        "value": {
                            "workflow": "pricing_custom_price",
                            "action": "submit",
                            "pricing_preview_record_id": preview_id,
                            "preview_id": preview_id,
                        },
                    },
                ],
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "name": "cancel_custom_price",
                        "type": "default",
                        "text": {"tag": "plain_text", "content": "取消"},
                        "value": {
                            "workflow": "pricing_custom_price",
                            "action": "cancel",
                            "pricing_preview_record_id": preview_id,
                            "preview_id": preview_id,
                        },
                    }
                ],
            },
        ],
    }


def _preview_actions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    candidate = preview.get("preview_candidate") if isinstance(preview.get("preview_candidate"), dict) else {}
    actions = candidate.get("actions") if isinstance(candidate, dict) else None
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _markdown(content: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": content}


def _preview_type(preview: dict[str, Any]) -> str:
    candidate = preview.get("preview_candidate") if isinstance(preview.get("preview_candidate"), dict) else {}
    return str(candidate.get("preview_type") or preview.get("preview_type") or "")


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


_PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}


def _platform_label(platform: Any) -> str:
    normalized = str(platform or "").strip().lower()
    return _PLATFORM_LABELS.get(normalized, "携程")


_CURRENT_RATE_READ_REASON_LABELS = {
    "human_required": "EBK 需要人工登录/验证后再读取",
    "not_found": "价格日历上未定位到该房型当日的价格块",
    "skipped": "该平台现价读取暂未接入",
    "failed": "浏览器读取现价失败",
}


def _current_rate_read_reason(action: dict[str, Any]) -> str | None:
    status = str(action.get("current_rate_readback_status") or "").strip().lower()
    reason = str(action.get("current_rate_readback_reason") or "").strip()
    if status in _CURRENT_RATE_READ_REASON_LABELS and not reason:
        return _CURRENT_RATE_READ_REASON_LABELS[status]
    if status and reason:
        return f"{_CURRENT_RATE_READ_REASON_LABELS.get(status, status)}（{reason}）"
    if reason:
        return reason
    return _CURRENT_RATE_READ_REASON_LABELS.get(status)


def _display_yuan(value: Any) -> str:
    number = _safe_int(value)
    if number is not None:
        return f"¥{number}"
    amount = _safe_amount(value)
    return f"¥{amount}" if amount is not None else "未读取到"


def _safe_amount(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    text = str(value).strip()
    if not text:
        return None
    try:
        return f"{float(text):.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return None


def _commission_rate_label(action: dict[str, Any]) -> str:
    rate = action.get("commission_rate")
    if isinstance(rate, (int, float)):
        return f"{float(rate) * 100:.0f}%"
    return "12%"


def _commission_conversion_note(action: dict[str, Any]) -> str:
    return (
        f"**说明**：飞书填写售卖价，携程 EBK 页面填入转换后底价"
        f"（佣金率 {_commission_rate_label(action)}）。"
    )


def _sell_base_price_line(
    action: dict[str, Any],
    *,
    sell_key: str,
    base_key: str,
    before_key: str | None = None,
    fallback_sell_key: str | None = None,
) -> str:
    sell = action.get(sell_key)
    if sell is None and before_key:
        sell = action.get(before_key)
    if sell is None and fallback_sell_key:
        sell = action.get(fallback_sell_key)
    return f"{_display_yuan(sell)} / {_display_yuan(action.get(base_key))}"
