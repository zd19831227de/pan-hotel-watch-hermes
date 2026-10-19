from __future__ import annotations

import unittest

from app.domain.promotion_adjust.commands import (
    classify_promotion_adjust_command,
    is_promotion_adjust_command,
)


class PromotionAdjustCommandTests(unittest.TestCase):
    def test_list_query_variants(self) -> None:
        for text in ("促销列表", "活动促销列表", "查促销列表", "查询活动促销列表", "看看促销列表"):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_promotion_adjust_command(text)["status"], "list_query"
                )

    def test_adjust_with_prefix(self) -> None:
        result = classify_promotion_adjust_command("促销调整 天天特价 立减调到5")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["promotion_name"], "天天特价")
        self.assertEqual(result["target_discount_percent"], 5)

    def test_adjust_full_sentence(self) -> None:
        result = classify_promotion_adjust_command("把促销门店新客立减的立减调到35%")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["promotion_name"], "门店新客立减")
        self.assertEqual(result["target_discount_percent"], 35)

    def test_adjust_without_lijian_keyword(self) -> None:
        result = classify_promotion_adjust_command("活动促销调整 学生专享 调到8")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["promotion_name"], "学生专享")
        self.assertEqual(result["target_discount_percent"], 8)

    def test_adjust_decimal_percent(self) -> None:
        result = classify_promotion_adjust_command("促销调整 天天特价 立减调到5.5")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["target_discount_percent"], 5.5)

    def test_percent_out_of_range(self) -> None:
        result = classify_promotion_adjust_command("促销调整 天天特价 立减调到0")
        self.assertEqual(result["status"], "invalid_percent")
        result = classify_promotion_adjust_command("促销调整 天天特价 立减调到98")
        self.assertEqual(result["status"], "invalid_percent")

    def test_missing_name_needs_confirmation(self) -> None:
        result = classify_promotion_adjust_command("促销调整 立减调到5")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("促销名称", result["summary"])

    def test_missing_percent_needs_confirmation(self) -> None:
        result = classify_promotion_adjust_command("促销调整 天天特价")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("目标立减", result["summary"])

    def test_data_qa_phrases_are_not_commands(self) -> None:
        """「促销效果」「促销报名」等数据中心问答短语不得被指令层截胡。"""
        for text in (
            "促销效果怎么样",
            "促销报名情况",
            "已参加促销有哪些",
            "活动促销有哪些活动",
            "美团促销",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_promotion_adjust_command(text))
                self.assertEqual(
                    classify_promotion_adjust_command(text)["status"],
                    "not_promotion",
                )

    def test_gate_catches_adjust_phrasings(self) -> None:
        for text in (
            "促销调整 天天特价 立减调到5",
            "查一下促销列表",
            "把促销天天特价的立减调到5%",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_promotion_adjust_command(text))


if __name__ == "__main__":
    unittest.main()
