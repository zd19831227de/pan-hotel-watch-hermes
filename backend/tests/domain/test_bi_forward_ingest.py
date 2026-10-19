# -*- coding: utf-8 -*-
"""bi_daily 前瞻 ingest 测试：日历在手/预埋价、市场需求事实、在店出租率、采集轮接线。"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime

from backend.tests._bi_isolation import tolerant_isolated_db

from app.domain.bi_daily import models as _bi_daily_models  # noqa: F401
from app.domain.ebk_period_reads import models as _period_models  # noqa: F401

NOW = datetime(2026, 10, 10, 9, 40, 0)


def _forward_payload() -> dict:
    return {
        "status": "ok",
        "dates": ["2026-10-10", "2026-10-11"],
        "room_groups": [
            {"room_group_name": "豪华套房", "total_rooms": 14},
            {"room_group_name": "行政套房", "total_rooms": 60},
        ],
        "rate_plans": [
            {
                "rate_plan_id": 1741110287,
                "rate_plan_name": "豪华套房<无早>",
                "cells": [
                    {"stay_date": "2026-10-10", "sale_price": 406.0, "floor_price": 365.4, "remaining": 9},
                    {"stay_date": "2026-10-11", "sale_price": 443.0, "floor_price": 398.7, "remaining": 4},
                ],
            },
            {
                # 钟点房按小时计价，不得混入预埋价最低值。
                "rate_plan_id": 1524632718,
                "rate_plan_name": "豪华套房<无早>(钟点房)<08:00到18:00>",
                "cells": [
                    {"stay_date": "2026-10-10", "sale_price": 118.0, "floor_price": 106.2, "remaining": 30},
                    {"stay_date": "2026-10-11", "sale_price": 118.0, "floor_price": 106.2, "remaining": 30},
                ],
            },
            {
                "rate_plan_id": 1741110290,
                "rate_plan_name": "豪华套房<双早>",
                "cells": [
                    {"stay_date": "2026-10-10", "sale_price": 454.0, "floor_price": 408.6, "remaining": 6},
                    {"stay_date": "2026-10-11", "sale_price": 491.0, "floor_price": 441.9, "remaining": 5},
                ],
            },
        ],
        "sold_by_room": {
            "467494979": {"2026-10-10": 16, "2026-10-11": 5},
            "467494990": {"2026-10-10": 16},
        },
        "inventory_rows": [
            {
                "rate_plan_id": 1741110287,
                "stay_date": "2026-10-10",
                "total_quantity": 25,
                "can_used_quantity": 9,
            }
        ],
        "rate_plan_registry": {
            "1741110287": {
                "basic_room_type_id": 467494979,
                "room_group_name": "豪华套房",
                "rate_plan_name": "豪华套房<无早>",
                "breakfast_hint": "none",
            },
            "1741110290": {
                "basic_room_type_id": 467494979,
                "room_group_name": "豪华套房",
                "rate_plan_name": "豪华套房<双早>",
                "breakfast_hint": "double",
            },
            "467494990-own": {
                "basic_room_type_id": 467494990,
                "room_group_name": "行政套房",
                "rate_plan_name": "行政套房<无早>",
                "breakfast_hint": "none",
            },
        },
    }


def _demand_payload() -> dict:
    return {
        "status": "ok",
        "market_heats": {
            "city": [{"stay_date": "2026-10-10", "heat": 3.28, "heat_label": "Normal"}],
            "city_last_year": [],
            "zone": [{"stay_date": "2026-10-10", "heat": 3.5, "heat_label": "Normal"}],
            "zone_last_year": [],
        },
        "travel_heats": [
            {"stay_date": "2026-10-10", "heat": 2.03, "heat_last_year": 2.34}
        ],
        "competitor_occupied": {
            "rows": [
                {
                    "stay_date": "2026-10-10",
                    "my_booked_rooms": 58.0,
                    "my_occupancy_rate": 0.3314,
                    "competitor_booked_rooms": 35.0,
                    "competitor_occupancy_rate": 0.2256,
                }
            ]
        },
        "price_change_trend": [
            {"stay_date": "2026-10-10", "trend": -0.09, "trend_last_year": 0}
        ],
        "events": [
            {"name": "安又琪演唱会", "start_date": "2026-10-10", "end_date": "2026-10-11"}
        ],
    }


class ForwardRowBuildersTest(unittest.TestCase):
    def test_forward_group_rows_sold_total_and_prices(self):
        from app.domain.bi_daily.ingest import _forward_group_rows

        rows = _forward_group_rows(_forward_payload(), NOW, account_id="acc-bi")
        by_key = {(row["room_type"], row["stay_date"].isoformat()): row for row in rows}
        deluxe_1010 = by_key[("豪华套房", "2026-10-10")]
        self.assertEqual(deluxe_1010["booked_room_nights"], 16)
        self.assertEqual(deluxe_1010["available_inventory"], 14)
        notes = deluxe_1010["notes_json"]
        self.assertEqual(notes["pre_set_price_min"], 406.0)
        self.assertEqual(notes["pre_set_price_min_no_breakfast"], 406.0)
        self.assertAlmostEqual(notes["floor_price_min"], 365.4)
        executive = by_key[("行政套房", "2026-10-10")]
        # 无售卖计划注册的组只有已售/总房量，价格为空，不猜。
        self.assertEqual(executive["booked_room_nights"], 16)
        self.assertIsNone(executive["notes_json"]["pre_set_price_min"])
        # 酒店级总房量行：各组共N间求和（14+60），booked 不在此行猜。
        # ref 必须带账号标识：跨店同分钟采集不互相判重（2026-10-10 生产实证）。
        hotel_1010 = by_key[("", "2026-10-10")]
        self.assertEqual(hotel_1010["available_inventory"], 74)
        self.assertIsNone(hotel_1010["booked_room_nights"])
        self.assertTrue(
            hotel_1010["source_row_ref"].startswith(
                "ctrip_inventory_calendar:acc-bi:hotel:"
            )
        )
        self.assertIn(("", "2026-10-11"), by_key)

    def test_demand_fact_rows_fields(self):
        from app.domain.bi_daily.ingest import _demand_fact_rows

        rows = _demand_fact_rows(_demand_payload(), NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stay_date"], date(2026, 10, 10))
        self.assertEqual(row["city_heat"], 3.28)
        self.assertEqual(row["zone_heat"], 3.5)
        self.assertEqual(row["travel_heat_last_year"], 2.34)
        self.assertEqual(row["my_booked_rooms"], 58)
        self.assertAlmostEqual(row["competitor_occupancy_rate"], 0.2256)
        self.assertEqual(row["competitor_price_trend"], -0.09)
        self.assertEqual(row["events_json"], ["安又琪演唱会"])

    def test_occupancy_extraction_from_staying_section(self):
        from app.domain.bi_daily.ingest import extract_day_business_metrics

        payload = {
            "sections": {
                "离店": {"离店间夜量": {"value": "36"}, "离店销售额": {"value": "9479"}, "平均卖价": {"value": "263"}},
                "在店": {"间夜量": {"value": "95"}, "出租率": {"value": "54.29%"}, "紧张度": {"value": "85.71%"}},
            }
        }
        metrics = extract_day_business_metrics(payload, "ctrip")
        self.assertEqual(metrics["booked_room_nights"], 36.0)
        self.assertAlmostEqual(metrics["occupancy_rate"], 0.5429)
        # 紧张度/在店间夜不得污染离店口径。
        self.assertEqual(metrics["adr"], 263.0)
        self.assertEqual(metrics["room_revenue"], 9479.0)

    def test_occupancy_plain_percent_value(self):
        from app.domain.bi_daily.ingest import extract_day_business_metrics

        metrics = extract_day_business_metrics(
            {"sections": {"在店": {"出租率": "16%"}}}, "ctrip"
        )
        self.assertAlmostEqual(metrics["occupancy_rate"], 0.16)

    def test_occupancy_not_inferred_without_platform_metric(self):
        from app.domain.bi_daily.ingest import extract_day_business_metrics

        metrics = extract_day_business_metrics(
            {"sections": {"在店数据": {"在店间夜": {"value": "95"}}}}, "fliggy"
        )
        self.assertIsNone(metrics["occupancy_rate"])


class ForwardIngestDbTest(unittest.TestCase):
    def test_forward_and_demand_ingest_idempotent(self):
        with tolerant_isolated_db():
            from sqlalchemy import select

            from app.db import session as db_session
            from app.domain.bi_daily import ingest
            from app.domain.bi_daily.models import (
                BookingPositionFact,
                MarketDemandFact,
            )
            from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot

            async def scenario():
                await db_session.ensure_db_ready()
                async with db_session.SessionLocal() as session:
                    session.add_all(
                        [
                            EbkPeriodReadSnapshot(
                                platform="ctrip",
                                data_kind="bi_forward",
                                period="day",
                                platform_account_id="acc-bi",
                                status="ok",
                                payload_json=_forward_payload(),
                                source="bi_forward",
                                captured_at=NOW,
                            ),
                            EbkPeriodReadSnapshot(
                                platform="ctrip",
                                data_kind="bi_demand",
                                period="day",
                                platform_account_id="acc-bi",
                                status="ok",
                                payload_json=_demand_payload(),
                                source="bi_demand",
                                captured_at=NOW,
                            ),
                        ]
                    )
                    await session.commit()
                    bindings = [
                        {
                            "shop_key": "shop_bi",
                            "platform": "ctrip",
                            "platform_account_id": "acc-bi",
                        }
                    ]
                    forward_written = await ingest.ingest_ctrip_forward_position(
                        session, shop_bindings=bindings, now=NOW
                    )
                    demand_written = await ingest.ingest_market_demand(
                        session, shop_bindings=bindings, now=NOW
                    )
                    await session.commit()
                    self.assertEqual(forward_written, 5)  # 2 豪华 + 1 行政 + 2 酒店总房量行
                    self.assertEqual(demand_written, 1)

                    facts = (
                        (
                            await session.execute(
                                select(BookingPositionFact).where(
                                    BookingPositionFact.source == "ctrip_inventory_calendar"
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    self.assertTrue(all(fact.stay_date >= NOW.date() for fact in facts))
                    self.assertTrue(all(fact.available_inventory is not None for fact in facts))
                    demand_facts = (
                        await session.execute(select(MarketDemandFact))
                    ).scalars().all()
                    self.assertAlmostEqual(demand_facts[0].my_occupancy_rate, 0.3314)

                    # 幂等：重跑不新增。
                    again_forward = await ingest.ingest_ctrip_forward_position(
                        session, shop_bindings=bindings, now=NOW
                    )
                    again_demand = await ingest.ingest_market_demand(
                        session, shop_bindings=bindings, now=NOW
                    )
                    await session.commit()
                    self.assertEqual((again_forward, again_demand), (0, 0))

            asyncio.run(scenario())

    def test_occupancy_fact_written_from_custom_range_snapshot(self):
        with tolerant_isolated_db():
            from sqlalchemy import select

            from app.db import session as db_session
            from app.domain.bi_daily import ingest
            from app.domain.bi_daily.models import BookingPositionFact
            from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot

            async def scenario():
                await db_session.ensure_db_ready()
                async with db_session.SessionLocal() as session:
                    session.add(
                        EbkPeriodReadSnapshot(
                            platform="ctrip",
                            data_kind="business",
                            period="day",
                            platform_account_id="acc-bi",
                            status="ok",
                            payload_json={
                                "sections": {
                                    "离店": {
                                        "离店间夜量": {"value": "36"},
                                        "离店销售额": {"value": "9479"},
                                        "平均卖价": {"value": "263.31"},
                                    },
                                    "在店": {"出租率": {"value": "54.29%"}},
                                }
                            },
                            source="bi_daily_yesterday",
                            business_date=date(2026, 10, 9),
                            captured_at=NOW,
                        )
                    )
                    await session.commit()
                    written = await ingest.ingest_ebk_period_day_actuals(
                        session,
                        shop_bindings=[
                            {
                                "shop_key": "shop_bi",
                                "platform": "ctrip",
                                "platform_account_id": "acc-bi",
                            }
                        ],
                        now=NOW,
                    )
                    await session.commit()
                    self.assertEqual(written, 1)
                    fact = (
                        await session.execute(select(BookingPositionFact))
                    ).scalars().one()
                    self.assertAlmostEqual(fact.occupancy_rate, 0.5429)
                    self.assertEqual(fact.notes_json.get("occupancy_caliber"), "ctrip_staying")

            asyncio.run(scenario())

    def test_collection_round_with_live_read_stub(self):
        with tolerant_isolated_db():
            from app.db import session as db_session
            from app.domain.bi_daily import ingest, service
            from app.domain.bi_daily.models import MarketDemandFact
            from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot

            async def scenario():
                await db_session.ensure_db_ready()
                async with db_session.SessionLocal() as session:
                    # live_read_fn 模拟浏览器读成功：落两份快照。
                    async def fake_live_read(*, binding, now):
                        async with db_session.SessionLocal() as inner:
                            inner.add_all(
                                [
                                    EbkPeriodReadSnapshot(
                                        platform="ctrip",
                                        data_kind="bi_forward",
                                        period="day",
                                        platform_account_id=binding["platform_account_id"],
                                        status="ok",
                                        payload_json=_forward_payload(),
                                        source="bi_forward",
                                        captured_at=now,
                                    ),
                                    EbkPeriodReadSnapshot(
                                        platform="ctrip",
                                        data_kind="bi_demand",
                                        period="day",
                                        platform_account_id=binding["platform_account_id"],
                                        status="ok",
                                        payload_json=_demand_payload(),
                                        source="bi_demand",
                                        captured_at=now,
                                    ),
                                ]
                            )
                            await inner.commit()
                        return {"status": "ok", "snapshots": 2}

                    bindings = [
                        {
                            "shop_key": "shop_bi",
                            "platform": "ctrip",
                            "platform_account_id": "acc-bi",
                            "browser_session_id": "sess-1",
                        }
                    ]
                    result = await service.run_bi_daily_collection_round(
                        session=session,
                        now=NOW,
                        shop_bindings=bindings,
                        source="test",
                        live_read_fn=fake_live_read,
                    )
                    await session.commit()
                    self.assertEqual(result["status"], "success")
                    self.assertEqual(result["forward_facts_written"], 5)
                    self.assertEqual(result["demand_facts_written"], 1)
                    self.assertEqual(result["live_read_issues"], [])
                    demand_count = len(
                        (await session.execute(select(MarketDemandFact))).scalars().all()
                    )
                    self.assertEqual(demand_count, 1)

            from sqlalchemy import select

            asyncio.run(scenario())

    def test_collection_round_live_failure_does_not_block(self):
        with tolerant_isolated_db():
            from app.db import session as db_session
            from app.domain.bi_daily import service

            async def scenario():
                await db_session.ensure_db_ready()
                async with db_session.SessionLocal() as session:
                    async def failing_live_read(*, binding, now):
                        return {"status": "human_required", "reason": "需要人工登录"}

                    result = await service.run_bi_daily_collection_round(
                        session=session,
                        now=NOW,
                        shop_bindings=[
                            {
                                "shop_key": "shop_bi",
                                "platform": "ctrip",
                                "platform_account_id": "acc-bi",
                            }
                        ],
                        source="test",
                        live_read_fn=failing_live_read,
                    )
                    await session.commit()
                    self.assertEqual(result["status"], "success")
                    self.assertEqual(len(result["live_read_issues"]), 1)
                    self.assertEqual(result["live_read_issues"][0]["status"], "human_required")

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
