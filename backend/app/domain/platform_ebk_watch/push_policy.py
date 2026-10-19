"""美团/飞猪 EBK 定时监控共享推送策略。

设计出处：docs/platform-ebk-watch-anchor-on-ctrip-group-design-2026-08-24.md

- 节流 key 必须带平台前缀：三平台共享 ``ctrip_ebooking_watch.push_throttle``
  的内存字典，裸 group_key 会与携程组（或跨平台同名组）互串人工推送冷却。
- 失败态（error/human_required 等）不推完整盯盘卡，只按冷却窗口发一次异常
  提醒，防止登录失效期间调度器每 60 秒刷一张失败卡（§2 问题⑧，P0）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

PLATFORM_WATCH_PLATFORMS = ("meituan", "fliggy")
DEFAULT_FAILURE_ALERT_COOLDOWN_MINUTES = 60

# throttle_key -> last alert sent at (UTC)
_failure_alert_last_at: dict[str, datetime] = {}


def platform_throttle_key(platform: str, group_key: str) -> str:
    """Build a platform-scoped throttle key.

    携程组 group_key 模式（``^[a-z0-9][a-z0-9_-]{0,63}$``）不含冒号，
    因此 ``platform_ebk:<platform>:<group_key>`` 不可能与任何携程组裸 key
    相同，也不会跨平台撞名。
    """

    normalized_platform = str(platform or "").strip().lower()
    normalized_group = str(group_key or "").strip()
    if not normalized_platform or not normalized_group:
        return ""
    return f"platform_ebk:{normalized_platform}:{normalized_group}"


def reset_failure_alert_state() -> None:
    """Clear in-memory failure-alert state (test helper)."""

    _failure_alert_last_at.clear()


def failure_alert_cooldown_minutes() -> int:
    from app.core.settings import get_settings

    raw = getattr(
        get_settings(),
        "platform_ebk_failure_alert_cooldown_minutes",
        DEFAULT_FAILURE_ALERT_COOLDOWN_MINUTES,
    )
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_FAILURE_ALERT_COOLDOWN_MINUTES
    return minutes if minutes > 0 else DEFAULT_FAILURE_ALERT_COOLDOWN_MINUTES


def should_send_failure_alert(
    throttle_key: str,
    *,
    now: datetime | None = None,
    cooldown_minutes: int | None = None,
) -> tuple[bool, str]:
    """Decide whether a failure alert card may be sent for ``throttle_key``.

    Returns ``(True, "")`` when allowed; ``(False, reason)`` when a previous
    alert was sent within the cooldown window.
    """

    normalized = str(throttle_key or "").strip()
    if not normalized:
        return False, "缺少平台监控组标识，未发送异常提醒。"
    window = cooldown_minutes if cooldown_minutes is not None else failure_alert_cooldown_minutes()
    last_at = _failure_alert_last_at.get(normalized)
    if last_at is None:
        return True, ""
    current = now or datetime.now(tz=timezone.utc)
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    elapsed_minutes = (current - last_at).total_seconds() / 60.0
    if elapsed_minutes < window:
        reason = "距上次异常提醒 {:.0f} 分钟（< {} 分钟冷却），本次不重复提醒。".format(
            elapsed_minutes, window
        )
        return False, reason
    return True, ""


def record_failure_alert(throttle_key: str, *, at: datetime | None = None) -> None:
    normalized = str(throttle_key or "").strip()
    if not normalized:
        return
    _failure_alert_last_at[normalized] = at or datetime.now(tz=timezone.utc)


def failure_alert_state_snapshot() -> dict[str, Any]:
    """Read-only snapshot for tests/diagnostics (no secrets, timestamps only)."""

    return {
        "keys": sorted(_failure_alert_last_at),
        "cooldown_minutes": failure_alert_cooldown_minutes(),
    }
