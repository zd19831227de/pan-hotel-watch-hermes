from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from client.operator_pending_action import (
    attach_pending_operator_action,
    register_stale_immediate_run_offer,
)
from client.watch_api import WatchApiClient, enrich_competitor_latest_response


class OperatorPendingActionClientTests(unittest.TestCase):
    def test_register_offer_only_when_stale(self) -> None:
        async def scenario() -> None:
            client = mock.AsyncMock()
            stale_payload = enrich_competitor_latest_response(
                {
                    "watch_group_key": "default_competitor_watch",
                    "run": {
                        "status": "success",
                        "captured_at": (datetime.now(UTC) - timedelta(hours=8)).isoformat(),
                    },
                    "hotels": [
                        {
                            "hotel_config": {"display_name": "竞对A"},
                            "snapshots": {
                                "today": {
                                    "rates": [
                                        {
                                            "is_trusted": True,
                                            "room_type": "高级大床",
                                            "breakfast_text": "无早",
                                            "price_amount_yuan": 420,
                                        }
                                    ]
                                }
                            },
                        }
                    ],
                }
            )
            await register_stale_immediate_run_offer(
                client,
                payload=stale_payload,
                chat_id="oc_group",
                operator_id="ou_user",
                watch_kind="competitor",
                watch_group_key="default_competitor_watch",
            )
            client.put_json.assert_awaited_once()
            _, call_kwargs = client.put_json.await_args
            self.assertIn("body", call_kwargs)
            self.assertEqual(call_kwargs["body"]["chat_id"], "oc_group")

        asyncio.run(scenario())

    def test_register_offer_uses_real_watch_api_client_signature(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient()
            captured: dict[str, object] = {}

            async def capture_put_json(path: str, *, body: dict[str, object] | None = None) -> dict[str, object]:
                captured["path"] = path
                captured["body"] = body or {}
                return {"status": "success"}

            client.put_json = capture_put_json  # type: ignore[method-assign]
            stale_payload = enrich_competitor_latest_response(
                {
                    "watch_group_key": "default_competitor_watch",
                    "run": {
                        "status": "success",
                        "captured_at": (datetime.now(UTC) - timedelta(hours=8)).isoformat(),
                    },
                    "hotels": [
                        {
                            "hotel_config": {"display_name": "竞对A"},
                            "snapshots": {
                                "today": {
                                    "rates": [
                                        {
                                            "is_trusted": True,
                                            "room_type": "高级大床",
                                            "breakfast_text": "无早",
                                            "price_amount_yuan": 420,
                                        }
                                    ]
                                }
                            },
                        }
                    ],
                }
            )
            await register_stale_immediate_run_offer(
                client,
                payload=stale_payload,
                chat_id="oc_group",
                operator_id="ou_user",
                watch_kind="competitor",
                watch_group_key="default_competitor_watch",
            )
            self.assertEqual(captured["path"], "/api/v1/ops/operator-pending-action")
            body = captured["body"]
            assert isinstance(body, dict)
            self.assertEqual(body["operator_id"], "ou_user")
            self.assertEqual(body["watch_kind"], "competitor")

        asyncio.run(scenario())

    def test_register_offer_skips_fresh_data(self) -> None:
        async def scenario() -> None:
            client = mock.AsyncMock()
            fresh_payload = enrich_competitor_latest_response(
                {
                    "watch_group_key": "default_competitor_watch",
                    "run": {"status": "success", "captured_at": datetime.now(UTC).isoformat()},
                    "hotels": [
                        {
                            "hotel_config": {"display_name": "竞对A"},
                            "snapshots": {
                                "today": {
                                    "rates": [
                                        {
                                            "is_trusted": True,
                                            "room_type": "高级大床",
                                            "breakfast_text": "无早",
                                            "price_amount_yuan": 420,
                                        }
                                    ]
                                }
                            },
                        }
                    ],
                }
            )
            self.assertFalse(fresh_payload["operator_summary"]["suggest_immediate_run"])
            await register_stale_immediate_run_offer(
                client,
                payload=fresh_payload,
                chat_id="oc_group",
                operator_id="ou_user",
                watch_kind="competitor",
                watch_group_key="default_competitor_watch",
            )
            client.put_json.assert_not_awaited()

        asyncio.run(scenario())

    def test_attach_pending_operator_action_merges_summary(self) -> None:
        async def scenario() -> None:
            client = mock.AsyncMock()
            client.get_json = mock.AsyncMock(
                return_value={
                    "operator_summary": {
                        "pending_operator_action": {
                            "active": True,
                            "watch_kind": "competitor",
                            "run_tool_name": "run_competitor_watch_once",
                            "agent_instruction": "直接 run",
                        }
                    }
                }
            )
            payload = {"operator_summary": {"suggest_immediate_run": True}}
            merged = await attach_pending_operator_action(
                client,
                payload,
                chat_id="oc_group",
                operator_id="ou_user",
            )
            pending = merged["operator_summary"]["pending_operator_action"]
            self.assertTrue(pending["active"])
            self.assertEqual(pending["run_tool_name"], "run_competitor_watch_once")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
