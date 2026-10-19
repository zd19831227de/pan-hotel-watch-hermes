from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.domain.competitor_prices.schedule import watch_group_is_due


class CompetitorNeedsReviewScheduleTests(unittest.TestCase):
    def test_needs_review_does_not_advance_last_polled_at(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                types = importlib.import_module("app.domain.competitor_prices.types")
                db_session = importlib.import_module("app.db.session")
                models = importlib.import_module("app.domain.competitor_prices.models")
                from sqlalchemy import select

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

                async with db_session.session_scope() as session:
                    group = await session.scalar(
                        select(models.CtripCompetitorWatchGroup).where(
                            models.CtripCompetitorWatchGroup.group_key == "ops_watch"
                        )
                    )
                    assert group is not None
                    group.last_polled_at = datetime(2026, 6, 27, 5, 0, 0)
                    await session.flush()

                await repository.save_ctrip_competitor_price_result(
                    result_status="needs_review",
                    summary="need review",
                    platform_account_id="ctrip_public_account",
                    browser_session_id="browser_public",
                    actor_id="test",
                    watch_group_key="ops_watch",
                    data=_minimal_competitor_data("2026-06-27T06:03:00"),
                    evidence={"parser_version": "test"},
                )

                async with db_session.session_scope() as session:
                    group = await session.scalar(
                        select(models.CtripCompetitorWatchGroup).where(
                            models.CtripCompetitorWatchGroup.group_key == "ops_watch"
                        )
                    )
                    assert group is not None
                    self.assertEqual(group.last_polled_at, datetime(2026, 6, 27, 5, 0, 0))

            asyncio.run(scenario())

    def test_success_still_advances_last_polled_at(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                types = importlib.import_module("app.domain.competitor_prices.types")
                db_session = importlib.import_module("app.db.session")
                models = importlib.import_module("app.domain.competitor_prices.models")
                from sqlalchemy import select

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

                await repository.save_ctrip_competitor_price_result(
                    result_status="success",
                    summary="ok",
                    platform_account_id="ctrip_public_account",
                    browser_session_id="browser_public",
                    actor_id="test",
                    watch_group_key="ops_watch",
                    data=_minimal_competitor_data("2026-06-27T08:00:00"),
                    evidence={"parser_version": "test"},
                )

                async with db_session.session_scope() as session:
                    group = await session.scalar(
                        select(models.CtripCompetitorWatchGroup).where(
                            models.CtripCompetitorWatchGroup.group_key == "ops_watch"
                        )
                    )
                    assert group is not None
                    self.assertEqual(group.last_polled_at, datetime(2026, 6, 27, 8, 0, 0))

            asyncio.run(scenario())

    def test_needs_review_group_still_due_in_same_slot(self) -> None:
        group = SimpleNamespace(
            group_key="ops_watch",
            timezone="Asia/Shanghai",
            schedule_mode="fixed_slots",
            schedule_start_time="06:00",
            schedule_end_time="22:00",
            schedule_interval_minutes=60,
            query_interval_minutes=60,
            last_polled_at=datetime(2026, 6, 27, 5, 0, 0),
        )
        self.assertTrue(
            watch_group_is_due(group, datetime(2026, 6, 27, 6, 10, 0)),
        )

    def test_retry_cooldown_blocks_poll_within_interval(self) -> None:
        from app.domain.competitor_prices.repository import competitor_poll_retry_cooldown_summary

        summary = competitor_poll_retry_cooldown_summary(
            {"schedule_interval_minutes": 60},
            {
                "status": "needs_review",
                "captured_at": "2026-06-27T06:03:00",
            },
            now=datetime(2026, 6, 27, 6, 30, 0),
        )
        self.assertIsNotNone(summary)
        self.assertIn("重试", summary)

    def test_retry_cooldown_allows_poll_after_interval(self) -> None:
        from app.domain.competitor_prices.repository import competitor_poll_retry_cooldown_summary

        summary = competitor_poll_retry_cooldown_summary(
            {"schedule_interval_minutes": 60},
            {
                "status": "needs_review",
                "captured_at": "2026-06-27T06:03:00",
            },
            now=datetime(2026, 6, 27, 7, 5, 0),
        )
        self.assertIsNone(summary)

    def test_scheduler_skips_browser_read_within_retry_cooldown(self) -> None:
        from unittest.mock import patch

        from app.domain.competitor_prices import scheduler

        async def scenario() -> None:
            with patch(
                "app.domain.competitor_prices.scheduler.get_latest_competitor_price_run_meta",
                return_value={
                    "id": 99,
                    "status": "needs_review",
                    "captured_at": "2026-06-27T06:03:00",
                    "summary": "need review",
                },
            ), patch(
                "app.domain.competitor_prices.scheduler.competitor_poll_retry_cooldown_summary",
                return_value="上一轮仍为需要复核（06:03），约 07:03 后再自动重试。",
            ):
                result = await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    {
                        "group_key": "ops_watch",
                        "platform_account_id": "ctrip-public",
                        "browser_session_id": "browser-public",
                        "schedule_interval_minutes": 60,
                        "query_interval_minutes": 60,
                    },
                    push_to_feishu=True,
                    actor_id="scheduler",
                    source="scheduler",
                )
            self.assertEqual(result["status"], "skipped")
            self.assertIn("重试", result["summary"])

        asyncio.run(scenario())

    def test_scheduler_does_not_advance_slot_when_browser_binding_is_not_ready(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.domain.competitor_prices import scheduler
        from app.domain.competitor_prices.browser_binding import CompetitorBrowserBindingResolution

        async def scenario() -> None:
            mark_polled = AsyncMock()
            with patch(
                "app.domain.competitor_prices.scheduler.get_latest_competitor_price_run_meta",
                return_value=None,
            ), patch(
                "app.domain.competitor_prices.scheduler.resolve_competitor_browser_binding_for_group",
                new=AsyncMock(return_value=CompetitorBrowserBindingResolution(
                    status="config_required",
                    summary="浏览器账号不存在：browser-trip3",
                    evidence={"browser_session_id": "browser-trip3"},
                )),
            ), patch(
                "app.domain.competitor_prices.scheduler.mark_competitor_watch_group_polled",
                mark_polled,
                create=True,
            ):
                result = await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    {
                        "group_key": "ops_watch",
                        "platform_account_id": "trip3",
                        "browser_session_id": "browser-trip3",
                        "schedule_interval_minutes": 60,
                        "query_interval_minutes": 60,
                    },
                    push_to_feishu=True,
                    actor_id="scheduler",
                    source="scheduler",
                )

            self.assertEqual(result["status"], "config_required")
            mark_polled.assert_not_awaited()

        asyncio.run(scenario())


def _minimal_competitor_data(captured_at: str) -> dict:
    return {
        "captured_at": captured_at,
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
                "status": "needs_review",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                "current_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                "date_label": "today",
                "date_context": {
                    "check_in": "2026-06-27",
                    "check_out": "2026-06-28",
                },
                "room_rates": [],
            },
        ],
    }


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
    repository = importlib.import_module("app.domain.competitor_prices.repository")
    return importlib.reload(repository)


if __name__ == "__main__":
    unittest.main()
