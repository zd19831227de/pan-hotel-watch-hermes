from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.tests._service_auth import service_token_headers
from app.main import app


class FeishuOperatorCardSendTests(unittest.TestCase):
    def test_send_operator_card_instantiates_client_with_chat_id(self) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"template": "green", "title": {"tag": "plain_text", "content": "Brief"}},
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

            async def send_interactive_card(
                self,
                chat_id: str,
                payload: dict,
                *,
                fallback_text: str | None = None,
            ) -> dict:
                _ = chat_id, payload, fallback_text
                return {"status": "success", "summary": "ok", "delivery": "app_bot"}

        with mock.patch("app.domain.notifications.operator_feishu_card.FeishuBotClient", FakeClient):
            client = TestClient(app)
            response = client.post(
                "/api/v1/feishu/operator-card-sends",
                headers=service_token_headers(),
                json={"chat_id": "oc_33333333333333333333333333333333", "card": card},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeClient.created_chat_ids, ["oc_33333333333333333333333333333333"])

    def test_send_operator_card_uses_app_bot_delivery(self) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"template": "green", "title": {"tag": "plain_text", "content": "Brief"}},
            "elements": [],
        }

        async def _deliver(**kwargs) -> dict:
            self.assertEqual(kwargs["chat_id"], "oc_test")
            self.assertEqual(kwargs["card"], card)
            return {"status": "success", "summary": "ok", "delivery": "app_bot"}

        with mock.patch(
            "app.api.v1.feishu.deliver_operator_briefing_card",
            side_effect=_deliver,
        ):
            client = TestClient(app)
            response = client.post(
                "/api/v1/feishu/operator-card-sends",
                headers=service_token_headers(),
                json={"chat_id": "oc_test", "card": card, "store_hint": "quanji"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["delivery"], "app_bot")

    def test_send_operator_card_from_text(self) -> None:
        async def _deliver(**kwargs) -> dict:
            self.assertEqual(kwargs["chat_id"], "oc_test")
            self.assertEqual(kwargs["text"], "hello test reply")
            return {"status": "success", "delivery": "app_bot"}

        with mock.patch(
            "app.api.v1.feishu.deliver_operator_text_as_card",
            side_effect=_deliver,
        ):
            client = TestClient(app)
            response = client.post(
                "/api/v1/feishu/operator-card-sends",
                headers=service_token_headers(),
                json={"chat_id": "oc_test", "text": "hello test reply"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["delivery"], "app_bot")


if __name__ == "__main__":
    unittest.main()
