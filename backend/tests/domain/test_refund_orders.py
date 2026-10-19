from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class RefundOrdersTests(unittest.TestCase):
    def setUp(self) -> None:
        # .env 开启真实退单适配器时，import 时已注册真 adapter；测试态需清空，
        # 保证 not_ready 分支可测且不连真实浏览器（fake adapter 注入不受影响）。
        from app.domain.refund_orders import adapters

        adapters._ADAPTERS.clear()

    def test_schema_and_repository_roundtrip(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                await db_session.ensure_db_ready()
                repo = importlib.reload(
                    importlib.import_module("app.domain.refund_orders.repository")
                )
                snapshot = await repo.append_refund_snapshot(
                    platform="meituan",
                    refund_date="2026-09-02",
                    total_count=3,
                    by_room_type={"行政套房": 2, "豪华套房": 1},
                    by_hour={"10": 2, "14": 1},
                    time_caliber="purchase_time",
                )
                self.assertIsNotNone(snapshot["id"])
                self.assertEqual(snapshot["total_count"], 3)
                self.assertEqual(snapshot["by_room_type"], {"行政套房": 2, "豪华套房": 1})
                self.assertEqual(snapshot["by_hour"], {"10": 2, "14": 1})
                self.assertEqual(
                    snapshot["peak_hours"],
                    [{"hour": "10", "count": 2}, {"hour": "14", "count": 1}],
                )
                self.assertEqual(snapshot["time_caliber"], "purchase_time")
                self.assertIsNotNone(snapshot["captured_at"])

                # 同日多次抓取 append-only，查询取最新。
                await repo.append_refund_snapshot(
                    platform="meituan",
                    refund_date="2026-09-02",
                    total_count=5,
                    by_room_type={"行政套房": 3, "豪华套房": 2},
                    by_hour={"14": 3},
                    time_caliber="purchase_time",
                )
                latest = await repo.get_latest_refund_snapshot(
                    platform="meituan", refund_date="2026-09-02"
                )
                self.assertEqual(latest["total_count"], 5)
                self.assertEqual(latest["by_hour"], {"14": 3})
                self.assertEqual(latest["peak_hours"], [{"hour": "14", "count": 3}])
                other = await repo.get_latest_refund_snapshot(platform="ctrip")
                self.assertIsNone(other)

            asyncio.run(scenario())

    def test_service_not_ready_and_success_paths(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                service = importlib.reload(
                    importlib.import_module("app.domain.refund_orders.service")
                )
                repo = importlib.reload(
                    importlib.import_module("app.domain.refund_orders.repository")
                )

                # 平台未接真实 adapter：返回 not_ready，不落库（无假数据）。
                result = await service.fetch_refund_orders(platform="meituan")
                self.assertEqual(result["status"], "not_ready")
                self.assertIsNone(await repo.get_latest_refund_snapshot(platform="meituan"))

                # 假 adapter 注入：成功抓取并落库。
                class _FakeAdapter:
                    platform = "meituan"

                    async def fetch_refund_orders(self):
                        return {
                            "refund_date": "2026-09-02",
                            "total_count": 4,
                            "by_room_type": {"行政套房": 4},
                            "by_hour": {"10": 4},
                            "time_caliber": "purchase_time",
                        }

                ok = await service.fetch_refund_orders(
                    platform="meituan", adapter=_FakeAdapter()
                )
                self.assertEqual(ok["status"], "ok")
                self.assertEqual(ok["snapshot"]["total_count"], 4)
                self.assertEqual(ok["snapshot"]["by_hour"], {"10": 4})
                # 显式传 refund_date：查询默认取「今天」，固定造数日期跨天后查不到。
                latest = await repo.get_latest_refund_snapshot(
                    platform="meituan", refund_date="2026-09-02"
                )
                self.assertEqual(latest["by_room_type"], {"行政套房": 4})
                self.assertEqual(latest["by_hour"], {"10": 4})
                self.assertEqual(latest["time_caliber"], "purchase_time")

            asyncio.run(scenario())

    def test_service_adapter_crash_returns_error_not_500(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                service = importlib.reload(
                    importlib.import_module("app.domain.refund_orders.service")
                )

                class _CrashAdapter:
                    platform = "meituan"

                    async def fetch_refund_orders(self):
                        raise RuntimeError("connect ECONNREFUSED 127.0.0.1:9521")

                result = await service.fetch_refund_orders(
                    platform="meituan", adapter=_CrashAdapter()
                )
                self.assertEqual(result["status"], "error")
                self.assertIn("ECONNREFUSED", result["error"])

            asyncio.run(scenario())

    def test_api_get_and_fetch_trigger(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                repo = importlib.reload(
                    importlib.import_module("app.domain.refund_orders.repository")
                )
                await repo.append_refund_snapshot(
                    platform="meituan",
                    refund_date="2026-09-02",
                    total_count=2,
                    by_room_type={"豪华套房": 2},
                )
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                got = client.get(
                    "/api/v1/refunds",
                    params={"platform": "meituan", "refund_date": "2026-09-02"},
                )
                self.assertEqual(got.status_code, 200)
                self.assertEqual(got.json()["snapshot"]["total_count"], 2)

                missing = client.get(
                    "/api/v1/refunds",
                    params={"platform": "ctrip", "refund_date": "2026-09-02"},
                )
                self.assertEqual(missing.status_code, 404)

                trig = client.post("/api/v1/refunds/fetch", json={"platform": "ctrip"})
                self.assertEqual(trig.status_code, 200)
                self.assertEqual(trig.json()["result"]["status"], "not_ready")

            asyncio.run(scenario())


class MeituanRefundParseTests(unittest.TestCase):
    """美团退单解析：购买日期=今天且已取消的订单按房型计数。"""

    def test_parse_today_cancelled_orders_by_room(self) -> None:
        from app.domain.refund_orders.meituan_refund_adapter import (
            _parse_meituan_cancelled_orders,
        )

        body = "\n".join(
            [
                "共 1095 个订单",
                "订单号：5035036023999999991",
                "酒店：静里全套房酒店",
                "自动接单",
                "购买时间：2026-09-02 10:00:21",
                "豪华套房[房间含冰箱、洗衣机、微波炉、电磁炉]",
                "1 间",
                "已取消",
                "订单号：5035036023888888882",
                "酒店：静里全套房酒店",
                "购买时间：2026-09-02 08:10:00",
                "行政套房[房间含冰箱、洗衣机、微波炉、电磁炉]",
                "1 间",
                "已取消",
                "订单号：5035036000000000001",
                "酒店：静里全套房酒店",
                "购买时间：2026-09-01 21:41:26",
                "豪华套房[房间含冰箱、洗衣机、微波炉、电磁炉]",
                "已取消",
            ]
        )
        result = _parse_meituan_cancelled_orders(body, today="2026-09-02")
        self.assertEqual(result["refund_date"], "2026-09-02")
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["by_room_type"], {"豪华套房": 1, "行政套房": 1})
        # 购买时间的小时段：10:00 与 08:10 两单当日且已取消。
        self.assertEqual(result["by_hour"], {"10": 1, "08": 1})
        self.assertEqual(result["time_caliber"], "purchase_time")

    def test_parse_no_today_orders_returns_zero(self) -> None:
        from app.domain.refund_orders.meituan_refund_adapter import (
            _parse_meituan_cancelled_orders,
        )

        body = "订单号：5035036000000000001\n购买时间：2026-09-01 21:41:26\n豪华套房[配置]\n已取消"
        result = _parse_meituan_cancelled_orders(body, today="2026-09-02")
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["by_room_type"], {})
        self.assertEqual(result["by_hour"], {})


class CtripRefundParseTests(unittest.TestCase):
    """携程退单解析：今日预订列表中通知时间=今天且已取消，按房型计数。"""

    def test_parse_today_cancelled_by_room(self) -> None:
        from app.domain.refund_orders.ctrip_refund_adapter import (
            _parse_ctrip_cancelled_orders,
        )

        body = "\n".join(
            [
                "50条订单",
                "1128150000000002",
                "09/02 17:24",
                "已取消",
                "1 间",
                "豪华套房(全屋智能+会客厅+全套厨卫家电)<乐享旅程><无早>",
                "预",
                "直",
                "2 晚",
                "张万里",
                "1128150000000002",
                "取消",
                "09-02 17:24",
                "已取消",
                "1128150000000001",
                "09/02 16:32",
                "已取消",
                "1 间",
                "家庭双卧套房(独立卧室+豪华会客厅+全套厨卫家电)<乐享旅程><无早>",
                "李烈",
                "1128150000000004",
                "09/02 16:31",
                "已接单",
                "1 间",
                "家庭双卧套房(独立卧室+豪华会客厅+全套厨卫家电)<乐享旅程><无早>",
                "李晋",
                "1128150000000002",
                "09/01 19:50",
                "已取消",
                "1 间",
                "豪华套房(全屋智能+会客厅+全套厨卫家电)<乐享旅程><无早>",
            ]
        )
        result = _parse_ctrip_cancelled_orders(
            body, today_md="09/02", today_iso="2026-09-02"
        )
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["by_room_type"], {"豪华套房": 1, "家庭双卧套房": 1})
        # 通知时间的小时段：17:24 与 16:32 两单已取消且今天。
        self.assertEqual(result["by_hour"], {"17": 1, "16": 1})
        self.assertEqual(result["time_caliber"], "notify_time")

    def test_parse_ignores_yesterday_cancel(self) -> None:
        from app.domain.refund_orders.ctrip_refund_adapter import (
            _parse_ctrip_cancelled_orders,
        )

        body = "\n".join(
            ["1128150000000002", "09/01 19:50", "已取消", "1 间", "豪华套房(配置)<无早>"]
        )
        result = _parse_ctrip_cancelled_orders(
            body, today_md="09/02", today_iso="2026-09-02"
        )
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["by_room_type"], {})
        self.assertEqual(result["by_hour"], {})


