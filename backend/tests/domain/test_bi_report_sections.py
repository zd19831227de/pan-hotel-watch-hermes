# -*- coding: utf-8 -*-
"""bi_daily 日报四板块测试：前瞻在手/竞争圈判定/去年同期/同比系数/星期视图。"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime

from backend.tests._bi_isolation import tolerant_isolated_db

from app.domain.bi_daily import models as _bi_daily_models  # noqa: F401

NOW = datetime(2026, 10, 10, 12, 0, 0)


async def _seed_sections(session) -> None:
    from app.domain.bi_daily.models import BookingPositionFact, MarketDemandFact
    from app.domain.shops.models import Shop

    session.add(Shop(shop_key="shop_bi", name="BI四板块店"))
    session.add_all(
        [
            # 前瞻：酒店级总房量行 + 组级预埋价行。
            BookingPositionFact(
                shop_key="shop_bi",
                platform="ctrip",
                stay_date=date(2026, 10, 10),
                weekday=6,
                as_of_time=NOW,
                room_type="",
                booked_room_nights=None,
                available_inventory=74,
                source="ctrip_inventory_calendar",
                source_row_ref="f0",
                notes_json={"caliber": "hotel_total_rooms"},
            ),
            BookingPositionFact(
                shop_key="shop_bi",
                platform="ctrip",
                stay_date=date(2026, 10, 10),
                weekday=6,
                as_of_time=NOW,
                room_type="豪华套房",
                booked_room_nights=16,
                available_inventory=14,
                source="ctrip_inventory_calendar",
                source_row_ref="f1",
                notes_json={
                    "pre_set_price_min": 406.0,
                    "pre_set_price_min_no_breakfast": 406.0,
                },
            ),
            BookingPositionFact(
                shop_key="shop_bi",
                platform="ctrip",
                stay_date=date(2026, 10, 10),
                weekday=6,
                as_of_time=NOW,
                room_type="行政套房",
                booked_room_nights=58,
                available_inventory=60,
                source="ctrip_inventory_calendar",
                source_row_ref="f2",
                notes_json={
                    "pre_set_price_min": 424.0,
                    "pre_set_price_min_no_breakfast": 424.0,
                },
            ),
            # 市场/竞争圈（携程市场分析口径）。
            MarketDemandFact(
                shop_key="shop_bi",
                stay_date=date(2026, 10, 10),
                weekday=6,
                city_heat=3.28,
                city_heat_label="Normal",
                zone_heat=3.5,
                zone_heat_label="Normal",
                travel_heat=2.03,
                travel_heat_last_year=2.34,
                my_booked_rooms=58,
                my_occupancy_rate=0.3314,
                competitor_booked_rooms=35,
                competitor_occupancy_rate=0.2256,
                competitor_price_trend=-0.09,
                events_json=["安又琪演唱会"],
                captured_at=NOW,
                source="bi_demand",
                source_row_ref="d1",
            ),
            # 历史：昨日（2026-10-09，周五）携程实际 + 去年同星期（2025-10-10）。
            BookingPositionFact(
                shop_key="shop_bi",
                platform="ctrip",
                stay_date=date(2026, 10, 9),
                weekday=5,
                as_of_time=datetime(2026, 10, 10, 9, 40),
                booked_room_nights=36,
                room_revenue=9479.0,
                adr=263.31,
                occupancy_rate=0.5429,
                source="ebk_period_day",
                source_row_ref="h1",
            ),
            BookingPositionFact(
                shop_key="shop_bi",
                platform="ctrip",
                stay_date=date(2025, 10, 10),
                weekday=5,
                as_of_time=datetime(2025, 10, 11, 9, 40),
                booked_room_nights=32,
                room_revenue=9458.0,
                adr=295.0,
                occupancy_rate=0.16,
                source="ebk_period_day",
                source_row_ref="h2",
            ),
            # 2026-10-10（周六）的同星期映射日（D-364 → 2025-10-11）。
            BookingPositionFact(
                shop_key="shop_bi",
                platform="ctrip",
                stay_date=date(2025, 10, 11),
                weekday=6,
                as_of_time=datetime(2025, 10, 12, 9, 40),
                booked_room_nights=40,
                adr=280.0,
                occupancy_rate=0.5,
                source="ebk_period_day",
                source_row_ref="h3",
            ),
        ]
    )
    await session.commit()


class DailyReportFourSectionsTest(unittest.TestCase):
    def test_forward_own_circle_market_and_prior_year_sections(self):
        with tolerant_isolated_db():
            from app.db import session as db_session
            from app.domain.bi_daily import service

            async def scenario():
                await db_session.ensure_db_ready()
                async with db_session.SessionLocal() as session:
                    await _seed_sections(session)
                    report = await service.build_daily_report(
                        session=session, shop_key="shop_bi", as_of=NOW
                    )

                    forward_today = report["forward"][0]
                    self.assertEqual(forward_today["stay_date"], "2026-10-10")
                    own = forward_today["own"]
                    # 在手主源=市场分析全店口径；总房量=日历页各组求和（酒店行）。
                    self.assertEqual(own["booked_room_nights"]["value"], 58)
                    self.assertEqual(own["booked_room_nights"]["source"], "ctrip_market_analysis")
                    self.assertEqual(own["available_inventory"]["value"], 74)
                    self.assertEqual(own["remaining_inventory"]["value"], 16)
                    self.assertAlmostEqual(own["occupancy_rate"]["value"], 0.3314)
                    self.assertEqual(own["pre_set_price"]["value"], 406.0)

                    circle = forward_today["circle"]
                    self.assertEqual(circle["status"], "available")
                    self.assertEqual(circle["occupancy_verdict"], "win")
                    self.assertIn("领先", circle["verdict_label"])
                    self.assertAlmostEqual(circle["competitor_occupancy_rate"], 0.2256)
                    self.assertEqual(circle["competitor_price_trend"], -0.09)

                    market = forward_today["market"]
                    self.assertEqual(market["status"], "available")
                    self.assertEqual(market["city_heat"]["value"], 3.28)
                    self.assertEqual(market["travel_heat_last_year"]["value"], 2.34)
                    self.assertEqual(market["events"], ["安又琪演唱会"])

                    # 前瞻行的去年同期参照（板块2：预埋定价参考去年同星期）。
                    prior_ref = forward_today["prior_year"]
                    self.assertEqual(prior_ref["mapped_date"], "2025-10-11")
                    self.assertEqual(prior_ref["actual"]["booked_room_nights"], 40)
                    self.assertAlmostEqual(prior_ref["actual"]["occupancy_rate"], 0.5)
                    self.assertAlmostEqual(prior_ref["actual"]["adr"], 280.0)

                    # 无数据日：own 保持显式 unavailable、circle/market empty。
                    empty_day = report["forward"][5]
                    self.assertEqual(empty_day["own"]["booked_room_nights"]["status"], "unavailable")
                    self.assertEqual(empty_day["circle"]["status"], "empty")
                    self.assertEqual(empty_day["market"]["status"], "empty")

                    # 历史日：去年同期出租率/过夜价 + 同比完成系数。
                    performance = report["performance"][0]
                    self.assertEqual(performance["stay_date"], "2026-10-09")
                    prior = performance["prior_year"]
                    self.assertEqual(prior["mapped_date"], "2025-10-10")
                    self.assertEqual(prior["actual"]["booked_room_nights"], 32)
                    self.assertAlmostEqual(prior["actual"]["occupancy_rate"], 0.16)
                    self.assertAlmostEqual(prior["actual"]["adr"], 295.0)
                    self.assertAlmostEqual(prior["completion"]["value"], 36 / 32)
                    self.assertAlmostEqual(prior["adr_ratio"]["value"], 263.31 / 295.0)
                    self.assertAlmostEqual(prior["occupancy_delta_pp"], 38.29)
                    self.assertAlmostEqual(prior["revenue_completion"]["value"], 9479.0 / 9458.0)

                    # 星期视图（周五一行）。
                    friday = next(
                        row
                        for row in report["weekday_profile"]
                        if row["weekday"] == 5
                    )
                    self.assertEqual(friday["weekday_label"], "周五")
                    self.assertEqual(friday["current"]["nights_sum"], 36)
                    self.assertAlmostEqual(friday["current"]["occupancy_avg"], 0.5429)
                    self.assertAlmostEqual(friday["prior_year"]["nights_sum"], 32)
                    self.assertAlmostEqual(friday["prior_year"]["adr_avg"], 295.0)
                    self.assertAlmostEqual(friday["completion"]["value"], 36 / 32)
                    self.assertAlmostEqual(friday["occupancy_delta_pp"], 38.29)

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
