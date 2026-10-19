"""bi_daily API 测试：只读报表、覆盖率、导入（multipart）、导出、映射查询。"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import unittest
from datetime import date, datetime

from fastapi.testclient import TestClient

from backend.tests._bi_isolation import tolerant_isolated_db

from app.domain.bi_daily import models as _bi_daily_models  # noqa: F401

NOW = datetime(2026, 10, 8, 9, 40, 0)

_CSV = (
    "入住日期,房型,预算间夜,预算收入,预测间夜\n"
    "2026-10-07,,50,12000,48\n"
).encode("utf-8-sig")


def _build_client() -> TestClient:
    app_module = importlib.reload(importlib.import_module("app.main"))
    return TestClient(app_module.create_app())


class BiDailyApiTest(unittest.TestCase):
    def test_daily_report_coverage_import_export_and_mapping(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario() -> None:
                await db_session.ensure_db_ready()
                from app.domain.bi_daily.models import BookingPositionFact
                from app.domain.shops.models import Shop

                async with db_session.SessionLocal() as session:
                    session.add(Shop(shop_key="shop_bi", name="BI测试店"))
                    session.add(
                        BookingPositionFact(
                            shop_key="shop_bi",
                            platform="ctrip",
                            stay_date=date(2026, 10, 7),
                            weekday=3,
                            as_of_time=NOW,
                            booked_room_nights=88,
                            room_revenue=23559.62,
                            adr=267.72,
                            source="ebk_period_day",
                            source_row_ref="api-t1",
                        )
                    )
                    await session.commit()

                client = _build_client()

                # 1) 日报：14 天骨架 + 表现区。
                got = client.get("/api/v1/bi-daily/daily-report", params={"shop_key": "shop_bi"})
                self.assertEqual(got.status_code, 200)
                body = got.json()
                self.assertEqual(len(body["forward"]), 14)
                self.assertEqual(body["forward"][0]["own"]["booked_room_nights"]["reason"], "no_on_the_books_source")
                self.assertEqual(body["performance"][0]["totals"]["booked_room_nights"], 88)
                self.assertIn("quality", body)

                # 未知店铺：404，不返回伪报表。
                missing = client.get("/api/v1/bi-daily/daily-report", params={"shop_key": "shop_none"})
                self.assertEqual(missing.status_code, 404)

                # 2) 覆盖率：未采集时给出明确提示。
                coverage = client.get("/api/v1/bi-daily/coverage")
                self.assertEqual(coverage.status_code, 200)
                self.assertEqual(coverage.json()["status"], "never_collected")

                # 3) 手动采集（operator 权限端点）。
                collect = client.post("/api/v1/bi-daily/collect")
                self.assertEqual(collect.status_code, 200)
                self.assertIn("status", collect.json())

                coverage2 = client.get("/api/v1/bi-daily/coverage", params={"shop_key": "shop_bi"})
                self.assertEqual(coverage2.status_code, 200)
                payload = coverage2.json()
                self.assertEqual(payload["status"], "ok")
                dims = payload["coverage"]["dimensions"]
                self.assertEqual(dims["own_on_the_books"]["status"], "no_source")

                # 4) 预算导入（multipart）+ 版本列表。
                imported = client.post(
                    "/api/v1/bi-daily/budget-forecast/import",
                    data={
                        "shop_key": "shop_bi",
                        "forecast_version": "v1",
                        "valid_from": "2026-10-01",
                        "valid_to": "2026-10-31",
                    },
                    files={"file": ("budget.csv", _CSV, "text/csv")},
                )
                self.assertEqual(imported.status_code, 200)
                self.assertEqual(imported.json()["status"], "success")
                self.assertEqual(imported.json()["rows_valid"], 1)

                versions = client.get(
                    "/api/v1/bi-daily/budget-forecast/versions", params={"shop_key": "shop_bi"}
                )
                self.assertEqual(versions.status_code, 200)
                self.assertEqual(len(versions.json()["versions"]), 1)
                self.assertEqual(versions.json()["versions"][0]["row_count"], 1)

                # 致命结构问题（缺入住日期列）→ 422。
                bad = client.post(
                    "/api/v1/bi-daily/budget-forecast/import",
                    data={"shop_key": "shop_bi", "forecast_version": "v2"},
                    files={"file": ("bad.csv", "房型,预算间夜\n房,1\n".encode("utf-8"), "text/csv")},
                )
                self.assertEqual(bad.status_code, 422)

                # 5) 导出：CSV 与 XLSX。
                csv_resp = client.get(
                    "/api/v1/bi-daily/export/daily-report",
                    params={"shop_key": "shop_bi", "format": "csv"},
                )
                self.assertEqual(csv_resp.status_code, 200)
                self.assertIn("text/csv", csv_resp.headers["content-type"])
                self.assertIn("入住日期", csv_resp.content.decode("utf-8-sig"))

                xlsx_resp = client.get(
                    "/api/v1/bi-daily/export/daily-report",
                    params={"shop_key": "shop_bi", "format": "xlsx"},
                )
                self.assertEqual(xlsx_resp.status_code, 200)
                self.assertIn("spreadsheetml", xlsx_resp.headers["content-type"])

                # 6) 去年同期映射查询。
                mapping = client.get(
                    "/api/v1/bi-daily/weekday-mapping",
                    params={"date_from": "2026-10-07", "date_to": "2026-10-08"},
                )
                self.assertEqual(mapping.status_code, 200)
                rows = mapping.json()["rows"]
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["current_stay_date"], "2026-10-07")
                self.assertEqual(rows[0]["prior_year_same_weekday_stay_date"], "2025-10-08")
                self.assertEqual(rows[0]["matching_rule"], "minus_364")

            asyncio.run(scenario())

    def test_backfill_actuals_validation_and_status(self):
        with tolerant_isolated_db():
            from app.db import session as db_session

            async def scenario() -> None:
                from datetime import timedelta

                await db_session.ensure_db_ready()
                from app.domain.shops.models import Shop

                async with db_session.SessionLocal() as session:
                    session.add(Shop(shop_key="shop_bi", name="BI测试店"))
                    await session.commit()

                from app.domain.bi_daily import backfill as backfill_module

                backfill_module.reset_run_state_for_tests()
                client = _build_client()
                today = date.today()

                # 含今日 → 422（平台页当日数据未生成）。
                future = client.post(
                    "/api/v1/bi-daily/backfill-actuals",
                    json={
                        "shop_key": "shop_bi",
                        "date_from": (today - timedelta(days=2)).isoformat(),
                        "date_to": today.isoformat(),
                    },
                )
                self.assertEqual(future.status_code, 422)

                # 未知店铺 → 404。
                missing = client.post(
                    "/api/v1/bi-daily/backfill-actuals",
                    json={
                        "shop_key": "shop_none",
                        "date_from": "2026-09-01",
                        "date_to": "2026-09-02",
                    },
                )
                self.assertEqual(missing.status_code, 404)

                # 合法区间启动：返回运行状态快照（后台任务无账号绑定会很快落 failed）。
                started = client.post(
                    "/api/v1/bi-daily/backfill-actuals",
                    json={
                        "shop_key": "shop_bi",
                        "date_from": (today - timedelta(days=9)).isoformat(),
                        "date_to": (today - timedelta(days=2)).isoformat(),
                    },
                )
                self.assertEqual(started.status_code, 200)
                body = started.json()
                self.assertIn(body["status"], {"running", "failed", "partial"})
                self.assertEqual(body["shop_key"], "shop_bi")

                status = client.get("/api/v1/bi-daily/backfill-actuals/status")
                self.assertEqual(status.status_code, 200)
                self.assertIn("status", status.json())

                task = backfill_module._RUN_STATE.get("_task")
                if task is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(asyncio.shield(task), timeout=10)
                backfill_module.reset_run_state_for_tests()

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
