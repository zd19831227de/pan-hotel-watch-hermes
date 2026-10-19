from __future__ import annotations

import asyncio
import importlib
import json
import unittest
from typing import Any
from unittest import mock


class AiAgentActionToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        context_module = importlib.import_module("app.domain.ops.feishu_chat_context")
        config_service = importlib.import_module("app.domain.config.service")
        self._chat_context_patcher = mock.patch.object(
            context_module,
            "build_feishu_chat_context",
            new=mock.AsyncMock(return_value=_authorized_chat_context()),
        )
        self._chat_context_patcher.start()
        self.addCleanup(self._chat_context_patcher.stop)
        self._role_patcher = mock.patch.object(
            config_service,
            "resolve_feishu_role_for_chat",
            new=mock.AsyncMock(return_value="operator"),
        )
        self._role_patcher.start()
        self.addCleanup(self._role_patcher.stop)

    def test_explicit_watch_group_key_must_belong_to_current_chat(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            context_module = importlib.import_module("app.domain.ops.feishu_chat_context")
            repository_calls: list[str] = []

            async def fake_latest(*, watch_group_key: str) -> None:
                repository_calls.append(watch_group_key)
                return None

            with (
                mock.patch.object(
                    context_module,
                    "build_feishu_chat_context",
                    new=mock.AsyncMock(
                        return_value={
                            "binding_mode": "single_store",
                            "resolution": {
                                "status": "resolved",
                                "ebk_watch_group_key": "ebk_group_a",
                            },
                            "ebk_groups": [{"group_key": "ebk_group_a"}],
                            "competitor_groups": [{"group_key": "competitor_group_a"}],
                        }
                    ),
                ),
                mock.patch.object(
                    tools,
                    "get_latest_data_center_watch_run",
                    new=fake_latest,
                ),
            ):
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_ebk_latest_result",
                    {"watch_group_key": "ebk_group_b"},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-cross-group",
                        chat_id="oc_group_a",
                    ),
                )

            self.assertEqual(result.status, "watch_group_not_allowed")
            self.assertEqual(repository_calls, [])
            self.assertFalse(result.evidence["handler_invoked"])

        asyncio.run(scenario())

    def test_supervisor_can_use_explicit_watch_group_outside_chat_scope(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            config_service = importlib.import_module("app.domain.config.service")
            repository_calls: list[str] = []

            async def fake_latest(*, watch_group_key: str) -> None:
                repository_calls.append(watch_group_key)
                return None

            with (
                mock.patch.object(
                    config_service,
                    "resolve_feishu_role_for_chat",
                    new=mock.AsyncMock(return_value="supervisor"),
                ),
                mock.patch.object(
                    tools,
                    "get_latest_data_center_watch_run",
                    new=fake_latest,
                ),
            ):
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_ebk_latest_result",
                    {"watch_group_key": "ebk_group_outside_chat"},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-supervisor-cross-group",
                        chat_id="oc_group_a",
                    ),
                )

            self.assertEqual(result.status, "data_required")
            self.assertEqual(repository_calls, ["ebk_group_outside_chat"])

        asyncio.run(scenario())

    def test_chat_context_builder_exception_fails_closed_before_repository_access(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            context_module = importlib.import_module("app.domain.ops.feishu_chat_context")
            repository_calls: list[str] = []

            async def fake_latest(*, watch_group_key: str) -> None:
                repository_calls.append(watch_group_key)
                return None

            with (
                mock.patch.object(
                    context_module,
                    "build_feishu_chat_context",
                    new=mock.AsyncMock(side_effect=KeyError("invalid context shape")),
                ),
                mock.patch.object(
                    tools,
                    "get_latest_data_center_watch_run",
                    new=fake_latest,
                ),
            ):
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_ebk_latest_result",
                    {},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-context-error",
                        chat_id="oc_group",
                    ),
                )

            self.assertEqual(result.status, "config_required")
            self.assertEqual(repository_calls, [])
            self.assertFalse(result.evidence["repository_invoked"])

        asyncio.run(scenario())

    def test_missing_chat_id_does_not_use_default_watch_group(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            repository_calls: list[str] = []

            async def fake_latest(*, watch_group_key: str) -> None:
                repository_calls.append(watch_group_key)
                return None

            with mock.patch.object(
                tools,
                "get_latest_competitor_price_snapshot",
                new=fake_latest,
            ):
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_competitor_latest_prices",
                    {},
                    context=registry_module.AgentToolCallContext(event_id="evt-no-chat"),
                )

            self.assertEqual(result.status, "config_required")
            self.assertEqual(repository_calls, [])
            self.assertFalse(result.evidence["handler_invoked"])

        asyncio.run(scenario())

    def test_all_watch_group_tools_fail_closed_without_chat_context(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            cases = [
                ("get_ebk_latest_result", {}),
                ("get_competitor_latest_prices", {}),
                ("get_calendar_price_latest", {}),
                ("get_calendar_price_watch_status", {}),
                ("run_calendar_price_watch_once", {}),
                ("run_ebk_watch_once", {}),
                ("read_ebk_datasets", {}),
                ("analyze_competitor_price_change", {}),
                ("pause_watch", {"watch_kind": "ebk"}),
                ("resume_watch", {"watch_kind": "competitor"}),
            ]

            for tool_name, arguments in cases:
                with self.subTest(tool_name=tool_name):
                    result = await registry_module.get_default_agent_tool_registry().execute_tool(
                        tool_name,
                        arguments,
                        context=registry_module.AgentToolCallContext(event_id=f"evt-{tool_name}"),
                    )
                    self.assertEqual(result.status, "config_required")
                    self.assertFalse(result.evidence["handler_invoked"])

        asyncio.run(scenario())

    def test_all_watch_group_tools_reject_cross_chat_explicit_key(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            cases = [
                ("get_ebk_latest_result", {"watch_group_key": "cross_group"}),
                ("get_competitor_latest_prices", {"watch_group_key": "cross_group"}),
                ("get_calendar_price_latest", {"watch_group_key": "cross_group"}),
                ("get_calendar_price_watch_status", {"watch_group_key": "cross_group"}),
                ("run_calendar_price_watch_once", {"watch_group_key": "cross_group"}),
                ("run_ebk_watch_once", {"watch_group_key": "cross_group"}),
                ("read_ebk_datasets", {"watch_group_key": "cross_group"}),
                ("analyze_competitor_price_change", {"watch_group_key": "cross_group"}),
                ("pause_watch", {"watch_kind": "ebk", "watch_group_key": "cross_group"}),
                (
                    "resume_watch",
                    {"watch_kind": "competitor", "watch_group_key": "cross_group"},
                ),
            ]

            for tool_name, arguments in cases:
                with self.subTest(tool_name=tool_name):
                    result = await registry_module.get_default_agent_tool_registry().execute_tool(
                        tool_name,
                        arguments,
                        context=registry_module.AgentToolCallContext(
                            event_id=f"evt-{tool_name}",
                            chat_id="oc_group",
                        ),
                    )
                    self.assertEqual(result.status, "watch_group_not_allowed")
                    self.assertFalse(result.evidence["handler_invoked"])

        asyncio.run(scenario())

    def test_read_ebk_datasets_reads_occupancy_rate_without_feishu_push(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []
            group = tools.DataCenterWatchGroupRuntime(
                group_key="ctrip_ebooking_data_center",
                name="EBK 数据中心盯盘",
                brief_title=None,
                platform_account_id="ctrip-ebk",
                browser_session_id="browser-ebk",
                feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/secret",
                feishu_chat_id="oc_group",
                timezone="Asia/Shanghai",
                retention_days=30,
                query_interval_minutes=10,
                auto_poll_enabled=True,
                enabled=True,
                last_polled_at=None,
                datasets=[
                    tools.DataCenterWatchDatasetRuntime(
                        dataset_id="business_outline",
                        dataset_label="收益概览",
                        enabled=True,
                        push_enabled=True,
                        sort_order=0,
                    ),
                    tools.DataCenterWatchDatasetRuntime(
                        dataset_id="beneficial_data",
                        dataset_label="销售数据",
                        enabled=True,
                        push_enabled=True,
                        sort_order=1,
                    ),
                ],
            )

            async def fake_get_group(*, watch_group_key: str) -> Any:
                calls.append({"name": "get_group", "watch_group_key": watch_group_key})
                return group

            class FakeDataCenterWatchService:
                async def run_watch_group(
                    self,
                    *,
                    group: Any,
                    actor_id: str,
                    source: str,
                    push_to_feishu: bool = True,
                ) -> dict[str, Any]:
                    calls.append(
                        {
                            "name": "run_watch_group",
                            "dataset_ids": [dataset.dataset_id for dataset in group.datasets],
                            "push_enabled": [dataset.push_enabled for dataset in group.datasets],
                            "actor_id": actor_id,
                            "source": source,
                            "push_to_feishu": push_to_feishu,
                        }
                    )
                    return {
                        "status": "success",
                        "summary": "EBK 数据中心读取成功",
                        "watch_group_key": group.group_key,
                        "dataset_results": [
                            {
                                "dataset_id": "beneficial_data",
                                "dataset_label": "销售数据",
                                "status": "success",
                                "summary": "销售数据读取成功",
                                "data": {
                                    "metrics": {
                                        "occupancy_rate": {
                                            "key": "occupancy_rate",
                                            "label": "出租率",
                                            "value": "30.41%",
                                            "display_value": "30.41%",
                                        },
                                        "average_price": {
                                            "key": "average_price",
                                            "label": "平均房价",
                                            "value": "291.81",
                                            "display_value": "291.81",
                                        },
                                    }
                                },
                            }
                        ],
                        "feishu_push": {
                            "status": "skipped",
                            "summary": "本次读取未发送飞书推送。",
                        },
                    }

            original_get_group = tools.get_data_center_watch_group_runtime
            original_service = tools.DataCenterWatchService
            tools.get_data_center_watch_group_runtime = fake_get_group
            tools.DataCenterWatchService = FakeDataCenterWatchService
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "read_ebk_datasets",
                    {"metric_keys": ["出租率"]},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-ai",
                        message_id="om-ai",
                        chat_id="oc_group",
                        open_id="ou_operator",
                    ),
                )
            finally:
                tools.get_data_center_watch_group_runtime = original_get_group
                tools.DataCenterWatchService = original_service

            self.assertEqual(result.status, "success")
            self.assertEqual(calls[1]["name"], "run_watch_group")
            self.assertEqual(calls[1]["dataset_ids"], ["beneficial_data"])
            self.assertEqual(calls[1]["push_enabled"], [False])
            self.assertFalse(calls[1]["push_to_feishu"])
            self.assertEqual(result.data["requested_metrics"]["occupancy_rate"]["display_value"], "30.41%")
            self.assertEqual(result.data["feishu_push"]["status"], "skipped")
            self.assertTrue(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["webhook_called"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_read_ebk_datasets_can_read_multiple_named_datasets(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []

            group = tools.DataCenterWatchGroupRuntime(
                group_key="ctrip_ebooking_data_center",
                name="EBK 数据中心盯盘",
                brief_title=None,
                platform_account_id="ctrip-ebk",
                browser_session_id="browser-ebk",
                feishu_webhook_url=None,
                feishu_chat_id=None,
                timezone="Asia/Shanghai",
                retention_days=30,
                query_interval_minutes=10,
                auto_poll_enabled=True,
                enabled=True,
                last_polled_at=None,
                datasets=[
                    tools.DataCenterWatchDatasetRuntime(
                        dataset_id="business_outline",
                        dataset_label="收益概览",
                        enabled=True,
                        push_enabled=True,
                        sort_order=0,
                    ),
                    tools.DataCenterWatchDatasetRuntime(
                        dataset_id="beneficial_data",
                        dataset_label="销售数据",
                        enabled=True,
                        push_enabled=True,
                        sort_order=1,
                    ),
                    tools.DataCenterWatchDatasetRuntime(
                        dataset_id="promotion_registered",
                        dataset_label="已参加促销",
                        enabled=True,
                        push_enabled=True,
                        sort_order=7,
                    ),
                ],
            )

            async def fake_get_group(*, watch_group_key: str) -> Any:
                return group

            class FakeDataCenterWatchService:
                async def run_watch_group(
                    self,
                    *,
                    group: Any,
                    actor_id: str,
                    source: str,
                    push_to_feishu: bool = True,
                ) -> dict[str, Any]:
                    calls.append(
                        {
                            "dataset_ids": [dataset.dataset_id for dataset in group.datasets],
                            "push_to_feishu": push_to_feishu,
                        }
                    )
                    return {
                        "status": "success",
                        "summary": "EBK 数据中心读取成功",
                        "watch_group_key": group.group_key,
                        "dataset_results": [
                            {
                                "dataset_id": dataset.dataset_id,
                                "dataset_label": dataset.dataset_label,
                                "status": "success",
                                "data": {"metrics": {}},
                            }
                            for dataset in group.datasets
                        ],
                        "feishu_push": {"status": "skipped"},
                    }

            original_get_group = tools.get_data_center_watch_group_runtime
            original_service = tools.DataCenterWatchService
            tools.get_data_center_watch_group_runtime = fake_get_group
            tools.DataCenterWatchService = FakeDataCenterWatchService
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "read_ebk_datasets",
                    {"dataset_ids": ["收益概览", "已参加促销"]},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-ai",
                        chat_id="oc_group",
                    ),
                )
            finally:
                tools.get_data_center_watch_group_runtime = original_get_group
                tools.DataCenterWatchService = original_service

            self.assertEqual(result.status, "success")
            self.assertEqual(calls[0]["dataset_ids"], ["business_outline", "promotion_registered"])
            self.assertFalse(calls[0]["push_to_feishu"])
            self.assertEqual(
                [item["dataset_id"] for item in result.data["dataset_results"]],
                ["business_outline", "promotion_registered"],
            )

        asyncio.run(scenario())

    def test_read_ebk_datasets_rejects_unknown_dataset_without_browser_read(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")

            result = await registry_module.get_default_agent_tool_registry().execute_tool(
                "read_ebk_datasets",
                {"dataset_ids": ["not_a_dataset"]},
                context=registry_module.AgentToolCallContext(event_id="evt-ai"),
            )

            self.assertEqual(result.status, "invalid_arguments")
            self.assertFalse(result.evidence["handler_invoked"])
            self.assertFalse(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["webhook_called"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_run_ebk_watch_once_invokes_existing_scheduler_and_sanitizes_result(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []

            class FakeScheduler:
                def __init__(self, *, tick_seconds: int) -> None:
                    calls.append({"name": "scheduler_init", "tick_seconds": tick_seconds})

                async def run_watch_group_now(self, *, watch_group_key: str) -> dict[str, Any]:
                    calls.append({"name": "run_watch_group_now", "watch_group_key": watch_group_key})
                    return {
                        "status": "success",
                        "summary": "EBK 数据中心读取完成。",
                        "watch_group_key": watch_group_key,
                        "persistence": {"run_id": 101},
                        "push": {
                            "status": "success",
                            "feishu_webhook_url": (
                                "https://open.feishu.cn/open-apis/bot/v2/hook/secret"
                            ),
                        },
                        "dataset_results": [
                            {
                                "dataset_id": "business_outline",
                                "status": "success",
                                "evidence": {"screenshot_path": "output/playwright/ebk.png"},
                            }
                        ],
                    }

            original = tools.CtripEbookingDataCenterWatchScheduler
            tools.CtripEbookingDataCenterWatchScheduler = FakeScheduler
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "run_ebk_watch_once",
                    {},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-ai",
                        message_id="om-ai",
                        chat_id="oc_group",
                    ),
                )
            finally:
                tools.CtripEbookingDataCenterWatchScheduler = original

            serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            self.assertEqual(result.status, "success")
            self.assertEqual(
                calls,
                [
                    {"name": "scheduler_init", "tick_seconds": 1},
                    {
                        "name": "run_watch_group_now",
                        "watch_group_key": "ctrip_ebooking_data_center",
                    },
                ],
            )
            self.assertIn("EBK 数据中心读取完成", result.summary)
            self.assertEqual(result.data["watch_group_key"], "ctrip_ebooking_data_center")
            self.assertEqual(result.data["persistence"]["run_id"], 101)
            self.assertNotIn("secret", serialized)
            self.assertNotIn("output/playwright", serialized)
            self.assertTrue(result.evidence["browser_read_invoked"])
            self.assertTrue(result.evidence["webhook_may_be_called_by_scheduler"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_run_ebk_watch_once_returns_not_found_without_retrying_or_writing(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")

            class FakeScheduler:
                def __init__(self, *, tick_seconds: int) -> None:
                    pass

                async def run_watch_group_now(self, *, watch_group_key: str) -> dict[str, Any]:
                    return {
                        "status": "not_found",
                        "summary": "未找到 EBK 数据中心监控组。",
                        "watch_group_key": watch_group_key,
                    }

            original = tools.CtripEbookingDataCenterWatchScheduler
            tools.CtripEbookingDataCenterWatchScheduler = FakeScheduler
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "run_ebk_watch_once",
                    {"watch_group_key": "missing_group"},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-ai",
                        chat_id="oc_group",
                    ),
                )
            finally:
                tools.CtripEbookingDataCenterWatchScheduler = original

            self.assertEqual(result.status, "not_found")
            self.assertIn("未找到", result.summary)
            self.assertEqual(result.data["watch_group_key"], "missing_group")
            self.assertTrue(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["ota_write_invoked"])
            self.assertFalse(result.evidence["pricing_execution_invoked"])

        asyncio.run(scenario())

    def test_pause_watch_updates_only_selected_ebk_watch_group(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []

            async def fake_update(*, watch_group_key: str, patch: Any) -> dict[str, Any]:
                calls.append(
                    {
                        "name": "update_ebk",
                        "watch_group_key": watch_group_key,
                        "enabled": patch.enabled,
                        "auto_poll_enabled": patch.auto_poll_enabled,
                    }
                )
                return {
                    "group_key": watch_group_key,
                    "name": "EBK 数据中心盯盘",
                    "enabled": False,
                    "auto_poll_enabled": False,
                }

            original = tools.update_data_center_watch_group
            tools.update_data_center_watch_group = fake_update
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "pause_watch",
                    {"watch_kind": "ebk", "watch_group_key": "ctrip_ebooking_data_center"},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-ai",
                        chat_id="oc_group",
                    ),
                )
            finally:
                tools.update_data_center_watch_group = original

            self.assertEqual(result.status, "success")
            self.assertEqual(calls[0]["name"], "update_ebk")
            self.assertFalse(calls[0]["enabled"])
            self.assertFalse(calls[0]["auto_poll_enabled"])
            self.assertEqual(result.data["watch_kind"], "ebk")
            self.assertFalse(result.data["watch_group"]["enabled"])
            self.assertFalse(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_resume_watch_updates_only_selected_competitor_watch_group(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []

            async def fake_update(*, watch_group_key: str, patch: Any) -> dict[str, Any]:
                calls.append(
                    {
                        "name": "update_competitor",
                        "watch_group_key": watch_group_key,
                        "enabled": patch.enabled,
                        "auto_poll_enabled": patch.auto_poll_enabled,
                    }
                )
                return {
                    "group_key": watch_group_key,
                    "name": "竞品盯盘",
                    "enabled": True,
                    "auto_poll_enabled": True,
                }

            original = tools.update_competitor_watch_group
            tools.update_competitor_watch_group = fake_update
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "resume_watch",
                    {"watch_kind": "competitor", "watch_group_key": "default_competitor_watch"},
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-ai",
                        chat_id="oc_group",
                    ),
                )
            finally:
                tools.update_competitor_watch_group = original

            self.assertEqual(result.status, "success")
            self.assertEqual(calls[0]["name"], "update_competitor")
            self.assertTrue(calls[0]["enabled"])
            self.assertTrue(calls[0]["auto_poll_enabled"])
            self.assertEqual(result.data["watch_kind"], "competitor")
            self.assertTrue(result.data["watch_group"]["auto_poll_enabled"])
            self.assertFalse(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_create_manual_pricing_approval_generates_card_without_execution(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []
            created_chat_ids: list[str | None] = []

            async def fake_preview(**kwargs: Any) -> Any:
                calls.append({"name": "preview", **kwargs})
                return _FakePreview(
                    status="preview_ready",
                    summary="手动调价预览已生成。",
                    next_action=None,
                    can_auto_write_price=True,
                    connection_mode="non_direct",
                    preview_id=321,
                )

            class FakeClient:
                def __init__(
                    self,
                    *,
                    chat_id: str | None = None,
                    app_profile: str | None = None,
                ) -> None:
                    _ = app_profile
                    created_chat_ids.append(chat_id)

                async def reply_interactive_card(
                    self,
                    message_id: str,
                    card: dict[str, Any],
                    *,
                    fallback_text: str | None = None,
                ) -> dict[str, Any]:
                    calls.append(
                        {
                            "name": "reply_interactive_card",
                            "message_id": message_id,
                            "card": card,
                            "fallback_text": fallback_text,
                        }
                    )
                    return {"status": "success", "summary": "sent"}

            original_preview = tools.create_manual_pricing_preview
            original_account = tools.get_pricing_browser_session_for_feishu_chat_id
            original_client = tools.FeishuBotClient
            tools.create_manual_pricing_preview = fake_preview
            tools.get_pricing_browser_session_for_feishu_chat_id = lambda chat_id: {
                "browser_session_id": "ebk-pricing",
                "connection_mode": "non_direct",
            }
            tools.FeishuBotClient = FakeClient
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "create_manual_pricing_approval",
                    {
                        "room_type": "高级大床房",
                        "date_label": "today",
                        "target_price": 420,
                    },
                    context=registry_module.AgentToolCallContext(
                        event_id="evt-ai",
                        message_id="om-ai",
                        chat_id="oc_group",
                        open_id="ou_operator",
                    ),
                )
            finally:
                tools.create_manual_pricing_preview = original_preview
                tools.get_pricing_browser_session_for_feishu_chat_id = original_account
                tools.FeishuBotClient = original_client

            self.assertEqual(result.status, "approval_card_replied")
            self.assertEqual(created_chat_ids, ["oc_group"])
            self.assertEqual(calls[0]["name"], "preview")
            self.assertEqual(calls[0]["room_type"], "高级大床房")
            self.assertEqual(calls[0]["date_label"], "today")
            self.assertEqual(calls[0]["target_price"], 420)
            self.assertEqual(calls[1]["name"], "reply_interactive_card")
            self.assertEqual(calls[1]["message_id"], "om-ai")
            self.assertFalse(result.evidence["execute_triggered"])
            self.assertFalse(result.evidence["ota_write_invoked"])
            self.assertTrue(result.evidence["approval_card_sent"])

        asyncio.run(scenario())

    def test_create_manual_pricing_approval_requires_feishu_chat_context(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")

            result = await registry_module.get_default_agent_tool_registry().execute_tool(
                "create_manual_pricing_approval",
                {
                    "room_type": "高级大床房",
                    "date_label": "today",
                    "target_price": 420,
                },
                context=registry_module.AgentToolCallContext(
                    event_id="evt-ai",
                    message_id="om-ai",
                    chat_id=None,
                    open_id="ou_operator",
                ),
            )

            self.assertEqual(result.status, "config_required")
            self.assertIn("飞书群", result.summary)
            self.assertFalse(result.evidence["execute_triggered"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())


def _reload(module_name: str):
    return importlib.reload(importlib.import_module(module_name))


def _authorized_chat_context() -> dict[str, Any]:
    return {
        "binding_mode": "single_store",
        "resolution": {
            "status": "resolved",
            "ebk_watch_group_key": "ctrip_ebooking_data_center",
            "competitor_watch_group_key": "default_competitor_watch",
        },
        "ebk_groups": [
            {"group_key": "ctrip_ebooking_data_center"},
            {"group_key": "missing_group"},
        ],
        "competitor_groups": [{"group_key": "default_competitor_watch"}],
    }


class _FakePreview:
    def __init__(
        self,
        *,
        status: str,
        summary: str,
        next_action: str | None,
        can_auto_write_price: bool,
        connection_mode: str,
        preview_id: int,
    ) -> None:
        self.status = status
        self.summary = summary
        self.next_action = next_action
        self.can_auto_write_price = can_auto_write_price
        self.connection_mode = connection_mode
        self.preview_id = preview_id

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "next_action": self.next_action,
            "can_auto_write_price": self.can_auto_write_price,
            "connection_mode": self.connection_mode,
            "preview_id": self.preview_id,
            "watch_group_key": "default_competitor_watch",
            "write_policy_message": "非直连账号可在 Feishu 审批通过后继续调价流程",
            "preview_candidate": {
                "actions": [
                    {
                        "room_type": "高级大床房",
                        "date_label": "today",
                        "target_price_yuan": 420,
                    }
                ]
            },
            "mode": mode,
        }


if __name__ == "__main__":
    unittest.main()
