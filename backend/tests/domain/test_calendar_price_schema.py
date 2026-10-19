from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class CalendarPriceSchemaTests(unittest.TestCase):
    def test_calendar_schema_has_config_runs_points_and_alerts(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                await db_session.ensure_db_ready()
                from sqlalchemy import inspect

                async with db_session.engine.begin() as connection:
                    def inspect_schema(sync_connection):
                        inspector = inspect(sync_connection)
                        tables = set(inspector.get_table_names())
                        config_columns = {
                            column["name"]
                            for column in inspector.get_columns("calendar_price_watch_configs")
                        }
                        point_columns = {
                            column["name"]
                            for column in inspector.get_columns("calendar_price_points")
                        }
                        alert_columns = {
                            column["name"]
                            for column in inspector.get_columns("calendar_price_alerts")
                        }
                        return tables, config_columns, point_columns, alert_columns

                    tables, config_columns, point_columns, alert_columns = await connection.run_sync(
                        inspect_schema
                    )

                self.assertIn("calendar_price_watch_configs", tables)
                self.assertIn("calendar_price_runs", tables)
                self.assertIn("calendar_price_points", tables)
                self.assertIn("calendar_price_alerts", tables)
                self.assertTrue(
                    {
                        "watch_group_id",
                        "future_days",
                        "high_gap_threshold_yuan",
                        "low_gap_threshold_yuan",
                        "auto_poll_enabled",
                        "matrix_brief_enabled",
                        "browser_session_id",
                        "platform_account_id",
                        "matrix_brief_morning_push_time",
                        "matrix_brief_evening_push_time",
                        "matrix_brief_push_times",
                        "matrix_brief_last_pushed_json",
                    }.issubset(config_columns)
                )
                self.assertTrue(
                    {
                        "run_id",
                        "hotel_config_id",
                        "role",
                        "checkin_date",
                        "price_amount_yuan",
                        "source",
                        "is_trusted",
                    }.issubset(point_columns)
                )
                self.assertTrue(
                    {
                        "run_id",
                        "alert_type",
                        "checkin_date",
                        "own_price_yuan",
                        "reference_price_yuan",
                        "gap_yuan",
                    }.issubset(alert_columns)
                )

            asyncio.run(scenario())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "calendar-watch.db"
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


def _reload_db(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return importlib.reload(db_session)


if __name__ == "__main__":
    unittest.main()
