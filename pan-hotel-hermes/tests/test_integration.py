from __future__ import annotations

import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from router.feishu_forward import (
    is_card_action,
    route_target,
    should_forward_to_watch,
)


class FeishuForwardTests(unittest.TestCase):
    def test_card_action_routes_to_watch(self) -> None:
        payload = {"header": {"event_type": "card.action.trigger"}, "action": {"value": {}}}
        self.assertTrue(should_forward_to_watch(payload))
        self.assertEqual(route_target(payload), "watch")
        self.assertTrue(is_card_action(payload))

    def test_url_verification_routes_to_watch(self) -> None:
        payload = {"type": "url_verification", "challenge": "abc"}
        self.assertTrue(should_forward_to_watch(payload))
        self.assertEqual(route_target(payload), "watch")

    def test_text_messages_never_route_to_watch(self) -> None:
        # 2026-10-14 全量 LLM 先行：文本消息一律进 Hermes 由 LLM 判断后
        # 自主调工具（含 dispatch 转发），词表直转通道全部退役——词表子串
        # 匹配会劫持测算/图表追问等问法（2026-10-14 生产实证）。
        for text in (
            "把今天高级大床房调到420",  # 原手动调价直转
            "@_user_1 查一下今天携程房价",  # 原房价查询直转
            "携程本店今日房价",
            "促销列表",  # 原促销指令直转
            "促销调整 天天特价 立减调到5",
            "金字塔出价调到2.5",  # 原金字塔指令直转
            # 2026-10-14 生产误触发原句：含「房型价格」被劫持成房价查询。
            "你给我做个测算，如果按照携程的8-10月的经营规律，我如果所有房型价格提高20元，每天的进单情况会有什么影响",
        ):
            payload = {
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "message": {
                        "message_id": "om_text",
                        "chat_id": "oc_text",
                        "chat_type": "p2p",
                        "content": json.dumps({"text": text}, ensure_ascii=False),
                    }
                },
            }
            with self.subTest(text=text):
                self.assertFalse(should_forward_to_watch(payload))
                self.assertEqual(route_target(payload), "hermes")

    def test_natural_language_routes_to_hermes(self) -> None:
        payload = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "content": '{"text":"@机器人 今天出租率多少？"}',
                }
            },
        }
        self.assertFalse(should_forward_to_watch(payload))
        self.assertEqual(route_target(payload), "hermes")


class WatchApiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_health_calls_ops_endpoint(self) -> None:
        from client.watch_api import WatchApiClient

        client = WatchApiClient(base_url="http://example.test")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"summary": {"status": "fresh"}})

        with patch("client.watch_api.httpx.AsyncClient") as client_cls:
            http = AsyncMock()
            http.get = AsyncMock(return_value=mock_response)
            client_cls.return_value.__aenter__.return_value = http
            result = await client.runtime_health()

        self.assertEqual(result["summary"]["status"], "fresh")
        http.get.assert_awaited_once()
        called_url = http.get.await_args.args[0]
        self.assertTrue(called_url.endswith("/api/v1/ops/runtime-health"))


class FeishuEventForwarderTests(unittest.IsolatedAsyncioTestCase):
    async def test_forward_card_payload(self) -> None:
        from router import FeishuEventForwarder

        forwarder = FeishuEventForwarder(watch_base_url="http://example.test")
        payload = {"header": {"event_type": "card.action.trigger"}}
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"status": "success"})

        with patch("router.forwarder.httpx.AsyncClient") as client_cls:
            http = AsyncMock()
            http.post = AsyncMock(return_value=mock_response)
            client_cls.return_value.__aenter__.return_value = http
            result = await forwarder.route(payload)

        self.assertEqual(result["route"], "watch")
        self.assertTrue(result["forwarded"])


class McpClientTests(unittest.TestCase):
    def test_get_client_returns_watch_api_client(self) -> None:
        import pan_hotel_mcp.server as mcp_server
        from client.watch_api import WatchApiClient

        mcp_server._client_instance = None
        client = mcp_server._get_client()
        self.assertIsInstance(client, WatchApiClient)
        same = mcp_server._get_client()
        self.assertIs(client, same)


if __name__ == "__main__":
    unittest.main()
