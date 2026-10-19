"""经营 BI 日报卡测试：卡片结构（无原始 JSON/口语化缺失说明）、推送幂等、定时解析。"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import date, datetime

from backend.tests._bi_isolation import tolerant_isolated_db

from app.domain.bi_daily import models as _bi_daily_models  # noqa: F401
from app.domain.bi_daily.cards import build_bi_daily_card, push_bi_daily_reports

NOW = datetime(2026, 10, 8, 10, 0, 0)


def _sample_report() -> dict:
    return {
        "shop_key": "shop_bi",
        "as_of": NOW.isoformat(),
        "horizon_days": 14,
        "forward": [
            {
                "stay_date": "2026-10-09",
                "weekday": 5,
                "own": {
                    "booked_room_nights": {
                        "value": None,
                        "status": "unavailable",
                        "reason": "no_on_the_books_source",
                    }
                },
                "competitor": {
                    "status": "available",
                    "prices": {"min": 300, "max": 350, "count": 2},
                    "hotels": [
                        {"alias": "本店", "role": "own", "price": 300},
                        {"alias": "竞品A", "role": "competitor", "price": 350},
                    ],
                },
            }
        ]
        + [
            {
                "stay_date": f"2026-10-{day:02d}",
                "weekday": 5,
                "own": {
                    "booked_room_nights": {
                        "value": None,
                        "status": "unavailable",
                        "reason": "no_on_the_books_source",
                    }
                },
                "competitor": {"status": "empty"},
            }
            for day in range(10, 23)
        ],
        "performance": [
            {
                "stay_date": "2026-10-07",
                "weekday": 3,
                "platforms": [],
                "totals": {"booked_room_nights": 88, "room_revenue": 23559.62},
                "prior_year": {
                    "mapped_date": "2025-10-08",
                    "matching_rule": "minus_364",
                    "completion": {"value": 1.1, "status": "available", "reason": None},
                },
                "budget": {
                    "version": "v1",
                    "completion": {"value": 0.88, "status": "available", "reason": None},
                },
                "forecast": {
                    "completion": {"value": None, "status": "unavailable", "reason": "forecast_baseline_missing"},
                },
            }
        ],
        "quality": {
            "coverage": {
                "dimensions": {
                    "competitor_prices": {"status": "fresh"},
                    "budget_forecast": {"status": "imported", "latest_version": "v1"},
                    "own_actuals": {"status": "covered", "latest_business_date": "2026-10-07"},
                }
            }
        },
    }


class BiDailyCardTest(unittest.TestCase):
    def test_card_has_operator_copy_and_no_raw_json(self):
        card = build_bi_daily_card(report=_sample_report(), shop_name="测试店铺")
        self.assertIn("经营 BI 日报 · 测试店铺", card["header"]["title"]["content"])
        self.assertEqual(card["header"]["template"], "green")
        markdown = next(
            element for element in card["elements"] if element.get("tag") == "markdown"
        )["content"]
        self.assertIn("竞品价格盯盘正常更新中", markdown)
        self.assertIn("预算：已导入版本 v1", markdown)
        self.assertIn("携程市场分析页口径", markdown)
        # 表格元素存在（共享表格助手），并含价格带。
        tables = [element for element in card["elements"] if element.get("tag") == "table"]
        self.assertTrue(tables)
        rendered = json.dumps(card, ensure_ascii=False)
        for banned in ("source_row_ref", "run_id", "webhook", "payload_json"):
            self.assertNotIn(banned, rendered)
        self.assertIn("¥300–¥350", rendered)

    def test_card_orange_when_competitor_not_fresh(self):
        report = _sample_report()
        report["quality"]["coverage"]["dimensions"]["competitor_prices"]["status"] = "unbound"
        card = build_bi_daily_card(report=report, shop_name="测试店铺")
        self.assertEqual(card["header"]["template"], "orange")


class BiDailyPushTest(unittest.TestCase):
    def test_push_failure_isolated_per_shop(self):
        """一家店报表构建失败：记录 failed，不阻断其他店，不重复推。"""
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from unittest.mock import patch

                from app.domain.ctrip_ebooking_watch.models import (
                    CtripEbookingWatchGroup,
                )
                from app.domain.shops.models import Shop

                async with db_session.SessionLocal() as session:
                    session.add(Shop(shop_key="shop_ok", name="正常店"))
                    session.add(Shop(shop_key="shop_bad", name="报表炸店"))
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="anchor_ok",
                            name="锚点A",
                            shop_key="shop_ok",
                            feishu_chat_id="oc_ok",
                        )
                    )
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="anchor_bad",
                            name="锚点B",
                            shop_key="shop_bad",
                            feishu_chat_id="oc_bad",
                        )
                    )
                    await session.commit()

                    from app.domain.bi_daily import service as bi_service

                    original_build = bi_service.build_daily_report

                    async def flaky_build(*, session, shop_key, **kwargs):
                        if shop_key == "shop_bad":
                            raise RuntimeError("报表构建模拟失败")
                        return await original_build(
                            session=session, shop_key=shop_key, **kwargs
                        )

                    pushed: list[str] = []

                    async def fake_pusher(chat_id, card, fallback_text):
                        pushed.append(chat_id)
                        return {"status": "ok"}

                    with patch(
                        "app.domain.bi_daily.cards.service.build_daily_report",
                        side_effect=flaky_build,
                    ):
                        results = await push_bi_daily_reports(
                            session=session, now=NOW, pusher=fake_pusher
                        )
                    await session.commit()
                    by_shop = {item["shop_key"]: item["status"] for item in results}
                    self.assertEqual(by_shop["shop_bad"], "failed")
                    self.assertEqual(by_shop["shop_ok"], "sent")
                    self.assertEqual(pushed, ["oc_ok"])
                    # 失败店的记录状态也落为 failed。
                    from app.domain.bi_daily.models import BiDailyCardPush

                    from sqlalchemy import select

                    pushes = (
                        await session.execute(select(BiDailyCardPush))
                    ).scalars().all()
                    statuses = {push.shop_key: push.status for push in pushes}
                    self.assertEqual(statuses["shop_bad"], "failed")
                    self.assertEqual(statuses["shop_ok"], "sent")

            asyncio.run(scenario())

    def test_push_once_per_day_and_skip_without_anchor_chat(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario():
                await db_session.ensure_db_ready()
                from app.domain.ctrip_ebooking_watch.models import (
                    CtripEbookingWatchGroup,
                )
                from app.domain.shops.models import Shop

                async with db_session.SessionLocal() as session:
                    session.add(Shop(shop_key="shop_bi", name="BI测试店"))
                    session.add(Shop(shop_key="shop_noanchor", name="无锚点店"))
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="anchor_bi",
                            name="锚点组",
                            shop_key="shop_bi",
                            feishu_chat_id="oc_test_bi",
                        )
                    )
                    await session.commit()

                    pushed: list[tuple[str, str]] = []

                    async def fake_pusher(chat_id, card, fallback_text):
                        pushed.append((chat_id, card["header"]["title"]["content"]))
                        return {"status": "ok"}

                    results = await push_bi_daily_reports(
                        session=session, now=NOW, pusher=fake_pusher
                    )
                    await session.commit()
                    by_shop = {item["shop_key"]: item["status"] for item in results}
                    self.assertEqual(by_shop["shop_bi"], "sent")
                    self.assertEqual(by_shop["shop_noanchor"], "skipped_no_chat")
                    self.assertEqual(len(pushed), 1)
                    self.assertEqual(pushed[0][0], "oc_test_bi")

                    # 同日重推：幂等跳过，不再调用推送。
                    results2 = await push_bi_daily_reports(
                        session=session, now=NOW, pusher=fake_pusher
                    )
                    await session.commit()
                    by_shop2 = {item["shop_key"]: item["status"] for item in results2}
                    self.assertEqual(by_shop2["shop_bi"], "already_pushed_today")
                    self.assertEqual(len(pushed), 1)

            asyncio.run(scenario())


class BiDailySchedulerTimeTest(unittest.TestCase):
    def test_next_run_at_parses_and_rolls_over(self):
        from app.domain.bi_daily.scheduler import next_run_at

        now = datetime(2026, 10, 8, 11, 0, 0)
        self.assertEqual(
            next_run_at(now, raw_time="09:40", fallback="09:40"),
            datetime(2026, 10, 9, 9, 40, 0),
        )
        self.assertEqual(
            next_run_at(now, raw_time="23:30", fallback="09:40"),
            datetime(2026, 10, 8, 23, 30, 0),
        )
        # 非法配置回退默认。
        self.assertEqual(
            next_run_at(now, raw_time="9点", fallback="09:40"),
            datetime(2026, 10, 9, 9, 40, 0),
        )


if __name__ == "__main__":
    unittest.main()
