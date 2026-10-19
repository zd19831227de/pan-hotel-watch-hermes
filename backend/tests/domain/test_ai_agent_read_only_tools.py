from __future__ import annotations

import asyncio
import importlib
import json
import unittest
from typing import Any
from unittest import mock


class AiAgentReadOnlyToolsTests(unittest.TestCase):
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

    def test_get_ebk_latest_result_reads_persisted_snapshot_only(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []

            async def fake_latest(*, watch_group_key: str) -> dict[str, Any]:
                calls.append({"name": "latest_ebk", "watch_group_key": watch_group_key})
                return {
                    "watch_group_key": watch_group_key,
                    "run": {
                        "id": 88,
                        "status": "success",
                        "summary": "读取成功",
                        "dataset_count": 2,
                        "success_count": 1,
                        "failed_count": 1,
                        "human_required_count": 0,
                        "captured_at": "2026-06-23T08:05:00",
                        "browser_session_id": "browser-ebk",
                        "push_result": {
                            "status": "success",
                            "feishu_webhook_url": (
                                "https://open.feishu.cn/open-apis/bot/v2/hook/secret"
                            ),
                        },
                    },
                    "dataset_results": [
                        {
                            "id": 1,
                            "run_id": 88,
                            "dataset_id": "business_outline",
                            "dataset_label": "收益概览",
                            "status": "success",
                            "summary": "收益概览读取成功",
                            "captured_at": "2026-06-23T08:05:00",
                            "evidence": {"screenshot_path": "output/playwright/ebk.png"},
                        },
                        {
                            "id": 2,
                            "run_id": 88,
                            "dataset_id": "promotion_effect",
                            "dataset_label": "促销效果",
                            "status": "failed",
                            "failure_reason": "页面结构需要人工确认",
                            "captured_at": "2026-06-23T08:05:00",
                        },
                    ],
                }

            original = tools.get_latest_data_center_watch_run
            tools.get_latest_data_center_watch_run = fake_latest
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_ebk_latest_result",
                    {},
                    context=_authorized_tool_context(registry_module),
                )
            finally:
                tools.get_latest_data_center_watch_run = original

            serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            self.assertEqual(result.status, "success")
            self.assertEqual(calls, [{"name": "latest_ebk", "watch_group_key": "ctrip_ebooking_data_center"}])
            self.assertIn("EBK 最新读取", result.summary)
            self.assertEqual(result.data["run"]["success_count"], 1)
            self.assertEqual(result.data["dataset_results"][0]["dataset_label"], "收益概览")
            self.assertNotIn("browser-ebk", serialized)
            self.assertNotIn("secret", serialized)
            self.assertNotIn("output/playwright", serialized)
            self.assertFalse(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["webhook_called"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_get_ebk_latest_result_handles_empty_history(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")

            async def fake_latest(*, watch_group_key: str) -> None:
                return None

            original = tools.get_latest_data_center_watch_run
            tools.get_latest_data_center_watch_run = fake_latest
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_ebk_latest_result",
                    {"watch_group_key": "custom_ebk"},
                    context=_authorized_tool_context(registry_module),
                )
            finally:
                tools.get_latest_data_center_watch_run = original

            self.assertEqual(result.status, "data_required")
            self.assertIn("还没有", result.summary)
            self.assertEqual(result.data["watch_group_key"], "custom_ebk")
            self.assertFalse(result.evidence["browser_read_invoked"])

        asyncio.run(scenario())

    def test_get_competitor_latest_prices_compacts_persisted_prices(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")

            async def fake_latest(*, watch_group_key: str) -> dict[str, Any]:
                return {
                    "watch_group_key": watch_group_key,
                    "trusted_source": "room_rate_dom_display_price",
                    "run": {
                        "id": 9,
                        "status": "success",
                        "summary": "竞品读取成功",
                        "captured_at": "2026-06-23T08:10:00",
                        "push_result": {
                            "feishu_webhook_url": (
                                "https://open.feishu.cn/open-apis/bot/v2/hook/secret"
                            )
                        },
                    },
                    "hotels": [
                        {
                            "hotel_config": {
                                "id": 10,
                                "role": "own",
                                "alias": "本店",
                                "hotel_name": "本店",
                                "detail_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=1",
                            },
                            "snapshots": {
                                "today": {
                                    "snapshot_id": 20,
                                    "checkin_date": "2026-06-23",
                                    "checkout_date": "2026-06-24",
                                    "status": "success",
                                    "rates": [
                                        {
                                            "room_type": "高级大床房",
                                            "breakfast_kind": "none",
                                            "price_amount_yuan": 420,
                                            "is_trusted": True,
                                            "source": "room_rate_dom_display_price",
                                        }
                                    ],
                                }
                            },
                        }
                    ],
                }

            original = tools.get_latest_competitor_price_snapshot
            tools.get_latest_competitor_price_snapshot = fake_latest
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_competitor_latest_prices",
                    {},
                    context=_authorized_tool_context(registry_module),
                )
            finally:
                tools.get_latest_competitor_price_snapshot = original

            serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            self.assertEqual(result.status, "success")
            self.assertIn("竞品最新价格", result.summary)
            self.assertEqual(result.data["hotel_count"], 1)
            self.assertEqual(result.data["trusted_rate_count"], 1)
            self.assertEqual(result.data["hotels"][0]["snapshots"][0]["rates"][0]["price_amount_yuan"], 420)
            self.assertNotIn("hotelId=1", serialized)
            self.assertNotIn("secret", serialized)
            self.assertFalse(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["webhook_called"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_get_calendar_price_latest_compacts_persisted_matrix_read_only(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []

            async def fake_latest(*, watch_group_key: str) -> dict[str, Any]:
                calls.append({"watch_group_key": watch_group_key})
                return {
                    "status": "alert",
                    "watch_group_key": watch_group_key,
                    "future_days": 15,
                    "trusted_source": "calendar_dom_cell_price",
                    "run": {
                        "id": 88,
                        "status": "alert",
                        "summary": "日历价提醒",
                        "captured_at": "2026-07-06T12:00:00+08:00",
                        "push_result": {
                            "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/secret"
                        },
                    },
                    "dates": [
                        {
                            "checkin_date": "2026-07-09",
                            "own": {"hotel_alias": "本店", "price_amount_yuan": 411, "status": "success"},
                            "competitors": [
                                {"hotel_alias": "竞品A", "price_amount_yuan": 398, "status": "success"}
                            ],
                            "alerts": [{"alert_type": "own_price_high", "gap_yuan": 13}],
                        }
                    ],
                }

            original = tools.get_latest_calendar_price_matrix
            tools.get_latest_calendar_price_matrix = fake_latest
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_calendar_price_latest",
                    {},
                    context=_authorized_tool_context(registry_module),
                )
            finally:
                tools.get_latest_calendar_price_matrix = original

            serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            self.assertEqual(result.status, "alert")
            self.assertEqual(calls, [{"watch_group_key": "default_competitor_watch"}])
            self.assertIn("日历价", result.summary)
            self.assertEqual(result.data["future_days"], 15)
            self.assertEqual(result.data["alert_count"], 1)
            self.assertIn("2026-07-09", result.data["calendar_rows"][0]["field"])
            self.assertIn("竞品A", result.data["calendar_rows"][0]["value"])
            self.assertNotIn("secret", serialized)
            self.assertNotIn('"id": 88', serialized)
            self.assertFalse(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["webhook_called"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_calendar_price_watch_status_reports_config_and_never_invokes_pricing(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")

            async def fake_config(*, watch_group_key: str) -> dict[str, Any]:
                return {
                    "watch_group_key": watch_group_key,
                    "future_days": 15,
                    "auto_poll_enabled": True,
                    "schedule_interval_minutes": 30,
                    "last_polled_at": "2026-07-08T10:31:00+08:00",
                }

            async def fake_matrix(*, watch_group_key: str) -> dict[str, Any]:
                return {
                    "status": "success",
                    "watch_group_key": watch_group_key,
                    "run": {
                        "status": "success",
                        "captured_at": "2026-07-08T10:31:00+08:00",
                    },
                    "dates": [],
                }

            def fake_scheduler() -> dict[str, Any]:
                return {"status": "running", "heartbeat_status": "healthy"}

            original_config = tools.get_calendar_price_watch_config
            original_matrix = tools.get_latest_calendar_price_matrix
            original_scheduler = tools.get_calendar_price_scheduler_status
            tools.get_calendar_price_watch_config = fake_config
            tools.get_latest_calendar_price_matrix = fake_matrix
            tools.get_calendar_price_scheduler_status = fake_scheduler
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_calendar_price_watch_status",
                    {"watch_group_key": "default_competitor_watch"},
                    context=_authorized_tool_context(
                        registry_module,
                        open_id="ou_operator",
                    ),
                )
            finally:
                tools.get_calendar_price_watch_config = original_config
                tools.get_latest_calendar_price_matrix = original_matrix
                tools.get_calendar_price_scheduler_status = original_scheduler

            self.assertEqual(result.status, "success")
            self.assertIn("30", result.summary)
            self.assertTrue(result.evidence["read_only"])
            self.assertTrue(result.data["auto_poll_enabled"])
            self.assertEqual(result.data["schedule_interval_minutes"], 30)
            self.assertFalse(result.evidence["pricing_execution_invoked"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_run_calendar_price_watch_once_invokes_scheduler_without_feishu_push_or_pricing(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []

            class FakeScheduler:
                def __init__(self, *, tick_seconds: int = 60) -> None:
                    self.tick_seconds = tick_seconds

                async def run_watch_group_now(
                    self,
                    watch_group_key: str,
                    *,
                    push_to_feishu: bool = True,
                    actor_id: str = "operator",
                    source: str = "api",
                ) -> dict[str, Any]:
                    calls.append(
                        {
                            "tick_seconds": self.tick_seconds,
                            "watch_group_key": watch_group_key,
                            "push_to_feishu": push_to_feishu,
                            "actor_id": actor_id,
                            "source": source,
                        }
                    )
                    return {
                        "watch_group_key": watch_group_key,
                        "status": "success",
                        "summary": "日历价读取完成",
                        "future_days": 15,
                        "alert_count": 0,
                        "run_id": 99,
                        "feishu_push": {"status": "skipped"},
                    }

            original = tools.CalendarPriceWatchScheduler
            tools.CalendarPriceWatchScheduler = FakeScheduler
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "run_calendar_price_watch_once",
                    {"watch_group_key": "watch_a"},
                    context=_authorized_tool_context(
                        registry_module,
                        open_id="ou_operator",
                    ),
                )
            finally:
                tools.CalendarPriceWatchScheduler = original

            serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            self.assertEqual(result.status, "success")
            self.assertEqual(
                calls,
                [
                    {
                        "tick_seconds": 1,
                        "watch_group_key": "watch_a",
                        "push_to_feishu": False,
                        "actor_id": "ou_operator",
                        "source": "feishu-ai-agent",
                    }
                ],
            )
            self.assertNotIn("run_id", serialized)
            self.assertTrue(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["webhook_called"])
            self.assertFalse(result.evidence["push_to_feishu"])
            self.assertFalse(result.evidence["pricing_execution_invoked"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_analyze_competitor_price_change_reads_latest_alert_without_evaluating(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")
            calls: list[dict[str, Any]] = []

            async def fake_latest_alert(*, watch_group_key: str | None) -> dict[str, Any]:
                calls.append({"watch_group_key": watch_group_key})
                return {
                    "id": 99,
                    "watch_group_key": watch_group_key,
                    "status": "suggestion_ready",
                    "summary": "检测到 1 个超过阈值的竞品价格变化。",
                    "changed_count": 1,
                    "suggestion_count": 1,
                    "compared_key_count": 3,
                    "connection_mode": "direct",
                    "can_auto_write_price": False,
                    "write_policy_message": "直连账号仅允许生成建议，不允许自动化改价。",
                    "current_run_id": 8,
                    "previous_run_id": 7,
                    "suggestions": [
                        {
                            "hotel_name": "竞品A",
                            "room_type": "高级大床房",
                            "recommended_price_yuan": 450,
                        }
                    ],
                    "evidence": {
                        "feishu_webhook_url": (
                            "https://open.feishu.cn/open-apis/bot/v2/hook/secret"
                        )
                    },
                }

            original = tools.get_latest_price_change_alert
            tools.get_latest_price_change_alert = fake_latest_alert
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "analyze_competitor_price_change",
                    {"watch_group_key": "watch_a"},
                    context=_authorized_tool_context(registry_module),
                )
            finally:
                tools.get_latest_price_change_alert = original

            serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            self.assertEqual(result.status, "suggestion_ready")
            self.assertEqual(calls, [{"watch_group_key": "watch_a"}])
            self.assertEqual(result.data["changed_count"], 1)
            self.assertEqual(result.data["suggestions"][0]["recommended_price_yuan"], 450)
            self.assertNotIn("current_run_id", serialized)
            self.assertNotIn("secret", serialized)
            self.assertFalse(result.evidence["price_change_evaluation_invoked"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())

    def test_get_watch_scheduler_status_reads_runtime_health_only(self) -> None:
        async def scenario() -> None:
            registry_module = _reload("app.domain.ai_agent.registry")
            tools = _reload("app.domain.ai_agent.tools")

            async def fake_health() -> dict[str, Any]:
                return {
                    "checked_at": "2026-06-23T08:20:00+00:00",
                    "summary": {
                        "status": "attention_required",
                        "attention_modules": ["feishu_listener"],
                    },
                    "modules": {
                        "feishu_listener": {
                            "module_key": "feishu_listener",
                            "display_name": "Feishu bot listener",
                            "freshness_status": "missing",
                            "process_confirmed": False,
                            "last_tick_summary": {
                                "webhook_url": (
                                    "https://open.feishu.cn/open-apis/bot/v2/hook/secret"
                                )
                            },
                        }
                    },
                    "evidence": {
                        "browser_read_invoked": False,
                        "webhook_called": False,
                        "ota_write_invoked": False,
                    },
                }

            original = tools.get_runtime_health
            tools.get_runtime_health = fake_health
            try:
                result = await registry_module.get_default_agent_tool_registry().execute_tool(
                    "get_watch_scheduler_status",
                    {},
                    context=registry_module.AgentToolCallContext(event_id="evt-ai"),
                )
            finally:
                tools.get_runtime_health = original

            serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            self.assertEqual(result.status, "attention_required")
            self.assertIn("需要关注", result.summary)
            self.assertEqual(result.data["summary"]["attention_modules"], ["feishu_listener"])
            self.assertNotIn("secret", serialized)
            self.assertFalse(result.evidence["browser_read_invoked"])
            self.assertFalse(result.evidence["webhook_called"])
            self.assertFalse(result.evidence["ota_write_invoked"])

        asyncio.run(scenario())


def _reload(module_name: str):
    return importlib.reload(importlib.import_module(module_name))


def _authorized_tool_context(registry_module, *, open_id: str | None = None):
    return registry_module.AgentToolCallContext(
        event_id="evt-ai",
        chat_id="oc_authorized_read_only_group",
        open_id=open_id,
    )


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
            {"group_key": "custom_ebk"},
        ],
        "competitor_groups": [
            {"group_key": "default_competitor_watch"},
            {"group_key": "watch_a"},
        ],
    }


if __name__ == "__main__":
    unittest.main()
