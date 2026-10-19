from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from app.domain.notifications.feishu import FeishuPushResult


class CompetitorWatchFeishuPushTests(unittest.TestCase):
    def test_push_ignores_desktop_webhook_and_uses_mobile_title(self) -> None:
        from app.domain.competitor_prices import scheduler

        calls: list[dict[str, Any]] = []

        async def fake_push(**kwargs: Any) -> FeishuPushResult:
            calls.append(kwargs)
            return FeishuPushResult(
                status="success",
                summary="sent",
                data={"title": kwargs["title"]},
                evidence={"feishu_webhook_configured": True},
            )

        original = scheduler.push_interactive_card_to_feishu_webhook
        scheduler.push_interactive_card_to_feishu_webhook = fake_push
        try:
            result = asyncio.run(
                scheduler._push_competitor_watch(
                    {
                        "group_key": "ops_watch",
                        "name": "默认监控组标题",
                        "brief_title": "澜江路竞品播报",
                        "feishu_mobile_webhook_url": "https://open.feishu.cn/mobile",
                        "feishu_desktop_webhook_url": "https://open.feishu.cn/desktop",
                        "feishu_card_layout": "desktop_table",
                    },
                    SimpleNamespace(status="success", data={"snapshots": []}),
                )
            )
        finally:
            scheduler.push_interactive_card_to_feishu_webhook = original

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["target_count"], 1)
        self.assertEqual([item["target"] for item in result["targets"]], ["mobile"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["title"], "澜江路竞品播报")
        self.assertEqual(calls[0]["webhook_url"], "https://open.feishu.cn/mobile")
        self.assertNotIn(
            "https://open.feishu.cn/desktop",
            [call["webhook_url"] for call in calls],
        )
        self.assertNotIn("mobile", str(result.get("evidence", {})))

    def test_push_requires_at_least_one_dedicated_webhook(self) -> None:
        from app.domain.competitor_prices import scheduler

        result = asyncio.run(
            scheduler._push_competitor_watch(
                {
                    "group_key": "ops_watch",
                    "name": "默认监控组标题",
                    "brief_title": "澜江路竞品播报",
                    "feishu_card_layout": "mobile_blocks",
                },
                SimpleNamespace(status="success", data={"snapshots": []}),
            )
        )

        self.assertEqual(result["status"], "config_required")
        self.assertEqual(result["target_count"], 0)

    def test_push_needs_review_uses_warning_header(self) -> None:
        from app.domain.competitor_prices.messages import build_competitor_price_feishu_card

        card = build_competitor_price_feishu_card(
            status="needs_review",
            data={
                "snapshots": [
                    {
                        "role": "own",
                        "hotel_name": "竞对A",
                        "status": "needs_review",
                        "date_context": {"check_in": "2026-06-27", "check_out": "2026-06-28"},
                    }
                ]
            },
            title="澜江路竞品播报",
        )
        self.assertEqual(card["header"]["template"], "yellow")

    def test_poll_group_returns_sanitized_operator_payload(self) -> None:
        from app.domain.competitor_prices import scheduler

        async def fake_resolve_targets(**kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                own_hotel_url="https://hotels.ctrip.com/hotels/detail/?hotelId=1",
                competitor_urls=["https://hotels.ctrip.com/hotels/detail/?hotelId=2"],
                own_hotel_alias="本店",
                competitor_aliases=["竞品A"],
            )

        async def fake_save_result(**kwargs: Any) -> SimpleNamespace:
            self.assertIn("output/playwright/competitor.png", json.dumps(kwargs["data"]))
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "status": "success",
                    "run_id": 42,
                    "summary": "saved",
                },
                run_id=42,
            )

        async def fake_update_push(**kwargs: Any) -> None:
            return None

        async def fake_evaluate_price_change(**kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "status": "skipped",
                    "summary": "price-change alert skipped in this scheduler payload test",
                }
            )

        class FakeService:
            async def read_competitor_prices(self, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(
                    status="success",
                    summary="读取成功",
                    data={
                        "snapshots": [
                            {
                                "role": "own",
                                "screenshot_path": "output/playwright/competitor.png",
                                "room_rates": [],
                            }
                        ]
                    },
                    evidence={
                        "screenshot_paths": ["output/playwright/competitor.png"],
                        "profile_path_returned": False,
                    },
                )

        with (
            patch(
                "app.domain.competitor_prices.scheduler.get_latest_competitor_price_run_meta",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.domain.competitor_prices.scheduler.resolve_competitor_browser_binding_for_group",
                new=AsyncMock(return_value=SimpleNamespace(
                    ok=True,
                    status="ready",
                    summary="ok",
                    evidence={},
                    next_action=None,
                    selection=SimpleNamespace(
                        platform_account_id="ctrip-public",
                        browser_session_id="browser-public",
                        evidence=lambda: {"selection": "configured"},
                    ),
                )),
            ),
            patch(
                "app.domain.competitor_prices.scheduler.resolve_configured_competitor_targets",
                fake_resolve_targets,
            ),
            patch(
                "app.domain.competitor_prices.scheduler.get_ctrip_ebooking_service",
                return_value=FakeService(),
            ),
            patch(
                "app.domain.competitor_prices.scheduler.save_ctrip_competitor_price_result",
                fake_save_result,
            ),
            patch(
                "app.domain.competitor_prices.scheduler.update_competitor_price_run_push_result",
                fake_update_push,
            ),
            patch(
                "app.domain.competitor_prices.scheduler.evaluate_price_change_alert",
                fake_evaluate_price_change,
            ),
        ):
            result = asyncio.run(
                scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    {
                        "group_key": "ops_watch",
                        "platform_account_id": "ctrip-public",
                        "browser_session_id": "browser-public",
                    },
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )
            )

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("output/playwright", serialized)
        self.assertEqual(result["persistence"]["run_id"], 42)


if __name__ == "__main__":
    unittest.main()
