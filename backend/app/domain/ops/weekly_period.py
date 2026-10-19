from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def resolve_previous_calendar_week(
    *,
    reference_date: date | None = None,
) -> dict[str, str]:
    today = reference_date or datetime.now(LOCAL_TIMEZONE).date()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    start_at = datetime.combine(last_monday, time.min)
    end_at = datetime.combine(last_sunday, time(23, 59, 59))
    return {
        "label": f"{last_monday.isoformat()} 至 {last_sunday.isoformat()}",
        "start_date": last_monday.isoformat(),
        "end_date": last_sunday.isoformat(),
        "start_at": start_at.isoformat(sep=" "),
        "end_at": end_at.isoformat(sep=" "),
    }


def previous_week_bounds(
    *,
    reference_date: date | None = None,
) -> tuple[datetime, datetime]:
    today = reference_date or datetime.now(LOCAL_TIMEZONE).date()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    return (
        datetime.combine(last_monday, time.min),
        datetime.combine(last_sunday, time(23, 59, 59)),
    )
