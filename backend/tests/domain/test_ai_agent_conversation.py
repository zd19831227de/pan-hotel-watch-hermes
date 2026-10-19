from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class AiAgentConversationTests(unittest.TestCase):
    def test_conversation_manager_reuses_session_and_returns_recent_context(self) -> None:
        async def scenario() -> None:
            from app.db.base import Base
            from app.domain.ai_agent.conversation import AiAgentConversationManager
            from app.domain.ai_agent.models import AiAgentConversation, AiAgentMessage

            session_factory, db_path = await _temp_session_factory(
                AiAgentConversation,
                AiAgentMessage,
            )
            try:
                manager = AiAgentConversationManager(session_factory=session_factory)
                first = await manager.get_or_create_session(
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
                second = await manager.get_or_create_session(
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
                await manager.add_message(first.session_id, "user", "第一句")
                await manager.add_message(first.session_id, "assistant", "第一答")
                await manager.add_message(first.session_id, "user", "第二句")
                await manager.add_message(first.session_id, "assistant", "第二答")
                await manager.add_message(first.session_id, "user", "第三句")

                context = await manager.get_context(first.session_id, max_turns=1)

                self.assertEqual(first.session_id, second.session_id)
                self.assertEqual([item["content"] for item in context], ["第二答", "第三句"])
                self.assertTrue(all("tool_calls" not in item for item in context))
            finally:
                Path(db_path).unlink(missing_ok=True)

        asyncio.run(scenario())

    def test_agent_includes_persisted_context_and_records_reply(self) -> None:
        async def scenario() -> None:
            from app.db.base import Base
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.conversation import AiAgentConversationManager
            from app.domain.ai_agent.llm import AgentLlmResponse
            from app.domain.ai_agent.models import AiAgentConversation, AiAgentMessage
            from app.domain.ai_agent.registry import AgentToolCallContext, AgentToolRegistry

            session_factory, db_path = await _temp_session_factory(
                AiAgentConversation,
                AiAgentMessage,
            )
            try:
                manager = AiAgentConversationManager(session_factory=session_factory)
                session = await manager.get_or_create_session(
                    chat_id="oc_group",
                    open_id="ou_operator",
                )
                await manager.add_message(session.session_id, "user", "上一轮：查今天出租率")
                await manager.add_message(session.session_id, "assistant", "上一轮出租率是 30.41%。")
                llm_client = _CaptureLlmClient("我记得上一轮出租率是 30.41%。")

                result = await AiAgentRunner(
                    registry=AgentToolRegistry(),
                    llm_client=llm_client,
                    conversation_manager=manager,
                    max_context_turns=3,
                ).run(
                    "刚才查的出租率是多少？",
                    context=AgentToolCallContext(
                        event_id="evt-ai",
                        message_id="om-current",
                        chat_id="oc_group",
                        open_id="ou_operator",
                    ),
                )

                messages = llm_client.calls[0]["messages"]
                self.assertEqual(result.final_output, "我记得上一轮出租率是 30.41%。")
                self.assertIn("上一轮：查今天出租率", [item["content"] for item in messages])
                self.assertIn("刚才查的出租率是多少？", [item["content"] for item in messages])

                stored = await manager.get_context(session.session_id, max_turns=5)
                self.assertEqual(stored[-2]["content"], "刚才查的出租率是多少？")
                self.assertEqual(stored[-1]["content"], "我记得上一轮出租率是 30.41%。")
            finally:
                Path(db_path).unlink(missing_ok=True)

        asyncio.run(scenario())

    def test_agent_records_tool_call_summary_without_raw_tool_payload(self) -> None:
        async def scenario() -> None:
            from app.db.base import Base
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.conversation import AiAgentConversationManager
            from app.domain.ai_agent.llm import AgentLlmResponse, AgentLlmToolCall
            from app.domain.ai_agent.models import AiAgentConversation, AiAgentMessage
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                AgentToolRegistry,
                AgentToolResult,
                AgentToolSpec,
                EmptyToolArgs,
            )

            session_factory, db_path = await _temp_session_factory(
                AiAgentConversation,
                AiAgentMessage,
            )
            try:
                async def handler(arguments: EmptyToolArgs, context: AgentToolCallContext) -> AgentToolResult:
                    return AgentToolResult(
                        status="success",
                        summary="工具执行成功",
                        data={"feishu_webhook_url": "https://open.feishu.cn/secret"},
                        evidence={"webhook_called": False, "ota_write_invoked": False},
                    )

                registry = AgentToolRegistry()
                registry.register_tool(
                    AgentToolSpec(
                        name="safe_tool",
                        description="安全测试工具",
                        parameters=EmptyToolArgs.model_json_schema(),
                        risk_level="read_only",
                        safety_notes=["只读"],
                    ),
                    EmptyToolArgs,
                    handler,
                )
                manager = AiAgentConversationManager(session_factory=session_factory)
                llm_client = _ScriptedCaptureLlmClient(
                    [
                        AgentLlmResponse(
                            tool_calls=[
                                AgentLlmToolCall(
                                    id="call_1",
                                    name="safe_tool",
                                    arguments={},
                                )
                            ]
                        ),
                        AgentLlmResponse(content="工具执行完成。"),
                    ]
                )

                await AiAgentRunner(
                    registry=registry,
                    llm_client=llm_client,
                    conversation_manager=manager,
                ).run(
                    "执行安全工具",
                    context=AgentToolCallContext(
                        event_id="evt-ai",
                        message_id="om-current",
                        chat_id="oc_group",
                        open_id="ou_operator",
                    ),
                )

                session = await manager.get_or_create_session("oc_group", "ou_operator")
                stored = await manager.get_context(session.session_id, max_turns=5)
                serialized = str(stored)

                self.assertIn("safe_tool", serialized)
                self.assertIn("success", serialized)
                self.assertNotIn("open.feishu.cn/secret", serialized)
            finally:
                Path(db_path).unlink(missing_ok=True)

        asyncio.run(scenario())


async def _temp_session_factory(*models: Any) -> tuple[Any, str]:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = handle.name
    handle.close()
    normalized_db_path = db_path.replace("\\", "/")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{normalized_db_path}",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        for model in models:
            await connection.run_sync(model.__table__.create, checkfirst=True)
    return async_sessionmaker(engine, expire_on_commit=False), db_path


class _CaptureLlmClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    async def chat(self, *, messages: list[dict[str, Any]], tools: list[Any]) -> AgentLlmResponse:
        from app.domain.ai_agent.llm import AgentLlmResponse

        self.calls.append({"messages": messages, "tools": tools})
        return AgentLlmResponse(content=self.content)


class _ScriptedCaptureLlmClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def chat(self, *, messages: list[dict[str, Any]], tools: list[Any]) -> Any:
        self.calls.append({"messages": messages, "tools": tools})
        return self.responses.pop(0)


if __name__ == "__main__":
    unittest.main()
