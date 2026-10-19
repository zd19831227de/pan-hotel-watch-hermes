from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

CONTRACT_VERSION = "operator_summary.v1"

_TOOL_DEFAULTS: dict[str, tuple[str, str]] = {
    "get_operator_hotel_overview": ("analysis", "analysis"),
    "get_operator_analysis_context": ("analysis", "analysis"),
    "read_ebk_datasets": ("ebk", "immediate_read"),
    "get_ebk_latest_result": ("ebk", "latest_query"),
    "run_ebk_watch_once": ("ebk", "immediate_read"),
    "get_competitor_latest_prices": ("competitor", "latest_query"),
    "get_competitor_price_trend": ("competitor", "trend"),
    "run_competitor_watch_once": ("competitor", "immediate_read"),
    "get_calendar_price_latest": ("calendar_price", "latest_query"),
    "get_calendar_price_watch_status": ("calendar_price", "status"),
    "run_calendar_price_watch_once": ("calendar_price", "immediate_read"),
    "analyze_competitor_price_change": ("pricing", "analysis"),
    "create_manual_pricing_approval": ("pricing", "approval_request"),
    "dispatch_watch_feishu_event": ("pricing", "event_forward"),
    "get_watch_scheduler_status": ("ops", "status"),
    "get_operator_recent_activity": ("ops", "latest_query"),
    "get_weekly_operator_briefing": ("analysis", "weekly_briefing"),
    "resolve_feishu_chat_context": ("config", "status"),
    "get_operator_watch_configuration": ("config", "status"),
    "pause_watch": ("ops", "watch_toggle"),
    "resume_watch": ("ops", "watch_toggle"),
    "list_watch_groups": ("config", "status"),
}

_SENSITIVE_MARKERS = (
    "webhook",
    "token",
    "secret",
    "cookie",
    "password",
    "verification",
    "profile",
    "screenshot",
    "appdata",
    "open-apis/bot",
    "chrome.exe",
)

_ROW_FIELDS = (
    "overview_rows",
    "price_rows",
    "calendar_rows",
    "change_rows",
    "trend_rows",
    "status_rows",
    "module_rows",
    "dataset_read_rows",
    "beneficial_metrics",
)

_MODULE_ALIASES = {
    "calendar_price_watch": "calendar_price",
}


def normalize_operator_summary(
    payload: dict[str, Any],
    *,
    tool_name: str | None = None,
    module: str | None = None,
    intent: str | None = None,
) -> dict[str, Any]:
    result = deepcopy(payload)
    raw_operator = result.get("operator_summary")
    operator: dict[str, Any] = raw_operator if isinstance(raw_operator, dict) else {}
    result["operator_summary"] = operator
    _sanitize_operator_rows(operator)

    default_module, default_intent = _TOOL_DEFAULTS.get(str(tool_name or ""), ("ops", "latest_query"))
    raw_module = str(module or operator.get("module") or default_module)
    normalized_module = _normalize_module(raw_module)
    if raw_module and raw_module != normalized_module and "legacy_module" not in operator:
        operator["legacy_module"] = raw_module
    operator["contract_version"] = CONTRACT_VERSION
    operator["module"] = normalized_module
    operator["intent"] = str(intent or operator.get("intent") or default_intent)
    operator["status"] = _normalize_status(result.get("status") or operator.get("status"))
    operator["conclusion"] = _safe_text(
        operator.get("conclusion")
        or operator.get("intro")
        or result.get("summary")
        or operator.get("summary")
        or _fallback_conclusion(operator["module"], operator["status"])
    )
    operator["data_time"] = _normalize_data_time(operator)
    operator["freshness"] = _normalize_freshness(operator)
    operator["evidence"] = _normalize_evidence(operator)
    operator["risk"] = _normalize_risk(operator, tool_name=tool_name)
    operator["next_actions"] = _normalize_next_actions(operator, tool_name=tool_name)
    operator["safety_boundary"] = _normalize_safety_boundary(operator, tool_name=tool_name)
    operator["reply_mode"] = str(operator.get("reply_mode") or _default_reply_mode(result, tool_name))
    response_mode = str(result.get("response_mode") or operator.get("response_mode") or "").strip().lower()
    if response_mode in {"text", "summary"} and not result.get("feishu_card_sent"):
        # 顶层 text/summary 且未发卡时，强制 text_only，避免残留 feishu_card 被判成 card_first。
        operator["reply_mode"] = "text_only"
        operator["response_mode"] = "text"
        result["response_mode"] = "text"
    elif response_mode in {"summary", "detail"}:
        operator["response_mode"] = response_mode
        result["response_mode"] = response_mode
    operator["routing_hints"] = _normalize_routing_hints(operator)
    return result


