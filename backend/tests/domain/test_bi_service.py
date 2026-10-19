"""bi_daily 服务层测试：采集轮+覆盖率、预算导入（版本/审计）、报表装配。"""

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

_BINDINGS = [
    {"shop_key": "shop_bi", "platform": "ctrip", "platform_account_id": "acc-bi"},
    {"shop_key": "shop_bi", "platform": "fliggy", "platform_account_id": "acc-bi"},
]

_CSV = (
    "入住日期,房型,预算间夜,预算收入,预测间夜\n"
    "2026-10-07,,50,12000,48\n"
    "2026-10-10,高级大床房,60,15000,55\n"
).encode("utf-8-sig")


class BiServiceCollectionTest(unittest.TestCase):
    def test_collection_round_writes_facts_and_coverage_and_is_idempotent(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from sqlalchemy import select

                from backend.tests.domain.test_bi_ingest import _seed
                from app.domain.bi_daily import service
                from app.domain.bi_daily.models import (
                    BiCollectionRun,
                    BookingPositionFact,
                    CompetitorMarketFact,
                )

                async with db_session.SessionLocal() as session:
                    await _seed(session)
                    await session.commit()

                    summary = await service.run_bi_daily_collection_round(
                        session=session, shop_bindings=_BINDINGS, now=NOW
                    )
                    await session.commit()
                    self.assertEqual(summary["booking_facts_written"], 2)
                    self.assertEqual(summary["competitor_facts_written"], 3)
                    self.assertEqual(summary["status"], "success")

                    runs = (
                        (await session.execute(select(BiCollectionRun))).scalars().all()
                    )
                    self.assertEqual(len(runs), 1)
                    coverage = runs[0].coverage_json
                    self.assertIn("shop_bi", coverage)
                    dims = coverage["shop_bi"]["dimensions"]
                    self.assertEqual(dims["own_on_the_books"]["status"], "no_source")
                    self.assertEqual(dims["competitor_prices"]["status"], "fresh")
                    self.assertIn("budget_forecast", dims)

                    # 同日重跑：更新同一 run 行，不新增；事实幂等。
                    summary2 = await service.run_bi_daily_collection_round(
                        session=session, shop_bindings=_BINDINGS, now=NOW
                    )
                    await session.commit()
                    self.assertEqual(summary2["competitor_facts_written"], 0)
                    self.assertEqual(summary2["booking_facts_written"], 0)
                    runs = (
                        (await session.execute(select(BiCollectionRun))).scalars().all()
                    )
                    self.assertEqual(len(runs), 1)
                    total_facts = len(
                        (await session.execute(select(CompetitorMarketFact.id))).scalars().all()
                    ) + len(
                        (await session.execute(select(BookingPositionFact.id))).scalars().all()
                    )
                    self.assertEqual(total_facts, 5)

            asyncio.run(scenario())

    def test_collection_round_marks_unbound_competitor_groups(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from sqlalchemy import select

                from app.domain.bi_daily import service
                from app.domain.competitor_prices.models import CtripCompetitorWatchGroup
                from app.domain.shops.models import Shop

                async with db_session.SessionLocal() as session:
                    session.add(Shop(shop_key="shop_bi", name="BI测试店"))
                    session.add(
                        CtripCompetitorWatchGroup(
                            group_key="g_free", name="未绑店监控组", platform_account_id="acc-free"
                        )
                    )
                    await session.commit()
                    await service.run_bi_daily_collection_round(
                        session=session, shop_bindings=_BINDINGS, now=NOW
                    )
                    await session.commit()
                    from app.domain.bi_daily.models import BiCollectionRun

                    run = (
                        (await session.execute(select(BiCollectionRun))).scalars().first()
                    )
                    dims = run.coverage_json["shop_bi"]["dimensions"]
                    # 本店没绑组 → no_group；全局未绑组名单作为提示字段保留。
                    self.assertEqual(dims["competitor_prices"]["status"], "no_group")
                    self.assertTrue(dims["competitor_prices"]["unbound_groups"])

            asyncio.run(scenario())


class BiServiceReviewFixTest(unittest.TestCase):
    """第一轮审查修复的回归用例：P1 缺数不为 0、P2 去重/导入/推送隔离。"""

    def test_prior_year_facts_without_nights_not_fabricated_as_zero(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from app.domain.bi_daily import service
                from app.domain.bi_daily.models import BookingPositionFact
                from app.domain.shops.models import Shop

                async with db_session.SessionLocal() as session:
                    session.add(Shop(shop_key="shop_bi", name="BI测试店"))
                    session.add_all(
                        [
                            BookingPositionFact(
                                shop_key="shop_bi",
                                platform="ctrip",
                                stay_date=date(2026, 10, 7),
                                weekday=3,
                                as_of_time=datetime(2026, 10, 8, 9, 30),
                                booked_room_nights=88,
                                source="ebk_period_day",
                                source_row_ref="f1",
                            ),
                            # 去年同星期有事实行，但间夜提取失败（None）。
                            BookingPositionFact(
                                shop_key="shop_bi",
                                platform="ctrip",
                                stay_date=date(2025, 10, 8),
                                weekday=3,
                                as_of_time=datetime(2025, 10, 9, 9, 30),
                                booked_room_nights=None,
                                source="ebk_period_day",
                                source_row_ref="f2",
                            ),
                        ]
                    )
                    await session.commit()
                    report = await service.build_daily_report(
                        session=session, shop_key="shop_bi", as_of=NOW
                    )
                    prior = report["performance"][0]["prior_year"]
                    self.assertIsNone(prior["actual"], "缺提取的去年间夜不得伪造成 0")
                    self.assertEqual(prior["completion"]["status"], "unavailable")

            asyncio.run(scenario())

    def test_prior_year_real_zero_reports_zero_baseline_reason(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from app.domain.bi_daily import service
                from app.domain.bi_daily.models import BookingPositionFact
                from app.domain.shops.models import Shop

                async with db_session.SessionLocal() as session:
                    session.add(Shop(shop_key="shop_bi", name="BI测试店"))
                    session.add_all(
                        [
                            BookingPositionFact(
                                shop_key="shop_bi",
                                platform="ctrip",
                                stay_date=date(2026, 10, 7),
                                weekday=3,
                                as_of_time=datetime(2026, 10, 8, 9, 30),
                                booked_room_nights=88,
                                source="ebk_period_day",
                                source_row_ref="z1",
                            ),
                            BookingPositionFact(
                                shop_key="shop_bi",
                                platform="ctrip",
                                stay_date=date(2025, 10, 8),
                                weekday=3,
                                as_of_time=datetime(2025, 10, 9, 9, 30),
                                booked_room_nights=0,
                                source="ebk_period_day",
                                source_row_ref="z2",
                            ),
                        ]
                    )
                    await session.commit()
                    report = await service.build_daily_report(
                        session=session, shop_key="shop_bi", as_of=NOW
                    )
                    prior = report["performance"][0]["prior_year"]
                    self.assertEqual(prior["actual"], {"booked_room_nights": 0})
                    self.assertEqual(prior["completion"]["reason"], "prior_year_baseline_zero")

            asyncio.run(scenario())

    def test_competitor_forward_uses_latest_snapshot_per_hotel(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from app.domain.bi_daily import service
                from app.domain.bi_daily.models import CompetitorMarketFact
                from app.domain.shops.models import Shop

                async with db_session.SessionLocal() as session:
                    session.add(Shop(shop_key="shop_bi", name="BI测试店"))
                    session.add_all(
                        [
                            CompetitorMarketFact(
                                shop_key="shop_bi",
                                competitor_hotel_id=7,
                                stay_date=date(2026, 10, 11),
                                weekday=7,
                                price=350,
                                availability="normal",
                                captured_at=datetime(2026, 10, 8, 8, 0),
                                source="calendar_price_point",
                                trusted=True,
                                source_row_ref="c1",
                            ),
                            CompetitorMarketFact(
                                shop_key="shop_bi",
                                competitor_hotel_id=7,
                                stay_date=date(2026, 10, 11),
                                weekday=7,
                                price=320,
                                availability="normal",
                                captured_at=datetime(2026, 10, 8, 9, 30),
                                source="calendar_price_point",
                                trusted=True,
                                source_row_ref="c2",
                            ),
                            # 同一酒店同一来源的不同入住日：每个日期都必须保留。
                            CompetitorMarketFact(
                                shop_key="shop_bi",
                                competitor_hotel_id=7,
                                stay_date=date(2026, 10, 12),
                                weekday=1,
                                price=310,
                                availability="normal",
                                captured_at=datetime(2026, 10, 8, 9, 30),
                                source="calendar_price_point",
                                trusted=True,
                                source_row_ref="c3",
                            ),
                            CompetitorMarketFact(
                                shop_key="shop_bi",
                                competitor_hotel_id=8,
                                stay_date=date(2026, 10, 12),
                                weekday=1,
                                price=280,
                                availability="normal",
                                captured_at=datetime(2026, 10, 8, 9, 30),
                                source="calendar_price_point",
                                trusted=True,
                                source_row_ref="c4",
                            ),
                        ]
                    )
                    await session.commit()
                    report = await service.build_daily_report(
                        session=session, shop_key="shop_bi", as_of=NOW
                    )
                    by_date = {row["stay_date"]: row for row in report["forward"]}
                    comp = by_date["2026-10-11"]["competitor"]
                    # 只用最新快照：价格带来自 320，而不是旧的 350。
                    self.assertEqual(comp["prices"]["min"], 320)
                    self.assertEqual(comp["prices"]["max"], 320)
                    self.assertEqual(comp["hotels"][0]["price"], 320)
                    self.assertEqual(comp["availability"].get("normal"), 1)
                    # 去重键必须含 stay_date：其他入住日的酒店不能被吃掉。
                    comp12 = by_date["2026-10-12"]["competitor"]
                    self.assertEqual(comp12["prices"]["min"], 280)
                    self.assertEqual(comp12["prices"]["max"], 310)
                    self.assertEqual(len(comp12["hotels"]), 2)

            asyncio.run(scenario())

    def test_import_with_duplicate_rows_succeeds_last_wins(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from sqlalchemy import func, select

                from app.domain.bi_daily import service
                from app.domain.bi_daily.models import BudgetForecastFact
                from app.domain.shops.models import Shop as ShopModel

                async with db_session.SessionLocal() as session:
                    session.add(ShopModel(shop_key="shop_bi", name="BI测试店"))
                    await session.commit()
                    payload = (
                        "入住日期,房型,预算间夜\n"
                        "2026-10-07,,50\n"
                        "2026-10-07,,70\n"
                    ).encode("utf-8-sig")
                    result = await service.import_budget_forecast(
                        session,
                        shop_key="shop_bi",
                        payload=payload,
                        filename="dup.csv",
                        forecast_version="v1",
                        valid_from=None,
                        valid_to=None,
                    )
                    await session.commit()
                    self.assertEqual(result["status"], "success")
                    count = (
                        await session.execute(select(func.count(BudgetForecastFact.id)))
                    ).scalar_one()
                    self.assertEqual(count, 1)
                    row = (
                        await session.execute(select(BudgetForecastFact))
                    ).scalar_one()
                    self.assertEqual(row.budget_room_nights, 70)

            asyncio.run(scenario())


class BiServiceImportTest(unittest.TestCase):
    def test_import_upserts_version_and_records_audit(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from sqlalchemy import func, select

                from app.domain.bi_daily import service
                from app.domain.bi_daily.models import BiImportAudit, BudgetForecastFact

                async with db_session.SessionLocal() as session:
                    result = await service.import_budget_forecast(
                        session,
                        shop_key="shop_bi",
                        payload=_CSV,
                        filename="budget.csv",
                        forecast_version="v1",
                        valid_from=date(2026, 10, 1),
                        valid_to=date(2026, 10, 31),
                    )
                    await session.commit()
                    self.assertEqual(result["status"], "success")
                    self.assertEqual(result["rows_valid"], 2)
                    self.assertEqual(result["rows_written"], 2)

                    rows = (
                        (await session.execute(select(BudgetForecastFact))).scalars().all()
                    )
                    self.assertEqual(len(rows), 2)
                    whole = next(r for r in rows if r.room_type == "")
                    self.assertEqual(whole.budget_room_nights, 50)
                    self.assertEqual(whole.weekday, 3)  # 2026-10-07 周三

                    versions = await service.list_budget_forecast_versions(
                        session, shop_key="shop_bi"
                    )
                    self.assertEqual(len(versions), 1)
                    self.assertEqual(versions[0]["row_count"], 2)

                    # 同版本重导：更新数值不新增行；审计再记一笔。
                    result2 = await service.import_budget_forecast(
                        session,
                        shop_key="shop_bi",
                        payload=_CSV.replace(b",50,", b",70,"),
                        filename="budget-v2-same-version.csv",
                        forecast_version="v1",
                        valid_from=date(2026, 10, 1),
                        valid_to=date(2026, 10, 31),
                    )
                    await session.commit()
                    self.assertEqual(result2["rows_written"], 2)
                    count = (
                        await session.execute(select(func.count(BudgetForecastFact.id)))
                    ).scalar_one()
                    self.assertEqual(count, 2)
                    whole = next(
                        r
                        for r in (await session.execute(select(BudgetForecastFact))).scalars().all()
                        if r.room_type == ""
                    )
                    self.assertEqual(whole.budget_room_nights, 70)
                    audits = (
                        (await session.execute(select(BiImportAudit))).scalars().all()
                    )
                    self.assertEqual(len(audits), 2)
                    self.assertEqual(audits[0].file_name, "budget.csv")

            asyncio.run(scenario())

    def test_import_rejects_shop_without_valid_rows(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from app.domain.bi_daily import service

                async with db_session.SessionLocal() as session:
                    bad = "入住日期,房型,预算间夜\nbad,房,10\n".encode("utf-8-sig")
                    result = await service.import_budget_forecast(
                        session,
                        shop_key="shop_bi",
                        payload=bad,
                        filename="b.csv",
                        forecast_version="v1",
                        valid_from=None,
                        valid_to=None,
                    )
                    await session.commit()
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(result["rows_valid"], 0)
                    self.assertTrue(result["errors"])

            asyncio.run(scenario())


class BiServiceReportTest(unittest.TestCase):
    def test_daily_report_skeleton_competitor_and_completion(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from app.domain.bi_daily import service
                from app.domain.bi_daily.models import BookingPositionFact, BudgetForecastFact

                async with db_session.SessionLocal() as session:
                    # 历史：昨日实际（今年）+ 去年同星期实际 + 预算。
                    from app.domain.shops.models import Shop

                    session.add(Shop(shop_key="shop_bi", name="BI测试店"))
                    session.add_all(
                        [
                            BookingPositionFact(
                                shop_key="shop_bi",
                                platform="ctrip",
                                stay_date=date(2026, 10, 7),
                                weekday=3,
                                as_of_time=datetime(2026, 10, 8, 9, 30),
                                booked_room_nights=88,
                                room_revenue=23559.62,
                                adr=267.72,
                                source="ebk_period_day",
                                source_row_ref="t1",
                            ),
                            BookingPositionFact(
                                shop_key="shop_bi",
                                platform="ctrip",
                                stay_date=date(2025, 10, 8),  # 2026-10-07 的 D-364 同星期
                                weekday=3,
                                as_of_time=datetime(2025, 10, 9, 9, 30),
                                booked_room_nights=80,
                                room_revenue=20000.0,
                                adr=250.0,
                                source="ebk_period_day",
                                source_row_ref="t2",
                            ),
                            BudgetForecastFact(
                                shop_key="shop_bi",
                                stay_date=date(2026, 10, 7),
                                weekday=3,
                                room_type="",
                                budget_room_nights=100,
                                budget_revenue=26000.0,
                                demand_forecast_room_nights=95,
                                demand_forecast_revenue=25000.0,
                                forecast_version="v1",
                                source="csv_import",
                                imported_at=datetime(2026, 10, 1, 8, 0),
                            ),
                        ]
                    )
                    await session.commit()

                    report = await service.build_daily_report(
                        session=session, shop_key="shop_bi", as_of=NOW
                    )
                    # 14 天前瞻骨架恒存在。
                    self.assertEqual(len(report["forward"]), 14)
                    self.assertEqual(report["forward"][0]["stay_date"], "2026-10-08")
                    for row in report["forward"]:
                        own = row["own"]
                        self.assertEqual(own["booked_room_nights"]["status"], "unavailable")
                        self.assertEqual(
                            own["booked_room_nights"]["reason"], "no_on_the_books_source"
                        )
                        self.assertEqual(
                            own["occupancy_rate"]["reason"], "no_inventory_source"
                        )
                        self.assertEqual(own["pickup"]["reason"], "no_on_the_books_source")
                        self.assertIn(row["competitor"]["status"], {"empty", "unbound"})
                    # 无竞品事实时明确提示，不给假价格。
                    self.assertNotIn("prices", report["forward"][0]["competitor"])

                    # 表现区：昨日实际 + 三类完成率。
                    self.assertTrue(report["performance"])
                    day = report["performance"][0]
                    self.assertEqual(day["stay_date"], "2026-10-07")
                    self.assertEqual(day["totals"]["booked_room_nights"], 88)
                    prior = day["prior_year"]
                    self.assertEqual(prior["mapped_date"], "2025-10-08")
                    self.assertEqual(prior["matching_rule"], "minus_364")
                    self.assertAlmostEqual(prior["completion"]["value"], 1.1)
                    self.assertAlmostEqual(day["budget"]["completion"]["value"], 0.88)
                    self.assertAlmostEqual(day["forecast"]["completion"]["value"], 88 / 95)
                    # 质量面板存在且带生成时间。
                    self.assertIn("quality", report)
                    self.assertTrue(report["quality"]["coverage"])

            asyncio.run(scenario())

    def test_daily_report_competitor_summary_uses_facts(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from app.domain.bi_daily import service
                from app.domain.bi_daily.models import CompetitorMarketFact

                async with db_session.SessionLocal() as session:
                    from backend.tests.domain.test_bi_ingest import _seed

                    await _seed(session)
                    await session.commit()
                    from app.domain.bi_daily import ingest

                    await ingest.ingest_calendar_price_points(session, now=NOW)
                    await ingest.ingest_competitor_room_rates(session, now=NOW)
                    await session.commit()

                    report = await service.build_daily_report(
                        session=session, shop_key="shop_bi", as_of=NOW
                    )
                    by_date = {row["stay_date"]: row for row in report["forward"]}
                    comp = by_date["2026-10-11"]["competitor"]
                    self.assertEqual(comp["status"], "available")
                    self.assertEqual(comp["prices"]["min"], 300)
                    self.assertEqual(comp["prices"]["max"], 350)
                    hotels = {h["alias"]: h for h in comp["hotels"]}
                    self.assertIn("本店", hotels)
                    self.assertIn("竞品A", hotels)
                    self.assertEqual(hotels["竞品A"]["price"], 350)
                    # 房型明细挂在对应日期。
                    comp9 = by_date["2026-10-09"]["competitor"]
                    rates = comp9["hotels"][0].get("room_rates") or []
                    self.assertEqual(rates[0]["price"], 280)

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
