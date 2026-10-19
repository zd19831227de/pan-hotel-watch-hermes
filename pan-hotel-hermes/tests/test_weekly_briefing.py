from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from client.weekly_briefing import enrich_weekly_briefing
from client.watch_api import WatchApiClient


class WeeklyBriefingTests(unittest.TestCase):
    def test_enrich_attaches_weekly_briefing_card(self) -> None:
        payload = enrich_weekly_briefing(
            {
                "status": "success",
                "store_label": "云州静里",
                "period": {"label": "2026-06-16 至 2026-06-22"},
                "operator_summary": {
                    "intro": "静里上周经营简报。",
                    "overview_rows": [
                        {"field": "EBK 读取", "value": "上周无 EBK 读取记录"},
                    ],
                },
            }
        )
        self.assertEqual(payload["feishu_card_kind"], "weekly_briefing")
        self.assertIn("上周简报", payload["feishu_card"]["header"]["title"]["content"])

    def test_get_weekly_operator_briefing_calls_ops_endpoint(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(
                client,
                "get_json",
                new=mock.AsyncMock(
                    return_value={
                        "status": "success",
                        "store_label": "云州静里",
                        "period": {"label": "2026-06-16 至 2026-06-22"},
                        "operator_summary": {"intro": "简报", "overview_rows": []},
                    }
                ),
            ) as get_json:
                payload = await client.get_weekly_operator_briefing(
                    chat_id="oc_test",
                    store_hint="静里",
                )

            get_json.assert_awaited_once_with(
                "/api/v1/ops/weekly-briefing",
                params={"chat_id": "oc_test", "store_hint": "静里"},
            )
            self.assertEqual(payload["feishu_card_kind"], "weekly_briefing")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
