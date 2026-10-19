from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


class CompetitorPriceTrendTests(unittest.TestCase):
    def test_login_required_snapshot_does_not_overwrite_configured_hotel_name(self) -> None:
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
                created = await repository.upsert_competitor_hotel_config(
                    platform_account_id="ctrip_public_account",
                    watch_group_key="ops_watch",
                    hotel=types.CompetitorHotelConfigIn(
                        role="own",
                        alias="泉吉云州澜江路步行街高空江景酒店",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                        sort_order=0,
                    ),
                )
                await repository.update_competitor_hotel_config(
                    hotel_config_id=created["id"],
                    watch_group_key="ops_watch",
                    patch=types.CompetitorHotelConfigPatch(
                        hotel_name="泉吉云州澜江路步行街高空江景酒店",
                    ),
                )

                await repository.save_ctrip_competitor_price_result(
                    result_status="human_required",
                    summary="login required",
                    platform_account_id="ctrip_public_account",
                    browser_session_id="browser_public",
                    actor_id="test",
                    watch_group_key="ops_watch",
                    data={
                        "captured_at": "2026-06-18T08:00:00",
                        "target_aliases": {
                            "own": "泉吉云州澜江路步行街高空江景酒店",
                        },
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
                                "hotel_name": "登录首页",
                                "status": "human_required",
                                "page_state": "login_required",
                                "review_reason": "login_required",
                                "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                                "current_url": "https://passport.ctrip.com/user/login",
                                "date_context": {
                                    "check_in": "2026-06-18",
                                    "check_out": "2026-06-19",
                                },
                                "room_rates": [],
                            },
                        ],
                    },
                    evidence={"parser_version": "test"},
                )

                hotels = await repository.list_competitor_hotels(
                    watch_group_key="ops_watch",
                )
                self.assertEqual(
                    hotels[0]["hotel_name"],
                    "泉吉云州澜江路步行街高空江景酒店",
                )
                self.assertEqual(
                    hotels[0]["alias"],
                    "泉吉云州澜江路步行街高空江景酒店",
                )
                self.assertEqual(hotels[0]["last_status"], "human_required")

            asyncio.run(scenario())

    def test_trend_uses_only_first_trusted_dom_room_type(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                repository.local_now = lambda: datetime(2026, 6, 20, 12, 0)
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
                        alias="竞品A",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                        sort_order=10,
                    ),
                )

                await repository.save_ctrip_competitor_price_result(
                    result_status="success",
                    summary="read ok",
                    platform_account_id="ctrip_public_account",
                    browser_session_id="browser_public",
                    actor_id="test",
                    watch_group_key="ops_watch",
                    data={
                        "captured_at": "2026-06-17T08:00:00",
                        "targets": [
                            {
                                "role": "own",
                                "label": "本店",
                                "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                            },
                            {
                                "role": "competitor_1",
                                "label": "竞品A",
                                "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                            },
                        ],
                        "snapshots": [
                            {
                                "role": "own",
                                "hotel_name": "本店",
                                "status": "success",
                                "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                                "date_context": {
                                    "check_in": "2026-06-17",
                                    "check_out": "2026-06-18",
                                },
                                "room_rates": [
                                    {
                                        "source": "visible_text_price",
                                        "room_type": "高级大床房",
                                        "breakfast": "无早",
                                        "availability": "正常",
                                        "price": {"amount": 199, "currency": "CNY"},
                                    },
                                    {
                                        "source": "room_rate_dom_display_price",
                                        "room_type": "高级大床房",
                                        "breakfast": "无早",
                                        "availability": "正常",
                                        "price": {"amount": 416, "currency": "CNY"},
                                    },
                                    {
                                        "source": "room_rate_dom_display_price",
                                        "room_type": "高级大床房",
                                        "breakfast": "含早",
                                        "availability": "正常",
                                        "price": {"amount": 481, "currency": "CNY"},
                                    },
                                    {
                                        "source": "room_rate_dom_display_price",
                                        "room_type": "豪华大床房",
                                        "breakfast": "无早",
                                        "availability": "正常",
                                        "price": {"amount": 366, "currency": "CNY"},
                                    },
                                ],
                            },
                            {
                                "role": "competitor_1",
                                "hotel_name": "竞品A",
                                "status": "success",
                                "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                                "date_context": {
                                    "check_in": "2026-06-17",
                                    "check_out": "2026-06-18",
                                },
                                "room_rates": [
                                    {
                                        "source": "room_rate_dom_display_price",
                                        "room_type": "商务大床房",
                                        "breakfast": "无早",
                                        "availability": "仅剩4间",
                                        "price": {"amount": 415, "currency": "CNY"},
                                    },
                                ],
                            },
                        ],
                    },
                    evidence={"parser_version": "test"},
                )

                trend = await repository.get_competitor_price_trend(
                    watch_group_key="ops_watch",
                    days=30,
                )

                assert trend is not None
                self.assertEqual(trend["status"], "success")
                self.assertEqual(trend["trusted_source"], "room_rate_dom_display_price")
                self.assertEqual(trend["point_count"], 3)
                prices = sorted(point["min_price_amount_yuan"] for point in trend["points"])
                self.assertEqual(prices, [415, 416, 481])
                self.assertNotIn(199, prices)
                self.assertNotIn(366, prices)
                own_point = next(
                    point
                    for point in trend["points"]
                    if point["hotel_role"] == "own"
                    and point["min_price_amount_yuan"] == 416
                )
                self.assertEqual(own_point["hotel_alias"], "本店")
                self.assertEqual(own_point["date_label"], "today")
                self.assertEqual(own_point["breakfast_kind"], "none")
                self.assertEqual(own_point["room_type"], "高级大床房")
                own_room_types = sorted(
                    point["room_type"]
                    for point in trend["points"]
                    if point["hotel_role"] == "own"
                )
                self.assertEqual(own_room_types, ["高级大床房", "高级大床房"])
                own_series_room_types = sorted(
                    item["room_type"]
                    for item in trend["series"]
                    if item["hotel_role"] == "own"
                )
                self.assertEqual(own_series_room_types, ["高级大床房", "高级大床房"])

            asyncio.run(scenario())

    def test_trend_keeps_one_series_when_checkin_date_and_first_room_type_change(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_repository(db_url)
                repository.local_now = lambda: datetime(2026, 6, 20, 12, 0)
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

                for captured_at, checkin, room_type, price in [
                    ("2026-06-17T08:00:00", "2026-06-17", "高级大床房", 416),
                    ("2026-06-18T08:00:00", "2026-06-18", "商务大床房", 421),
                ]:
                    await repository.save_ctrip_competitor_price_result(
                        result_status="success",
                        summary="read ok",
                        platform_account_id="ctrip_public_account",
                        browser_session_id="browser_public",
                        actor_id="test",
                        watch_group_key="ops_watch",
                        data={
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
                                    "status": "success",
                                    "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                                    "date_context": {
                                        "check_in": checkin,
                                        "check_out": "2026-06-19",
                                    },
                                    "room_rates": [
                                        {
                                            "source": "room_rate_dom_display_price",
                                            "room_type": room_type,
                                            "breakfast": "无早",
                                            "availability": "正常",
                                            "price": {"amount": price, "currency": "CNY"},
                                        },
                                    ],
                                },
                            ],
                        },
                        evidence={"parser_version": "test"},
                    )

                trend = await repository.get_competitor_price_trend(
                    watch_group_key="ops_watch",
                    days=30,
                )

                assert trend is not None
                own_today_none = [
                    series
                    for series in trend["series"]
                    if series["hotel_role"] == "own"
                    and series["date_label"] == "today"
                    and series["breakfast_kind"] == "none"
                ]
                self.assertEqual(len(own_today_none), 1)
                self.assertEqual(
                    [point["price_amount_yuan"] for point in own_today_none[0]["points"]],
                    [416, 421],
                )
                self.assertEqual(
                    [point["room_type"] for point in own_today_none[0]["points"]],
                    ["高级大床房", "商务大床房"],
                )
                self.assertEqual(own_today_none[0]["room_types"], ["高级大床房", "商务大床房"])
                self.assertTrue(own_today_none[0]["room_type_changed"])
                self.assertEqual(own_today_none[0]["room_type"], "房型有变化")

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
        # are about to delete. Must run before _tmp.cleanup().
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
