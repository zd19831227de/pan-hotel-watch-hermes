from __future__ import annotations

import asyncio
import importlib
import json
import os
import unittest
from typing import Any
from unittest import mock

from app.domain.ai_agent.agent import AiAgentRunResult
from app.core.settings import get_settings
from backend.tests.domain.test_competitor_watch_group_config import (
    _create_active_agent_binding,
    _isolated_sqlite_database,
    _reload_db_session,
)


class FeishuAgentModeTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_hermes_mode_does_not_invoke_builtin_llm_on_free_text(self) -> None:
        async def scenario() -> None:
            bot = _reload("app.domain.notifications.feishu_bot")
            bot._feishu_agent_mode = lambda: "hermes"

            async def fail_agent(user_message: str, *, context: Any) -> AiAgentRunResult:
                raise AssertionError("hermes 模式不应调用后端内置大模型")

            bot.run_default_ai_agent = fail_agent

            result = await bot.handle_event(
                _message_event(
                    text='<at user_id="bot">机器人</at> 今天出租率多少',
                    event_id="evt-hermes",
                    message_id="om-hermes",
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
            )

            self.assertEqual(result["status"], "ignored_hermes_mode")
            self.assertFalse(result["evidence"]["execute_triggered"])
            self.assertEqual(result["evidence"]["feishu_agent_mode"], "hermes")

        asyncio.run(scenario())

    def test_builtin_mode_still_invokes_ai_agent(self) -> None:
        async def scenario() -> None:
            bot = _reload("app.domain.notifications.feishu_bot")
            bot._feishu_agent_mode = lambda: "builtin"
            calls: list[dict[str, Any]] = []

            async def fake_agent(user_message: str, *, context: Any) -> AiAgentRunResult:
                calls.append({"name": "agent", "user_message": user_message})
                return AiAgentRunResult(
                    status="success",
                    summary="EBK 最新读取正常。",
                    final_output="EBK 最新读取正常。",
                    evidence={"ota_write_invoked": False},
                )

            bot.run_default_ai_agent = fake_agent
            bot._feishu_client_factory = lambda chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                _message_event(
                    text='<at user_id="bot">机器人</at> 今天出租率多少',
                    event_id="evt-builtin",
                    message_id="om-builtin",
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
            )

            self.assertEqual(result["status"], "ai_agent_replied")
            self.assertEqual(calls[0]["name"], "agent")

        asyncio.run(scenario())

    def test_hermes_mode_still_routes_structured_pricing_command(self) -> None:
        async def scenario() -> None:
            bot = _reload("app.domain.notifications.feishu_bot")
            bot._feishu_agent_mode = lambda: "hermes"
            seen: list[dict[str, Any]] = []

            async def fail_agent(user_message: str, *, context: Any) -> AiAgentRunResult:
                raise AssertionError("结构化调价命令不应进入大模型")

            async def fake_manual_command(
                context: Any,
                *,
                manual_request: dict[str, Any],
                watch_group_key: str | None = None,
            ) -> dict[str, Any]:
                seen.append({"manual_request": manual_request})
                return {"status": "manual_handler_used", "evidence": {"execute_triggered": False}}

            bot.run_default_ai_agent = fail_agent
            bot.handle_manual_price_adjustment_command = fake_manual_command

            result = await bot.handle_event(
                _message_event(
                    text='<at user_id="bot">机器人</at> 把今天高级大床房调到420',
                    event_id="evt-manual-hermes",
                    message_id="om-manual-hermes",
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
            )

            self.assertEqual(result["status"], "manual_handler_used")
            self.assertEqual(seen[0]["manual_request"]["room_type"], "高级大床房")
            self.assertEqual(seen[0]["manual_request"]["target_price"], 420)

        asyncio.run(scenario())

    def test_default_mode_is_hermes(self) -> None:
        bot = _reload("app.domain.notifications.feishu_bot")
        self.assertEqual(bot._feishu_agent_mode(), "hermes")

    def test_compat_mode_allows_unbound_chat_to_reach_manual_pricing_handler(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"FEISHU_STRICT_TENANT_ROUTING": "false"},
            clear=False,
        ):
            get_settings.cache_clear()

            async def scenario() -> None:
                bot = _reload("app.domain.notifications.feishu_bot")
                seen: list[str] = []

                async def fake_manual_command(
                    context: Any,
                    *,
                    manual_request: dict[str, Any],
                    watch_group_key: str | None = None,
                ) -> dict[str, Any]:
                    seen.append(context.chat_id)
                    return {"status": "manual_handler_used"}

                bot.handle_manual_price_adjustment_command = fake_manual_command
                result = await bot.handle_event(
                    _message_event(
                        text='<at user_id="bot">机器人</at> 把今天高级大床房调到420',
                        event_id="evt-compat-unbound",
                        message_id="om-compat-unbound",
                        chat_id="oc_unbound_group",
                        open_id="ou_operator",
                    )
                )

                self.assertEqual(result["status"], "manual_handler_used")
                self.assertEqual(seen, ["oc_unbound_group"])

            asyncio.run(scenario())

    def test_strict_mode_rejects_unbound_chat_before_manual_pricing_handler(self) -> None:
        with _strict_agent_environment(chat_profile="test-agent"):
            with _isolated_sqlite_database() as db_url:
                _reload_db_session(db_url)

                async def scenario() -> None:
                    bot = _reload("app.domain.notifications.feishu_bot")
                    seen: list[str] = []

                    async def fake_manual_command(
                        context: Any,
                        *,
                        manual_request: dict[str, Any],
                        watch_group_key: str | None = None,
                    ) -> dict[str, Any]:
                        seen.append(context.chat_id)
                        return {"status": "manual_handler_used"}

                    bot.handle_manual_price_adjustment_command = fake_manual_command
                    with self.assertLogs(bot.logger, level="WARNING") as captured:
                        result = await bot.handle_event(
                            _message_event(
                                text='<at user_id="bot">机器人</at> 把今天高级大床房调到420',
                                event_id="evt-strict-unbound",
                                message_id="om-strict-unbound",
                                chat_id="oc_group",
                                open_id="ou_operator",
                            )
                        )

                    self.assertEqual(result["status"], "config_required")
                    self.assertEqual(seen, [])
                    self.assertFalse(result["evidence"]["execute_triggered"])
                    self.assertRegex(result["evidence"]["chat_fingerprint"], r"^[0-9a-f]{12}$")
                    self.assertNotIn("oc_group", repr(result))
                    self.assertNotIn("oc_group", repr(captured.output))
                    self.assertIn("94ded6bd5798", repr(captured.output))

                asyncio.run(scenario())

    def test_strict_mode_rejects_event_without_chat_context(self) -> None:
        with _strict_agent_environment(chat_profile="test-agent"):
            get_settings.cache_clear()

            async def scenario() -> None:
                bot = _reload("app.domain.notifications.feishu_bot")
                seen: list[str] = []

                async def fake_manual_command(
                    context: Any,
                    *,
                    manual_request: dict[str, Any],
                    watch_group_key: str | None = None,
                ) -> dict[str, Any]:
                    seen.append(context.chat_id)
                    return {"status": "manual_handler_used"}

                bot.handle_manual_price_adjustment_command = fake_manual_command
                with self.assertLogs(bot.logger, level="WARNING") as captured:
                    result = await bot.handle_event(
                        _message_event(
                            text='<at user_id="bot">机器人</at> 把今天高级大床房调到420',
                            event_id="evt-strict-no-chat",
                            message_id="om-strict-no-chat",
                            chat_id="",
                            open_id="ou_operator",
                        )
                    )

                self.assertEqual(result["status"], "config_required")
                self.assertEqual(result["evidence"]["error_type"], "chat_context_missing")
                self.assertEqual(seen, [])
                self.assertIn("error_type=chat_context_missing", repr(captured.output))

            asyncio.run(scenario())

    def test_strict_mode_rejects_profile_mapping_mismatch(self) -> None:
        with _strict_agent_environment(chat_profile="other-agent"):
            with _isolated_sqlite_database() as db_url:
                _reload_db_session(db_url)

                async def scenario() -> None:
                    await _create_active_agent_binding("oc_group")
                    bot = _reload("app.domain.notifications.feishu_bot")
                    seen: list[str] = []

                    async def fake_manual_command(
                        context: Any,
                        *,
                        manual_request: dict[str, Any],
                        watch_group_key: str | None = None,
                    ) -> dict[str, Any]:
                        seen.append(context.chat_id)
                        return {"status": "manual_handler_used"}

                    bot.handle_manual_price_adjustment_command = fake_manual_command
                    with self.assertLogs(bot.logger, level="WARNING") as captured:
                        result = await bot.handle_event(
                            _message_event(
                                text='<at user_id="bot">机器人</at> 把今天高级大床房调到420',
                                event_id="evt-strict-mismatch",
                                message_id="om-strict-mismatch",
                                chat_id="oc_group",
                                open_id="ou_operator",
                            )
                        )

                    self.assertEqual(result["status"], "config_required")
                    self.assertEqual(seen, [])
                    self.assertEqual(result["evidence"]["profile_key"], "test-agent")
                    self.assertEqual(result["evidence"]["error_type"], "profile_mismatch")
                    self.assertNotIn("oc_group", repr(captured.output))
                    self.assertIn("profile_key=test-agent", repr(captured.output))

                asyncio.run(scenario())

    def test_strict_mode_accepts_active_matching_profile(self) -> None:
        with _strict_agent_environment(chat_profile="test-agent"):
            with _isolated_sqlite_database() as db_url:
                _reload_db_session(db_url)

                async def scenario() -> None:
                    await _create_active_agent_binding("oc_group")
                    bot = _reload("app.domain.notifications.feishu_bot")
                    seen: list[str] = []

                    async def fake_manual_command(
                        context: Any,
                        *,
                        manual_request: dict[str, Any],
                        watch_group_key: str | None = None,
                    ) -> dict[str, Any]:
                        seen.append(context.chat_id)
                        return {"status": "manual_handler_used"}

                    bot.handle_manual_price_adjustment_command = fake_manual_command
                    result = await bot.handle_event(
                        _message_event(
                            text='<at user_id="bot">机器人</at> 把今天高级大床房调到420',
                            event_id="evt-strict-match",
                            message_id="om-strict-match",
                            chat_id="oc_group",
                            open_id="ou_operator",
                        )
                    )

                    self.assertEqual(result["status"], "manual_handler_used")
                    self.assertEqual(seen, ["oc_group"])

                asyncio.run(scenario())

    def test_builtin_listener_preserves_app_id_in_normalized_header(self) -> None:
        listener = _reload("app.domain.notifications.feishu_listener")

        payload = listener._sdk_event_to_payload(
            {
                "header": {
                    "event_id": "evt-source",
                    "event_type": "im.message.receive_v1",
                    "app_id": "cli_source_app",
                    "tenant_key": "tenant-source",
                },
                "event": {"message": {"chat_id": "oc_group"}},
            }
        )

        self.assertEqual(payload["header"]["app_id"], "cli_source_app")
        self.assertEqual(payload["header"]["tenant_key"], "tenant-source")


class _FakeFeishuClient:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        self.calls.append({"name": "reply_text", "message_id": message_id, "text": text})
        return {"status": "success", "summary": "sent", "data": {}}


def _message_event(
    *,
    text: str,
    event_id: str,
    message_id: str,
    chat_id: str,
    open_id: str,
) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": open_id}},
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "content": {"text": text},
            },
        },
    }


def _strict_agent_environment(*, chat_profile: str):
    credentials = {
        "test-agent": {
            "app_id": "cli_test_agent",
            "app_secret": "test_agent_secret",
        },
        "other-agent": {
            "app_id": "cli_other_agent",
            "app_secret": "other_agent_secret",
        },
    }
    return mock.patch.dict(
        os.environ,
        {
            "FEISHU_STRICT_TENANT_ROUTING": "true",
            "FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials),
            "FEISHU_CHAT_APP_MAP_JSON": json.dumps({"oc_group": chat_profile}),
        },
        clear=False,
    )


def _reload(module_name: str):
    return importlib.reload(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
