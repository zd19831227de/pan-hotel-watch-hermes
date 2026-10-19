from __future__ import annotations

import math
from typing import Any

from app.domain.browser.ctrip_price_converter import DEFAULT_COMMISSION_RATE, sell_to_base
from app.domain.price_change_alerts.types import TRUSTED_SOURCE_STATEMENT, ComparableRate
from app.domain.pricing_strategy.repository import PricingStrategyDetail


def build_rule_based_suggestions(
    *,
    changed_items: list[dict[str, Any]],
    current_competitor_rates: dict[tuple[int, str, str, str], ComparableRate],
    own_current_rates: dict[tuple[str, str], ComparableRate],
    strategy: PricingStrategyDetail,
    account_write_policy: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    suggestions: list[dict[str, Any]] = []
    missing_reasons: list[str] = []
    for item in changed_items:
        if int(item.get("signed_delta_yuan") or 0) > 0:
            missing_reasons.append("competitor_price_increase_no_own_price_suggestion")
            continue
        competitor_rate = current_competitor_rates.get(_item_key(item))
        if competitor_rate is None:
            missing_reasons.append("competitor_rate_missing")
            continue
        own_rate = own_current_rates.get(competitor_rate.own_match_key)
        if own_rate is None:
            missing_reasons.append("own_matching_trusted_rate_missing")
            continue
        suggestions.append(
            _suggestion_from_change(
                item=item,
                competitor_rate=competitor_rate,
                own_rate=own_rate,
                strategy=strategy,
                account_write_policy=account_write_policy or {},
            )
        )
    return suggestions, missing_reasons


def _suggestion_from_change(
    *,
    item: dict[str, Any],
    competitor_rate: ComparableRate,
    own_rate: ComparableRate,
    strategy: PricingStrategyDetail,
    account_write_policy: dict[str, Any],
) -> dict[str, Any]:
    delta = int(item.get("signed_delta_yuan") or 0)
    absolute_delta = abs(delta)
    if delta >= 0:
        raise ValueError("price-change suggestions are only generated for competitor price drops")
    if not strategy.allow_downward_suggestion:
        adjustment = 0
        needs_attention = True
        reason = "竞品降价超过阈值，当前策略暂不自动建议降价，建议关注。"
    else:
        capped = min(absolute_delta, int(strategy.max_suggested_change_yuan))
        stepped = _round_to_step(capped, int(strategy.recommendation_step_yuan))
        adjustment = -stepped
        needs_attention = False
        reason = (
            f"竞品降价超过 {float(strategy.change_trigger_percent):.2f}% 或配置金额阈值，"
            "建议本店降价。"
        )
    recommended_price = max(0, int(own_rate.price_amount_yuan) + adjustment)
    commission_rate = DEFAULT_COMMISSION_RATE
    hotel = item.get("hotel") if isinstance(item.get("hotel"), dict) else {}
    return {
        "hotel_config_id": competitor_rate.hotel_config_id,
        "hotel_name": hotel.get("alias") or hotel.get("hotel_name") or competitor_rate.hotel_name,
        "ctrip_hotel_id": competitor_rate.ctrip_hotel_id,
        "date_label": competitor_rate.date_label,
        "checkin_date": competitor_rate.checkin_date,
        "checkout_date": competitor_rate.checkout_date,
        "room_type": own_rate.room_type,
        "normalized_room_type": own_rate.room_type_key,
        "competitor_room_type": competitor_rate.room_type,
        "breakfast_kind": competitor_rate.breakfast_kind,
        "breakfast_text": competitor_rate.breakfast_text,
        "current_own_price_yuan": own_rate.price_amount_yuan,
        "current_base_price_yuan": sell_to_base(own_rate.price_amount_yuan, commission_rate),
        "recommended_base_price_yuan": sell_to_base(recommended_price, commission_rate),
        "commission_rate": commission_rate,
        "competitor_previous_price_yuan": item.get("previous_price_yuan"),
        "competitor_current_price_yuan": competitor_rate.price_amount_yuan,
        "change_amount_yuan": delta,
        "change_percent": item.get("delta_percent"),
        "recommended_adjustment_yuan": adjustment,
        "recommended_price_yuan": recommended_price,
        "trigger_reason": reason,
        "needs_human_attention": needs_attention,
        "trusted_source_statement": TRUSTED_SOURCE_STATEMENT,
        "connection_mode": str(account_write_policy.get("connection_mode") or "unknown"),
        "can_auto_write_price": bool(account_write_policy.get("can_auto_write_price")),
        "write_policy_message": str(
            account_write_policy.get("write_policy_message")
            or "账号类型未确认，默认按直连处理，仅生成调价建议，不会自动改价。"
        ),
    }


def _round_to_step(amount: int, step: int) -> int:
    if amount <= 0:
        return 0
    step = max(1, step)
    return int(math.floor(amount / step) * step) or step


def _item_key(item: dict[str, Any]) -> tuple[int, str, str, str]:
    hotel = item.get("hotel") if isinstance(item.get("hotel"), dict) else {}
    return (
        int(hotel.get("config_id") or 0),
        str(item.get("checkin_date") or item.get("date_label") or ""),
        str(item.get("breakfast_kind") or ""),
        str(item.get("room_type_match_key") or ""),
    )
