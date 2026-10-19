from __future__ import annotations

import asyncio
import importlib
import unittest
from typing import Any
from unittest import mock

from app.domain.price_change_alerts.evaluator import ComparableRate
from app.domain.price_change_alerts.suggestions import build_rule_based_suggestions
from app.domain.pricing_strategy.repository import PricingStrategyDetail


def _reload_cards():
    return importlib.reload(importlib.import_module("app.domain.pricing_approvals.cards"))


def _reload_preview():
    return importlib.reload(importlib.import_module("app.domain.pricing_suggestions.preview"))


def _today_iso(preview_module) -> str:
    return preview_module.local_now().date().isoformat()


class PricingCardConversionTests(unittest.TestCase):
    def test_approval_card_shows_sell_and_base_prices(self) -> None:
        cards = _reload_cards()
        preview = {
            "preview_id": 1,
            "status": "preview_ready",
            "connection_mode": "non_direct",
            "can_auto_write_price": True,
            "write_policy_message": "",
            "preview_candidate": {
                "actions": [
                    {
                        "room_type": "童趣双床套房",
                        "date_label": "today",
                        "checkin_date": "2026-07-01",
                        "breakfast_kind": "unknown",
                        "current_price_yuan": 880,
                        "current_base_price_yuan": 785.71,
                        "proposed_price_yuan": 960,
                        "proposed_base_price_yuan": 857.14,
                        "commission_rate": 0.12,
                        "trigger_reason": "手动调价",
                    }
                ]
            },
        }

        card = cards.build_pricing_approval_card(preview)
        content = str(card)

        self.assertIn("880", content)
        self.assertIn("785.71", content)
        self.assertIn("960", content)
        self.assertIn("857.14", content)
        self.assertIn("卖价", content)
        self.assertIn("底价", content)
        self.assertIn("携程", content)

    def test_custom_price_form_explains_sell_to_base_conversion(self) -> None:
        cards = _reload_cards()
        preview = {
            "preview_id": 2,
            "preview_candidate": {
                "actions": [
                    {
                        "room_type": "童趣双床套房",
                        "checkin_date": "2026-07-01",
                        "proposed_price_yuan": 960,
                        "proposed_base_price_yuan": 857.14,
                        "commission_rate": 0.12,
                    }
                ]
            },
        }

        card = cards.build_custom_price_form_card(preview)
        content = str(card)

        self.assertIn("售卖价", content)
        self.assertIn("底价", content)
        self.assertIn("857.14", content)

    def test_operator_confirmation_card_shows_target_sell_and_base(self) -> None:
        cards = _reload_cards()
        preview = {
            "preview_id": 3,
            "preview_candidate": {
                "actions": [
                    {
                        "room_type": "童趣双床套房",
                        "checkin_date": "2026-07-01",
                        "proposed_price_yuan": 960,
                        "proposed_base_price_yuan": 857.14,
                    }
                ]
            },
        }

        card = cards.build_operator_target_price_confirmation_card(preview)
        content = str(card)

        self.assertIn("960", content)
        self.assertIn("857.14", content)
        self.assertIn("卖价", content)
        self.assertIn("底价", content)


