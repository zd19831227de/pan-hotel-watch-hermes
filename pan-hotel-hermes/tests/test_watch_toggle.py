from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from pan_hotel_mcp import server as mcp_server


class WatchToggleTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_all_bound_competitor_groups(self) -> None:
        client = mock.AsyncMock()
        client.get_feishu_chat_context.return_value = {
            "binding_mode": "multi_store",
            "competitor_groups": [
                {"group_key": "default_competitor_watch", "name": "澜江路泉吉竞品组"},
                {"group_key": "watch_1782109266413", "name": "静里竞品组"},
            ],
        }
        client.patch_competitor_watch_group.return_value = {
            "watch_group": {"enabled": True, "auto_poll_enabled": True},
        }
        payload = await mcp_server._set_watch_enabled(
            client,
            {
                "watch_kind": "competitor",
                "chat_id": "oc_test",
                "all_bound_groups": True,
            },
            enabled=True,
        )
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["multi_group"])
        self.assertEqual(len(payload["group_results"]), 2)
        self.assertEqual(client.patch_competitor_watch_group.await_count, 2)
        self.assertIn("独立", payload["operator_summary"]["usage_note"])

    async def test_resume_single_uses_chat_resolution_not_default(self) -> None:
        client = mock.AsyncMock()
        client.patch_competitor_watch_group.return_value = {
            "watch_group": {"name": "静里竞品组", "enabled": True},
        }
        with mock.patch.object(
            mcp_server,
            "_competitor_watch_group",
            new=mock.AsyncMock(return_value=("watch_1782109266413", None)),
        ):
            payload = await mcp_server._set_watch_enabled(
                client,
                {"watch_kind": "competitor", "chat_id": "oc_test", "store_hint": "静里"},
                enabled=True,
            )
        self.assertEqual(payload["group_results"][0]["watch_group_key"], "watch_1782109266413")
        self.assertNotIn("default_competitor_watch", payload["summary"])


if __name__ == "__main__":
    unittest.main()
