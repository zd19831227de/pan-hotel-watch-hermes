from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from client.operator_recent_activity import enrich_operator_recent_activity
from client.watch_api import WatchApiClient


class OperatorRecentActivityClientTests(unittest.TestCase):
    def test_enrich_attaches_recent_activity_card(self) -> None:
        payload = enrich_operator_recent_activity(
            {
                "status": "success",
                "operator_summary": {
                    "intro": "最近活动",
                    "overview_rows": [{"field": "EBK 最近读取", "value": "success"}],
                },
            }
        )
        self.assertEqual(payload["feishu_card_kind"], "recent_activity")
        self.assertEqual(payload["feishu_card"]["header"]["title"]["content"], "最近盯盘活动")

    def test_get_operator_recent_activity_calls_ops_endpoint(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(
                client,
                "get_json",
                new=mock.AsyncMock(
                    return_value={
                        "status": "success",
                        "operator_summary": {
                            "intro": "最近活动",
                            "overview_rows": [],
                        },
                    }
                ),
            ) as get_json:
                payload = await client.get_operator_recent_activity(
                    ebk_watch_group_key="ctrip_ebooking_data_center",
                    competitor_watch_group_key="default_competitor_watch",
                )

            get_json.assert_awaited_once_with(
                "/api/v1/ops/operator-recent-activity",
                params={
                    "ebk_watch_group_key": "ctrip_ebooking_data_center",
                    "competitor_watch_group_key": "default_competitor_watch",
                },
            )
            self.assertEqual(payload["feishu_card_kind"], "recent_activity")

        asyncio.run(scenario())

    def test_get_operator_recent_activity_can_disable_default_groups(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(
                client,
                "get_json",
                new=mock.AsyncMock(
                    return_value={
                        "status": "success",
                        "operator_summary": {
                            "intro": "最近活动",
                            "overview_rows": [],
                        },
                    }
                ),
            ) as get_json:
                await client.get_operator_recent_activity(
                    ebk_watch_group_key="ctrip_ebooking_data_center",
                    competitor_watch_group_key=None,
                    allow_default_groups=False,
                )

            get_json.assert_awaited_once_with(
                "/api/v1/ops/operator-recent-activity",
                params={
                    "ebk_watch_group_key": "ctrip_ebooking_data_center",
                    "allow_default_groups": False,
                },
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
