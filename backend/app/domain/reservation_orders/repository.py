from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.session import ensure_db_ready, session_scope

from .models import ReservationOrderSnapshot, local_now


async def append_reservation_snapshot(
    *,
    platform: str,
    reservation_date: str,
    total_count: int,
    by_room_type: dict[str, int] | None,
    by_hour: dict[str, int] | None = None,
    time_caliber: str | None = None,
    source: str = "manual",
    shop_key: str | None = None,
    platform_account_id: str | None = None,
    browser_session_id: str | None = None,
) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        row = ReservationOrderSnapshot(
            platform=platform,
            reservation_date=reservation_date,
            total_count=int(total_count),
            by_room_type_json=by_room_type,
            by_hour_json=by_hour,
            time_caliber=time_caliber,
            source=source,
            shop_key=shop_key,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            captured_at=local_now(),
        )
        session.add(row)
        await session.flush()
        return _to_dict(row)


async def get_latest_reservation_snapshot(
    *,
    platform: str,
    reservation_date: str | None = None,
    shop_key: str | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(ReservationOrderSnapshot).where(
            ReservationOrderSnapshot.platform == platform
        )
        # 单店视图只取该店行；历史 NULL 行（旧数据无归属）不混入（P3 §4.9.1）。
        if shop_key:
            stmt = stmt.where(ReservationOrderSnapshot.shop_key == shop_key)
        if reservation_date:
            stmt = stmt.where(ReservationOrderSnapshot.reservation_date == reservation_date)
        else:
            today = local_now().strftime("%Y-%m-%d")
            stmt = stmt.where(ReservationOrderSnapshot.reservation_date == today)
        stmt = stmt.order_by(ReservationOrderSnapshot.id.desc()).limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()
        return _to_dict(row) if row is not None else None


def _derive_peak_hours(by_hour: dict[str, Any] | None) -> list[dict[str, int]]:
    """按单量降序（同量取小时小者）取 Top3 集中时段。"""
    if not by_hour:
        return []
    items = sorted(by_hour.items(), key=lambda kv: (-int(kv[1]), kv[0]))
    return [{"hour": h, "count": int(c)} for h, c in items[:3]]


def _to_dict(row: ReservationOrderSnapshot) -> dict[str, Any]:
    by_hour_raw = row.by_hour_json or {}
    return {
        "id": row.id,
        "platform": row.platform,
        "reservation_date": row.reservation_date,
        "total_count": row.total_count,
        "by_room_type": row.by_room_type_json or {},
        "by_hour": {h: by_hour_raw[h] for h in sorted(by_hour_raw)},
        "peak_hours": _derive_peak_hours(by_hour_raw),
        "time_caliber": row.time_caliber,
        "source": row.source,
        "shop_key": row.shop_key,
        "platform_account_id": row.platform_account_id,
        "browser_session_id": row.browser_session_id,
        "captured_at": _format_datetime(row.captured_at),
    }


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value is not None else None


async def list_reservation_snapshots_by_date_range(
    *,
    platform: str,
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    """按业务日期（reservation_date）范围取每日最新一条快照，按日期升序。

    历史查询口径：预定快照的 reservation_date 即业务日，同日多抓取取最新一条。
    """
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = (
            select(ReservationOrderSnapshot)
            .where(
                ReservationOrderSnapshot.platform == platform,
                ReservationOrderSnapshot.reservation_date >= date_from,
                ReservationOrderSnapshot.reservation_date <= date_to,
            )
            .order_by(
                ReservationOrderSnapshot.reservation_date.asc(),
                ReservationOrderSnapshot.id.asc(),
            )
        )
        rows = list((await session.scalars(stmt)).all())
    latest_by_date: dict[str, ReservationOrderSnapshot] = {}
    for row in rows:
        latest_by_date[row.reservation_date] = row
    return [_to_dict(row) for row in latest_by_date.values()]
