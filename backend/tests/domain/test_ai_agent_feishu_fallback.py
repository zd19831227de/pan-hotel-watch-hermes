from __future__ import annotations

import asyncio
import importlib
import unittest
from typing import Any

from app.domain.ai_agent.agent import AiAgentRunResult


class AiAgentFeishuFallbackTests(unittest.TestCase):
    def test_mentioned_unknown_message_falls_back_to_ai_agent_and_replies_text(self) -> None:
        async def scenario() -> None:
            bot = _reload("app.domain.notifications.feishu_bot")
            bot._feishu_agent_mode = lambda: "builtin"
            calls: list[dict[str, Any]] = []

            async def fake_agent(user_message: str, *, context: Any) -> AiAgentRunResult:
                calls.append(
                    {
                        "name": "agent",
                        "user_message": user_message,
                        "event_id": context.event_id,
                        "message_id": context.message_id,
                        "chat_id": context.chat_id,
                        "open_id": context.open_id,
                    }
                )
                return AiAgentRunResult(
                    status="success",
                    summary="EBK 最新读取正常。",
                    final_output="EBK 最新读取正常，暂时没有需要人工处理的数据集。",
                    tool_results=[
                        {
                            "tool_name": "get_ebk_latest_result",
                            "status": "success",
                            "summary": "ok",
                        }
                    ],
                    evidence={"ota_write_invoked": False},
                )

            bot.run_default_ai_agent = fake_agent
            bot._feishu_client_factory = lambda _chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                _message_event(
                    text='<at user_id="bot">机器人</at> 帮我看一下 EBK 今天怎么样',
                    event_id="evt-ai",
                    message_id="om-ai",
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
            )

            self.assertEqual(result["status"], "ai_agent_replied")
            self.assertEqual(calls[0]["name"], "agent")
            self.assertEqual(calls[0]["user_message"], "帮我看一下 EBK 今天怎么样")
            self.assertEqual(calls[0]["chat_id"], "oc_group")
            self.assertEqual(calls[1]["name"], "reply_text")
            self.assertEqual(
                calls[1]["text"],
                "EBK 最新读取正常，暂时没有需要人工处理的数据集。",
            )
            self.assertFalse(result["evidence"]["execute_triggered"])
            self.assertFalse(result["evidence"]["ota_write_invoked"])
            self.assertEqual(result["evidence"]["agent_tool_count"], 1)

        asyncio.run(scenario())

    def test_unmentioned_message_does_not_trigger_ai_agent(self) -> None:
        async def scenario() -> None:
            bot = _reload("app.domain.notifications.feishu_bot")

            async def fake_agent(user_message: str, *, context: Any) -> AiAgentRunResult:
                raise AssertionError("普通群聊消息不应进入 AI Agent")

            bot.run_default_ai_agent = fake_agent

            result = await bot.handle_event(
                _message_event(
                    text="帮我看一下 EBK 今天怎么样",
                    event_id="evt-no-mention",
                    message_id="om-no-mention",
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
            )

            self.assertEqual(result["status"], "ignored")
            self.assertFalse(result["evidence"]["execute_triggered"])

        asyncio.run(scenario())

    def test_existing_manual_pricing_command_stays_on_old_handler_before_ai_fallback(self) -> None:
        async def scenario() -> None:
            bot = _reload("app.domain.notifications.feishu_bot")
            seen: list[dict[str, Any]] = []

            async def fake_agent(user_message: str, *, context: Any) -> AiAgentRunResult:
                raise AssertionError("固定调价命令应优先走原有审批卡逻辑")

            async def fake_manual_command(
                context: Any,
                *,
                manual_request: dict[str, Any],
                watch_group_key: str | None = None,
            ) -> dict[str, Any]:
                seen.append(
                    {
                        "command": context.command,
                        "manual_request": manual_request,
                        "watch_group_key": watch_group_key,
                    }
                )
                return {"status": "manual_handler_used", "evidence": {"execute_triggered": False}}

            bot.run_default_ai_agent = fake_agent
            bot.handle_manual_price_adjustment_command = fake_manual_command

            result = await bot.handle_event(
                _message_event(
                    text='<at user_id="bot">机器人</at> 把今天高级大床房调到420',
                    event_id="evt-manual-ai",
                    message_id="om-manual-ai",
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
            )

            self.assertEqual(result["status"], "manual_handler_used")
            self.assertEqual(seen[0]["manual_request"]["room_type"], "高级大床房")
            self.assertEqual(seen[0]["manual_request"]["date_label"], "today")
            self.assertEqual(seen[0]["manual_request"]["target_price"], 420)

        asyncio.run(scenario())

    def test_ai_agent_config_required_is_replied_as_operator_text(self) -> None:
        async def scenario() -> None:
            bot = _reload("app.domain.notifications.feishu_bot")
            bot._feishu_agent_mode = lambda: "builtin"
            calls: list[dict[str, Any]] = []

            async def fake_agent(user_message: str, *, context: Any) -> AiAgentRunResult:
                return AiAgentRunResult(
                    status="config_required",
                    summary="大模型未配置，暂时无法使用飞书智能体。",
                    evidence={"ota_write_invoked": False},
                    next_action="在模型配置中填写 API key 后重试。",
                )

            bot.run_default_ai_agent = fake_agent
            bot._feishu_client_factory = lambda _chat_id=None: _FakeFeishuClient(calls)

            result = await bot.handle_event(
                _message_event(
                    text='<at user_id="bot">机器人</at> 分析一下竞品价格',
                    event_id="evt-ai-config",
                    message_id="om-ai-config",
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
            )

            self.assertEqual(result["status"], "ai_agent_replied")
            self.assertEqual(calls[0]["name"], "reply_text")
            self.assertIn("大模型未配置", calls[0]["text"])
            self.assertIn("API key", calls[0]["text"])
            self.assertFalse(result["evidence"]["execute_triggered"])

        asyncio.run(scenario())


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


def _reload(module_name: str):
    return importlib.reload(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
