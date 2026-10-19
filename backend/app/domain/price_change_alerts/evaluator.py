from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import and_, or_, select

from app.domain.competitor_prices.models import (
    CtripCompetitorHotel,
    CtripCompetitorHotelSnapshot,
    CtripCompetitorPriceRun,
    CtripCompetitorRoomRate,
    CtripCompetitorWatchGroup,
)
from app.domain.price_change_alerts.types import (
    TRUSTED_PRICE_SOURCE,
    ComparableRate,
)


async def load_watch_group(session, watch_group_key: str) -> CtripCompetitorWatchGroup | None:
    return await session.scalar(
        select(CtripCompetitorWatchGroup).where(
            CtripCompetitorWatchGroup.group_key == watch_group_key
        )
    )


async def latest_success_run(
    session,
    *,
    group_id: int,
    current_run_id: int | None,
) -> CtripCompetitorPriceRun | None:
    if current_run_id is not None:
        run = await session.get(CtripCompetitorPriceRun, current_run_id)
        if run is not None and run.group_id == group_id:
            return run
        return None
    return await session.scalar(
        select(CtripCompetitorPriceRun)
        .where(
            CtripCompetitorPriceRun.group_id == group_id,
            CtripCompetitorPriceRun.status == "success",
        )
        .order_by(CtripCompetitorPriceRun.captured_at.desc(), CtripCompetitorPriceRun.id.desc())
        .limit(1)
    )


async def previous_success_run(
    session,
    *,
    group_id: int,
    current_run: CtripCompetitorPriceRun,
) -> CtripCompetitorPriceRun | None:
    return await session.scalar(
        select(CtripCompetitorPriceRun)
        .where(
            CtripCompetitorPriceRun.group_id == group_id,
            CtripCompetitorPriceRun.status == "success",
            or_(
                CtripCompetitorPriceRun.captured_at < current_run.captured_at,
                and_(
                    CtripCompetitorPriceRun.captured_at == current_run.captured_at,
                    CtripCompetitorPriceRun.id < current_run.id,
                ),
            ),
        )
        .order_by(CtripCompetitorPriceRun.captured_at.desc(), CtripCompetitorPriceRun.id.desc())
        .limit(1)
    )


async def trusted_alert_rates_for_run(
    session,
    *,
    group_id: int,
    run_id: int,
) -> list[ComparableRate]:
    rows = (
        await session.execute(
            select(
                CtripCompetitorHotel,
                CtripCompetitorHotelSnapshot,
                CtripCompetitorRoomRate,
            )
            .select_from(CtripCompetitorRoomRate)
            .join(
                CtripCompetitorHotelSnapshot,
                CtripCompetitorHotelSnapshot.id == CtripCompetitorRoomRate.snapshot_id,
            )
            .join(
                CtripCompetitorHotel,
                CtripCompetitorHotel.id == CtripCompetitorRoomRate.hotel_config_id,
            )
            .where(
                CtripCompetitorHotelSnapshot.group_id == group_id,
                CtripCompetitorRoomRate.run_id == run_id,
                CtripCompetitorHotel.role.in_(("own", "competitor")),
                CtripCompetitorRoomRate.is_trusted.is_(True),
                CtripCompetitorRoomRate.source == TRUSTED_PRICE_SOURCE,
                CtripCompetitorRoomRate.price_amount_yuan.is_not(None),
            )
            .order_by(
                CtripCompetitorHotel.role.desc(),
                CtripCompetitorHotel.sort_order.asc(),
                CtripCompetitorHotelSnapshot.checkin_date.asc(),
                CtripCompetitorHotelSnapshot.date_label.asc(),
                CtripCompetitorRoomRate.is_primary.desc(),
                CtripCompetitorRoomRate.id.asc(),
            )
        )
    ).all()
    rates: list[ComparableRate] = []
    for hotel, snapshot, rate in rows:
        if not isinstance(rate.price_amount_yuan, int):
            continue
        room_type_key = room_type_match_key(rate.room_type)
        if not room_type_key:
            continue
        rates.append(
            ComparableRate(
                snapshot_id=int(snapshot.id),
                hotel_config_id=int(hotel.id),
                hotel_role=str(hotel.role),
                hotel_alias=str(hotel.alias or ""),
                ctrip_hotel_id=str(hotel.ctrip_hotel_id or ""),
                hotel_name=snapshot.hotel_name or hotel.hotel_name,
                date_label=str(snapshot.date_label or ""),
                checkin_date=snapshot.checkin_date.isoformat()
                if snapshot.checkin_date
                else None,
                checkout_date=snapshot.checkout_date.isoformat()
                if snapshot.checkout_date
                else None,
                room_type=rate.room_type,
                room_type_key=room_type_key,
                breakfast_kind=str(rate.breakfast_kind or "unknown"),
                breakfast_text=rate.breakfast_text,
                price_amount_yuan=rate.price_amount_yuan,
                availability_status=str(rate.availability_status or "unknown"),
                availability_text=rate.availability_text,
                is_primary=bool(rate.is_primary),
            )
        )
    return primary_room_type_alert_rates(rates)


