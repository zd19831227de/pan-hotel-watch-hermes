from __future__ import annotations

from typing import Any, Protocol


class PricingReferenceAiClient(Protocol):
    async def summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


async def build_pricing_reference_summary(
    alert_record: dict[str, Any],
    *,
    ai_client: PricingReferenceAiClient | None = None,
) -> dict[str, Any]:
    payload = _summary_payload(alert_record)
    if ai_client is not None:
        try:
            return _normalize_summary(await ai_client.summarize(payload), fallback_source=payload)
        except Exception:
            pass
    return _fallback_summary(payload, ai_failed=ai_client is not None)


def _summary_payload(alert_record: dict[str, Any]) -> dict[str, Any]:
    suggestions = [
        item for item in alert_record.get("suggestions") or [] if isinstance(item, dict)
    ]
    return {"suggestions": suggestions}


def _normalize_summary(value: dict[str, Any], *, fallback_source: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_summary(fallback_source, ai_failed=False)
    summary_cn = str(value.get("summary_cn") or fallback["summary_cn"]).strip()
    suggested_price = _safe_int(value.get("suggested_price_yuan"))
    price_range = value.get("suggested_price_range") if isinstance(value.get("suggested_price_range"), dict) else {}
    min_price = _safe_int(price_range.get("min")) or fallback["suggested_price_range"]["min"]
    max_price = _safe_int(price_range.get("max")) or fallback["suggested_price_range"]["max"]
    risk_flags = value.get("risk_flags") if isinstance(value.get("risk_flags"), list) else fallback["risk_flags"]
    evidence_lines = value.get("evidence_lines") if isinstance(value.get("evidence_lines"), list) else fallback["evidence_lines"]
    confidence = str(value.get("confidence_label") or fallback["confidence_label"]).strip()
    return {
        "summary_cn": summary_cn,
        "suggested_price_yuan": suggested_price or fallback["suggested_price_yuan"],
        "suggested_price_range": {"min": min(min_price, max_price), "max": max(min_price, max_price)},
        "risk_flags": [str(item) for item in risk_flags if str(item).strip()],
        "evidence_lines": [str(item) for item in evidence_lines if str(item).strip()],
        "confidence_label": confidence or "medium",
    }


def _fallback_summary(payload: dict[str, Any], *, ai_failed: bool) -> dict[str, Any]:
    suggestions = payload.get("suggestions") if isinstance(payload.get("suggestions"), list) else []
    prices = [_safe_int(item.get("recommended_price_yuan")) for item in suggestions if isinstance(item, dict)]
    prices = [price for price in prices if price is not None]
    suggested_price = min(prices) if prices else None
    if suggested_price is None:
        suggested_price = 0
    evidence_lines = [_evidence_line(item) for item in suggestions[:5] if isinstance(item, dict)]
    risk_flags = ["manual_confirmation_required"]
    if len(suggestions) > 1:
        risk_flags.append("multiple_competitor_changes")
    if ai_failed:
        risk_flags.append("ai_fallback")
    return {
        "summary_cn": "Multiple competitors dropped prices; use the rule-based reference and require manager confirmation." if len(suggestions) > 1 else "A competitor dropped prices; use the rule-based reference and require manager confirmation.",
        "suggested_price_yuan": suggested_price,
        "suggested_price_range": {"min": suggested_price, "max": suggested_price + 10},
        "risk_flags": risk_flags,
        "evidence_lines": evidence_lines or ["No trusted price evidence available."],
        "confidence_label": "rule_fallback" if ai_failed else "rule_based",
    }


def _evidence_line(item: dict[str, Any]) -> str:
    date = {"today": "today", "tomorrow": "tomorrow"}.get(str(item.get("date_label") or ""), str(item.get("date_label") or "-"))
    breakfast = {"none": " no breakfast", "included": " breakfast included"}.get(str(item.get("breakfast_kind") or ""), " breakfast unknown")
    previous_price = item.get("competitor_previous_price_yuan", "-")
    current_price = item.get("competitor_current_price_yuan", "-")
    own_price = item.get("current_own_price_yuan", "-")
    return f"{date}{breakfast}: competitor {previous_price} -> {current_price}, ours {own_price}"


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