def _normalize_status(value: Any) -> str:
    text = str(value or "success").strip()
    if text in {
        "success",
        "partial",
        "stale",
        "missing",
        "failed",
        "config_required",
        "human_required",
        "blocked",
        "no_trigger",
        "alert",
        "queued",
        "tool_not_allowed",
        "invalid_arguments",
    }:
        return text
    if text in {"error", "timeout"}:
        return "failed"
    return text or "success"


def _normalize_module(value: Any) -> str:
    text = str(value or "ops").strip() or "ops"
    return _MODULE_ALIASES.get(text, text)


def _normalize_data_time(operator: dict[str, Any]) -> dict[str, Any]:
    existing = operator.get("data_time") if isinstance(operator.get("data_time"), dict) else {}
    captured_at = (
        existing.get("captured_at")
        or operator.get("latest_captured_at")
        or operator.get("captured_at")
        or operator.get("data_captured_at")
    )
    display = existing.get("display") or operator.get("data_captured_at_display") or _display_time(captured_at)
    source = existing.get("source") or operator.get("source") or "unknown"
    return {"captured_at": captured_at, "display": display, "source": source}


def _normalize_freshness(operator: dict[str, Any]) -> dict[str, Any]:
    existing = operator.get("freshness") if isinstance(operator.get("freshness"), dict) else {}
    captured_at = operator.get("data_time", {}).get("captured_at") if isinstance(operator.get("data_time"), dict) else None
    age_minutes = existing.get("age_minutes")
    if age_minutes is None:
        age_minutes = _age_minutes(captured_at)
    state = existing.get("state")
    if not state:
        if operator.get("data_is_stale") is True or operator.get("suggest_immediate_run") is True:
            state = "stale"
        elif captured_at:
            state = "fresh" if age_minutes is None or age_minutes <= int(existing.get("stale_after_minutes") or 360) else "stale"
        elif operator.get("status") in {"failed", "human_required"}:
            state = "failed"
        elif operator.get("status") in {"missing", "config_required"}:
            state = "missing"
        else:
            state = "unknown"
    note = existing.get("note")
    if not note:
        if state == "unknown":
            note = "未提供明确读取时间。"
        elif age_minutes is not None:
            note = f"数据约 {age_minutes} 分钟前读取。"
        elif captured_at:
            note = "已提供读取时间。"
        else:
            note = "暂无数据时间。"
    return {
        "state": state,
        "age_minutes": age_minutes,
        "stale_after_minutes": existing.get("stale_after_minutes"),
        "note": note,
    }


def _normalize_evidence(operator: dict[str, Any]) -> list[dict[str, str]]:
    existing = operator.get("evidence")
    if isinstance(existing, list) and existing:
        cleaned = [_clean_evidence_item(item) for item in existing if isinstance(item, dict)]
        return [item for item in cleaned if item]
    evidence: list[dict[str, str]] = []
    for field_name in _ROW_FIELDS:
        rows = operator.get(field_name)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = row.get("label") or row.get("field") or row.get("name")
            value = row.get("value") or row.get("display_value") or row.get("summary")
            item = _clean_evidence_item({"label": label, "value": value, "kind": _evidence_kind(field_name)})
            if item:
                evidence.append(item)
            if len(evidence) >= 6:
                return evidence
    return evidence


def _clean_evidence_item(item: dict[str, Any]) -> dict[str, str]:
    label = _safe_text(item.get("label") or item.get("field") or "")
    value = _safe_text(item.get("value") or "")
    kind = str(item.get("kind") or "fact")
    if not label or not value or label == "敏感信息已隐藏" or value == "敏感信息已隐藏":
        return {}
    return {"label": label, "value": value, "kind": kind}


