from __future__ import annotations

import unittest

from client.operator_summary_contract import (
    CONTRACT_VERSION,
    normalize_operator_summary,
)


class OperatorSummaryContractTests(unittest.TestCase):
    def test_adds_contract_version_and_preserves_existing_rows(self) -> None:
        payload = {
            "status": "success",
            "operator_summary": {
                "intro": "EBK 数据中心现读完成，出租率 82%。",
                "overview_rows": [
                    {"field": "出租率", "value": "82%"},
                    {"field": "ADR", "value": "420元"},
                ],
            },
        }

        result = normalize_operator_summary(payload, tool_name="read_ebk_datasets")
        operator = result["operator_summary"]

        self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
        self.assertEqual(operator["module"], "ebk")
        self.assertEqual(operator["intent"], "immediate_read")
        self.assertEqual(operator["conclusion"], "EBK 数据中心现读完成，出租率 82%。")
        self.assertEqual(operator["evidence"][0], {"label": "出租率", "value": "82%", "kind": "metric"})
        self.assertEqual(operator["overview_rows"][1]["field"], "ADR")

    def test_missing_freshness_is_unknown_not_invented(self) -> None:
        result = normalize_operator_summary(
            {"status": "success", "operator_summary": {"intro": "已读取竞品价格。"}},
            tool_name="get_competitor_latest_prices",
        )

        freshness = result["operator_summary"]["freshness"]
        self.assertEqual(freshness["state"], "unknown")
        self.assertIsNone(freshness["age_minutes"])
        self.assertIn("未提供明确读取时间", freshness["note"])

    def test_calendar_latest_is_read_only(self) -> None:
        result = normalize_operator_summary(
            {
                "status": "success",
                "operator_summary": {
                    "intro": "已读取未来15天日历价。",
                    "calendar_rows": [{"field": "2026-07-09", "value": "本店 411元，竞品 398元"}],
                },
            },
            tool_name="get_calendar_price_latest",
        )

        safety = result["operator_summary"]["safety_boundary"]
        self.assertEqual(result["operator_summary"]["module"], "calendar_price")
        self.assertEqual(safety["level"], "read_only")
        self.assertIn("ota_write", safety["forbidden"])
        self.assertIn("pricing_execute", safety["forbidden"])

    def test_competitor_rise_is_alert_only(self) -> None:
        result = normalize_operator_summary(
            {
                "status": "alert",
                "operator_summary": {
                    "intro": "竞品价格上涨，仅提醒。",
                    "alert_direction": "increase",
                },
            },
            tool_name="analyze_competitor_price_change",
        )

        operator = result["operator_summary"]
        self.assertEqual(operator["module"], "pricing")
        self.assertEqual(operator["risk"]["level"], "attention")
        self.assertIn("竞品涨价仅提醒", operator["risk"]["flags"])
        self.assertEqual(operator["safety_boundary"]["level"], "read_only")

    def test_manual_pricing_is_approval_only(self) -> None:
        result = normalize_operator_summary(
            {
                "status": "success",
                "summary": "已创建手动调价审批卡。",
                "operator_summary": {"intro": "已创建手动调价审批卡。"},
            },
            tool_name="create_manual_pricing_approval",
        )

        safety = result["operator_summary"]["safety_boundary"]
        self.assertEqual(result["operator_summary"]["module"], "pricing")
        self.assertEqual(result["operator_summary"]["intent"], "approval_request")
        self.assertEqual(safety["level"], "approval_only")
        self.assertIn("直接 OTA 写价", safety["summary"])

    def test_sensitive_values_are_not_promoted_to_operator_fields(self) -> None:
        payload = {
            "status": "failed",
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/secret",
            "profile_path": "C:\\Users\\Administrator\\AppData\\Local\\SecretProfile",
            "operator_summary": {
                "intro": "读取失败，请稍后重试。",
                "overview_rows": [
                    {"field": "webhook", "value": "https://open.feishu.cn/open-apis/bot/v2/hook/secret"},
                    {"field": "状态", "value": "失败"},
                ],
            },
        }

        result = normalize_operator_summary(payload, tool_name="get_watch_scheduler_status")
        serialized_operator = str(result["operator_summary"])

        self.assertNotIn("open-apis/bot", serialized_operator)
        self.assertNotIn("AppData", serialized_operator)
        self.assertIn("读取失败", result["operator_summary"]["conclusion"])
        self.assertEqual(result["operator_summary"]["evidence"][0]["label"], "状态")

    def test_text_response_mode_forces_text_only_despite_embedded_card(self) -> None:
        result = normalize_operator_summary(
            {
                "status": "success",
                "response_mode": "text",
                "feishu_card_sent": False,
                "feishu_card": {"header": {}, "elements": []},
                "operator_summary": {
                    "intro": "出租率 72%",
                    "reply_mode": "card_first",
                },
            },
            tool_name="read_ebk_datasets",
        )
        operator = result["operator_summary"]
        self.assertEqual(operator["reply_mode"], "text_only")
        self.assertEqual(operator.get("response_mode"), "text")


if __name__ == "__main__":
    unittest.main()
