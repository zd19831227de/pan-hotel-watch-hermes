from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


class _isolated_env:
    def __enter__(self) -> "_isolated_env":
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {
            key: os.environ.get(key)
            for key in (
                "BROWSER_STATE_DIR",
                "DATABASE_URL",
                "DATABASE_AUTO_CREATE_TABLES",
            )
        }
        os.environ["BROWSER_STATE_DIR"] = self._tmp.name
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'orders.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        from app.core.settings import get_settings

        get_settings.cache_clear()
        # 进圈即重绑全局引擎到临时库：否则会沿用上一用例的引擎（甚至写真实库）。
        db_session = _reload_db()
        db_session.settings = get_settings()
        return self

    def __exit__(self, *args) -> None:
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from app.core.settings import get_settings

        get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db():
    import importlib

    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(
        os.environ["DATABASE_URL"], pool_pre_ping=True
    )
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return importlib.reload(db_session)


class OrderSnapshotShopAttributionTests(unittest.TestCase):
    def test_legacy_tables_get_shop_columns_via_add_column(self) -> None:
        with _isolated_env():
            async def scenario() -> tuple[set[str], set[str]]:
                db_session = _reload_db()
                async with db_session.engine.begin() as connection:
                    from sqlalchemy import text

                    # 先按 09 月旧结构手造两张表（无归属列），模拟线上既有库。
                    await connection.execute(
                        text(
                            """
                            CREATE TABLE reservation_order_snapshots (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                platform VARCHAR(16) NOT NULL,
                                reservation_date VARCHAR(10) NOT NULL,
                                total_count INTEGER NOT NULL DEFAULT 0,
                                by_room_type_json JSON NULL,
                                time_caliber VARCHAR(24) NULL,
                                source VARCHAR(16) NOT NULL DEFAULT 'manual',
                                captured_at DATETIME NOT NULL,
                                created_at DATETIME NOT NULL
                            )
                            """
                        )
                    )
                    await connection.execute(
                        text(
                            """
                            CREATE TABLE refund_order_snapshots (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                platform VARCHAR(16) NOT NULL,
                                refund_date VARCHAR(10) NOT NULL,
                                total_count INTEGER NOT NULL DEFAULT 0,
                                by_room_type_json JSON NULL,
                                source VARCHAR(16) NOT NULL DEFAULT 'push_read',
                                captured_at DATETIME NOT NULL,
                                created_at DATETIME NOT NULL
                            )
                            """
                        )
                    )
                    from app.domain.reservation_orders.migrations import (
                        ensure_reservation_orders_schema,
                    )
                    from app.domain.refund_orders.migrations import (
                        ensure_refund_orders_schema,
                    )

                    await ensure_reservation_orders_schema(connection)
                    await ensure_reservation_orders_schema(connection)
                    await ensure_refund_orders_schema(connection)
                    await ensure_refund_orders_schema(connection)

                    from sqlalchemy import inspect

                    def columns(sync_connection, table):
                        inspector = inspect(sync_connection)
                        return {
                            column["name"]
                            for column in inspector.get_columns(table)
                        }

                    reservation_columns = await connection.run_sync(
                        lambda c: columns(c, "reservation_order_snapshots")
                    )
                    refund_columns = await connection.run_sync(
                        lambda c: columns(c, "refund_order_snapshots")
                    )
                return reservation_columns, refund_columns

            reservation_columns, refund_columns = asyncio.run(scenario())

        expected = {"shop_key", "platform_account_id", "browser_session_id"}
        self.assertTrue(expected.issubset(reservation_columns))
        self.assertTrue(expected.issubset(refund_columns))

    def test_fetch_persists_shop_attribution_from_resolved_binding(self) -> None:
        with _isolated_env():
            shop_key = _prepare_single_shop_account()

            class FakeReservationAdapter:
                async def fetch_today_reservations(self, chat_id=None):
                    return {
                        "reservation_date": "2026-09-28",
                        "total_count": 3,
                        "by_room_type": {"大床房": 2},
                        "by_hour": None,
                        "time_caliber": "截至当前",
                    }

            class FakeRefundAdapter:
                async def fetch_refund_orders(self, chat_id=None):
                    return {
                        "refund_date": "2026-09-28",
                        "total_count": 1,
                        "by_room_type": {"大床房": 1},
                    }

            async def scenario() -> tuple[dict, dict]:
                from app.domain.refund_orders import service as refund_service
                from app.domain.reservation_orders import (
                    service as reservation_service,
                )

                reserved = await reservation_service.fetch_today_reservations(
                    platform="meituan", adapter=FakeReservationAdapter()
                )
                refunded = await refund_service.fetch_refund_orders(
                    platform="meituan", adapter=FakeRefundAdapter(), source="manual"
                )
                return reserved, refunded

            reserved, refunded = asyncio.run(scenario())

        self.assertEqual(reserved["status"], "ok")
        self.assertEqual(reserved["snapshot"]["shop_key"], shop_key)
        self.assertEqual(reserved["snapshot"]["platform_account_id"], "mx_attr")
        self.assertEqual(
            reserved["snapshot"]["browser_session_id"], "browser-mx_attr"
        )
        self.assertEqual(refunded["status"], "ok")
        self.assertEqual(refunded["snapshot"]["shop_key"], shop_key)

    def test_latest_with_shop_filter_excludes_null_history(self) -> None:
        with _isolated_env():
            _prepare_single_shop_account()

            async def scenario() -> tuple[dict | None, dict | None, dict | None]:
                from app.domain.refund_orders import repository as refund_repo
                from app.domain.reservation_orders import (
                    repository as reservation_repo,
                )

                await reservation_repo.append_reservation_snapshot(
                    platform="meituan",
                    reservation_date="2026-09-28",
                    total_count=5,
                    by_room_type={},
                    shop_key="shop_filtered",
                    platform_account_id="mx_attr",
                    browser_session_id="browser-mx_attr",
                )
                # 历史无归属行（id 更大、更新）：不带过滤时应可见，单店视图应排除。
                await reservation_repo.append_reservation_snapshot(
                    platform="meituan",
                    reservation_date="2026-09-28",
                    total_count=9,
                    by_room_type={},
                )
                scoped = await reservation_repo.get_latest_reservation_snapshot(
                    platform="meituan",
                    reservation_date="2026-09-28",
                    shop_key="shop_filtered",
                )
                overall = await reservation_repo.get_latest_reservation_snapshot(
                    platform="meituan", reservation_date="2026-09-28"
                )
                missing = await reservation_repo.get_latest_reservation_snapshot(
                    platform="meituan",
                    reservation_date="2026-09-28",
                    shop_key="shop_other",
                )
                return scoped, overall, missing

            scoped, overall, missing = asyncio.run(scenario())

        self.assertIsNotNone(scoped)
        self.assertEqual(scoped["total_count"], 5)
        self.assertEqual(scoped["shop_key"], "shop_filtered")
        self.assertIsNotNone(overall)
        self.assertEqual(overall["total_count"], 9)
        self.assertIsNone(missing)


def _prepare_single_shop_account() -> str:
    """建店 + 注册唯一美团 EBK 账号（chat 为空时唯一候选可确定解析）。"""
    from tests._db_isolation import create_isolated_test_shop

    shop_key = create_isolated_test_shop("归属测试店铺")
    from app.domain.browser.sessions import (
        BrowserSessionCreate,
        get_browser_session_store,
    )

    get_browser_session_store().create(
        BrowserSessionCreate(
            platform_account_id="mx_attr",
            platform="meituan",
            intended_use="ebk",
            shop_key=shop_key,
            session_port=9541,
            browser_session_id="browser-mx_attr",
        )
    )
    return shop_key


if __name__ == "__main__":
    unittest.main()
