from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.ops.worker_heartbeat import get_worker_heartbeat_status, write_worker_heartbeat


class SchedulerStatusTests(unittest.TestCase):
    def test_worker_heartbeat_status_reports_healthy_without_sensitive_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            heartbeat_path = Path(directory) / "scheduler.json"
            now = datetime(2026, 6, 18, 8, 5, tzinfo=UTC)

            write_worker_heartbeat(
                "competitor_price_scheduler",
                "tick_finished",
                tick_summary={
                    "last_due_group_count": 1,
                    "last_run_ids": [12],
                    "webhook_url": "https://open.feishu.cn/secret",
                    "profile_path": "C:/secret/profile",
                },
                path=heartbeat_path,
                now=now,
            )

            status = get_worker_heartbeat_status(
                "competitor_price_scheduler",
                path=heartbeat_path,
                now=now + timedelta(seconds=30),
            )

        self.assertEqual(status["status"], "healthy")
        self.assertTrue(status["process_confirmed"])
        self.assertEqual(status["heartbeat_age_seconds"], 30)
        self.assertEqual(status["last_tick_summary"]["last_due_group_count"], 1)
        self.assertEqual(status["last_tick_summary"]["last_run_ids"], [12])
        self.assertNotIn("webhook_url", status["last_tick_summary"])
        self.assertNotIn("profile_path", status["last_tick_summary"])

    def test_worker_heartbeat_status_reports_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            heartbeat_path = Path(directory) / "scheduler.json"
            now = datetime(2026, 6, 18, 8, 5, tzinfo=UTC)

            write_worker_heartbeat("ctrip_ebooking_data_center_scheduler", "tick_finished", path=heartbeat_path, now=now)

            status = get_worker_heartbeat_status(
                "ctrip_ebooking_data_center_scheduler",
                path=heartbeat_path,
                now=now + timedelta(seconds=360),
                stale_after_seconds=300,
            )

        self.assertEqual(status["status"], "stale")
        self.assertFalse(status["process_confirmed"])


if __name__ == "__main__":
    unittest.main()
