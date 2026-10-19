"""EBK 全局推送仓储单测（隔离 sqlite）。"""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.domain.ebk_global_push.repository import (
    EbkGlobalPushConfigCreate,
    EbkGlobalPushConfigPatch,
)


class EbkGlobalPushRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_url = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'global-push.db').as_posix()}"
        )
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = self.db_url
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        self.repo = self._reload_repository()

    def tearDown(self) -> None:
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()

    def _reload_repository(self):
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        db_session = importlib.import_module("app.db.session")
        db_session.settings = settings.get_settings()
        db_session.engine = db_session.create_async_engine(self.db_url, pool_pre_ping=True)
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine, expire_on_commit=False
        )
        return importlib.reload(
            importlib.import_module("app.domain.ebk_global_push.repository")
        )

    def _create_request(self, **overrides):
        base = dict(
            ctrip_watch_group_key="anchor_group",
            name="示例门店全局推送",
            enabled=False,
            enabled_platforms=["ctrip", "meituan", "fliggy"],
            schedule_mode="fixed_slots",
            push_times=["20:00", "08:00", "08:00"],
            interval_minutes=60,
            window_start_time="06:00",
            window_end_time="23:00",
        )
        base.update(overrides)
        return EbkGlobalPushConfigCreate(**base)

    def test_create_normalizes_and_dedupes_push_times(self):
        async def scenario():
            config = await self.repo.create_global_push_config(self._create_request())
            self.assertIsNotNone(config)
            self.assertEqual(config["push_times"], ["08:00", "20:00"])
            self.assertFalse(config["enabled"])

        asyncio.run(scenario())

    def test_create_rejects_duplicate_anchor(self):
        async def scenario():
            first = await self.repo.create_global_push_config(self._create_request())
            second = await self.repo.create_global_push_config(
                self._create_request(name="第二条")
            )
            self.assertIsNotNone(first)
            self.assertIsNone(second)

        asyncio.run(scenario())

    def test_create_rejects_bad_schedule(self):
        async def scenario():
            with self.assertRaises(ValueError):
                await self.repo.create_global_push_config(self._create_request(push_times=[]))
            with self.assertRaises(ValueError):
                await self.repo.create_global_push_config(
                    self._create_request(schedule_mode="last_run_interval", interval_minutes=5)
                )
            with self.assertRaises(ValueError):
                await self.repo.create_global_push_config(
                    self._create_request(enabled_platforms=["ctrip", "unknown"])
                )

        asyncio.run(scenario())

    def test_claim_run_mutex_and_slot_uniqueness(self):
        async def scenario():
            config = await self.repo.create_global_push_config(self._create_request())
            run = await self.repo.claim_global_push_run(
                config_id=config["id"], schedule_key="fixed:1:2026-08-28:08:00"
            )
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "running")
            # 同槽重复 → already_claimed（None）
            duplicate = await self.repo.claim_global_push_run(
                config_id=config["id"], schedule_key="fixed:1:2026-08-28:08:00"
            )
            self.assertIsNone(duplicate)
            # 相邻槽但上一运行仍在 running → 互斥拒绝
            overlap = await self.repo.claim_global_push_run(
                config_id=config["id"], schedule_key="interval:1:2026-08-28T08:05"
            )
            self.assertIsNone(overlap)

        asyncio.run(scenario())

    def test_finish_and_cursor_updates(self):
        async def scenario():
            config = await self.repo.create_global_push_config(self._create_request())
            run = await self.repo.claim_global_push_run(
                config_id=config["id"], schedule_key="fixed:1:2026-08-28:08:00"
            )
            finished = await self.repo.finish_global_push_run(
                run["id"],
                status="success",
                platform_results={
                    "ctrip": {"status": "success", "snapshot_id": 101},
                    "meituan": {"status": "success", "snapshot_id": 202},
                },
                card_results={"business": 2, "promotion": 1, "advertising": 1},
            )
            self.assertEqual(finished["status"], "success")
            self.assertIn("error_fingerprint", finished)
            # T2.2：携程 watch run id → 全局运行 id 映射，供 Excel 成组裁剪关联
            links = await self.repo.list_ctrip_watch_run_links_for_export()
            self.assertEqual(links, {"101": str(run["id"])})
            await self.repo.mark_config_run_finished(
                config["id"], succeeded=True, next_run_at=datetime(2026, 8, 28, 20, 0)
            )
            updated = await self.repo.get_global_push_config(config["id"])
            self.assertIsNotNone(updated["last_successful_push_at"])
            self.assertEqual(updated["consecutive_failure_count"], 0)
            self.assertEqual(updated["next_run_at"], "2026-08-28T20:00:00")

        asyncio.run(scenario())

    def test_soft_disable_and_stale_running_recovery(self):
        async def scenario():
            config = await self.repo.create_global_push_config(self._create_request(enabled=True))
            await self.repo.soft_disable_global_push_config(config["id"])
            disabled = await self.repo.get_global_push_config(config["id"])
            self.assertFalse(disabled["enabled"])

            run = await self.repo.claim_global_push_run(
                config_id=config["id"], schedule_key="fixed:1:2026-08-28:08:00"
            )
            # 伪造 created_at 超过 45 分钟
            from app.domain.ebk_global_push import models as gp_models
            from app.db.session import session_scope

            async with session_scope() as session:
                row = await session.get(gp_models.EbkGlobalPushRun, run["id"])
                row.created_at = datetime.now() - timedelta(minutes=46)
                await session.commit()
            recovered = await self.repo.fail_stale_running_runs()
            self.assertEqual(recovered, 1)
            runs = await self.repo.list_global_push_runs(config["id"])
            self.assertEqual(runs[0]["status"], "failed")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
