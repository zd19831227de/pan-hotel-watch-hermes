from __future__ import annotations

import unittest

from client.intent_routing import enrich_ebk_run_response


class EnrichEbkRunResponseTests(unittest.TestCase):
    def test_skipped_no_datasets_explains_config_not_message_id(self) -> None:
        payload = enrich_ebk_run_response(
            {
                "status": "skipped",
                "summary": "没有启用的 EBK 数据集。",
                "watch_group_key": "ctrip_ebooking_data_center",
            }
        )
        operator = payload.get("operator_summary") or {}
        self.assertIn("不是 message_id", operator.get("usage_note") or "")
        self.assertIn("operator_reply_note", payload)
        self.assertIn("message_id", payload["operator_reply_note"])

    def test_success_merges_latest_snapshot(self) -> None:
        payload = enrich_ebk_run_response(
            {
                "status": "success",
                "summary": "EBK 读取成功",
                "watch_group_key": "ctrip_ebooking_data_center",
            },
            latest_snapshot={
                "watch_group_key": "ctrip_ebooking_data_center",
                "run": {"status": "success", "captured_at": "2026-06-29T10:00:00+00:00"},
                "dataset_results": [],
            },
        )
        self.assertTrue(payload.get("run_triggered"))
        self.assertNotIn("feishu_card", payload)
        self.assertIn("operator_reply_note", payload)
        self.assertIn("未自动发送飞书卡片", payload["operator_reply_note"])
        self.assertNotIn("需要 message_id", payload["operator_reply_note"])

    def test_success_run_does_not_attach_card_even_with_metrics(self) -> None:
        payload = enrich_ebk_run_response(
            {
                "status": "success",
                "summary": "EBK 读取成功",
                "watch_group_key": "ctrip_ebooking_data_center",
            },
            latest_snapshot={
                "watch_group_key": "ctrip_ebooking_data_center",
                "run": {"status": "success", "captured_at": "2026-06-30T09:59:00+00:00"},
                "dataset_results": [
                    {
                        "dataset_id": "beneficial_data",
                        "status": "success",
                        "data": {
                            "metrics": {
                                "occupancy_rate": {
                                    "label": "出租率",
                                    "value": "48%",
                                    "display_value": "48%",
                                }
                            }
                        },
                    },
                    {
                        "dataset_id": "comment_monitor",
                        "status": "success",
                        "data": {
                            "comment_monitor": {
                                "review_overview": {
                                    "positive_reviews_yesterday": "12",
                                    "negative_reviews_yesterday": "1",
                                }
                            }
                        },
                    },
                ],
            },
        )
        self.assertNotIn("feishu_card", payload)
        self.assertIn("beneficial_metrics", payload.get("operator_summary") or {})


if __name__ == "__main__":
    unittest.main()
