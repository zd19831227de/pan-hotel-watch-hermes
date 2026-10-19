from __future__ import annotations

import asyncio
import unittest

from client.intent_routing import EBK_LATEST_RESULT_TOOL, build_ebk_review_misroute_response
from client.watch_api import WatchApiClient


class EbkToolGuardTests(unittest.TestCase):
    def test_read_ebk_datasets_rejects_comment_monitor(self) -> None:
        post_called = False

        class _StubClient(WatchApiClient):
            async def get_json(self, path: str, *, params: dict | None = None):  # type: ignore[override]
                return {
                    "watch_group": {
                        "group_key": "ctrip_ebooking_data_center",
                        "platform_account_id": "ebk-1",
                        "enabled": True,
                    }
                }

            async def post_json(  # type: ignore[override]
                self,
                path: str,
                *,
                body: dict | None = None,
                params: dict | None = None,
                **_: object,
            ):
                nonlocal post_called
                post_called = True
                return {"status": "success"}

        async def scenario() -> dict:
            return await _StubClient().read_ebk_datasets(
                watch_group_key="ctrip_ebooking_data_center",
                dataset_ids=["comment_monitor"],
                metric_keys=None,
                actor_id="operator",
            )

        payload = asyncio.run(scenario())
        self.assertEqual(payload["status"], "misrouted")
        self.assertIn(EBK_LATEST_RESULT_TOOL, payload.get("routing_hints") or [])
        self.assertIn(
            EBK_LATEST_RESULT_TOOL,
            (payload.get("operator_summary") or {}).get("routing_hints") or [],
        )
        self.assertFalse(post_called)

    def test_run_ebk_watch_once_no_enabled_datasets_adds_routing_hint(self) -> None:
        class _StubClient(WatchApiClient):
            async def post_json(  # type: ignore[override]
                self,
                path: str,
                *,
                body: dict | None = None,
                params: dict | None = None,
                **_: object,
            ):
                return {
                    "status": "skipped",
                    "summary": "没有启用的 EBK 数据集。",
                    "dataset_results": [],
                }

        async def scenario() -> dict:
            return await _StubClient().run_ebk_watch_once("ctrip_ebooking_data_center")

        payload = asyncio.run(scenario())
        self.assertIn(EBK_LATEST_RESULT_TOOL, payload.get("routing_hints") or [])
        self.assertIn(
            EBK_LATEST_RESULT_TOOL,
            (payload.get("operator_summary") or {}).get("routing_hints") or [],
        )
        self.assertIn("enabled=false", (payload.get("operator_summary") or {}).get("usage_note") or "")

    def test_build_ebk_review_misroute_response_shape(self) -> None:
        payload = build_ebk_review_misroute_response(watch_group_key="ctrip_ebooking_data_center")
        self.assertEqual(payload["status"], "misrouted")
        self.assertEqual(payload["watch_group_key"], "ctrip_ebooking_data_center")


if __name__ == "__main__":
    unittest.main()
