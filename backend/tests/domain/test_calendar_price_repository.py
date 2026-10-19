from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.domain.calendar_price_watch.types import (
    CalendarPriceWatchConfigPatch,
    DEFAULT_HIGH_GAP_THRESHOLD_YUAN,
    DEFAULT_LOW_GAP_THRESHOLD_YUAN,
    MAX_CALENDAR_FUTURE_DAYS,
)


class CalendarPriceRepositoryTests(unittest.TestCase):
    def test_get_or_create_config_uses_operator_defaults(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                config = await repository.get_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                )
                assert config is not None
                self.assertEqual(config["watch_group_key"], "ops_watch")
                self.assertEqual(config["future_days"], 15)
                self.assertEqual(config["high_gap_threshold_yuan"], DEFAULT_HIGH_GAP_THRESHOLD_YUAN)
                self.assertEqual(config["low_gap_threshold_yuan"], DEFAULT_LOW_GAP_THRESHOLD_YUAN)
                self.assertFalse(config["auto_poll_enabled"])

            asyncio.run(scenario())

    def test_list_config_summaries_includes_groups_without_config(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                summaries = await repository.list_calendar_price_watch_config_summaries()
                self.assertEqual(len(summaries), 1)
                self.assertEqual(summaries[0]["watch_group_key"], "ops_watch")
                self.assertEqual(summaries[0]["group_name"], "运营竞品盯盘")
                self.assertTrue(summaries[0]["group_enabled"])
                self.assertFalse(summaries[0]["auto_poll_enabled"])
                self.assertFalse(summaries[0]["config_present"])

                await repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=CalendarPriceWatchConfigPatch(auto_poll_enabled=True),
                )
                summaries = await repository.list_calendar_price_watch_config_summaries()
                self.assertTrue(summaries[0]["auto_poll_enabled"])
                self.assertTrue(summaries[0]["config_present"])

            asyncio.run(scenario())

    def test_patch_config_bounds_future_days_to_30(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                await repository.get_calendar_price_watch_config(watch_group_key="ops_watch")
                updated = await repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=CalendarPriceWatchConfigPatch(future_days=MAX_CALENDAR_FUTURE_DAYS),
                )
                assert updated is not None
                self.assertEqual(updated["future_days"], 30)

            asyncio.run(scenario())

    def test_patch_config_persists_calendar_browser_session_binding(self) -> None:
        with _isolated_sqlite_database() as db_url:
            with tempfile.TemporaryDirectory() as tmp_dir:
                previous_browser_state_dir = os.environ.get("BROWSER_STATE_DIR")
                os.environ["BROWSER_STATE_DIR"] = tmp_dir
                try:
                    _write_competitor_browser_session(
                        Path(tmp_dir) / "browser_sessions.json",
                        browser_session_id="browser-calendar-profile",
                        platform_account_id="ctrip-calendar-profile",
                    )
                    _reload_browser_session_modules()

                    async def scenario() -> None:
                        competitor_repository = _reload_competitor_repository(db_url)
                        repository = _reload_repository(db_url)
                        await _seed_watch_group(competitor_repository)
                        await repository.get_calendar_price_watch_config(watch_group_key="ops_watch")
                        updated = await repository.update_calendar_price_watch_config(
                            watch_group_key="ops_watch",
                            patch=CalendarPriceWatchConfigPatch(
                                browser_session_id="browser-calendar-profile",
                            ),
                        )
                        assert updated is not None
                        self.assertEqual(updated["browser_session_id"], "browser-calendar-profile")
                        self.assertEqual(updated["platform_account_id"], "ctrip-calendar-profile")
                        self.assertTrue(updated["browser_session_configured"])

                        reloaded = await repository.get_calendar_price_watch_config(
                            watch_group_key="ops_watch",
                        )
                        assert reloaded is not None
                        self.assertEqual(
                            reloaded["browser_session_id"],
                            "browser-calendar-profile",
                        )
                        self.assertEqual(
                            reloaded["platform_account_id"],
                            "ctrip-calendar-profile",
                        )

                    asyncio.run(scenario())
                finally:
                    if previous_browser_state_dir is None:
                        os.environ.pop("BROWSER_STATE_DIR", None)
                    else:
                        os.environ["BROWSER_STATE_DIR"] = previous_browser_state_dir
                    _reload_browser_session_modules()

    def test_save_run_points_and_latest_matrix_omits_secrets(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                config = await repository.get_calendar_price_watch_config(watch_group_key="ops_watch")
                assert config is not None
                hotels = await competitor_repository.list_competitor_hotels(
                    watch_group_key="ops_watch",
                )
                own_hotel = next(item for item in hotels if item["role"] == "own")
                competitor = next(item for item in hotels if item["role"] == "competitor")
                start_date = date(2026, 7, 8)
                saved = await repository.save_calendar_price_run(
                    watch_group_key="ops_watch",
                    config=config,
                    result={
                        "status": "success",
                        "summary": "读取到 2 个可信日历价。",
                        "platform_account_id": "ctrip-public",
                        "browser_session_id": "browser-public",
                        "points": [
                            {
                                "hotel_config_id": own_hotel["id"],
                                "role": "own",
                                "hotel_alias": own_hotel["alias"],
                                "checkin_date": start_date.isoformat(),
                                "price_amount_yuan": 420,
                                "currency": "CNY",
                                "source": "calendar_dom_cell_price",
                                "is_trusted": True,
                                "status": "success",
                            },
                            {
                                "hotel_config_id": competitor["id"],
                                "role": "competitor",
                                "hotel_alias": competitor["alias"],
                                "checkin_date": start_date.isoformat(),
                                "price_amount_yuan": 410,
                                "currency": "CNY",
                                "source": "calendar_dom_cell_price",
                                "is_trusted": True,
                                "status": "success",
                            },
                        ],
                        "alerts": [],
                        "start_date": start_date.isoformat(),
                        "evidence": {
                            "screenshot_path": "C:/secret/screenshot.png",
                            "webhook_url": "https://open.feishu.cn/secret",
                        },
                    },
                    actor_id="test",
                    source="test",
                )
                self.assertNotIn("screenshot_path", str(saved).lower())
                self.assertNotIn("webhook", str(saved).lower())

                matrix = await repository.get_latest_calendar_price_matrix(
                    watch_group_key="ops_watch",
                )
                assert matrix is not None
                self.assertEqual(matrix["trusted_source"], "calendar_dom_cell_price")
                self.assertNotIn("webhook", str(matrix).lower())
                self.assertNotIn("screenshot_path", str(matrix).lower())
                self.assertEqual(matrix["dates"][0]["own"]["price_amount_yuan"], 420)

            asyncio.run(scenario())

    def test_latest_matrix_keeps_missing_dates_as_review_items(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                config = await repository.get_calendar_price_watch_config(watch_group_key="ops_watch")
                assert config is not None
                hotels = await competitor_repository.list_competitor_hotels(
                    watch_group_key="ops_watch",
                )
                own_hotel = next(item for item in hotels if item["role"] == "own")
                start_date = date(2026, 7, 8)
                await repository.save_calendar_price_run(
                    watch_group_key="ops_watch",
                    config=config,
                    result={
                        "status": "partial",
                        "summary": "仅读取到部分日历价。",
                        "platform_account_id": "ctrip-public",
                        "browser_session_id": "browser-public",
                        "points": [
                            {
                                "hotel_config_id": own_hotel["id"],
                                "role": "own",
                                "hotel_alias": own_hotel["alias"],
                                "checkin_date": start_date.isoformat(),
                                "price_amount_yuan": 420,
                                "currency": "CNY",
                                "source": "calendar_dom_cell_price",
                                "is_trusted": True,
                                "status": "success",
                            },
                        ],
                        "alerts": [],
                        "start_date": start_date.isoformat(),
                    },
                    actor_id="test",
                    source="test",
                )
                matrix = await repository.get_latest_calendar_price_matrix(
                    watch_group_key="ops_watch",
                )
                assert matrix is not None
                self.assertEqual(len(matrix["dates"]), config["future_days"])
                missing = [
                    item
                    for item in matrix["dates"]
                    if item["checkin_date"] != start_date.isoformat()
                ]
                self.assertTrue(missing)
                for item in missing:
                    self.assertEqual(item["own"]["status"], "needs_review")
                    self.assertIsNone(item["own"]["price_amount_yuan"])

            asyncio.run(scenario())

    def test_matrix_brief_morning_slot_due_once_per_day(self) -> None:
        repository = importlib.import_module("app.domain.calendar_price_watch.repository")
        morning = datetime(2026, 7, 9, 8, 5)
        config = SimpleNamespace(
            matrix_brief_last_pushed_json={},
        )
        self.assertTrue(
            repository._matrix_brief_slot_is_due("08:00", config.matrix_brief_last_pushed_json, morning)
        )
        config.matrix_brief_last_pushed_json = {"08:00": morning.isoformat()}
        self.assertFalse(
            repository._matrix_brief_slot_is_due("08:00", config.matrix_brief_last_pushed_json, morning)
        )
        next_day = morning + timedelta(days=1)
        self.assertTrue(
            repository._matrix_brief_slot_is_due("08:00", config.matrix_brief_last_pushed_json, next_day)
        )

    def test_enabling_matrix_brief_does_not_catch_up_past_slots_today(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                await repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=CalendarPriceWatchConfigPatch(
                        matrix_brief_enabled=False,
                        matrix_brief_push_times=["10:00", "15:00", "21:00"],
                    ),
                )
                # Turn on after morning slot has already passed today.
                await repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=CalendarPriceWatchConfigPatch(matrix_brief_enabled=True),
                    now=datetime(2026, 7, 9, 11, 41),
                )
                due = await repository.list_due_matrix_brief_configs(
                    now=datetime(2026, 7, 9, 11, 42)
                )
                self.assertEqual(due, [])
                # Afternoon slot later today should still fire.
                due_afternoon = await repository.list_due_matrix_brief_configs(
                    now=datetime(2026, 7, 9, 15, 1)
                )
                self.assertEqual(len(due_afternoon), 1)
                self.assertEqual(due_afternoon[0]["due_slot_time"], "15:00")

            asyncio.run(scenario())

    def test_list_due_matrix_brief_configs_returns_slot_metadata(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                await repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=CalendarPriceWatchConfigPatch(
                        matrix_brief_enabled=True,
                        matrix_brief_push_times=["10:00", "15:00", "21:00"],
                    ),
                    now=datetime(2026, 7, 9, 9, 0),
                )
                due = await repository.list_due_matrix_brief_configs(
                    now=datetime(2026, 7, 9, 10, 10)
                )
                self.assertEqual(len(due), 1)
                self.assertEqual(due[0]["due_slot_time"], "10:00")
                self.assertEqual(due[0]["matrix_brief_push_times"], ["10:00", "15:00", "21:00"])

            asyncio.run(scenario())

    def test_saving_new_push_times_while_enabled_does_not_catch_up_past_slots(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                await repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=CalendarPriceWatchConfigPatch(
                        matrix_brief_enabled=True,
                        matrix_brief_push_times=["08:00", "18:00"],
                    ),
                    now=datetime(2026, 7, 9, 7, 0),
                )
                # Operator later switches to 10/15/21 after 15:00 has already passed.
                await repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=CalendarPriceWatchConfigPatch(
                        matrix_brief_push_times=["10:00", "15:00", "21:00"],
                    ),
                    now=datetime(2026, 7, 9, 16, 30),
                )
                due = await repository.list_due_matrix_brief_configs(
                    now=datetime(2026, 7, 9, 16, 31)
                )
                self.assertEqual(due, [])
                due_evening = await repository.list_due_matrix_brief_configs(
                    now=datetime(2026, 7, 9, 21, 1)
                )
                self.assertEqual(len(due_evening), 1)
                self.assertEqual(due_evening[0]["due_slot_time"], "21:00")

            asyncio.run(scenario())

    def test_enabling_with_push_times_in_same_save_skips_past_slots(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                competitor_repository = _reload_competitor_repository(db_url)
                repository = _reload_repository(db_url)
                await _seed_watch_group(competitor_repository)
                await repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=CalendarPriceWatchConfigPatch(
                        matrix_brief_enabled=True,
                        matrix_brief_push_times=["10:00", "15:00", "21:00"],
                    ),
                    now=datetime(2026, 7, 9, 16, 30),
                )
                due = await repository.list_due_matrix_brief_configs(
                    now=datetime(2026, 7, 9, 16, 31)
                )
                self.assertEqual(due, [])

            asyncio.run(scenario())

    def test_matrix_brief_push_times_normalize_dedupe_and_require_one(self) -> None:
        repository = importlib.import_module("app.domain.calendar_price_watch.repository")
        self.assertEqual(
            repository._normalize_matrix_brief_push_times(
                ["21:00", "10:00", "10:00", "15:00"]
            ),
            ["10:00", "15:00", "21:00"],
        )
        with self.assertRaises(ValueError):
            repository._normalize_matrix_brief_push_times([])