async def review_issue_for_run(
    session,
    *,
    group_id: int,
    run_id: int,
) -> dict[str, Any] | None:
    snapshot_rows = list(
        (
            await session.execute(
                select(
                    CtripCompetitorHotelSnapshot.id,
                    CtripCompetitorHotelSnapshot.snapshot_status,
                    CtripCompetitorHotelSnapshot.page_state,
                    CtripCompetitorHotelSnapshot.review_reason,
                )
                .where(
                    CtripCompetitorHotelSnapshot.group_id == group_id,
                    CtripCompetitorHotelSnapshot.run_id == run_id,
                    or_(
                        CtripCompetitorHotelSnapshot.snapshot_status.not_in(("success",)),
                        and_(
                            CtripCompetitorHotelSnapshot.page_state.is_not(None),
                            CtripCompetitorHotelSnapshot.page_state != "",
                            CtripCompetitorHotelSnapshot.page_state != "authenticated",
                        ),
                    ),
                )
                .limit(5)
            )
        ).all()
    )
    if snapshot_rows:
        return {
            "reason": "snapshot_needs_review",
            "snapshots": [
                {
                    "snapshot_id": row.id,
                    "status": row.snapshot_status,
                    "page_state": row.page_state,
                    "review_reason": row.review_reason,
                }
                for row in snapshot_rows
            ],
        }
    rate_rows = list(
        (
            await session.execute(
                select(
                    CtripCompetitorRoomRate.id,
                    CtripCompetitorRoomRate.source,
                    CtripCompetitorRoomRate.is_trusted,
                )
                .where(
                    CtripCompetitorRoomRate.run_id == run_id,
                    CtripCompetitorRoomRate.price_amount_yuan.is_not(None),
                    or_(
                        CtripCompetitorRoomRate.is_trusted.is_not(True),
                        CtripCompetitorRoomRate.source != TRUSTED_PRICE_SOURCE,
                    ),
                )
                .limit(5)
            )
        ).all()
    )
    if rate_rows:
        return {
            "reason": "untrusted_price_source",
            "trusted_source": TRUSTED_PRICE_SOURCE,
            "rates": [
                {
                    "room_rate_id": row.id,
                    "source": row.source,
                    "is_trusted": bool(row.is_trusted),
                }
                for row in rate_rows
            ],
        }
    return None


def primary_room_type_alert_rates(rates: list[ComparableRate]) -> list[ComparableRate]:
    primary_room_keys: dict[tuple[int, str], str] = {}
    fallback_room_keys: dict[tuple[int, str], str] = {}
    for rate in rates:
        scope_key = rate.primary_room_scope_key
        if rate.is_primary and scope_key not in primary_room_keys:
            primary_room_keys[scope_key] = rate.room_type_key
        fallback_room_keys.setdefault(scope_key, rate.room_type_key)

    allowed_room_keys = {
        scope_key: primary_room_keys.get(scope_key) or fallback_key
        for scope_key, fallback_key in fallback_room_keys.items()
    }
    return [
        rate
        for rate in rates
        if rate.room_type_key == allowed_room_keys.get(rate.primary_room_scope_key)
    ]


