from __future__ import annotations

import asyncio
import importlib
import unittest

from app.domain.page_change_alerts.models import PageHealthAlertRecord, local_now

from tests.domain.test_data_center_watch_group_config import (
    _isolated_sqlite_database,
)


def _reload_repo(db_url: str):
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings_module.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine, expire_on_commit=False
    )
    importlib.import_module("app.domain.page_change_alerts.models")
    return importlib.reload(importlib.import_module("app.domain.page_change_alerts.repository"))


class PageHealthAlertDeleteTests(unittest.TestCase):
    def test_delete_removes_alert(self) -> None:
        with _isolated_sqlite_database() as db_url:
            repo = _reload_repo(db_url)

            async def scenario() -> None:
                from app.db.session import ensure_db_ready, session_scope

                await ensure_db_ready()
                async with session_scope() as session:
                    record = PageHealthAlertRecord(
                        platform="ctrip",
                        target="hotel_page",
                        problem_key="selector_missing",
                        level="partial_fail",
                        summary="test",
                        created_at=local_now(),
                    )
                    session.add(record)
                    await session.flush()
                    alert_id = record.id

                self.assertTrue(await repo.delete_page_health_alert(alert_id))
                self.assertFalse(await repo.delete_page_health_alert(alert_id))
                remaining = await repo.list_page_health_alerts(limit=50)
                self.assertFalse(any(a["id"] == alert_id for a in remaining))

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
