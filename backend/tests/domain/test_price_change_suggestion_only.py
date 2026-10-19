from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.tests.domain.test_competitor_watch_group_config import (
    _configured_test_agent,
    _create_active_agent_binding,
)


class PriceChangeSuggestionOnlyTests(unittest.TestCase):
    def test_evaluate_sends_change_only_alert_when_competitor_price_rises(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                strategy_repo = _reload_pricing_strategy_repository(db_url)
                service = _reload_price_change_service(db_url)

                await _seed_watch_group(repository)
                await _save_price_run(
                    repository,
                    captured_at="2026-06-18T08:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=400,
                    competitor_included=460,
                )
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T10:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=460,
                    competitor_included=461,
                )
                await strategy_repo.update_pricing_strategy_config(
                    patch=strategy_repo.PricingStrategyConfigPatch(
                        watch_group_key="ops_watch",
                        change_trigger_mode="percent",
                        change_trigger_percent=5,
                        recommendation_step_yuan=10,
                        max_suggested_change_yuan=30,
                        allow_downward_suggestion=True,
                    ),
                    actor_id="test",
                )

                result = await service.evaluate_price_change_alert(
                    watch_group_key="ops_watch",
                    current_run_id=current.run_id,
                    push_to_feishu=False,
                    actor_id="test",
                )

                self.assertEqual(result.status, "change_detected")
                self.assertEqual(result.changed_count, 1)
                self.assertEqual(result.suggestion_count, 0)
                self.assertEqual(result.suggestions, [])
                self.assertIn("竞品涨价", result.summary)
                self.assertIn("不建议本店涨价", result.summary)
                change = result.changed_items[0]
                self.assertEqual(change["previous_price_yuan"], 400)
                self.assertEqual(change["current_price_yuan"], 460)
                self.assertEqual(change["signed_delta_yuan"], 60)
                self.assertEqual(change["delta_percent"], 15.0)
                self.assertEqual(change["direction"], "up")
                self.assertEqual(result.evidence["account_write_policy"]["connection_mode"], "unknown")
                self.assertFalse(result.evidence["account_write_policy"]["can_auto_write_price"])

                latest = await service.get_latest_price_change_alert(watch_group_key="ops_watch")
                assert latest is not None
                self.assertEqual(latest["status"], "change_detected")
                self.assertEqual(latest["suggestion_count"], 0)
                self.assertEqual(latest["suggestions"], [])
                self.assertEqual(latest["changed_items"][0]["direction"], "up")
                self.assertNotIn("webhook", str(latest).lower())

            asyncio.run(scenario())

    def test_evaluate_generates_own_price_reduction_suggestion_when_competitor_price_drops(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                strategy_repo = _reload_pricing_strategy_repository(db_url)
                service = _reload_price_change_service(db_url)

                await _seed_watch_group(repository)
                await _save_price_run(
                    repository,
                    captured_at="2026-06-18T08:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=460,
                    competitor_included=520,
                )
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T10:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=400,
                    competitor_included=520,
                )
                await strategy_repo.update_pricing_strategy_config(
                    patch=strategy_repo.PricingStrategyConfigPatch(
                        watch_group_key="ops_watch",
                        change_trigger_mode="percent",
                        change_trigger_percent=5,
                        recommendation_step_yuan=10,
                        max_suggested_change_yuan=30,
                        allow_downward_suggestion=True,
                    ),
                    actor_id="test",
                )

                result = await service.evaluate_price_change_alert(
                    watch_group_key="ops_watch",
                    current_run_id=current.run_id,
                    push_to_feishu=False,
                    actor_id="test",
                )

                self.assertEqual(result.status, "suggestion_ready")
                self.assertEqual(result.changed_count, 1)
                self.assertEqual(result.suggestion_count, 1)
                suggestion = result.suggestions[0]
                self.assertEqual(suggestion["current_own_price_yuan"], 420)
                self.assertEqual(suggestion["competitor_previous_price_yuan"], 460)
                self.assertEqual(suggestion["competitor_current_price_yuan"], 400)
                self.assertEqual(suggestion["change_amount_yuan"], -60)
                self.assertEqual(suggestion["change_percent"], 13.04)
                self.assertEqual(suggestion["recommended_adjustment_yuan"], -30)
                self.assertEqual(suggestion["recommended_price_yuan"], 390)
                self.assertFalse(suggestion["needs_human_attention"])
                self.assertIn("建议本店降价", suggestion["trigger_reason"])
                self.assertEqual(
                    suggestion["trusted_source_statement"],
                    "仅使用可信 DOM 房价 room_rate_dom_display_price 生成建议",
                )
                self.assertEqual(suggestion["connection_mode"], "unknown")
                self.assertFalse(suggestion["can_auto_write_price"])
                self.assertIn("仅生成调价建议", suggestion["write_policy_message"])
                self.assertIn("不会自动改价", suggestion["write_policy_message"])

            asyncio.run(scenario())

    def test_evaluate_uses_watch_group_pricing_ebk_binding_for_downward_suggestion(self) -> None:
        agent_config = _configured_test_agent()
        agent_config.start()
        self.addCleanup(agent_config.stop)
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                strategy_repo = _reload_pricing_strategy_repository(db_url)
                service = _reload_price_change_service(db_url)
                sessions = _reload_browser_sessions()

                _restore_browser_session(
                    sessions,
                    browser_session_id="browser-ebk-non-direct",
                    platform_account_id="ctrip-ebk-non-direct",
                    intended_use="ebk",
                    connection_mode="non_direct",
                    pricing_feishu_chat_id="oc_pricing_only",
                    session_port=9422,
                )

                await _seed_watch_group(repository)
                await _create_active_agent_binding("oc_competitor_watch_group")
                types = importlib.import_module("app.domain.competitor_prices.types")
                rebound = await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        feishu_chat_id="oc_competitor_watch_group",
                        pricing_browser_session_id="browser-ebk-non-direct",
                    ),
                )
                assert rebound is not None
                self.assertEqual(rebound["pricing_browser_session_id"], "browser-ebk-non-direct")
                self.assertEqual(rebound["pricing_connection_mode"], "non_direct")

                await _save_price_run(
                    repository,
                    captured_at="2026-06-18T08:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=460,
                    competitor_included=520,
                )
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T10:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=400,
                    competitor_included=520,
                )
                await strategy_repo.update_pricing_strategy_config(
                    patch=strategy_repo.PricingStrategyConfigPatch(
                        watch_group_key="ops_watch",
                        change_trigger_mode="percent",
                        change_trigger_percent=5,
                        recommendation_step_yuan=10,
                        max_suggested_change_yuan=30,
                        allow_downward_suggestion=True,
                    ),
                    actor_id="test",
                )

                result = await service.evaluate_price_change_alert(
                    watch_group_key="ops_watch",
                    current_run_id=current.run_id,
                    push_to_feishu=False,
                    actor_id="test",
                )

                self.assertEqual(result.status, "suggestion_ready")
                self.assertEqual(result.connection_mode, "non_direct")
                self.assertTrue(result.can_auto_write_price)
                policy = result.evidence["account_write_policy"]
                self.assertEqual(policy["connection_mode"], "non_direct")
                self.assertTrue(policy["can_auto_write_price"])
                self.assertEqual(policy["pricing_browser_session_id"], "browser-ebk-non-direct")
                self.assertEqual(policy["pricing_platform_account_id"], "ctrip-ebk-non-direct")
                self.assertEqual(result.suggestions[0]["connection_mode"], "non_direct")
                self.assertTrue(result.suggestions[0]["can_auto_write_price"])
                self.assertEqual(result.suggestions[0]["recommended_adjustment_yuan"], -30)
                self.assertEqual(result.suggestions[0]["recommended_price_yuan"], 390)

                latest = await service.get_latest_price_change_alert(watch_group_key="ops_watch")
                assert latest is not None
                latest_policy = latest["evidence"]["account_write_policy"]
                self.assertEqual(latest_policy["pricing_browser_session_id"], "browser-ebk-non-direct")
                self.assertEqual(latest["suggestions"][0]["connection_mode"], "non_direct")

                previews = importlib.reload(
                    importlib.import_module("app.domain.pricing_suggestions.preview")
                )
                preview = await previews.create_pricing_preview_from_alert(
                    alert_record=latest,
                    actor_id="test",
                )
                self.assertEqual(preview.status, "preview_ready")
                self.assertEqual(preview.connection_mode, "non_direct")
                self.assertTrue(preview.can_auto_write_price)
                self.assertEqual(
                    preview.preview_candidate["pricing_account"]["browser_session_id"],
                    "browser-ebk-non-direct",
                )
                self.assertEqual(
                    preview.preview_candidate["pricing_account"]["platform_account_id"],
                    "ctrip-ebk-non-direct",
                )
                self.assertEqual(
                    preview.preview_candidate["actions"][0]["recommended_adjustment_yuan"],
                    -30,
                )

            asyncio.run(scenario())

    def test_no_trigger_when_change_is_below_threshold(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                strategy_repo = _reload_pricing_strategy_repository(db_url)
                service = _reload_price_change_service(db_url)

                await _seed_watch_group(repository)
                await _save_price_run(
                    repository,
                    captured_at="2026-06-18T08:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=400,
                    competitor_included=460,
                )
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T10:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=410,
                    competitor_included=461,
                )
                await strategy_repo.update_pricing_strategy_config(
                    patch=strategy_repo.PricingStrategyConfigPatch(
                        watch_group_key="ops_watch",
                        change_trigger_mode="percent",
                        change_trigger_percent=5,
                    ),
                    actor_id="test",
                )

                result = await service.evaluate_price_change_alert(
                    watch_group_key="ops_watch",
                    current_run_id=current.run_id,
                    push_to_feishu=False,
                    actor_id="test",
                )

                self.assertEqual(result.status, "no_trigger")
                self.assertEqual(result.changed_count, 0)
                self.assertEqual(result.suggestions, [])

            asyncio.run(scenario())

    def test_data_required_when_previous_run_is_missing(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                service = _reload_price_change_service(db_url)

                await _seed_watch_group(repository)
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T08:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=400,
                    competitor_included=460,
                )

                result = await service.evaluate_price_change_alert(
                    watch_group_key="ops_watch",
                    current_run_id=current.run_id,
                    push_to_feishu=False,
                    actor_id="test",
                )

                self.assertEqual(result.status, "data_required")
                self.assertEqual(result.suggestions, [])

            asyncio.run(scenario())

    def test_untrusted_and_non_dom_prices_do_not_drive_alerts(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                service = _reload_price_change_service(db_url)

                await _seed_watch_group(repository)
                await _save_price_run(
                    repository,
                    captured_at="2026-06-18T08:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=400,
                    competitor_included=460,
                    untrusted_competitor_none=100,
                )
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T10:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=400,
                    competitor_included=460,
                    untrusted_competitor_none=999,
                )

                result = await service.evaluate_price_change_alert(
                    watch_group_key="ops_watch",
                    current_run_id=current.run_id,
                    push_to_feishu=False,
                    actor_id="test",
                )

                self.assertEqual(result.status, "no_trigger")
                serialized = str(result.model_dump(mode="json"))
                self.assertIn("room_rate_dom_display_price", serialized)
                self.assertNotIn("999", serialized)
                self.assertNotIn("visible_text_price", serialized)

            asyncio.run(scenario())

    def test_untrusted_price_sources_return_needs_review_when_no_trusted_competitor_price(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                service = _reload_price_change_service(db_url)

                await _seed_watch_group(repository)
                await _save_price_run(
                    repository,
                    captured_at="2026-06-18T08:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=None,
                    competitor_included=None,
                    untrusted_competitor_none=400,
                )
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T10:00:00",
                    own_none=420,
                    own_included=480,
                    competitor_none=None,
                    competitor_included=None,
                    untrusted_competitor_none=460,
                )

                result = await service.evaluate_price_change_alert(
                    watch_group_key="ops_watch",
                    current_run_id=current.run_id,
                    push_to_feishu=False,
                    actor_id="test",
                )

                self.assertEqual(result.status, "needs_review")
                self.assertEqual(result.suggestions, [])
                self.assertEqual(result.evidence["review_issue"]["reason"], "untrusted_price_source")

            asyncio.run(scenario())

    def test_downward_change_without_downward_suggestion_only_marks_attention(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                strategy_repo = _reload_pricing_strategy_repository(db_url)
                service = _reload_price_change_service(db_url)

                await _seed_watch_group(repository)
                await _save_price_run(
                    repository,
                    captured_at="2026-06-18T08:00:00",
                    own_none=460,
                    own_included=520,
                    competitor_none=500,
                    competitor_included=560,
                )
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T10:00:00",
                    own_none=460,
                    own_included=520,
                    competitor_none=430,
                    competitor_included=560,
                )
                await strategy_repo.update_pricing_strategy_config(
                    patch=strategy_repo.PricingStrategyConfigPatch(
                        watch_group_key="ops_watch",
                        change_trigger_mode="amount",
                        min_delta_yuan=20,
                        max_delta_yuan=200,
                        allow_downward_suggestion=False,
                    ),
                    actor_id="test",
                )

                result = await service.evaluate_price_change_alert(
                    watch_group_key="ops_watch",
                    current_run_id=current.run_id,
                    push_to_feishu=False,
                    actor_id="test",
                )

                self.assertEqual(result.status, "suggestion_ready")
                suggestion = result.suggestions[0]
                self.assertEqual(suggestion["recommended_adjustment_yuan"], 0)
                self.assertEqual(suggestion["recommended_price_yuan"], 460)
                self.assertTrue(suggestion["needs_human_attention"])
                self.assertIn("暂不自动建议降价", suggestion["trigger_reason"])

            asyncio.run(scenario())

    def test_feishu_card_renders_suggestion_only_copy(self) -> None:
        cards = _reload_price_change_cards()
        types = _reload_price_change_types()

        result = types.PriceChangeAlertResult(
            status="suggestion_ready",
            summary="检测到 1 个超过阈值的价格变化，已生成规则化建议。",
            watch_group_key="ops_watch",
            current_run_id=2,
            previous_run_id=1,
            changed_count=1,
            compared_key_count=1,
            suggestions=[
                {
                    "hotel_name": "竞品 A",
                    "date_label": "today",
                    "checkin_date": "2026-06-18",
                    "breakfast_kind": "none",
                    "room_type": "商务大床房",
                    "current_own_price_yuan": 420,
                    "competitor_previous_price_yuan": 460,
                    "competitor_current_price_yuan": 400,
                    "change_amount_yuan": -60,
                    "change_percent": 13.04,
                    "recommended_adjustment_yuan": -30,
                    "recommended_price_yuan": 390,
                    "trigger_reason": "竞品降价超过 5.00% 或配置金额阈值，建议本店按规则降价。",
                    "needs_human_attention": False,
                    "trusted_source_statement": (
                        "仅使用可信 DOM 房价 room_rate_dom_display_price 生成建议"
                    ),
                    "connection_mode": "direct",
                    "can_auto_write_price": False,
                    "write_policy_message": "直连账号仅生成调价建议，不会自动改价。",
                }
            ],
        )

        card = cards.build_price_change_alert_feishu_card(
            result,
            title="澜江路泉吉酒店竞对盯盘简报",
        )
        content = str(card)
        self.assertIn("澜江路泉吉酒店竞对盯盘价格变化提醒", card["header"]["title"]["content"])
        self.assertNotIn("简报", card["header"]["title"]["content"])
        self.assertIn("价格变化提醒", content)
        self.assertIn("**今日预警**", content)
        self.assertNotIn("写价策略", content)
        self.assertIn("🏨 竞品 A | 今日入住 · 2026-06-18 | 无早", content)
        self.assertIn("📉 竞品降价：**¥460 → ¥400**", content)
        self.assertIn("变化：<font color='green'>↓60 元</font> / 13.04%", content)
        self.assertIn("建议本店降价", content)
        self.assertIn("🏠 本店当前价：**¥420**；建议本店降价：**-¥30**；建议后价格：**¥390**", content)
        self.assertIn("建议后价格", content)
        self.assertNotIn("直连/未确认门店无法自动调价", content)
        self.assertNotIn("建议人工关注", content)
        self.assertNotIn("状态", content)
        self.assertNotIn("结论", content)
        self.assertNotIn("room_rate_dom_display_price", content)
        self.assertNotIn("仅使用可信 DOM 房价", content)
        forbidden = ("立即执行", "自动执行", "写入携程", "提交到 OTA", "dry_run")
        for word in forbidden:
            self.assertNotIn(word, content)

    def test_feishu_card_renders_price_rise_as_change_only_copy(self) -> None:
        cards = _reload_price_change_cards()
        types = _reload_price_change_types()

        result = types.PriceChangeAlertResult(
            status="change_detected",
            summary="检测到 1 个超过阈值的竞品价格变化；本轮为竞品涨价，仅提醒价格变化和涨幅，不建议本店涨价。",
            watch_group_key="ops_watch",
            current_run_id=2,
            previous_run_id=1,
            changed_count=1,
            compared_key_count=1,
            changed_items=[
                {
                    "hotel": {"alias": "竞品 A"},
                    "date_label": "tomorrow",
                    "checkin_date": "2026-06-19",
                    "breakfast_kind": "included",
                    "room_type": "商务大床房",
                    "previous_price_yuan": 400,
                    "current_price_yuan": 460,
                    "signed_delta_yuan": 60,
                    "delta_percent": 15.0,
                    "direction": "up",
                }
            ],
            suggestions=[],
        )

        card = cards.build_price_change_alert_feishu_card(
            result,
            title="澜江路泉吉酒店竞对盯盘简报",
        )
        content = str(card)
        self.assertIn("澜江路泉吉酒店竞对盯盘价格变化提醒", card["header"]["title"]["content"])
        self.assertIn("**明日预警**", content)
        self.assertIn("🏨 竞品 A | 明日入住 · 2026-06-19 | 含早", content)
        self.assertIn("📈 竞品涨价：**¥400 → ¥460**", content)
        self.assertIn("变化：<font color='red'>↑60 元</font> / 15.00%", content)
        self.assertIn("仅提醒竞品涨价，不建议本店涨价", content)
        self.assertNotIn("建议本店降价", content)
        self.assertNotIn("建议后价格", content)
        self.assertNotIn("room_rate_dom_display_price", content)

    def test_feishu_card_renders_unsuggested_price_rise_next_to_downward_suggestion(self) -> None:
        cards = _reload_price_change_cards()
        types = _reload_price_change_types()

        result = types.PriceChangeAlertResult(
            status="suggestion_ready",
            summary="检测到 2 个超过阈值的竞品价格变化，已生成 1 条本店降价建议。",
            watch_group_key="ops_watch",
            current_run_id=2,
            previous_run_id=1,
            changed_count=2,
            suggestion_count=1,
            changed_items=[
                {
                    "hotel": {"config_id": 2, "alias": "竞品 A"},
                    "date_label": "today",
                    "checkin_date": "2026-06-18",
                    "breakfast_kind": "none",
                    "room_type": "商务大床房",
                    "room_type_match_key": "商务大床房",
                    "previous_price_yuan": 460,
                    "current_price_yuan": 400,
                    "signed_delta_yuan": -60,
                    "delta_percent": 13.04,
                    "direction": "down",
                },
                {
                    "hotel": {"config_id": 3, "alias": "竞品 B"},
                    "date_label": "tomorrow",
                    "checkin_date": "2026-06-19",
                    "breakfast_kind": "none",
                    "room_type": "高级大床房",
                    "room_type_match_key": "高级大床房",
                    "previous_price_yuan": 400,
                    "current_price_yuan": 460,
                    "signed_delta_yuan": 60,
                    "delta_percent": 15.0,
                    "direction": "up",
                },
            ],
            suggestions=[
                {
                    "hotel_config_id": 2,
                    "hotel_name": "竞品 A",
                    "date_label": "today",
                    "checkin_date": "2026-06-18",
                    "breakfast_kind": "none",
                    "room_type": "商务大床房",
                    "normalized_room_type": "商务大床房",
                    "current_own_price_yuan": 420,
                    "competitor_previous_price_yuan": 460,
                    "competitor_current_price_yuan": 400,
                    "change_amount_yuan": -60,
                    "change_percent": 13.04,
                    "recommended_adjustment_yuan": -30,
                    "recommended_price_yuan": 390,
                    "trigger_reason": "竞品降价超过阈值，建议本店降价。",
                    "needs_human_attention": False,
                }
            ],
        )

        card = cards.build_price_change_alert_feishu_card(result)
        fallback = cards.build_price_change_alert_fallback_text(result)
        content = f"{card} {fallback}"
        self.assertIn("竞品 A", content)
        self.assertIn("**今日预警**", content)
        self.assertIn("🏠 本店当前价：**¥420**；建议本店降价：**-¥30**；建议后价格：**¥390**", content)
        self.assertIn("竞品 B", content)
        self.assertIn("**明日预警**", content)
        self.assertLess(content.index("**今日预警**"), content.index("**明日预警**"))
        self.assertIn("📈 竞品涨价：**¥400 → ¥460**", content)
        self.assertIn("仅提醒竞品涨价，不建议本店涨价", content)

    def test_feishu_card_renders_unknown_account_warning_in_red(self) -> None:
        cards = _reload_price_change_cards()
        types = _reload_price_change_types()

        result = types.PriceChangeAlertResult(
            status="suggestion_ready",
            summary="检测到 1 个超过阈值的价格变化，已生成规则化建议。",
            watch_group_key="ops_watch",
            connection_mode="unknown",
            can_auto_write_price=False,
            write_policy_message="账号类型未确认，默认按直连处理，仅生成调价建议，不会自动改价。",
            current_run_id=2,
            previous_run_id=1,
            changed_count=1,
            compared_key_count=1,
            suggestions=[
                {
                    "hotel_name": "竞品 A",
                    "date_label": "tomorrow",
                    "checkin_date": "2026-06-19",
                    "breakfast_kind": "none",
                    "room_type": "商务大床房",
                    "current_own_price_yuan": 420,
                    "competitor_previous_price_yuan": 460,
                    "competitor_current_price_yuan": 400,
                    "change_amount_yuan": -60,
                    "change_percent": 13.04,
                    "recommended_adjustment_yuan": -30,
                    "recommended_price_yuan": 390,
                    "trigger_reason": "竞品降价超过 5.00%，建议本店按规则降价。",
                    "needs_human_attention": False,
                }
            ],
        )

        card = cards.build_price_change_alert_feishu_card(result)
        fallback = cards.build_price_change_alert_fallback_text(result)
        content = str(card)
        # 盯盘提醒卡不再展示「直连/未确认门店无法自动调价」提示（2026-08-22 运营反馈）；
        # 写入拦截仍由服务层 can_auto_write_price 每阶段强制执行，审批卡保留该提示。
        self.assertNotIn("直连/未确认门店无法自动调价", content)
        self.assertNotIn("直连/未确认门店无法自动调价", fallback)

    def test_price_change_push_uses_dedicated_alert_title(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                service = _reload_price_change_service(db_url)
                types = _reload_price_change_types()
                result = types.PriceChangeAlertResult(
                    status="suggestion_ready",
                    summary="检测到 1 个超过阈值的价格变化，已生成规则化建议。",
                    watch_group_key="ops_watch",
                    current_run_id=2,
                    previous_run_id=1,
                    changed_count=1,
                    compared_key_count=1,
                    suggestions=[
                        {
                            "hotel_name": "竞品 A",
                            "checkin_date": "2026-06-18",
                            "breakfast_kind": "none",
                            "room_type": "商务大床房",
                            "current_own_price_yuan": 420,
                            "competitor_previous_price_yuan": 460,
                            "competitor_current_price_yuan": 400,
                            "change_amount_yuan": -60,
                            "change_percent": 13.04,
                            "recommended_adjustment_yuan": -30,
                            "recommended_price_yuan": 390,
                            "trigger_reason": "竞品降价超过 5.00%，建议本店按规则降价。",
                            "needs_human_attention": False,
                        }
                    ],
                )
                pushed: list[dict[str, object]] = []

                async def fake_group(**_: object) -> dict[str, object]:
                    return {
                        "group_key": "ops_watch",
                        "name": "澜江路泉吉酒店竞对盯盘",
                        "brief_title": "澜江路泉吉酒店竞对盯盘简报",
                        "price_change_alert_title": "澜江路泉吉价格变化提醒",
                        "feishu_mobile_webhook_url": "https://example.test/webhook",
                    }

                async def fake_push(**kwargs: object) -> SimpleNamespace:
                    pushed.append(kwargs)
                    return SimpleNamespace(
                        model_dump=lambda mode: {
                            "status": "success",
                            "summary": "sent",
                            "data": {"title": kwargs["title"]},
                        }
                    )

                with (
                    patch(
                        "app.domain.competitor_prices.repository.get_competitor_watch_group_detail",
                        fake_group,
                    ),
                    patch(
                        "app.domain.price_change_alerts.service.push_interactive_card_to_feishu_webhook",
                        fake_push,
                    ),
                ):
                    payload = await service.push_price_change_alert_to_feishu(
                        result=result,
                        watch_group_key="ops_watch",
                    )

                self.assertEqual(payload["status"], "success")
                self.assertEqual(pushed[0]["title"], "澜江路泉吉价格变化提醒")
                card = pushed[0]["card"]
                self.assertIsInstance(card, dict)
                self.assertIn(
                    "澜江路泉吉价格变化提醒",
                    card["header"]["title"]["content"],
                )
                self.assertNotIn("简报", card["header"]["title"]["content"])

            asyncio.run(scenario())

    def test_price_change_modules_do_not_import_write_workflow_modules(self) -> None:
        module_dir = Path(__file__).resolve().parents[2] / "app" / "domain" / "price_change_alerts"
        source = "\n".join(path.read_text(encoding="utf-8") for path in module_dir.glob("*.py"))
        for forbidden in ("pricing_approval", "pricing_execution", "pricing_preview", "ai_brain"):
            self.assertNotIn(forbidden, source)


async def _seed_watch_group(repository) -> None:
    types = importlib.import_module("app.domain.competitor_prices.types")
    await repository.create_competitor_watch_group(
        types.CompetitorWatchGroupCreate(
            group_key="ops_watch",
            name="运营竞品盯盘",
            platform_account_id="ctrip_public_account",
        )
    )
    await repository.upsert_competitor_hotel_config(
        platform_account_id="ctrip_public_account",
        watch_group_key="ops_watch",
        hotel=types.CompetitorHotelConfigIn(
            role="own",
            alias="本店",
            detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
            sort_order=0,
        ),
    )
    await repository.upsert_competitor_hotel_config(
        platform_account_id="ctrip_public_account",
        watch_group_key="ops_watch",
        hotel=types.CompetitorHotelConfigIn(
            role="competitor",
            alias="竞品 A",
            detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
            sort_order=10,
        ),
    )


async def _save_price_run(
    repository,
    *,
    captured_at: str,
    own_none: int | None,
    own_included: int | None,
    competitor_none: int | None,
    competitor_included: int | None,
    untrusted_competitor_none: int | None = None,
):
    return await repository.save_ctrip_competitor_price_result(
        result_status="success",
        summary="read ok",
        platform_account_id="ctrip_public_account",
        browser_session_id="browser_public",
        actor_id="test",
        watch_group_key="ops_watch",
        data={
            "captured_at": captured_at,
            "targets": [
                {
                    "role": "own",
                    "label": "本店",
                    "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                },
                {
                    "role": "competitor_1",
                    "label": "竞品 A",
                    "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                },
            ],
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "本店",
                    "status": "success",
                    "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                    "current_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                    "date_context": {
                        "check_in": "2026-06-18",
                        "check_out": "2026-06-19",
                    },
                    "room_rates": _rates(
                        room_type="高级大床房",
                        none=own_none,
                        included=own_included,
                    ),
                },
                {
                    "role": "competitor_1",
                    "hotel_name": "竞品 A",
                    "status": "success",
                    "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                    "current_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                    "date_context": {
                        "check_in": "2026-06-18",
                        "check_out": "2026-06-19",
                    },
                    "room_rates": _rates(
                        room_type="高级大床房",
                        none=competitor_none,
                        included=competitor_included,
                        untrusted_none=untrusted_competitor_none,
                    ),
                },
            ],
        },
        evidence={"parser_version": "test"},
    )


def _rates(
    *,
    room_type: str,
    none: int | None,
    included: int | None,
    untrusted_none: int | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if untrusted_none is not None:
        rows.append(
            {
                "source": "visible_text_price",
                "room_type": room_type,
                "breakfast": "无早",
                "availability": "正常",
                "price": {"amount": untrusted_none, "currency": "CNY"},
            }
        )
    if none is not None:
        rows.append(
            {
                "source": "room_rate_dom_display_price",
                "room_type": room_type,
                "breakfast": "无早",
                "availability": "正常",
                "price": {"amount": none, "currency": "CNY"},
            }
        )
    if included is not None:
        rows.append(
            {
                "source": "room_rate_dom_display_price",
                "room_type": room_type,
                "breakfast": "含早",
                "availability": "正常",
                "price": {"amount": included, "currency": "CNY"},
            }
        )
    return rows


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watch-test.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        # Restore the global app.db.session engine so later tests in this
        # process do not inherit a stale engine pointing at the temp file we
        # are about to delete. Must run before the temp dir cleanup.
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db_session(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return db_session


def _reload_competitor_repository(db_url: str):
    _reload_db_session(db_url)
    repository = importlib.import_module("app.domain.competitor_prices.repository")
    return importlib.reload(repository)


def _reload_pricing_strategy_repository(db_url: str):
    _reload_db_session(db_url)
    repository = importlib.import_module("app.domain.pricing_strategy.repository")
    return importlib.reload(repository)


def _reload_price_change_service(db_url: str):
    _reload_db_session(db_url)
    modules = [
        "app.domain.price_change_alerts.types",
        "app.domain.price_change_alerts.evaluator",
        "app.domain.price_change_alerts.suggestions",
        "app.domain.price_change_alerts.repository",
        "app.domain.price_change_alerts.service",
    ]
    loaded = None
    for module_name in modules:
        loaded = importlib.import_module(module_name)
        importlib.reload(loaded)
    return loaded


def _reload_browser_sessions():
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    sessions = importlib.import_module("app.domain.browser.sessions")
    return importlib.reload(sessions)


def _restore_browser_session(
    sessions,
    *,
    browser_session_id: str,
    platform_account_id: str,
    intended_use: str,
    connection_mode: str,
    session_port: int,
    pricing_feishu_chat_id: str | None = None,
) -> None:
    sessions.get_browser_session_store().restore(
        sessions.BrowserSession.model_validate(
            {
                "browser_session_id": browser_session_id,
                "platform_account_id": platform_account_id,
                "display_name": platform_account_id,
                "platform": "ctrip",
                "session_port": session_port,
                "intended_use": intended_use,
                "connection_mode": connection_mode,
                "pricing_feishu_chat_id": pricing_feishu_chat_id,
                "login_state": "logged_in",
                "human_verification_status": "none",
                "last_heartbeat_at": "2026-06-17T00:00:00+00:00",
                "takeover_url": f"/browser-control?session={browser_session_id}",
                "manual_takeover_url": (
                    f"/browser-control?session={browser_session_id}&mode=manual"
                ),
                "can_takeover": True,
                "can_logout": True,
                "last_action": "Test session.",
            }
        )
    )


def _reload_price_change_cards():
    module = importlib.import_module("app.domain.price_change_alerts.cards")
    return importlib.reload(module)


def _reload_price_change_types():
    module = importlib.import_module("app.domain.price_change_alerts.types")
    return importlib.reload(module)


if __name__ == "__main__":
    unittest.main()
