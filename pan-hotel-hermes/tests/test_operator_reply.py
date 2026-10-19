from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from client.operator_reply import finalize_operator_payload


class OperatorReplyDeliveryTests(unittest.TestCase):
    def test_prefers_chat_id_app_bot_send(self) -> None:
        client = mock.Mock()
        client.send_operator_feishu_card = mock.AsyncMock(
            return_value={"status": "success", "delivery": "app_bot"}
        )
        client.reply_operator_feishu_card = mock.AsyncMock()
        payload = {
            "feishu_card": {"header": {}, "elements": []},
            "operator_summary": {"intro": "上周数据摘要"},
        }

        result = asyncio.run(
            finalize_operator_payload(
                client,
                payload,
                chat_id="oc_group",
                message_id="om_msg",
                store_hint="静里",
                response_mode="detail",
            )
        )
        client.send_operator_feishu_card.assert_awaited_once()
        client.reply_operator_feishu_card.assert_not_called()
        self.assertTrue(result["feishu_card_sent"])
        self.assertEqual(result["feishu_card_delivery"], "app_bot")

    def test_chat_card_send_error_preserves_operator_payload(self) -> None:
        client = mock.Mock()
        client.send_operator_feishu_card = mock.AsyncMock(
            side_effect=RuntimeError("HTTP 500")
        )
        payload = {
            "status": "success",
            "summary": "weekly briefing data",
            "watch_group_key": "ctrip_ebooking_data_center",
            "feishu_card": {"header": {}, "elements": []},
        }

        result = asyncio.run(
            finalize_operator_payload(
                client,
                payload,
                chat_id="oc_group",
                store_hint="jingli",
                response_mode="detail",
            )
        )

        client.send_operator_feishu_card.assert_awaited_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["watch_group_key"], "ctrip_ebooking_data_center")
        self.assertFalse(result["feishu_card_sent"])
        self.assertEqual(result["feishu_card_reply"]["status"], "failed")
        self.assertIn("HTTP 500", result["feishu_card_reply"]["summary"])
        self.assertIn("card", result["operator_reply_note"].lower())
        self.assertIn("original payload", result["operator_reply_note"].lower())

    def test_reply_card_send_error_preserves_operator_payload(self) -> None:
        client = mock.Mock()
        client.reply_operator_feishu_card = mock.AsyncMock(
            side_effect=RuntimeError("HTTP 500")
        )
        payload = {
            "status": "success",
            "summary": "weekly briefing data",
            "watch_group_key": "ctrip_ebooking_data_center",
            "feishu_card": {"header": {}, "elements": []},
        }

        result = asyncio.run(
            finalize_operator_payload(
                client,
                payload,
                message_id="om_msg",
                response_mode="detail",
            )
        )

        client.reply_operator_feishu_card.assert_awaited_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["watch_group_key"], "ctrip_ebooking_data_center")
        self.assertFalse(result["feishu_card_sent"])
        self.assertEqual(result["feishu_card_reply"]["status"], "failed")
        self.assertIn("HTTP 500", result["feishu_card_reply"]["summary"])
        self.assertIn("card", result["operator_reply_note"].lower())

    def test_failed_chat_card_send_does_not_block_retry_as_duplicate(self) -> None:
        client = mock.Mock()
        client.send_operator_feishu_card = mock.AsyncMock(
            side_effect=[
                RuntimeError("HTTP 500"),
                {"status": "success", "delivery": "app_bot"},
            ]
        )
        payload = {
            "status": "success",
            "summary": "latest ebk data",
            "source": "browser_run_now",
            "feishu_card_kind": "ebk_latest",
            "operator_summary": {"latest_captured_at": "2026-07-06T10:00:00+08:00"},
            "feishu_card": {"header": {}, "elements": []},
        }

        first = asyncio.run(
            finalize_operator_payload(
                client, payload, chat_id="oc_group", response_mode="detail"
            )
        )
        second = asyncio.run(
            finalize_operator_payload(
                client, payload, chat_id="oc_group", response_mode="detail"
            )
        )

        self.assertFalse(first["feishu_card_sent"])
        self.assertTrue(second["feishu_card_sent"])
        self.assertNotIn("feishu_card_skipped_duplicate", second)
        self.assertEqual(client.send_operator_feishu_card.await_count, 2)

    def test_summary_mode_is_compatible_alias_for_text(self) -> None:
        client = mock.Mock()
        client.send_operator_feishu_card = mock.AsyncMock(
            return_value={"status": "success", "delivery": "app_bot"}
        )
        payload = {
            "feishu_card": {"header": {"title": {"content": "酒店今日概览"}}, "elements": []},
            "feishu_card_kind": "hotel_overview",
            "operator_summary": {"intro": "概览"},
            "mobile_evidence": [
                {
                    "evidence_ref": "ev_occ",
                    "label": "出租率",
                    "value": "82",
                    "unit": "%",
                }
            ],
            "response_mode": "summary",
        }

        result = asyncio.run(
            finalize_operator_payload(
                client,
                payload,
                chat_id="oc_test",
                response_mode="summary",
            )
        )

        self.assertNotEqual(result.get("feishu_card_sent"), True)
        self.assertEqual(result.get("response_mode"), "text")
        self.assertEqual(result.get("reply_mode"), "text")
        self.assertEqual(client.send_operator_feishu_card.await_count, 0)
        self.assertTrue(result.get("mobile_evidence"))

    def test_text_mode_forces_operator_text_only_and_strips_card(self) -> None:
        client = mock.Mock()
        client.send_operator_feishu_card = mock.AsyncMock()
        payload = {
            "feishu_card": {"header": {}, "elements": []},
            "feishu_card_kind": "ebk_dataset_read",
            "operator_summary": {
                "intro": "出租率 72%",
                "reply_mode": "card_first",
            },
            "response_mode": "text",
        }

        result = asyncio.run(
            finalize_operator_payload(
                client,
                payload,
                chat_id="oc_test",
                response_mode="text",
            )
        )

        self.assertEqual(result.get("response_mode"), "text")
        self.assertFalse(result.get("feishu_card_sent"))
        self.assertNotIn("feishu_card", result)
        self.assertNotIn("feishu_card_kind", result)
        self.assertEqual(result["operator_summary"].get("reply_mode"), "text_only")
        self.assertEqual(client.send_operator_feishu_card.await_count, 0)

    def test_detail_mode_still_sends_card(self) -> None:
        client = mock.Mock()
        client.send_operator_feishu_card = mock.AsyncMock(
            return_value={"status": "success", "delivery": "app_bot"}
        )
        payload = {
            "feishu_card": {"header": {"title": {"content": "酒店今日概览"}}, "elements": []},
            "feishu_card_kind": "hotel_overview",
            "operator_summary": {"intro": "概览"},
            "response_mode": "detail",
        }

        result = asyncio.run(
            finalize_operator_payload(
                client,
                payload,
                chat_id="oc_test",
                response_mode="detail",
            )
        )

        self.assertTrue(result["feishu_card_sent"])
        self.assertEqual(client.send_operator_feishu_card.await_count, 1)


if __name__ == "__main__":
    unittest.main()
