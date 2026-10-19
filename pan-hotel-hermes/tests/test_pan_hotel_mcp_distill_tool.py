from __future__ import annotations

import asyncio
import importlib
import unittest
from unittest import mock


class DistillExternalSourceMcpToolTests(unittest.TestCase):
    """V6 Step 2.7 — distill_external_source MCP 工具注册与转发。"""

    def setUp(self) -> None:
        self.server = importlib.import_module("pan_hotel_mcp.server")
        self.api = importlib.import_module("client.watch_api")

    def _list_tool_names(self) -> list[str]:
        tools = asyncio.run(self.server.list_tools())
        return [tool.name for tool in tools]

    def test_tool_registered_with_expected_schema(self) -> None:
        tools = asyncio.run(self.server.list_tools())
        tool = next((t for t in tools if t.name == "distill_external_source"), None)
        self.assertIsNotNone(tool, "distill_external_source 未注册")
        self.assertEqual(
            set(tool.inputSchema["required"]), {"source_type", "content", "platform"}
        )
        self.assertEqual(tool.inputSchema["properties"]["platform"]["enum"], ["doubao", "qwen"])
        self.assertEqual(
            tool.inputSchema["properties"]["source_type"]["enum"],
            ["text", "image_url", "video_url"],
        )
        self.assertIn("distill_external_source", self.server._TOOL_HANDLERS)

    def test_client_validates_source_type(self) -> None:
        client = self.api.WatchApiClient()
        payload = asyncio.run(
            client.distill_external_source(
                source_type="audio", content="x", platform="doubao"
            )
        )
        self.assertEqual(payload["status"], "invalid_arguments")

    def test_client_validates_platform(self) -> None:
        client = self.api.WatchApiClient()
        payload = asyncio.run(
            client.distill_external_source(
                source_type="text", content="规则", platform="gpt"
            )
        )
        self.assertEqual(payload["status"], "invalid_arguments")

    def test_client_posts_to_distill_runs(self) -> None:
        client = self.api.WatchApiClient()
        captured: dict[str, object] = {}

        async def fake_post_json(route: str, *, body: dict) -> dict:
            captured["route"] = route
            captured["body"] = body
            return {"status": "success", "rag_document_id": 11}

        with mock.patch.object(client, "post_json", side_effect=fake_post_json):
            payload = asyncio.run(
                client.distill_external_source(
                    source_type="image_url",
                    content="https://example.com/pic.png",
                    platform="doubao",
                    distill_prompt="提取运营规则要点",
                )
            )
        self.assertEqual(payload["status"], "success")
        self.assertEqual(captured["route"], "/api/v1/distill/runs")
        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["source_type"], "image_url")
        self.assertEqual(body["platform"], "doubao")
        self.assertEqual(body["distill_prompt"], "提取运营规则要点")

    def test_handler_delegates_to_client(self) -> None:
        client = mock.Mock()

        async def fake_distill(**kwargs) -> dict:
            return {"status": "success", "summary": "蒸馏完成并入库。", "rag_document_id": 3}

        client.distill_external_source = fake_distill
        result = asyncio.run(
            self.server._tool_distill_external_source(
                client,
                {
                    "source_type": "text",
                    "content": "携程活动报名规则",
                    "platform": "qwen",
                    "distill_prompt": "",
                },
            )
        )
        self.assertEqual(len(result), 1)
        self.assertIn("rag_document_id", result[0].text)


