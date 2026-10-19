from __future__ import annotations

import html
from typing import Any


_DECISION_LABELS = {
    "approve": "通过",
    "reject": "拒绝",
    "human_takeover": "人工接管",
}

_DECISION_TEMPLATES = {
    "approved": "blue",
    "rejected": "grey",
    "human_takeover": "orange",
    "already_decided": "grey",
    "write_not_allowed": "red",
    "operator_target_price_required": "orange",
    "not_found": "red",
}

_PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}


def build_pricing_approval_decision_card(approval_result: dict[str, Any]) -> dict[str, Any]:
    """审批卡按钮决策的群反馈卡：拒绝/人工接管/重复点击/执行启动都回卡。"""

    status = str(approval_result.get("status") or "unknown")
    decision = str(approval_result.get("decision") or "-")
    candidate = (
        approval_result.get("approval_candidate")
        if isinstance(approval_result.get("approval_candidate"), dict)
        else {}
    )
    actions = candidate.get("actions") if isinstance(candidate.get("actions"), list) else []
    first = actions[0] if actions and isinstance(actions[0], dict) else {}

    lines = [
        f"**决策**：{_escape(_DECISION_LABELS.get(decision, decision))}",
        f"**状态**：{_escape(_decision_status_label(status))}",
        f"**说明**：{_escape(approval_result.get('summary') or '-')}",
    ]
    if first:
        platform = _PLATFORM_LABELS.get(
            str(first.get("platform") or "").strip().lower(), "携程"
        )
        target = first.get("proposed_price_yuan") or first.get("target_price_yuan")
        lines.append(
            f"**房型**：{_escape(first.get('room_type') or '-')}（{_escape(platform)}）"
        )
        lines.append(f"**入住日期**：{_escape(first.get('checkin_date') or '-')}")
        lines.append(f"**目标卖价**：{_display_yuan(target)}")
    if status == "approved" and approval_result.get("evidence", {}).get(
        "execution_pipeline_queued"
    ):
        lines.append("**下一步**：执行链已启动（预检 → 空跑 → 写价），结果稍后以执行结果卡推送。")
    elif approval_result.get("next_action"):
        lines.append(f"**下一步**：{_escape(approval_result.get('next_action'))}")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _DECISION_TEMPLATES.get(status, "grey"),
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 调价审批结果"},
        },
        "elements": [_text("\n".join(lines))],
    }


def build_pricing_approval_decision_fallback_text(card: dict[str, Any]) -> str:
    lines = ["Pan Hotel Watch 调价审批结果"]
    for element in card.get("elements") or []:
        if not isinstance(element, dict):
            continue
        text = element.get("text") if isinstance(element.get("text"), dict) else {}
        content = text.get("content")
        if content:
            lines.append(str(content).replace("**", ""))
            break
    return "\n".join(lines)


def _decision_status_label(status: str) -> str:
    return {
        "approved": "已通过，执行链启动",
        "rejected": "已拒绝",
        "human_takeover": "人工接管",
        "already_decided": "该卡已处理过",
        "write_not_allowed": "账号策略不允许自动写价",
        "operator_target_price_required": "需先自定义目标价",
        "not_found": "审批预览不存在",
    }.get(status, status)


