"""bi_daily 域数据模型测试：建表、唯一约束、ensure 幂等。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import unittest
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError

from backend.tests._db_isolation import isolated_test_db

# create_all 只建「已导入」的模型：必须在任何 ensure_db_ready 之前导入域模型。
from app.domain.bi_daily import models as _bi_daily_models  # noqa: F401


@contextlib.contextmanager
def _tolerant_isolated_db():
    """isolated_test_db 的容忍版：aiosqlite 连接失效路径在 Windows 下会把
    临时库句柄留到进程退出，TemporaryDirectory 清理抛 PermissionError 但
    不影响断言结果，吞掉即可（%TEMP% 残留文件无害，进程退出自动回收）。"""
    try:
        with isolated_test_db():
            yield
    except PermissionError:
        pass


def _table_names() -> set[str]:
    conn = sqlite3.connect(os.environ["DATABASE_URL"].removeprefix("sqlite+aiosqlite:///"))
    try:
        rows = conn.execute("select name from sqlite_master where type='table'").fetchall()
    finally:
        conn.close()
    return {name for (name,) in rows}


def _columns(table: str) -> set[str]:
    conn = sqlite3.connect(os.environ["DATABASE_URL"].removeprefix("sqlite+aiosqlite:///"))
    try:
        return {row[1] for row in conn.execute(f"pragma table_info({table})")}
    finally:
        conn.close()


class BiDailyModelsTest(unittest.TestCase):
    def test_creates_all_bi_daily_tables_with_expected_columns(self):
        with _tolerant_isolated_db():
            from app.db import session as db_session

            async def _scenario():
                await db_session.ensure_db_ready()

            asyncio.run(_scenario())
            names = _table_names()
            expected = {
                "booking_position_facts",
                "competitor_market_facts",
                "budget_forecast_facts",
                "weekday_date_mappings",
                "bi_import_audits",
                "bi_collection_runs",
                "bi_daily_card_pushes",
            }
            missing = expected - names
            self.assertEqual(set(), missing, f"缺少表：{missing}")

            competitor_cols = _columns("competitor_market_facts")
            booking_cols = _columns("booking_position_facts")
            # 禁止从价格推断的竞品指标列必须存在（值恒 NULL 由 ingest 测试保证）。
            self.assertLessEqual({"occupancy_rate", "booking_pace", "rank"}, competitor_cols)
            self.assertLessEqual(
                {
                    "shop_key",
                    "platform",
                    "stay_date",
                    "weekday",
                    "as_of_time",
                    "booked_room_nights",
                    "booking_orders",
                    "room_revenue",
                    "adr",
                    "occupancy_rate",
                    "available_inventory",
                    "remaining_inventory",
                    "cancellations",
                    "source",
                    "is_trusted",
                    "source_row_ref",
                },
                booking_cols,
            )

    def test_source_row_ref_unique_blocks_duplicate_ingest(self):
        with _tolerant_isolated_db():
            from app.db import session as db_session

            from app.domain.bi_daily.models import BookingPositionFact

            async def _scenario():
                await db_session.ensure_db_ready()
                async with db_session.SessionLocal() as session:
                    common = dict(
                        shop_key="shop_x",
                        platform="ctrip",
                        stay_date=date(2026, 10, 10),
                        weekday=6,
                        as_of_time=datetime(2026, 10, 8, 9, 40, 0),
                        source="ebk_period_day",
                        source_row_ref="ebk_period_read:1",
                    )
                    session.add(BookingPositionFact(**common, booked_room_nights=10))
                    await session.flush()
                    session.add(BookingPositionFact(**common, booked_room_nights=11))
                    try:
                        await session.flush()
                        self.fail("source_row_ref 重复应被唯一约束拦截")
                    except IntegrityError:
                        await session.rollback()
                # 同一事件循环内显式 dispose，释放 Windows 下的文件句柄。
                await db_session.engine.dispose()

            asyncio.run(_scenario())

    def test_budget_forecast_version_unique_per_stay_room(self):
        with _tolerant_isolated_db():
            from app.db import session as db_session

            from app.domain.bi_daily.models import BudgetForecastFact

            async def _scenario():
                await db_session.ensure_db_ready()
                async with db_session.SessionLocal() as session:
                    common = dict(
                        shop_key="shop_x",
                        stay_date=date(2026, 10, 10),
                        weekday=6,
                        forecast_version="v1",
                    )
                    session.add(BudgetForecastFact(**common, budget_room_nights=50))
                    await session.flush()
                    # room_type 空串=全店口径；同版本同日重复插入必须被唯一约束拦下。
                    session.add(BudgetForecastFact(**common, budget_room_nights=60))
                    try:
                        await session.flush()
                        self.fail("同版本同入住日重复导入应被唯一约束拦截")
                    except IntegrityError:
                        await session.rollback()
                await db_session.engine.dispose()

            asyncio.run(_scenario())

    def test_weekday_mapping_current_date_unique(self):
        with _tolerant_isolated_db():
            from app.db import session as db_session

            from app.domain.bi_daily.models import WeekdayDateMapping

            async def _scenario():
                await db_session.ensure_db_ready()
                async with db_session.SessionLocal() as session:
                    session.add(
                        WeekdayDateMapping(
                            current_stay_date=date(2026, 10, 10),
                            current_weekday=6,
                            prior_year_same_weekday_stay_date=date(2025, 10, 11),
                            matching_rule="minus_364",
                        )
                    )
                    await session.flush()
                    session.add(
                        WeekdayDateMapping(
                            current_stay_date=date(2026, 10, 10),
                            current_weekday=6,
                            matching_rule="none",
                        )
                    )
                    try:
                        await session.flush()
                        self.fail("同日期重复映射应被唯一约束拦截")
                    except IntegrityError:
                        await session.rollback()
                await db_session.engine.dispose()

            asyncio.run(_scenario())

    def test_ensure_bi_daily_schema_is_idempotent(self):
        with _tolerant_isolated_db():
            from app.db import session as db_session

            from app.domain.bi_daily.migrations import ensure_bi_daily_schema

            async def _scenario():
                await db_session.ensure_db_ready()
                async with db_session.engine.begin() as connection:
                    await ensure_bi_daily_schema(connection)
                async with db_session.engine.begin() as connection:
                    await ensure_bi_daily_schema(connection)
                await db_session.engine.dispose()

            asyncio.run(_scenario())


if __name__ == "__main__":
    unittest.main()
