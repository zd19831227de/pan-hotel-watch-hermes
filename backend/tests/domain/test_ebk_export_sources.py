from __future__ import annotations

import asyncio
import importlib
import json
import unittest
from datetime import datetime
from unittest import mock

from fastapi.testclient import TestClient

from tests.domain.test_data_center_watch_group_config import (
    _isolated_sqlite_database,
    _reload_repository,
)
from tests.domain.test_ebk_business_metrics import (
    _ctrip_full_snapshot,
    build_sample_snapshots,
)


def _watch_run(
    *,
    captured_at: str,
    source: str,
    actor_id: str,
) -> dict:
    return {
        "run_id": 10,
        "platform_account_id": "ctrip-account",
        "source": source,
        "actor_id": actor_id,
        "captured_at": captured_at,
        "results": [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "success",
                "data_json": {
                    "metrics": {
                        "departure_sales_amount": {
                            "label": "离店销售额",
                            "value": "8888",
                        }
                    }
                },
            }
        ],
    }


class EbkExportSourceAdapterTests(unittest.TestCase):
    def test_watch_runs_are_safe_snapshots_with_operator_source_labels(self) -> None:
        sources = importlib.import_module("app.domain.exports.ebk_sources")
        runs = [
            _watch_run(
                captured_at="2026-08-24T08:00:00",
                source="scheduler",
                actor_id="scheduler",
            ),
            _watch_run(
                captured_at="2026-08-24T08:01:00",
                source="scheduler",
                actor_id="daily_brief_scheduler",
            ),
            _watch_run(
                captured_at="2026-08-24T08:02:00",
                source="api",
                actor_id="operator",
            ),
            _watch_run(
                captured_at="2026-08-24T08:03:00",
                source="api",
                actor_id="hermes",
            ),
        ]

        snapshots = sources.watch_runs_to_snapshots(runs)

        self.assertEqual(
            [snapshot["export_source"] for snapshot in snapshots],
            ["定时轮询", "每日简报", "手动读取", "机器人触发"],
        )
        blob = json.dumps(snapshots, ensure_ascii=False).lower()
        self.assertNotIn("evidence", blob)
        self.assertNotIn("browser_session_id", blob)
        self.assertEqual(
            snapshots[0]["payload_json"]["datasets"]["business_outline"]["data"]
            ["metrics"]["departure_sales_amount"]["value"],
            "8888",
        )

    def test_merge_labels_snapshots_and_keeps_latest_global_limit_in_ascending_order(self) -> None:
        sources = importlib.import_module("app.domain.exports.ebk_sources")
        snapshots = [
            {
                "platform": "meituan",
                "platform_account_id": "mt",
                "created_at": "2026-08-24T08:00:00",
                "payload_json": {},
            },
            {
                "platform": "fliggy",
                "platform_account_id": "fz",
                "created_at": "2026-08-24T10:00:00",
                "payload_json": {"read_trigger": {"via": "console"}},
            },
        ]
        runs = [
            _watch_run(
                captured_at="2026-08-24T09:00:00",
                source="scheduler",
                actor_id="scheduler",
            ),
            _watch_run(
                captured_at="2026-08-24T11:00:00",
                source="api",
                actor_id="hermes",
            ),
        ]

        merged = sources.merge_export_snapshots(snapshots, runs, limit=2)

        self.assertEqual(
            [item["created_at"] for item in merged],
            ["2026-08-24T10:00:00", "2026-08-24T11:00:00"],
        )
        self.assertEqual(
            [item["export_source"] for item in merged],
            ["手动读取", "机器人触发"],
        )

    def test_every_business_sheet_exposes_data_source(self) -> None:
        metrics = importlib.import_module("app.domain.exports.ebk_business_metrics")
        snapshots = [*build_sample_snapshots(), _ctrip_full_snapshot()]
        for snapshot in snapshots:
            snapshot["export_source"] = "未标记"

        builders = (
            (metrics.DETAIL_HEADERS, metrics.extract_business_metric_rows),
            (metrics.CORE_HEADERS, metrics.build_core_metric_rows),
            (metrics.OPERATING_BRIEF_HEADERS, metrics.build_operating_brief_rows),
            (metrics.APP_CONVERSION_HEADERS, metrics.build_app_conversion_rows),
            (metrics.COMPETITION_HEADERS, metrics.build_competition_rank_rows),
        )
        for headers, builder in builders:
            with self.subTest(builder=builder.__name__):
                self.assertIn("数据来源", headers)
                rows = builder(snapshots)
                self.assertTrue(rows)
                self.assertTrue(all(row["数据来源"] == "未标记" for row in rows))


class EbkWatchRunExportRepositoryTests(unittest.TestCase):
    def test_export_query_returns_only_whitelisted_run_and_dataset_fields(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                await repository.create_data_center_watch_group(
                    repository.DataCenterWatchGroupCreate(
                        group_key="export_watch",
                        name="导出测试",
                        platform_account_id="ctrip-account",
                    )
                )
                await repository.save_data_center_watch_run(
                    watch_group_key="export_watch",
                    platform_account_id="ctrip-account",
                    browser_session_id="private-browser-session",
                    actor_id="scheduler",
                    source="scheduler",
                    status="success",
                    summary="ok",
                    captured_at=datetime(2026, 8, 24, 9, 0),
                    dataset_results=[
                        {
                            "dataset_id": "business_outline",
                            "status": "success",
                            "data": {"metrics": {"sales": {"value": "100"}}},
                            "evidence": {"screenshot_path": "private.png"},
                        }
                    ],
                )

                runs = await repository.list_data_center_watch_runs_for_export(
                    start_at=datetime(2026, 8, 24),
                    end_at=datetime(2026, 8, 25),
                    limit=10,
                )

                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["actor_id"], "scheduler")
                self.assertEqual(runs[0]["results"][0]["data_json"]["metrics"]["sales"]["value"], "100")
                blob = json.dumps(runs, ensure_ascii=False).lower()
                self.assertNotIn("browser_session", blob)
                self.assertNotIn("evidence", blob)
                self.assertNotIn("screenshot", blob)

            asyncio.run(scenario())


class EbkBusinessMergedExportApiTests(unittest.TestCase):
    def test_csv_merges_snapshot_and_watch_run_with_global_source_column(self) -> None:
        exports_api = importlib.import_module("app.api.v1.exports")
        main = importlib.import_module("app.main")
        snapshot = build_sample_snapshots()[0]
        snapshot["payload_json"]["read_trigger"] = {"via": "console"}
        run = _watch_run(
            captured_at="2026-08-24T11:00:00",
            source="scheduler",
            actor_id="daily_brief_scheduler",
        )
        with mock.patch.object(
            exports_api.ebk_snapshots_repository,
            "list_ebk_snapshots_with_payload_for_export",
            new=mock.AsyncMock(return_value=[snapshot]),
        ), mock.patch.object(
            exports_api.ebk_watch_repository,
            "list_data_center_watch_runs_for_export",
            new=mock.AsyncMock(return_value=[run]),
            create=True,
        ) as watch_list:
            response = TestClient(main.create_app()).get(
                "/api/v1/export/ebk-business-data",
                params={"range": "today", "limit": 2},
            )

        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8-sig")
        self.assertIn("数据来源", text)
        self.assertIn("手动读取", text)
        self.assertIn("每日简报", text)
        self.assertIn("8888", text)
        self.assertEqual(watch_list.await_count, 1)
        self.assertEqual(watch_list.call_args.kwargs["limit"], 2)


if __name__ == "__main__":
    unittest.main()
