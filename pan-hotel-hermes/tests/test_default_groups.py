from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from typing import Any

from client.watch_api import WatchApiClient, normalize_ebk_watch_group_key


class DefaultGroupKeyTests(unittest.TestCase):
    def test_server_default_ebk_group_is_data_center(self) -> None:
        os.environ.pop("WATCH_DEFAULT_EBK_GROUP", None)
        server = importlib.reload(importlib.import_module("pan_hotel_mcp.server"))
        self.assertEqual(server.DEFAULT_EBK_GROUP, "ctrip_ebooking_data_center")

    def test_server_default_ebk_group_env_override(self) -> None:
        os.environ["WATCH_DEFAULT_EBK_GROUP"] = "custom_group_key"
        try:
            server = importlib.reload(importlib.import_module("pan_hotel_mcp.server"))
            self.assertEqual(server.DEFAULT_EBK_GROUP, "custom_group_key")
        finally:
            os.environ.pop("WATCH_DEFAULT_EBK_GROUP", None)
            importlib.reload(importlib.import_module("pan_hotel_mcp.server"))

    def test_read_ebk_datasets_defaults_to_data_center_key(self) -> None:
        os.environ.pop("WATCH_DEFAULT_EBK_GROUP", None)
        captured: dict[str, str] = {}

        class _StubClient(WatchApiClient):
            async def get_json(self, path: str, *, params: dict | None = None):  # type: ignore[override]
                captured["get_path"] = path
                return {
                    "watch_group": {
                        "group_key": "ctrip_ebooking_data_center",
                        "platform_account_id": "ebk-1",
                        "browser_session_id": "browser-1",
                        "enabled": True,
                    }
                }

            async def post_json(  # type: ignore[override]
                self,
                path: str,
                *,
                body: dict | None = None,
                params: dict | None = None,
                **_: Any,
            ):
                captured["post_path"] = path
                return {"status": "success", "summary": "ok", "data": {"metrics": {}}}

        async def scenario() -> None:
            await _StubClient().read_ebk_datasets(
                watch_group_key=None,
                dataset_ids=None,
                metric_keys=None,
                actor_id="operator",
            )

        asyncio.run(scenario())
        self.assertIn("/ctrip_ebooking_data_center", captured["get_path"])
        self.assertIn("/sessions/ebk-1/read-dataset", captured["post_path"])

    def test_legacy_default_data_center_watch_alias(self) -> None:
        os.environ.pop("WATCH_DEFAULT_EBK_GROUP", None)
        self.assertEqual(
            normalize_ebk_watch_group_key("default_data_center_watch"),
            "ctrip_ebooking_data_center",
        )

    def test_read_ebk_datasets_maps_legacy_key_and_rejects_comment_monitor(self) -> None:
        os.environ.pop("WATCH_DEFAULT_EBK_GROUP", None)
        captured: dict[str, str] = {}

        class _StubClient(WatchApiClient):
            async def get_json(self, path: str, *, params: dict | None = None):  # type: ignore[override]
                captured["get_path"] = path
                return {
                    "watch_group": {
                        "group_key": "ctrip_ebooking_data_center",
                        "platform_account_id": "ebk-2",
                        "enabled": True,
                    }
                }

            async def post_json(  # type: ignore[override]
                self,
                path: str,
                *,
                body: dict | None = None,
                params: dict | None = None,
                **_: Any,
            ):
                captured["post_path"] = path
                captured["dataset_id"] = str((body or {}).get("dataset_id") or "")
                return {"status": "success", "summary": "ok", "data": {"metrics": {}}}

        async def scenario() -> dict:
            return await _StubClient().read_ebk_datasets(
                watch_group_key="default_data_center_watch",
                dataset_ids=["comment_monitor"],
                metric_keys=None,
                actor_id="operator",
            )

        payload = asyncio.run(scenario())
        self.assertIn("/ctrip_ebooking_data_center", captured["get_path"])
        self.assertNotIn("post_path", captured)
        self.assertEqual(payload["status"], "misrouted")


if __name__ == "__main__":
    unittest.main()
