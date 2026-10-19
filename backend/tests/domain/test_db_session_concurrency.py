from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.db import session as db_session


class DatabaseSessionConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_ready_calls_initialize_the_same_engine_once(self) -> None:
        original_engine = db_session.engine
        original_ready_engine = getattr(db_session, "_database_ready_engine", None)
        fake_engine = object()
        db_session.engine = fake_engine
        db_session._database_ready_engine = None

        try:
            with (
                patch.object(
                    db_session,
                    "get_settings",
                    return_value=SimpleNamespace(database_auto_create_tables=True),
                ),
                patch.object(
                    db_session,
                    "_initialize_database",
                    new=AsyncMock(),
                ) as initialize_database,
            ):
                await asyncio.gather(
                    db_session.ensure_db_ready(),
                    db_session.ensure_db_ready(),
                    db_session.ensure_db_ready(),
                )
                await db_session.ensure_db_ready()

            self.assertEqual(initialize_database.await_count, 1)
            initialize_database.assert_awaited_once_with(fake_engine)
        finally:
            db_session.engine = original_engine
            db_session._database_ready_engine = original_ready_engine


if __name__ == "__main__":
    unittest.main()
