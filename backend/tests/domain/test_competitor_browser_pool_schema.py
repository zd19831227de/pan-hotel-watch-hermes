from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class CompetitorBrowserPoolSchemaTests(unittest.TestCase):
    def test_schema_has_pool_mode_member_and_lease_tables(self) -> None:
        with _isolated_sqlite_database():

            async def scenario() -> None:
                repository = _reload_repository()
                db_session = importlib.import_module("app.db.session")
                from sqlalchemy import inspect

                await repository.ensure_db_ready()

                async with db_session.engine.begin() as connection:

                    def inspect_schema(sync_connection):
                        inspector = inspect(sync_connection)
                        group_columns = {
                            column["name"]
                            for column in inspector.get_columns(
                                "ctrip_competitor_watch_groups"
                            )
                        }
                        tables = set(inspector.get_table_names())
                        member_columns = {
                            column["name"]
                            for column in inspector.get_columns(
                                "ctrip_competitor_watch_group_browser_pool_members"
                            )
                        }
                        lease_columns = {
                            column["name"]
                            for column in inspector.get_columns(
                                "ctrip_competitor_browser_leases"
                            )
                        }
                        return group_columns, tables, member_columns, lease_columns

                    (
                        group_columns,
                        tables,
                        member_columns,
                        lease_columns,
                    ) = await connection.run_sync(inspect_schema)

                self.assertIn("browser_rotation_mode", group_columns)
                self.assertIn("pool_failover_enabled", group_columns)
                self.assertIn("pool_cooldown_minutes", group_columns)
                self.assertIn("ctrip_competitor_watch_group_browser_pool_members", tables)
                self.assertIn("ctrip_competitor_browser_leases", tables)
                self.assertTrue(
                    {
                        "group_id",
                        "browser_session_id",
                        "enabled",
                        "sort_order",
                        "weight",
                        "last_selected_at",
                        "last_success_at",
                        "last_failure_at",
                        "last_status",
                        "cooldown_until",
                        "consecutive_failures",
                    }.issubset(member_columns)
                )
                self.assertTrue(
                    {
                        "browser_session_id",
                        "watch_group_key",
                        "lease_owner",
                        "lease_expires_at",
                    }.issubset(lease_columns)
                )

            asyncio.run(scenario())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watch-test.db"
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


def _reload_repository():
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(
        os.environ["DATABASE_URL"],
        pool_pre_ping=True,
    )
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    repository = importlib.import_module("app.domain.competitor_prices.repository")
    return importlib.reload(repository)
