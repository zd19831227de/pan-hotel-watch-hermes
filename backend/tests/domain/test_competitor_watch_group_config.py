from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CompetitorWatchGroupConfigTests(unittest.TestCase):
    def test_watch_group_config_fields_are_persisted_and_masked(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                types = importlib.import_module("app.domain.competitor_prices.types")

                created = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="运营竞品盯盘",
                        brief_title="澜江路竞品价格播报",
                        price_change_alert_title="澜江路价格变化提醒",
                        platform_account_id="ctrip_public_account",
                        feishu_mobile_webhook_url="https://open.feishu.cn/mobile-secret-token",
                        feishu_desktop_webhook_url="https://open.feishu.cn/desktop-secret-token",
                        feishu_card_layout="desktop_table",
                        schedule_mode="fixed_slots",
                        schedule_start_time="06:00",
                        schedule_end_time="01:00",
                        schedule_interval_minutes=120,
                    )
                )
                self.assertIsNotNone(created)
                assert created is not None
                self.assertEqual(created["brief_title"], "澜江路竞品价格播报")
                self.assertEqual(created["price_change_alert_title"], "澜江路价格变化提醒")
                self.assertEqual(created["feishu_card_layout"], "desktop_table")
                self.assertTrue(created["feishu_mobile_webhook_configured"])
                self.assertTrue(created["feishu_desktop_webhook_configured"])
                self.assertNotIn("feishu_mobile_webhook_url", created)
                self.assertNotIn("feishu_desktop_webhook_url", created)
                self.assertEqual(created["schedule_mode"], "fixed_slots")
                self.assertEqual(created["schedule_start_time"], "06:00")
                self.assertEqual(created["schedule_end_time"], "01:00")
                self.assertEqual(created["schedule_interval_minutes"], 120)
                self.assertIn("schedule_state", created)
                self.assertIsNone(created["pricing_platform_account_id"])
                self.assertIsNone(created["pricing_browser_session_id"])
                self.assertFalse(created["pricing_account_configured"])

                updated = await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        price_change_alert_title="汉埠门店价格变化提醒",
                    ),
                )
                assert updated is not None
                self.assertEqual(updated["brief_title"], "澜江路竞品价格播报")
                self.assertEqual(updated["price_change_alert_title"], "汉埠门店价格变化提醒")

                secret_detail = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                    include_secrets=True,
                )
                assert secret_detail is not None
                self.assertEqual(
                    secret_detail["feishu_mobile_webhook_url"],
                    "https://open.feishu.cn/mobile-secret-token",
                )
                self.assertEqual(
                    secret_detail["feishu_desktop_webhook_url"],
                    "https://open.feishu.cn/desktop-secret-token",
                )

            asyncio.run(scenario())

    def test_push_date_scope_is_persisted_and_defaults_to_today_tomorrow(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                types = importlib.import_module("app.domain.competitor_prices.types")

                created = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_scope",
                        name="推送范围盯盘",
                        platform_account_id="ctrip_public_account",
                        push_date_scope="today_only",
                    )
                )
                assert created is not None
                self.assertEqual(created["push_date_scope"], "today_only")

                default_group = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_scope_default",
                        name="默认范围盯盘",
                        platform_account_id="ctrip_public_account",
                    )
                )
                assert default_group is not None
                self.assertEqual(default_group["push_date_scope"], "today_tomorrow")

                updated = await repository.update_competitor_watch_group(
                    watch_group_key="ops_scope",
                    patch=types.CompetitorWatchGroupPatch(
                        push_date_scope="today_tomorrow",
                    ),
                )
                assert updated is not None
                self.assertEqual(updated["push_date_scope"], "today_tomorrow")

            asyncio.run(scenario())

    def test_schema_migration_adds_watch_group_columns_to_existing_table(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                db_session = _reload_db_session(db_url)
                from sqlalchemy import text

                async with db_session.engine.begin() as connection:
                    await connection.execute(
                        text(
                            "CREATE TABLE ctrip_competitor_watch_groups ("
                            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                            "group_key VARCHAR(64) NOT NULL UNIQUE, "
                            "name VARCHAR(128) NOT NULL, "
                            "platform_account_id VARCHAR(128) NOT NULL, "
                            "enabled BOOLEAN NOT NULL DEFAULT 1, "
                            "created_at DATETIME NOT NULL, "
                            "updated_at DATETIME NOT NULL)"
                        )
                    )

                await db_session.ensure_db_ready()

                async with db_session.engine.begin() as connection:
                    rows = await connection.execute(
                        text("PRAGMA table_info(ctrip_competitor_watch_groups)")
                    )
                    columns = {str(row[1]) for row in rows}

                expected = {
                    "brief_title",
                    "browser_session_id",
                    "feishu_mobile_webhook_url",
                    "feishu_desktop_webhook_url",
                    "feishu_card_layout",
                    "schedule_mode",
                    "schedule_start_time",
                    "schedule_end_time",
                    "schedule_interval_minutes",
                    "auto_poll_enabled",
                    "last_polled_at",
                    "pricing_platform_account_id",
                    "pricing_browser_session_id",
                    "price_change_alert_title",
                }
                self.assertTrue(expected.issubset(columns), sorted(expected - columns))

            asyncio.run(scenario())

    def test_watch_group_binds_pricing_ebk_account(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
                    sessions,
                    browser_session_id="browser-public-1",
                    platform_account_id="ctrip-public-1",
                    session_port=9321,
                )
                _restore_ebk_browser_session(
                    sessions,
                    browser_session_id="browser-ebk-1",
                    platform_account_id="ctrip-ebk-1",
                    session_port=9421,
                    connection_mode="non_direct",
                )

                created = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="竞品监控组",
                        browser_session_id="browser-public-1",
                        pricing_browser_session_id="browser-ebk-1",
                    )
                )

                self.assertIsNotNone(created)
                assert created is not None
                self.assertEqual(created["platform_account_id"], "ctrip-public-1")
                self.assertEqual(created["browser_session_id"], "browser-public-1")
                self.assertEqual(created["pricing_platform_account_id"], "ctrip-ebk-1")
                self.assertEqual(created["pricing_browser_session_id"], "browser-ebk-1")
                self.assertTrue(created["pricing_account_configured"])
                self.assertEqual(created["pricing_connection_mode"], "non_direct")
                self.assertTrue(created["pricing_can_auto_write_price"])

                updated = await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        pricing_browser_session_id="",
                    ),
                )
                assert updated is not None
                self.assertIsNone(updated["pricing_platform_account_id"])
                self.assertIsNone(updated["pricing_browser_session_id"])
                self.assertFalse(updated["pricing_account_configured"])

                rebound = await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        pricing_browser_session_id="browser-ebk-1",
                    ),
                )
                assert rebound is not None
                self.assertEqual(rebound["pricing_platform_account_id"], "ctrip-ebk-1")
                self.assertEqual(rebound["pricing_browser_session_id"], "browser-ebk-1")
                self.assertTrue(rebound["pricing_account_configured"])
                self.assertEqual(rebound["pricing_connection_mode"], "non_direct")
                self.assertTrue(rebound["pricing_can_auto_write_price"])

            asyncio.run(scenario())

    def test_watch_group_can_be_resolved_by_feishu_chat_id(self) -> None:
        with _configured_test_agent():
            with _isolated_sqlite_database() as db_url:
                async def scenario() -> None:
                    repository = _reload_repository(db_url)
                    types = importlib.import_module("app.domain.competitor_prices.types")
                    await _create_active_agent_binding("oc_room_a")

                    created = await repository.create_competitor_watch_group(
                        types.CompetitorWatchGroupCreate(
                            group_key="room_a_watch",
                            name="A 酒店监控组",
                            platform_account_id="ctrip-public-a",
                            feishu_chat_id="oc_room_a",
                        )
                    )
                    self.assertIsNotNone(created)

                    resolved = await repository.get_competitor_watch_group_by_feishu_chat_id(
                        "oc_room_a"
                    )

                    self.assertIsNotNone(resolved)
                    assert resolved is not None
                    self.assertEqual(resolved["group_key"], "room_a_watch")
                    self.assertEqual(resolved["feishu_chat_id_configured"], True)
                    self.assertNotIn("oc_room_a", str(resolved))

                asyncio.run(scenario())

    def test_watch_group_feishu_chat_id_resolution_is_ambiguous_when_shared(self) -> None:
        with _configured_test_agent():
            with _isolated_sqlite_database() as db_url:
                async def scenario() -> None:
                    repository = _reload_repository(db_url)
                    types = importlib.import_module("app.domain.competitor_prices.types")
                    await _create_active_agent_binding("oc_shared_group")

                    for group_key, name in (
                        ("watch_alpha", "Alpha 监控组"),
                        ("watch_beta", "Beta 监控组"),
                    ):
                        created = await repository.create_competitor_watch_group(
                            types.CompetitorWatchGroupCreate(
                                group_key=group_key,
                                name=name,
                                platform_account_id=f"ctrip-public-{group_key}",
                                feishu_chat_id="oc_shared_group",
                            )
                        )
                        self.assertIsNotNone(created)

                    groups = await repository.list_competitor_watch_groups_by_feishu_chat_id(
                        "oc_shared_group"
                    )
                    self.assertEqual(len(groups), 2)

                    resolved = await repository.get_competitor_watch_group_by_feishu_chat_id(
                        "oc_shared_group"
                    )
                    self.assertIsNone(resolved)

                asyncio.run(scenario())

    def test_watch_group_rejects_non_ebk_pricing_account(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
                    sessions,
                    browser_session_id="browser-public-1",
                    platform_account_id="ctrip-public-1",
                    session_port=9321,
                )

                with self.assertRaisesRegex(ValueError, "EBK"):
                    await repository.create_competitor_watch_group(
                        types.CompetitorWatchGroupCreate(
                            group_key="ops_watch",
                            name="竞品监控组",
                            pricing_browser_session_id="browser-public-1",
                        )
                    )

            asyncio.run(scenario())

    def test_enabled_watch_groups_can_share_browser_session(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
                    sessions,
                    browser_session_id="browser-public-1",
                    platform_account_id="ctrip-public-1",
                    session_port=9321,
                )

                first = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_a",
                        name="竞品监控组 A",
                        browser_session_id="browser-public-1",
                        enabled=True,
                    )
                )
                second = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_b",
                        name="竞品监控组 B",
                        browser_session_id="browser-public-1",
                        enabled=True,
                    )
                )

                self.assertEqual(first["browser_session_id"], "browser-public-1")
                self.assertEqual(second["browser_session_id"], "browser-public-1")

            asyncio.run(scenario())

    def test_disabled_duplicate_browser_binding_can_be_enabled_together(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
                    sessions,
                    browser_session_id="browser-public-1",
                    platform_account_id="ctrip-public-1",
                    session_port=9321,
                )

                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_a",
                        name="竞品监控组 A",
                        browser_session_id="browser-public-1",
                        enabled=True,
                    )
                )
                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_b",
                        name="竞品监控组 B",
                        browser_session_id="browser-public-1",
                        enabled=False,
                    )
                )

                enabled = await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch_b",
                    patch=types.CompetitorWatchGroupPatch(enabled=True),
                )

                self.assertTrue(enabled["enabled"])
                self.assertEqual(enabled["browser_session_id"], "browser-public-1")

            asyncio.run(scenario())

    def test_delete_watch_group_removes_config_and_price_history(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                types = importlib.import_module("app.domain.competitor_prices.types")
                db_session = importlib.import_module("app.db.session")
                models = importlib.import_module("app.domain.competitor_prices.models")
                from sqlalchemy import func, select

                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="竞品监控组",
                        platform_account_id="ctrip_public_account",
                    )
                )
                await repository.upsert_competitor_hotel_config(
                    platform_account_id="ctrip_public_account",
                    watch_group_key="ops_watch",
                    hotel=types.CompetitorHotelConfigIn(
                        role="own",
                        alias="本店",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                        sort_order=0,
                    ),
                )
                await repository.save_ctrip_competitor_price_result(
                    result_status="success",
                    summary="ok",
                    platform_account_id="ctrip_public_account",
                    browser_session_id="browser_public",
                    actor_id="test",
                    watch_group_key="ops_watch",
                    data={
                        "captured_at": "2026-06-18T08:00:00",
                        "targets": [
                            {
                                "role": "own",
                                "label": "本店",
                                "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                            },
                        ],
                        "snapshots": [
                            {
                                "role": "own",
                                "hotel_name": "本店",
                                "status": "success",
                                "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                                "current_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                                "date_label": "today",
                                "date_context": {
                                    "check_in": "2026-06-18",
                                    "check_out": "2026-06-19",
                                },
                                "room_rates": [
                                    {
                                        "source": "room_rate_dom_display_price",
                                        "room_type": "高级大床房",
                                        "breakfast": "无早",
                                        "breakfast_kind": "none",
                                        "price": {"amount": 416, "currency": "CNY"},
                                        "is_trusted": True,
                                    },
                                ],
                            },
                        ],
                    },
                    evidence={"parser_version": "test"},
                )

                deleted = await repository.delete_competitor_watch_group(
                    watch_group_key="ops_watch",
                )
                self.assertIsNotNone(deleted)
                assert deleted is not None
                self.assertEqual(deleted["group_key"], "ops_watch")

                self.assertIsNone(
                    await repository.get_competitor_watch_group_detail(
                        watch_group_key="ops_watch",
                        create_if_missing=False,
                    )
                )

                async with db_session.session_scope() as session:
                    counts = {
                        "groups": await session.scalar(
                            select(func.count(models.CtripCompetitorWatchGroup.id))
                        ),
                        "hotels": await session.scalar(
                            select(func.count(models.CtripCompetitorHotel.id))
                        ),
                        "runs": await session.scalar(
                            select(func.count(models.CtripCompetitorPriceRun.id))
                        ),
                        "snapshots": await session.scalar(
                            select(func.count(models.CtripCompetitorHotelSnapshot.id))
                        ),
                        "room_rates": await session.scalar(
                            select(func.count(models.CtripCompetitorRoomRate.id))
                        ),
                    }
                self.assertEqual(counts, {
                    "groups": 0,
                    "hotels": 0,
                    "runs": 0,
                    "snapshots": 0,
                    "room_rates": 0,
                })

            asyncio.run(scenario())

    def test_disabled_competitor_hotel_is_hidden_by_default_and_can_be_restored(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                types = importlib.import_module("app.domain.competitor_prices.types")

                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="运营竞品盯盘",
                        platform_account_id="ctrip_public_account",
                    )
                )
                own = await repository.upsert_competitor_hotel_config(
                    platform_account_id="ctrip_public_account",
                    watch_group_key="ops_watch",
                    hotel=types.CompetitorHotelConfigIn(
                        role="own",
                        alias="本店",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                        sort_order=0,
                    ),
                )
                competitor = await repository.upsert_competitor_hotel_config(
                    platform_account_id="ctrip_public_account",
                    watch_group_key="ops_watch",
                    hotel=types.CompetitorHotelConfigIn(
                        role="competitor",
                        alias="竞品A",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                        sort_order=10,
                    ),
                )

                removed = await repository.delete_competitor_hotel_config(
                    hotel_config_id=competitor["id"],
                    watch_group_key="ops_watch",
                )
                assert removed is not None
                self.assertFalse(removed["enabled"])

                active_only = await repository.list_competitor_hotels(
                    watch_group_key="ops_watch",
                )
                self.assertEqual([item["id"] for item in active_only], [own["id"]])

                with_disabled = await repository.list_competitor_hotels(
                    watch_group_key="ops_watch",
                    include_disabled=True,
                )
                self.assertEqual(len(with_disabled), 2)

                with self.assertRaisesRegex(ValueError, "本店配置不能从列表移除"):
                    await repository.delete_competitor_hotel_config(
                        hotel_config_id=own["id"],
                        watch_group_key="ops_watch",
                    )

            asyncio.run(scenario())


