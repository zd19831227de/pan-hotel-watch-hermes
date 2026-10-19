from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest import mock

import httpx

from client.watch_api import WatchApiClient


class WatchApiAggregateTests(unittest.TestCase):
    def test_get_watch_group_catalog_only_returns_current_chat_groups(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                self.assertEqual(path, "/api/v1/ops/feishu-chat-context")
                self.assertEqual(params, {"chat_id": "oc_group_a"})
                return _chat_a_context()

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_watch_group_catalog(chat_id="oc_group_a")

            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertEqual(payload["status"], "success")
            self.assertIn("ebk_group_a", serialized)
            self.assertIn("competitor_group_a", serialized)
            self.assertNotIn("group_b", serialized)
            self.assertNotIn("defaults", payload)
            self.assertNotIn("oc_raw_group_a", serialized)
            self.assertNotIn("secret-webhook-a", serialized)
            self.assertNotIn("cli_sensitive_app_a", serialized)

        asyncio.run(scenario())

    def test_get_operator_watch_configuration_only_fetches_current_chat_groups(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            called_paths: list[str] = []

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                called_paths.append(path)
                if path == "/api/v1/ops/feishu-chat-context":
                    self.assertEqual(params, {"chat_id": "oc_group_a"})
                    return _chat_a_context()
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups":
                    return {"watch_groups": [_ebk_group("a"), _ebk_group("b")]}
                if path == "/api/v1/ctrip-ebooking/competitor-watch-groups":
                    return {
                        "watch_groups": [
                            _competitor_group("a"),
                            _competitor_group("b"),
                        ]
                    }
                if path == "/api/v1/browser-sessions":
                    return {"sessions": [_browser_session("a"), _browser_session("b")]}
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups/ebk_group_a":
                    return {"watch_group": _ebk_group("a")}
                if path == "/api/v1/ctrip-ebooking/competitor-watch-groups/competitor_group_a":
                    return {"watch_group": _competitor_group("a")}
                if path == "/api/v1/browser-sessions/browser_a":
                    return _browser_session("a")
                if path == "/api/v1/ops/runtime-health":
                    return {
                        "summary": {"status": "fresh", "attention_modules": []},
                        "modules": {
                            "competitor_price_scheduler": {
                                "display_name": "Hotel B competitor scheduler",
                                "freshness_status": "fresh",
                                "last_tick_summary": {
                                    "last_watch_group_keys": ["competitor_group_b"]
                                },
                            }
                        },
                        "operator_summary": {
                            "overall_status": "fresh",
                            "module_rows": [
                                {
                                    "field": "Competitor scheduler",
                                    "value": "fresh / group competitor_group_b / Hotel B",
                                }
                            ],
                        },
                    }
                if path.endswith("/competitor_group_a/hotels"):
                    return {"hotels": [_hotel("a")]}
                if path.endswith("/competitor_group_b/hotels"):
                    return {"hotels": [_hotel("b")]}
                raise AssertionError(f"unexpected path: {path}")

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_operator_watch_configuration(
                    chat_id="oc_group_a",
                    include_runtime_health=True,
                )

            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertIn("Hotel A", serialized)
            self.assertIn("ebk_group_a", serialized)
            self.assertIn("competitor_group_a", serialized)
            self.assertNotIn("Hotel B", serialized)
            self.assertNotIn("group_b", serialized)
            self.assertNotIn("account_b", serialized)
            self.assertNotIn("oc_raw_group_a", serialized)
            self.assertNotIn("secret-webhook-a", serialized)
            self.assertNotIn("cli_sensitive_app_a", serialized)
            self.assertNotIn("browser-profile-a", serialized)
            self.assertEqual(payload["operator_summary"]["runtime_status"], "fresh")
            self.assertNotIn(
                "/api/v1/ctrip-ebooking/data-center-watch-groups",
                called_paths,
            )
            self.assertNotIn(
                "/api/v1/ctrip-ebooking/competitor-watch-groups",
                called_paths,
            )
            self.assertNotIn("/api/v1/browser-sessions", called_paths)

        asyncio.run(scenario())

    def test_get_operator_watch_configuration_without_chat_fails_before_api_calls(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            get_json = mock.AsyncMock(side_effect=AssertionError("must not call global API"))

            with mock.patch.object(client, "get_json", new=get_json):
                payload = await client.get_operator_watch_configuration()

            self.assertEqual(payload["status"], "session_context_required")
            get_json.assert_not_awaited()

        asyncio.run(scenario())

    def test_get_admin_operator_watch_configuration_aggregates_multiple_endpoints(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups":
                    return {
                        "watch_groups": [
                            {
                                "group_key": "ctrip_ebooking_data_center",
                                "name": "EBK 数据中心",
                                "browser_session_id": "browser-ebk-1",
                                "enabled": True,
                                "auto_poll_enabled": False,
                                "datasets": [],
                            }
                        ]
                    }
                if path == "/api/v1/ctrip-ebooking/competitor-watch-groups":
                    return {
                        "watch_groups": [
                            {
                                "group_key": "default_competitor_watch",
                                "name": "竞品组",
                                "browser_session_id": "browser-trip-1",
                                "browser_rotation_mode": "pool_round_robin",
                                "browser_pool": {
                                    "pool_enabled": True,
                                    "pool_size": 2,
                                    "usable_pool_size": 2,
                                    "members": [{"browser_session_id": "browser-trip-1"}],
                                },
                                "pricing_browser_session_id": "browser-ebk-1",
                                "feishu_chat_id": "oc_secret_delivery_group",
                                "enabled": True,
                                "auto_poll_enabled": True,
                            }
                        ]
                    }
                if path == "/api/v1/browser-sessions":
                    return {
                        "sessions": [
                            {
                                "browser_session_id": "browser-ebk-1",
                                "display_name": "默认EBK",
                            }
                        ]
                    }
                if path.endswith("/hotels"):
                    return {
                        "hotels": [
                            {"role": "own", "alias": "本店", "enabled": True},
                            {"role": "competitor", "alias": "竞对A", "enabled": True},
                        ]
                    }
                raise AssertionError(f"unexpected path: {path}")

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_admin_operator_watch_configuration(
                    admin_authorized=True,
                )

            operator = payload["operator_summary"]
            self.assertEqual(operator["ebk_sections"][0]["ebk_account_display_name"], "默认EBK")
            self.assertEqual(operator["competitor_sections"][0]["competitor_hotels"], ["竞对A"])
            self.assertEqual(operator["competitor_sections"][0]["browser_rotation_mode"], "pool_round_robin")
            self.assertEqual(operator["competitor_sections"][0]["browser_pool"]["usable_pool_size"], 2)
            self.assertEqual(operator["competitor_sections"][0]["pricing_browser_session_id"], "browser-ebk-1")
            self.assertNotIn("oc_secret_delivery_group", str(operator))
            self.assertTrue(payload.get("feishu_card"))
            self.assertEqual(payload.get("feishu_card_kind"), "watch_configuration")

        asyncio.run(scenario())

    def test_get_admin_operator_watch_configuration_requires_authorization(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            get_json = mock.AsyncMock(side_effect=AssertionError("must not call global API"))

            with mock.patch.object(client, "get_json", new=get_json):
                payload = await client.get_admin_operator_watch_configuration()

            self.assertEqual(payload["status"], "admin_auth_required")
            get_json.assert_not_awaited()

        asyncio.run(scenario())

    def test_get_operator_watch_configuration_with_chat_id_merges_binding_and_runtime(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                if path == "/api/v1/ops/feishu-chat-context":
                    self.assertEqual(params, {"chat_id": "oc_group"})
                    return {
                        "status": "success",
                        "chat_id_configured": True,
                        "binding_mode": "single_store",
                        "resolution": {
                            "status": "resolved",
                            "store_label": "泉吉云州澜江路步行街高空江景酒店",
                            "ebk_watch_group_key": "ebk_1782812373623",
                            "competitor_watch_group_key": "default_competitor_watch",
                        },
                        "operator_summary": {
                            "intro": "本群已绑定泉吉。",
                            "overview_rows": [{"field": "EBK 后台账号", "value": "云州泉吉EBK"}],
                        },
                    }
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups/ebk_1782812373623":
                    return {
                        "watch_group": {
                            "group_key": "ebk_1782812373623",
                            "name": "泉吉 EBK",
                            "enabled": True,
                        }
                    }
                if path == "/api/v1/ctrip-ebooking/competitor-watch-groups/default_competitor_watch":
                    return {
                        "watch_group": {
                            "group_key": "default_competitor_watch",
                            "name": "泉吉竞品",
                            "enabled": True,
                        }
                    }
                if path == "/api/v1/ctrip-ebooking/competitor-watch-groups/default_competitor_watch/hotels":
                    return {"hotels": []}
                raise AssertionError(f"unexpected path: {path}")

            with (
                mock.patch.object(client, "get_json", side_effect=fake_get_json),
                mock.patch.object(
                    client,
                    "runtime_health",
                    new=mock.AsyncMock(
                        return_value={
                            "operator_summary": {
                                "overall_status": "fresh",
                                "module_rows": [{"field": "竞品轮询", "value": "fresh"}],
                            }
                        }
                    ),
                ),
            ):
                payload = await client.get_operator_watch_configuration(
                    chat_id="oc_group",
                    include_runtime_health=True,
                )

            operator = payload["operator_summary"]
            self.assertEqual(operator["chat_binding_status"], "resolved")
            self.assertIn("泉吉云州澜江路", operator["chat_binding_intro"])
            self.assertEqual(operator["runtime_status"], "fresh")
            self.assertEqual(operator["runtime_module_rows"][0]["field"], "运行模块 1")
            self.assertIn("禁止再说本群未绑定", operator["routing_hints"][0])

        asyncio.run(scenario())

    def test_get_feishu_chat_context_aggregates_ops_endpoint(self) -> None:
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
                        "competitor_hotels": ["竞对A", "竞对B"],
                        "pricing_ebk_display_name": "云州泉吉EBK",
                        "operator_summary": {
                            "intro": "本群已绑定。",
                            "overview_rows": [
                                {"field": "EBK 后台账号", "value": "云州泉吉EBK"},
                                {"field": "竞对酒店", "value": "竞对A、竞对B"},
                            ],
                        },
                    }
                ),
            ):
                payload = await client.get_feishu_chat_context("oc_group")

            self.assertTrue(payload["chat_id_configured"])
            rows = payload["operator_summary"]["overview_rows"]
            self.assertEqual(rows[1]["value"], "竞对A、竞对B")
            self.assertEqual(payload["feishu_card_kind"], "chat_context")

        asyncio.run(scenario())

    def test_get_operator_hotel_overview_aggregates_four_sources(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "runtime_health",
                    new=mock.AsyncMock(
                        return_value={
                            "checked_at": "2026-06-25T09:00:00+00:00",
                            "operator_summary": {
                                "overall_status": "fresh",
                                "attention_modules": [],
                                "module_rows": [{"field": "竞品轮询", "value": "fresh"}],
                            },
                        }
                    ),
                ),
                mock.patch.object(
                    client,
                    "get_ebk_latest",
                    new=mock.AsyncMock(
                        return_value={
                            "operator_summary": {
                                "review_summary": {
                                    "available": True,
                                    "positive_reviews_yesterday": "3",
                                    "negative_reviews_yesterday": "0",
                                },
                                "beneficial_metrics": [{"field": "出租率", "value": "85%"}],
                                "latest_captured_at": "2026-06-25T08:00:00+00:00",
                            }
                        }
                    ),
                ),
                mock.patch.object(
                    client,
                    "get_competitor_latest",
                    new=mock.AsyncMock(
                        return_value={
                            "operator_summary": {
                                "price_rows": [{"field": "竞对A · 今天", "value": "420元"}],
                                "latest_captured_at": "2026-06-25T08:30:00+00:00",
                            }
                        }
                    ),
                ),
                mock.patch.object(
                    client,
                    "get_price_change_alert_latest",
                    new=mock.AsyncMock(
                        return_value={
                            "operator_summary": {
                                "alert_status": "no_change",
                                "summary": "暂无新变化",
                            }
                        }
                    ),
                ),
            ):
                payload = await client.get_operator_hotel_overview()

            operator = payload["operator_summary"]
            fields = {row["field"]: row["value"] for row in operator["overview_rows"]}
            self.assertEqual(fields["昨日好评"], "3")
            self.assertIn("420元", fields.get("竞对A · 今天", ""))
            self.assertEqual(payload["feishu_card_kind"], "hotel_overview")

        asyncio.run(scenario())

    def test_hotel_overview_skips_unbound_competitor_without_default_fallback(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            competitor = mock.AsyncMock()
            price_change = mock.AsyncMock()
            with (
                mock.patch.object(
                    client,
                    "runtime_health",
                    new=mock.AsyncMock(
                        return_value={
                            "operator_summary": {
                                "overall_status": "fresh",
                                "attention_modules": [],
                                "module_rows": [],
                            },
                        }
                    ),
                ),
                mock.patch.object(
                    client,
                    "get_ebk_latest",
                    new=mock.AsyncMock(
                        return_value={
                            "operator_summary": {
                                "review_summary": {"available": False},
                                "beneficial_metrics": [{"field": "出租率", "value": "85%"}],
                            }
                        }
                    ),
                ),
                mock.patch.object(client, "get_competitor_latest", new=competitor),
                mock.patch.object(client, "get_price_change_alert_latest", new=price_change),
            ):
                payload = await client.get_operator_hotel_overview(
                    ebk_watch_group_key="ctrip_ebooking_data_center",
                    competitor_watch_group_key=None,
                    allow_default_groups=False,
                )

            competitor.assert_not_awaited()
            price_change.assert_not_awaited()
            fields = {
                row["field"]: row["value"]
                for row in payload["operator_summary"]["overview_rows"]
            }
            self.assertEqual(fields["出租率"], "85%")
            self.assertIn("竞品监控组未绑定", fields["价格变化"])

        asyncio.run(scenario())

    def test_get_operator_analysis_context_returns_compact_evidence_package(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "get_feishu_chat_context",
                    new=mock.AsyncMock(
                        return_value={
                            "binding_mode": "single_store",
                            "resolution": {
                                "status": "resolved",
                                "store_name": "静里全套房酒店",
                                "ebk_watch_group_key": "ctrip_ebooking_data_center",
                                "competitor_watch_group_key": "watch_1782109266413",
                            },
                        }
                    ),
                ),
                mock.patch.object(
                    client,
                    "get_operator_hotel_overview",
                    new=mock.AsyncMock(
                        return_value={
                            "operator_summary": {
                                "intro": "酒店今日概览",
                                "overview_rows": [
                                    {"field": "出租率", "value": "82%"},
                                    {"field": "竞品A · 今天", "value": "大床 / 无早 / ¥399"},
                                ],
                                "next_actions": ["如需最新 EBK，请现读销售数据。"],
                            }
                        }
                    ),
                ),
                mock.patch.object(
                    client,
                    "get_operator_recent_activity",
                    new=mock.AsyncMock(
                        return_value={
                            "operator_summary": {
                                "overview_rows": [
                                    {"field": "最近 EBK", "value": "09:20 成功"},
                                ]
                            }
                        }
                    ),
                ),
            ):
                payload = await client.get_operator_analysis_context(
                    chat_id="oc_group",
                    store_hint="静里",
                )

            self.assertEqual(payload["status"], "success")
            operator = payload["operator_summary"]
            self.assertEqual(operator["store_name"], "静里全套房酒店")
            self.assertIn("已证实数据", operator["analysis_sections"])
            self.assertIn("可能原因", operator["analysis_sections"])
            self.assertIn("建议动作", operator["analysis_sections"])
            self.assertIn("数据缺口", operator["analysis_sections"])
            source_by_key = {source["source_key"]: source for source in operator["sources"]}
            self.assertEqual(source_by_key["ctrip_ebk"]["status"], "available")
            self.assertEqual(source_by_key["ctrip_competitor"]["status"], "available")
            self.assertEqual(source_by_key["meituan_ebk"]["status"], "snapshot_backed")
            self.assertEqual(source_by_key["fliggy_ebk"]["status"], "snapshot_backed")
            self.assertIn("get_ebk_joint_analysis_context", source_by_key["meituan_ebk"]["usage_note"])
            self.assertIn("get_ebk_joint_analysis_context", source_by_key["fliggy_ebk"]["usage_note"])
            self.assertTrue(operator["hypothesis_prompts"])
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False)), 6000)

        asyncio.run(scenario())

    def test_read_ebk_datasets_runs_requested_dataset_even_when_watch_dataset_disabled(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            calls: list[dict[str, object]] = []

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                calls.append({"method": "get", "path": path, "params": params})
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups/ctrip_ebooking_data_center":
                    return {
                        "watch_group": {
                            "group_key": "ctrip_ebooking_data_center",
                            "platform_account_id": "ctrip-ebk",
                            "browser_session_id": "browser-ebk",
                            "enabled": True,
                            "datasets": [
                                {
                                    "dataset_id": "beneficial_data",
                                    "dataset_label": "销售数据",
                                    "enabled": False,
                                    "push_enabled": False,
                                }
                            ],
                        }
                    }
                raise AssertionError(f"unexpected GET path: {path}")

            async def fake_post_json(
                path: str,
                *,
                body: dict | None = None,
                params: dict | None = None,
                **_: object,
            ) -> dict:
                calls.append({"method": "post", "path": path, "body": body, "params": params})
                if path == "/api/v1/ctrip-ebooking/sessions/ctrip-ebk/read-dataset":
                    self.assertEqual(body["dataset_id"], "beneficial_data")
                    self.assertEqual(body["browser_session_id"], "browser-ebk")
                    self.assertFalse(body["push_to_feishu"])
                    return {
                        "status": "success",
                        "summary": "销售数据读取成功",
                        "dataset_id": "beneficial_data",
                        "data": {
                            "metrics": {
                                "occupancy_rate": {
                                    "key": "occupancy_rate",
                                    "label": "出租率",
                                    "value": "30.41%",
                                    "display_value": "30.41%",
                                }
                            }
                        },
                    }
                raise AssertionError(f"unexpected POST path: {path}")

            with (
                mock.patch.object(client, "get_json", side_effect=fake_get_json),
                mock.patch.object(client, "post_json", side_effect=fake_post_json),
            ):
                payload = await client.read_ebk_datasets(
                    watch_group_key="ctrip_ebooking_data_center",
                    dataset_ids=None,
                    metric_keys=["occupancy_rate"],
                    actor_id="ou_operator",
                    chat_id="oc_group",
                    operator_id="ou_operator",
                )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["source"], "browser_dataset_read")
            self.assertEqual(payload["operator_summary"]["beneficial_metrics"][0]["field"], "出租率")
            self.assertEqual(payload["operator_summary"]["beneficial_metrics"][0]["value"], "30.41%")
            self.assertEqual(payload["feishu_card_kind"], "ebk_datasets_read")
            post_calls = [item for item in calls if item["method"] == "post"]
            self.assertEqual(len(post_calls), 1)

        asyncio.run(scenario())

    def test_read_ebk_datasets_falls_back_to_latest_when_browser_read_http_error(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            request = httpx.Request("POST", "http://127.0.0.1:8000/read-dataset")
            response = httpx.Response(404, request=request)

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups/ctrip_ebooking_data_center":
                    return {
                        "watch_group": {
                            "group_key": "ctrip_ebooking_data_center",
                            "platform_account_id": "ebk-2",
                            "browser_session_id": "browser-ebk-2",
                            "enabled": True,
                            "datasets": [
                                {
                                    "dataset_id": "beneficial_data",
                                    "dataset_label": "销售数据",
                                    "enabled": True,
                                }
                            ],
                        }
                    }
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups/ctrip_ebooking_data_center/latest":
                    return {
                        "watch_group_key": "ctrip_ebooking_data_center",
                        "run": {
                            "status": "success",
                            "captured_at": "2026-06-29T15:48:13.163162",
                        },
                        "dataset_results": [
                            {
                                "dataset_id": "beneficial_data",
                                "status": "success",
                                "captured_at": "2026-06-29T15:46:15.141908",
                                "data": {
                                    "metrics": {
                                        "occupancy_rate": {
                                            "label": "出租率",
                                            "value": "82%",
                                            "display_value": "82%",
                                        }
                                    }
                                },
                            }
                        ],
                    }
                raise AssertionError(f"unexpected GET path: {path}")

            async def fake_post_json(
                path: str,
                *,
                body: dict | None = None,
                params: dict | None = None,
                **_: object,
            ) -> dict:
                raise httpx.HTTPStatusError("not found", request=request, response=response)

            with (
                mock.patch.object(client, "get_json", side_effect=fake_get_json),
                mock.patch.object(client, "post_json", side_effect=fake_post_json),
            ):
                payload = await client.read_ebk_datasets(
                    watch_group_key="ctrip_ebooking_data_center",
                    dataset_ids=None,
                    metric_keys=["occupancy_rate"],
                    actor_id="ou_operator",
                )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["source"], "persisted_latest_fallback")
            self.assertIn("82%", payload["summary"])
            self.assertEqual(payload["operator_summary"]["beneficial_metrics"][0]["field"], "出租率")

        asyncio.run(scenario())

    def test_read_ebk_datasets_falls_back_to_latest_when_browser_read_connection_error(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            request = httpx.Request("POST", "http://127.0.0.1:8000/read-dataset")

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups/ctrip_ebooking_data_center":
                    return {
                        "watch_group": {
                            "group_key": "ctrip_ebooking_data_center",
                            "platform_account_id": "ebk-2",
                            "browser_session_id": "browser-ebk-2",
                            "enabled": True,
                            "datasets": [
                                {
                                    "dataset_id": "beneficial_data",
                                    "dataset_label": "Sales data",
                                    "enabled": True,
                                }
                            ],
                        }
                    }
                if path == "/api/v1/ctrip-ebooking/data-center-watch-groups/ctrip_ebooking_data_center/latest":
                    return {
                        "watch_group_key": "ctrip_ebooking_data_center",
                        "run": {
                            "status": "success",
                            "captured_at": "2026-06-29T15:48:13.163162",
                        },
                        "dataset_results": [
                            {
                                "dataset_id": "beneficial_data",
                                "status": "success",
                                "captured_at": "2026-06-29T15:46:15.141908",
                                "data": {
                                    "metrics": {
                                        "occupancy_rate": {
                                            "label": "Occupancy rate",
                                            "value": "82%",
                                            "display_value": "82%",
                                        }
                                    }
                                },
                            }
                        ],
                    }
                raise AssertionError(f"unexpected GET path: {path}")

            async def fake_post_json(
                path: str,
                *,
                body: dict | None = None,
                params: dict | None = None,
                **_: object,
            ) -> dict:
                raise httpx.ConnectError("backend unavailable", request=request)

            with (
                mock.patch.object(client, "get_json", side_effect=fake_get_json),
                mock.patch.object(client, "post_json", side_effect=fake_post_json),
            ):
                payload = await client.read_ebk_datasets(
                    watch_group_key="ctrip_ebooking_data_center",
                    dataset_ids=None,
                    metric_keys=["occupancy_rate"],
                    actor_id="ou_operator",
                )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["source"], "persisted_latest_fallback")
            self.assertIn("82%", payload["summary"])
            self.assertIn("ConnectError", payload["summary"])

        asyncio.run(scenario())

    def test_get_competitor_price_trend_calls_backend_trend_endpoint(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            with mock.patch.object(
                client,
                "get_json",
                new=mock.AsyncMock(
                    return_value={
                        "watch_group_key": "default_competitor_watch",
                        "days": 3,
                        "granularity": "run",
                        "trusted_source": "room_rate_dom_display_price",
                        "points": [],
                    }
                ),
            ) as get_json:
                payload = await client.get_competitor_price_trend(
                    "default_competitor_watch",
                    days=3,
                    granularity="run",
                )

            get_json.assert_awaited_once_with(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/default_competitor_watch/price-trend",
                params={"days": 3, "granularity": "run"},
            )
            operator = payload["operator_summary"]
            self.assertEqual(operator["watch_kind"], "competitor")
            self.assertIn("最近3天", operator["intro"])

        asyncio.run(scenario())

    def test_get_competitor_price_trend_summarizes_trusted_points_for_operator(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(
                client,
                "get_json",
                new=mock.AsyncMock(
                    return_value={
                        "watch_group_key": "default_competitor_watch",
                        "days": 3,
                        "granularity": "run",
                        "trusted_source": "room_rate_dom_display_price",
                        "series": [
                            {
                                "hotel_name": "澜江路泉吉酒店",
                                "room_type": "高级大床房",
                                "date_label": "today",
                                "breakfast_kind": "no_breakfast",
                                "points": [
                                    {
                                        "captured_at": "2026-06-25T10:00:00+08:00",
                                        "price_amount_yuan": 430,
                                    },
                                    {
                                        "captured_at": "2026-06-27T16:01:00+08:00",
                                        "price_amount_yuan": 398,
                                    },
                                ],
                            }
                        ],
                    }
                ),
            ):
                payload = await client.get_competitor_price_trend(
                    "default_competitor_watch",
                    days=3,
                    granularity="run",
                )

            operator = payload["operator_summary"]
            self.assertIn("可信 DOM 房价趋势", operator["caliber_note"])
            self.assertIn("澜江路泉吉酒店", str(operator["trend_rows"]))
            self.assertIn("398", str(operator["trend_rows"]))
            row = operator["trend_rows"][0]
            self.assertEqual(row["hotel"], "澜江路泉吉酒店")
            self.assertEqual(row["stay"], "今日无早")
            self.assertEqual(row["room_type"], "高级大床房")
            self.assertEqual(row["observation_count"], 2)
            self.assertEqual(row["price_min"], "398元")
            self.assertEqual(row["price_max"], "430元")
            self.assertEqual(row["latest_price"], "398元")
            self.assertIn("430", row["process"])
            self.assertIn("398", row["process"])
            self.assertNotIn("room_rate_dom_display_price", str(operator))
            self.assertEqual(payload["feishu_card_kind"], "competitor_price_trend")

        asyncio.run(scenario())

    def test_manual_pricing_approval_payload_includes_store_hint(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            calls: list[dict[str, Any]] = []

            async def fake_forward(payload: dict[str, Any]) -> dict[str, Any]:
                calls.append(payload)
                return {"status": "approval_card_replied"}

            with mock.patch.object(client, "forward_feishu_event", side_effect=fake_forward):
                await client.create_manual_pricing_approval(
                    room_type="童趣双床套房",
                    date_label="today",
                    target_price=960,
                    watch_group_key=None,
                    chat_id="oc_group",
                    message_id="om_1",
                    open_id="ou_1",
                    store_hint="静里",
                )

            event = calls[0]["event"]
            self.assertEqual(event["message"]["store_hint"], "静里")
            content = json.loads(event["message"]["content"])
            self.assertEqual(content["text"], "把今天童趣双床套房调到960")

        asyncio.run(scenario())


def _chat_a_context() -> dict[str, Any]:
    return {
        "status": "success",
        "chat_id_configured": True,
        "binding_mode": "single_store",
        "resolution": {
            "status": "resolved",
            "store_label": "Hotel A",
            "ebk_watch_group_key": "ebk_group_a",
            "competitor_watch_group_key": "competitor_group_a",
        },
        "ebk_groups": [
            {
                "group_key": "ebk_group_a",
                "name": "Hotel A EBK",
                "feishu_chat_id": "oc_raw_group_a",
                "feishu_webhook_url": "https://example.invalid/secret-webhook-a",
                "feishu_app_id": "cli_sensitive_app_a",
            }
        ],
        "competitor_groups": [
            {
                "group_key": "competitor_group_a",
                "name": "Hotel A Competitors",
                "feishu_chat_id": "oc_raw_group_a",
                "feishu_webhook_url": "https://example.invalid/secret-webhook-a",
                "feishu_app_id": "cli_sensitive_app_a",
            }
        ],
        "operator_summary": {"intro": "Current chat is bound to Hotel A."},
    }


def _ebk_group(suffix: str) -> dict[str, Any]:
    return {
        "group_key": f"ebk_group_{suffix}",
        "name": f"Hotel {suffix.upper()} EBK",
        "browser_session_id": f"browser_{suffix}",
        "platform_account_id": f"account_{suffix}",
        "enabled": True,
        "auto_poll_enabled": True,
        "datasets": [],
        "feishu_chat_id": f"oc_raw_group_{suffix}",
        "feishu_webhook_url": f"https://example.invalid/secret-webhook-{suffix}",
        "feishu_app_id": f"cli_sensitive_app_{suffix}",
    }


def _competitor_group(suffix: str) -> dict[str, Any]:
    return {
        "group_key": f"competitor_group_{suffix}",
        "name": f"Hotel {suffix.upper()} Competitors",
        "browser_session_id": f"browser_{suffix}",
        "platform_account_id": f"competitor_account_{suffix}",
        "enabled": True,
        "auto_poll_enabled": True,
        "feishu_chat_id": f"oc_raw_group_{suffix}",
        "feishu_webhook_url": f"https://example.invalid/secret-webhook-{suffix}",
        "feishu_app_id": f"cli_sensitive_app_{suffix}",
    }


def _browser_session(suffix: str) -> dict[str, Any]:
    return {
        "browser_session_id": f"browser_{suffix}",
        "display_name": f"Hotel {suffix.upper()} Browser",
        "platform_account_id": f"account_{suffix}",
        "intended_use": "ebk",
        "browser_profile_path": f"C:/sensitive/browser-profile-{suffix}",
    }


def _hotel(suffix: str) -> dict[str, Any]:
    return {
        "role": "own",
        "alias": f"Hotel {suffix.upper()}",
        "enabled": True,
    }


class HotelAnalyticsClientTests(unittest.TestCase):
    """V5 Step 2 — supervisor-gated cross-profile analytics client."""

    def test_headers_include_supervisor_key_only_when_configured(self) -> None:
        client = WatchApiClient(base_url="http://127.0.0.1:8000")
        with mock.patch.dict(
            "os.environ", {"WATCH_API_SUPERVISOR_KEY": "supervisor-secret"}, clear=False
        ):
            headers = client._headers()
        self.assertEqual(headers["X-Pan-Hotel-API-Key"], "supervisor-secret")

        with mock.patch.dict(
            "os.environ", {"WATCH_API_SUPERVISOR_KEY": ""}, clear=True
        ):
            headers = client._headers()
        self.assertNotIn("X-Pan-Hotel-API-Key", headers)

    def test_get_hotel_analytics_overview_passes_chat_id_scope_and_profile_keys(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            captured: dict[str, Any] = {}

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                captured["path"] = path
                captured["params"] = params
                return {
                    "caller_role": "supervisor",
                    "scope": "selected",
                    "hotels": [{"profile_key": "jingli-ai"}],
                }

            with (
                mock.patch.object(client, "get_json", side_effect=fake_get_json),
                mock.patch.dict(
                    "os.environ",
                    {"WATCH_API_SUPERVISOR_KEY": "supervisor-secret"},
                    clear=False,
                ),
            ):
                payload = await client.get_hotel_analytics_overview(
                    chat_id="oc_room",
                    scope="selected",
                    profile_keys=["jingli-ai", "manxin-ai"],
                )

            self.assertEqual(captured["path"], "/api/v1/analytics/hotel-overview")
            self.assertEqual(captured["params"]["chat_id"], "oc_room")
            self.assertEqual(captured["params"]["scope"], "selected")
            self.assertEqual(captured["params"]["profile_keys"], ["jingli-ai", "manxin-ai"])
            self.assertEqual(payload["scope"], "selected")

        asyncio.run(scenario())

    def test_get_hotel_analytics_overview_returns_config_required_without_key(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("get_json must not be called without supervisor key")

            with (
                mock.patch.object(client, "get_json", side_effect=boom),
                mock.patch.dict("os.environ", {"WATCH_API_SUPERVISOR_KEY": ""}, clear=True),
            ):
                payload = await client.get_hotel_analytics_overview(chat_id="oc_room", scope="all")

            self.assertEqual(payload["status"], "config_required")
            self.assertIn("WATCH_API_SUPERVISOR_KEY", payload["summary"])

        asyncio.run(scenario())

    def test_get_hotel_analytics_overview_fail_closed_on_403(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            request = httpx.Request("GET", "http://127.0.0.1:8000/api/v1/analytics/hotel-overview")
            response = httpx.Response(
                403, request=request, json={"detail": "权限不足：仅 supervisor 可查询跨店数据。"}
            )

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                raise httpx.HTTPStatusError("forbidden", request=request, response=response)

            with (
                mock.patch.object(client, "get_json", side_effect=fake_get_json),
                mock.patch.dict(
                    "os.environ",
                    {"WATCH_API_SUPERVISOR_KEY": "supervisor-secret"},
                    clear=False,
                ),
            ):
                payload = await client.get_hotel_analytics_overview(chat_id="oc_room", scope="all")

            self.assertEqual(payload["status"], "permission_denied")
            self.assertIn("supervisor", payload["summary"])

        asyncio.run(scenario())

    def test_get_hotel_analytics_overview_requires_chat_id(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.dict(
                "os.environ", {"WATCH_API_SUPERVISOR_KEY": "supervisor-secret"}, clear=False
            ):
                payload = await client.get_hotel_analytics_overview(chat_id="", scope="own")
            self.assertEqual(payload["status"], "session_context_required")

        asyncio.run(scenario())

    def test_search_rag_knowledge_base_posts_query_and_returns_results(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            captured: dict[str, Any] = {}

            async def fake_post_json(path: str, *, body: dict | None = None) -> dict:
                captured["path"] = path
                captured["body"] = body
                return {"results": [{"title": "退款政策", "chunk_text": "入住前24h免费取消", "score": 0.9}]}

            with (
                mock.patch.object(client, "post_json", side_effect=fake_post_json),
                mock.patch.dict(
                    "os.environ",
                    {"WATCH_API_SUPERVISOR_KEY": "supervisor-secret"},
                    clear=False,
                ),
            ):
                payload = await client.search_rag_knowledge_base(query="退款 取消", top_k=5)

            self.assertEqual(captured["path"], "/api/v1/rag/search")
            self.assertEqual(captured["body"], {"query": "退款 取消", "top_k": 5})
            self.assertEqual(payload["results"][0]["title"], "退款政策")

        asyncio.run(scenario())

    def test_search_rag_knowledge_base_fail_closed_without_supervisor_key(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("post_json must not be called without supervisor key")

            with (
                mock.patch.object(client, "post_json", side_effect=boom),
                mock.patch.dict("os.environ", {"WATCH_API_SUPERVISOR_KEY": ""}, clear=True),
            ):
                payload = await client.search_rag_knowledge_base(query="退款", top_k=3)

            self.assertEqual(payload["status"], "config_required")
            self.assertIn("WATCH_API_SUPERVISOR_KEY", payload["summary"])

        asyncio.run(scenario())

    def test_search_rag_knowledge_base_fail_closed_on_422_unconfigured_model(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            request = httpx.Request("POST", "http://127.0.0.1:8000/api/v1/rag/search")
            response = httpx.Response(
                422, request=request, json={"detail": "向量模型未配置或缺少 API key。"}
            )

            async def fake_post_json(path: str, *, body: dict | None = None) -> dict:
                raise httpx.HTTPStatusError("unprocessable", request=request, response=response)

            with (
                mock.patch.object(client, "post_json", side_effect=fake_post_json),
                mock.patch.dict(
                    "os.environ",
                    {"WATCH_API_SUPERVISOR_KEY": "supervisor-secret"},
                    clear=False,
                ),
            ):
                payload = await client.search_rag_knowledge_base(query="退款", top_k=3)

            self.assertEqual(payload["status"], "failed")
            self.assertIn("向量模型", payload["summary"])

        asyncio.run(scenario())

    def test_rag_search_posts_query_with_default_top_k(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            captured: dict[str, Any] = {}

            async def fake_post_json(path: str, *, body: dict | None = None) -> dict:
                captured["path"] = path
                captured["body"] = body
                return {"results": [{"title": "Refund policy", "chunk_text": "text", "score": 0.9}]}

            with mock.patch.object(client, "post_json", side_effect=fake_post_json):
                payload = await client.rag_search(query="refund cancel")

            self.assertEqual(captured["path"], "/api/v1/rag/search")
            self.assertEqual(captured["body"], {"query": "refund cancel", "top_k": 3})
            self.assertEqual(payload["results"][0]["title"], "Refund policy")

        asyncio.run(scenario())

    def test_rag_search_rejects_empty_query_without_calling_backend(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("post_json must not be called for an empty query")

            with mock.patch.object(client, "post_json", side_effect=boom):
                payload = await client.rag_search(query="   ")

            self.assertEqual(payload["status"], "invalid_arguments")

        asyncio.run(scenario())

    def test_rag_search_returns_structured_error_on_http_failure(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            request = httpx.Request("POST", "http://127.0.0.1:8000/api/v1/rag/search")
            response = httpx.Response(422, request=request, json={"detail": "embedding model unavailable"})

            async def fake_post_json(path: str, *, body: dict | None = None) -> dict:
                raise httpx.HTTPStatusError("unprocessable", request=request, response=response)

            with mock.patch.object(client, "post_json", side_effect=fake_post_json):
                payload = await client.rag_search(query="refund", top_k=3)

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["detail"], "embedding model unavailable")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
