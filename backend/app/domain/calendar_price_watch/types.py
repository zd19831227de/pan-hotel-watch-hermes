from __future__ import annotations

import re

from pydantic import BaseModel, Field

DEFAULT_CALENDAR_FUTURE_DAYS = 15
MAX_CALENDAR_FUTURE_DAYS = 30
DEFAULT_HIGH_GAP_THRESHOLD_YUAN = 10
DEFAULT_LOW_GAP_THRESHOLD_YUAN = 20
DEFAULT_MATRIX_BRIEF_PUSH_TIMES = ("10:00", "15:00", "21:00")
# Legacy single-slot defaults kept for migration/compat reads.
DEFAULT_MATRIX_BRIEF_MORNING_PUSH_TIME = DEFAULT_MATRIX_BRIEF_PUSH_TIMES[0]
DEFAULT_MATRIX_BRIEF_EVENING_PUSH_TIME = DEFAULT_MATRIX_BRIEF_PUSH_TIMES[-1]
TIME_OF_DAY_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CalendarPriceWatchConfigPatch(BaseModel):
    future_days: int | None = Field(default=None, ge=1, le=MAX_CALENDAR_FUTURE_DAYS)
    high_gap_threshold_yuan: int | None = Field(default=None, ge=0, le=5000)
    low_gap_threshold_yuan: int | None = Field(default=None, ge=0, le=5000)
    auto_poll_enabled: bool | None = None
    schedule_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    alert_title: str | None = Field(default=None, max_length=128)
    matrix_brief_enabled: bool | None = None
    matrix_brief_title: str | None = Field(default=None, max_length=128)
    matrix_brief_push_times: list[str] | None = None
    matrix_brief_morning_push_time: str | None = Field(default=None, max_length=5)
    matrix_brief_evening_push_time: str | None = Field(default=None, max_length=5)
    browser_session_id: str | None = Field(default=None, max_length=128)
