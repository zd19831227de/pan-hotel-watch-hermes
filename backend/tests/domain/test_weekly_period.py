from __future__ import annotations

import unittest
from datetime import date

from app.domain.ops.weekly_period import previous_week_bounds, resolve_previous_calendar_week


class WeeklyPeriodTests(unittest.TestCase):
    def test_previous_calendar_week_from_friday(self) -> None:
        period = resolve_previous_calendar_week(reference_date=date(2026, 6, 26))
        self.assertEqual(period["start_date"], "2026-06-15")
        self.assertEqual(period["end_date"], "2026-06-21")

    def test_previous_week_bounds_are_inclusive(self) -> None:
        start_at, end_at = previous_week_bounds(reference_date=date(2026, 6, 26))
        self.assertEqual(start_at.isoformat(sep=" "), "2026-06-15 00:00:00")
        self.assertEqual(end_at.isoformat(sep=" "), "2026-06-21 23:59:59")


if __name__ == "__main__":
    unittest.main()
