from __future__ import annotations

import unittest
from typing import Any

from client.watch_api import WatchApiClient


class OperatorCardReplyPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_operator_card_includes_chat_id_when_present(self) -> None:
        captured: dict[str, Any] = {}

        class CapturingClient(WatchApiClient):
            async def post_json(
                self,
                path: str,
                *,
                body: dict[str, Any] | None = None,
                params: dict[str, Any] | None = None,
                timeout: float | None = None,
            ) -> dict[str, Any]:
                captured["path"] = path
                captured["body"] = body
                captured["params"] = params
                captured["timeout"] = timeout
                return {"status": "success"}

        client = CapturingClient(base_url="http://watch.local")

        await client.reply_operator_feishu_card(
            message_id="om_test",
            chat_id="oc_33333333333333333333333333333333",
            card={"elements": []},
        )

        self.assertEqual(captured["path"], "/api/v1/feishu/operator-card-replies")
        self.assertEqual(
            captured["body"],
            {
                "message_id": "om_test",
                "chat_id": "oc_33333333333333333333333333333333",
                "card": {"elements": []},
            },
        )

    async def test_incomplete_manual_pricing_reply_passes_chat_id(self) -> None:
        calls: list[dict[str, Any]] = []

        class CapturingClient(WatchApiClient):
            async def reply_operator_feishu_card(self, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs)
                return {"status": "success"}

        client = CapturingClient(base_url="http://watch.local")

        await client.create_manual_pricing_approval(
            room_type="",
            date_label="today",
            target_price=420,
            watch_group_key=None,
            chat_id="oc_33333333333333333333333333333333",
            message_id="om_test",
            open_id="ou_test",
        )

        self.assertEqual(calls[0]["message_id"], "om_test")
        self.assertEqual(calls[0]["chat_id"], "oc_33333333333333333333333333333333")


if __name__ == "__main__":
    unittest.main()
