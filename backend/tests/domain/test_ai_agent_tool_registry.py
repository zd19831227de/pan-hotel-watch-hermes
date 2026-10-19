from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest import mock

from pydantic import BaseModel


class AiAgentToolRegistryTests(unittest.TestCase):
    def test_default_registry_exposes_only_first_version_whitelisted_tools(self) -> None:
        from app.domain.ai_agent.registry import get_default_agent_tool_registry

        registry = get_default_agent_tool_registry()

        self.assertEqual(
            registry.tool_names(),
            [
                "get_ebk_latest_result",
                "run_ebk_watch_once",
                "read_ebk_datasets",
                "get_competitor_latest_prices",
                "get_calendar_price_latest",
                "get_calendar_price_watch_status",
                "run_calendar_price_watch_once",
                "analyze_competitor_price_change",
                "get_watch_scheduler_status",
                "pause_watch",
                "resume_watch",
                "create_manual_pricing_approval",
            ],
        )
        self.assertNotIn("execute_pricing", registry.tool_names())
        self.assertNotIn("dry_run_pricing", registry.tool_names())
        self.assertNotIn("run_browser_dom_script", registry.tool_names())

    def test_default_tool_specs_are_complete_for_llm_tool_calling(self) -> None:
        from app.domain.ai_agent.registry import get_default_agent_tool_registry

        registry = get_default_agent_tool_registry()

        for spec in registry.tool_specs():
            self.assertTrue(spec.name)
            self.assertTrue(spec.description)
            self.assertIn(spec.risk_level, {"read_only", "state_change", "approval_only"})
            self.assertIsInstance(spec.parameters, dict)
            self.assertEqual(spec.parameters.get("type"), "object")
            self.assertIsInstance(spec.parameters.get("properties"), dict)
            self.assertTrue(spec.safety_notes)

    def test_default_pricing_approval_tool_requires_context_and_never_writes_ota(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                get_default_agent_tool_registry,
            )

            registry = get_default_agent_tool_registry()

            result = await registry.execute_tool(
                "create_manual_pricing_approval",
                {"room_type": "高级大床房", "date_label": "today", "target_price": 420},
                context=AgentToolCallContext(
                    event_id="evt-ai",
                    message_id=None,
                    chat_id=None,
                    open_id="ou_operator",
                ),
            )

            self.assertEqual(result.status, "config_required")
            self.assertTrue(result.evidence["handler_invoked"])
            self.assertFalse(result.evidence["ota_write_invoked"])
            self.assertFalse(result.evidence["execute_triggered"])
            self.assertFalse(result.evidence["approval_card_sent"])

        asyncio.run(scenario())

    def test_unknown_tool_is_rejected_without_invoking_any_handler(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.registry import AgentToolCallContext, AgentToolRegistry

            registry = AgentToolRegistry()
            result = await registry.execute_tool(
                "execute_pricing",
                {},
                context=AgentToolCallContext(
                    event_id="evt-ai",
                    message_id="om-ai",
                    chat_id="oc_group",
                    open_id="ou_operator",
                ),
            )

            self.assertEqual(result.status, "tool_not_allowed")
            self.assertIn("不允许", result.summary)
            self.assertFalse(result.evidence["handler_invoked"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_tool_arguments_are_validated_before_handler_runs(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                AgentToolRegistry,
                AgentToolResult,
                AgentToolSpec,
            )

            class Args(BaseModel):
                target_price: int

            invoked: list[dict[str, Any]] = []

            async def handler(arguments: Args, context: AgentToolCallContext) -> AgentToolResult:
                invoked.append(arguments.model_dump())
                return AgentToolResult(status="success", summary="ok")

            registry = AgentToolRegistry()
            registry.register_tool(
                AgentToolSpec(
                    name="test_tool",
                    description="测试工具",
                    parameters=Args.model_json_schema(),
                    risk_level="read_only",
                    safety_notes=["测试"],
                ),
                Args,
                handler,
            )

            result = await registry.execute_tool(
                "test_tool",
                {"target_price": "not-a-number"},
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )

            self.assertEqual(result.status, "invalid_arguments")
            self.assertEqual(invoked, [])
            self.assertFalse(result.evidence["handler_invoked"])

        asyncio.run(scenario())

    def test_viewer_can_only_invoke_read_only_tools(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                AgentToolRegistry,
                AgentToolResult,
                AgentToolSpec,
                EmptyToolArgs,
            )
            from app.domain.config import service as config_service

            invoked: list[str] = []

            async def handler(
                arguments: EmptyToolArgs,
                context: AgentToolCallContext,
            ) -> AgentToolResult:
                invoked.append(context.event_id)
                return AgentToolResult(status="success", summary="ok")

            registry = AgentToolRegistry()
            for name, risk_level in (
                ("viewer_read", "read_only"),
                ("viewer_state", "state_change"),
                ("viewer_approval", "approval_only"),
            ):
                registry.register_tool(
                    AgentToolSpec(
                        name=name,
                        description=name,
                        parameters=EmptyToolArgs.model_json_schema(),
                        risk_level=risk_level,
                        safety_notes=["role matrix test"],
                    ),
                    EmptyToolArgs,
                    handler,
                )

            role_resolver = mock.AsyncMock(return_value="viewer")
            with mock.patch.object(
                config_service,
                "resolve_feishu_role_for_chat",
                new=role_resolver,
            ):
                context = AgentToolCallContext(
                    event_id="evt-viewer",
                    chat_id="oc_viewer_group",
                )
                read_result = await registry.execute_tool(
                    "viewer_read",
                    {},
                    context=context,
                )
                state_result = await registry.execute_tool(
                    "viewer_state",
                    {},
                    context=context,
                )
                approval_result = await registry.execute_tool(
                    "viewer_approval",
                    {},
                    context=context,
                )

            self.assertEqual(read_result.status, "success")
            self.assertEqual(state_result.status, "tool_not_allowed")
            self.assertEqual(approval_result.status, "tool_not_allowed")
            self.assertEqual(invoked, ["evt-viewer"])
            self.assertFalse(state_result.evidence["handler_invoked"])
            self.assertFalse(approval_result.evidence["handler_invoked"])
            self.assertNotIn("role", AgentToolCallContext.model_fields)
            self.assertEqual(role_resolver.await_count, 3)

        asyncio.run(scenario())

    def test_operator_role_preserves_state_change_behavior(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                AgentToolRegistry,
                AgentToolResult,
                AgentToolSpec,
                EmptyToolArgs,
            )
            from app.domain.config import service as config_service

            invoked: list[str] = []

            async def handler(
                arguments: EmptyToolArgs,
                context: AgentToolCallContext,
            ) -> AgentToolResult:
                invoked.append(context.event_id)
                return AgentToolResult(status="success", summary="ok")

            registry = AgentToolRegistry()
            registry.register_tool(
                AgentToolSpec(
                    name="operator_state",
                    description="operator state-change compatibility",
                    parameters=EmptyToolArgs.model_json_schema(),
                    risk_level="state_change",
                    safety_notes=["role matrix test"],
                ),
                EmptyToolArgs,
                handler,
            )
            role_resolver = mock.AsyncMock(return_value="operator")
            with mock.patch.object(
                config_service,
                "resolve_feishu_role_for_chat",
                new=role_resolver,
            ):
                result = await registry.execute_tool(
                    "operator_state",
                    {},
                    context=AgentToolCallContext(
                        event_id="evt-operator",
                        chat_id="oc_operator_group",
                    ),
                )

            self.assertEqual(result.status, "success")
            self.assertEqual(invoked, ["evt-operator"])
            role_resolver.assert_awaited_once_with("oc_operator_group")

        asyncio.run(scenario())

    def test_pause_and_resume_watch_require_explicit_watch_kind(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                get_default_agent_tool_registry,
            )

            registry = get_default_agent_tool_registry()

            result = await registry.execute_tool(
                "pause_watch",
                {},
                context=AgentToolCallContext(event_id="evt-ai"),
            )

            self.assertEqual(result.status, "invalid_arguments")
            self.assertIn("watch_kind", str(result.data["validation_errors"]))
            self.assertFalse(result.evidence["handler_invoked"])

        asyncio.run(scenario())

    def test_tool_results_are_sanitized_before_returning_to_agent(self) -> None:
        async def scenario() -> None:
            from app.domain.ai_agent.registry import (
                AgentToolCallContext,
                AgentToolRegistry,
                AgentToolResult,
                AgentToolSpec,
            )

            class Args(BaseModel):
                pass

            async def handler(arguments: Args, context: AgentToolCallContext) -> AgentToolResult:
                return AgentToolResult(
                    status="success",
                    summary="ok",
                    data={
                        "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/secret",
                        "screenshot_path": "output/playwright/ebk.png",
                    },
                    evidence={
                        "persistent_profile_dir": r"C:\pan-hotel-watch-runtime\profile",
                        "webhook_called": False,
                        "ota_write_invoked": False,
                    },
                )

            registry = AgentToolRegistry()
            registry.register_tool(
                AgentToolSpec(
                    name="safe_tool",
                    description="安全工具",
                    parameters=Args.model_json_schema(),
                    risk_level="read_only",
                    safety_notes=["测试"],
                ),
                Args,
                handler,
            )

            result = await registry.execute_tool(
                "safe_tool",
                {},
                context=AgentToolCallContext(event_id="evt-ai", message_id="om-ai"),
            )
            serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

            self.assertNotIn("secret", serialized)
            self.assertNotIn("output/playwright", serialized)
            self.assertNotIn("pan-hotel-watch-runtime", serialized)
            self.assertEqual(result.data["feishu_webhook_url"], "***")
            self.assertEqual(result.data["screenshot_path"], "artifact_not_returned")
            self.assertEqual(result.evidence["persistent_profile_dir"], "profile_path_not_returned")
            self.assertFalse(result.evidence["webhook_called"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
