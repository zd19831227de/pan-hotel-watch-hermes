from __future__ import annotations

import asyncio
import unittest
from datetime import date
from unittest import mock

from app.domain.ops import operator_weekly_briefing


class OperatorWeeklyBriefingTests(unittest.TestCase):
    def test_ambiguous_store_returns_no_numbers(self) -> None:
        async def scenario() -> None:
            with mock.patch.object(
                operator_weekly_briefing,
                "build_feishu_chat_context",
                new=mock.AsyncMock(
                    return_value={
                        "chat_id": "oc_test",
                        "binding_mode": "multi_store",
                        "resolution": {
                            "status": "ambiguous",
                            "summary": "本群绑定了多家门店，请在问题里带上店名。",
                        },
                        "operator_summary": {
                            "store_selection_rows": [
                                {"field": "静里", "value": "EBK=ebk_jingli；竞品=watch_jingli"},
                            ],
                        },
                    }
                ),
            ):
                payload = await operator_weekly_briefing.build_operator_weekly_briefing(
                    chat_id="oc_test",
                )

            self.assertEqual(payload["status"], "ambiguous_store")
            self.assertFalse(payload.get("ebk_run_count"))
            self.assertIn("store_selection_rows", payload["operator_summary"])

        asyncio.run(scenario())

    def test_resolved_store_reports_data_gaps_honestly(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    operator_weekly_briefing,
                    "build_feishu_chat_context",
                    new=mock.AsyncMock(
                        return_value={
                            "chat_id": "oc_test",
                            "binding_mode": "single_store",
                            "resolution": {
                                "status": "resolved",
                                "store_label": "云州静里",
                                "ebk_watch_group_key": "ebk_jingli",
                                "competitor_watch_group_key": "watch_jingli",
                            },
                        }
                    ),
                ),
                mock.patch.object(
                    operator_weekly_briefing,
                    "_ebk_runs_in_period",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    operator_weekly_briefing,
                    "_latest_ebk_run_payload_in_period",
                    new=mock.AsyncMock(return_value=None),
                ),
                mock.patch.object(
                    operator_weekly_briefing,
                    "_competitor_runs_in_period",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    operator_weekly_briefing,
                    "_price_alerts_in_period",
                    new=mock.AsyncMock(return_value=[]),
                ),
            ):
                payload = await operator_weekly_briefing.build_operator_weekly_briefing(
                    chat_id="oc_test",
                    store_hint="静里",
                    reference_date=date(2026, 6, 26),
                )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["store_label"], "云州静里")
            self.assertFalse(payload["has_comparable_yoy"])
            self.assertIn("上周无 EBK 读取记录", payload["data_gaps"])
            self.assertIn("上周无竞品轮询记录", payload["data_gaps"])
            intro = payload["operator_summary"]["intro"]
            self.assertIn("不含模型推算或同比", intro)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
