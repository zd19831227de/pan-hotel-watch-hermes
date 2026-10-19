"""EBK 全局推送调度纯逻辑单测（设计文档 §7.3/§8.1/§8.3/§8.4）。"""

from __future__ import annotations

import unittest
from datetime import datetime

from app.domain.ebk_global_push.scheduling import (
    FIXED_SLOT_TOLERANCE_MINUTES,
    STALE_RUNNING_MINUTES,
    aggregate_run_status,
    backoff_minutes,
    compute_first_run_at,
    compute_next_run_at,
    fixed_slot_for_now,
    interval_due,
    is_within_window,
    schedule_key_for_fixed_slot,
    schedule_key_for_interval,
    severity_rank,
)


def _config(**overrides):
    base = {
        "id": 7,
        "schedule_mode": "fixed_slots",
        "push_times": ["08:00", "20:00"],
        "interval_minutes": 60,
        "window_start": "06:00",
        "window_end": "23:00",
    }
    base.update(overrides)
    return base


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


class FixedSlotTests(unittest.TestCase):
    def test_slot_due_when_within_tolerance(self):
        slot = fixed_slot_for_now(
            _config(), _at("2026-08-28T08:20:00"), tolerance_minutes=FIXED_SLOT_TOLERANCE_MINUTES
        )
        self.assertIsNotNone(slot)
        self.assertEqual(slot.strftime("%H:%M"), "08:00")

    def test_slot_missed_beyond_tolerance(self):
        slot = fixed_slot_for_now(
            _config(), _at("2026-08-28T08:31:00"), tolerance_minutes=FIXED_SLOT_TOLERANCE_MINUTES
        )
        self.assertIsNone(slot)

    def test_no_slot_before_first_time(self):
        slot = fixed_slot_for_now(
            _config(), _at("2026-08-28T07:59:00"), tolerance_minutes=FIXED_SLOT_TOLERANCE_MINUTES
        )
        self.assertIsNone(slot)

    def test_slot_key_uses_local_date_and_slot(self):
        key = schedule_key_for_fixed_slot(7, _at("2026-08-28T20:05:00"), "20:00")
        self.assertEqual(key, "fixed:7:2026-08-28:20:00")

    def test_midnight_slot_is_same_day_zero(self):
        config = _config(push_times=["00:00"])
        slot = fixed_slot_for_now(
            config, _at("2026-08-28T00:10:00"), tolerance_minutes=FIXED_SLOT_TOLERANCE_MINUTES
        )
        self.assertIsNotNone(slot)
        self.assertEqual(slot.strftime("%H:%M"), "00:00")


class IntervalTests(unittest.TestCase):
    def test_first_run_enabled_inside_window_runs_next_tick(self):
        config = _config(schedule_mode="last_run_interval")
        first = compute_first_run_at(config, _at("2026-08-28T09:00:00"))
        self.assertEqual(first, _at("2026-08-28T09:00:00"))

    def test_first_run_outside_window_defers_to_window_start(self):
        config = _config(schedule_mode="last_run_interval")
        first = compute_first_run_at(config, _at("2026-08-28T03:00:00"))
        self.assertEqual(first, _at("2026-08-28T06:00:00"))

    def test_due_when_now_reaches_next_run_at(self):
        self.assertTrue(
            interval_due(_at("2026-08-28T10:00:00"), _at("2026-08-28T10:00:00"))
        )
        self.assertFalse(
            interval_due(_at("2026-08-28T09:59:00"), _at("2026-08-28T10:00:00"))
        )

    def test_next_after_success_uses_end_plus_interval(self):
        config = _config(schedule_mode="last_run_interval", interval_minutes=60)
        nxt = compute_next_run_at(
            config, finished_at=_at("2026-08-28T10:05:00"), succeeded=True
        )
        self.assertEqual(nxt, _at("2026-08-28T11:05:00"))

    def test_next_after_failure_uses_backoff(self):
        config = _config(schedule_mode="last_run_interval", interval_minutes=60)
        nxt = compute_next_run_at(
            config,
            finished_at=_at("2026-08-28T10:05:00"),
            succeeded=False,
            consecutive_failures=1,
        )
        self.assertEqual(nxt, _at("2026-08-28T10:07:00"))  # 退避 2 分钟

    def test_interval_key_buckets_by_minute(self):
        key = schedule_key_for_interval(7, _at("2026-08-28T10:05:33"))
        self.assertEqual(key, "interval:7:2026-08-28T10:05")


class WindowTests(unittest.TestCase):
    def test_window_crossing_midnight(self):
        self.assertTrue(is_within_window(_at("2026-08-28T23:30:00"), "23:00", "06:00"))
        self.assertTrue(is_within_window(_at("2026-08-28T02:00:00"), "23:00", "06:00"))
        self.assertFalse(is_within_window(_at("2026-08-28T12:00:00"), "23:00", "06:00"))

    def test_window_same_day(self):
        self.assertTrue(is_within_window(_at("2026-08-28T10:00:00"), "06:00", "23:00"))
        self.assertFalse(is_within_window(_at("2026-08-28T05:00:00"), "06:00", "23:00"))


class BackoffTests(unittest.TestCase):
    def test_backoff_ladder_capped_by_interval(self):
        config = _config(interval_minutes=3)
        self.assertEqual(backoff_minutes(1, interval_minutes=60), 1)
        self.assertEqual(backoff_minutes(2, interval_minutes=60), 2)
        self.assertEqual(backoff_minutes(3, interval_minutes=60), 5)
        self.assertEqual(backoff_minutes(4, interval_minutes=60), 10)
        self.assertEqual(backoff_minutes(9, interval_minutes=60), 10)
        # 上限不超过一个配置间隔
        self.assertEqual(backoff_minutes(4, interval_minutes=3), 3)


class AggregationTests(unittest.TestCase):
    def test_severity_order(self):
        self.assertGreater(severity_rank("human_required"), severity_rank("failed"))
        self.assertGreater(severity_rank("failed"), severity_rank("partial"))
        self.assertGreater(severity_rank("partial"), severity_rank("needs_review"))
        self.assertGreater(severity_rank("needs_review"), severity_rank("config_required"))
        self.assertGreater(severity_rank("config_required"), severity_rank("success"))

    def test_aggregate_takes_heaviest_platform_result(self):
        self.assertEqual(
            aggregate_run_status(
                platform_statuses=["success", "failed", "config_required"],
                all_cards_delivered=True,
            ),
            "failed",
        )

    def test_aggregate_partial_when_some_cards_failed(self):
        self.assertEqual(
            aggregate_run_status(
                platform_statuses=["success", "success", "success"],
                all_cards_delivered=False,
            ),
            "partial",
        )

    def test_aggregate_config_required_only_when_rest_success(self):
        self.assertEqual(
            aggregate_run_status(
                platform_statuses=["success", "config_required"],
                all_cards_delivered=True,
            ),
            "config_required",
        )
        self.assertEqual(
            aggregate_run_status(
                platform_statuses=["needs_review", "config_required"],
                all_cards_delivered=True,
            ),
            "needs_review",
        )

    def test_aggregate_all_success(self):
        self.assertEqual(
            aggregate_run_status(
                platform_statuses=["success", "success"],
                all_cards_delivered=True,
            ),
            "success",
        )

    def test_stale_running_threshold_constant(self):
        self.assertEqual(STALE_RUNNING_MINUTES, 45)


if __name__ == "__main__":
    unittest.main()