class FliggyRefundParseTests(unittest.TestCase):
    """飞猪退单解析：预订时间=今天且状态含已取消，按房型计数（无筛选按钮口径）。"""

    def test_parse_today_cancelled_by_room(self) -> None:
        from app.domain.refund_orders.fliggy_refund_adapter import (
            _parse_fliggy_cancelled_orders,
        )

        body = "\n".join(
            [
                "共有101条订单",
                "3317003000000000005",
                "已取消(确认后)",
                "精英套房（大复式+独卧+会客厅+独立办公+全套厨卫家电）-单早-限时退【F1面纱专享】-含单早",
                "1",
                "间",
                "09/02 14:56",
                "09/02-09/03",
                "1",
                "晚",
                "王军",
                "预付",
                "3316378000000000002",
                "已接单(自动接单)",
                "豪华套房（全屋声控+智能马桶+小冰箱+洗烘一体机）-单早-限时退【F1面纱专享】-含单早",
                "1",
                "间",
                "09/02 14:03",
                "预付",
                "3316823000000000004",
                "已取消(确认后)",
                "豪华套房（全屋声控+智能马桶+小冰箱+洗烘一体机）-单早-限时退【F1面纱专享】-含单早",
                "1",
                "间",
                "09/02 11:08",
                "预付",
                "3316376000000000001",
                "已接单",
                "豪华套房（配置）",
                "1",
                "间",
                "09/01 23:55",
            ]
        )
        result = _parse_fliggy_cancelled_orders(
            body, today_md="09/02", today_iso="2026-09-02"
        )
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["by_room_type"], {"精英套房": 1, "豪华套房": 1})
        # 预订时间的小时段：14:56 与 11:08 两单今日且已取消。
        self.assertEqual(result["by_hour"], {"14": 1, "11": 1})
        self.assertEqual(result["time_caliber"], "booking_time")

    def test_parse_ignores_yesterday(self) -> None:
        from app.domain.refund_orders.fliggy_refund_adapter import (
            _parse_fliggy_cancelled_orders,
        )

        body = "\n".join(["3316376000000000001", "已取消(确认后)", "豪华套房（配置）", "1", "间", "09/01 23:55"])
        result = _parse_fliggy_cancelled_orders(body, today_md="09/02", today_iso="2026-09-02")
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["by_hour"], {})


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "refund-orders.db"
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
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return importlib.reload(db_session)