class PricingSuggestionOwnRoomTypeTests(unittest.TestCase):
    def test_suggestion_uses_own_room_type_when_competitor_name_differs(self) -> None:
        changed_items = [
            {
                "hotel": {"config_id": 2, "alias": "竞品 A"},
                "date_label": "today",
                "checkin_date": "2026-07-01",
                "breakfast_kind": "none",
                "room_type": "商务大床房",
                "room_type_match_key": "商务大床房",
                "signed_delta_yuan": -60,
                "delta_percent": 13.0,
            }
        ]
        competitor_rates = {
            (
                2,
                "2026-07-01",
                "none",
                "商务大床房",
            ): ComparableRate(
                snapshot_id=1,
                hotel_config_id=2,
                hotel_role="competitor",
                hotel_alias="竞品 A",
                ctrip_hotel_id="100002",
                hotel_name="竞品 A",
                date_label="today",
                checkin_date="2026-07-01",
                checkout_date="2026-07-02",
                room_type="商务大床房",
                room_type_key="商务大床房",
                breakfast_kind="none",
                breakfast_text="无早",
                price_amount_yuan=400,
                availability_status="available",
                availability_text="有房",
            )
        }
        own_rates = {
            (
                "2026-07-01",
                "none",
                "商务大床房",
            ): ComparableRate(
                snapshot_id=2,
                hotel_config_id=1,
                hotel_role="own",
                hotel_alias="静里",
                ctrip_hotel_id="100001",
                hotel_name="静里酒店",
                date_label="today",
                checkin_date="2026-07-01",
                checkout_date="2026-07-02",
                room_type="童趣双床套房",
                room_type_key="商务大床房",
                breakfast_kind="none",
                breakfast_text="无早",
                price_amount_yuan=880,
                availability_status="available",
                availability_text="有房",
            )
        }
        strategy = PricingStrategyDetail.model_construct(
            id=1,
            strategy_key="default",
            name="default",
            watch_group_key="watch_jingli",
            change_trigger_mode="percent",
            change_trigger_percent=5.0,
            min_delta_yuan=10,
            max_delta_yuan=500,
            recommendation_step_yuan=10,
            max_suggested_change_yuan=100,
            allow_downward_suggestion=True,
            enabled=True,
            summary="test",
            created_at="2026-07-01T00:00:00",
            updated_at="2026-07-01T00:00:00",
        )

        suggestions, missing = build_rule_based_suggestions(
            changed_items=changed_items,
            current_competitor_rates=competitor_rates,
            own_current_rates=own_rates,
            strategy=strategy,
            account_write_policy={"connection_mode": "non_direct", "can_auto_write_price": True},
        )

        self.assertEqual(missing, [])
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["room_type"], "童趣双床套房")
        self.assertEqual(suggestions[0]["normalized_room_type"], "商务大床房")
        self.assertEqual(suggestions[0]["current_own_price_yuan"], 880)
        self.assertEqual(suggestions[0]["competitor_room_type"], "商务大床房")


