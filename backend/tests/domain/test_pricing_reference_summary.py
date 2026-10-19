from __future__ import annotations

import asyncio
import unittest
from typing import Any


class PricingReferenceSummaryTests(unittest.TestCase):
    def test_ai_summary_returns_required_reference_fields(self) -> None:
        from app.domain.competitor_prices.pricing_reference_summary import (
            build_pricing_reference_summary,
        )

        class FakeAiClient:
            async def summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
                return {
                    "summary_cn": "Multiple competitors dropped; manager should confirm.",
                    "suggested_price_yuan": 228,
                    "suggested_price_range": {"min": 228, "max": 238},
                    "risk_flags": ["bait_room_type"],
                    "evidence_lines": ["today no breakfast: competitor 225 -> 207, ours 238"],
                    "confidence_label": "medium",
                }

        result = asyncio.run(
            build_pricing_reference_summary(_sample_alert(), ai_client=FakeAiClient())
        )

        self.assertEqual(result["summary_cn"], "Multiple competitors dropped; manager should confirm.")
        self.assertEqual(result["suggested_price_yuan"], 228)
        self.assertEqual(result["suggested_price_range"], {"min": 228, "max": 238})
        self.assertEqual(result["risk_flags"], ["bait_room_type"])
        self.assertEqual(result["evidence_lines"], ["today no breakfast: competitor 225 -> 207, ours 238"])
        self.assertEqual(result["confidence_label"], "medium")

    def test_ai_failure_falls_back_to_rule_summary_without_raw_error(self) -> None:
        from app.domain.competitor_prices.pricing_reference_summary import (
            build_pricing_reference_summary,
        )

        class FailingAiClient:
            async def summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("api key secret stack trace")

        result = asyncio.run(
            build_pricing_reference_summary(_sample_alert(), ai_client=FailingAiClient())
        )

        self.assertIn("competitor", result["summary_cn"].lower())
        self.assertEqual(result["suggested_price_yuan"], 228)
        self.assertEqual(result["suggested_price_range"], {"min": 228, "max": 238})
        self.assertIn("ai_fallback", result["risk_flags"])
        self.assertTrue(result["evidence_lines"])
        self.assertEqual(result["confidence_label"], "rule_fallback")
        self.assertNotIn("secret", str(result).lower())
        self.assertNotIn("stack", str(result).lower())


def _sample_alert() -> dict[str, Any]:
    return {
        "status": "suggestion_ready",
        "suggestions": [
            {
                "hotel_name": "?? A",
                "date_label": "today",
                "room_type": "Deluxe King",
                "breakfast_kind": "none",
                "current_own_price_yuan": 238,
                "competitor_previous_price_yuan": 225,
                "competitor_current_price_yuan": 207,
                "recommended_price_yuan": 228,
                "trigger_reason": "competitor dropped",
            },
            {
                "hotel_name": "?? B",
                "date_label": "tomorrow",
                "room_type": "Deluxe King",
                "breakfast_kind": "included",
                "current_own_price_yuan": 257,
                "competitor_previous_price_yuan": 261,
                "competitor_current_price_yuan": 240,
                "recommended_price_yuan": 238,
                "trigger_reason": "competitor dropped",
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
