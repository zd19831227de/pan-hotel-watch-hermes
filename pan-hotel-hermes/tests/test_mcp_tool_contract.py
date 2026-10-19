from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest import mock

from client.operator_summary_contract import CONTRACT_VERSION
from client.watch_groups import DEFAULT_COMPETITOR_GROUP, DEFAULT_EBK_GROUP


def _load_mcp_server():
    try:
        from pan_hotel_mcp import server as mcp_server

        return mcp_server
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest(
            "mcp package required; use pan-hotel-hermes/.venv or "
            "pip install -e pan-hotel-hermes"
        ) from exc


def _list_tools():
    mcp_server = _load_mcp_server()
    return asyncio.run(mcp_server.list_tools())


class _BoundContextClient:
    async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
        return {
            "binding_mode": "single_store",
            "resolution": {
                "status": "resolved",
                "ebk_watch_group_key": DEFAULT_EBK_GROUP,
                "competitor_watch_group_key": DEFAULT_COMPETITOR_GROUP,
            },
            "ebk_groups": [
                {"group_key": DEFAULT_EBK_GROUP},
                {"group_key": "default_ebk_watch"},
            ],
            "competitor_groups": [{"group_key": DEFAULT_COMPETITOR_GROUP}],
        }


EXPECTED_TOOL_NAMES = {
    "list_watch_groups",
    "resolve_feishu_chat_context",
    "query_hotel_analytics",
    "rag_search",
    "distill_external_source",
    "create_distill_source",
    "list_distill_sources",
    "get_weekly_operator_briefing",
    "get_operator_watch_configuration",
    "get_watch_scheduler_status",
    "get_operator_hotel_overview",
    "get_operator_analysis_context",
    "get_operator_recent_activity",
    "get_ebk_latest_result",
    "get_refund_status",
    "get_today_reservations",
    "get_ebk_period_read",
    "pending_review_count",
    "get_latest_ebk_snapshot",
    "get_ebk_joint_analysis_context",
    "read_ebk_datasets",
    "run_ebk_watch_once",
    "run_competitor_watch_once",
    "get_competitor_latest_prices",
    "get_competitor_price_trend",
    "get_calendar_price_latest",
    "get_calendar_price_watch_status",
    "run_calendar_price_watch_once",
    "analyze_competitor_price_change",
    "pause_watch",
    "resume_watch",
    "create_manual_pricing_approval",
    "send_operator_document",
    "dispatch_watch_feishu_event",
    "search_group_archive",
    "get_bi_daily_report",
}

# 2026-10-10 起所有工具都声明 MCP_TOOL_CONTEXT_SCHEMA（会话注入兼容），
# 此集合留空作机制保留：将来新增真正 context-free 的工具时在此登记豁免。
CONTEXT_FREE_TOOL_NAMES: set[str] = set()


