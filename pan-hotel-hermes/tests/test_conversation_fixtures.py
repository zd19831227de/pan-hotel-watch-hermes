from __future__ import annotations

import unittest

from client.intent_routing import (
    INTENT_TOOL_FIXTURES,
    fixture_by_key,
    is_ebk_review_only_dataset_read,
)


class ConversationFixtureTests(unittest.TestCase):
    def test_fixtures_cover_three_golden_intents(self) -> None:
        keys = {item["key"] for item in INTENT_TOOL_FIXTURES}
        self.assertEqual(
            keys,
            {
                "review_today",
                "occupancy_now_read",
                "promotion_and_pyramid",
                "competitor_trend_3d",
                "competitor_trend_1d_amplitude",
                "competitor_latest_spot",
                "competitor_change_alert",
                "ebk_run_once",
            },
        )

    def test_review_today_routing(self) -> None:
        fixture = fixture_by_key("review_today")
        assert fixture is not None
        self.assertEqual(fixture["required_tool"], "get_ebk_latest_result")
        self.assertIn("run_ebk_watch_once", fixture["forbidden_tools"])
        self.assertIn("read_ebk_datasets", fixture["forbidden_tools"])
        self.assertTrue(is_ebk_review_only_dataset_read(["comment_monitor"]))
        self.assertFalse(is_ebk_review_only_dataset_read(["beneficial_data"]))

    def test_occupancy_now_read_routing(self) -> None:
        fixture = fixture_by_key("occupancy_now_read")
        assert fixture is not None
        self.assertEqual(fixture["required_tool"], "read_ebk_datasets")
        self.assertIn("run_ebk_watch_once", fixture["forbidden_tools"])
        self.assertFalse(is_ebk_review_only_dataset_read(list(fixture["allowed_read_datasets"])))

    def test_competitor_trend_3d_routing(self) -> None:
        fixture = fixture_by_key("competitor_trend_3d")
        assert fixture is not None
        self.assertEqual(fixture["required_tool"], "get_competitor_price_trend")
        self.assertIn("analyze_competitor_price_change", fixture["forbidden_tools"])
        self.assertIn("get_competitor_latest_prices", fixture["forbidden_tools"])
        self.assertEqual(fixture["days"], 3)

    def test_competitor_trend_1d_amplitude_routing(self) -> None:
        fixture = fixture_by_key("competitor_trend_1d_amplitude")
        assert fixture is not None
        self.assertEqual(fixture["required_tool"], "get_competitor_price_trend")
        self.assertEqual(fixture["days"], 1)
        self.assertIn("get_competitor_latest_prices", fixture["forbidden_tools"])
        self.assertIn("analyze_competitor_price_change", fixture["forbidden_tools"])

    def test_competitor_latest_spot_routing(self) -> None:
        fixture = fixture_by_key("competitor_latest_spot")
        assert fixture is not None
        self.assertEqual(fixture["required_tool"], "get_competitor_latest_prices")
        self.assertIn("get_competitor_price_trend", fixture["forbidden_tools"])

    def test_competitor_change_alert_routing(self) -> None:
        fixture = fixture_by_key("competitor_change_alert")
        assert fixture is not None
        self.assertEqual(fixture["required_tool"], "analyze_competitor_price_change")
        self.assertIn("get_competitor_latest_prices", fixture["forbidden_tools"])
        self.assertIn("get_competitor_price_trend", fixture["forbidden_tools"])


if __name__ == "__main__":
    unittest.main()
