from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from app.domain.ops import operator_pending_action


class OperatorPendingActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.temp_dir.name) / "operator_pending_actions.json"
        self.path_patch = mock.patch.object(
            operator_pending_action,
            "operator_pending_store_path",
            return_value=self.store_path,
        )
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_upsert_and_get_immediate_run_offer(self) -> None:
        async def scenario() -> None:
            created = await operator_pending_action.upsert_immediate_run_offer(
                chat_id="oc_group",
                operator_id="ou_user",
                watch_kind="competitor",
                watch_group_key="default_competitor_watch",
            )
            self.assertEqual(created["status"], "offered")
            self.assertEqual(created["watch_kind"], "competitor")
            pending = await operator_pending_action.get_pending_action("oc_group", "ou_user")
            assert pending is not None
            self.assertEqual(pending["run_tool_name"], "run_competitor_watch_once")

        asyncio.run(scenario())

    def test_build_pending_operator_summary_for_agent(self) -> None:
        pending = {
            "status": "offered",
            "watch_kind": "ebk",
            "watch_group_key": "ctrip_ebooking_data_center",
            "run_tool_name": "run_ebk_watch_once",
            "offered_at": "2026-06-26T10:00:00+00:00",
            "expires_at": "2026-06-26T10:30:00+00:00",
        }
        summary = operator_pending_action.build_pending_operator_summary(pending)
        self.assertTrue(summary["active"])
        self.assertIn("run_ebk_watch_once", summary["agent_instruction"])

    def test_expired_pending_is_ignored(self) -> None:
        async def scenario() -> None:
            expired_at = datetime.now(UTC) - timedelta(minutes=1)
            await operator_pending_action.upsert_immediate_run_offer(
                chat_id="oc_group",
                operator_id="ou_user",
                watch_kind="competitor",
                watch_group_key="default_competitor_watch",
                now=expired_at - timedelta(minutes=31),
            )
            pending = await operator_pending_action.get_pending_action("oc_group", "ou_user")
            self.assertIsNone(pending)

        asyncio.run(scenario())

    def test_clear_pending_action(self) -> None:
        async def scenario() -> None:
            await operator_pending_action.upsert_immediate_run_offer(
                chat_id="oc_group",
                operator_id="ou_user",
                watch_kind="competitor",
                watch_group_key="default_competitor_watch",
            )
            cleared = await operator_pending_action.clear_pending_action("oc_group", "ou_user")
            self.assertTrue(cleared)
            self.assertIsNone(await operator_pending_action.get_pending_action("oc_group", "ou_user"))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
