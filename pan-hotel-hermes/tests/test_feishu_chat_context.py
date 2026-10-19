from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from client.feishu_chat_context import enrich_feishu_chat_context
from client.watch_api import WatchApiClient


class FeishuChatContextEnrichmentTests(unittest.TestCase):
    def test_enrich_attaches_chat_context_card(self) -> None:
        payload = enrich_feishu_chat_context(
            {
                "status": "success",
                "chat_id_configured": True,
                "binding_mode": "single_store",
                "operator_summary": {
                    "intro": "本群已绑定竞品组：澜江路泉吉。",
                    "overview_rows": [
                        {"field": "EBK 后台账号", "value": "云州泉吉EBK"},
                        {"field": "竞对酒店", "value": "竞对A"},
                    ],
                },
            }
        )
        self.assertEqual(payload["feishu_card_kind"], "chat_context")
        self.assertEqual(
            payload["feishu_card"]["header"]["title"]["content"],
            "运营问答上下文群（单店）",
        )
        self.assertIn("本群已绑定竞品组", payload["feishu_card"]["elements"][0]["content"])


class FeishuChatContextApiTests(unittest.TestCase):
    def test_get_feishu_chat_context_calls_ops_endpoint(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(
                client,
                "get_json",
                new=mock.AsyncMock(
                    return_value={
                        "status": "success",
                        "chat_id_configured": True,
                        "competitor_watch_group_key": "default_competitor_watch",
                        "own_hotels": ["本店"],
                        "competitor_hotels": ["竞对A"],
                        "pricing_ebk_display_name": "云州泉吉EBK",
                        "operator_summary": {
                            "intro": "本群已绑定竞品组：澜江路泉吉。",
                            "overview_rows": [
                                {"field": "EBK 后台账号", "value": "云州泉吉EBK"},
                            ],
                        },
                    }
                ),
            ) as get_json:
                payload = await client.get_feishu_chat_context("oc_test", store_hint="静里")

            get_json.assert_awaited_once_with(
                "/api/v1/ops/feishu-chat-context",
                params={"chat_id": "oc_test", "store_hint": "静里"},
            )
            self.assertTrue(payload["chat_id_configured"])
            self.assertEqual(payload["feishu_card_kind"], "chat_context")
            self.assertIn("intro", payload["operator_summary"])
            self.assertIn("本群已绑定竞品组", payload["operator_summary"]["intro"])


if __name__ == "__main__":
    unittest.main()
