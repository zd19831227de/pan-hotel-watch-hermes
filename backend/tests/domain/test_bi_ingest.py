"""bi_daily ingest 测试：存量可信数据 → 事实表；幂等；未绑店排除；禁推断列恒 NULL。"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime

from backend.tests._bi_isolation import tolerant_isolated_db

from app.domain.bi_daily import models as _bi_daily_models  # noqa: F401
from app.domain.calendar_price_watch import models as _cal_models  # noqa: F401
from app.domain.competitor_prices import models as _comp_models  # noqa: F401
from app.domain.ebk_period_reads import models as _period_models  # noqa: F401

NOW = datetime(2026, 10, 8, 9, 40, 0)


async def _seed(session) -> dict:
    """种子：绑店竞品组+日历价+房型价；未绑店组；EBK 按日实际。"""
    from app.domain.competitor_prices.models import (
        CtripCompetitorHotel,
        CtripCompetitorHotelSnapshot,
        CtripCompetitorPriceRun,
        CtripCompetitorRoomRate,
        CtripCompetitorWatchGroup,
    )
    from app.domain.calendar_price_watch.models import (
        CalendarPricePoint,
        CalendarPriceRun,
        CalendarPriceWatchConfig,
    )
    from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot

    bound_group = CtripCompetitorWatchGroup(
        group_key="g_bound", name="绑定组", platform_account_id="acc-bi"
    )
    unbound_group = CtripCompetitorWatchGroup(
        group_key="g_unbound", name="未绑组", platform_account_id="acc-x"
    )
    session.add_all([bound_group, unbound_group])
    await session.flush()

    own_hotel = CtripCompetitorHotel(
        group_id=bound_group.id,
        role="own",
        alias="本店",
        hotel_name="本店酒店",
        ctrip_hotel_id="100001",
    )
    comp_hotel = CtripCompetitorHotel(
        group_id=bound_group.id,
        role="competitor",
        alias="竞品A",
        hotel_name="竞品A酒店",
        ctrip_hotel_id="100002",
    )
    unbound_hotel = CtripCompetitorHotel(
        group_id=unbound_group.id,
        role="competitor",
        alias="游离竞品",
        hotel_name="游离竞品酒店",
        ctrip_hotel_id="100003",
    )
    session.add_all([own_hotel, comp_hotel, unbound_hotel])
    await session.flush()

    config = CalendarPriceWatchConfig(watch_group_id=bound_group.id)
    unbound_config = CalendarPriceWatchConfig(watch_group_id=unbound_group.id)
    session.add_all([config, unbound_config])
    await session.flush()

    cal_run = CalendarPriceRun(
        watch_group_id=bound_group.id,
        config_id=config.id,
        platform_account_id="acc-bi",
        actor_id="test",
        source="test",
        status="success",
        future_days=15,
        high_gap_threshold_yuan=10,
        low_gap_threshold_yuan=20,
        captured_at=NOW,
    )
    unbound_cal_run = CalendarPriceRun(
        watch_group_id=unbound_group.id,
        config_id=unbound_config.id,
        platform_account_id="acc-x",
        actor_id="test",
        source="test",
        status="success",
        future_days=15,
        high_gap_threshold_yuan=10,
        low_gap_threshold_yuan=20,
        captured_at=NOW,
    )
    session.add_all([cal_run, unbound_cal_run])
    await session.flush()

    points = [
        CalendarPricePoint(
            run_id=cal_run.id,
            watch_group_id=bound_group.id,
            hotel_config_id=own_hotel.id,
            role="own",
            hotel_alias="本店",
            checkin_date=date(2026, 10, 11),
            price_amount_yuan=300,
            source="test",
            is_trusted=True,
            status="success",
            captured_at=NOW,
        ),
        CalendarPricePoint(
            run_id=cal_run.id,
            watch_group_id=bound_group.id,
            hotel_config_id=comp_hotel.id,
            role="competitor",
            hotel_alias="竞品A",
            checkin_date=date(2026, 10, 11),
            price_amount_yuan=350,
            source="test",
            is_trusted=True,
            status="success",
            captured_at=NOW,
        ),
        CalendarPricePoint(
            run_id=cal_run.id,
            watch_group_id=bound_group.id,
            hotel_config_id=comp_hotel.id,
            role="competitor",
            hotel_alias="竞品A",
            checkin_date=date(2026, 10, 12),
            price_amount_yuan=999,  # 未信任：不得入事实
            source="test",
            is_trusted=False,
            status="needs_review",
            captured_at=NOW,
        ),
        CalendarPricePoint(
            run_id=unbound_cal_run.id,
            watch_group_id=unbound_group.id,
            hotel_config_id=unbound_hotel.id,
            role="competitor",
            hotel_alias="游离竞品",
            checkin_date=date(2026, 10, 11),
            price_amount_yuan=400,
            source="test",
            is_trusted=True,
            status="success",
            captured_at=NOW,
        ),
    ]
    session.add_all(points)

    rate_run = CtripCompetitorPriceRun(
        group_id=bound_group.id,
        platform_account_id="acc-bi",
        actor_id="test",
        source="test",
        status="success",
        target_count=1,
        captured_at=NOW,
    )
    session.add(rate_run)
    await session.flush()
    snapshot = CtripCompetitorHotelSnapshot(
        run_id=rate_run.id,
        group_id=bound_group.id,
        hotel_config_id=comp_hotel.id,
        role="competitor",
        date_label="今天",
        checkin_date=date(2026, 10, 9),
        snapshot_status="success",
        captured_at=NOW,
    )
    session.add(snapshot)
    await session.flush()
    session.add(
        CtripCompetitorRoomRate(
            snapshot_id=snapshot.id,
            run_id=rate_run.id,
            hotel_config_id=comp_hotel.id,
            room_type="高级大床房",
            breakfast_kind="no_breakfast",
            price_amount_yuan=280,
            availability_status="tight",
            source="room_rate_dom_display_price",
            is_primary=True,
            is_trusted=True,
        )
    )

    session.add_all(
        [
            EbkPeriodReadSnapshot(
                platform="ctrip",
                data_kind="business",
                period="day",
                platform_account_id="acc-bi",
                status="ok",
                caliber="经营概览·昨日数据",
                payload_json={
                    "sections": {
                        "昨日数据": {
                            "离店销售额": {"value": "23559.62"},
                            "离店间夜量": {"value": "88"},
                            "平均卖价": {"value": "267.72"},
                        }
                    }
                },
                source="day_end_snapshot",
                business_date=date(2026, 10, 7),
                captured_at=NOW,
            ),
            EbkPeriodReadSnapshot(
                platform="fliggy",
                data_kind="business",
                period="day",
                platform_account_id="acc-bi",
                status="ok",
                caliber="经营数据·昨日",
                payload_json={
                    "sections": {
                        "在店数据": {
                            "在店间夜": {"value": "9间夜"},
                            "房费收入": {"value": "2,450.2元"},
                            "间夜单价": {"value": "272.2元"},
                        }
                    }
                },
                source="day_end_snapshot",
                business_date=date(2026, 10, 7),
                captured_at=NOW,
            ),
            EbkPeriodReadSnapshot(
                platform="ctrip",
                data_kind="business",
                period="day",
                platform_account_id="acc-bi",
                status="ok",
                payload_json={"sections": {}},
                source="day_end_snapshot",
                business_date=None,  # 无业务日期：跳过
                captured_at=NOW,
            ),
            EbkPeriodReadSnapshot(
                platform="ctrip",
                data_kind="business",
                period="day",
                platform_account_id="acc-other",
                status="ok",
                payload_json={"sections": {"昨日数据": {"离店间夜量": {"value": "1"}}}},
                source="day_end_snapshot",
                business_date=date(2026, 10, 7),
                captured_at=NOW,
            ),
        ]
    )
    await session.flush()
    return {"bound_group_id": bound_group.id}


class BiIngestTest(unittest.TestCase):
    def test_competitor_market_ingest_trusted_only_idempotent_no_inference(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from sqlalchemy import select

                from app.domain.bi_daily import ingest

                async with db_session.SessionLocal() as session:
                    await _seed(session)
                    await session.commit()

                    written_1 = await ingest.ingest_calendar_price_points(session, now=NOW)
                    written_2 = await ingest.ingest_competitor_room_rates(session, now=NOW)
                    await session.commit()
                    self.assertEqual(written_1, 2, "只有绑店组的 2 条 trusted 日历价应入事实")
                    self.assertEqual(written_2, 1, "1 条 trusted 房型价应入事实")

                    from app.domain.bi_daily.models import CompetitorMarketFact

                    facts = (
                        (await session.execute(select(CompetitorMarketFact).order_by(CompetitorMarketFact.id)))
                        .scalars()
                        .all()
                    )
                    by_source = {}
                    for fact in facts:
                        by_source.setdefault(fact.source, []).append(fact)
                    cal = by_source["calendar_price_point"]
                    rate = by_source["competitor_room_rate"]

                    # 未信任行（999）与未绑店组（400）都不在。
                    prices = sorted(f.price for f in cal)
                    self.assertEqual(prices, [300, 350])
                    self.assertEqual(rate[0].price, 280)
                    self.assertEqual(rate[0].availability, "tight")
                    self.assertEqual(rate[0].room_type, "高级大床房")
                    self.assertEqual(rate[0].breakfast_kind, "no_breakfast")
                    self.assertEqual(rate[0].stay_date, date(2026, 10, 9))
                    for fact in facts:
                        self.assertEqual(fact.weekday, fact.stay_date.isoweekday())
                        self.assertTrue(fact.trusted)
                        # 禁止从价格推断的列必须恒 NULL。
                        self.assertIsNone(fact.occupancy_rate)
                        self.assertIsNone(fact.booking_pace)
                        self.assertIsNone(fact.rank)
                        self.assertTrue(fact.source_row_ref)

                    # 幂等：重跑不新增。
                    again_cal = await ingest.ingest_calendar_price_points(session, now=NOW)
                    again_rate = await ingest.ingest_competitor_room_rates(session, now=NOW)
                    await session.commit()
                    self.assertEqual((again_cal, again_rate), (0, 0))
                    total = len(
                        (await session.execute(select(CompetitorMarketFact.id))).scalars().all()
                    )
                    self.assertEqual(total, 3)

            asyncio.run(scenario())

    def test_booking_position_ingest_from_ebk_period_day(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from sqlalchemy import select

                from app.domain.bi_daily import ingest

                async with db_session.SessionLocal() as session:
                    await _seed(session)
                    await session.commit()

                    bindings = [
                        {"shop_key": "shop_bi", "platform": "ctrip", "platform_account_id": "acc-bi"},
                        {"shop_key": "shop_bi", "platform": "fliggy", "platform_account_id": "acc-bi"},
                    ]
                    written = await ingest.ingest_ebk_period_day_actuals(
                        session, shop_bindings=bindings, now=NOW
                    )
                    await session.commit()
                    self.assertEqual(written, 2, "仅 2 条带业务日期且账号匹配的快照应入事实")

                    from app.domain.bi_daily.models import BookingPositionFact

                    facts = (
                        (await session.execute(select(BookingPositionFact).order_by(BookingPositionFact.platform)))
                        .scalars()
                        .all()
                    )
                    by_platform = {fact.platform: fact for fact in facts}
                    ctrip = by_platform["ctrip"]
                    self.assertEqual(ctrip.stay_date, date(2026, 10, 7))
                    self.assertEqual(ctrip.weekday, 3)  # 周三
                    self.assertEqual(ctrip.booked_room_nights, 88)
                    self.assertAlmostEqual(ctrip.room_revenue, 23559.62)
                    self.assertAlmostEqual(ctrip.adr, 267.72)
                    self.assertEqual(ctrip.shop_key, "shop_bi")
                    self.assertEqual(ctrip.source, "ebk_period_day")
                    self.assertEqual(ctrip.as_of_time, NOW)
                    fliggy = by_platform["fliggy"]
                    self.assertEqual(fliggy.booked_room_nights, 9)
                    self.assertAlmostEqual(fliggy.room_revenue, 2450.2)
                    self.assertAlmostEqual(fliggy.adr, 272.2)
                    # 无库存源：出租率与库存列必须为空。
                    for fact in facts:
                        self.assertIsNone(fact.occupancy_rate)
                        self.assertIsNone(fact.available_inventory)
                        self.assertIsNone(fact.remaining_inventory)
                        self.assertIsNone(fact.booking_orders)

                    again = await ingest.ingest_ebk_period_day_actuals(
                        session, shop_bindings=bindings, now=NOW
                    )
                    await session.commit()
                    self.assertEqual(again, 0)

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
