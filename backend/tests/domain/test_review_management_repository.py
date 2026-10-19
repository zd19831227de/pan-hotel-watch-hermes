from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class ReviewManagementRepositoryTests(unittest.TestCase):
    def test_config_get_or_create_defaults_and_patch(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repo = _reload_repository(db_url)
                from app.domain.review_management.types import ReviewAutoConfigPatch

                config = await repo.get_review_auto_config(platform="ctrip")
                self.assertEqual(config["platform"], "ctrip")
                self.assertEqual(config["auto_reply_mode"], "off")
                self.assertFalse(config["auto_fetch_enabled"])
                self.assertEqual(config["fetch_interval_minutes"], 30)
                self.assertIsNone(config["approval_chat_id"])

                updated = await repo.update_review_auto_config(
                    platform="ctrip",
                    patch=ReviewAutoConfigPatch(
                        auto_reply_mode="semi_auto",
                        approval_chat_id="oc_review_chat",
                    ),
                )
                self.assertEqual(updated["auto_reply_mode"], "semi_auto")
                self.assertEqual(updated["approval_chat_id"], "oc_review_chat")
                self.assertFalse(updated["auto_fetch_enabled"])

                again = await repo.get_review_auto_config(platform="ctrip")
                self.assertEqual(again["auto_reply_mode"], "semi_auto")

            asyncio.run(scenario())

    def test_template_crud_and_enabled_filter(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repo = _reload_repository(db_url)
                from app.domain.review_management.types import ReplyTemplateInput

                created = await repo.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="good",
                        name="好评通用",
                        content="感谢您 {user_nickname} 的认可",
                    )
                )
                self.assertIsNotNone(created["id"])
                self.assertEqual(created["source"], "manual")
                self.assertTrue(created["enabled"])

                updated = await repo.save_template(
                    template=ReplyTemplateInput(
                        id=created["id"],
                        platform="ctrip",
                        category="good",
                        name="好评通用改",
                        content="谢谢 {user_nickname}",
                        score_range={"min": 4, "max": 5},
                    )
                )
                self.assertEqual(updated["id"], created["id"])
                self.assertEqual(updated["name"], "好评通用改")
                self.assertEqual(updated["score_range"], {"min": 4, "max": 5})

                await repo.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="bad",
                        name="差评致歉",
                        content="很抱歉给您带来不好的体验",
                        enabled=False,
                        source="imported",
                    )
                )

                self.assertEqual(len(await repo.list_templates(platform="ctrip")), 2)
                enabled = await repo.list_templates(platform="ctrip", only_enabled=True)
                self.assertEqual({row["name"] for row in enabled}, {"好评通用改"})
                bad = await repo.list_templates(platform="ctrip", category="bad")
                self.assertEqual(len(bad), 1)
                self.assertEqual(bad[0]["source"], "imported")

                self.assertTrue(await repo.delete_template(template_id=created["id"]))
                self.assertFalse(await repo.delete_template(template_id=created["id"]))
                self.assertEqual(len(await repo.list_templates(platform="ctrip")), 1)

            asyncio.run(scenario())

    def test_task_lifecycle(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repo = _reload_repository(db_url)
                task = await repo.create_review_task(platform="ctrip", task_type="fetch")
                self.assertEqual(task["status"], "running")

                done = await repo.complete_review_task(
                    task_id=task["id"],
                    status="completed",
                    review_count=5,
                    result_json={"inserted": 3, "updated": 2},
                )
                self.assertEqual(done["status"], "completed")
                self.assertEqual(done["review_count"], 5)
                self.assertIsNotNone(done["completed_at"])

                tasks = await repo.list_review_tasks(limit=10)
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["result"]["inserted"], 3)

                failed = await repo.create_review_task(platform="meituan", task_type="fetch")
                await repo.complete_review_task(
                    task_id=failed["id"],
                    status="failed",
                    error_msg="平台评价接口未接入",
                )
                meituan = await repo.list_review_tasks(platform="meituan", limit=10)
                self.assertEqual(meituan[0]["status"], "failed")
                self.assertEqual(meituan[0]["error_msg"], "平台评价接口未接入")

            asyncio.run(scenario())


def _reload_repository(db_url: str):
    db_session = _reload_db(db_url)
    repository = importlib.import_module("app.domain.review_management.repository")
    repository = importlib.reload(repository)
    assert db_session is not None
    return repository


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
