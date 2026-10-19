from __future__ import annotations

import importlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.domain.ctrip_ebooking_watch import push_throttle


class EbkPushThrottleTests(unittest.TestCase):
    """V5 Step 6 — push throttle decision logic + daily-report due check."""

    def setUp(self) -> None:
        push_throttle.reset_push_throttle_state()

    def tearDown(self) -> None:
        push_throttle.reset_push_throttle_state()

    def test_disabled_by_default_so_scheduled_push_never_throttled(self) -> None:
        push_throttle.record_push("group_a", source="api", run_kind="data_center_watch")
        throttled, reason = push_throttle.should_throttle_scheduled_push(
            "group_a", enabled=False, cooldown=120
        )
        self.assertFalse(throttled)
        self.assertEqual(reason, "")

    def test_manual_push_within_cooldown_throttles_scheduled_push(self) -> None:
        now = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)
        last = now - timedelta(minutes=30)
        throttled, reason = push_throttle.should_throttle_scheduled_push(
            "group_a", now=now, enabled=True, cooldown=120, last_push=(last, "api")
        )
        self.assertTrue(throttled)
        self.assertIn("30", reason)
        self.assertIn("120", reason)

    def test_manual_push_outside_cooldown_does_not_throttle(self) -> None:
        now = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)
        last = now - timedelta(minutes=200)
        throttled, _ = push_throttle.should_throttle_scheduled_push(
            "group_a", now=now, enabled=True, cooldown=120, last_push=(last, "api")
        )
        self.assertFalse(throttled)

    def test_previous_scheduled_push_does_not_throttle_next(self) -> None:
        now = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)
        last = now - timedelta(minutes=10)
        throttled, _ = push_throttle.should_throttle_scheduled_push(
            "group_a", now=now, enabled=True, cooldown=120, last_push=(last, "scheduler")
        )
        self.assertFalse(throttled)

    def test_daily_brief_push_is_not_recorded(self) -> None:
        push_throttle.record_push("group_a", source="api", run_kind="daily_brief")
        self.assertIsNone(push_throttle.get_last_push("group_a"))

    def test_recorded_manual_push_drives_throttle_via_in_memory_state(self) -> None:
        now = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)
        with mock.patch.object(push_throttle, "_utc_now", return_value=now - timedelta(minutes=15)):
            push_throttle.record_push("group_b", source="api", run_kind="data_center_watch")
        throttled, _ = push_throttle.should_throttle_scheduled_push(
            "group_b", now=now, enabled=True, cooldown=120
        )
        self.assertTrue(throttled)

    def test_daily_report_due_only_at_scheduled_hour_once_per_day(self) -> None:
        tz = timezone(timedelta(hours=8))
        due_time = datetime(2026, 8, 12, 22, 5, tzinfo=tz)
        other_hour = datetime(2026, 8, 12, 21, 5, tzinfo=tz)
        already_today = datetime(2026, 8, 12, 22, 10, tzinfo=tz)

        self.assertTrue(push_throttle.daily_report_due(now=due_time, scheduled_hour=22, last_pushed_at=None))
        self.assertFalse(push_throttle.daily_report_due(now=other_hour, scheduled_hour=22, last_pushed_at=None))
        self.assertFalse(
            push_throttle.daily_report_due(
                now=already_today, scheduled_hour=22, last_pushed_at=datetime(2026, 8, 12, 22, 0, tzinfo=tz)
            )
        )

    def test_settings_default_off(self) -> None:
        settings = importlib.import_module("app.core.settings")
        with mock.patch.dict("os.environ", {}, clear=False):
            settings.get_settings.cache_clear()
            snapshot = push_throttle.settings_snapshot()
        self.assertFalse(snapshot["enabled"])
        self.assertEqual(snapshot["cooldown_minutes"], 120)


if __name__ == "__main__":
    unittest.main()