def _sanitize_operator_rows(operator: dict[str, Any]) -> None:
    for field_name in _ROW_FIELDS:
        rows = operator.get(field_name)
        if not isinstance(rows, list):
            continue
        sanitized_rows: list[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                sanitized_rows.append(row)
                continue
            sanitized = {
                key: (_safe_text(value) if isinstance(value, str) else value)
                for key, value in row.items()
            }
            sanitized_rows.append(sanitized)
        operator[field_name] = sanitized_rows


def _normalize_risk(operator: dict[str, Any], *, tool_name: str | None) -> dict[str, Any]:
    existing = operator.get("risk") if isinstance(operator.get("risk"), dict) else {}
    flags = (
        [str(item) for item in existing.get("flags", []) if str(item).strip()]
        if isinstance(existing.get("flags"), list)
        else []
    )
    status = str(operator.get("status") or "")
    if status in {"human_required"} and "需要人工处理" not in flags:
        flags.append("需要人工处理")
    if status in {"failed"} and "读取失败" not in flags:
        flags.append("读取失败")
    if tool_name == "analyze_competitor_price_change" and operator.get("alert_direction") == "increase":
        flags.append("竞品涨价仅提醒")
    level = existing.get("level")
    if not level:
        if status in {"human_required"}:
            level = "human_required"
        elif status in {"blocked", "config_required"}:
            level = "blocked"
        elif flags or status in {"alert", "failed", "partial", "tool_not_allowed", "invalid_arguments"}:
            level = "attention"
        else:
            level = "none"
    note = _safe_text(existing.get("note") or operator.get("risk_note") or "")
    return {"level": level, "flags": flags, "note": note}


def _normalize_next_actions(operator: dict[str, Any], *, tool_name: str | None) -> list[dict[str, Any]]:
    existing = operator.get("next_actions")
    if isinstance(existing, list):
        normalized: list[dict[str, Any]] = []
        for item in existing:
            if isinstance(item, dict):
                normalized.append(item)
                continue
            label = str(item or "").strip()
            if label:
                normalized.append({"label": label, "requires_confirmation": False})
        return normalized
    if operator.get("suggest_immediate_run") is True:
        run_tool = _run_tool_for(tool_name)
        if run_tool:
            return [
                {
                    "label": "立即刷新数据",
                    "tool": run_tool,
                    "requires_confirmation": True,
                    "arguments": {},
                }
            ]
    return []


def _normalize_safety_boundary(operator: dict[str, Any], *, tool_name: str | None) -> dict[str, Any]:
    existing = operator.get("safety_boundary") if isinstance(operator.get("safety_boundary"), dict) else {}
    if existing:
        return existing
    name = str(tool_name or "")
    if name == "create_manual_pricing_approval":
        return {
            "level": "approval_only",
            "summary": "只创建飞书审批卡，不直接 OTA 写价。",
            "allowed": ["create_feishu_approval_card"],
            "forbidden": ["direct_ota_write", "pricing_execute_without_feishu_approval"],
        }
    if name == "dispatch_watch_feishu_event":
        return {
            "level": "execution_guarded",
            "summary": "仅转发飞书事件，后端继续执行审批安全链。",
            "allowed": ["forward_feishu_event"],
            "forbidden": ["bypass_feishu_approval", "direct_ota_write"],
        }
    if name == "analyze_competitor_price_change" and operator.get("alert_direction") == "increase":
        return {
            "level": "read_only",
            "summary": "竞品涨价仅提醒，不生成本店涨价建议或执行路径。",
            "allowed": ["read_price_change_alert"],
            "forbidden": ["own_hotel_price_increase_suggestion", "pricing_execute", "pricing_dry_run", "ota_write"],
        }
    return {
        "level": "read_only",
        "summary": "只读查询，不触发调价执行。",
        "allowed": ["read_persisted_data", "manual_browser_read"],
        "forbidden": ["ota_write", "pricing_execute", "pricing_dry_run"],
    }


def _normalize_routing_hints(operator: dict[str, Any]) -> list[str]:
    hints = operator.get("routing_hints")
    normalized = [str(item) for item in hints if str(item).strip()] if isinstance(hints, list) else []
    if "优先读取 operator_summary.v1 后再回复" not in normalized:
        normalized.append("优先读取 operator_summary.v1 后再回复")
    return normalized


def _fallback_conclusion(module: str, status: str) -> str:
    if status in {"failed", "human_required"}:
        return "本次读取未完成，请查看风险提示和下一步。"
    labels = {
        "ebk": "已读取 EBK 数据。",
        "competitor": "已读取竞品价格数据。",
        "calendar_price": "已读取日历价数据。",
        "pricing": "已读取价格变化与审批信息。",
        "analysis": "已生成经营分析摘要。",
        "config": "已读取当前配置。",
        "ops": "已读取运行状态。",
    }
    return labels.get(module, "已读取数据，请查看卡片。")


def _display_time(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text.replace("T", " ").replace("+08:00", "")[:16]


def _age_minutes(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        captured = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() // 60))


def _evidence_kind(field_name: str) -> str:
    if "price" in field_name or "calendar" in field_name:
        return "price"
    if "module" in field_name:
        return "status"
    return "metric"


def _run_tool_for(tool_name: str | None) -> str | None:
    mapping = {
        "get_ebk_latest_result": "run_ebk_watch_once",
        "get_competitor_latest_prices": "run_competitor_watch_once",
        "get_calendar_price_latest": "run_calendar_price_watch_once",
    }
    return mapping.get(str(tool_name or ""))


def _default_reply_mode(payload: dict[str, Any], tool_name: str | None) -> str:
    top_mode = str(payload.get("response_mode") or "").strip().lower()
    if top_mode in {"text", "summary"} and not payload.get("feishu_card_sent"):
        return "text_only"
    if payload.get("feishu_card") or payload.get("feishu_card_sent"):
        return "card_first"
    if tool_name == "create_manual_pricing_approval":
        return "approval_card"
    if tool_name == "dispatch_watch_feishu_event":
        return "callback_response"
    return "text_only"


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "敏感信息已隐藏"
    if "\\" in text and len(text) > 80:
        return "敏感信息已隐藏"
    return text
