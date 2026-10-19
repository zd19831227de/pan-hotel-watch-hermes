from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class CompetitorPriceDiscountPersistenceTests(unittest.TestCase):
    def test_discount_fields_are_persisted_and_returned_in_latest_snapshot(self) -> None:
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
                                        "price": {"amount": 416, "currency": "CNY"},
                                        "discount_amount_yuan": 32,
                                        "discount_display": "优惠32",
                                        "source_discount_line": "优惠32",
                                    },
                                ],
                            },
                        ],
                    },
                    evidence={"parser_version": "test"},
                )

                latest = await repository.get_latest_competitor_price_snapshot(
                    watch_group_key="ops_watch",
                )
                assert latest is not None
                rates = latest["hotels"][0]["snapshots"]["today"]["rates"]
                self.assertEqual(rates[0]["discount_amount_yuan"], 32)
                self.assertEqual(rates[0]["discount_display"], "优惠32")

                async with db_session.session_scope() as session:
                    saved_rate = await session.scalar(
                        select(models.CtripCompetitorRoomRate)
                    )
                assert saved_rate is not None
                self.assertEqual(saved_rate.discount_amount_yuan, 32)
                self.assertEqual(saved_rate.discount_display, "优惠32")
                self.assertEqual(saved_rate.source_discount_line, "优惠32")

            asyncio.run(scenario())


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
