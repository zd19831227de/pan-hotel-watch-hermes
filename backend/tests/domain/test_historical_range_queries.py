from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class RefundRangeQueryTests(unittest.TestCase):
    """退单历史范围查询：按业务日期（refund_date）过滤，每日取最新一条 + 汇总。"""

    def test_repository_lists_latest_per_date(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.refund_orders import repository

                # 9-05 两条（取最新）、9-06 一条、9-07 不在范围内。
                await repository.append_refund_snapshot(
                    platform="meituan", refund_date="2026-09-05", total_count=2,
                    by_room_type={"豪华套房": 2},
                )
                await repository.append_refund_snapshot(
                    platform="meituan", refund_date="2026-09-05", total_count=4,
                    by_room_type={"豪华套房": 3, "行政套房": 1},
                )
                await repository.append_refund_snapshot(
                    platform="meituan", refund_date="2026-09-06", total_count=5,
                    by_room_type={"行政套房": 5},
                )
                await repository.append_refund_snapshot(
                    platform="meituan", refund_date="2026-09-07", total_count=9,
                    by_room_type={"豪华套房": 9},
                )
                snapshots = await repository.list_refund_snapshots_by_date_range(
                    platform="meituan", date_from="2026-09-05", date_to="2026-09-06"
                )
                self.assertEqual(
                    [(s["refund_date"], s["total_count"]) for s in snapshots],
                    [("2026-09-05", 4), ("2026-09-06", 5)],
                )

            asyncio.run(scenario())

    def test_service_range_aggregates_totals(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.refund_orders import repository, service

                await repository.append_refund_snapshot(
                    platform="ctrip", refund_date="2026-09-01", total_count=3,
                    by_room_type={"豪华套房": 3},
                )
                await repository.append_refund_snapshot(
                    platform="ctrip", refund_date="2026-09-02", total_count=2,
                    by_room_type={"豪华套房": 1, "行政套房": 1},
                )
                result = await service.get_refund_status_range(
                    platform="ctrip", date_from="2026-09-01", date_to="2026-09-02"
                )
                self.assertEqual(result["total_count"], 5)
                self.assertEqual(result["by_room_type"], {"豪华套房": 4, "行政套房": 1})
                self.assertEqual(len(result["snapshots"]), 2)
                self.assertEqual(
                    result["date_range"], {"from": "2026-09-01", "to": "2026-09-02"}
                )

            asyncio.run(scenario())

    def test_api_range_and_validation(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.refund_orders import repository

                await repository.append_refund_snapshot(
                    platform="meituan", refund_date="2026-09-01", total_count=2,
                    by_room_type={"豪华套房": 2},
                )
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                got = client.get(
                    "/api/v1/refunds",
                    params={
                        "platform": "meituan",
                        "date_from": "2026-09-01",
                        "date_to": "2026-09-02",
                    },
                )
                self.assertEqual(got.status_code, 200)
                body = got.json()
                self.assertEqual(body["total_count"], 2)
                self.assertEqual(body["snapshots"][0]["refund_date"], "2026-09-01")

                # from > to → 422；非法日期 → 422。
                bad_order = client.get(
                    "/api/v1/refunds",
                    params={
                        "platform": "meituan",
                        "date_from": "2026-09-05",
                        "date_to": "2026-09-01",
                    },
                )
                self.assertEqual(bad_order.status_code, 422)
                bad_date = client.get(
                    "/api/v1/refunds",
                    params={"platform": "meituan", "date_from": "abc"},
                )
                self.assertEqual(bad_date.status_code, 422)

                # 不带日期 → 行为不变（今日口径，404 无记录）。
                unchanged = client.get("/api/v1/refunds", params={"platform": "meituan"})
                self.assertEqual(unchanged.status_code, 404)

            asyncio.run(scenario())


class ReservationRangeQueryTests(unittest.TestCase):
    def test_repository_and_api_range(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.reservation_orders import repository

                await repository.append_reservation_snapshot(
                    platform="ctrip", reservation_date="2026-09-01", total_count=6,
                    by_room_type={"豪华套房": 6},
                )
                await repository.append_reservation_snapshot(
                    platform="ctrip", reservation_date="2026-09-01", total_count=8,
                    by_room_type={"豪华套房": 5, "行政套房": 3},
                )
                snapshots = await repository.list_reservation_snapshots_by_date_range(
                    platform="ctrip", date_from="2026-09-01", date_to="2026-09-01"
                )
                self.assertEqual(
                    [(s["reservation_date"], s["total_count"]) for s in snapshots],
                    [("2026-09-01", 8)],
                )
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                got = client.get(
                    "/api/v1/reservations",
                    params={
                        "platform": "ctrip",
                        "date_from": "2026-09-01",
                        "date_to": "2026-09-01",
                    },
                )
                self.assertEqual(got.status_code, 200)
                body = got.json()
                self.assertEqual(body["total_count"], 8)
                self.assertEqual(body["by_room_type"], {"豪华套房": 5, "行政套房": 3})

            asyncio.run(scenario())


class PeriodReadDayDimensionTests(unittest.TestCase):
    """period=day（昨日经营/排名）维度支持。"""

    def test_supported_dimensions_include_day(self) -> None:
        from app.domain.ebk_period_reads.service import is_dimension_supported

        for platform in ("ctrip", "meituan", "fliggy"):
            for kind in ("business", "ranking"):
                self.assertTrue(
                    is_dimension_supported(platform, kind, "day"),
                    f"{platform}/{kind}/day 应支持",
                )
        self.assertFalse(is_dimension_supported("fliggy", "ranking", "month"))

    def test_ctrip_outline_yesterday_parser(self) -> None:
        from app.domain.browser.ebk_period_read_flows import (
            _parse_ctrip_outline_yesterday_section,
        )

        body = "\n".join(
            [
                "收益概览",
                "实时数据 更新时间 今天 17:18",
                "实时预订订单",
                "52",
                "昨日数据 2026/09/06的数据",
                "离店销售额",
                "18050.19",
                "上周同期",
                "3",
                "竞争圈排名",
                "17 / 23",
                "离店间夜量",
                "62",
                "上周同期",
                "89",
                "竞争圈排名",
                "14 / 23",
                "成交率",
                "54 .39%",
                "上周同期",
                "64.49%",
                "竞争圈排名",
                "23 / 23",
                "平均卖价",
                "291 .13",
                "上周同期",
                "368",
                "竞争圈排名",
                "16 / 23",
                "流量概览",
            ]
        )
        result = _parse_ctrip_outline_yesterday_section(body)
        self.assertEqual(result["data_period_label"], "2026/09/06")
        metrics = result["metrics"]
        self.assertEqual(metrics["离店销售额"]["value"], "18050.19")
        self.assertEqual(metrics["离店销售额"]["竞争圈排名"], "17/23")
        self.assertEqual(metrics["离店间夜量"]["value"], "62")
        self.assertEqual(metrics["成交率"]["value"], "54.39%")
        self.assertEqual(metrics["平均卖价"]["value"], "291.13")
        self.assertEqual(metrics["平均卖价"]["上周同期"], "368")

    def test_fliggy_daily_yesterday_parser(self) -> None:
        from app.domain.browser.ebk_period_read_flows import (
            _parse_fliggy_daily_yesterday_section,
        )

        body = "\n".join(
            [
                "实时数据 / 更新时间：2026/09/07 17:19 / 今日在店 / 4间夜",
                "昨日数据",
                "更新时间 2026/09/06",
                "在店间夜",
                "2间夜",
                "上周同期33%",
                "间夜单价",
                "251元",
                "上周同期1%",
                "预订订单",
                "3间夜",
                "上周同期25%",
                "客人点评得分",
            ]
        )
        result = _parse_fliggy_daily_yesterday_section(body)
        self.assertEqual(result["data_period_label"], "2026/09/06")
        metrics = result["metrics"]
        self.assertEqual(metrics["在店间夜"]["value"], "2间夜")
        self.assertEqual(metrics["在店间夜"]["上周同期"], "33%")
        self.assertEqual(metrics["间夜单价"]["value"], "251元")
        self.assertEqual(metrics["预订订单"]["value"], "3间夜")

    def test_range_query_matches_day_snapshots_by_business_date(self) -> None:
        """day 快照按业务日期（抓取日前一天）匹配；同业务日多抓取取最新一条。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from datetime import timedelta

                from app.domain.ebk_period_reads import repository
                from app.domain.ebk_period_reads.models import local_now

                # 抓取于 D-3 的 day 快照 → 业务日期 D-4；同日稍晚再抓一条（取最新）。
                await repository.append_period_read_snapshot(
                    platform="ctrip", data_kind="business", period="day",
                    status="ok", caliber="经营概览·昨日数据", summary="first",
                    payload={"sections": {}},
                    captured_at=local_now() - timedelta(days=3),
                )
                await repository.append_period_read_snapshot(
                    platform="ctrip", data_kind="business", period="day",
                    status="ok", caliber="经营概览·昨日数据", summary="second",
                    payload={"sections": {}},
                    captured_at=local_now() - timedelta(days=3) + timedelta(minutes=10),
                )
                # week 快照抓取于 D-3：按抓取时间窗归入 [D-3] 查询。
                await repository.append_period_read_snapshot(
                    platform="ctrip", data_kind="business", period="week",
                    status="ok", caliber="预订销售·按周", payload={"sections": {}},
                    captured_at=local_now() - timedelta(days=3),
                )
                business_date = (local_now() - timedelta(days=4)).date().isoformat()
                capture_date = (local_now() - timedelta(days=3)).date().isoformat()

                # 业务日期窗口 [D-4, D-4]：day 窗口 [D-3, D-2) 命中并去重取最新；
                # week 按抓取窗 [D-4, D-3) 不含 D-3 抓取的 week 快照。
                got = await repository.list_period_reads_by_captured_range(
                    platform="ctrip", data_kind="business",
                    date_from=business_date, date_to=business_date,
                )
                self.assertEqual(
                    [(s["period"], s["summary"]) for s in got],
                    [("day", "second")],
                    got,
                )

                # 抓取日期窗口 [D-3, D-3]：day 快照（业务 D-4）不再命中；
                # week 快照按抓取时间命中。
                got_capture = await repository.list_period_reads_by_captured_range(
                    platform="ctrip", data_kind="business",
                    date_from=capture_date, date_to=capture_date,
                )
                self.assertEqual(
                    [s["period"] for s in got_capture], ["week"], got_capture
                )

            asyncio.run(scenario())

    def test_api_rejects_impossible_calendar_date(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                # 2026-02-31 通过正则但日历上不存在：应 422 而非 500/静默查空。
                bad = client.get(
                    "/api/v1/ebk-period-reads",
                    params={
                        "platform": "ctrip",
                        "data_kind": "business",
                        "date_from": "2026-02-31",
                    },
                )
                self.assertEqual(bad.status_code, 422)
                bad_refund = client.get(
                    "/api/v1/refunds",
                    params={"platform": "ctrip", "date_from": "2026-02-31"},
                )
                self.assertEqual(bad_refund.status_code, 422)
                bad_reservation = client.get(
                    "/api/v1/reservations",
                    params={"platform": "ctrip", "date_from": "2026-02-31"},
                )
                self.assertEqual(bad_reservation.status_code, 422)

            asyncio.run(scenario())

    def test_api_period_day_and_range_query(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository
                from app.domain.ebk_period_reads.models import local_now
                from datetime import timedelta

                await repository.append_period_read_snapshot(
                    platform="ctrip", data_kind="business", period="day",
                    status="ok", caliber="经营概览·昨日数据",
                    payload={"sections": {}},
                )
                # 前一天的 day 快照（captured_at 跨天，正常 fresh 查询拿不到）。
                await repository.append_period_read_snapshot(
                    platform="ctrip", data_kind="business", period="day",
                    status="ok", caliber="经营概览·昨日数据(旧)",
                    payload={"sections": {}},
                    captured_at=local_now() - timedelta(days=1),
                )
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                # period=day 正常当日读取。
                got = client.get(
                    "/api/v1/ebk-period-reads",
                    params={"platform": "ctrip", "data_kind": "business", "period": "day"},
                )
                self.assertEqual(got.status_code, 200)
                self.assertEqual(got.json()["snapshot"]["caliber"], "经营概览·昨日数据")

                # 日期范围模式：按业务日期返回历史快照列表（day=抓取日前一天；
                # week/month 按抓取时间；忽略 period 过滤，两种都返回）。
                rng = client.get(
                    "/api/v1/ebk-period-reads",
                    params={
                        "platform": "ctrip",
                        "data_kind": "business",
                        "date_from": "2026-01-01",
                        "date_to": "2026-12-31",
                    },
                )
                self.assertEqual(rng.status_code, 200)
                snapshots = rng.json()["snapshots"]
                self.assertEqual(len(snapshots), 2)
                self.assertEqual(rng.json()["date_range"]["from"], "2026-01-01")

                # 非法 period → 422。
                bad = client.get(
                    "/api/v1/ebk-period-reads",
                    params={"platform": "ctrip", "data_kind": "business", "period": "quarter"},
                )
                self.assertEqual(bad.status_code, 422)

            asyncio.run(scenario())


class PeriodDaySnapshotSchedulerTests(unittest.TestCase):
    """P3（修正后）：日终快照轮次——读前一日的经营+排名（三平台×2 维度）。"""

    @staticmethod
    def _single_store_bindings(platform: str) -> list[dict[str, str]]:
        # 2026-09-16 起轮次按店铺遍历；单店铺桩保持旧口径的 6 维度读取。
        return [
            {
                "platform_account_id": f"{platform}-acct",
                "browser_session_id": f"browser-{platform}-acct",
                "label": f"{platform} 样例监控组",
            }
        ]

    def test_round_executor_covers_all_dimensions_silently(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.workers.read_only_scheduler import (
            _execute_period_read_day_snapshot_round,
        )

        calls: list[dict] = []

        async def fake_fetch(**kwargs):
            calls.append(kwargs)
            if kwargs["platform"] == "fliggy" and kwargs["data_kind"] == "ranking":
                return {"status": "not_available"}
            if kwargs["platform"] == "meituan":
                raise RuntimeError("browser cold start failed")
            return {"status": "ok"}

        with patch(
            "app.domain.ebk_period_reads.service.fetch_period_read",
            new=AsyncMock(side_effect=fake_fetch),
        ), patch(
            "app.workers.read_only_scheduler.list_order_account_bindings",
            new=AsyncMock(side_effect=self._single_store_bindings),
        ):
            summary = asyncio.run(_execute_period_read_day_snapshot_round())

        self.assertEqual(len(calls), 6)
        for call in calls:
            self.assertEqual(call["period"], "day")
            self.assertEqual(call["source"], "day_end_snapshot")
        self.assertEqual(summary["ok"], 3)
        self.assertEqual(summary["not_available"], 1)
        self.assertEqual(summary["error"], 2)  # meituan 两维度异常被吞并计数
        self.assertTrue(any("meituan" in e for e in summary["errors"]), summary["errors"])

    def test_next_run_delay_parses_configured_time(self) -> None:
        import datetime as dt

        from app.workers.read_only_scheduler import (
            _seconds_until_next_period_day_snapshot,
        )

        with patch(
            "app.workers.read_only_scheduler.get_settings"
        ) as fake_settings:
            fake_settings.return_value.period_read_day_snapshot_time = "09:30"
            fake_settings.return_value.period_read_day_snapshot_enabled = True
            before = dt.datetime.now()
            delay = _seconds_until_next_period_day_snapshot()
        target = before + dt.timedelta(seconds=delay)
        # before 与函数内部 now 之间时钟可能前进一格（毫秒级），容差 1 秒比对。
        expected = before.replace(hour=9, minute=30, second=0, microsecond=0)
        if expected <= before:
            expected += dt.timedelta(days=1)
        self.assertLessEqual(abs((target - expected).total_seconds()), 1.0)
        self.assertGreaterEqual(delay, 0)
        self.assertLessEqual(delay, 24 * 3600)


class PeriodDaySnapshotRunLogTests(unittest.TestCase):
    """昨日快照轮次运行记录：轮次 summary 聚合 + 落库 + 只读 API。"""

    def test_executor_summary_contains_platform_results(self) -> None:
        from app.workers.read_only_scheduler import (
            _execute_period_read_day_snapshot_round,
        )

        async def fake_fetch(**kwargs):
            if kwargs["platform"] == "fliggy" and kwargs["data_kind"] == "ranking":
                return {"status": "not_available"}
            if kwargs["platform"] == "meituan":
                return {"status": "error"}
            return {"status": "ok"}

        with patch(
            "app.domain.ebk_period_reads.service.fetch_period_read",
            new=AsyncMock(side_effect=fake_fetch),
        ), patch(
            "app.workers.read_only_scheduler.list_order_account_bindings",
            new=AsyncMock(
                side_effect=PeriodDaySnapshotSchedulerTests._single_store_bindings
            ),
        ):
            summary = asyncio.run(_execute_period_read_day_snapshot_round())

        results = summary["platform_results"]
        label = "ctrip 样例监控组"
        self.assertEqual(
            results["ctrip"],
            {"status": "success", "detail": f"{label}：经营成功·排名成功"},
        )
        self.assertEqual(
            results["meituan"],
            {"status": "failed", "detail": "meituan 样例监控组：经营失败·排名失败"},
        )
        self.assertEqual(
            results["fliggy"],
            {"status": "partial", "detail": "fliggy 样例监控组：经营成功·排名无数据"},
        )

    def test_run_record_status_mapping(self) -> None:
        import datetime as dt

        from app.workers.read_only_scheduler import _period_day_snapshot_run_record

        started = dt.datetime(2026, 9, 8, 9, 30, 0)
        base = {"started_at": started, "platform_results": {}, "errors": []}
        self.assertEqual(
            _period_day_snapshot_run_record({**base, "ok": 6, "total": 6})["status"],
            "success",
        )
        record = _period_day_snapshot_run_record(
            {
                **base,
                "ok": 4,
                "total": 6,
                "not_available": 1,
                "error": 1,
                "errors": ["meituan/ranking: error"],
            }
        )
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["ok_count"], 4)
        self.assertEqual(record["error_summary"], "meituan/ranking: error")
        self.assertEqual(record["triggered_at"], started)
        self.assertEqual(
            _period_day_snapshot_run_record({**base, "ok": 0, "error": 6})["status"], "failed"
        )

    def test_repository_and_api_roundtrip(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                import datetime as dt

                from app.domain.ebk_period_reads import repository

                await repository.append_day_snapshot_run(
                    status="partial",
                    platform_results={
                        "ctrip": {"status": "success", "detail": "经营成功·排名成功"},
                        "meituan": {"status": "failed", "detail": "经营失败·排名失败"},
                    },
                    ok_count=4,
                    not_available_count=0,
                    error_count=2,
                    error_summary="meituan/business: error",
                    triggered_at=dt.datetime(2026, 9, 8, 9, 30, 0),
                )
                runs = await repository.list_day_snapshot_runs(limit=10)
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["status"], "partial")
                self.assertEqual(runs[0]["ok_count"], 4)
                self.assertEqual(runs[0]["platform_results"]["ctrip"]["detail"], "经营成功·排名成功")

                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                got = client.get("/api/v1/ebk-period-reads/day-snapshot/runs", params={"limit": 5})
                self.assertEqual(got.status_code, 200)
                body = got.json()
                self.assertEqual(len(body["runs"]), 1)
                self.assertEqual(body["runs"][0]["status"], "partial")
                self.assertEqual(body["runs"][0]["triggered_at"], "2026-09-08 09:30:00")
                # 路由层 ge=1 拒绝非法 limit（422）；仓储层钳制为兜底。
                clamped = client.get("/api/v1/ebk-period-reads/day-snapshot/runs", params={"limit": 0})
                self.assertEqual(clamped.status_code, 422)

            asyncio.run(scenario())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "historical-queries.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb):
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


if __name__ == "__main__":
    unittest.main()
