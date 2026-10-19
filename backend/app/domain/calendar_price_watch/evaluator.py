from __future__ import annotations

from typing import Any


def evaluate_calendar_price_alerts(
    *,
    points: list[dict[str, Any]],
    high_gap_threshold_yuan: int,
    low_gap_threshold_yuan: int | None = None,
) -> list[dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        if not point.get("is_trusted") or point.get("price_amount_yuan") is None:
            continue
        by_date.setdefault(str(point["checkin_date"]), []).append(point)

    alerts: list[dict[str, Any]] = []
    for checkin_date, date_points in sorted(by_date.items()):
        own_points = [point for point in date_points if point.get("role") == "own"]
        competitor_points = [point for point in date_points if point.get("role") != "own"]
        if not own_points or not competitor_points:
            continue
        own = min(own_points, key=lambda point: int(point["price_amount_yuan"]))
        lowest = min(competitor_points, key=lambda point: int(point["price_amount_yuan"]))
        own_price = int(own["price_amount_yuan"])
        lowest_price = int(lowest["price_amount_yuan"])
        if own_price > lowest_price:
            high_gap = own_price - lowest_price
            if high_gap < high_gap_threshold_yuan:
                continue
            alerts.append(
                _calendar_alert_payload(
                    alert_type="own_price_high",
                    checkin_date=checkin_date,
                    own=own,
                    reference=lowest,
                    gap_yuan=high_gap,
                )
            )
            continue
        low_threshold = int(low_gap_threshold_yuan or 0)
        low_gap = lowest_price - own_price
        if low_threshold > 0 and low_gap >= low_threshold:
            alerts.append(
                _calendar_alert_payload(
                    alert_type="own_price_low",
                    checkin_date=checkin_date,
                    own=own,
                    reference=lowest,
                    gap_yuan=low_gap,
                )
            )
    return alerts


def _calendar_alert_payload(
    *,
    alert_type: str,
    checkin_date: str,
    own: dict[str, Any],
    reference: dict[str, Any],
    gap_yuan: int,
) -> dict[str, Any]:
    own_alias = str(own.get("hotel_alias") or "本店")
    reference_alias = str(reference.get("hotel_alias") or "竞对")
    own_price = int(own["price_amount_yuan"])
    reference_price = int(reference["price_amount_yuan"])
    direction = "低" if alert_type == "own_price_low" else "高"
    summary = f"{checkin_date} {own_alias} ¥{own_price}，比 {reference_alias} ¥{reference_price} {direction} ¥{gap_yuan}"
    return {
        "alert_type": alert_type,
        "checkin_date": checkin_date,
        "own_price_yuan": own_price,
        "reference_price_yuan": reference_price,
        "gap_yuan": gap_yuan,
        "reference_hotel_alias": reference_alias,
        "summary": summary,
    }
