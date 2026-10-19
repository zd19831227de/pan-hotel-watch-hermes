from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class ReviewManagementSchemaTests(unittest.TestCase):
    def test_review_management_schema_has_expected_tables_and_columns(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                await db_session.ensure_db_ready()
                from sqlalchemy import inspect

                async with db_session.engine.begin() as connection:
                    def inspect_schema(sync_connection):
                        inspector = inspect(sync_connection)
                        tables = set(inspector.get_table_names())
                        columns_by_table = {
                            table: {column["name"] for column in inspector.get_columns(table)}
                            for table in (
                                "review_items",
                                "reply_templates",
                                "review_reply_approvals",
                                "review_auto_reply_configs",
                                "review_store_bindings",
                                "review_tasks",
                            )
                            if inspector.has_table(table)
                        }
                        return tables, columns_by_table

                    tables, columns_by_table = await connection.run_sync(inspect_schema)

                expected_tables = {
                    "review_items",
                    "reply_templates",
                    "review_reply_approvals",
                    "review_auto_reply_configs",
                    "review_store_bindings",
                    "review_tasks",
                }
                self.assertTrue(expected_tables.issubset(tables))
                self.assertTrue(
                    {
                        "platform",
                        "hotel_id",
                        "review_id",
                        "user_nickname",
                        "score",
                        "content",
                        "review_time",
                        "reply_status",
                        "reply_content",
                        "replied_at",
                    }.issubset(columns_by_table["review_items"])
                )
                self.assertTrue(
                    {
                        "platform",
                        "category",
                        "name",
                        "content",
                        "score_range_json",
                        "priority",
                        "enabled",
                        "source",
                    }.issubset(columns_by_table["reply_templates"])
                )
                self.assertTrue(
                    {
                        "platform",
                        "review_item_id",
                        "generated_reply_content",
                        "status",
                        "decision_source",
                        "feishu_chat_id",
                        "submitted_at",
                        "submit_result_json",
                    }.issubset(columns_by_table["review_reply_approvals"])
                )
                self.assertTrue(
                    {
                        "platform",
                        "auto_reply_mode",
                        "auto_fetch_enabled",
                        "fetch_interval_minutes",
                        "approval_chat_id",
                        "last_fetched_at",
                        "auto_process_time",
                        "last_auto_processed_at",
                    }.issubset(columns_by_table["review_auto_reply_configs"])
                )
                self.assertTrue(
                    {
                        "platform",
                        "auto_reply_mode",
                        "auto_fetch_enabled",
                        "auto_process_time",
                        "last_auto_processed_at",
                    }.issubset(columns_by_table["review_store_bindings"])
                )
                self.assertTrue(
                    {
                        "platform",
                        "task_type",
                        "status",
                        "review_count",
                        "result_json",
                        "error_msg",
                        "completed_at",
                    }.issubset(columns_by_table["review_tasks"])
                )

            asyncio.run(scenario())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "review-management.db"
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
