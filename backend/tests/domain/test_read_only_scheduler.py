from __future__ import annotations

import unittest
from pathlib import Path


class ReadOnlySchedulerTests(unittest.TestCase):
    def test_worker_runs_calendar_and_competitor_ticks_in_parallel(self) -> None:
        source = Path("backend/app/workers/read_only_scheduler.py").read_text(encoding="utf-8")
        self.assertIn("asyncio.gather", source)
        self.assertIn("calendar.tick()", source)
        self.assertIn("competitor.tick()", source)
        self.assertIn("calendar_profile_poll", Path("backend/app/domain/calendar_price_watch/scheduler.py").read_text(encoding="utf-8"))
        self.assertIn('enabled=source in {"scheduler", "matrix_brief"}', Path("backend/app/domain/calendar_price_watch/scheduler.py").read_text(encoding="utf-8"))
        self.assertIn("competitor_profile_poll", Path("backend/app/domain/competitor_prices/scheduler.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