def _display_yuan(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"¥{value:g}"
    text = str(value or "").strip()
    return text if text else "-"


def build_pricing_execution_result_card(execution_result: dict[str, Any]) -> dict[str, Any]:
    status = str(execution_result.get("status") or "unknown")
    data = execution_result.get("data") if isinstance(execution_result.get("data"), dict) else {}
    action_results = data.get("action_results") if isinstance(data.get("action_results"), list) else []
    submit_result = data.get("submit_result") if isinstance(data.get("submit_result"), dict) else {}
    elements: list[dict[str, Any]] = [
        _text(
            "\n".join(
                [
                    f"**状态**：{_escape(_status_label(status))}",
                    f"**说明**：{_escape(execution_result.get('summary') or '-')}",
                    f"**执行时间**：{_escape(data.get('captured_at') or '-')}",
                ]
            )
        ),
        {"tag": "hr"},
    ]
    if action_results:
        for action in action_results:
            if not isinstance(action, dict):
                continue
            fill = action.get("fill_result") if isinstance(action.get("fill_result"), dict) else {}
            elements.append(
                _text(
                    f"**{_escape(action.get('room_type') or '-')}** | "
                    f"{_escape(action.get('checkin_date') or '-')}\n"
                    f"填入：{_escape(fill.get('filled_value') or '-')}；"
                    f"读回：{_escape(fill.get('verified_value') or '-')}；"
                    f"状态：{_escape(_status_label(action.get('status') or '-'))}"
                )
            )
    else:
        elements.append(_text("暂无页面写入动作明细。"))
    if submit_result:
        elements.append({"tag": "hr"})
        elements.append(
            _text(
                f"**提交结果**：{_escape(_status_label(submit_result.get('status') or '-'))}\n"
                f"{_escape(submit_result.get('message') or '')}"
            )
        )
    elements.append({"tag": "hr"})
    elements.append(_text("请以携程 EBK 后台实际页面结果为最终确认依据。"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if status in {"executed", "success"} else "orange",
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 调价执行结果"},
        },
        "elements": elements,
    }


def build_pricing_execution_blocked_card(
    *,
    blocked_step: str,
    blocked_status: str,
    blocked_summary: str,
    next_action: str = "",
    audit_record_id: int | None = None,
    watch_group_key: str = "",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lines = [
        f"**调价流程阻断**：{_escape(blocked_step)}",
        f"**状态**：{_escape(_status_label(blocked_status))}",
        f"**原因**：{_escape(blocked_summary)}",
    ]
    if next_action:
        lines.append(f"**建议操作**：{_escape(next_action)}")
    if audit_record_id is not None:
        lines.append(f"**审计记录**：{audit_record_id}")
    if watch_group_key:
        lines.append(f"**监控组**：{_escape(watch_group_key)}")
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
            "title": {"tag": "plain_text", "content": "Pan Hotel Watch 调价流程阻断"},
        },
        "elements": elements,
    }


def build_pricing_execution_result_fallback_text(execution_result: dict[str, Any]) -> str:
    status = str(execution_result.get("status") or "unknown")
    lines = [
        "Pan Hotel Watch 调价执行结果",
        f"状态：{_status_label(status)}",
        f"说明：{execution_result.get('summary') or '-'}",
    ]
    data = execution_result.get("data") if isinstance(execution_result.get("data"), dict) else {}
    for action in data.get("action_results") or []:
        if not isinstance(action, dict):
            continue
        lines.append(
            f"{action.get('checkin_date') or '-'} {action.get('room_type') or '-'}："
            f"{_status_label(action.get('status') or '-')}"
        )
    return "\n".join(lines)


def build_pricing_execution_blocked_fallback_text(card: dict[str, Any]) -> str:
    header = card.get("header") if isinstance(card.get("header"), dict) else {}
    title = header.get("title") if isinstance(header.get("title"), dict) else {}
    lines = [str(title.get("content") or "Pan Hotel Watch 调价流程阻断")]
    for element in card.get("elements") or []:
        if not isinstance(element, dict):
            continue
        text = element.get("text") if isinstance(element.get("text"), dict) else {}
        content = text.get("content")
        if content:
            lines.append(str(content).replace("**", ""))
            break
    return "\n".join(lines)


def _status_label(status: Any) -> str:
    return {
        "executed": "已执行",
        "success": "成功",
        "failed": "失败",
        "human_required": "需要人工处理",
        "needs_review": "需要复核",
        "page_structure_changed": "页面结构变化",
        "data_required": "缺少可信数据",
        "page_write_preview_ready": "页面空跑已就绪",
        "preflight_ready": "预检通过",
        "write_not_allowed": "不允许自动写价",
        "filled": "已填入",
        "execute_submitted": "已提交",
        "not_approved": "未通过 Feishu 审批",
        "not_preflight_ready": "未完成前置步骤",
    }.get(str(status), str(status))


def _safe_evidence(evidence: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in evidence.items():
        lower = str(key).lower()
        if any(
            token in lower
            for token in (
                "secret",
                "password",
                "cookie",
                "token",
                "webhook",
                "screenshot",
                "profile",
            )
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