class McpToolContractTests(unittest.TestCase):
    def test_explicit_watch_group_key_must_belong_to_current_chat(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "ebk_watch_group_key": "ebk_group_a",
                            "competitor_watch_group_key": "competitor_group_a",
                        },
                        "ebk_groups": [{"group_key": "ebk_group_a"}],
                        "competitor_groups": [{"group_key": "competitor_group_a"}],
                    }

            key, blocked = await mcp_server._resolve_tool_watch_group_key(
                _StubClient(),
                {"chat_id": "oc_group_a"},
                watch_kind="ebk",
                explicit_key="ebk_group_b",
            )

            self.assertIsNone(key)
            assert blocked is not None
            self.assertEqual(blocked["status"], "watch_group_not_allowed")

        asyncio.run(scenario())

    def test_competitor_explicit_watch_group_key_must_belong_to_current_chat(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "competitor_watch_group_key": "competitor_group_a",
                        },
                        "ebk_groups": [],
                        "competitor_groups": [{"group_key": "competitor_group_a"}],
                    }

            key, blocked = await mcp_server._resolve_tool_watch_group_key(
                _StubClient(),
                {"chat_id": "oc_group_a"},
                watch_kind="competitor",
                explicit_key="competitor_group_b",
            )

            self.assertIsNone(key)
            assert blocked is not None
            self.assertEqual(blocked["status"], "watch_group_not_allowed")

        asyncio.run(scenario())

    def test_pause_watch_rejects_cross_chat_key_without_patching(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            patch_calls: list[str] = []

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "ebk_watch_group_key": "ebk_group_a",
                        },
                        "ebk_groups": [{"group_key": "ebk_group_a"}],
                        "competitor_groups": [],
                    }

                async def patch_ebk_watch_group(self, watch_group_key, *, body):
                    patch_calls.append(watch_group_key)
                    return {"status": "success"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "pause_watch",
                    {
                        "watch_kind": "ebk",
                        "watch_group_key": "ebk_group_b",
                        "chat_id": "oc_group_a",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "watch_group_not_allowed")
            self.assertEqual(patch_calls, [])

        asyncio.run(scenario())

    def test_pause_competitor_watch_rejects_cross_chat_key_without_patching(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            patch_calls: list[str] = []

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "competitor_watch_group_key": "competitor_group_a",
                        },
                        "ebk_groups": [],
                        "competitor_groups": [{"group_key": "competitor_group_a"}],
                    }

                async def patch_competitor_watch_group(self, watch_group_key, *, body):
                    patch_calls.append(watch_group_key)
                    return {"status": "success"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "pause_watch",
                    {
                        "watch_kind": "competitor",
                        "watch_group_key": "competitor_group_b",
                        "chat_id": "oc_group_a",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "watch_group_not_allowed")
            self.assertEqual(patch_calls, [])

        asyncio.run(scenario())

    def test_get_competitor_latest_rejects_cross_chat_key_without_reading(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            read_calls: list[str] = []

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "competitor_watch_group_key": "competitor_group_a",
                        },
                        "ebk_groups": [],
                        "competitor_groups": [{"group_key": "competitor_group_a"}],
                    }

                async def get_competitor_latest(self, watch_group_key, **kwargs):
                    read_calls.append(watch_group_key)
                    return {"status": "success"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_competitor_latest_prices",
                    {
                        "watch_group_key": "competitor_group_b",
                        "chat_id": "oc_group_a",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "watch_group_not_allowed")
            self.assertEqual(read_calls, [])

        asyncio.run(scenario())

    def test_list_tools_matches_expected_contract(self) -> None:
        mcp_server = _load_mcp_server()
        tools = asyncio.run(mcp_server.list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(names, EXPECTED_TOOL_NAMES)

    def test_all_tools_allow_hermes_injected_session_fields(self) -> None:
        tools = _list_tools()
        for tool in tools:
            with self.subTest(name=tool.name):
                if tool.name in CONTEXT_FREE_TOOL_NAMES:
                    continue
                props = tool.inputSchema.get("properties", {})
                self.assertIn("open_id", props)
                self.assertIn("chat_id", props)
                self.assertIn("message_id", props)

    def test_rag_search_accepts_session_fields_and_is_read_only(self) -> None:
        """rag_search 保持 query/top_k 口径，但必须声明会话字段（插件注入兼容）。"""
        tools = _list_tools()
        tool = next(item for item in tools if item.name == "rag_search")
        props = tool.inputSchema.get("properties", {})
        self.assertTrue({"query", "top_k"} <= set(props))
        self.assertTrue({"chat_id", "message_id", "open_id", "user_text"} <= set(props))
        self.assertEqual(tool.inputSchema.get("required"), ["query"])
        self.assertEqual(props["top_k"].get("default"), 3)
        self.assertIn("read-only", str(tool.description or "").lower())

    def test_rag_search_handler_calls_context_free_client_method(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            captured: dict[str, object] = {}

            class _StubClient:
                async def rag_search(self, *, query: str, top_k: int) -> dict:
                    captured["query"] = query
                    captured["top_k"] = top_k
                    return {
                        "results": [
                            {
                                "document_id": 1,
                                "title": "Refund policy",
                                "chunk_text": "Free cancellation within 24 hours.",
                                "score": 0.98,
                            }
                        ]
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "rag_search",
                    {"query": "refund cancellation", "top_k": 3},
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["results"][0]["title"], "Refund policy")
            self.assertEqual(captured, {"query": "refund cancellation", "top_k": 3})

        asyncio.run(scenario())

    def test_store_configuration_tools_require_chat_id(self) -> None:
        tools = _list_tools()
        by_name = {tool.name: tool for tool in tools}
        for name in ("list_watch_groups", "get_operator_watch_configuration"):
            with self.subTest(name=name):
                self.assertIn("chat_id", by_name[name].inputSchema.get("required", []))
                self.assertNotIn("全局所有", str(by_name[name].description or ""))

    def test_store_configuration_tools_fail_closed_without_chat_id(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            client = mock.Mock()
            client.get_watch_group_catalog = mock.AsyncMock(
                return_value={"status": "success", "ebk_groups": [{"watch_group_key": "group_b"}]}
            )
            client.get_operator_watch_configuration = mock.AsyncMock(
                return_value={"status": "success", "operator_summary": {"intro": "Hotel B"}}
            )

            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                list_result = await mcp_server.call_tool("list_watch_groups", {})
                config_result = await mcp_server.call_tool(
                    "get_operator_watch_configuration",
                    {},
                )
            finally:
                mcp_server._client_instance = old_client

            list_payload = json.loads(list_result[0].text)
            config_payload = json.loads(config_result[0].text)
            self.assertEqual(list_payload["status"], "session_context_required")
            self.assertEqual(config_payload["status"], "session_context_required")
            client.get_watch_group_catalog.assert_not_awaited()
            client.get_operator_watch_configuration.assert_not_awaited()

        asyncio.run(scenario())

    def test_manual_pricing_tool_accepts_store_hint(self) -> None:
        tools = _list_tools()
        manual_tool = next(tool for tool in tools if tool.name == "create_manual_pricing_approval")
        props = manual_tool.inputSchema.get("properties", {})
        self.assertIn("store_hint", props)
        self.assertIn("一群多店", str(props["store_hint"].get("description") or ""))

    def test_read_only_query_tools_allow_text_fallback_without_message_id(self) -> None:
        tools = _list_tools()
        by_name = {tool.name: tool for tool in tools}
        for name in (
            "get_operator_watch_configuration",
            "get_watch_scheduler_status",
            "get_operator_hotel_overview",
            "get_operator_analysis_context",
            "get_operator_recent_activity",
            "get_ebk_latest_result",
            "get_latest_ebk_snapshot",
            "get_ebk_joint_analysis_context",
            "get_competitor_latest_prices",
            "get_competitor_price_trend",
            "get_calendar_price_latest",
            "get_calendar_price_watch_status",
            "analyze_competitor_price_change",
        ):
            with self.subTest(name=name):
                required = by_name[name].inputSchema.get("required", [])
                self.assertNotIn("message_id", required)

    def test_chat_context_tool_requires_chat_id_without_message_id(self) -> None:
        tools = _list_tools()
        context_tool = next(tool for tool in tools if tool.name == "resolve_feishu_chat_context")
        required = context_tool.inputSchema.get("required", [])
        self.assertIn("chat_id", required)
        self.assertNotIn("message_id", required)
        description = str(context_tool.description or "")
        self.assertIn("chat_id", description)
        self.assertIn("绑定", description)
        self.assertLessEqual(len(description), 120)
        self.assertIn("可选", context_tool.inputSchema["properties"]["message_id"]["description"])

    def test_chat_context_tool_allows_explicit_response_mode(self) -> None:
        tools = _list_tools()
        context_tool = next(tool for tool in tools if tool.name == "resolve_feishu_chat_context")
        response_mode = context_tool.inputSchema.get("properties", {}).get("response_mode", {})
        self.assertEqual(response_mode.get("enum"), ["text", "detail", "summary"])
        self.assertEqual(response_mode.get("default"), "text")
        self.assertIn("text=默认", str(response_mode.get("description") or ""))

    def test_competitor_trend_tool_exposes_with_chart_flag(self) -> None:
        # 2026-10-08 价格办公软件技能：用户要看曲线图时模型可传 with_chart=true。
        tools = _list_tools()
        trend_tool = next(tool for tool in tools if tool.name == "get_competitor_price_trend")
        with_chart = trend_tool.inputSchema.get("properties", {}).get("with_chart", {})
        self.assertEqual(with_chart.get("type"), "boolean")
        self.assertIn("曲线", str(with_chart.get("description") or ""))

    def test_competitor_trend_tool_exposes_metric_and_hotels_params(self) -> None:
        # 2026-10-08 对比图能力：metric=min（每店一条各时段最低价线）+
        # hotels 过滤（『本店』匹配 role=own）。
        tools = _list_tools()
        trend_tool = next(tool for tool in tools if tool.name == "get_competitor_price_trend")
        props = trend_tool.inputSchema.get("properties", {})
        self.assertEqual(props.get("metric", {}).get("enum"), ["series", "min"])
        hotels = props.get("hotels", {})
        self.assertEqual(hotels.get("type"), "string")
        self.assertIn("本店", str(hotels.get("description") or ""))

    def test_competitor_trend_metric_min_merges_breakfasts_and_filters_hotels(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "competitor_watch_group_key": "competitor_group_a",
                        },
                        "ebk_groups": [],
                        "competitor_groups": [{"group_key": "competitor_group_a"}],
                    }

                async def get_competitor_price_trend(self, watch_group_key, **kwargs):
                    return {
                        "status": "success",
                        "watch_group_key": watch_group_key,
                        "granularity": kwargs.get("granularity"),
                        "series": [
                            {
                                "hotel_alias": "立瑞酒店",
                                "hotel_role": "own",
                                "date_label": "today",
                                "breakfast_kind": "included",
                                "points": [
                                    {"price_amount_yuan": 234, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"}
                                ],
                            },
                            {
                                "hotel_alias": "水云岸酒店",
                                "hotel_role": "competitor",
                                "date_label": "today",
                                "breakfast_kind": "none",
                                "points": [
                                    {"price_amount_yuan": 262, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"}
                                ],
                            },
                            {
                                "hotel_alias": "水云岸酒店",
                                "hotel_role": "competitor",
                                "date_label": "today",
                                "breakfast_kind": "included",
                                "points": [
                                    {"price_amount_yuan": 300, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"}
                                ],
                            },
                            {
                                "hotel_alias": "无关竞店",
                                "hotel_role": "competitor",
                                "date_label": "today",
                                "breakfast_kind": "none",
                                "points": [
                                    {"price_amount_yuan": 199, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"}
                                ],
                            },
                        ],
                    }

                async def send_operator_feishu_card(self, **kwargs):  # pragma: no cover
                    return {"status": "success", "delivery": "app_bot"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_competitor_price_trend",
                    {
                        "watch_group_key": "competitor_group_a",
                        "chat_id": "oc_group_a",
                        "days": 1,
                        "granularity": "hour",
                        "metric": "min",
                        "hotels": "本店,水云岸",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("metric"), "min")
            aliases = {str(s.get("hotel_alias")) for s in payload.get("series") or []}
            # 过滤生效（无关竞店被剔除）+ 本店与水云岸归并成各一条最低价线
            self.assertEqual(aliases, {"立瑞酒店", "水云岸酒店"})
            for series in payload["series"]:
                self.assertEqual(series.get("breakfast_kind"), "min_compare")
            comp = next(s for s in payload["series"] if s.get("hotel_role") == "competitor")
            self.assertEqual(comp["points"][0]["price_amount_yuan"], 262)
            self.assertEqual(payload.get("point_count"), 2)

        asyncio.run(scenario())

    def test_send_operator_document_tool_renders_and_sends_file(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            client = mock.Mock()
            client.send_operator_feishu_file = mock.AsyncMock(
                return_value={"status": "success", "summary": "已向本群发送文件"}
            )

            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                result = await mcp_server.call_tool(
                    "send_operator_document",
                    {
                        "chat_id": "oc_group",
                        "doc_type": "xlsx",
                        "title": "静里铺排",
                        "content": "# 铺排\n\n## 表\n| 日期 | 价 |\n| 10-08 | 206 |",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("status"), "success")
            client.send_operator_feishu_file.assert_awaited_once()
            kwargs = client.send_operator_feishu_file.await_args.kwargs
            self.assertEqual(kwargs["doc_type"], "xlsx")
            self.assertTrue(kwargs["filename"].endswith(".xlsx"))
            import base64 as _b64

            decoded = _b64.b64decode(kwargs["data_base64"])
            self.assertGreater(len(decoded), 1000)

        asyncio.run(scenario())

    def test_send_operator_document_rejects_bad_doc_type(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            client = mock.Mock()
            client.send_operator_feishu_file = mock.AsyncMock()
            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                result = await mcp_server.call_tool(
                    "send_operator_document",
                    {
                        "chat_id": "oc_group",
                        "doc_type": "exe",
                        "title": "x",
                        "content": "# x",
                    },
                )
            finally:
                mcp_server._client_instance = old_client
            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("status"), "failed")
            client.send_operator_feishu_file.assert_not_awaited()

        asyncio.run(scenario())

    def test_competitor_trend_with_chart_sends_chart_card(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            chart_card = {
                "header": {"title": "竞品价格趋势"},
                "elements": [],
                "_feishu_image_uploads": [
                    {"alt": "trend", "data_base64": "aGk=", "position": 1}
                ],
            }
            client = mock.Mock()
            client.send_operator_feishu_card = mock.AsyncMock(
                return_value={"status": "success", "delivery": "app_bot"}
            )

            payload = {
                "status": "ok",
                "feishu_card_kind": "competitor_price_trend",
                "feishu_card": chart_card,
                "operator_summary": {"intro": "竞品价格趋势"},
            }
            result = await mcp_server._with_auto_feishu_card(
                client,
                {"chat_id": "oc_group", "with_chart": True},
                payload,
                tool_name="get_competitor_price_trend",
            )

            self.assertEqual(result.get("response_mode"), "detail")
            self.assertTrue(result.get("feishu_card_sent"))
            client.send_operator_feishu_card.assert_awaited_once()
            sent_card = client.send_operator_feishu_card.await_args.kwargs.get("card")
            self.assertEqual(
                sent_card.get("_feishu_image_uploads")[0].get("data_base64"), "aGk="
            )

        asyncio.run(scenario())

    def test_competitor_trend_without_chart_flag_stays_text(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            client = mock.Mock()
            client.send_operator_feishu_card = mock.AsyncMock(
                return_value={"status": "success", "delivery": "app_bot"}
            )

            payload = {
                "status": "ok",
                "feishu_card_kind": "competitor_price_trend",
                "feishu_card": {"header": {}, "elements": []},
                "operator_summary": {"intro": "竞品价格趋势"},
            }
            result = await mcp_server._with_auto_feishu_card(
                client,
                {"chat_id": "oc_group"},
                payload,
                tool_name="get_competitor_price_trend",
            )

            self.assertEqual(result.get("response_mode"), "text")
            self.assertNotIn("feishu_card", result)
            client.send_operator_feishu_card.assert_not_awaited()

        asyncio.run(scenario())

    def test_chat_context_defaults_to_text_without_sending_detail_card(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            client = mock.Mock()
            client.get_feishu_chat_context = mock.AsyncMock(
                return_value={
                    "status": "success",
                    "binding_mode": "single_store",
                    "resolution": {"status": "resolved", "store_name": "曼馨"},
                    "feishu_card_kind": "chat_context",
                    "feishu_card": {"header": {}, "elements": []},
                    "operator_summary": {"intro": "本群绑定曼馨酒店。"},
                }
            )
            client.send_operator_feishu_card = mock.AsyncMock(
                return_value={"status": "success", "delivery": "app_bot"}
            )

            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                result = await mcp_server.call_tool(
                    "resolve_feishu_chat_context",
                    {"chat_id": "oc_group"},
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))
            client.send_operator_feishu_card.assert_not_awaited()

        asyncio.run(scenario())

    def test_chat_context_detail_mode_forced_to_text(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            client = mock.Mock()
            client.get_feishu_chat_context = mock.AsyncMock(
                return_value={
                    "status": "success",
                    "binding_mode": "single_store",
                    "resolution": {"status": "resolved", "store_name": "曼馨"},
                    "feishu_card_kind": "chat_context",
                    "feishu_card": {"header": {}, "elements": []},
                    "operator_summary": {"intro": "本群绑定曼馨酒店。"},
                }
            )
            client.send_operator_feishu_card = mock.AsyncMock(
                return_value={"status": "success", "delivery": "app_bot"}
            )

            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                result = await mcp_server.call_tool(
                    "resolve_feishu_chat_context",
                    {"chat_id": "oc_group", "response_mode": "detail"},
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))
            self.assertNotIn("feishu_card", payload)
            client.send_operator_feishu_card.assert_not_awaited()

        asyncio.run(scenario())

    def test_watch_configuration_allows_explicit_response_mode(self) -> None:
        tools = _list_tools()
        config_tool = next(tool for tool in tools if tool.name == "get_operator_watch_configuration")
        response_mode = config_tool.inputSchema.get("properties", {}).get("response_mode", {})
        self.assertEqual(response_mode.get("enum"), ["text", "detail", "summary"])

    def test_watch_configuration_defaults_to_text_without_sending_detail_card(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            client = mock.Mock()
            client.get_operator_watch_configuration = mock.AsyncMock(
                return_value={
                    "status": "success",
                    "feishu_card_kind": "watch_configuration",
                    "feishu_card": {"header": {}, "elements": []},
                    "operator_summary": {"intro": "全局盯盘配置。"},
                }
            )
            client.send_operator_feishu_card = mock.AsyncMock(
                return_value={"status": "success", "delivery": "app_bot"}
            )

            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                result = await mcp_server.call_tool(
                    "get_operator_watch_configuration",
                    {"chat_id": "oc_group"},
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))
            client.send_operator_feishu_card.assert_not_awaited()

        asyncio.run(scenario())

    def test_chat_context_tool_describes_operations_context_not_pricing_approval(self) -> None:
        tools = _list_tools()
        context_tool = next(tool for tool in tools if tool.name == "resolve_feishu_chat_context")
        description = str(context_tool.description or "")
        self.assertIn("门店", description)
        self.assertIn("只读", description)
        self.assertNotIn("调价审批群", description)

    def test_weekly_briefing_tool_requires_chat_id_without_message_id(self) -> None:
        tools = _list_tools()
        weekly_tool = next(tool for tool in tools if tool.name == "get_weekly_operator_briefing")
        required = weekly_tool.inputSchema.get("required", [])
        self.assertIn("chat_id", required)
        self.assertNotIn("message_id", required)
        description = str(weekly_tool.description or "")
        self.assertIn("经营简报", description)
        self.assertLessEqual(len(description), 120)
        self.assertIn("可选", weekly_tool.inputSchema["properties"]["message_id"]["description"])

    def test_run_tools_do_not_require_message_id(self) -> None:
        tools = _list_tools()
        by_name = {tool.name: tool for tool in tools}
        for name in ("run_ebk_watch_once", "run_competitor_watch_once", "run_calendar_price_watch_once", "read_ebk_datasets"):
            with self.subTest(name=name):
                required = by_name[name].inputSchema.get("required", [])
                self.assertNotIn("message_id", required)

    def test_recent_activity_tool_requires_message_id(self) -> None:
        tools = _list_tools()
        activity_tool = next(tool for tool in tools if tool.name == "get_operator_recent_activity")
        self.assertNotIn("message_id", activity_tool.inputSchema.get("required", []))

    def test_default_group_constants_match_watch_groups_module(self) -> None:
        server = _load_mcp_server()

        self.assertEqual(server.DEFAULT_EBK_GROUP_KEY, DEFAULT_EBK_GROUP)
        self.assertEqual(server.DEFAULT_COMPETITOR_GROUP_KEY, DEFAULT_COMPETITOR_GROUP)

    def test_tool_descriptions_are_short(self) -> None:
        tools = _list_tools()
        for tool in tools:
            description = str(tool.description or "")
            self.assertLessEqual(
                len(description),
                120,
                msg=f"{tool.name} description too long: {len(description)} chars",
            )

    def test_competitor_tool_descriptions_are_mutually_distinct(self) -> None:
        tools = _list_tools()
        by_name = {tool.name: str(tool.description or "") for tool in tools}
        self.assertIn("现价", by_name["get_competitor_latest_prices"])
        self.assertIn("历史价格序列", by_name["get_competitor_price_trend"])
        self.assertIn("相邻两次", by_name["analyze_competitor_price_change"])
        self.assertIn("刷新", by_name["run_competitor_watch_once"])

    def test_analysis_context_tool_is_read_only_and_compact(self) -> None:
        tools = _list_tools()
        tool = next(tool for tool in tools if tool.name == "get_operator_analysis_context")
        description = str(tool.description or "")
        self.assertLessEqual(len(description), 120)
        self.assertIn("经营分析上下文", description)
        props = tool.inputSchema.get("properties", {})
        self.assertIn("chat_id", props)
        self.assertIn("store_hint", props)
        self.assertIn("ebk_watch_group_key", props)
        self.assertIn("competitor_watch_group_key", props)
        self.assertNotIn("message_id", tool.inputSchema.get("required", []))

    def test_analysis_context_tool_dispatches_to_client(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_operator_analysis_context(self, **kwargs):
                    return {
                        "status": "success",
                        "kwargs": kwargs,
                        "operator_summary": {
                            "intro": "经营分析上下文",
                            "analysis_sections": ["结论", "已证实数据", "可能原因", "建议动作", "数据缺口"],
                        },
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_operator_analysis_context",
                    {"chat_id": "oc_group", "store_hint": "静里"},
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["kwargs"]["chat_id"], "oc_group")
            self.assertEqual(payload["kwargs"]["store_hint"], "静里")

        asyncio.run(scenario())

    def test_overview_defaults_to_text_without_sending_detail_card(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "store_name": "静里",
                            "ebk_watch_group_key": "jingli-ebk",
                            "competitor_watch_group_key": "jingli-comp",
                        },
                    }

                async def get_operator_hotel_overview(self, **kwargs):
                    return {
                        "status": "success",
                        "feishu_card": {"header": {"title": {"content": "酒店今日概览"}}, "elements": []},
                        "feishu_card_kind": "hotel_overview",
                        "operator_summary": {"intro": "概览", "overview_rows": []},
                        "mobile_evidence": [
                            {"evidence_ref": "ev_occ", "label": "出租率", "value": "82", "unit": "%"}
                        ],
                    }

                async def send_operator_feishu_card(self, **kwargs):
                    return {"status": "success", "delivery": "app_bot"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                with mock.patch(
                    "client.feishu_store_resolver.resolve_overview_watch_groups",
                    new=mock.AsyncMock(return_value=(("jingli-ebk", "jingli-comp"), None)),
                ):
                    result = await mcp_server.call_tool(
                        "get_operator_hotel_overview",
                        {"chat_id": "oc_group"},
                    )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))
            self.assertTrue(payload.get("mobile_evidence"))

        asyncio.run(scenario())

    def test_joint_brief_tool_sets_store_label_without_auto_push(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "store_name": "静里",
                            "ebk_watch_group_key": "jingli-ebk",
                        },
                        "ota_ebk_accounts": {
                            "ctrip": None,
                            "meituan": {"platform_account_id": "meituan_account_01"},
                            "fliggy": None,
                        },
                    }

                async def get_ebk_joint_analysis_context(self, **kwargs):
                    return {
                        "status": "success",
                        "analysis_mode": "meituan",
                        "summary": "美团 EBK 分析可用。",
                        "kwargs": kwargs,
                        "operator_summary": {
                            "platforms": {"meituan": {"status": "available"}},
                            "read_times": {"meituan": "2026-07-16T01:10:00+00:00"},
                            "missing_data": [],
                            "meituan_workbench": {
                                "available": True,
                                "realtime_data": {"今日订单": "3"},
                            },
                            "usage_note": "只读分析。",
                        },
                    }

                async def send_operator_feishu_card(self, **kwargs):
                    return {"status": "success", "delivery": "app_bot", "kwargs": kwargs}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_ebk_joint_analysis_context",
                    {
                        "chat_id": "oc_group",
                        "platforms": ["meituan"],
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            # P6：账号来自后端 chat-context 的 ota_ebk_accounts（无客户端模糊匹配）。
            self.assertEqual(
                payload.get("kwargs", {}).get("meituan_platform_account_id"),
                "meituan_account_01",
            )
            self.assertEqual(payload["operator_summary"]["store_label"], "静里")
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload["feishu_card_sent"])
            self.assertNotIn("feishu_card", payload)
            self.assertNotIn("feishu_card_kind", payload)
            self.assertEqual(payload["operator_summary"].get("reply_mode"), "text_only")

        asyncio.run(scenario())

    def test_read_ebk_datasets_description_emphasizes_on_demand_browser_read(self) -> None:
        tools = _list_tools()
        tool = next(item for item in tools if item.name == "read_ebk_datasets")
        description = str(tool.description or "")
        schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        dataset_desc = str((properties.get("dataset_ids") or {}).get("description") or "")
        self.assertIn("按需现读", description)
        self.assertIn("enabled_datasets", description)
        self.assertIn("promotion_registered", dataset_desc)
        self.assertIn("ad_pyramid", dataset_desc)
        self.assertIn("flow_data", dataset_desc)
        self.assertIn("beneficial_data", dataset_desc)
        self.assertIn("dataset_read_note", description)
        self.assertNotIn("禁止", description)
        self.assertLessEqual(len(description), 120)

    def test_joint_analysis_description_covers_platforms_without_ota_names(self) -> None:
        tools = _list_tools()
        tool = next(item for item in tools if item.name == "get_ebk_joint_analysis_context")
        description = str(tool.description or "")
        self.assertIn("多平台", description)
        self.assertIn("经营证据", description)
        self.assertNotIn("禁止", description)
        self.assertLessEqual(len(description), 120)

    def test_competitor_price_trend_tool_dispatches_to_client(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def get_competitor_price_trend(self, watch_group_key, **kwargs):
                    return {
                        "status": "success",
                        "watch_group_key": watch_group_key,
                        "kwargs": kwargs,
                        "operator_summary": {"intro": "最近3天竞品价格趋势"},
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_competitor_price_trend",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "days": 3,
                        "chat_id": "oc_group",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["watch_group_key"], "default_competitor_watch")
            self.assertEqual(payload["kwargs"]["days"], 3)
            self.assertEqual(payload["kwargs"]["granularity"], "run")
            self.assertFalse(payload.get("feishu_card_sent"))

        asyncio.run(scenario())

    def test_calendar_price_latest_tool_dispatches_to_client(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def get_calendar_price_latest(self, watch_group_key, **kwargs):
                    return {
                        "status": "success",
                        "watch_group_key": watch_group_key,
                        "kwargs": kwargs,
                        "operator_summary": {"module": "calendar_price_watch"},
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_calendar_price_latest",
                    {"watch_group_key": "default_competitor_watch", "chat_id": "oc_group", "open_id": "ou_operator"},
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["watch_group_key"], "default_competitor_watch")
            self.assertEqual(payload["kwargs"]["chat_id"], "oc_group")
            self.assertEqual(payload["kwargs"]["operator_id"], "ou_operator")

        asyncio.run(scenario())

    def test_calendar_price_status_tool_dispatches_to_client(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def get_calendar_price_watch_status(self, watch_group_key, **kwargs):
                    return {
                        "status": "success",
                        "watch_group_key": watch_group_key,
                        "kwargs": kwargs,
                        "operator_summary": {
                            "module": "calendar_price_watch",
                            "status_kind": "calendar_price_watch_status",
                        },
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_calendar_price_watch_status",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "chat_id": "oc_group",
                        "open_id": "ou_operator",
                        "store_hint": "澜江路泉吉",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["watch_group_key"], "default_competitor_watch")
            self.assertEqual(payload["kwargs"]["chat_id"], "oc_group")
            self.assertEqual(payload["kwargs"]["operator_id"], "ou_operator")
            self.assertIn("calendar_price_watch_status", result[0].text)

        asyncio.run(scenario())

    def test_calendar_price_run_tool_defaults_to_no_feishu_push(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def run_calendar_price_watch_once(self, watch_group_key, **kwargs):
                    return {
                        "status": "alert",
                        "watch_group_key": watch_group_key,
                        "kwargs": kwargs,
                        "operator_summary": {"module": "calendar_price_watch"},
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "run_calendar_price_watch_once",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "open_id": "ou_operator",
                        "chat_id": "oc_group",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["watch_group_key"], "default_competitor_watch")
            self.assertIs(payload["kwargs"]["push_to_feishu"], False)
            self.assertEqual(payload["kwargs"]["operator_id"], "ou_operator")

        asyncio.run(scenario())

    def test_calendar_price_run_tool_ignores_explicit_feishu_push(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def run_calendar_price_watch_once(self, watch_group_key, **kwargs):
                    return {
                        "status": "success",
                        "watch_group_key": watch_group_key,
                        "kwargs": kwargs,
                        "operator_summary": {"intro": "日历价已刷新"},
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "run_calendar_price_watch_once",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "push_to_feishu": True,
                        "chat_id": "oc_group",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertIs(payload["kwargs"]["push_to_feishu"], False)

        asyncio.run(scenario())

    def test_competitor_run_tool_never_pushes_feishu_from_hermes(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def run_competitor_watch_once(self, watch_group_key, **kwargs):
                    return {
                        "status": "success",
                        "watch_group_key": watch_group_key,
                        "kwargs": kwargs,
                        "operator_summary": {"intro": "竞品已刷新"},
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                omitted = await mcp_server.call_tool(
                    "run_competitor_watch_once",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "chat_id": "oc_group",
                        "user_text": "刷新竞品",
                    },
                )
                forced = await mcp_server.call_tool(
                    "run_competitor_watch_once",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "chat_id": "oc_group",
                        "push_to_feishu": True,
                        "user_text": "刷新竞品",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            self.assertIs(json.loads(omitted[0].text)["kwargs"]["push_to_feishu"], False)
            self.assertIs(json.loads(forced[0].text)["kwargs"]["push_to_feishu"], False)

        asyncio.run(scenario())

    def test_read_ebk_datasets_uses_open_id_as_actor_id(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def read_ebk_datasets(self, **kwargs):
                    return {
                        "status": "success",
                        "kwargs": kwargs,
                        "operator_summary": {"intro": "EBK 数据现读"},
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "read_ebk_datasets",
                    {
                        "watch_group_key": "default_ebk_watch",
                        "open_id": "ou_operator",
                        "chat_id": "oc_group",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["kwargs"]["actor_id"], "ou_operator")

        asyncio.run(scenario())

    def test_v2_read_ebk_datasets_forces_text_no_card(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def read_ebk_datasets(self, **kwargs):
                    return {
                        "status": "success",
                        "feishu_card": {"header": {}, "elements": []},
                        "operator_summary": {
                            "intro": "出租率 72%",
                            "reply_mode": "card_first",
                        },
                    }

                send_operator_feishu_card = mock.AsyncMock(
                    return_value={"status": "success", "delivery": "app_bot"}
                )

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                with mock.patch.dict(os.environ, {"PAN_HOTEL_REPLY_POLICY": "v2"}):
                    result = await mcp_server.call_tool(
                        "read_ebk_datasets",
                        {
                            "watch_group_key": "default_ebk_watch",
                            "chat_id": "oc_group",
                            "dataset_ids": ["occupancy"],
                        },
                    )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))
            self.assertNotIn("feishu_card", payload)
            self.assertEqual(
                (payload.get("operator_summary") or {}).get("reply_mode"),
                "text_only",
            )

        asyncio.run(scenario())

    def test_joint_and_read_tools_declare_response_mode(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            tools = await mcp_server.list_tools()
            by_name = {tool.name: tool for tool in tools}
            for name in ("get_ebk_joint_analysis_context", "read_ebk_datasets"):
                props = (by_name[name].inputSchema or {}).get("properties") or {}
                self.assertIn("response_mode", props, name)
                self.assertEqual(props["response_mode"].get("enum"), ["text", "detail", "summary"])

        asyncio.run(scenario())

    def test_explicit_detail_mode_no_longer_sends_qna_briefing_card(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            from client import operator_reply as operator_reply_mod

            operator_reply_mod._RECENT_CARD_PUSHES.clear()
            operator_reply_mod._RECENT_EBK_CAPTURES.clear()

            client = mock.Mock()
            client.get_feishu_chat_context = mock.AsyncMock(
                return_value={
                    "status": "success",
                    "binding_mode": "single_store",
                    "resolution": {"status": "resolved", "store_name": "曼馨"},
                    "feishu_card_kind": "chat_context",
                    "feishu_card": {"header": {}, "elements": []},
                    "operator_summary": {"intro": "本群绑定曼馨酒店。"},
                }
            )
            client.send_operator_feishu_card = mock.AsyncMock(
                return_value={"status": "success", "delivery": "app_bot"}
            )

            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                result = await mcp_server.call_tool(
                    "resolve_feishu_chat_context",
                    {
                        "chat_id": "oc_detail_unique",
                        "response_mode": "detail",
                        "user_text": "请发卡片展开明细",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))
            self.assertNotIn("feishu_card", payload)
            client.send_operator_feishu_card.assert_not_awaited()

        asyncio.run(scenario())

    def test_scheduler_status_detail_stays_text(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            from client import operator_reply as operator_reply_mod

            operator_reply_mod._RECENT_CARD_PUSHES.clear()
            operator_reply_mod._RECENT_EBK_CAPTURES.clear()

            client = mock.Mock()
            client.runtime_health = mock.AsyncMock(
                return_value={
                    "status": "success",
                    "feishu_card_kind": "scheduler_status",
                    "feishu_card": {"header": {"title": {"content": "盯盘服务状态"}}, "elements": []},
                    "operator_summary": {"intro": "调度器正常"},
                }
            )
            client.send_operator_feishu_card = mock.AsyncMock(
                return_value={"status": "success", "delivery": "app_bot"}
            )

            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                result = await mcp_server.call_tool(
                    "get_watch_scheduler_status",
                    {
                        "chat_id": "oc_no_card",
                        "response_mode": "detail",
                        "user_text": "系统情况如何 1,2",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))
            self.assertNotIn("feishu_card", payload)
            client.send_operator_feishu_card.assert_not_awaited()

        asyncio.run(scenario())

    def test_v2_joint_defaults_to_text(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_feishu_chat_context(self, chat_id, *, store_hint=None):
                    return {
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "store_name": "曼馨",
                            "ebk_watch_group_key": "manxin-ebk",
                        },
                        "ota_ebk_accounts": {
                            "ctrip": {"platform_account_id": "c1"},
                            "meituan": {"platform_account_id": "m1"},
                            "fliggy": {"platform_account_id": "f1"},
                        },
                    }

                async def get_ebk_joint_analysis_context(self, **kwargs):
                    return {
                        "status": "success",
                        "operator_summary": {
                            "intro": "三端经营简报",
                            "platforms": {"meituan": {"status": "available"}},
                        },
                    }

                async def send_operator_feishu_card(self, **kwargs):
                    return {"status": "success", "delivery": "app_bot"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                with mock.patch.dict(os.environ, {"PAN_HOTEL_REPLY_POLICY": "v2"}):
                    result = await mcp_server.call_tool(
                        "get_ebk_joint_analysis_context",
                        {"chat_id": "oc_group"},
                    )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))

        asyncio.run(scenario())

    def test_unknown_tool_returns_not_allowed_after_dispatch_refactor(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            result = await mcp_server.call_tool("not_a_real_tool", {})
            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "tool_not_allowed")

        asyncio.run(scenario())
    def test_safe_tool_error_read_timeout_for_browser_run(self) -> None:
        import httpx

        mcp_server = _load_mcp_server()
        payload = mcp_server._safe_tool_error("run_ebk_watch_once", httpx.ReadTimeout("timed out"))
        self.assertEqual(payload["status"], "failed")
        self.assertIn("超时", payload["summary"])
        self.assertIn("get_ebk_latest_result", payload.get("routing_hints") or [])

    def test_safe_tool_error_read_timeout_for_competitor_run_does_not_claim_background_persistence(self) -> None:
        import httpx

        mcp_server = _load_mcp_server()
        payload = mcp_server._safe_tool_error("run_competitor_watch_once", httpx.ReadTimeout("timed out"))
        summary = payload["summary"]
        self.assertEqual(payload["status"], "failed")
        self.assertIn("无法确认本次刷新是否完成", summary)
        self.assertNotIn("后台任务", summary)
        self.assertNotIn("仍在执行", summary)
        self.assertNotIn("持久化", summary)
        self.assertIn("get_competitor_latest_prices", payload.get("routing_hints") or [])
        self.assertNotIn("get_ebk_latest_result", payload.get("routing_hints") or [])

    def test_competitor_run_allowed_for_generic_agent_query(self) -> None:
        # 2026-10-09 通用模式：非刷新话法也放行 LLM 按需现读（成本由网关侧
        # 每轮预算控制）；Q&A 链路 push_to_feishu 恒为 False 的硬禁保持。
        captured: dict = {}

        async def scenario() -> dict:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_feishu_chat_context(self, *args, **kwargs):
                    return {"competitor_groups": [{"group_key": "default_competitor_watch"}]}

                async def run_competitor_watch_once(self, *args, **kwargs):
                    captured.update(kwargs)
                    captured["args"] = args
                    return {"status": "success", "summary": "读取完成"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "run_competitor_watch_once",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "chat_id": "oc_group",
                        "user_text": "竞品最近变化如何",
                    },
                )
            finally:
                mcp_server._client_instance = old_client
            return json.loads(result[0].text)

        payload = asyncio.run(scenario())
        self.assertEqual(payload["status"], "success")
        self.assertIs(captured.get("push_to_feishu"), False)

    def test_competitor_run_gate_after_llm_first_flip(self) -> None:
        # 2026-10-14 全量 LLM 先行：不再有词表安全通道。「查房价」问法同轮
        # 误调 run_competitor_watch_once 由插件层预算/LLM 判断控制，MCP 侧
        # 只保留空 user_text 的兜底拦截（无本轮问法不启浏览器现读）。
        async def blocked_scenario() -> dict:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def run_competitor_watch_once(self, **kwargs):
                    raise AssertionError("browser run must be blocked before client call")

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "run_competitor_watch_once",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "chat_id": "oc_group",
                        "user_text": "",
                    },
                )
            finally:
                mcp_server._client_instance = old_client
            return json.loads(result[0].text)

        payload = asyncio.run(blocked_scenario())
        self.assertEqual(payload["status"], "tool_not_allowed")

        async def allowed_scenario() -> dict:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_feishu_chat_context(self, *args, **kwargs):
                    return {"competitor_groups": [{"group_key": "default_competitor_watch"}]}

                async def run_competitor_watch_once(self, *args, **kwargs):
                    return {"status": "success", "summary": "读取完成"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "run_competitor_watch_once",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "chat_id": "oc_group",
                        "user_text": "查一下今天携程房价",
                    },
                )
            finally:
                mcp_server._client_instance = old_client
            return json.loads(result[0].text)

        payload = asyncio.run(allowed_scenario())
        self.assertEqual(payload["status"], "success")

    def test_dispatch_text_arg_composes_full_event(self) -> None:
        # 2026-10-14 生产实证：LLM 自组 payload 缺 message_id，后端
        # parse_command_context 硬性要求而直接忽略——查房价卡/调价审批卡
        # 链路全断。dispatch 改为优先接收 text=运营原话，由服务端组装
        # 完整事件（message_id/chat_id 从会话参数补齐）。
        async def scenario() -> dict:
            mcp_server = _load_mcp_server()
            captured: dict = {}

            class _StubClient:
                async def forward_feishu_event(self, payload):
                    captured["payload"] = payload
                    return {"status": "success", "summary": "已发房价情况表卡"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "dispatch_watch_feishu_event",
                    {
                        "text": "查下携程房价情况",
                        "chat_id": "oc_dispatch",
                        "message_id": "om_dispatch",
                    },
                )
            finally:
                mcp_server._client_instance = old_client
            captured["reply"] = json.loads(result[0].text)
            return captured

        captured = asyncio.run(scenario())
        payload = captured["payload"]
        self.assertEqual(payload["header"]["event_type"], "im.message.receive_v1")
        message = payload["event"]["message"]
        self.assertEqual(message["message_id"], "om_dispatch")
        self.assertEqual(message["chat_id"], "oc_dispatch")
        self.assertEqual(json.loads(message["content"])["text"], "查下携程房价情况")
        self.assertEqual(captured["reply"]["status"], "success")

    def test_dispatch_payload_missing_message_id_is_normalized(self) -> None:
        # LLM 传了自组 payload（缺 message_id、content 为 dict）时，
        # 服务端须补齐会话 message_id 并把 content 归一为 JSON 字符串。
        async def scenario() -> dict:
            mcp_server = _load_mcp_server()
            captured: dict = {}

            class _StubClient:
                async def forward_feishu_event(self, payload):
                    captured["payload"] = payload
                    return {"status": "success"}

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "dispatch_watch_feishu_event",
                    {
                        "payload": {
                            "header": {"event_type": "im.message.receive_v1"},
                            "event": {
                                "message": {
                                    "chat_id": "oc_norm",
                                    "content": {"text": "把今天大床房调到420"},
                                }
                            },
                        },
                        "chat_id": "oc_norm",
                        "message_id": "om_norm",
                    },
                )
            finally:
                mcp_server._client_instance = old_client
            captured["reply"] = json.loads(result[0].text)
            return captured

        captured = asyncio.run(scenario())
        message = captured["payload"]["event"]["message"]
        self.assertEqual(message["message_id"], "om_norm")
        self.assertEqual(message["chat_id"], "oc_norm")
        self.assertEqual(json.loads(message["content"])["text"], "把今天大床房调到420")

    def test_dispatch_requires_text_or_payload(self) -> None:
        async def scenario() -> dict:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def forward_feishu_event(self, payload):
                    raise AssertionError("dispatch must reject before client call")

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "dispatch_watch_feishu_event",
                    {"chat_id": "oc_missing", "message_id": "om_missing"},
                )
            finally:
                mcp_server._client_instance = old_client
            return json.loads(result[0].text)

        payload = asyncio.run(scenario())
        self.assertEqual(payload["status"], "invalid_arguments")

    def test_competitor_http_error_does_not_suggest_ebk_fallback(self) -> None:
        import httpx

        mcp_server = _load_mcp_server()
        request = httpx.Request("GET", "http://localhost")
        response = httpx.Response(502, request=request)
        payload = mcp_server._safe_tool_error(
            "get_competitor_latest_prices",
            httpx.HTTPStatusError("bad gateway", request=request, response=response),
        )
        self.assertNotIn("get_ebk_latest_result", payload.get("routing_hints") or [])
        self.assertNotIn("run_ebk_watch_once", payload.get("routing_hints") or [])
        self.assertIn("get_competitor_latest_prices", payload.get("routing_hints") or [])


class McpOperatorSummaryContractTests(unittest.TestCase):
    def test_tool_response_includes_operator_summary_contract(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_operator_analysis_context(self, **kwargs):
                    return {
                        "status": "success",
                        "kwargs": kwargs,
                        "operator_summary": {
                            "intro": "经营分析上下文已整理。",
                            "overview_rows": [{"field": "EBK", "value": "出租率 82%"}],
                        },
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_operator_analysis_context",
                    {"chat_id": "oc_group", "store_hint": "门店A"},
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            operator = payload["operator_summary"]
            self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
            self.assertEqual(operator["module"], "analysis")
            self.assertEqual(operator["intent"], "analysis")
            self.assertEqual(operator["conclusion"], "经营分析上下文已整理。")
            self.assertEqual(operator["evidence"][0], {"label": "EBK", "value": "出租率 82%", "kind": "metric"})
            self.assertEqual(operator["safety_boundary"]["level"], "read_only")

        asyncio.run(scenario())

    def test_unknown_tool_response_includes_operator_summary_contract(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            result = await mcp_server.call_tool("not_a_real_tool", {})
            payload = json.loads(result[0].text)
            operator = payload["operator_summary"]
            self.assertEqual(payload["status"], "tool_not_allowed")
            self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
            self.assertEqual(operator["status"], "tool_not_allowed")
            self.assertIn("not_a_real_tool", operator["conclusion"])
            self.assertEqual(operator["risk"]["level"], "attention")
            self.assertEqual(operator["safety_boundary"]["level"], "read_only")

        asyncio.run(scenario())

    def test_safe_tool_error_includes_operator_summary_contract(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def get_competitor_price_trend(self, watch_group_key, **kwargs):
                    raise RuntimeError("backend boom")

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_competitor_price_trend",
                    {
                        "watch_group_key": "default_competitor_watch",
                        "chat_id": "oc_group",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            operator = payload["operator_summary"]
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
            self.assertEqual(operator["status"], "failed")
            self.assertEqual(operator["risk"]["level"], "attention")
            self.assertEqual(operator["safety_boundary"]["level"], "read_only")

        asyncio.run(scenario())

    def test_context_schema_declares_user_text(self) -> None:
        mcp_server = _load_mcp_server()
        schema = mcp_server.MCP_TOOL_CONTEXT_SCHEMA
        self.assertIn("user_text", schema)
        self.assertEqual(schema["user_text"]["type"], "string")

    def test_all_tools_accept_injected_user_text(self) -> None:
        """Feishu session plugin injects user_text; closed schemas must declare it."""

        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            tools = await mcp_server.list_tools()
            missing: list[str] = []
            for tool in tools:
                schema = tool.inputSchema or {}
                props = schema.get("properties") or {}
                if tool.name in CONTEXT_FREE_TOOL_NAMES:
                    continue
                if schema.get("additionalProperties") is False and "user_text" not in props:
                    missing.append(tool.name)
            self.assertEqual(
                missing,
                [],
                msg=f"tools reject injected user_text: {missing}",
            )

        asyncio.run(scenario())

    def test_read_tool_result_is_not_rewritten_by_user_text_keywords(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient(_BoundContextClient):
                async def get_competitor_latest_prices(self, watch_group_key, **kwargs):
                    return {
                        "status": "success",
                        "watch_group_key": watch_group_key,
                        "feishu_card": {
                            "header": {"title": {"content": "竞品价格"}},
                            "elements": [],
                        },
                        "operator_summary": {"intro": "竞品现价"},
                    }

                async def get_competitor_latest(self, watch_group_key, **kwargs):
                    return await self.get_competitor_latest_prices(watch_group_key, **kwargs)

                async def send_operator_feishu_card(self, **kwargs):
                    raise AssertionError("must not send wrong spot-price card")

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                with mock.patch.dict(os.environ, {"PAN_HOTEL_REPLY_POLICY": "v2"}):
                    result = await mcp_server.call_tool(
                        "get_competitor_latest_prices",
                        {
                            "chat_id": "oc_group",
                            "watch_group_key": "default_competitor_watch",
                            "user_text": "竞品最近一天的价格涨幅情况",
                        },
                    )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("response_mode"), "text")
            self.assertFalse(payload.get("feishu_card_sent"))
            self.assertEqual(payload.get("status"), "success")
            self.assertNotIn("routing_hints", payload)

        asyncio.run(scenario())

    def test_get_ebk_period_read_tool_schema_and_handler(self) -> None:
        tools = _list_tools()
        tool = next(item for item in tools if item.name == "get_ebk_period_read")
        props = tool.inputSchema.get("properties", {})
        self.assertIn("platform", props)
        self.assertEqual(
            props["data_kind"]["enum"], ["business", "ranking", "pyramid", "flow"]
        )
        self.assertEqual(props["period"]["enum"], ["day", "week", "month"])
        self.assertEqual(tool.inputSchema.get("required"), ["data_kind", "period"])

        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            captured: dict[str, object] = {}

            class _StubClient:
                async def get_ebk_period_read(
                    self,
                    *,
                    platform=None,
                    data_kind="business",
                    period="week",
                    chat_id=None,
                    refresh=False,
                    date_from=None,
                    date_to=None,
                    range_preset=None,
                ) -> dict:
                    captured.update(
                        platform=platform,
                        data_kind=data_kind,
                        period=period,
                        chat_id=chat_id,
                    )
                    return {
                        "snapshot": {
                            "platform": platform or "ctrip",
                            "data_kind": data_kind,
                            "period": period,
                            "status": "ok",
                            "caliber": "预订销售·按周（自然周，较去年同期）",
                            "payload": {"sections": {}},
                        }
                    }

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()  # type: ignore[assignment]
            try:
                result = await mcp_server.call_tool(
                    "get_ebk_period_read",
                    {
                        "data_kind": "business",
                        "period": "week",
                        "chat_id": "oc_group",
                    },
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(
                captured,
                {
                    "platform": None,
                    "data_kind": "business",
                    "period": "week",
                    "chat_id": "oc_group",
                },
            )
            self.assertEqual(payload["snapshot"]["period"], "week")

        asyncio.run(scenario())

    def test_bi_daily_report_tool_schema_contract(self) -> None:
        """get_bi_daily_report：必填 chat_id、四板块枚举、horizon/lookback 上限与后端一致。"""
        tools = _list_tools()
        tool = next(item for item in tools if item.name == "get_bi_daily_report")
        self.assertIn("chat_id", tool.inputSchema.get("required", []))
        props = tool.inputSchema.get("properties", {})
        self.assertTrue({"chat_id", "message_id", "open_id", "user_text"} <= set(props))
        self.assertEqual(
            set(props["section"]["enum"]),
            {"forward", "performance", "weekday_profile", "quality"},
        )
        self.assertEqual(props["section"].get("default"), "forward")
        self.assertEqual(props["horizon_days"].get("maximum"), 60)
        self.assertEqual(props["lookback_days"].get("maximum"), 120)
        self.assertIn("read-only", str(tool.description or "").lower())
        self.assertIn("在手", str(tool.description or ""))
        self.assertIn("完成系数", str(tool.description or ""))

    def test_bi_daily_report_fails_closed_without_chat_id(self) -> None:
        async def scenario() -> None:
            mcp_server = _load_mcp_server()
            client = mock.Mock()
            client.get_bi_daily_report = mock.AsyncMock(return_value={"status": "ok"})

            old_client = mcp_server._client_instance
            mcp_server._client_instance = client
            try:
                result = await mcp_server.call_tool("get_bi_daily_report", {})
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "session_context_required")
            client.get_bi_daily_report.assert_not_awaited()

        asyncio.run(scenario())

    def test_bi_daily_report_handler_compacts_forward_section(self) -> None:
        """forward 板块：按日紧凑输出在手/竞争圈判定/去年同星期参照，丢掉 source 包装。"""

        def _metric(value, status="available", source="ctrip_market_analysis"):
            return {"value": value, "status": status, "source": source}

        report = {
            "shop_key": "shop_x",
            "as_of": "2026-10-12T09:20:35",
            "horizon_days": 14,
            "forward": [
                {
                    "stay_date": "2026-10-14",
                    "weekday": 3,
                    "own": {
                        "booked_room_nights": _metric(16),
                        "occupancy_rate": _metric(0.0914),
                        "pre_set_price": _metric(None, status="unavailable"),
                    },
                    "circle": {
                        "status": "available",
                        "my_occupancy_rate": 0.0914,
                        "competitor_occupancy_rate": 0.1157,
                        "verdict_label": "输给竞争圈",
                    },
                    "market": {"status": "available", "city_heat": {"value": 3.51}},
                    "prior_year": {
                        "mapped_date": "2025-10-15",
                        "matching_rule": "minus_364",
                        "actual": {"booked_room_nights": 40, "occupancy_rate": 0.22, "adr": 231.0},
                    },
                }
            ],
            "performance": [],
            "weekday_profile": [],
            "quality": {
                "coverage": {"dimensions": {"budget_forecast": {"status": "missing"}}},
                "metric_notes": ["n1"],
            },
        }

        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_bi_daily_report(self, **kwargs):
                    return report

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()
            try:
                result = await mcp_server.call_tool(
                    "get_bi_daily_report",
                    {"chat_id": "oc_group", "section": "forward"},
                )
            finally:
                mcp_server._client_instance = old_client

            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["section"], "forward")
            self.assertEqual(payload["coverage"], {"budget_forecast": "missing"})
            day = payload["days"][0]
            self.assertEqual(day["date"], "2026-10-14")
            self.assertEqual(day["own"]["booked_rooms"], 16)
            self.assertEqual(day["own"]["pre_set_price"], None)
            self.assertEqual(day["circle"]["verdict"], "输给竞争圈")
            self.assertEqual(day["prior_year_same_weekday"]["adr"], 231.0)
            self.assertNotIn("source", json.dumps(day))

        asyncio.run(scenario())

    def test_bi_daily_report_handler_compacts_performance_and_weekday(self) -> None:
        """performance/weekday_profile 板块：完成系数与缺基线原因必须保留。"""
        report = {
            "shop_key": "shop_x",
            "as_of": "2026-10-12T09:20:35",
            "forward": [],
            "performance": [
                {
                    "stay_date": "2026-10-10",
                    "weekday": 6,
                    "totals": {"booked_room_nights": 70, "room_revenue": 16800.0},
                    "prior_year": {
                        "mapped_date": "2025-10-11",
                        "actual": {"booked_room_nights": 36, "occupancy_rate": 0.54, "adr": 263.31},
                        "completion": {"value": 1.94, "status": "available", "reason": None},
                    },
                    "budget": {
                        "version": None,
                        "completion": {"value": None, "status": "unavailable", "reason": "budget_baseline_missing"},
                    },
                    "forecast": {
                        "demand_forecast_room_nights": None,
                        "completion": {"value": None, "status": "unavailable", "reason": "forecast_baseline_missing"},
                    },
                }
            ],
            "weekday_profile": [
                {
                    "weekday": 5,
                    "weekday_label": "周五",
                    "current": {"days": 1, "nights_sum": 46, "occupancy_avg": None, "adr_avg": 221.09},
                    "prior_year": {"days": 1, "nights_sum": 48, "occupancy_avg": None, "adr_avg": 262.14},
                    "completion": {"value": 0.958, "status": "available", "reason": None},
                }
            ],
            "quality": {
                "coverage": {"dimensions": {}},
                "metric_notes": ["出租率/均价同比取携程离店口径"],
            },
        }

        async def scenario() -> None:
            mcp_server = _load_mcp_server()

            class _StubClient:
                async def get_bi_daily_report(self, **kwargs):
                    return report

            old_client = mcp_server._client_instance
            mcp_server._client_instance = _StubClient()
            try:
                perf = json.loads(
                    (
                        await mcp_server.call_tool(
                            "get_bi_daily_report",
                            {"chat_id": "oc_group", "section": "performance"},
                        )
                    )[0].text
                )
                weekday = json.loads(
                    (
                        await mcp_server.call_tool(
                            "get_bi_daily_report",
                            {"chat_id": "oc_group", "section": "weekday_profile"},
                        )
                    )[0].text
                )
            finally:
                mcp_server._client_instance = old_client

            perf_day = perf["days"][0]
            self.assertEqual(perf_day["actual"]["room_nights"], 70)
            self.assertEqual(perf_day["prior_year"]["completion"], 1.94)
            self.assertEqual(
                perf_day["budget_completion"]["reason"], "budget_baseline_missing"
            )
            self.assertEqual(
                perf_day["forecast_completion"]["reason"], "forecast_baseline_missing"
            )
            wk = weekday["weekday_profile"][0]
            self.assertEqual(wk["weekday"], "周五")
            self.assertEqual(wk["current"]["adr_avg"], 221.09)
            self.assertEqual(wk["prior_year"]["adr_avg"], 262.14)
            self.assertEqual(wk["completion"]["value"], 0.958)

        asyncio.run(scenario())

    def test_main_forces_utf8_stdio_for_windows_pipe(self) -> None:
        """Windows 管道默认 GBK 会把中文 JSON-RPC 帧写坏；main 入口必须先强制 UTF-8。"""
        import io

        mcp_server = _load_mcp_server()
        self.assertTrue(hasattr(mcp_server, "_force_utf8_stdio"))

        gbk_out = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
        plain_out = io.StringIO()  # 无 reconfigure 的流不能因此崩溃
        original = (sys.stdout, sys.stderr)
        sys.stdout = gbk_out
        sys.stderr = plain_out
        try:
            mcp_server._force_utf8_stdio()
        finally:
            sys.stdout, sys.stderr = original
        self.assertEqual(gbk_out.encoding.lower(), "utf-8")


if __name__ == "__main__":
    unittest.main()
