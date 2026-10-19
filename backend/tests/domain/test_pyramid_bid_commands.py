from __future__ import annotations

import unittest

from app.domain.pyramid_bidding.commands import (
    PYRAMID_BID_MAX_CNY,
    classify_pyramid_bid_command,
    is_pyramid_command,
)


class PyramidCommandClassificationTests(unittest.TestCase):
    def test_adjust_with_pyramid_prefix_is_ready(self) -> None:
        result = classify_pyramid_bid_command("金字塔调价 14--16 出价调到6")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["campaign_name"], "14--16")
        self.assertEqual(result["target_bid"], 6.0)

    def test_adjust_with_ba_prefix_is_ready(self) -> None:
        result = classify_pyramid_bid_command("把金字塔14--16的出价调到6元")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["campaign_name"], "14--16")
        self.assertEqual(result["target_bid"], 6.0)

    def test_adjust_decimal_bid_is_ready(self) -> None:
        result = classify_pyramid_bid_command("金字塔 22--24 出价改为5.5块")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["campaign_name"], "22--24")
        self.assertEqual(result["target_bid"], 5.5)

    def test_adjust_smart_campaign_by_name(self) -> None:
        result = classify_pyramid_bid_command("金字塔 智能投放 出价调整为4")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["campaign_name"], "智能投放")
        self.assertEqual(result["target_bid"], 4.0)

    def test_adjust_without_chujia_word_is_ready(self) -> None:
        result = classify_pyramid_bid_command("金字塔 20--22 调到4.2")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["campaign_name"], "20--22")
        self.assertEqual(result["target_bid"], 4.2)

    def test_mention_is_stripped(self) -> None:
        result = classify_pyramid_bid_command("@机器人 金字塔调价 14--16 出价调到6")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["campaign_name"], "14--16")

    def test_query_variants(self) -> None:
        for text in (
            "金字塔出价",
            "查金字塔出价",
            "金字塔出价列表",
            "金字塔推广列表",
            "看一下金字塔推广计划",
        ):
            result = classify_pyramid_bid_command(text)
            self.assertEqual(result["status"], "list_query", text)

    def test_missing_campaign_name_needs_confirmation(self) -> None:
        result = classify_pyramid_bid_command("金字塔出价调到6")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("campaign_name", result["missing_fields"])
        self.assertEqual(result["partial"].get("target_bid"), 6.0)

    def test_missing_bid_needs_confirmation(self) -> None:
        result = classify_pyramid_bid_command("金字塔 14--16 出价调到")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("target_bid", result["missing_fields"])
        self.assertEqual(result["partial"].get("campaign_name"), "14--16")

    def test_bare_jinzita_tiaojia_needs_confirmation(self) -> None:
        result = classify_pyramid_bid_command("金字塔调价")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("campaign_name", result["missing_fields"])
        self.assertIn("target_bid", result["missing_fields"])

    def test_bid_over_cap_is_invalid_bid(self) -> None:
        result = classify_pyramid_bid_command("金字塔 14--16 出价调到500")
        self.assertEqual(result["status"], "invalid_bid")
        self.assertIn(str(int(PYRAMID_BID_MAX_CNY)), result["summary"])

    def test_non_pyramid_texts(self) -> None:
        for text in (
            "今天出租率多少？",
            "金字塔今天投放情况怎么样",
            "把今天高级大床房调到420",
            "综合分析各平台促销效果和广告金字塔",
        ):
            self.assertEqual(classify_pyramid_bid_command(text)["status"], "not_pyramid", text)
            self.assertFalse(is_pyramid_command(text), text)

    def test_is_pyramid_command_matches_forward_gate(self) -> None:
        for text in (
            "金字塔出价",
            "金字塔调价 14--16 出价调到6",
            "金字塔推广列表",
            "金字塔推广计划",
        ):
            self.assertTrue(is_pyramid_command(text), text)


if __name__ == "__main__":
    unittest.main()
