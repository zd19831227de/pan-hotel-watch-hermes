from __future__ import annotations

import asyncio
import importlib
import unittest
from unittest import mock

from app.domain.ebk_snapshots import UnifiedEbkSnapshot
from tests.domain.test_ebk_snapshot_repository import (
    _isolated_sqlite_database,
    _reload_repository,
)


class EbkRunNowActorTests(unittest.TestCase):
    def test_run_now_route_passes_explicit_actor_to_scheduler(self) -> None:
        api = importlib.import_module("app.api.v1.ctrip_ebooking_watch")
        calls: list[dict] = []

        class FakeScheduler:
            def __init__(self, *, tick_seconds: int) -> None:
                self.tick_seconds = tick_seconds

            async def run_watch_group_now(self, **kwargs):
                calls.append(kwargs)
                return {"status": "success"}

        with mock.patch.object(api, "CtripEbookingDataCenterWatchScheduler", FakeScheduler):
            result = asyncio.run(
                api.run_ctrip_data_center_watch_group_now(
                    watch_group_key="ebk_watch",
                    push_to_feishu=False,
                    actor_id="hermes",
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            calls,
            [
                {
                    "watch_group_key": "ebk_watch",
                    "push_to_feishu": False,
                    "actor_id": "hermes",
                }
            ],
        )


class EbkSnapshotReadTriggerTests(unittest.TestCase):
    def test_snapshot_repository_persists_only_read_trigger_allowlist(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="meituan",
                        platform_account_id="mt-account",
                        browser_session_id="private-session",
                        read_at="2026-08-24T12:00:00+08:00",
                        status="success",
                        summary="ok",
                        datasets={"data_center_home": {"status": "success", "data": {}}},
                    ),
                    read_trigger={
                        "actor_id": "ou_operator",
                        "via": "bot_analysis",
                        "cookie": "must-not-persist",
                    },
                )
                latest = await repository.get_latest_ebk_snapshot(
                    platform="meituan",
                    platform_account_id="mt-account",
                )
                assert latest is not None
                self.assertEqual(
                    latest["payload_json"]["read_trigger"],
                    {"actor_id": "ou_operator", "via": "bot_analysis"},
                )
                self.assertNotIn("must-not-persist", str(latest["payload_json"]))

            asyncio.run(scenario())

    def test_read_all_passes_trigger_to_each_platform_reader(self) -> None:
        reader = importlib.import_module("app.domain.ebk_snapshots.ebk_snapshot_reader")
        calls: list[dict] = []

        async def fake_read_meituan_platform(**kwargs):
            calls.append(kwargs)
            return {"status": "success"}

        with (
            mock.patch.object(reader, "_require_ebk_session", return_value="session"),
            mock.patch.object(reader, "_read_meituan_platform", fake_read_meituan_platform),
        ):
            result = asyncio.run(
                reader.read_all_ebk_snapshots(
                    meituan_platform_account_id="mt-account",
                    platforms=["meituan"],
                    actor_id="ou_operator",
                    trigger_via="bot_analysis",
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(calls[0]["actor_id"], "ou_operator")
        self.assertEqual(calls[0]["trigger_via"], "bot_analysis")

    def test_read_fliggy_platform_returns_platform_result(self) -> None:
        reader = importlib.import_module("app.domain.ebk_snapshots.ebk_snapshot_reader")

        class FakeService:
            async def read_dataset(self, **kwargs):
                return {"status": "success", "data": {"rate": 1}, "evidence": {}}

        with (
            mock.patch.object(reader, "get_fliggy_ebooking_service", return_value=FakeService()),
            mock.patch.object(
                reader,
                "save_ebk_snapshot",
                new=mock.AsyncMock(return_value={"id": 9, "dataset_count": 1}),
            ),
        ):
            result = asyncio.run(
                reader._read_fliggy_platform(
                    platform_account_id="fliggy_acc",
                    browser_session_id="browser-fliggy_acc",
                    actor_id="operator",
                    headless=False,
                    trigger_via="console",
                )
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["platform"], "fliggy")
        self.assertEqual(result["snapshot_id"], 9)
        self.assertEqual(result["dataset_count"], 1)


if __name__ == "__main__":
    unittest.main()