def _configured_test_agent():
    return mock.patch.dict(
        os.environ,
        {
            "FEISHU_APP_CREDENTIALS_JSON": json.dumps(
                {
                    "test-agent": {
                        "app_id": "cli_test_agent",
                        "app_secret": "test_agent_secret",
                    }
                }
            )
        },
        clear=False,
    )


async def _create_active_agent_binding(chat_id: str) -> None:
    service = importlib.import_module("app.domain.config.service")
    await service.create_feishu_agent_profile_payload(
        service.FeishuAgentProfileCreate(
            profile_key="test-agent",
            display_name="Test Agent",
            enabled=True,
        )
    )
    await service.create_feishu_chat_agent_binding_payload(
        service.FeishuChatAgentBindingCreate(
            chat_id=chat_id,
            profile_key="test-agent",
            display_name="Test business chat",
            enabled=True,
        )
    )


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watch-test.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        self._previous_browser_state_dir = os.environ.get("BROWSER_STATE_DIR")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        os.environ["BROWSER_STATE_DIR"] = str(Path(self._tmp.name) / "browser-state")
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
        if self._previous_browser_state_dir is None:
            os.environ.pop("BROWSER_STATE_DIR", None)
        else:
            os.environ["BROWSER_STATE_DIR"] = self._previous_browser_state_dir
        _reload_browser_sessions()
        # Restore the global app.db.session engine so later tests in this
        # process do not inherit a stale engine pointing at the temp file we
        # are about to delete. Must run before the temp dir cleanup.
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db_session(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return db_session


def _reload_repository(db_url: str):
    _reload_db_session(db_url)
    repository = importlib.import_module("app.domain.competitor_prices.repository")
    return importlib.reload(repository)


def _reload_browser_sessions():
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    sessions = importlib.import_module("app.domain.browser.sessions")
    return importlib.reload(sessions)


def _restore_competitor_browser_session(
    sessions,
    *,
    browser_session_id: str,
    platform_account_id: str,
    session_port: int,
) -> None:
    sessions.get_browser_session_store().restore(
        sessions.BrowserSession.model_validate(
            {
                "browser_session_id": browser_session_id,
                "platform_account_id": platform_account_id,
                "display_name": platform_account_id,
                "platform": "ctrip",
                "session_port": session_port,
                "intended_use": "competitor_price",
                "login_state": "logged_in",
                "human_verification_status": "none",
                "last_heartbeat_at": "2026-06-17T00:00:00+00:00",
                "takeover_url": f"/browser-control?session={browser_session_id}",
                "manual_takeover_url": (
                    f"/browser-control?session={browser_session_id}&mode=manual"
                ),
                "can_takeover": True,
                "can_logout": True,
                "last_action": "Test session.",
            }
        )
    )


def _restore_ebk_browser_session(
    sessions,
    *,
    browser_session_id: str,
    platform_account_id: str,
    session_port: int,
    connection_mode: str = "unknown",
) -> None:
    sessions.get_browser_session_store().restore(
        sessions.BrowserSession.model_validate(
            {
                "browser_session_id": browser_session_id,
                "platform_account_id": platform_account_id,
                "display_name": platform_account_id,
                "platform": "ctrip",
                "session_port": session_port,
                "intended_use": "ebk",
                "connection_mode": connection_mode,
                "login_state": "logged_in",
                "human_verification_status": "none",
                "last_heartbeat_at": "2026-06-17T00:00:00+00:00",
                "takeover_url": f"/browser-control?session={browser_session_id}",
                "manual_takeover_url": (
                    f"/browser-control?session={browser_session_id}&mode=manual"
                ),
                "can_takeover": True,
                "can_logout": True,
                "last_action": "Test session.",
            }
        )
    )


if __name__ == "__main__":
    unittest.main()
