from __future__ import annotations

from typing import Any

from client.mobile_summary_contract import build_rule_mobile_summary, normalize_mobile_summary


def _operator(payload: dict[str, Any]) -> dict[str, Any]:
    operator = payload.get("operator_summary")
    return operator if isinstance(operator, dict) else {}


def build_alert_mobile_summary(payload: dict[str, Any]) -> dict[str, Any]:
    alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else payload
    operator = _operator(payload)
    direction = str(alert.get("alert_direction") or alert.get("direction") or "").strip().lower()
    summary_text = str(alert.get("summary") or alert.get("headline") or "竞品价格有变化")
    captured_at = str(operator.get("latest_captured_at") or operator.get("captured_at") or "")
    evidence_ref = "ev_alert_1"
    evidence_items = [
        {
            "evidence_ref": evidence_ref,
            "source": "ctrip_competitor",
            "label": "竞品价格变化",
            "value": summary_text,
            "unit": "",
            "captured_at": captured_at or None,
            "freshness": "fresh" if captured_at else "unknown",
            "trust": "trusted",
            "priority": 5,
        }
    ]
    risk_flags: list[str] = []
    if direction == "increase":
        risk_flags.append("竞品涨价仅提醒")
        actions = [{"text": "竞品上行仅提醒，不做本店跟价建议"}]
        conclusion = "竞品价格上行，仅提醒，不建议跟进本店调高。"
    elif direction == "decrease":
        actions = [{"text": "人工确认目标价后再走飞书审批"}]
        conclusion = "竞品价格下行，可做影响分析，需人工确认目标价。"
    else:
        actions = [{"text": "核对变化明细后再决定人工动作"}]
        conclusion = "竞品价格有变化，请先看证据。"
    raw = {
        "status": "alert" if direction else "success",
        "conclusion": conclusion,
        "findings": [{"text": summary_text, "evidence_refs": [evidence_ref]}],
        "actions": actions,
        "data_time": captured_at or "未提供明确读取时间",
        "freshness": "fresh" if captured_at else "unknown",
        "missing_data": [],
        "risk_flags": risk_flags,
        "detail_intents": ["查看明细"],
        "safety_boundary": "read_only",
        "response_mode": "summary",
    }
    summary = normalize_mobile_summary(
        raw,
        evidence_index={evidence_ref: evidence_items[0]},
    )
    if not summary.get("findings"):
        summary = build_rule_mobile_summary(
            evidence_items=evidence_items,
            status=str(raw["status"]),
            risk_flags=risk_flags,
            safety_boundary="read_only",
            detail_intents=["查看明细"],
        )
    return summary


def build_status_mobile_summary(payload: dict[str, Any]) -> dict[str, Any]:
    operator = _operator(payload)
    overall = str(operator.get("overall_status") or payload.get("overall_status") or "unknown")
    checked_at = str(payload.get("checked_at") or operator.get("latest_captured_at") or "")
    module_rows = operator.get("module_rows") if isinstance(operator.get("module_rows"), list) else []
    abnormal = [
        row
        for row in module_rows
        if isinstance(row, dict)
        and str(row.get("status") or row.get("value") or "").lower()
        not in {"ok", "fresh", "success", "正常", ""}
    ]
    evidence_items: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if abnormal:
        for index, row in enumerate(abnormal[:3], start=1):
            ref = f"ev_status_{index}"
            label = str(row.get("field") or row.get("label") or f"模块{index}")
            value = str(row.get("value") or row.get("status") or "异常")
            evidence_items.append(
                {
                    "evidence_ref": ref,
                    "source": "ops",
                    "label": label,
                    "value": value,
                    "unit": "",
                    "captured_at": checked_at or None,
                    "freshness": "fresh" if checked_at else "unknown",
                    "trust": "trusted",
                    "priority": 10 + index,
                }
            )
            findings.append({"text": f"{label} {value}", "evidence_refs": [ref]})
        conclusion = "盯盘服务有异常，请先处理下方模块。"
        actions = [{"text": "核对异常模块并决定是否重启 worker"}]
    else:
        ref = "ev_status_ok"
        evidence_items.append(
            {
                "evidence_ref": ref,
                "source": "ops",
                "label": "服务状态",
                "value": overall or "正常",
                "unit": "",
                "captured_at": checked_at or None,
                "freshness": "fresh" if checked_at else "unknown",
                "trust": "trusted",
                "priority": 20,
            }
        )
        findings = []
        conclusion = "盯盘服务当前正常。"
        actions = [{"text": "如需明细请说查看明细"}]
    return normalize_mobile_summary(
        {
            "status": "success" if not abnormal else "alert",
            "conclusion": conclusion,
            "findings": findings,
            "actions": actions,
            "data_time": checked_at or "未提供明确读取时间",
            "freshness": "fresh" if checked_at else "unknown",
            "missing_data": [],
            "risk_flags": ["服务异常"] if abnormal else [],
            "detail_intents": ["查看明细"],
            "safety_boundary": "read_only",
            "response_mode": "summary",
        },
        evidence_index={item["evidence_ref"]: item for item in evidence_items},
    )
