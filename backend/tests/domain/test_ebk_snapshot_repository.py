from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect

from app.domain.ebk_snapshots import UnifiedEbkSnapshot


class EbkSnapshotRepositoryTests(unittest.TestCase):
    def test_schema_creates_ebk_read_snapshots_table_with_required_columns(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                await repository.ensure_ebk_snapshot_schema_ready()
                db_session = importlib.import_module("app.db.session")

                async with db_session.engine.begin() as connection:
                    def _columns(sync_connection) -> set[str]:
                        return {
                            column["name"]
                            for column in inspect(sync_connection).get_columns("ebk_read_snapshots")
                        }

                    columns = await connection.run_sync(_columns)

                self.assertTrue(
                    {
                        "id",
                        "platform",
                        "platform_account_id",
                        "browser_session_id",
                        "status",
                        "dataset_count",
                        "payload_json",
                        "created_at",
                    }.issubset(columns)
                )

            asyncio.run(scenario())

    def test_save_and_get_latest_returns_most_recent_row(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                first = await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="ctrip",
                        platform_account_id="ctrip_account_01",
                        browser_session_id="bs_ctrip_01",
                        read_at="2026-07-10T09:00:00+08:00",
                        status="success",
                        summary="first",
                        datasets={"business_outline": {"status": "success", "data": {}}},
                    )
                )
                second = await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="ctrip",
                        platform_account_id="ctrip_account_01",
                        browser_session_id="bs_ctrip_01",
                        read_at="2026-07-10T10:00:00+08:00",
                        status="partial",
                        summary="second",
                        datasets={
                            "business_outline": {"status": "success", "data": {}},
                            "flow_data": {"status": "needs_review", "data": {}},
                        },
                    )
                )
                latest = await repository.get_latest_ebk_snapshot(
                    platform="ctrip",
                    platform_account_id="ctrip_account_01",
                )

                self.assertIsNotNone(latest)
                assert latest is not None
                self.assertEqual(latest["id"], second["id"])
                self.assertNotEqual(latest["id"], first["id"])
                self.assertEqual(latest["status"], "partial")
                self.assertEqual(latest["dataset_count"], 2)
                self.assertEqual(latest["payload_json"]["summary"], "second")
                self.assertEqual(latest["platform"], "ctrip")
                self.assertEqual(latest["platform_account_id"], "ctrip_account_01")
                self.assertEqual(latest["browser_session_id"], "bs_ctrip_01")
                self.assertIn("created_at", latest)

            asyncio.run(scenario())

    def test_get_latest_success_skips_non_success_rows(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                success = await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="meituan",
                        platform_account_id="meituan_account_01",
                        browser_session_id="bs_meituan_01",
                        read_at="2026-07-10T09:00:00+08:00",
                        status="success",
                        summary="ok",
                        datasets={"data_center_home": {"status": "success", "data": {"orders": 7}}},
                        platform_data={"meituan_tab_label": "首页"},
                    )
                )
                await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="meituan",
                        platform_account_id="meituan_account_01",
                        browser_session_id="bs_meituan_01",
                        read_at="2026-07-10T10:00:00+08:00",
                        status="human_required",
                        summary="need login",
                        datasets={},
                    )
                )
                latest_success = await repository.get_latest_success_ebk_snapshot(
                    platform="meituan",
                    platform_account_id="meituan_account_01",
                )
                latest_any = await repository.get_latest_ebk_snapshot(
                    platform="meituan",
                    platform_account_id="meituan_account_01",
                )

                self.assertIsNotNone(latest_success)
                assert latest_success is not None
                self.assertEqual(latest_success["id"], success["id"])
                self.assertEqual(latest_success["status"], "success")
                self.assertEqual(
                    latest_success["payload_json"]["platform_data"]["meituan_tab_label"],
                    "首页",
                )
                assert latest_any is not None
                self.assertEqual(latest_any["status"], "human_required")

            asyncio.run(scenario())

    def test_save_strips_secrets_from_persisted_payload(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                saved = await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="ctrip",
                        platform_account_id="ctrip_account_01",
                        browser_session_id="bs_ctrip_01",
                        read_at="2026-07-10T11:00:00+08:00",
                        status="error",
                        summary="bad evidence",
                        datasets={},
                        evidence={
                            "cookie": "session=abc",
                            "password": "secret-pass",
                            "profile_path": r"C:\Users\Administrator\.runtime\browser-state\ctrip\acc",
                            "verification_code": "123456",
                            "screenshot_path": r"C:\Users\Administrator\Desktop\output\shot.png",
                            "parser": "ctrip_dom_v1",
                        },
                    )
                )
                latest = await repository.get_latest_ebk_snapshot(
                    platform="ctrip",
                    platform_account_id="ctrip_account_01",
                )
                assert latest is not None
                evidence = latest["payload_json"]["evidence"]
                payload_text = str(latest["payload_json"])

                self.assertEqual(saved["id"], latest["id"])
                self.assertEqual(evidence["parser"], "ctrip_dom_v1")
                for forbidden in (
                    "session=abc",
                    "secret-pass",
                    "browser-state",
                    "123456",
                    "shot.png",
                ):
                    self.assertNotIn(forbidden, payload_text)

            asyncio.run(scenario())

    def test_baseline_query_includes_partial_excludes_failed(self) -> None:
        """2026-09-11：环比基线纳入 partial 快照（成功子集是有效基线），
        排除 human_required/needs_review/error。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="meituan",
                        platform_account_id="acct",
                        browser_session_id="bs",
                        read_at="2026-09-11T14:27:00+08:00",
                        status="partial",
                        summary="workbench failed",
                        datasets={
                            "data_center_traffic": {
                                "status": "success",
                                "data": {"浏览人数": "139 人"},
                            }
                        },
                    )
                )
                await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="meituan",
                        platform_account_id="acct",
                        browser_session_id="bs",
                        read_at="2026-09-11T14:28:00+08:00",
                        status="human_required",
                        summary="login failed",
                        datasets={},
                    )
                )
                rows = await repository.list_recent_success_ebk_snapshots(
                    platform="meituan", platform_account_id="acct", limit=5
                )
                statuses = [row["status"] for row in rows]
                self.assertIn("partial", statuses)
                self.assertNotIn("human_required", statuses)

            asyncio.run(scenario())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "ebk-snapshots.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        # Restore the global app.db.session engine so later tests in this
        # process do not inherit a stale engine pointing at the temp file we
        # are about to delete. Must run before the temp dir cleanup.
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_repository(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    repository = importlib.import_module("app.domain.ebk_snapshots.repository")
    return importlib.reload(repository)


if __name__ == "__main__":
    unittest.main()
