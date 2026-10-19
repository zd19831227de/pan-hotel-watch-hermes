from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class CompetitorLatestSuccessFallbackTests(unittest.TestCase):
    def test_latest_falls_back_to_last_success_when_latest_is_needs_review(self) -> None:
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
                    data=_success_competitor_data("2026-06-27T05:00:00", price=420),
                    evidence={"parser_version": "test"},
                )
                await repository.save_ctrip_competitor_price_result(
                    result_status="needs_review",
                    summary="need review",
                    platform_account_id="ctrip_public_account",
                    browser_session_id="browser_public",
                    actor_id="test",
                    watch_group_key="ops_watch",
                    data=_success_competitor_data("2026-06-27T06:03:00", price=999, status="needs_review"),
                    evidence={"parser_version": "test"},
                )

                latest = await repository.get_latest_competitor_price_snapshot(
                    watch_group_key="ops_watch",
                )
                assert latest is not None
                self.assertEqual(latest["run"]["status"], "success")
                self.assertEqual(latest["run"]["captured_at"], "2026-06-27T05:00:00")
                self.assertIn("latest_poll_run", latest)
                self.assertEqual(latest["latest_poll_run"]["status"], "needs_review")
                self.assertIn("operator_note", latest)
                rates = latest["hotels"][0]["snapshots"]["today"]["rates"]
                self.assertEqual(rates[0]["price_amount_yuan"], 420)

            asyncio.run(scenario())


def _success_competitor_data(
    captured_at: str,
    *,
    price: int,
    status: str = "success",
) -> dict:
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
                "status": status,
                "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                "current_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                "date_label": "today",
                "date_context": {
                    "check_in": "2026-06-27",
                    "check_out": "2026-06-28",
                },
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "高级大床房",
                        "breakfast": "无早",
                        "price": {"amount": price, "currency": "CNY"},
                    },
                ],
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
