"""去年同期同星期映射算法测试：D-364 优先 / D-371 跨年兜底。"""

from __future__ import annotations

import unittest
from datetime import date

from app.domain.bi_daily.weekday_mapping import (
    build_weekday_mappings_for_range,
    map_prior_year_same_weekday,
)


class WeekdayMappingTest(unittest.TestCase):
    def test_regular_date_uses_minus_364_same_weekday(self):
        # 2026-10-10(周六) - 364天 = 2025-10-11，同为周六且在去年。
        spec = map_prior_year_same_weekday(date(2026, 10, 10))
        self.assertEqual(spec.prior_year_date, date(2025, 10, 11))
        self.assertEqual(spec.matching_rule, "minus_364")
        self.assertIsNone(spec.fallback_reason)
        self.assertEqual(spec.prior_year_date.isoweekday(), date(2026, 10, 10).isoweekday())

    def test_january_first_uses_minus_364(self):
        # 2027-01-01 - 364天 = 2026-01-02，仍在去年，取 minus_364。
        spec = map_prior_year_same_weekday(date(2027, 1, 1))
        self.assertEqual(spec.prior_year_date, date(2026, 1, 2))
        self.assertEqual(spec.matching_rule, "minus_364")

    def test_december_tail_falls_back_to_minus_371(self):
        # 2026-12-31 - 364天 = 2026-01-01（仍在今年，不可用）→ 取 -371天。
        spec = map_prior_year_same_weekday(date(2026, 12, 31))
        self.assertEqual(spec.prior_year_date, date(2025, 12, 25))
        self.assertEqual(spec.matching_rule, "minus_371")
        self.assertEqual(spec.prior_year_date.isoweekday(), date(2026, 12, 31).isoweekday())

    def test_leap_day_maps_to_prior_year_weekday(self):
        # 2028-02-29(闰) - 364天 = 2027-03-02，同星期且在去年（闰年差一天属正常）。
        spec = map_prior_year_same_weekday(date(2028, 2, 29))
        self.assertEqual(spec.prior_year_date, date(2027, 3, 2))
        self.assertEqual(spec.matching_rule, "minus_364")
        self.assertEqual(spec.prior_year_date.isoweekday(), date(2028, 2, 29).isoweekday())

    def test_never_uses_same_month_day(self):
        # 去年同月同日（2025-10-10 周五）绝不能作为 2026-10-10(周六) 的映射。
        spec = map_prior_year_same_weekday(date(2026, 10, 10))
        self.assertNotEqual(spec.prior_year_date, date(2025, 10, 10))

    def test_range_build_covers_every_date_once(self):
        specs = build_weekday_mappings_for_range(date(2026, 10, 1), date(2026, 10, 14))
        self.assertEqual(len(specs), 14)
        self.assertEqual([s.current_date for s in specs][0], date(2026, 10, 1))
        # 每个映射都满足：去年、同 ISO 星期。
        for spec in specs:
            self.assertEqual(spec.current_weekday, spec.current_date.isoweekday())
            self.assertEqual(spec.prior_year_date.year, 2025)
            self.assertEqual(
                spec.prior_year_date.isoweekday(), spec.current_date.isoweekday()
            )


if __name__ == "__main__":
    unittest.main()