async def _seed_watch_group(repository) -> None:
    types = importlib.import_module("app.domain.competitor_prices.types")
    await repository.create_competitor_watch_group(
        types.CompetitorWatchGroupCreate(
            group_key="ops_watch",
            name="运营竞品盯盘",
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
    await repository.upsert_competitor_hotel_config(
        platform_account_id="ctrip_public_account",
        watch_group_key="ops_watch",
        hotel=types.CompetitorHotelConfigIn(
            role="competitor",
            alias="竞对A",
            detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
            sort_order=10,
        ),
    )


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "calendar-repo.db"
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


def _reload_competitor_repository(db_url: str):
    _reload_db_session(db_url)
    repository = importlib.import_module("app.domain.competitor_prices.repository")
    return importlib.reload(repository)


def _reload_repository(db_url: str):
    _reload_db_session(db_url)
    repository = importlib.import_module("app.domain.calendar_price_watch.repository")
    return importlib.reload(repository)


def _write_competitor_browser_session(
    state_path: Path,
    *,
    browser_session_id: str,
    platform_account_id: str,
) -> None:
    state_path.write_text(
        json.dumps(
            {
                "sessions": {
                    browser_session_id: {
                        "browser_session_id": browser_session_id,
                        "platform_account_id": platform_account_id,
                        "display_name": "Calendar profile",
                        "platform": "ctrip",
                        "session_port": 9229,
                        "intended_use": "competitor_price",
                        "login_state": "logged_in",
                        "human_verification_status": "none",
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _reload_browser_session_modules() -> None:
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    sessions = importlib.import_module("app.domain.browser.sessions")
    importlib.reload(sessions)
    browser_binding = importlib.import_module("app.domain.competitor_prices.browser_binding")
    importlib.reload(browser_binding)


if __name__ == "__main__":
    unittest.main()