class ManualPreviewPlatformTests(unittest.TestCase):
    def _make_account(self, platform: str) -> Any:
        return mock.Mock(
            browser_session_id=f"browser-{platform}",
            platform_account_id=f"ebk-{platform}",
            intended_use="ebk",
            connection_mode="non_direct",
            can_auto_write_price=True,
            platform=platform,
            display_name=f"{platform} EBK",
        )

    def _run(self, coro):
        return asyncio.run(coro)

    def test_platform_mismatch_is_rejected(self) -> None:
        async def scenario() -> None:
            preview_module = _reload_preview()
            with mock.patch.object(preview_module, "ensure_db_ready", new=mock.AsyncMock()):
                with mock.patch.object(
                    preview_module,
                    "_save_preview",
                    new=mock.AsyncMock(side_effect=lambda _alert, result, **_: result),
                ):
                    result = await preview_module.create_manual_pricing_preview(
                        watch_group_key="watch_jingli",
                        room_type="奢华三卧套房",
                        date_label="tomorrow",
                        target_price=1100,
                        pricing_account=self._make_account("meituan"),
                        actor_id="operator",
                        platform="ctrip",
                    )
            self.assertEqual(result.status, "config_required")
            self.assertIn("美团", result.summary)
            self.assertIn("携程", result.summary)
            self.assertEqual(
                result.evidence.get("requested_platform"), "ctrip"
            )
            self.assertEqual(result.evidence.get("account_platform"), "meituan")

        asyncio.run(scenario())

    def test_meituan_account_defaults_to_meituan_platform_and_skips_ctrip_read(self) -> None:
        async def scenario() -> None:
            preview_module = _reload_preview()

            # 真实 _read_manual_current_rate：美团平台必须直接 skipped 返回，
            # 绝不触碰携程日历读取（内部 import 的 ctrip service）。
            with mock.patch.object(preview_module, "ensure_db_ready", new=mock.AsyncMock()):
                with mock.patch.object(
                    preview_module,
                    "_save_preview",
                    new=mock.AsyncMock(side_effect=lambda _alert, result, **_: result),
                ):
                    result = await preview_module.create_manual_pricing_preview(
                        watch_group_key="watch_jingli",
                        room_type="江景大床房",
                        date_label="today",
                        target_price=520,
                        pricing_account=self._make_account("meituan"),
                        actor_id="operator",
                    )
            self.assertEqual(result.status, "preview_ready")
            action = result.preview_candidate["actions"][0]
            self.assertEqual(action["platform"], "meituan")
            self.assertIn(
                "ota.meituan_ebooking_price_write_after_feishu_approval",
                action["write_intent"]["tool_name"],
            )
            self.assertIsNone(action["current_price_yuan"])
            self.assertIn(
                "current_price_readback_missing", action["risk_flags"]
            )

        asyncio.run(scenario())

    def test_ctrip_account_keeps_ctrip_platform(self) -> None:
        async def scenario() -> None:
            preview_module = _reload_preview()

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                self.assertEqual(kwargs.get("platform"), "ctrip")
                return {
                    "status": "success",
                    "readback": {"status": "located", "current_price_yuan": 880},
                }

            preview_module._read_manual_current_rate = fake_read
            with mock.patch.object(preview_module, "ensure_db_ready", new=mock.AsyncMock()):
                with mock.patch.object(
                    preview_module,
                    "_save_preview",
                    new=mock.AsyncMock(side_effect=lambda _alert, result, **_: result),
                ):
                    result = await preview_module.create_manual_pricing_preview(
                        watch_group_key="watch_jingli",
                        room_type="奢华三卧套房",
                        date_label="tomorrow",
                        target_price=1100,
                        pricing_account=self._make_account("ctrip"),
                        actor_id="operator",
                    )
            self.assertEqual(result.status, "preview_ready")
            action = result.preview_candidate["actions"][0]
            self.assertEqual(action["platform"], "ctrip")
            self.assertEqual(action["current_price_yuan"], 880)

        asyncio.run(scenario())

    def test_unknown_platform_argument_is_ignored(self) -> None:
        async def scenario() -> None:
            preview_module = _reload_preview()

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                self.assertEqual(kwargs.get("platform"), "ctrip")
                return {"status": "skipped", "readback": {}}

            preview_module._read_manual_current_rate = fake_read
            with mock.patch.object(preview_module, "ensure_db_ready", new=mock.AsyncMock()):
                with mock.patch.object(
                    preview_module,
                    "_save_preview",
                    new=mock.AsyncMock(side_effect=lambda _alert, result, **_: result),
                ):
                    result = await preview_module.create_manual_pricing_preview(
                        watch_group_key="watch_jingli",
                        room_type="奢华三卧套房",
                        date_label="tomorrow",
                        target_price=1100,
                        pricing_account=self._make_account("ctrip"),
                        actor_id="operator",
                        platform="qunar",
                    )
            self.assertEqual(result.status, "preview_ready")
            action = result.preview_candidate["actions"][0]
            self.assertEqual(action["platform"], "ctrip")

        asyncio.run(scenario())

    def test_invalid_checkin_date_returns_data_required_not_500(self) -> None:
        """specified 日期混入非 ISO 文本（如「9月20日」原文）不得让预览抛错。"""

        async def scenario() -> None:
            preview_module = _reload_preview()
            with mock.patch.object(preview_module, "ensure_db_ready", new=mock.AsyncMock()):
                with mock.patch.object(
                    preview_module,
                    "_save_preview",
                    new=mock.AsyncMock(side_effect=lambda _alert, result, **_: result),
                ):
                    result = await preview_module.create_manual_pricing_preview(
                        watch_group_key="watch_jingli",
                        room_type="奢华三卧套房",
                        date_label="specified",
                        target_price=1100,
                        pricing_account=self._make_account("ctrip"),
                        actor_id="operator",
                        checkin_date="9月20日",
                    )
            self.assertEqual(result.status, "data_required")
            self.assertEqual(result.evidence.get("invalid_checkin_date"), "9月20日")

        asyncio.run(scenario())

    def test_checkin_date_beyond_15_days_is_rejected(self) -> None:
        """MCP 直传超窗口日期：预览层兜底拦截，不进审批。"""

        async def scenario() -> None:
            from datetime import date, timedelta

            preview_module = _reload_preview()
            far = (date.today() + timedelta(days=40)).isoformat()
            with mock.patch.object(preview_module, "ensure_db_ready", new=mock.AsyncMock()):
                with mock.patch.object(
                    preview_module,
                    "_save_preview",
                    new=mock.AsyncMock(side_effect=lambda _alert, result, **_: result),
                ):
                    result = await preview_module.create_manual_pricing_preview(
                        watch_group_key="watch_jingli",
                        room_type="奢华三卧套房",
                        date_label="specified",
                        target_price=1100,
                        pricing_account=self._make_account("ctrip"),
                        actor_id="operator",
                        checkin_date=far,
                    )
            self.assertEqual(result.status, "data_required")
            self.assertIn("15", result.summary)

        asyncio.run(scenario())

    def test_invalid_checkin_date_with_today_label_falls_back_to_label(self) -> None:
        """date_label=today 时非法 checkin_date 回退今天口径，不阻断预览。"""

        async def scenario() -> None:
            preview_module = _reload_preview()

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                return {"status": "skipped", "readback": {}}

            preview_module._read_manual_current_rate = fake_read
            with mock.patch.object(preview_module, "ensure_db_ready", new=mock.AsyncMock()):
                with mock.patch.object(
                    preview_module,
                    "_save_preview",
                    new=mock.AsyncMock(side_effect=lambda _alert, result, **_: result),
                ):
                    result = await preview_module.create_manual_pricing_preview(
                        watch_group_key="watch_jingli",
                        room_type="奢华三卧套房",
                        date_label="today",
                        target_price=1100,
                        pricing_account=self._make_account("ctrip"),
                        actor_id="operator",
                        checkin_date="not-a-date",
                    )
            self.assertEqual(result.status, "preview_ready")
            action = result.preview_candidate["actions"][0]
            self.assertEqual(action["checkin_date"], _today_iso(preview_module))

        asyncio.run(scenario())


