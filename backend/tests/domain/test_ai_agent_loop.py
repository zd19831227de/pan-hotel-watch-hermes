from __future__ import annotations

import asyncio
import unittest
from typing import Any

from pydantic import BaseModel


class AiAgentLoopTests(unittest.TestCase):
    def test_agent_returns_config_required_when_llm_is_not_configured(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmConfigError
            from app.domain.ai_agent.registry import AgentToolCallContext, AgentToolRegistry

            class MissingConfigClient:
                async def chat(self, *, messages: list[dict[str, Any]], tools: list[Any]) -> Any:
                    raise AgentLlmConfigError("missing api key")

            result = await AiAgentRunner(
                registry=AgentToolRegistry(),
                llm_client=MissingConfigClient(),
            ).run(
                "查一下 EBK",
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )

            self.assertEqual(result.status, "config_required")
            self.assertIn("模型", result.summary)
            self.assertFalse(result.evidence["tool_invoked"])

        asyncio.run(scenario())

    def test_agent_executes_tool_call_and_uses_final_model_reply(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmResponse, AgentLlmToolCall
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                AgentToolRegistry,
                AgentToolResult,
                AgentToolSpec,
            )

            class Args(BaseModel):
                target: str

            tool_contexts: list[AgentToolCallContext] = []

            async def handler(arguments: Args, context: AgentToolCallContext) -> AgentToolResult:
                tool_contexts.append(context)
                return AgentToolResult(
                    status="success",
                    summary=f"{arguments.target} 正常",
                    data={"status": "fresh"},
                )

            registry = AgentToolRegistry()
            registry.register_tool(
                AgentToolSpec(
                    name="status_tool",
                    description="查询状态",
                    parameters=Args.model_json_schema(),
                    risk_level="read_only",
                    safety_notes=["只读"],
                ),
                Args,
                handler,
            )
            llm_client = _ScriptedLlmClient(
                [
                    AgentLlmResponse(
                        tool_calls=[
                            AgentLlmToolCall(
                                id="call_1",
                                name="status_tool",
                                arguments={"target": "盯盘服务"},
                            )
                        ]
                    ),
                    AgentLlmResponse(content="盯盘服务当前正常。"),
                ]
            )

            result = await AiAgentRunner(registry=registry, llm_client=llm_client).run(
                "服务状态正常吗",
                context=AgentToolCallContext(
                    event_id="evt-ai",
                    message_id="om-ai",
                    chat_id="oc_group",
                    open_id="ou_operator",
                ),
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.final_output, "盯盘服务当前正常。")
            self.assertEqual(len(result.tool_results), 1)
            self.assertEqual(result.tool_results[0]["tool_name"], "status_tool")
            self.assertEqual(result.tool_results[0]["status"], "success")
            self.assertEqual(tool_contexts[0].chat_id, "oc_group")
            self.assertEqual(len(llm_client.calls), 2)
            self.assertEqual(llm_client.calls[0]["tools"][0].name, "status_tool")
            self.assertEqual(llm_client.calls[1]["messages"][-1]["role"], "tool")
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_agent_can_answer_ebk_metric_from_direct_dataset_read_tool(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmResponse, AgentLlmToolCall
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                AgentToolRegistry,
                AgentToolResult,
                AgentToolSpec,
                EbkDatasetReadToolArgs,
            )

            async def handler(
                arguments: EbkDatasetReadToolArgs,
                context: AgentToolCallContext,
            ) -> AgentToolResult:
                return AgentToolResult(
                    status="success",
                    summary="EBK 数据中心读取成功，出租率：30.41%。",
                    data={
                        "requested_metrics": {
                            "occupancy_rate": {
                                "label": "出租率",
                                "display_value": "30.41%",
                                "dataset_id": "beneficial_data",
                            }
                        }
                    },
                    evidence={
                        "tool_name": "read_ebk_datasets",
                        "browser_read_invoked": True,
                        "webhook_called": False,
                        "ota_write_invoked": False,
                    },
                )

            registry = AgentToolRegistry()
            registry.register_tool(
                AgentToolSpec(
                    name="read_ebk_datasets",
                    description="直接读取 EBK 数据集并返回指标，不推送飞书。",
                    parameters=EbkDatasetReadToolArgs.model_json_schema(),
                    risk_level="read_only",
                    safety_notes=["不推送飞书", "不写 OTA"],
                ),
                EbkDatasetReadToolArgs,
                handler,
            )
            llm_client = _ScriptedLlmClient(
                [
                    AgentLlmResponse(
                        tool_calls=[
                            AgentLlmToolCall(
                                id="call_1",
                                name="read_ebk_datasets",
                                arguments={"metric_keys": ["occupancy_rate"]},
                            )
                        ]
                    ),
                    AgentLlmResponse(content="刚读取到 EBK 销售数据，当前出租率是 30.41%。"),
                ]
            )

            result = await AiAgentRunner(registry=registry, llm_client=llm_client).run(
                "现在读取一次 EBK的经营报告，告诉我出租率多少",
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.final_output, "刚读取到 EBK 销售数据，当前出租率是 30.41%。")
            self.assertEqual(result.tool_results[0]["tool_name"], "read_ebk_datasets")
            self.assertIn("read_ebk_datasets", llm_client.calls[0]["messages"][0]["content"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_agent_hides_ebk_push_tool_for_metric_question(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmResponse
            from app.domain.ai_agent.registry import AgentToolCallContext, get_default_agent_tool_registry

            llm_client = _ScriptedLlmClient([AgentLlmResponse(content="我会直接读取指标后回答。")])

            await AiAgentRunner(
                registry=get_default_agent_tool_registry(),
                llm_client=llm_client,
            ).run(
                "现在读取一次 EBK的经营报告，告诉我出租率多少",
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )

            tool_names = [tool.name for tool in llm_client.calls[0]["tools"]]
            self.assertIn("read_ebk_datasets", tool_names)
            self.assertNotIn("run_ebk_watch_once", tool_names)

        asyncio.run(scenario())

    def test_agent_exposes_ebk_push_tool_for_explicit_feishu_push_request(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmResponse
            from app.domain.ai_agent.registry import AgentToolCallContext, get_default_agent_tool_registry

            llm_client = _ScriptedLlmClient([AgentLlmResponse(content="我会执行盯盘推送。")])

            await AiAgentRunner(
                registry=get_default_agent_tool_registry(),
                llm_client=llm_client,
            ).run(
                "现在跑一次 EBK 盯盘并推送飞书",
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )

            tool_names = [tool.name for tool in llm_client.calls[0]["tools"]]
            self.assertIn("run_ebk_watch_once", tool_names)

        asyncio.run(scenario())

    def test_agent_hides_calendar_run_tool_for_plain_calendar_question(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmResponse
            from app.domain.ai_agent.registry import AgentToolCallContext, get_default_agent_tool_registry

            llm_client = _ScriptedLlmClient([AgentLlmResponse(content="我会读取最新持久化日历价。")])

            await AiAgentRunner(
                registry=get_default_agent_tool_registry(),
                llm_client=llm_client,
            ).run(
                "未来15天日历价怎么样",
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )

            tool_names = [tool.name for tool in llm_client.calls[0]["tools"]]
            self.assertIn("get_calendar_price_latest", tool_names)
            self.assertNotIn("run_calendar_price_watch_once", tool_names)
            self.assertIn("日历价", llm_client.calls[0]["messages"][0]["content"])

        asyncio.run(scenario())

    def test_calendar_status_question_exposes_status_tool_not_run_tool(self) -> None:
        from app.domain.ai_agent import agent as agent_module
        from app.domain.ai_agent.registry import get_default_agent_tool_registry

        registry = get_default_agent_tool_registry()
        tools = agent_module._tool_specs_for_message(
            "日历价不是设置了自动轮询间隔吗？",
            registry.tool_specs(),
        )

        names = {tool.name for tool in tools}
        self.assertIn("get_calendar_price_watch_status", names)
        self.assertNotIn("get_calendar_price_latest", names)
        self.assertNotIn("run_calendar_price_watch_once", names)

    def test_agent_exposes_calendar_run_tool_for_explicit_refresh_request(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmResponse
            from app.domain.ai_agent.registry import AgentToolCallContext, get_default_agent_tool_registry

            llm_client = _ScriptedLlmClient([AgentLlmResponse(content="我会立即读取一次日历价。")])

            await AiAgentRunner(
                registry=get_default_agent_tool_registry(),
                llm_client=llm_client,
            ).run(
                "现在刷新一次未来15天日历价",
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )

            tool_names = [tool.name for tool in llm_client.calls[0]["tools"]]
            self.assertIn("get_calendar_price_latest", tool_names)
            self.assertIn("run_calendar_price_watch_once", tool_names)

        asyncio.run(scenario())

    def test_agent_routes_unknown_tool_through_registry_rejection(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmResponse, AgentLlmToolCall
            from app.domain.ai_agent.registry import AgentToolCallContext, AgentToolRegistry

            llm_client = _ScriptedLlmClient(
                [
                    AgentLlmResponse(
                        tool_calls=[
                            AgentLlmToolCall(
                                id="call_1",
                                name="execute_pricing",
                                arguments={"price": 1},
                            )
                        ]
                    ),
                    AgentLlmResponse(content="这个操作不允许由 AI 直接执行。"),
                ]
            )

            result = await AiAgentRunner(
                registry=AgentToolRegistry(),
                llm_client=llm_client,
            ).run(
                "直接改价",
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.tool_results[0]["tool_name"], "execute_pricing")
            self.assertEqual(result.tool_results[0]["status"], "tool_not_allowed")
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_agent_stops_when_tool_round_limit_is_reached(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.agent import AiAgentRunner
            from app.domain.ai_agent.llm import AgentLlmResponse, AgentLlmToolCall
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                AgentToolRegistry,
                AgentToolResult,
                AgentToolSpec,
                EmptyToolArgs,
            )

            async def handler(arguments: EmptyToolArgs, context: AgentToolCallContext) -> AgentToolResult:
                return AgentToolResult(status="success", summary="ok")

            registry = AgentToolRegistry()
            registry.register_tool(
                AgentToolSpec(
                    name="loop_tool",
                    description="循环工具",
                    parameters=EmptyToolArgs.model_json_schema(),
                    risk_level="read_only",
                    safety_notes=["只读"],
                ),
                EmptyToolArgs,
                handler,
            )
            llm_client = _RepeatingToolClient("loop_tool")

            result = await AiAgentRunner(
                registry=registry,
                llm_client=llm_client,
                max_tool_rounds=2,
            ).run(
                "一直查",
                context=AgentToolCallContext(event_id="evt-ai"),
            )

            self.assertEqual(result.status, "tool_round_limit")
            self.assertEqual(len(result.tool_results), 2)
            self.assertEqual(llm_client.call_count, 3)
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())


class _ScriptedLlmClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def chat(self, *, messages: list[dict[str, Any]], tools: list[Any]) -> Any:
        self.calls.append({"messages": messages, "tools": tools})
        return self.responses.pop(0)


class _RepeatingToolClient:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.call_count = 0

    async def chat(self, *, messages: list[dict[str, Any]], tools: list[Any]) -> Any:
        from app.domain.ai_agent.llm import AgentLlmResponse, AgentLlmToolCall

        self.call_count += 1
        return AgentLlmResponse(
            tool_calls=[
                AgentLlmToolCall(
                    id=f"call_{self.call_count}",
                    name=self.tool_name,
                    arguments={},
                )
            ]
        )


if __name__ == "__main__":
    unittest.main()
