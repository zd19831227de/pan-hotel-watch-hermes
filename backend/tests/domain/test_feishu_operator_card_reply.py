from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.tests._service_auth import service_token_headers
from app.main import app


class FeishuOperatorCardReplyTests(unittest.TestCase):
    def test_reply_operator_card_instantiates_client_with_chat_id(self) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "Test"}},
            "elements": [],
        }

        class FakeClient:
            created_chat_ids: list[str | None] = []

            def __init__(
                self,
                *,
                chat_id: str | None = None,
                app_profile: str | None = None,
            ) -> None:
                _ = app_profile
                self.created_chat_ids.append(chat_id)

            async def reply_interactive_card(
                self,
                message_id: str,
                payload: dict,
                *,
                fallback_text: str | None = None,
            ) -> dict:
                _ = message_id, payload, fallback_text
                return {"status": "success", "summary": "sent"}

        with mock.patch("app.api.v1.feishu.FeishuBotClient", FakeClient):
            client = TestClient(app)
            response = client.post(
                "/api/v1/feishu/operator-card-replies",
                headers=service_token_headers(),
                json={
                    "message_id": "om_test",
                    "chat_id": "oc_33333333333333333333333333333333",
                    "card": card,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeClient.created_chat_ids, ["oc_33333333333333333333333333333333"])

    def test_reply_operator_card_uses_feishu_client(self) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "Test"}},
            "elements": [],
        }

        async def _reply(message_id: str, payload: dict, *, fallback_text: str | None = None) -> dict:
            self.assertEqual(message_id, "om_test")
            self.assertEqual(payload, card)
            return {"status": "success", "summary": "sent"}

        with mock.patch(
            "app.api.v1.feishu.FeishuBotClient.reply_interactive_card",
            side_effect=_reply,
        ):
            client = TestClient(app)
            response = client.post(
                "/api/v1/feishu/operator-card-replies",
                headers=service_token_headers(),
                json={"message_id": "om_test", "card": card},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")


if __name__ == "__main__":
    unittest.main()
