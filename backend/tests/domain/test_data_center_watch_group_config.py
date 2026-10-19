from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class DataCenterWatchGroupConfigTests(unittest.TestCase):
    def test_watch_group_crud_and_webhook_masking(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)

                created = await repository.create_data_center_watch_group(
                    repository.DataCenterWatchGroupCreate(
                        group_key="ebk_ops_watch",
                        name="澜江路 EBK 数据中心",
                        brief_title="澜江路 EBK 经营简报",
                        daily_brief_title="澜江路 EBK 数据简报",
                        daily_brief_push_time="09:15",
                        daily_brief_enabled=True,
                        platform_account_id="ctrip_ebk_account",
                        feishu_webhook_url="https://open.feishu.cn/ebk-secret-token",
                    )
                )
                self.assertIsNotNone(created)
                assert created is not None
                self.assertEqual(created["group_key"], "ebk_ops_watch")
                self.assertEqual(created["daily_brief_title"], "澜江路 EBK 数据简报")
                self.assertEqual(created["daily_brief_push_time"], "09:15")
                self.assertTrue(created["daily_brief_enabled"])
                self.assertTrue(created["feishu_webhook_configured"])
                self.assertNotIn("feishu_webhook_url", created)

                groups = await repository.list_data_center_watch_group_summaries()
                self.assertEqual([group["group_key"] for group in groups], ["ebk_ops_watch"])
                self.assertIsNone(groups[0]["latest_run"])
                self.assertGreaterEqual(len(groups[0]["datasets"]), 1)

                updated = await repository.update_data_center_watch_group(
                    watch_group_key="ebk_ops_watch",
                    patch=repository.DataCenterWatchGroupPatch(
                        feishu_webhook_url="",
                        daily_brief_enabled=False,
                    ),
                )
                assert updated is not None
                self.assertFalse(updated["feishu_webhook_configured"])
                self.assertFalse(updated["daily_brief_enabled"])

                deleted = await repository.delete_data_center_watch_group(
                    watch_group_key="ebk_ops_watch",
                )
                assert deleted is not None
                self.assertEqual(deleted["group_key"], "ebk_ops_watch")
                missing = await repository.delete_data_center_watch_group(
                    watch_group_key="ebk_ops_watch",
                )
                self.assertIsNone(missing)

            asyncio.run(scenario())

    def test_deleted_group_is_not_recreated_by_manual_runs(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                service_module = _reload_module("app.domain.ctrip_ebooking_watch.service")
                scheduler_module = _reload_module("app.domain.ctrip_ebooking_watch.scheduler")

                created = await repository.create_data_center_watch_group(
                    repository.DataCenterWatchGroupCreate(
                        group_key="deleted_watch",
                        name="Deleted watch",
                        platform_account_id="ctrip_ebk_account",
                    )
                )
                self.assertIsNotNone(created)
                await repository.delete_data_center_watch_group(watch_group_key="deleted_watch")

                read_result = await service_module.get_data_center_watch_service().run_watch_group_now(
                    watch_group_key="deleted_watch",
                    push_to_feishu=False,
                )
                brief_result = await scheduler_module.CtripEbookingDataCenterWatchScheduler(
                    tick_seconds=1,
                ).run_daily_brief_now("deleted_watch")
                groups = await repository.list_data_center_watch_group_summaries()

                self.assertEqual(read_result["status"], "not_found")
                self.assertEqual(brief_result["status"], "not_found")
                self.assertNotIn("deleted_watch", [group["group_key"] for group in groups])

            asyncio.run(scenario())

    def test_group_summary_tags_unregistered_browser_session(self) -> None:
        """Summaries must expose a session_status field so the operator console
        can show "会话未注册" instead of surfacing Unknown browser session errors."""
        from types import SimpleNamespace
        from unittest.mock import patch

        def fake_session(browser_session_id: str):
            return SimpleNamespace(
                browser_session_id=browser_session_id,
                platform="ctrip",
                intended_use="ebk",
                platform_account_id="ctrip_ebk_account",
            )

        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)

                class FakeStore:
                    def __init__(self) -> None:
                        # Both ids valid at create time; one is dropped before
                        # listing so summaries see it as stale/unregistered.
                        self._known = {"browser-ebk-missing", "browser-ebk-ok"}

                    def get(self, browser_session_id: str):
                        if browser_session_id in self._known:
                            return fake_session(browser_session_id)
                        raise KeyError(
                            f"Unknown browser session: {browser_session_id}"
                        )

                store = FakeStore()
                with patch.object(
                    repository, "get_browser_session_store", lambda: store
                ):
                    await repository.create_data_center_watch_group(
                        repository.DataCenterWatchGroupCreate(
                            group_key="ebk_unregistered",
                            name="未注册会话监控组",
                            platform_account_id="ctrip_ebk_account",
                            browser_session_id="browser-ebk-missing",
                        )
                    )
                    await repository.create_data_center_watch_group(
                        repository.DataCenterWatchGroupCreate(
                            group_key="ebk_registered",
                            name="已注册会话监控组",
                            platform_account_id="ctrip_ebk_account",
                            browser_session_id="browser-ebk-ok",
                        )
                    )
                    # Now drop the "missing" id to simulate a stale binding.
                    store._known.discard("browser-ebk-missing")
                    groups = await repository.list_data_center_watch_group_summaries()
                by_key = {group["group_key"]: group for group in groups}
                # Stale session id -> unregistered (raw id preserved, not rewritten).
                self.assertEqual(
                    by_key["ebk_unregistered"]["session_status"], "unregistered"
                )
                self.assertEqual(
                    by_key["ebk_unregistered"]["browser_session_id"],
                    "browser-ebk-missing",
                )
                # Known session id -> registered.
                self.assertEqual(
                    by_key["ebk_registered"]["session_status"], "registered"
                )

            asyncio.run(scenario())

    def test_summaries_include_secrets_exposes_raw_feishu_webhook_url(self) -> None:
        """include_secrets=True must expose the raw feishu_webhook_url so the
        supervisor overview can show it to authenticated admins. The default
        (non-supervisor) call must NOT leak it."""
        from types import SimpleNamespace
        from unittest.mock import patch

        def fake_session(browser_session_id: str):
            return SimpleNamespace(
                browser_session_id=browser_session_id,
                platform="ctrip",
                intended_use="ebk",
                platform_account_id="ctrip_ebk_account",
            )

        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)

                class FakeStore:
                    def get(self, browser_session_id: str):
                        return fake_session(browser_session_id)

                with patch.object(
                    repository, "get_browser_session_store", lambda: FakeStore()
                ):
                    await repository.create_data_center_watch_group(
                        repository.DataCenterWatchGroupCreate(
                            group_key="ebk_webhook_group",
                            name="Webhook 可见性监控组",
                            platform_account_id="ctrip_ebk_account",
                            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/ebk-secret-token",
                        )
                    )

                    # Default call (non-supervisor context) must NOT leak the raw URL.
                    default_summaries = await repository.list_data_center_watch_group_summaries()
                    default_group = default_summaries[0]
                    self.assertNotIn("feishu_webhook_url", default_group)
                    self.assertTrue(default_group["feishu_webhook_configured"])

                    # include_secrets=True (supervisor context) must expose the raw URL.
                    secret_summaries = (
                        await repository.list_data_center_watch_group_summaries(
                            include_secrets=True
                        )
                    )
                    secret_group = secret_summaries[0]
                    self.assertEqual(
                        secret_group["feishu_webhook_url"],
                        "https://open.feishu.cn/open-apis/bot/v2/hook/ebk-secret-token",
                    )

            asyncio.run(scenario())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "test.sqlite3"
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
        self.directory.cleanup()


def _reload_repository(db_url: str):
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings_module.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    importlib.import_module("app.domain.ctrip_ebooking_watch.models")
    repository = importlib.import_module("app.domain.ctrip_ebooking_watch.repository")
    return importlib.reload(repository)


def _reload_module(module_name: str):
    return importlib.reload(importlib.import_module(module_name))
