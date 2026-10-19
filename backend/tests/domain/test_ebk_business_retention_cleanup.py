from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from datetime import timedelta
from pathlib import Path

from app.domain.ebk_snapshots.models import EbkReadSnapshot, local_now
from app.domain.ctrip_ebooking_watch.models import (
    CtripEbookingWatchDatasetResult,
    CtripEbookingWatchGroup,
    CtripEbookingWatchRun,
)

from tests.domain.test_data_center_watch_group_config import (
    _isolated_sqlite_database,
    _reload_repository,
)


def _reload_ebk_repos(db_url: str):
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings_module.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    importlib.import_module("app.domain.ebk_snapshots.models")
    importlib.import_module("app.domain.ctrip_ebooking_watch.models")
    return importlib.reload(importlib.import_module("app.domain.ebk_snapshots.repository"))


class EbkBusinessRetentionCleanupTests(unittest.TestCase):
    def test_cleanup_removes_old_snapshots_and_watch_runs(self) -> None:
        with _isolated_sqlite_database() as db_url:
            repository = _reload_ebk_repos(db_url)

            async def scenario() -> None:
                from app.db.session import ensure_db_ready, session_scope

                await ensure_db_ready()
                old = local_now() - timedelta(days=200)
                recent = local_now() - timedelta(days=5)
                async with session_scope() as session:
                    group = CtripEbookingWatchGroup(
                        group_key="g1",
                        name="G1",
                        platform_account_id="acc",
                        browser_session_id="bs-1",
                        retention_days=180,
                    )
                    session.add(group)
                    await session.flush()
                    old_run = CtripEbookingWatchRun(
                        group_id=group.id,
                        platform_account_id="acc",
                        status="success",
                        source="scheduled",
                        actor_id="s",
                        captured_at=old,
                    )
                    new_run = CtripEbookingWatchRun(
                        group_id=group.id,
                        platform_account_id="acc",
                        status="success",
                        source="scheduled",
                        actor_id="s",
                        captured_at=recent,
                    )
                    session.add_all([old_run, new_run])
                    await session.flush()
                    session.add(
                        CtripEbookingWatchDatasetResult(
                            run_id=old_run.id,
                            group_id=group.id,
                            dataset_id="d1",
                            dataset_label="D1",
                            status="success",
                            captured_at=old,
                        )
                    )
                    session.add_all(
                        [
                            EbkReadSnapshot(
                                platform="ctrip",
                                platform_account_id="acc",
                                browser_session_id="bs-1",
                                status="success",
                                payload_json={},
                                created_at=old,
                            ),
                            EbkReadSnapshot(
                                platform="ctrip",
                                platform_account_id="acc",
                                browser_session_id="bs-1",
                                status="success",
                                payload_json={},
                                created_at=recent,
                            ),
                        ]
                    )

                result = await repository.cleanup_expired_ebk_business_data(retention_days=180)
                self.assertEqual(result["deleted_snapshots"], 1)
                self.assertEqual(result["deleted_runs"], 1)

                async with session_scope() as session:
                    from sqlalchemy import func, select

                    snap_count = (
                        await session.scalar(select(func.count(EbkReadSnapshot.id)))
                    ) or 0
                    run_count = (
                        await session.scalar(select(func.count(CtripEbookingWatchRun.id)))
                    ) or 0
                    result_count = (
                        await session.scalar(
                            select(func.count(CtripEbookingWatchDatasetResult.id))
                        )
                    ) or 0
                    self.assertEqual(snap_count, 1)
                    self.assertEqual(run_count, 1)
                    self.assertEqual(result_count, 0)

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
