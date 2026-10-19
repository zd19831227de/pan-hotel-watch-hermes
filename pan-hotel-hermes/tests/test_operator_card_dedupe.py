from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from client import operator_reply
from client.operator_reply import finalize_operator_payload
from client.watch_api import WatchApiClient


class OperatorCardDedupeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        operator_reply._RECENT_CARD_PUSHES.clear()
        operator_reply._RECENT_EBK_CAPTURES.clear()

    async def test_skips_duplicate_ebk_cards_for_same_captured_at(self) -> None:
        client = WatchApiClient(base_url="http://example.test")
        client.send_operator_feishu_card = AsyncMock(  # type: ignore[method-assign]
            return_value={"status": "success", "summary": "ok"}
        )
        payload = {
            "feishu_card": {"header": {"title": {"content": "经营数据"}}},
            "feishu_card_kind": "beneficial_data",
            "operator_summary": {"latest_captured_at": "2026-06-30T09:59:00+00:00"},
            "source": "browser_run_now",
        }
        first = await finalize_operator_payload(
            client, payload, chat_id="oc_test", response_mode="detail"
        )
        second_payload = {
            **payload,
            "feishu_card_kind": "ebk_latest",
            "feishu_card": {"header": {"title": {"content": "点评概览"}}},
        }
        second = await finalize_operator_payload(
            client, second_payload, chat_id="oc_test", response_mode="detail"
        )

        self.assertTrue(first["feishu_card_sent"])
        self.assertTrue(second.get("feishu_card_skipped_duplicate"))
        self.assertEqual(client.send_operator_feishu_card.await_count, 1)


if __name__ == "__main__":
    unittest.main()
