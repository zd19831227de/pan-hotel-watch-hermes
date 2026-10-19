from __future__ import annotations

import unittest

from client.watch_api import build_hotel_overview_sections, enrich_hotel_overview_response


class HotelOverviewTests(unittest.TestCase):
    def test_build_overview_sections_merges_ebk_competitor_and_health(self) -> None:
        payload = build_hotel_overview_sections(
            health={
                "checked_at": "2026-06-25T10:00:00+00:00",
                "operator_summary": {
                    "overall_status": "fresh",
                    "attention_modules": [],
                },
            },
            ebk={
                "operator_summary": {
                    "review_summary": {
                        "available": True,
                        "positive_reviews_yesterday": "6",
                        "negative_reviews_yesterday": "0",
                    },
                    "beneficial_metrics": [{"field": "出租率", "value": "82%"}],
                    "latest_captured_at": "2026-06-25T09:00:00+00:00",
                }
            },
            competitor={
                "operator_summary": {
                    "price_rows": [{"field": "竞品A · today", "value": "大床 / 无早 / ¥399"}],
                    "latest_captured_at": "2026-06-25T09:30:00+00:00",
                }
            },
            price_change={
                "operator_summary": {
                    "alert_status": "no_trigger",
                    "summary": "暂无需要关注的价格变化。",
                }
            },
        )
        rows = payload["operator_summary"]["overview_rows"]
        fields = {row["field"] for row in rows}
        self.assertIn("昨日好评", fields)
        self.assertIn("出租率", fields)
        self.assertIn("竞品A · today", fields)
        self.assertIn("价格变化", fields)

    def test_enrich_hotel_overview_attaches_card(self) -> None:
        payload = enrich_hotel_overview_response(
            {
                "checked_at": "2026-06-25T10:00:00+00:00",
                "operator_summary": {
                    "overall_status": "fresh",
                    "overview_rows": [{"field": "盯盘状态", "value": "fresh"}],
                    "next_actions": ["当前数据正常。"],
                },
            }
        )
        self.assertEqual(payload["feishu_card_kind"], "hotel_overview")
        self.assertEqual(payload["feishu_card"]["header"]["title"]["content"], "酒店今日概览")


if __name__ == "__main__":
    unittest.main()
