from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path


class _isolated_db:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DATABASE_AUTO_CREATE_TABLES")
        }
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'snapshots.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        from app.core.settings import get_settings

        get_settings.cache_clear()
        return os.environ["DATABASE_URL"]

    def __exit__(self, *args) -> None:
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from app.core.settings import get_settings

        get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db():
    import importlib

    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(
        os.environ["DATABASE_URL"], pool_pre_ping=True
    )
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return importlib.reload(db_session)


def _snapshot_fixture() -> "object":
    from app.domain.ebk_snapshots.types import UnifiedEbkSnapshot

    return UnifiedEbkSnapshot(
        platform="ctrip",
        platform_account_id="uni_acc",
        browser_session_id="browser-uni_acc",
        read_at="2026-09-28T10:00:00+08:00",
        status="success",
        summary="unification test",
        datasets={"business_outline": {"status": "success", "data": {}}},
    )


class EbkSnapshotUnificationTests(unittest.TestCase):
    def test_legacy_table_gets_source_run_and_shop_columns(self) -> None:
        with _isolated_db():
            async def scenario() -> tuple[set[str], list[dict]]:
                db_session = _reload_db()
                async with db_session.engine.begin() as connection:
                    from sqlalchemy import inspect, text

                    await connection.execute(
                        text(
                            """
                            CREATE TABLE ebk_read_snapshots (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                platform VARCHAR(32) NOT NULL,
                                platform_account_id VARCHAR(128) NOT NULL,
                                browser_session_id VARCHAR(128) NOT NULL,
                                status VARCHAR(32) NOT NULL,
                                dataset_count INTEGER NOT NULL DEFAULT 0,
                                payload_json JSON NOT NULL,
                                created_at DATETIME NOT NULL
                            )
                            """
                        )
                    )
                    from app.domain.ebk_snapshots.migrations import (
                        ensure_ebk_snapshot_schema,
                    )

                    await ensure_ebk_snapshot_schema(connection)
                    await ensure_ebk_snapshot_schema(connection)

                    def inspect_table(sync_connection):
                        inspector = inspect(sync_connection)
                        columns = {
                            column["name"]
                            for column in inspector.get_columns("ebk_read_snapshots")
                        }
                        indexes = [
                            {
                                "name": index["name"],
                                "columns": list(index["column_names"]),
                                "unique": bool(index["unique"]),
                            }
                            for index in inspector.get_indexes("ebk_read_snapshots")
                        ]
                        return columns, indexes

                    return await connection.run_sync(inspect_table)

            columns, indexes = asyncio.run(scenario())

        self.assertIn("source_watch_run_id", columns)
        self.assertIn("shop_key", columns)
        self.assertTrue(
            any(
                index["unique"] and "source_watch_run_id" in index["columns"]
                for index in indexes
            ),
            f"source_watch_run_id 需唯一索引，实际：{indexes}",
        )

    def test_save_is_idempotent_per_source_run(self) -> None:
        with _isolated_db():
            _reload_db()

            async def scenario() -> tuple[int, int, int]:
                from app.domain.ebk_snapshots import repository

                first = await repository.save_ebk_snapshot(
                    _snapshot_fixture(),  # type: ignore[arg-type]
                    source_watch_run_id=4242,
                    shop_key="shop_uni",
                )
                retry = await repository.save_ebk_snapshot(
                    _snapshot_fixture(),  # type: ignore[arg-type]
                    source_watch_run_id=4242,
                    shop_key="shop_uni",
                )
                independent = await repository.save_ebk_snapshot(
                    _snapshot_fixture()  # type: ignore[arg-type]
                )
                assert first is not None and retry is not None and independent is not None
                return int(first["id"]), int(retry["id"]), int(independent["id"])

            first_id, retry_id, independent_id = asyncio.run(scenario())

        self.assertEqual(first_id, retry_id, "同一 watch run 重试补存不得新增行")
        self.assertNotEqual(first_id, independent_id, "无来源的统一读取仍是独立快照")

    def test_persist_ctrip_run_snapshot_carries_run_id_and_shop(self) -> None:
        with _isolated_db():
            _reload_db()

            async def scenario() -> tuple[int, int, object, object]:
                from app.domain.ebk_snapshots import repository
                from app.domain.ebk_snapshots.ebk_snapshot_reader import (
                    persist_ctrip_run_snapshot,
                )

                run_payload = {
                    "status": "success",
                    "summary": "global push read",
                    "captured_at": "2026-09-28T10:05:00+08:00",
                    "dataset_results": [
                        {
                            "dataset_id": "business_outline",
                            "status": "success",
                            "data": {"orders": 10},
                            "captured_at": "2026-09-28T10:05:00+08:00",
                        }
                    ],
                    "persistence": {"run_id": 777},
                }
                first = await persist_ctrip_run_snapshot(
                    run_payload=run_payload,
                    platform_account_id="gp_acc",
                    browser_session_id="browser-gp_acc",
                    actor_id="ebk_global_push_scheduler",
                    trigger_via="global_push",
                    shop_key="shop_gp",
                )
                retry = await persist_ctrip_run_snapshot(
                    run_payload=run_payload,
                    platform_account_id="gp_acc",
                    browser_session_id="browser-gp_acc",
                    actor_id="ebk_global_push_scheduler",
                    trigger_via="global_push",
                    shop_key="shop_gp",
                )
                assert first is not None and retry is not None
                rows = await repository.list_recent_success_ebk_snapshots(
                    platform="ctrip", platform_account_id="gp_acc", limit=10
                )
                statuses = [
                    (
                        row.get("source_watch_run_id"),
                        row.get("shop_key"),
                    )
                    for row in rows
                ]
                return int(first["id"]), int(retry["id"]), statuses, rows

            first_id, retry_id, statuses, rows = asyncio.run(scenario())

        self.assertEqual(first_id, retry_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_watch_run_id"], 777)
        self.assertEqual(rows[0]["shop_key"], "shop_gp")


if __name__ == "__main__":
    unittest.main()