class ManualPreviewCurrentRateTests(unittest.TestCase):
    def test_manual_preview_includes_ebk_current_rate_readback(self) -> None:
        async def scenario() -> None:
            preview_module = _reload_preview()
            fake_account = mock.Mock(
                browser_session_id="browser-ebk-2",
                platform_account_id="ebk-2",
                intended_use="ebk",
                connection_mode="non_direct",
                can_auto_write_price=True,
                display_name="静里 EBK",
            )

            async def fake_read_manual_current_rate(**kwargs: Any) -> dict[str, Any]:
                self.assertEqual(kwargs["room_type"], "童趣双床套房")
                self.assertEqual(kwargs["pricing_browser_session_id"], "browser-ebk-2")
                return {
                    "status": "success",
                    "readback": {
                        "status": "located",
                        "current_price_yuan": 880,
                        "current_base_price_yuan": 785.71,
                        "room_status_text": "开房",
                        "room_inventory_text": "充足",
                    },
                }

            preview_module._read_manual_current_rate = fake_read_manual_current_rate

            with mock.patch.object(preview_module, "ensure_db_ready", new=mock.AsyncMock()):
                with mock.patch.object(
                    preview_module,
                    "_save_preview",
                    new=mock.AsyncMock(side_effect=lambda _alert, result, **_: result),
                ):
                    result = await preview_module.create_manual_pricing_preview(
                        watch_group_key="watch_jingli",
                        room_type="童趣双床套房",
                        date_label="today",
                        target_price=960,
                        pricing_account=fake_account,
                        actor_id="operator",
                        reason="手动调价",
                    )

            action = result.preview_candidate["actions"][0]
            self.assertEqual(result.status, "preview_ready")
            self.assertEqual(action["current_price_yuan"], 880)
            self.assertEqual(action["current_base_price_yuan"], 785.71)
            self.assertEqual(action["proposed_base_price_yuan"], 857.14)
            self.assertEqual(action["current_rate_readback_status"], "success")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
