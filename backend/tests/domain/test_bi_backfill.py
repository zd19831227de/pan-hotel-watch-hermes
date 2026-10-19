"""bi_daily 历史缺数浏览器回填测试：单日区间现读 → 按日快照 → ingest 进事实表。

纯函数（区间校验/缺日规划/携程离店组标签归一）+ 注入假 fetch_fn 的集成路径
（只读缺日、human_required 中止该平台、快照与事实落库口径）。
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime

from backend.tests._bi_isolation import tolerant_isolated_db

from app.domain.bi_daily import models as _bi_daily_models  # noqa: F401
from app.domain.ebk_period_reads import models as _period_models  # noqa: F401

NOW = datetime(2026, 10, 8, 15, 0, 0)


def _ctrip_custom_sections() -> dict:
    return {
        "预订": {"间夜量": {"value": "120"}, "销售额": {"value": "30000"}},
        "在店": {"间夜量": {"value": "60"}, "出租率": {"value": "80%"}},
        "离店": {
            "间夜量": {"value": "88"},
            "销售额": {"value": "23559.62"},
            "平均卖价": {"value": "267.72"},
        },
    }


class BiBackfillPureTests(unittest.TestCase):
    def test_validate_rejects_future_today_overspan_and_reversed_range(self) -> None:
        from app.domain.bi_daily.backfill import validate_backfill_range

        today = NOW.date()
        self.assertIsNone(validate_backfill_range(date(2026, 9, 1), date(2026, 9, 30), now=NOW))
        # 含今日/未来：平台页当日数据未生成，不能回填。
        self.assertIsNotNone(validate_backfill_range(date(2026, 10, 1), today, now=NOW))
        self.assertIsNotNone(validate_backfill_range(date(2026, 10, 9), date(2026, 10, 10), now=NOW))
        self.assertIsNotNone(validate_backfill_range(date(2026, 9, 30), date(2026, 9, 1), now=NOW))
        self.assertIsNotNone(validate_backfill_range(date(2026, 1, 1), date(2026, 9, 30), now=NOW))

    def test_plan_missing_dates_skips_existing_and_orders_ascending(self) -> None:
        from app.domain.bi_daily.backfill import plan_missing_dates

        missing = plan_missing_dates(
            date(2026, 9, 28), date(2026, 10, 2), existing={date(2026, 9, 29)}
        )
        self.assertEqual(
            missing,
            [date(2026, 9, 28), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)],
        )

    def test_normalize_ctrip_maps_departure_group_to_ingest_labels(self) -> None:
        from app.domain.bi_daily.backfill import normalize_custom_range_sections
        from app.domain.bi_daily.ingest import extract_day_business_metrics

        normalized = normalize_custom_range_sections(_ctrip_custom_sections(), "ctrip")
        metrics = extract_day_business_metrics({"sections": normalized}, "ctrip")
        self.assertEqual(metrics["booked_room_nights"], 88.0)
        self.assertEqual(metrics["room_revenue"], 23559.62)
        self.assertEqual(metrics["adr"], 267.72)
        # 预订组的「间夜量」不得串进离店口径。
        self.assertNotIn("预订", normalized)

    def test_parse_metric_number_handles_wan_units(self) -> None:
        # 携程预订销售页自定义区间的离店销售额是「7.00万」格式（生产实证）。
        from app.domain.bi_daily.ingest import _parse_metric_number

        self.assertEqual(_parse_metric_number("7.00万"), 70000.0)
        self.assertAlmostEqual(_parse_metric_number("10.38万"), 103800.0)
        self.assertEqual(_parse_metric_number("1.2万元"), 12000.0)
        self.assertEqual(_parse_metric_number("6.75万"), 67500.0)
        self.assertEqual(_parse_metric_number("23559.62"), 23559.62)
        self.assertIsNone(_parse_metric_number("43.02%"))
        self.assertIsNone(_parse_metric_number("暂无"))

    def test_normalize_passes_meituan_fliggy_sections_through(self) -> None:
        from app.domain.bi_daily.backfill import normalize_custom_range_sections

        meituan = {"经营表现": {"入住间夜": {"value": "5"}, "入住金额": {"value": "1200"}}}
        self.assertEqual(normalize_custom_range_sections(meituan, "meituan"), meituan)
        fliggy = {"在店数据": {"在店间夜": {"value": "7"}}}
        self.assertEqual(normalize_custom_range_sections(fliggy, "fliggy"), fliggy)


class BiBackfillExecutionTests(unittest.TestCase):
    def test_execute_backfill_reads_only_missing_dates_and_writes_facts(self) -> None:
        from app.domain.bi_daily.backfill import execute_backfill, reset_run_state_for_tests
        from app.domain.bi_daily.models import BookingPositionFact
        from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot
        from app.db.session import session_scope

        with tolerant_isolated_db():
            asyncio.run(
                _seed_existing_fact(shop_key="shop_t", platform="ctrip", stay=date(2026, 9, 30))
            )
            calls: list[tuple[str, str]] = []

            async def fake_fetch(**kwargs):
                calls.append((kwargs["platform"], kwargs["date_from"]))
                if kwargs["platform"] == "ctrip":
                    sections = _ctrip_custom_sections()
                else:
                    sections = {"经营表现": {"入住间夜": {"value": "5"}, "入住金额": {"value": "1200"}, "平均房价": {"value": "240"}}}
                return {
                    "status": "ok",
                    "platform": kwargs["platform"],
                    "caliber": f"回填·{kwargs['date_from']}",
                    "summary": "ok",
                    "sections": sections,
                }

            reset_run_state_for_tests()
            result = asyncio.run(
                execute_backfill(
                    shop_key="shop_t",
                    date_from=date(2026, 9, 29),
                    date_to=date(2026, 9, 30),
                    bindings=[
                        {"shop_key": "shop_t", "platform": "ctrip", "platform_account_id": "acc-c", "browser_session_id": "sess-c"},
                        {"shop_key": "shop_t", "platform": "meituan", "platform_account_id": "acc-m", "browser_session_id": "sess-m"},
                    ],
                    fetch_fn=fake_fetch,
                    now=NOW,
                )
            )

            # 9/30 ctrip 已有事实：只补 9/29；美团两天都缺。
            self.assertEqual(
                sorted(calls),
                [("ctrip", "2026-09-29"), ("meituan", "2026-09-29"), ("meituan", "2026-09-30")],
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["facts_written"] >= 2)

            async def _inspect():
                async with session_scope() as session:
                    snapshots = (
                        await session.execute(
                            __import__("sqlalchemy").select(EbkPeriodReadSnapshot)
                        )
                    ).scalars().all()
                    facts = (
                        await session.execute(
                            __import__("sqlalchemy").select(BookingPositionFact)
                        )
                    ).scalars().all()
                    return snapshots, facts

            snapshots, facts = asyncio.run(_inspect())
            backfill_rows = [row for row in snapshots if row.source == "bi_backfill"]
            self.assertEqual(len(backfill_rows), 3)
            for row in backfill_rows:
                self.assertEqual(row.period, "day")
                self.assertEqual(row.status, "ok")
                self.assertIsNotNone(row.business_date)
            ctrip_backfill = [row for row in backfill_rows if row.platform == "ctrip"]
            self.assertEqual(ctrip_backfill[0].business_date, date(2026, 9, 29))
            # 携程离店组标签归一后进事实。
            ctrip_facts = [fact for fact in facts if fact.platform == "ctrip"]
            self.assertEqual(len(ctrip_facts), 2)
            backfilled = [fact for fact in ctrip_facts if fact.stay_date == date(2026, 9, 29)]
            self.assertEqual(backfilled[0].booked_room_nights, 88)
            self.assertAlmostEqual(backfilled[0].room_revenue, 23559.62)

    def test_execute_backfill_aborts_platform_on_human_required_but_continues_others(self) -> None:
        from app.domain.bi_daily.backfill import execute_backfill, reset_run_state_for_tests

        with tolerant_isolated_db():
            calls: list[tuple[str, str]] = []

            async def fake_fetch(**kwargs):
                calls.append((kwargs["platform"], kwargs["date_from"]))
                if kwargs["platform"] == "ctrip":
                    return {
                        "status": "human_required",
                        "platform": "ctrip",
                        "reason": "需要人工完成登录或安全验证后重试。",
                    }
                return {
                    "status": "ok",
                    "platform": "meituan",
                    "sections": {"经营表现": {"入住间夜": {"value": "5"}}},
                }

            reset_run_state_for_tests()
            result = asyncio.run(
                execute_backfill(
                    shop_key="shop_t",
                    date_from=date(2026, 9, 28),
                    date_to=date(2026, 9, 30),
                    bindings=[
                        {"shop_key": "shop_t", "platform": "ctrip", "platform_account_id": "acc-c", "browser_session_id": "sess-c"},
                        {"shop_key": "shop_t", "platform": "meituan", "platform_account_id": "acc-m", "browser_session_id": "sess-m"},
                    ],
                    fetch_fn=fake_fetch,
                    now=NOW,
                )
            )

            # 携程首日 human_required：同平台剩余日期不再拉浏览器；美团继续。
            self.assertEqual(calls[0], ("ctrip", "2026-09-28"))
            self.assertEqual([call for call in calls if call[0] == "ctrip"], [("ctrip", "2026-09-28")])
            self.assertEqual(len([call for call in calls if call[0] == "meituan"]), 3)
            self.assertEqual(result["status"], "partial")
            ctrip_state = result["platforms"]["ctrip"]
            self.assertEqual(ctrip_state["aborted_reason_status"], "human_required")

    def test_resolve_shop_bindings_carries_browser_session_id(self) -> None:
        # 生产事故回归（2026-10-08）：binding 缺 browser_session_id 时
        # acquire_order_page_for_binding 取键 KeyError，回填全平台失败。
        from unittest import mock

        from app.domain.bi_daily.backfill import resolve_shop_backfill_bindings

        async def fake_resolve(shop_key: str, platform: str) -> dict:
            if platform == "ctrip":
                return {
                    "status": "resolved",
                    "platform_account_id": "acc-c",
                    "browser_session_id": "sess-c",
                }
            return {"status": "binding_required", "summary": "未绑定"}

        with mock.patch(
            "app.domain.shops.resolver.resolve_platform_binding",
            side_effect=fake_resolve,
        ):
            bindings, issues = __import__("asyncio").run(
                resolve_shop_backfill_bindings("shop_t")
            )
        self.assertEqual(bindings, [
            {
                "shop_key": "shop_t",
                "platform": "ctrip",
                "platform_account_id": "acc-c",
                "browser_session_id": "sess-c",
            }
        ])
        self.assertEqual(len(issues), 2)

    def test_run_guard_rejects_second_start_while_running(self) -> None:
        from app.domain.bi_daily.backfill import (
            begin_backfill_run,
            finish_run_state_for_tests,
        )

        first = begin_backfill_run("shop_t", date(2026, 9, 1), date(2026, 9, 2))
        self.assertTrue(first)
        second = begin_backfill_run("shop_t", date(2026, 9, 3), date(2026, 9, 4))
        self.assertFalse(second)
        finish_run_state_for_tests()


async def _seed_existing_fact(*, shop_key: str, platform: str, stay: date) -> None:
    from app.db.session import ensure_db_ready
    from app.domain.bi_daily.models import BookingPositionFact
    from app.db.session import session_scope

    await ensure_db_ready()
    async with session_scope() as session:
        session.add(
            BookingPositionFact(
                shop_key=shop_key,
                platform=platform,
                stay_date=stay,
                weekday=stay.isoweekday(),
                as_of_time=NOW,
                source="day_end_snapshot",
                is_trusted=True,
                source_row_ref=f"seed:{platform}:{stay.isoformat()}",
            )
        )


if __name__ == "__main__":
    unittest.main()
