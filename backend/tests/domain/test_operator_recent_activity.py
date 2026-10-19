from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.domain.ops import operator_recent_activity


class OperatorRecentActivityTests(unittest.TestCase):
    def test_build_recent_activity_aggregates_sources(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_data_center_watch_run",
                    new=mock.AsyncMock(
                        return_value={
                            "run": {
                                "status": "success",
                                "captured_at": "2026-06-26T08:00:00+00:00",
                                "summary": "EBK ok",
                            }
                        }
                    ),
                ),
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_competitor_price_snapshot",
                    new=mock.AsyncMock(
                        return_value={
                            "run": {
                                "status": "success",
                                "captured_at": "2026-06-26T08:05:00+00:00",
                                "summary": "Comp ok",
                            }
                        }
                    ),
                ),
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_price_change_alert_record",
                    new=mock.AsyncMock(
                        return_value={
                            "status": "change_detected",
                            "summary": "竞对降价",
                            "changed_count": 1,
                            "created_at": "2026-06-26T08:06:00+00:00",
                        }
                    ),
                ),
                mock.patch.object(
                    operator_recent_activity,
                    "list_recent_pricing_approval_records",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "approval_record_id": 1,
                                "status": "approved",
                                "decision": "approve",
                                "summary": "已通过审批",
                                "source": "feishu_card",
                                "execute_triggered": False,
                                "created_at": "2026-06-26T07:00:00+00:00",
                            }
                        ]
                    ),
                ),
            ):
                payload = await operator_recent_activity.build_operator_recent_activity()

            self.assertEqual(payload["status"], "success")
            rows = payload["operator_summary"]["overview_rows"]
            fields = {row["field"] for row in rows}
            self.assertIn("EBK 最近读取", fields)
            self.assertIn("竞品最近读取", fields)
            self.assertIn("最近价格变化", fields)
            self.assertIn("最近调价审批", fields)
            alert = payload["latest_price_change_alert"]
            self.assertEqual(alert["summary"], "竞对降价")

        asyncio.run(scenario())

    def test_build_recent_activity_without_webhook_fields(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_data_center_watch_run",
                    new=mock.AsyncMock(return_value=None),
                ),
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_competitor_price_snapshot",
                    new=mock.AsyncMock(return_value=None),
                ),
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_price_change_alert_record",
                    new=mock.AsyncMock(return_value=None),
                ),
                mock.patch.object(
                    operator_recent_activity,
                    "list_recent_pricing_approval_records",
                    new=mock.AsyncMock(return_value=[]),
                ),
            ):
                payload = await operator_recent_activity.build_operator_recent_activity()

            self.assertIn("不含 webhook", payload["operator_summary"]["usage_note"])

        asyncio.run(scenario())

    def test_build_recent_activity_can_disable_default_watch_groups(self) -> None:
        async def scenario() -> None:
            competitor_latest = mock.AsyncMock(return_value=None)
            price_alert = mock.AsyncMock(return_value=None)
            with (
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_data_center_watch_run",
                    new=mock.AsyncMock(return_value=None),
                ) as ebk_latest,
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_competitor_price_snapshot",
                    new=competitor_latest,
                ),
                mock.patch.object(
                    operator_recent_activity,
                    "get_latest_price_change_alert_record",
                    new=price_alert,
                ),
                mock.patch.object(
                    operator_recent_activity,
                    "list_recent_pricing_approval_records",
                    new=mock.AsyncMock(return_value=[]),
                ),
            ):
                payload = await operator_recent_activity.build_operator_recent_activity(
                    ebk_watch_group_key="ctrip_ebooking_data_center",
                    competitor_watch_group_key=None,
                    allow_default_groups=False,
                )

            ebk_latest.assert_awaited_once_with(watch_group_key="ctrip_ebooking_data_center")
            competitor_latest.assert_not_awaited()
            price_alert.assert_not_awaited()
            rows = {
                row["field"]: row["value"]
                for row in payload["operator_summary"]["overview_rows"]
            }
            self.assertEqual(rows["竞品最近读取"], "竞品监控组未绑定")
            self.assertEqual(rows["最近价格变化"], "竞品监控组未绑定")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
