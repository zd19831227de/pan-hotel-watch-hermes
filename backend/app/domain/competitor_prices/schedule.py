from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.competitor_prices.types import (
    DEFAULT_SCHEDULE_END_TIME,
    DEFAULT_SCHEDULE_INTERVAL_MINUTES,
    DEFAULT_SCHEDULE_MODE,
    DEFAULT_SCHEDULE_START_TIME,
    SCHEDULE_MODE_PATTERN,
    TIME_OF_DAY_PATTERN,
)

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def competitor_watch_schedule_state(
    group: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = (now or _local_now()).replace(tzinfo=None)
    mode = _normalize_schedule_mode(_group_value(group, "schedule_mode"))
    start_text = _normalize_time_of_day(
        _group_value(group, "schedule_start_time"),
        default=DEFAULT_SCHEDULE_START_TIME,
    )
    end_text = _normalize_time_of_day(
        _group_value(group, "schedule_end_time"),
        default=DEFAULT_SCHEDULE_END_TIME,
    )
    interval_minutes = _schedule_interval_minutes(group)
    start_at, end_at = _schedule_window_for(current_time, start_text, end_text)
    in_window = start_at <= current_time < end_at
    last_polled_at = _as_naive_datetime(_group_value(group, "last_polled_at"))
    last_slot_at = _latest_schedule_slot_at(start_at, end_at, current_time, interval_minutes)
    next_slot_at = _next_schedule_slot_at(start_at, end_at, current_time, interval_minutes)
    drift_due_at = _drift_due_at(start_at, last_polled_at, interval_minutes)

    if mode == "last_run_interval":
        due = in_window and drift_due_at <= current_time
        next_run_at = (
            max(drift_due_at, start_at)
            if current_time < end_at
            else _next_window_start(start_at, current_time)
        )
    else:
        due = (
            in_window
            and last_slot_at is not None
            and (
                last_polled_at is None
                or last_polled_at < last_slot_at
                or last_polled_at < start_at
            )
        )
        next_run_at = next_slot_at

    return {
        "timezone": str(_group_value(group, "timezone") or "Asia/Shanghai"),
        "mode": mode,
        "start_time": start_text,
        "end_time": end_text,
        "interval_minutes": interval_minutes,
        "window_start_at": start_at.isoformat(),
        "window_end_at": end_at.isoformat(),
        "in_window": in_window,
        "due": due,
        "last_slot_at": last_slot_at.isoformat() if last_slot_at else None,
        "next_slot_at": next_slot_at.isoformat() if next_slot_at else None,
        "drift_due_at": drift_due_at.isoformat(),
        "next_run_at": next_run_at.isoformat() if next_run_at else None,
        "last_polled_at": last_polled_at.isoformat() if last_polled_at else None,
    }


def watch_group_is_due(group: Any, now: datetime) -> bool:
    return bool(competitor_watch_schedule_state(group, now=now)["due"])


def _group_value(group: Any, key: str) -> Any:
    if isinstance(group, dict):
        return group.get(key)
    return getattr(group, key, None)


def _schedule_interval_minutes(group: Any) -> int:
    value = _group_value(group, "schedule_interval_minutes")
    if value is None:
        value = _group_value(group, "query_interval_minutes")
    try:
        interval = int(value)
    except (TypeError, ValueError):
        interval = DEFAULT_SCHEDULE_INTERVAL_MINUTES
    return max(1, min(interval, 1440))


def _normalize_schedule_mode(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(SCHEDULE_MODE_PATTERN, text):
        return text
    return DEFAULT_SCHEDULE_MODE


def _normalize_time_of_day(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(TIME_OF_DAY_PATTERN, text):
        return text
    return default


def _parse_time_of_day(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def _schedule_window_for(
    now: datetime,
    start_text: str,
    end_text: str,
) -> tuple[datetime, datetime]:
    start_time = _parse_time_of_day(start_text)
    end_time = _parse_time_of_day(end_text)
    start_at = datetime.combine(now.date(), start_time)
    end_at = datetime.combine(now.date(), end_time)
    if end_at <= start_at:
        end_at += timedelta(days=1)
        if now < start_at and now < datetime.combine(now.date(), end_time):
            start_at -= timedelta(days=1)
            end_at -= timedelta(days=1)
    return start_at, end_at


def _latest_schedule_slot_at(
    start_at: datetime,
    end_at: datetime,
    now: datetime,
    interval_minutes: int,
) -> datetime | None:
    if not (start_at <= now < end_at):
        return None
    elapsed_minutes = int((now - start_at).total_seconds() // 60)
    slot_offset = (elapsed_minutes // interval_minutes) * interval_minutes
    slot_at = start_at + timedelta(minutes=slot_offset)
    return slot_at if slot_at < end_at else None


def _next_schedule_slot_at(
    start_at: datetime,
    end_at: datetime,
    now: datetime,
    interval_minutes: int,
) -> datetime | None:
    if now < start_at:
        return start_at
    if now >= end_at:
        return start_at + timedelta(days=1)
    latest = _latest_schedule_slot_at(start_at, end_at, now, interval_minutes)
    if latest is None:
        return start_at
    next_slot = latest + timedelta(minutes=interval_minutes)
    return next_slot if next_slot < end_at else start_at + timedelta(days=1)


def _next_window_start(start_at: datetime, now: datetime) -> datetime:
    if now < start_at:
        return start_at
    return start_at + timedelta(days=1)


def _drift_due_at(
    start_at: datetime,
    last_polled_at: datetime | None,
    interval_minutes: int,
) -> datetime:
    if last_polled_at is None or last_polled_at < start_at:
        return start_at
    return last_polled_at + timedelta(minutes=interval_minutes)


def _as_naive_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
    return parsed


def _local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
