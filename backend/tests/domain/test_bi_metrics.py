"""BI 计算层测试：pickup、同比/预算/forecast 完成率；缺数不补零。"""

from __future__ import annotations

import unittest

from app.domain.bi_daily.metrics import completion_rate, pickup


class PickupTest(unittest.TestCase):
    def test_pickup_is_delta(self):
        result = pickup(current_booked=120, prior_booked=100)
        self.assertEqual(result["value"], 20)
        self.assertEqual(result["status"], "available")

    def test_pickup_missing_either_side_is_unavailable_with_reason(self):
        result = pickup(current_booked=None, prior_booked=100)
        self.assertIsNone(result["value"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "no_on_the_books_source")
        result = pickup(current_booked=100, prior_booked=None)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "no_on_the_books_source")


class CompletionRateTest(unittest.TestCase):
    def test_budget_completion_rate_normal(self):
        result = completion_rate(actual=110.0, baseline=100.0, baseline_kind="budget")
        self.assertAlmostEqual(result["value"], 1.1)
        self.assertEqual(result["status"], "available")
        self.assertIsNone(result["reason"])

    def test_zero_actual_is_real_zero_not_missing(self):
        # 真实的 0 完成率必须如实输出 0，不得伪装缺失。
        result = completion_rate(actual=0.0, baseline=100.0, baseline_kind="budget")
        self.assertEqual(result["value"], 0.0)
        self.assertEqual(result["status"], "available")

    def test_missing_actual_is_unavailable(self):
        result = completion_rate(actual=None, baseline=100.0, baseline_kind="budget")
        self.assertIsNone(result["value"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "actual_missing")

    def test_missing_baseline_is_unavailable_with_kind(self):
        result = completion_rate(actual=100.0, baseline=None, baseline_kind="prior_year")
        self.assertIsNone(result["value"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "prior_year_baseline_missing")

    def test_zero_baseline_is_unavailable_not_zero_division(self):
        result = completion_rate(actual=100.0, baseline=0.0, baseline_kind="forecast")
        self.assertIsNone(result["value"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "forecast_baseline_zero")


if __name__ == "__main__":
    unittest.main()