class CreateDistillSourceMcpToolTests(unittest.TestCase):
    """UI-Fix-F4F — create_distill_source / list_distill_sources MCP 工具。"""

    def setUp(self) -> None:
        self.server = importlib.import_module("pan_hotel_mcp.server")
        self.api = importlib.import_module("client.watch_api")

    def test_create_tool_registered_with_expected_schema(self) -> None:
        tools = asyncio.run(self.server.list_tools())
        tool = next((t for t in tools if t.name == "create_distill_source"), None)
        self.assertIsNotNone(tool, "create_distill_source 未注册")
        self.assertEqual(
            set(tool.inputSchema["required"]), {"source_type", "content", "platform"}
        )
        self.assertEqual(tool.inputSchema["properties"]["platform"]["enum"], ["doubao", "qwen"])
        interval = tool.inputSchema["properties"]["schedule_interval_minutes"]
        self.assertEqual(interval["default"], 120)
        self.assertIn("create_distill_source", self.server._TOOL_HANDLERS)

    def test_list_tool_registered(self) -> None:
        tools = asyncio.run(self.server.list_tools())
        tool = next((t for t in tools if t.name == "list_distill_sources"), None)
        self.assertIsNotNone(tool, "list_distill_sources 未注册")
        self.assertIn("list_distill_sources", self.server._TOOL_HANDLERS)

    def test_client_posts_to_distill_sources(self) -> None:
        client = self.api.WatchApiClient()
        captured: dict[str, object] = {}

        async def fake_post_json(route: str, *, body: dict) -> dict:
            captured["route"] = route
            captured["body"] = body
            return {"status": "success", "id": 31}

        with mock.patch.object(client, "post_json", side_effect=fake_post_json):
            payload = asyncio.run(
                client.create_distill_source(
                    source_type="video_url",
                    content="https://example.com/video",
                    platform="qwen",
                    schedule_interval_minutes=120,
                )
            )
        self.assertEqual(payload["status"], "success")
        self.assertEqual(captured["route"], "/api/v1/distill/sources")
        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["source_type"], "video_url")
        self.assertEqual(body["platform"], "qwen")
        self.assertEqual(body["schedule_interval_minutes"], 120)

    def test_client_defaults_interval_to_120(self) -> None:
        client = self.api.WatchApiClient()
        captured: dict[str, object] = {}

        async def fake_post_json(route: str, *, body: dict) -> dict:
            captured["body"] = body
            return {"status": "success", "id": 32}

        with mock.patch.object(client, "post_json", side_effect=fake_post_json):
            asyncio.run(
                client.create_distill_source(
                    source_type="text", content="规则", platform="doubao"
                )
            )
        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["schedule_interval_minutes"], 120)

    def test_client_lists_sources_via_get(self) -> None:
        client = self.api.WatchApiClient()
        captured: dict[str, object] = {}

        async def fake_get_json(route: str, **kwargs) -> dict:
            captured["route"] = route
            return {"sources": [], "count": 0}

        with mock.patch.object(client, "get_json", side_effect=fake_get_json):
            payload = asyncio.run(client.list_distill_sources())
        self.assertEqual(captured["route"], "/api/v1/distill/sources")
        self.assertEqual(payload["count"], 0)

    def test_create_handler_delegates_to_client(self) -> None:
        client = mock.Mock()

        async def fake_create(**kwargs) -> dict:
            return {"status": "success", "id": 33, "schedule_interval_minutes": 120}

        client.create_distill_source = fake_create
        result = asyncio.run(
            self.server._tool_create_distill_source(
                client,
                {
                    "source_type": "text",
                    "content": "每日规则",
                    "platform": "doubao",
                    "title": "规则",
                    "distill_prompt": "",
                    "schedule_interval_minutes": 120,
                    "enabled": True,
                },
            )
        )
        self.assertEqual(len(result), 1)
        self.assertIn("schedule_interval_minutes", result[0].text)

    def test_list_handler_delegates_to_client(self) -> None:
        client = mock.Mock()

        async def fake_list() -> dict:
            return {"sources": [{"id": 1, "title": "规则"}], "count": 1}

        client.list_distill_sources = fake_list
        result = asyncio.run(self.server._tool_list_distill_sources(client, {}))
        self.assertEqual(len(result), 1)
        self.assertIn("规则", result[0].text)


if __name__ == "__main__":
    unittest.main()