def best_rates_by_key(rates: list[ComparableRate]) -> dict[tuple[int, str, str, str], ComparableRate]:
    best: dict[tuple[int, str, str, str], ComparableRate] = {}
    for rate in rates:
        existing = best.get(rate.key)
        if existing is None or rate.price_amount_yuan < existing.price_amount_yuan:
            best[rate.key] = rate
    return best


def own_rates_by_match_key(rates: list[ComparableRate]) -> dict[tuple[str, str, str], ComparableRate]:
    own_rates: dict[tuple[str, str], ComparableRate] = {}
    for rate in rates:
        if rate.hotel_role != "own":
            continue
        existing = own_rates.get(rate.own_match_key)
        if existing is None or rate.price_amount_yuan < existing.price_amount_yuan:
            own_rates[rate.own_match_key] = rate
    return own_rates


def competitor_rates_by_key(
    rates: dict[tuple[int, str, str, str], ComparableRate],
) -> dict[tuple[int, str, str, str], ComparableRate]:
    return {key: rate for key, rate in rates.items() if rate.hotel_role == "competitor"}


def trigger_result(
    *,
    previous_price: int,
    delta: int,
    trigger_mode: str,
    trigger_percent: float,
    min_delta_yuan: int,
    max_delta_yuan: int,
) -> dict[str, Any]:
    absolute_delta = abs(delta)
    percent = round((absolute_delta / previous_price) * 100, 2) if previous_price > 0 else None
    if trigger_mode == "amount":
        triggered = min_delta_yuan <= absolute_delta <= max_delta_yuan
    else:
        triggered = percent is not None and percent >= trigger_percent
    return {
        "triggered": bool(triggered),
        "mode": "amount" if trigger_mode == "amount" else "percent",
        "absolute_delta_yuan": absolute_delta,
        "signed_delta_yuan": delta,
        "delta_percent": percent,
        "threshold_percent": trigger_percent,
        "min_delta_yuan": min_delta_yuan,
        "max_delta_yuan": max_delta_yuan,
    }


def changed_item(
    *,
    current_rate: ComparableRate,
    previous_rate: ComparableRate,
    current_run: CtripCompetitorPriceRun,
    previous_run: CtripCompetitorPriceRun,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    delta = current_rate.price_amount_yuan - previous_rate.price_amount_yuan
    return {
        "candidate_key": "|".join(str(part) for part in current_rate.key),
        "hotel": {
            "config_id": current_rate.hotel_config_id,
            "role": current_rate.hotel_role,
            "alias": current_rate.hotel_alias,
            "hotel_name": current_rate.hotel_name,
            "ctrip_hotel_id": current_rate.ctrip_hotel_id,
        },
        "date_label": current_rate.date_label,
        "checkin_date": current_rate.checkin_date,
        "checkout_date": current_rate.checkout_date,
        "room_type": current_rate.room_type,
        "room_type_match_key": current_rate.room_type_key,
        "breakfast_kind": current_rate.breakfast_kind,
        "breakfast_text": current_rate.breakfast_text,
        "previous_price_yuan": previous_rate.price_amount_yuan,
        "current_price_yuan": current_rate.price_amount_yuan,
        "signed_delta_yuan": delta,
        "absolute_delta_yuan": abs(delta),
        "delta_percent": trigger.get("delta_percent"),
        "direction": "up" if delta > 0 else "down",
        "availability_status": current_rate.availability_status,
        "availability_text": current_rate.availability_text,
        "current_run_id": current_run.id,
        "previous_run_id": previous_run.id,
        "current_captured_at": current_run.captured_at.isoformat(),
        "previous_captured_at": previous_run.captured_at.isoformat(),
        "trusted_source": TRUSTED_PRICE_SOURCE,
        "trigger": trigger,
    }


def room_type_match_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", normalized).strip().lower()
