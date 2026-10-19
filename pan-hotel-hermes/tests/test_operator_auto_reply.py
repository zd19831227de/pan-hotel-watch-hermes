from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from client.operator_reply import finalize_operator_payload
from client.watch_api import WatchApiClient


class OperatorAutoReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_sends_card_when_message_id_present(self) -> None:
        client = WatchApiClient(base_url="http://example.test")
        client.reply_operator_feishu_card = AsyncMock(  # type: ignore[method-assign]
            return_value={"status": "success", "summary": "飞书消息发送成功。"}
        )
        payload = {
            "feishu_card": {"header": {"title": {"content": "测试"}}},
            "operator_summary": {"overall_status": "fresh"},
        }
        result = await finalize_operator_payload(
            client, payload, message_id="om_test", response_mode="detail"
        )
        self.assertTrue(result["feishu_card_sent"])
        client.reply_operator_feishu_card.assert_awaited_once()

    async def test_finalize_skips_send_without_message_id(self) -> None:
        client = WatchApiClient(base_url="http://example.test")
        client.reply_operator_feishu_card = AsyncMock()  # type: ignore[method-assign]
        payload = {"feishu_card": {"header": {"title": {"content": "测试"}}}}
        result = await finalize_operator_payload(client, payload, message_id=None)
        self.assertFalse(result["feishu_card_sent"])
        client.reply_operator_feishu_card.assert_not_called()
    async def test_missing_message_id_note_keeps_operator_reply_text_only(self) -> None:
        client = WatchApiClient(base_url="http://example.test")
        client.reply_operator_feishu_card = AsyncMock()  # type: ignore[method-assign]
        payload = {"feishu_card": {"header": {"title": {"content": "card"}}}}
        # detail mode, but neither chat_id nor message_id is available, so no
        # card can be delivered — the note must steer the model to a text reply.
        result = await finalize_operator_payload(
            client, payload, message_id=None, response_mode="detail"
        )
        self.assertFalse(result["feishu_card_sent"])
        note = str(result.get("operator_reply_note") or "")
        self.assertIn("未自动发送飞书卡片", note)
        self.assertIn("文字", note)
        self.assertNotIn("Triggering message", note)
        self.assertNotIn("om_", note)

    async def test_text_mode_does_not_send_card_or_require_mobile_summary(self) -> None:
        client = WatchApiClient(base_url="http://example.test")
        client.send_operator_feishu_card = AsyncMock()  # type: ignore[method-assign]
        client.reply_operator_feishu_card = AsyncMock()  # type: ignore[method-assign]
        payload = {
            "feishu_card": {"header": {"title": {"content": "测试大卡"}}},
            "operator_summary": {"overall_status": "fresh"},
        }
        result = await finalize_operator_payload(
            client,
            payload,
            chat_id="oc_group",
            response_mode="text",
        )
        self.assertEqual(result.get("response_mode"), "text")
        self.assertEqual(result.get("reply_mode"), "text")
        self.assertFalse(result.get("feishu_card_sent"))
        note = str(result.get("operator_reply_note") or "")
        self.assertNotIn("请基于 mobile_evidence 输出 MOBILE_SUMMARY", note)
        self.assertIn("禁止 MOBILE_SUMMARY", note)
        self.assertTrue("短" in note or "文字" in note)
        client.send_operator_feishu_card.assert_not_called()
        client.reply_operator_feishu_card.assert_not_called()


if __name__ == "__main__":
    unittest.main()
