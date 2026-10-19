"""V5 Step 6 — EBK push throttle + daily-report scheduling guard.

Plan context (docs/pan-hotel-watch-dev-plan-v5.md Step 6): after a manual
trigger pushes, the next *scheduled* push for the same group should wait a
cooldown (default 2h) to avoid back-to-back pushes; the 22:00 daily brief is a
separate path and is exempt.

Precondition note: the plan gates this whole step behind "推送链路稳定运行
1-2 周无降级". That is an operational observation this code cannot make, so the
behaviour ships behind ``EBK_PUSH_THROTTLE_ENABLED`` which defaults to **False**
(no behaviour change). An operator flips it on once the push link has run
stably for 1-2 weeks.

The last-push state is held in process memory (the scheduler is a single
dedicated worker); this deliberately avoids new DB tables (the plan says
"沿用现有轮询配置，不新建") and is sufficient for a 2-hour cooldown. The
decision itself is a pure function fully covered by unit tests.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_MINUTES = 120
MANUAL_PUSH_SOURCES = frozenset({"api", "operator"})

# group_key -> (last_pushed_at_utc, source)
_last_push: dict[str, tuple[datetime, str]] = {}


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def reset_push_throttle_state() -> None:
    """Clear in-memory last-push state (test helper)."""

    _last_push.clear()


def record_push(group_key: str, *, source: str, run_kind: str, at: datetime | None = None) -> None:
    """Record the most recent successful push for ``group_key``.

    Daily-brief pushes are NOT recorded here, so they never trigger the manual
    cooldown on the regular scheduled push (the daily brief is a separate,
    exempt path).
    """

    if (run_kind or "").strip().lower() == "daily_brief":
        return
    normalized = str(group_key or "").strip()
    if not normalized:
        return
    _last_push[normalized] = ((at or _utc_now()), str(source or "").strip().lower())


def get_last_push(group_key: str) -> tuple[datetime, str] | None:
    return _last_push.get(str(group_key or "").strip())


def throttle_enabled() -> bool:
    return bool(get_settings().ebk_push_throttle_enabled)


def cooldown_minutes() -> int:
    value = get_settings().ebk_push_throttle_cooldown_minutes
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_MINUTES
    return minutes if minutes > 0 else DEFAULT_COOLDOWN_MINUTES


def should_throttle_scheduled_push(
    group_key: str,
    *,
    now: datetime | None = None,
    enabled: bool | None = None,
    cooldown: int | None = None,
    last_push: tuple[datetime, str] | None = None,
) -> tuple[bool, str]:
    """Decide whether a scheduled push should be skipped.

    Returns ``(True, reason)`` when the throttle fires (skip + log the reason),
    otherwise ``(False, "")``. Throttle fires only when enabled AND the most
    recent successful push for this group was manual (operator/API) AND it
    happened within the cooldown window.
    """

    is_enabled = throttle_enabled() if enabled is None else bool(enabled)
    if not is_enabled:
        return False, ""
    window = cooldown_minutes() if cooldown is None else cooldown
    record = last_push if last_push is not None else get_last_push(group_key)
    if record is None:
        return False, ""
    last_at, last_source = record
    if last_source not in MANUAL_PUSH_SOURCES:
        return False, ""
    current = now or _utc_now()
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    elapsed_minutes = (current - last_at).total_seconds() / 60.0
    if elapsed_minutes < window:
        reason = "距上次人工推送 {:.0f} 分钟（< {} 分钟冷却），跳过本次定时推送。".format(
            elapsed_minutes, window
        )
        return True, reason
    return False, ""


def maybe_log_throttle(group_key: str, reason: str) -> None:
    if reason:
        logger.info("EBK push throttle: group=%s %s", group_key, reason)


def daily_report_due(
    *,
    now: datetime | None,
    scheduled_hour: int,
    last_pushed_at: datetime | None,
    window_minutes: int = 30,
) -> bool:
    """V5 Step 6 — is a daily-report push due at ``scheduled_hour``?

    Pure helper for the 22:00 daily report: due when the local-hour matches and
    no daily report has been pushed yet today (within ``window_minutes`` of the
    scheduled time). Exposed for unit tests and the daily scheduler.
    """

    if now is None:
        return False
    local = now.astimezone(timezone(timedelta(hours=8))) if now.tzinfo else now
    if local.hour != scheduled_hour:
        return False
    if last_pushed_at is not None:
        last = last_pushed_at.astimezone(timezone(timedelta(hours=8))) if last_pushed_at.tzinfo else last_pushed_at
        if last.date() == local.date():
            return False
    # Within the firing window of the scheduled hour.
    return local.minute < window_minutes or window_minutes <= 0


def settings_snapshot() -> dict[str, Any]:
    return {
        "enabled": throttle_enabled(),
        "cooldown_minutes": cooldown_minutes(),
    }