class RefundScrollCompletenessTests(unittest.TestCase):
    """退单侧滚动/翻页加载「今日单是否读全」判定（复用今日预定的页面加载逻辑）。"""

    def test_ctrip_refund_complete_via_end_marker(self) -> None:
        from app.domain.refund_orders.ctrip_refund_adapter import (
            _ctrip_refund_list_complete,
        )

        body = "1128150000000002\n09/04 00:30\n已取消\n1 间\n豪华套房(配置)<无早>\n没有更多订单了"
        self.assertTrue(_ctrip_refund_list_complete(body, today_md="09/04"))

    def test_ctrip_refund_incomplete_only_today(self) -> None:
        from app.domain.refund_orders.ctrip_refund_adapter import (
            _ctrip_refund_list_complete,
        )

        body = "1128150000000002\n09/04 15:00\n已取消\n1 间\n豪华套房(配置)<无早>"
        self.assertFalse(_ctrip_refund_list_complete(body, today_md="09/04"))

    def test_fliggy_refund_complete_when_older(self) -> None:
        from app.domain.refund_orders.fliggy_refund_adapter import (
            _fliggy_refund_list_complete,
        )

        body = "3316398000000000003\n已取消\n豪华套房（配置）\n1\n间\n09/03 23:00"
        self.assertTrue(_fliggy_refund_list_complete(body, today_md="09/04"))

    def test_meituan_refund_complete_when_older(self) -> None:
        from app.domain.refund_orders.meituan_refund_adapter import (
            _meituan_refund_list_complete,
        )

        body = "订单号：5035036023999999991\n购买时间：2026-09-03 10:00:21\n豪华套房[配置]"
        self.assertTrue(_meituan_refund_list_complete(body, today="2026-09-04"))


if __name__ == "__main__":
    unittest.main()