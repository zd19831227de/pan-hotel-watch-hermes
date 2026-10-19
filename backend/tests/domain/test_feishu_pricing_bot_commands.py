from __future__ import annotations

import asyncio
import importlib
import unittest
from typing import Any

from fastapi.testclient import TestClient

from backend.tests._service_auth import service_token_headers
from app.domain.price_change_alerts.types import PriceChangeAlertResult
from app.domain.pricing_suggestions.preview import PricingPreviewResult


class FeishuPricingBotCommandTests(unittest.TestCase):
    def test_manual_price_command_parser_supports_today_and_tomorrow(self) -> None:
        bot = _reload_module("app.domain.notifications.feishu_bot")

        self.assertEqual(
            bot.parse_manual_price_adjustment_command("把今天高级大床房调到420"),
            {"room_type": "高级大床房", "platform": None, "date_label": "today", "target_price": 420},
        )
        self.assertEqual(
            bot.parse_manual_price_adjustment_command("高级房 超大床明天改到399"),
            {"room_type": "高级房 超大床", "platform": None, "date_label": "tomorrow", "target_price": 399},
        )
        self.assertEqual(
            bot.parse_manual_price_adjustment_command("将今天的奢华三卧套房调至702"),
            {"room_type": "奢华三卧套房", "platform": None, "date_label": "today", "target_price": 702},
        )
        self.assertEqual(
            bot.parse_manual_price_adjustment_command("今天奢华三卧套房调至702"),
            {"room_type": "奢华三卧套房", "platform": None, "date_label": "today", "target_price": 702},
        )
        self.assertEqual(
            bot.parse_manual_price_adjustment_command("把明天奢华三卧套房设置为702"),
            {"room_type": "奢华三卧套房", "platform": None, "date_label": "tomorrow", "target_price": 702},
        )

    def test_ambiguous_manual_price_command_replies_confirmation_card(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-manual-ambiguous", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_manual_ambiguous",
                            "chat_id": "oc_group",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 把高级大床房调到420\"}",
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "needs_confirmation")
            self.assertTrue(result["evidence"]["confirmation_card_sent"])
            self.assertFalse(result["evidence"]["approval_card_sent"])
            self.assertEqual(calls[0]["name"], "reply_interactive_card")
            self.assertIn("请补充调价信息", str(calls[0]["card"]))

        asyncio.run(scenario())

    def test_manual_confirmation_uses_chat_scoped_client(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            def fake_factory(chat_id: str | None = None):
                calls.append({"name": "client_factory", "chat_id": chat_id})
                return _FakeFeishuClient(calls)

            bot._feishu_client_factory = fake_factory

            result = await bot.handle_manual_price_adjustment_confirmation(
                bot.FeishuCommandContext(
                    event_id="evt-confirm",
                    message_id="om_confirm",
                    chat_id="oc_group",
                    open_id="ou_operator",
                    command="partial command",
                    mentioned=True,
                ),
                classification={
                    "status": "needs_confirmation",
                    "summary": "need more fields",
                    "missing_fields": ["room_type"],
                    "partial": {},
                },
            )

            self.assertEqual(result["status"], "needs_confirmation")
            self.assertEqual(calls[0], {"name": "client_factory", "chat_id": "oc_group"})

        asyncio.run(scenario())

    def test_manual_price_command_replies_config_required_when_pricing_ebk_account_missing(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            async def fake_manual_preview(**_: Any) -> PricingPreviewResult:
                return PricingPreviewResult(
                    status="config_required",
                    summary="监控组还没有绑定用于调价的 EBK 账号。",
                    watch_group_key="default_competitor_watch",
                    connection_mode="unknown",
                    can_auto_write_price=False,
                    write_policy_message="请先绑定调价 EBK 账号。",
                    evidence={"pricing_account_configured": False},
                    next_action="在账号与浏览器中给 EBK 账号绑定当前飞书调价审批群后重试。",
                )

            bot.create_manual_pricing_preview = fake_manual_preview
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-manual-missing", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_manual_missing",
                            "chat_id": "oc_group",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 将今天的奢华三卧套房调至702\"}",
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "config_required")
            self.assertEqual(calls[0]["name"], "reply_text")
            self.assertIn("绑定用于调价的 EBK 账号", calls[0]["text"])
            self.assertIn("账号与浏览器", calls[0]["text"])
            self.assertFalse(result["evidence"]["execute_triggered"])

        asyncio.run(scenario())

    def test_manual_price_command_replies_with_original_three_button_approval_card(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            async def fake_manual_preview(**kwargs: Any) -> PricingPreviewResult:
                calls.append({"name": "manual_preview", **kwargs})
                return _manual_preview_result(
                    can_auto_write_price=True,
                    connection_mode="non_direct",
                    target_price=420,
                )

            async def fake_evaluate(**_: Any) -> PriceChangeAlertResult:
                raise AssertionError("manual price command should not evaluate price-change alerts")

            bot.create_manual_pricing_preview = fake_manual_preview
            bot.evaluate_price_change_alert = fake_evaluate
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-manual", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_manual",
                            "chat_id": "oc_group",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 把今天高级大床房调到420\"}",
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "approval_card_replied")
            self.assertEqual(calls[0]["name"], "manual_preview")
            self.assertEqual(calls[0]["room_type"], "高级大床房")
            self.assertEqual(calls[0]["date_label"], "today")
            self.assertEqual(calls[0]["target_price"], 420)
            self.assertEqual(calls[1]["name"], "reply_interactive_card")
            content = str(calls[1]["card"])
            self.assertIn("通过", content)
            self.assertIn("拒绝", content)
            self.assertIn("自定义调价", content)
            self.assertNotIn("人工接管", content)
            self.assertNotIn("通过并执行", content)
            self.assertIn("custom_price", content)

        asyncio.run(scenario())

    def test_manual_price_store_hint_uses_chat_bound_pricing_account(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            class _Account:
                browser_session_id = "browser-chat-bound"
                platform_account_id = "ebk-chat-bound"
                connection_mode = "non_direct"

            async def fake_chat_context(chat_id: str | None, *, store_hint: str | None = None) -> dict[str, Any]:
                self.assertEqual(chat_id, "oc_multi")
                self.assertEqual(store_hint, "静里")
                return {
                    "resolution": {
                        "status": "resolved",
                        "competitor_watch_group_key": "watch_1782109266413",
                        "ebk_watch_group_key": "ctrip_ebooking_data_center",
                    },
                    "competitor_watch_group_key": "watch_1782109266413",
                    "pricing_browser_session_id": "browser-context-must-not-be-used",
                    "pricing_platform_account_id": "ebk-context-must-not-be-used",
                }

            def fake_chat_account(chat_id: str | None) -> Any:
                self.assertEqual(chat_id, "oc_multi")
                return _Account()

            def fake_browser_account(session_id: str | None) -> Any:
                raise AssertionError("manual pricing must not resolve account from ops context")

            async def fake_manual_preview(**kwargs: Any) -> PricingPreviewResult:
                calls.append({"name": "manual_preview", **kwargs})
                return _manual_preview_result(
                    can_auto_write_price=True,
                    connection_mode="non_direct",
                    target_price=960,
                )

            bot.build_feishu_chat_context = fake_chat_context
            bot.get_pricing_browser_session_for_feishu_chat_id = fake_chat_account
            bot.get_browser_session = fake_browser_account
            bot.create_manual_pricing_preview = fake_manual_preview
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-manual-store", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_manual_store",
                            "chat_id": "oc_multi",
                            "store_hint": "静里",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 把今天童趣双床套房调到960\"}",
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "approval_card_replied")
            preview_call = next(item for item in calls if item["name"] == "manual_preview")
            self.assertEqual(preview_call["watch_group_key"], "watch_1782109266413")
            self.assertIs(preview_call["pricing_account"].__class__, _Account)

        asyncio.run(scenario())

    def test_manual_price_store_hint_still_blocks_unknown_pricing_account(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            class _UnknownAccount:
                browser_session_id = "browser-chat-bound-unknown"
                platform_account_id = "ebk-chat-bound-unknown"
                intended_use = "ebk"
                connection_mode = "unknown"
                can_auto_write_price = False

            async def fake_chat_context(chat_id: str | None, *, store_hint: str | None = None) -> dict[str, Any]:
                return {
                    "resolution": {
                        "status": "resolved",
                        "competitor_watch_group_key": "watch_unknown",
                    },
                    "pricing_browser_session_id": "browser-context-must-not-be-used",
                    "pricing_platform_account_id": "ebk-context-must-not-be-used",
                }

            def fake_chat_account(chat_id: str | None) -> Any:
                self.assertEqual(chat_id, "oc_multi")
                return _UnknownAccount()

            def fake_browser_account(session_id: str | None) -> Any:
                raise AssertionError("manual pricing must not resolve account from ops context")

            async def fake_manual_preview(**kwargs: Any) -> PricingPreviewResult:
                calls.append({"name": "manual_preview", **kwargs})
                return _manual_preview_result(
                    can_auto_write_price=False,
                    connection_mode="unknown",
                    target_price=960,
                )

            bot.build_feishu_chat_context = fake_chat_context
            bot.get_pricing_browser_session_for_feishu_chat_id = fake_chat_account
            bot.get_browser_session = fake_browser_account
            bot.create_manual_pricing_preview = fake_manual_preview
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-manual-store-unknown", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_manual_store_unknown",
                            "chat_id": "oc_multi",
                            "store_hint": "静里",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 把今天童趣双床套房调到960\"}",
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "approval_card_replied")
            self.assertFalse(result["evidence"]["can_auto_write_price"])
            content = str(next(item for item in calls if "card" in item)["card"])
            self.assertIn("直连/未确认门店无法自动调价", content)
            self.assertNotIn("通过", content)

        asyncio.run(scenario())

    def test_manual_price_inferrs_store_hint_from_room_type_prefix(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            class _Account:
                browser_session_id = "browser-chat-bound"
                platform_account_id = "ebk-chat-bound"
                intended_use = "ebk"
                connection_mode = "non_direct"
                can_auto_write_price = True

            async def fake_chat_context(chat_id: str | None, *, store_hint: str | None = None) -> dict[str, Any]:
                self.assertEqual(chat_id, "oc_multi")
                self.assertEqual(store_hint, "静里")
                return {
                    "resolution": {
                        "status": "resolved",
                        "competitor_watch_group_key": "watch_1782109266413",
                    },
                    "pricing_browser_session_id": "browser-context-must-not-be-used",
                    "pricing_platform_account_id": "ebk-context-must-not-be-used",
                }

            def fake_chat_account(chat_id: str | None) -> Any:
                self.assertEqual(chat_id, "oc_multi")
                return _Account()

            def fake_browser_account(session_id: str | None) -> Any:
                raise AssertionError("manual pricing must not resolve account from ops context")

            async def fake_manual_preview(**kwargs: Any) -> PricingPreviewResult:
                calls.append({"name": "manual_preview", **kwargs})
                return _manual_preview_result(
                    can_auto_write_price=True,
                    connection_mode="non_direct",
                    target_price=960,
                )

            bot.build_feishu_chat_context = fake_chat_context
            bot.get_pricing_browser_session_for_feishu_chat_id = fake_chat_account
            bot.get_browser_session = fake_browser_account
            bot.create_manual_pricing_preview = fake_manual_preview
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-manual-room-prefix", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_manual_room_prefix",
                            "chat_id": "oc_multi",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 把今天静里酒店的童趣双床套房调到960\"}",
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "approval_card_replied")
            preview_call = next(item for item in calls if item["name"] == "manual_preview")
            self.assertEqual(preview_call["watch_group_key"], "watch_1782109266413")
            self.assertEqual(preview_call["room_type"], "童趣双床套房")

        asyncio.run(scenario())

    def test_pricing_reference_defer_action_returns_no_change_feedback(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")

            result = await bot.handle_event(_pricing_reference_defer_event("evt-defer", 321))

            self.assertEqual(result["status"], "pricing_reference_deferred")
            self.assertFalse(result["evidence"]["execute_triggered"])
            self.assertEqual(result["evidence"]["preview_id"], 321)
            toast = result["card_callback_response"]["toast"]
            self.assertEqual(toast["type"], "info")
            self.assertTrue(toast["content"])

        asyncio.run(scenario())

    def test_reference_preview_custom_price_action_opens_form(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")

            async def fake_get_preview(preview_id: int) -> dict[str, Any] | None:
                self.assertEqual(preview_id, 501)
                return _reference_preview_payload(can_auto_write_price=True, preview_id=501)

            bot.get_pricing_preview = fake_get_preview
            result = await bot.handle_event(_pricing_custom_price_button_event("evt-ref-custom", 501))

            self.assertEqual(result["status"], "custom_price_form_ready")
            self.assertFalse(result["evidence"]["execute_triggered"])

        asyncio.run(scenario())

    def test_custom_price_action_rejects_missing_preview_id(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")

            result = await bot.handle_event(_pricing_custom_price_button_event("evt-zero", 0))

            self.assertEqual(result["status"], "ignored")
            self.assertFalse(result["evidence"]["execute_triggered"])

        asyncio.run(scenario())

    def test_custom_price_action_parser_and_form_card(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")

            action = bot.parse_pricing_custom_price_action(
                _pricing_custom_price_button_event("evt-custom", 321)
            )
            self.assertIsNotNone(action)
            assert action is not None
            self.assertEqual(action["preview_id"], 321)
            self.assertEqual(action["chat_id"], "oc_group")

            async def fake_get_preview(preview_id: int) -> dict[str, Any] | None:
                self.assertEqual(preview_id, 321)
                return _manual_preview_payload(can_auto_write_price=True, target_price=420)

            bot.get_pricing_preview = fake_get_preview
            result = await bot.handle_event(_pricing_custom_price_button_event("evt-custom", 321))

            self.assertEqual(result["status"], "custom_price_form_ready")
            card_text = str(result["card_callback_response"])
            self.assertIn("自定义调价", card_text)
            self.assertIn("pricing_custom_price", card_text)
            self.assertIn("目标售卖价", card_text)
            self.assertFalse(result["evidence"]["execute_triggered"])

        asyncio.run(scenario())

    def test_custom_price_action_blocked_for_suggestion_only_account(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")

            async def fake_get_preview(preview_id: int) -> dict[str, Any] | None:
                self.assertEqual(preview_id, 321)
                return _manual_preview_payload(can_auto_write_price=False, target_price=420)

            bot.get_pricing_preview = fake_get_preview
            result = await bot.handle_event(_pricing_custom_price_button_event("evt-custom-direct", 321))

            self.assertEqual(result["status"], "write_not_allowed")
            self.assertFalse(result["evidence"]["execute_triggered"])
            self.assertIn("仅可查看调价建议", result["card_callback_response"]["toast"]["content"])

        asyncio.run(scenario())

    def test_custom_price_submit_updates_current_card_with_manual_confirmation(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")

            async def fake_get_preview(preview_id: int) -> dict[str, Any] | None:
                self.assertEqual(preview_id, 321)
                return _manual_preview_payload(can_auto_write_price=True, target_price=420)

            async def fake_manual_preview(**kwargs: Any) -> PricingPreviewResult:
                self.assertEqual(kwargs["room_type"], "高级大床房")
                self.assertEqual(kwargs["date_label"], "today")
                self.assertEqual(kwargs["target_price"], 388)
                return _manual_preview_result(
                    can_auto_write_price=True,
                    connection_mode="non_direct",
                    target_price=388,
                    preview_id=322,
                )

            bot.get_pricing_preview = fake_get_preview
            bot.create_manual_pricing_preview = fake_manual_preview

            result = await bot.handle_event(_pricing_custom_price_submit_event("evt-submit", 321, "388"))

            self.assertEqual(result["status"], "custom_price_confirmation_ready")
            self.assertEqual(result["pricing_preview"]["preview_id"], 322)
            card = result["card_callback_response"]["card"]["data"]
            card_text = str(card)
            self.assertIn("388", card_text)
            actions = card["elements"][-1]["actions"]
            self.assertEqual([item["value"].get("decision") for item in actions], ["approve", "cancel"])
            self.assertIn("confirm_operator_target_price", str(actions))
            self.assertNotIn("custom_price", str(actions))
            self.assertFalse(result["evidence"]["execute_triggered"])
            self.assertTrue(result["evidence"]["requires_manual_confirmation"])
            self.assertEqual(result["evidence"]["custom_target_price"], 388)

        asyncio.run(scenario())

    def test_custom_price_submit_blocked_for_direct_or_unknown_account(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")

            for mode in ("direct", "unknown"):
                manual_preview_called = False

                async def fake_get_preview(preview_id: int, *, mode: str = mode) -> dict[str, Any] | None:
                    return _manual_preview_payload(
                        can_auto_write_price=False,
                        connection_mode=mode,
                        target_price=420,
                    )

                async def fail_manual_preview(**_: Any) -> PricingPreviewResult:
                    nonlocal manual_preview_called
                    manual_preview_called = True
                    raise AssertionError("direct/unknown custom submit must not create preview")

                bot.get_pricing_preview = fake_get_preview
                bot.create_manual_pricing_preview = fail_manual_preview

                result = await bot.handle_event(
                    _pricing_custom_price_submit_event(f"evt-submit-{mode}", 321, "388")
                )

                self.assertEqual(result["status"], "write_not_allowed")
                self.assertFalse(manual_preview_called)
                self.assertFalse(result["evidence"]["execute_triggered"])

        asyncio.run(scenario())

    def test_feishu_events_route_returns_card_callback_response_at_top_level(self) -> None:
        feishu_api = _reload_module("app.api.v1.feishu")

        async def fake_handle_event(payload: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(payload["event"]["action"]["value"]["action"], "custom_price")
            return {
                "status": "custom_price_form_ready",
                "summary": "已打开自定义调价表单。",
                "card_callback_response": {
                    "toast": {"type": "info", "content": "请输入自定义价格。"},
                    "card": {
                        "type": "raw",
                        "data": {
                            "header": {
                                "title": {"tag": "plain_text", "content": "Pan Hotel Watch 调价审批"}
                            },
                            "elements": [{"tag": "markdown", "content": "**自定义调价**"}],
                        },
                    },
                },
                "evidence": {"execute_triggered": False},
            }

        original = feishu_api.handle_event
        feishu_api.handle_event = fake_handle_event
        try:
            app_module = _reload_module("app.main")
            response = TestClient(app_module.create_app()).post(
                "/api/v1/feishu/events",
                headers=service_token_headers(),
                json=_pricing_custom_price_button_event("evt-route-custom", 321),
            )
        finally:
            feishu_api.handle_event = original

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["toast"]["type"], "info")
        self.assertEqual(payload["card"]["type"], "raw")
        self.assertNotIn("card_callback_response", payload)

    def test_manual_price_direct_account_card_has_no_approval_buttons(self) -> None:
        approval = _reload_module("app.domain.pricing_approvals.cards")
        preview = _manual_preview_payload(can_auto_write_price=False, target_price=420)

        card = approval.build_pricing_approval_card(preview)
        content = str(card)

        self.assertNotIn("'content': '通过'", content)
        self.assertNotIn("拒绝", content)
        self.assertNotIn("自定义调价", content)
        self.assertNotIn("通过并执行", content)
        self.assertIn("直连/未确认门店无法自动调价", content)

    def test_price_suggestion_command_explains_scheduler_auto_trigger(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            async def fake_evaluate(**kwargs: Any) -> PriceChangeAlertResult:
                raise AssertionError("调价建议命令不应即时检测价格变化提醒")

            async def fake_preview(**kwargs: Any) -> PricingPreviewResult:
                raise AssertionError("调价建议命令不应生成调价预览")

            bot.evaluate_price_change_alert = fake_evaluate
            bot.create_pricing_preview_from_alert = fake_preview
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_feishu_pricing_command(
                bot.FeishuCommandContext(
                    event_id="evt-suggestion",
                    message_id="om_suggestion",
                    chat_id="oc_group",
                    open_id="ou_operator",
                    command="调价建议",
                )
            )

            self.assertEqual(result["status"], "pricing_help_replied")
            self.assertEqual(calls[0]["name"], "reply_text")
            self.assertIn("竞品价格轮询", calls[0]["text"])
            self.assertFalse(result["evidence"]["execute_triggered"])
            self.assertFalse(result["evidence"]["price_change_alert_manual_evaluation"])

        asyncio.run(scenario())

    def test_direct_pricing_command_explains_manual_price_command(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            async def fake_evaluate(**kwargs: Any) -> PriceChangeAlertResult:
                raise AssertionError("调价命令不应即时检测价格变化提醒")

            async def fake_preview(**kwargs: Any) -> PricingPreviewResult:
                raise AssertionError("调价命令不应基于最新价格变化建议生成审批卡")

            bot.evaluate_price_change_alert = fake_evaluate
            bot.create_pricing_preview_from_alert = fake_preview
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_feishu_pricing_command(
                bot.FeishuCommandContext(
                    event_id="evt-direct",
                    message_id="om_direct",
                    chat_id="oc_group",
                    open_id="ou_operator",
                    command="调价",
                )
            )

            self.assertEqual(result["status"], "pricing_help_replied")
            self.assertEqual(calls[0]["name"], "reply_text")
            self.assertIn("把今天高级大床房调到420", calls[0]["text"])
            self.assertIn("降价参考卡", calls[0]["text"])
            self.assertNotIn("审批卡", calls[0]["text"])
            self.assertEqual(result["evidence"]["auto_trigger"], "competitor_polling_after_persistence")

        asyncio.run(scenario())

    def test_non_direct_pricing_command_still_does_not_bypass_scheduler_or_manual_price(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []

            async def fake_evaluate(**kwargs: Any) -> PriceChangeAlertResult:
                raise AssertionError("调价命令不应即时检测价格变化提醒")

            async def fake_preview(**kwargs: Any) -> PricingPreviewResult:
                raise AssertionError("调价命令不应直接生成审批卡")

            bot.evaluate_price_change_alert = fake_evaluate
            bot.create_pricing_preview_from_alert = fake_preview
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_feishu_pricing_command(
                bot.FeishuCommandContext(
                    event_id="evt-non-direct",
                    message_id="om_non_direct",
                    chat_id="oc_group",
                    open_id="ou_operator",
                    command="调价",
                )
            )

            self.assertEqual(result["status"], "pricing_help_replied")
            self.assertEqual(calls[0]["name"], "reply_text")
            self.assertFalse(result["evidence"]["execute_triggered"])

        asyncio.run(scenario())

    def test_pricing_policy_question_is_not_pricing_command(self) -> None:
        bot = _reload_module("app.domain.notifications.feishu_bot")
        for command in (
            "静里是配置了调价账号的",
            "非直连和未设置的账号竞对降价参考卡是怎样的",
            "泉吉有没有绑调价EBK",
        ):
            with self.subTest(command=command):
                self.assertFalse(bot._is_pricing_command(command))

    def test_schema2_policy_question_does_not_dispatch_pricing_command(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")

            async def fake_command(context: Any, *, watch_group_key: str | None = None) -> dict[str, Any]:
                raise AssertionError("policy questions must not route to pricing help")

            bot.handle_feishu_pricing_command = fake_command
            bot._feishu_agent_mode = lambda: "hermes"

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-policy", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_policy",
                            "chat_id": "oc_group",
                            "content": '{"text":"<at user_id=\\"bot\\">机器人</at> 静里是配置了调价账号的"}',
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "ignored_hermes_mode")

        asyncio.run(scenario())

    def test_schema2_message_event_dispatches_pricing_command(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            seen: list[Any] = []

            async def fake_command(context: Any, *, watch_group_key: str | None = None) -> dict[str, Any]:
                seen.append((context.command, context.message_id, context.chat_id, watch_group_key))
                return {"status": "ok"}

            async def fake_resolve(chat_id: str | None) -> str | None:
                self.assertEqual(chat_id, "oc_group")
                return None

            bot.handle_feishu_pricing_command = fake_command
            bot._resolve_watch_group_key_for_chat = fake_resolve

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-message", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_message",
                            "chat_id": "oc_group",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 调价\"}",
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(seen, [("调价", "om_message", "oc_group", None)])

        asyncio.run(scenario())

    def test_schema2_manual_price_event_does_not_route_through_competitor_watch_group(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            seen: list[Any] = []

            async def fake_resolve(chat_id: str | None) -> str | None:
                raise AssertionError(
                    "manual pricing commands must not resolve EBK pricing account from competitor watch group"
                )

            async def fake_manual_command(
                context: Any,
                *,
                manual_request: dict[str, Any],
                watch_group_key: str | None = None,
            ) -> dict[str, Any]:
                seen.append((context.command, manual_request, watch_group_key))
                return {"status": "ok"}

            bot._resolve_watch_group_key_for_chat = fake_resolve
            bot.handle_manual_price_adjustment_command = fake_manual_command

            result = await bot.handle_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": "evt-room-a", "event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_room_a",
                            "chat_id": "oc_room_a",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 将今天的奢华三卧套房调至702\"}",
                        },
                    },
                }
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                seen,
                [
                    (
                        "将今天的奢华三卧套房调至702",
                        {"status": "ready", "room_type": "奢华三卧套房", "platform": None, "date_label": "today", "target_price": 702},
                        None,
                    )
                ],
            )

        asyncio.run(scenario())

    def test_long_connection_payload_dispatches_manual_pricing_to_watch_handler(self) -> None:
        async def scenario() -> None:
            listener_module = _reload_module("app.domain.notifications.feishu_listener")
            seen: list[dict[str, Any]] = []

            async def fake_handler(payload: dict[str, Any]) -> dict[str, Any]:
                seen.append(payload)
                return {
                    "status": "approval_card_replied",
                    "summary": "已回复手动调价审批卡片。",
                    "evidence": {"execute_triggered": False},
                }

            listener = listener_module.FeishuLongConnectionListener(payload_handler=fake_handler)
            payload = listener_module._sdk_event_to_payload(
                {
                    "header": {
                        "event_id": "evt-long-manual",
                        "event_type": "im.message.receive_v1",
                    },
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_operator"}},
                        "message": {
                            "message_id": "om_long_manual",
                            "chat_id": "oc_group",
                            "content": "{\"text\":\"<at user_id=\\\"bot\\\">机器人</at> 将今天的奢华三卧套房调至702\"}",
                        },
                    },
                }
            )

            result = listener._run_async_handler(payload)

            self.assertEqual(result["status"], "approval_card_replied")
            self.assertEqual(seen[0]["schema"], "2.0")
            self.assertEqual(seen[0]["header"]["event_type"], "im.message.receive_v1")
            command = _reload_module("app.domain.notifications.feishu_bot").parse_command_context(seen[0])
            self.assertIsNotNone(command)
            assert command is not None
            self.assertEqual(command.command, "将今天的奢华三卧套房调至702")

        asyncio.run(scenario())

    def test_card_action_parser_keeps_chat_context_for_result_notification(self) -> None:
        bot = _reload_module("app.domain.notifications.feishu_bot")

        action = bot.parse_pricing_approval_card_action(
            {
                "schema": "2.0",
                "header": {"event_id": "evt-action", "event_type": "card.action.trigger"},
                "event": {
                    "operator": {
                        "operator_id": {"open_id": "ou_operator", "union_id": "on_operator"}
                    },
                    "context": {"open_chat_id": "oc_group"},
                    "message_id": "om_card",
                    "action": {
                        "value": {
                            "workflow": "pricing_approval",
                            "preview_id": 123,
                            "decision": "approve",
                        }
                    },
                },
            }
        )

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action["preview_id"], 123)
        self.assertEqual(action["chat_id"], "oc_group")
        self.assertEqual(action["open_id"], "ou_operator")
        self.assertEqual(action["union_id"], "on_operator")

    def test_feishu_approve_action_passes_chat_id_to_execution_queue(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            pipeline = _reload_module("app.domain.notifications.pricing_execution_pipeline")
            approval_service = _reload_module("app.domain.pricing_approvals.service")
            queued: list[dict[str, Any]] = []
            marked: list[int] = []

            async def fake_record(**_: Any) -> Any:
                return _FakeApprovalResult(
                    status="approved",
                    approval_record_id=88,
                    summary="Feishu 审批已通过，等待执行队列。",
                )

            async def fake_mark(approval_record_id: int) -> None:
                marked.append(approval_record_id)

            original_record = bot.record_pricing_approval_decision
            original_queue = pipeline.queue_pricing_execution_pipeline
            original_mark = approval_service.mark_approval_execute_queued
            bot.record_pricing_approval_decision = fake_record
            pipeline.queue_pricing_execution_pipeline = (
                lambda *, approval_record_id, chat_id=None: queued.append(
                    {"approval_record_id": approval_record_id, "chat_id": chat_id}
                )
                or True
            )
            approval_service.mark_approval_execute_queued = fake_mark
            try:
                result = await bot.handle_pricing_approval_card_action(
                    {
                        "preview_id": 201,
                        "decision": "approve",
                        "open_id": "ou_operator",
                        "chat_id": "oc_group",
                    }
                )
            finally:
                bot.record_pricing_approval_decision = original_record
                pipeline.queue_pricing_execution_pipeline = original_queue
                approval_service.mark_approval_execute_queued = original_mark

            self.assertEqual(result["status"], "approved")
            self.assertEqual(queued, [{"approval_record_id": 88, "chat_id": "oc_group"}])
            self.assertEqual(marked, [88])

        asyncio.run(scenario())

    def test_feishu_reject_action_replies_decision_card(self) -> None:
        async def scenario() -> None:
            bot = _reload_module("app.domain.notifications.feishu_bot")
            calls: list[dict[str, Any]] = []
            bot._feishu_client_factory = lambda chat_id=None: _DecisionCardFakeClient(calls)

            async def fake_record(**_: Any) -> Any:
                return _FakeApprovalResult(
                    status="rejected",
                    approval_record_id=89,
                    summary="已拒绝本次调价预览，不会自动写价。",
                    decision="reject",
                )

            original_record = bot.record_pricing_approval_decision
            bot.record_pricing_approval_decision = fake_record
            try:
                result = await bot.handle_pricing_approval_card_action(
                    {
                        "preview_id": 202,
                        "decision": "reject",
                        "open_id": "ou_operator",
                        "chat_id": "oc_group",
                    }
                )
            finally:
                bot.record_pricing_approval_decision = original_record

            self.assertEqual(result["status"], "rejected")
            self.assertTrue(result["evidence"]["decision_card_sent"])
            self.assertEqual(len(calls), 1)
            card = calls[0]["card"]
            self.assertEqual(
                card["header"]["title"]["content"], "Pan Hotel Watch 调价审批结果"
            )
            self.assertEqual(card["header"]["template"], "grey")
            self.assertIn("**决策**：拒绝", calls[0]["card_text"])

        asyncio.run(scenario())

    def test_pipeline_card_delivery_uses_feishu_chat(self) -> None:
        async def scenario() -> None:
            pipeline = _reload_module("app.domain.notifications.pricing_execution_pipeline")
            sent: list[dict[str, Any]] = []
            created_chat_ids: list[str | None] = []

            class FakeFeishuClient:
                def __init__(
                    self,
                    *,
                    chat_id: str | None = None,
                    app_profile: str | None = None,
                ) -> None:
                    _ = app_profile
                    created_chat_ids.append(chat_id)

                async def send_interactive_card(
                    self,
                    chat_id: str,
                    card: dict[str, Any],
                    *,
                    fallback_text: str | None = None,
                ) -> dict[str, Any]:
                    sent.append(
                        {
                            "chat_id": chat_id,
                            "card": card,
                            "fallback_text": fallback_text,
                        }
                    )
                    return {"status": "success", "summary": "sent"}

            original = pipeline.FeishuBotClient
            pipeline.FeishuBotClient = FakeFeishuClient
            try:
                result = await pipeline._send_pipeline_card(  # noqa: SLF001
                    {"header": {"title": {"content": "结果"}}},
                    chat_id="oc_group",
                    fallback_text="fallback",
                )
            finally:
                pipeline.FeishuBotClient = original

            self.assertEqual(result["status"], "success")
            self.assertEqual(created_chat_ids, ["oc_group"])
            self.assertEqual(sent[0]["chat_id"], "oc_group")
            self.assertEqual(sent[0]["fallback_text"], "fallback")

        asyncio.run(scenario())


class _FakeFeishuClient:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def reply_interactive_card(
        self,
        message_id: str,
        card: dict[str, Any],
        *,
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "name": "reply_interactive_card",
                "message_id": message_id,
                "card": card,
                "fallback_text": fallback_text,
            }
        )
        return {"status": "success", "summary": "sent"}

    async def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        self.calls.append(
            {
                "name": "reply_text",
                "message_id": message_id,
                "text": text,
            }
        )
        return {"status": "success", "summary": "sent"}


class _DecisionCardFakeClient:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def send_interactive_card(
        self,
        chat_id: str,
        card: dict[str, Any],
        *,
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        card_text = chr(10).join(
            str(element.get("text", {}).get("content") or "")
            for element in card.get("elements") or []
            if isinstance(element, dict)
        )
        self.calls.append(
            {
                "name": "send_interactive_card",
                "chat_id": chat_id,
                "card": card,
                "fallback_text": fallback_text,
                "card_text": card_text,
            }
        )
        return {"status": "success", "summary": "sent"}


class _FakeApprovalResult:
    def __init__(
        self,
        *,
        status: str,
        approval_record_id: int,
        summary: str,
        decision: str = "approve",
    ) -> None:
        self.status = status
        self.approval_record_id = approval_record_id
        self.summary = summary
        self.decision = decision
        self.next_action = "执行队列将继续预检、页面空跑和写价。"

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {
            "status": self.status,
            "approval_record_id": self.approval_record_id,
            "summary": self.summary,
            "decision": self.decision,
            "mode": mode,
        }


def _alert_result(*, can_auto_write_price: bool, connection_mode: str) -> PriceChangeAlertResult:
    return PriceChangeAlertResult(
        status="suggestion_ready",
        summary="检测到竞品价格变化，已生成规则化建议。",
        watch_group_key="default_competitor_watch",
        connection_mode=connection_mode,
        can_auto_write_price=can_auto_write_price,
        write_policy_message=(
            "非直连账号可在 Feishu 审批通过后继续调价流程"
            if can_auto_write_price
            else "当前携程 EBK 账号为直连或未确认账号，仅允许生成调价建议，不允许自动化改价"
        ),
        suggestions=[
            {
                "hotel_name": "竞品酒店",
                "checkin_date": "2026-06-24",
                "room_type": "高级大床房",
                "breakfast_kind": "none",
                "current_own_price_yuan": 420,
                "competitor_previous_price_yuan": 400,
                "competitor_current_price_yuan": 460,
                "change_amount_yuan": 60,
                "change_percent": 15.0,
                "recommended_adjustment_yuan": 30,
                "recommended_price_yuan": 450,
                "trigger_reason": "竞品上涨超过阈值。",
            }
        ],
        alert_record_id=101,
    )


def _preview_result(*, can_auto_write_price: bool, connection_mode: str) -> PricingPreviewResult:
    return PricingPreviewResult(
        status="preview_ready",
        summary="调价预览已生成。",
        watch_group_key="default_competitor_watch",
        preview_id=201,
        connection_mode=connection_mode,
        can_auto_write_price=can_auto_write_price,
        write_policy_message=(
            "非直连账号可在 Feishu 审批通过后继续调价流程"
            if can_auto_write_price
            else "当前携程 EBK 账号为直连或未确认账号，仅允许生成调价建议，不允许自动化改价"
        ),
        preview_candidate={
            "actions": [
                {
                    "room_type": "高级大床房",
                    "checkin_date": "2026-06-24",
                    "breakfast_kind": "none",
                    "current_price_yuan": 420,
                    "recommended_adjustment_yuan": 30,
                    "proposed_price_yuan": 450,
                    "trigger_reason": "竞品上涨超过阈值。",
                }
            ]
        },
    )


def _manual_preview_result(
    *,
    can_auto_write_price: bool,
    connection_mode: str,
    target_price: int,
    preview_id: int = 321,
) -> PricingPreviewResult:
    payload = _manual_preview_payload(
        can_auto_write_price=can_auto_write_price,
        target_price=target_price,
        preview_id=preview_id,
        connection_mode=connection_mode,
    )
    return PricingPreviewResult(
        status=str(payload["status"]),
        summary=str(payload["summary"]),
        watch_group_key=str(payload["watch_group_key"]),
        preview_id=int(payload["preview_id"]),
        connection_mode=connection_mode,
        can_auto_write_price=can_auto_write_price,
        write_policy_message=str(payload["write_policy_message"]),
        preview_candidate=payload["preview_candidate"],
        evidence={
            "approval_source_required": "feishu_card",
            "manual_price_adjustment": True,
        },
    )


def _reference_preview_payload(
    *,
    can_auto_write_price: bool,
    preview_id: int = 501,
) -> dict[str, Any]:
    return {
        "status": "reference_ready",
        "summary": "竞品降价调价参考已生成，请店长自定义目标价。",
        "watch_group_key": "default_competitor_watch",
        "preview_id": preview_id,
        "id": preview_id,
        "connection_mode": "non_direct" if can_auto_write_price else "direct",
        "can_auto_write_price": can_auto_write_price,
        "write_policy_message": (
            "非直连账号可在 Feishu 审批通过后继续调价流程"
            if can_auto_write_price
            else "当前携程 EBK 账号为直连或未确认账号，仅允许生成调价建议，不允许自动化改价"
        ),
        "preview_candidate": {
            "source": "competitor_price_alert_reference",
            "requires_operator_target_price": True,
            "actions": [
                {
                    "room_type": "沉浸观影大床房",
                    "date_label": "tomorrow",
                    "checkin_date": "2026-06-30",
                    "checkout_date": "2026-07-01",
                    "breakfast_kind": "none",
                    "current_price_yuan": 218,
                    "recommended_adjustment_yuan": -10,
                    "proposed_price_yuan": 208,
                    "trigger_reason": "竞品降价超过阈值。",
                }
            ],
        },
    }


def _manual_preview_payload(
    *,
    can_auto_write_price: bool,
    target_price: int,
    preview_id: int = 321,
    connection_mode: str | None = None,
) -> dict[str, Any]:
    mode = connection_mode or ("non_direct" if can_auto_write_price else "direct")
    return {
        "status": "preview_ready",
        "summary": f"手动调价预览已生成：高级大床房 今日入住 -> ¥{target_price}。",
        "watch_group_key": "default_competitor_watch",
        "preview_id": preview_id,
        "id": preview_id,
        "connection_mode": mode,
        "can_auto_write_price": can_auto_write_price,
        "write_policy_message": (
            "非直连账号可在 Feishu 审批通过后继续调价流程"
            if can_auto_write_price
            else "当前携程 EBK 账号为直连或未确认账号，仅允许生成调价建议，不允许自动化改价"
        ),
        "preview_candidate": {
            "preview_type": "manual_price_adjustment",
            "actions": [
                {
                    "action_id": "manual-price-test",
                    "operation": "set_room_price",
                    "source": "manual_feishu_command",
                    "platform": "ctrip",
                    "room_type": "高级大床房",
                    "date_label": "today",
                    "checkin_date": "2026-06-24",
                    "checkout_date": "2026-06-25",
                    "breakfast_kind": "unknown",
                    "current_price_yuan": 420,
                    "recommended_adjustment_yuan": target_price - 420,
                    "proposed_price_yuan": target_price,
                    "target_price_yuan": target_price,
                    "trigger_reason": "Feishu 手动调价指令。",
                }
            ],
        },
    }



def _pricing_reference_defer_event(event_id: str, preview_id: int) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "card.action.trigger"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_operator"}},
            "context": {"open_chat_id": "oc_group"},
            "message_id": "om_card",
            "action": {
                "value": {
                    "workflow": "pricing_reference",
                    "preview_id": preview_id,
                    "action": "defer",
                }
            },
        },
    }


def _pricing_custom_price_button_event(event_id: str, preview_id: int) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "card.action.trigger"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_operator"}},
            "context": {"open_chat_id": "oc_group"},
            "message_id": "om_card",
            "action": {
                "value": {
                    "workflow": "pricing_approval",
                    "preview_id": preview_id,
                    "action": "custom_price",
                }
            },
        },
    }


def _pricing_custom_price_submit_event(
    event_id: str,
    preview_id: int,
    target_price: str,
) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "card.action.trigger"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_operator"}},
            "context": {"open_chat_id": "oc_group"},
            "message_id": "om_card",
            "token": "update-token",
            "action": {
                "value": {
                    "workflow": "pricing_custom_price",
                    "action": "submit",
                    "preview_id": preview_id,
                },
                "form_value": {"target_price": target_price},
            },
        },
    }


def _reload_module(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)
