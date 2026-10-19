from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from app.domain.competitor_prices.schedule import competitor_watch_schedule_state


class CompetitorWatchScheduleTests(unittest.TestCase):
    def test_fixed_slots_due_once_per_slot_inside_cross_midnight_window(self) -> None:
        group = SimpleNamespace(
            group_key="ops_watch",
            timezone="Asia/Shanghai",
            schedule_mode="fixed_slots",
            schedule_start_time="06:00",
            schedule_end_time="01:00",
            schedule_interval_minutes=120,
            query_interval_minutes=120,
            last_polled_at=datetime(2026, 6, 17, 6, 5),
        )

        state = competitor_watch_schedule_state(group, now=datetime(2026, 6, 17, 8, 1))

        self.assertTrue(state["in_window"])
        self.assertTrue(state["due"])
        self.assertEqual(state["last_slot_at"], "2026-06-17T08:00:00")
        self.assertEqual(state["next_run_at"], "2026-06-17T10:00:00")

        group.last_polled_at = datetime(2026, 6, 17, 8, 2)
        state = competitor_watch_schedule_state(group, now=datetime(2026, 6, 17, 8, 10))

        self.assertTrue(state["in_window"])
        self.assertFalse(state["due"])
        self.assertEqual(state["next_run_at"], "2026-06-17T10:00:00")

    def test_fixed_slots_outside_window_waits_until_next_window_start(self) -> None:
        group = SimpleNamespace(
            group_key="ops_watch",
            timezone="Asia/Shanghai",
            schedule_mode="fixed_slots",
            schedule_start_time="06:00",
            schedule_end_time="01:00",
            schedule_interval_minutes=120,
            query_interval_minutes=120,
            last_polled_at=datetime(2026, 6, 17, 22, 0),
        )

        state = competitor_watch_schedule_state(group, now=datetime(2026, 6, 17, 2, 0))

        self.assertFalse(state["in_window"])
        self.assertFalse(state["due"])
        self.assertEqual(state["next_run_at"], "2026-06-17T06:00:00")

    def test_last_run_interval_uses_drift_from_last_poll_inside_window(self) -> None:
        group = SimpleNamespace(
            group_key="ops_watch",
            timezone="Asia/Shanghai",
            schedule_mode="last_run_interval",
            schedule_start_time="06:00",
            schedule_end_time="01:00",
            schedule_interval_minutes=90,
            query_interval_minutes=90,
            last_polled_at=datetime(2026, 6, 17, 6, 30),
        )

        not_due = competitor_watch_schedule_state(group, now=datetime(2026, 6, 17, 7, 59))
        due = competitor_watch_schedule_state(group, now=datetime(2026, 6, 17, 8, 0))

        self.assertFalse(not_due["due"])
        self.assertEqual(not_due["next_run_at"], "2026-06-17T08:00:00")
        self.assertTrue(due["due"])


if __name__ == "__main__":
    unittest.main()
