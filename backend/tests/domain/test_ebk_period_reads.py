from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path


class EbkPeriodReadRepositoryTests(unittest.TestCase):
    def test_append_and_fresh_only_latest(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                assert db_session is not None
                from app.domain.ebk_period_reads import repository

                snapshot = await repository.append_period_read_snapshot(
                    platform="ctrip",
                    data_kind="business",
                    period="week",
                    status="ok",
                    caliber="预订销售·按周（自然周）",
                    summary="本周预订/在店/离店指标已读取",
                    payload={"sections": {"预订": {"预订订单数": "359"}}},
                )
                self.assertIsNotNone(snapshot["id"])
                self.assertEqual(snapshot["status"], "ok")
                self.assertEqual(snapshot["caliber"], "预订销售·按周（自然周）")
                self.assertEqual(
                    snapshot["payload"]["sections"]["预订"]["预订订单数"], "359"
                )
                self.assertIsNotNone(snapshot["captured_at"])

                # 同维度多次抓取 append-only，查询取最新。
                await repository.append_period_read_snapshot(
                    platform="ctrip",
                    data_kind="business",
                    period="week",
                    status="ok",
                    payload={"sections": {"预订": {"预订订单数": "400"}}},
                )
                latest = await repository.get_latest_period_read(
                    platform="ctrip", data_kind="business", period="week"
                )
                self.assertEqual(latest["payload"]["sections"]["预订"]["预订订单数"], "400")

                # 其他维度互不影响。
                other = await repository.get_latest_period_read(
                    platform="ctrip", data_kind="ranking", period="week"
                )
                self.assertIsNone(other)

                # 「不是当天」判定为过期：默认 fresh_only 不返回跨天快照。
                from app.domain.ebk_period_reads.models import local_now

                await repository.append_period_read_snapshot(
                    platform="meituan",
                    data_kind="business",
                    period="month",
                    status="ok",
                    payload={},
                    captured_at=local_now() - timedelta(days=1),
                )
                stale = await repository.get_latest_period_read(
                    platform="meituan", data_kind="business", period="month"
                )
                self.assertIsNone(stale)
                any_latest = await repository.get_latest_period_read(
                    platform="meituan",
                    data_kind="business",
                    period="month",
                    fresh_only=False,
                )
                self.assertIsNotNone(any_latest)

            asyncio.run(scenario())


class EbkPeriodReadBusinessDateTests(unittest.TestCase):
    def test_day_business_date_drives_range_query_not_captured_minus_one(self) -> None:
        """A4：day 快照带页面真实数据日期时按它过滤，前前日数据不再错挂昨日。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository

                # 09-10 早 8 点现读，美团页面仍是 09-08 的前前日数据。
                await repository.append_period_read_snapshot(
                    platform="meituan",
                    data_kind="business",
                    period="day",
                    status="ok",
                    caliber="交易分析·按日（前日数据）",
                    payload={"data_period_label": "2026/09/08"},
                    captured_at=datetime(2026, 9, 10, 8, 0),
                    business_date=date(2026, 9, 8),
                )

                # 问「昨天经营数据」（业务日 09-09）：前前日 09-08 不应命中。
                yesterday = await repository.list_period_reads_by_captured_range(
                    platform="meituan", data_kind="business",
                    date_from="2026-09-09", date_to="2026-09-09",
                )
                self.assertEqual(yesterday, [])

                # 问「09-08 经营数据」：命中。
                target = await repository.list_period_reads_by_captured_range(
                    platform="meituan", data_kind="business",
                    date_from="2026-09-08", date_to="2026-09-08",
                )
                self.assertEqual(len(target), 1)
                self.assertEqual(target[0]["business_date"], "2026-09-08")

            asyncio.run(scenario())

    def test_legacy_day_snapshot_without_business_date_falls_back_to_captured_minus_one(self) -> None:
        """历史行无 business_date：回落 captured-1，既有口径不变。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository

                await repository.append_period_read_snapshot(
                    platform="ctrip",
                    data_kind="business",
                    period="day",
                    status="ok",
                    payload={},
                    captured_at=datetime(2026, 9, 9, 9, 30),
                )
                rows = await repository.list_period_reads_by_captured_range(
                    platform="ctrip", data_kind="business",
                    date_from="2026-09-08", date_to="2026-09-08",
                )
                self.assertEqual(len(rows), 1)

            asyncio.run(scenario())


class EbkPeriodReadServiceTests(unittest.TestCase):
    def test_unsupported_dimension_returns_not_available_without_browser(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import service

                # 飞猪页面没有月维度同行排名：明确 not_available，不触浏览器。
                result = await service.fetch_period_read(
                    platform="fliggy",
                    data_kind="ranking",
                    period="month",
                    page=_RefusesToBeUsedPage(),
                )
                self.assertEqual(result["status"], "not_available")
                self.assertIn("飞猪", result["reason"])

            asyncio.run(scenario())

    def test_fetch_persists_page_business_date_for_day(self) -> None:
        """service 从 reader 的 data_period_label 提取业务日期落库（A4）。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                async def fake_reader(page, *, period: str) -> dict:
                    return {
                        "status": "ok",
                        "caliber": "交易分析·按日（前日数据）",
                        "summary": "已读取美团按日经营指标",
                        "sections": {"经营表现": {"销售额": {"value": "8600"}}},
                        "data_period_label": "2026/09/08",
                        "evidence": {"selected_control": "按日"},
                    }

                result = await service.fetch_period_read(
                    platform="meituan",
                    data_kind="business",
                    period="day",
                    page=_FakePage(),
                    reader=fake_reader,
                )
                self.assertEqual(result["status"], "ok")
                snapshot_id = result["snapshot"]["id"]
                snapshot = await repository.get_latest_period_read(
                    platform="meituan", data_kind="business", period="day",
                    fresh_only=False,
                )
                self.assertEqual(snapshot["business_date"], "2026-09-08")

            asyncio.run(scenario())

    def test_missing_binding_returns_not_ready(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from unittest.mock import AsyncMock, patch

                from app.domain.ebk_period_reads import service

                with patch(
                    "app.domain.ebk_period_reads.service.resolve_order_account_binding",
                    new=AsyncMock(return_value=None),
                ):
                    result = await service.fetch_period_read(
                        platform="meituan",
                        data_kind="business",
                        period="week",
                    )
                self.assertEqual(result["status"], "not_ready")
                self.assertIn("绑定", result["reason"])

            asyncio.run(scenario())

    def test_reader_success_persists_snapshot(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                async def fake_reader(page, *, period: str) -> dict:
                    return {
                        "status": "ok",
                        "caliber": "预订销售·按周（自然周）",
                        "summary": "已读取携程按周经营指标",
                        "sections": {"预订": {"预订订单数": "359"}},
                        "evidence": {"dataset": "beneficial_data", "selected": "按周"},
                    }

                result = await service.fetch_period_read(
                    platform="ctrip",
                    data_kind="business",
                    period="week",
                    page=_FakePage(),
                    reader=fake_reader,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["snapshot"]["payload"]["sections"]["预订"]["预订订单数"], "359")
                latest = await repository.get_latest_period_read(
                    platform="ctrip", data_kind="business", period="week"
                )
                self.assertIsNotNone(latest)
                self.assertEqual(latest["caliber"], "预订销售·按周（自然周）")

            asyncio.run(scenario())

    def test_reader_human_required_not_persisted(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                async def human_reader(page, *, period: str) -> dict:
                    return {
                        "status": "human_required",
                        "summary": "需要人工登录后才能读取。",
                        "evidence": {},
                    }

                result = await service.fetch_period_read(
                    platform="meituan",
                    data_kind="ranking",
                    period="week",
                    page=_FakePage(),
                    reader=human_reader,
                )
                self.assertEqual(result["status"], "human_required")
                self.assertIsNone(
                    await repository.get_latest_period_read(
                        platform="meituan", data_kind="ranking", period="week"
                    )
                )

            asyncio.run(scenario())

    def test_reader_crash_returns_error_not_exception(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import service

                async def crash_reader(page, *, period: str) -> dict:
                    raise RuntimeError("net::ERR_TIMED_OUT")

                result = await service.fetch_period_read(
                    platform="fliggy",
                    data_kind="business",
                    period="week",
                    page=_FakePage(),
                    reader=crash_reader,
                )
                self.assertEqual(result["status"], "error")
                self.assertIn("ERR_TIMED_OUT", result["reason"])

            asyncio.run(scenario())

    def test_pyramid_custom_range_unsupported_platform_intercepted(self) -> None:
        """金字塔仅携程有报表：美团/飞猪在解析浏览器前即拦截。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from unittest.mock import AsyncMock, patch

                from app.domain.ebk_period_reads import service

                resolve = AsyncMock(side_effect=AssertionError("must not resolve binding"))
                with patch(
                    "app.domain.ebk_period_reads.service.resolve_order_account_binding",
                    new=resolve,
                ):
                    for platform in ("meituan", "fliggy"):
                        result = await service.fetch_period_read(
                            platform=platform,
                            data_kind="pyramid",
                            period="week",
                            date_from="2026-09-07",
                            date_to="2026-09-13",
                            page=_RefusesToBeUsedPage(),
                        )
                        self.assertEqual(result["status"], "not_available", platform)
                        self.assertIn("仅携程", result["reason"], platform)

            asyncio.run(scenario())

    def test_pyramid_custom_range_reader_receives_dates(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import service

                received: dict = {}

                async def pyramid_reader(
                    page, *, period: str, date_from: str | None = None, date_to: str | None = None
                ) -> dict:
                    received.update(period=period, date_from=date_from, date_to=date_to)
                    return {
                        "status": "ok",
                        "caliber": "金字塔数据报告·自定义 2026-09-07~2026-09-13",
                        "summary": "已读取金字塔自定义区间效果数据。",
                        "sections": {"金字塔": {"groups": []}},
                        "evidence": {"selected_control": "自定义"},
                    }

                result = await service.fetch_period_read(
                    platform="ctrip",
                    data_kind="pyramid",
                    period="week",
                    date_from="2026-09-07",
                    date_to="2026-09-13",
                    page=_FakePage(),
                    reader=pyramid_reader,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(
                    received,
                    {"period": "week", "date_from": "2026-09-07", "date_to": "2026-09-13"},
                )

            asyncio.run(scenario())

    def test_custom_range_unsupported_combo_short_circuits_before_browser(self) -> None:
        """美团同行分析/飞猪页面无自定义区间控件：明确 not_available，不解析绑定不触浏览器。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from unittest.mock import AsyncMock, patch

                from app.domain.ebk_period_reads import service

                resolve = AsyncMock(side_effect=AssertionError("must not resolve binding"))
                with patch(
                    "app.domain.ebk_period_reads.service.resolve_order_account_binding",
                    new=resolve,
                ):
                    for platform, reason_needle in (
                        ("meituan", "美团同行分析"),
                        ("fliggy", "飞猪"),
                    ):
                        result = await service.fetch_period_read(
                            platform=platform,
                            data_kind="ranking",
                            period="week",
                            date_from="2026-09-07",
                            date_to="2026-09-13",
                            page=_RefusesToBeUsedPage(),
                        )
                        self.assertEqual(result["status"], "not_available", platform)
                        self.assertIn(reason_needle, result["reason"], platform)

            asyncio.run(scenario())

    def test_custom_range_supported_reader_receives_dates_and_nothing_persisted(self) -> None:
        """携程经营自定义区间：日期透传给 reader；结果不落当日快照（区间读不污染 fresh_only 口径）。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                received: dict = {}

                async def range_reader(
                    page, *, period: str, date_from: str | None = None, date_to: str | None = None
                ) -> dict:
                    received.update(period=period, date_from=date_from, date_to=date_to)
                    return {
                        "status": "ok",
                        "caliber": "预订销售·自定义 2026-09-07~2026-09-13（较去年同期）",
                        "summary": "已读取携程自定义区间经营指标。",
                        "sections": {"预订": {"预订订单数": {"value": "359"}}},
                        "evidence": {"selected_control": "自定义"},
                    }

                result = await service.fetch_period_read(
                    platform="ctrip",
                    data_kind="business",
                    period="week",
                    date_from="2026-09-07",
                    date_to="2026-09-13",
                    page=_FakePage(),
                    reader=range_reader,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(
                    result.get("date_range"), {"from": "2026-09-07", "to": "2026-09-13"}
                )
                self.assertEqual(
                    received,
                    {"period": "week", "date_from": "2026-09-07", "date_to": "2026-09-13"},
                )
                self.assertIsNone(
                    await repository.get_latest_period_read(
                        platform="ctrip", data_kind="business", period="week"
                    )
                )

            asyncio.run(scenario())

    def test_fliggy_business_custom_range_reader_receives_dates_and_nothing_persisted(self) -> None:
        """飞猪经营自定义区间（2026-09-17 实测经营数据页有 ant-picker-range 控件）：
        日期透传 reader，结果不落当日快照。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                received: dict = {}

                async def range_reader(
                    page, *, period: str, date_from: str | None = None, date_to: str | None = None
                ) -> dict:
                    received.update(period=period, date_from=date_from, date_to=date_to)
                    return {
                        "status": "ok",
                        "caliber": "经营数据·自定义 2026-09-01~2026-09-10",
                        "summary": "已读取飞猪自定义区间经营指标。",
                        "sections": {"在店数据": {"在店间夜": {"value": "27间夜"}}},
                        "evidence": {
                            "selected_control": "自定义",
                            "range_inputs": ["2026-09-01", "2026-09-10"],
                        },
                    }

                result = await service.fetch_period_read(
                    platform="fliggy",
                    data_kind="business",
                    period="week",
                    date_from="2026-09-01",
                    date_to="2026-09-10",
                    page=_FakePage(),
                    reader=range_reader,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(
                    result.get("date_range"), {"from": "2026-09-01", "to": "2026-09-10"}
                )
                self.assertEqual(
                    received,
                    {"period": "week", "date_from": "2026-09-01", "date_to": "2026-09-10"},
                )
                self.assertIsNone(
                    await repository.get_latest_period_read(
                        platform="fliggy", data_kind="business", period="week"
                    )
                )

            asyncio.run(scenario())

    def test_custom_range_rejects_invalid_windows(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from datetime import date, timedelta

                from app.domain.ebk_period_reads import service

                today = date.today()
                yesterday = (today - timedelta(days=1)).isoformat()
                invalid_windows = (
                    ("2026-09-13", "2026-09-07", "date_from 不能晚于"),
                    (yesterday, today.isoformat(), "不能包含今日"),
                    (today.isoformat(), today.isoformat(), "不能包含今日"),
                    ("2026-08-01", "2026-09-15", "最多31天"),
                )
                for date_from, date_to, needle in invalid_windows:
                    result = await service.fetch_period_read(
                        platform="ctrip",
                        data_kind="business",
                        period="week",
                        date_from=date_from,
                        date_to=date_to,
                        page=_RefusesToBeUsedPage(),
                    )
                    self.assertEqual(result["status"], "invalid_request", (date_from, date_to))
                    self.assertIn(needle, result["reason"], (date_from, date_to))

            asyncio.run(scenario())

    def test_reader_not_available_is_not_persisted(self) -> None:
        # 瞬时态（按钮没点上/渲染失败）不落库：当天下一次问答按过期重读，
        # 避免一次页面抖动锁住整日响应。与退单 not_ready 不落库一致。
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                async def not_available_reader(page, *, period: str) -> dict:
                    return {
                        "status": "not_available",
                        "caliber": "交易分析·按周（自然周，较上期/较去年）",
                        "summary": "美团交易分析页「按周」按钮未切换到选中态。",
                        "sections": {"经营表现": {}},
                        "evidence": {"selected_state": False},
                    }

                result = await service.fetch_period_read(
                    platform="meituan",
                    data_kind="business",
                    period="week",
                    page=_FakePage(),
                    reader=not_available_reader,
                )
                self.assertEqual(result["status"], "not_available")
                self.assertEqual(result["caliber"], "交易分析·按周（自然周，较上期/较去年）")
                # 关键：未落库，下一次 GET 拿不到当日快照（问答端会触发现读）。
                self.assertIsNone(
                    await repository.get_latest_period_read(
                        platform="meituan", data_kind="business", period="week"
                    )
                )

            asyncio.run(scenario())


class EbkPeriodReadApiTests(unittest.TestCase):
    def test_get_returns_snapshot_and_404(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository

                await repository.append_period_read_snapshot(
                    platform="ctrip",
                    data_kind="ranking",
                    period="month",
                    status="ok",
                    caliber="竞争排名·上月",
                    payload={"rankings": {"销售排名_预订订单量排名": "4"}},
                )
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                got = client.get(
                    "/api/v1/ebk-period-reads",
                    params={"platform": "ctrip", "data_kind": "ranking", "period": "month"},
                )
                self.assertEqual(got.status_code, 200)
                body = got.json()
                self.assertEqual(body["snapshot"]["caliber"], "竞争排名·上月")
                self.assertEqual(
                    body["snapshot"]["payload"]["rankings"]["销售排名_预订订单量排名"], "4"
                )

                # 无当日快照 → 404（供问答端识别过期并触发重读）。
                missing = client.get(
                    "/api/v1/ebk-period-reads",
                    params={"platform": "meituan", "data_kind": "business", "period": "week"},
                )
                self.assertEqual(missing.status_code, 404)

                # 不带 platform → 三平台当日快照汇总。
                all_platforms = client.get(
                    "/api/v1/ebk-period-reads",
                    params={"data_kind": "ranking", "period": "month"},
                )
                self.assertEqual(all_platforms.status_code, 200)
                snapshots = all_platforms.json()["snapshots"]
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0]["platform"], "ctrip")

            asyncio.run(scenario())

    def test_post_fetch_unsupported_returns_200_not_available(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                resp = client.post(
                    "/api/v1/ebk-period-reads/fetch",
                    json={
                        "platform": "fliggy",
                        "data_kind": "ranking",
                        "period": "month",
                    },
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["result"]["status"], "not_available")

                # 自定义区间：POST /fetch 接收 date_from/date_to 并透传（无控件的
                # 平台按 200+not_available 契约返回，不触浏览器）。
                ranged = client.post(
                    "/api/v1/ebk-period-reads/fetch",
                    json={
                        "platform": "meituan",
                        "data_kind": "ranking",
                        "period": "week",
                        "date_from": "2026-09-07",
                        "date_to": "2026-09-13",
                    },
                )
                self.assertEqual(ranged.status_code, 200)
                ranged_result = ranged.json()["result"]
                self.assertEqual(ranged_result["status"], "not_available")
                self.assertIn("美团同行分析", ranged_result["reason"])

                # 日期形状非法 → 422。
                bad_date = client.post(
                    "/api/v1/ebk-period-reads/fetch",
                    json={
                        "platform": "ctrip",
                        "data_kind": "business",
                        "period": "week",
                        "date_from": "2026/09/07",
                        "date_to": "2026-09-13",
                    },
                )
                self.assertEqual(bad_date.status_code, 422)

                # 参数非法 → 422。
                bad = client.post(
                    "/api/v1/ebk-period-reads/fetch",
                    json={"platform": "ctrip", "data_kind": "sales", "period": "week"},
                )
                self.assertEqual(bad.status_code, 422)

            asyncio.run(scenario())


class EbkPeriodReadFlowDimensionTests(unittest.TestCase):
    """携程流量数据（flowdata 页）维度：2026-09-16 页面实测补齐（昨天/过去7天/过去30天/自定义）。"""

    def test_flow_dimension_resolves_ctrip_reader_only(self) -> None:
        from app.domain.browser import ebk_period_read_flows as flows
        from app.domain.ebk_period_reads import service

        for period in ("day", "week", "month"):
            self.assertIs(
                service.resolve_period_reader("ctrip", "flow", period),
                flows.read_ctrip_flow_period,
                period,
            )
        self.assertIsNone(service.resolve_period_reader("meituan", "flow", "week"))
        self.assertIsNone(service.resolve_period_reader("fliggy", "flow", "month"))

    def test_flow_custom_range_unsupported_platform_intercepted(self) -> None:
        """流量区间读取仅携程 flowdata 页支持：美团/飞猪在解析浏览器前即拦截。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from unittest.mock import AsyncMock, patch

                from app.domain.ebk_period_reads import service

                resolve = AsyncMock(side_effect=AssertionError("must not resolve binding"))
                with patch(
                    "app.domain.ebk_period_reads.service.resolve_order_account_binding",
                    new=resolve,
                ):
                    for platform in ("meituan", "fliggy"):
                        result = await service.fetch_period_read(
                            platform=platform,
                            data_kind="flow",
                            period="week",
                            date_from="2026-09-01",
                            date_to="2026-09-15",
                            page=_RefusesToBeUsedPage(),
                        )
                        self.assertEqual(result["status"], "not_available", platform)
                        self.assertIn("仅携程", result["reason"], platform)

            asyncio.run(scenario())

    def test_flow_custom_range_reader_receives_dates_and_nothing_persisted(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                received: dict = {}

                async def flow_reader(
                    page, *, period: str, date_from: str | None = None, date_to: str | None = None
                ) -> dict:
                    received.update(period=period, date_from=date_from, date_to=date_to)
                    return {
                        "status": "ok",
                        "caliber": "流量数据·自定义 2026-09-01~2026-09-15",
                        "summary": "已读取携程流量数据自定义区间指标。",
                        "sections": {"流量": {"访客量": {"value": "9100"}}},
                        "evidence": {"selected_control": "自定义"},
                    }

                result = await service.fetch_period_read(
                    platform="ctrip",
                    data_kind="flow",
                    period="month",
                    date_from="2026-09-01",
                    date_to="2026-09-15",
                    page=_FakePage(),
                    reader=flow_reader,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(
                    received,
                    {"period": "month", "date_from": "2026-09-01", "date_to": "2026-09-15"},
                )
                latest = await repository.get_latest_period_read(
                    platform="ctrip", data_kind="flow", period="month", fresh_only=False
                )
                self.assertIsNone(latest, "自定义区间读取不得落库")

            asyncio.run(scenario())

    def test_api_accepts_flow_data_kind(self) -> None:
        """API 层 data_kind 正则须放行 flow（否则工具链 422 卡死整条链路）。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient

                client = TestClient(app_module.create_app())
                resp = client.post(
                    "/api/v1/ebk-period-reads/fetch",
                    json={
                        "platform": "ctrip",
                        "data_kind": "flow",
                        "period": "week",
                        "date_from": "2026-09-01",
                        "date_to": "2026-09-15",
                    },
                )
                self.assertEqual(resp.status_code, 200)
                result = resp.json()["result"]
                self.assertNotEqual(result["status"], "invalid_request")
                self.assertEqual(result["data_kind"], "flow")

            asyncio.run(scenario())


class _FakePage:
    """占位 Page：真实读取逻辑由注入的 reader 替代。"""


class _RefusesToBeUsedPage:
    async def __getattr__(self, name):  # pragma: no cover - 防御：绝不应被触碰
        raise AssertionError("unsupported dimension must not touch the browser page")


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "ebk-period-reads.db"
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
