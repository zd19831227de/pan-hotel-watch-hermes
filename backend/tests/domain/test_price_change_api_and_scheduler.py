from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.tests._service_auth import service_token_headers


class PriceChangeApiAndSchedulerTests(unittest.TestCase):
    def test_pricing_strategy_api_exposes_suggestion_only_config(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = TestClient(app)

            response = client.get("/api/v1/pricing-strategy/config")
            self.assertEqual(response.status_code, 200)
            strategy = response.json()["strategy"]
            self.assertEqual(strategy["change_trigger_mode"], "percent")
            self.assertIn("recommendation_step_yuan", strategy)
            self.assertIn("max_suggested_change_yuan", strategy)
            self.assertIn("allow_downward_suggestion", strategy)
            serialized = str(strategy).lower()
            for forbidden in ("approval", "preflight", "dry_run", "execute", "ota"):
                self.assertNotIn(forbidden, serialized)

            response = client.patch(
                "/api/v1/pricing-strategy/config",
                json={
                    "change_trigger_mode": "amount",
                    "min_delta_yuan": 15,
                    "max_delta_yuan": 120,
                    "recommendation_step_yuan": 5,
                    "max_suggested_change_yuan": 25,
                    "allow_downward_suggestion": True,
                },
            )
            self.assertEqual(response.status_code, 200)
            strategy = response.json()["strategy"]
            self.assertEqual(strategy["change_trigger_mode"], "amount")
            self.assertEqual(strategy["recommendation_step_yuan"], 5)
            self.assertTrue(strategy["allow_downward_suggestion"])

    def test_price_change_alert_api_evaluates_and_lists_records(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> int:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                await _save_price_run(repository, captured_at="2026-06-18T08:00:00", competitor_none=460)
                current = await _save_price_run(
                    repository,
                    captured_at="2026-06-18T10:00:00",
                    competitor_none=400,
                )
                return int(current.run_id)

            current_run_id = asyncio.run(seed())
            app = _reload_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/price-change-alerts/evaluate",
                json={
                    "watch_group_key": "ops_watch",
                    "current_run_id": current_run_id,
                    "push_to_feishu": False,
                },
            )
            self.assertEqual(response.status_code, 200)
            alert = response.json()["alert"]
            self.assertEqual(alert["status"], "suggestion_ready")
            self.assertEqual(alert["suggestion_count"], 1)
            self.assertIsNone(alert["feishu_push"])

            latest = client.get("/api/v1/price-change-alerts/latest?watch_group_key=ops_watch")
            self.assertEqual(latest.status_code, 200)
            self.assertEqual(latest.json()["alert"]["status"], "suggestion_ready")

            records = client.get("/api/v1/price-change-alerts?watch_group_key=ops_watch")
            self.assertEqual(records.status_code, 200)
            self.assertGreaterEqual(len(records.json()["alerts"]), 1)

    def test_price_change_evaluate_api_uses_request_actor_id(self) -> None:
        app = _reload_app()
        calls: list[dict[str, Any]] = []

        async def fake_evaluate(**kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "status": "no_trigger",
                    "summary": "ok",
                    "watch_group_key": kwargs["watch_group_key"],
                }
            )

        with patch("app.api.v1.price_change_alerts.evaluate_price_change_alert", fake_evaluate):
            response = TestClient(app).post(
                "/api/v1/price-change-alerts/evaluate",
                json={
                    "watch_group_key": "ops_watch",
                    "push_to_feishu": False,
                    "actor_id": "ou_operator_1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0]["actor_id"], "ou_operator_1")

    def test_pricing_strategy_api_uses_request_actor_id(self) -> None:
        app = _reload_app()
        calls: list[dict[str, Any]] = []

        async def fake_update(**kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "strategy_key": "default",
                    "updated_by": kwargs["actor_id"],
                }
            )

        with patch("app.api.v1.pricing_strategy.update_pricing_strategy_config", fake_update):
            response = TestClient(app).patch(
                "/api/v1/pricing-strategy/config",
                json={"enabled": True, "actor_id": "ou_operator_2"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0]["actor_id"], "ou_operator_2")

    def test_competitor_run_api_uses_request_actor_id(self) -> None:
        app = _reload_app()
        calls: list[dict[str, Any]] = []

        class FakeScheduler:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            async def run_watch_group_now(self, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs)
                return {"status": "success", "summary": "ok"}

        with patch("app.api.v1.competitor_watch.CompetitorPriceScheduler", FakeScheduler):
            response = TestClient(app).post(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/ops_watch/run-now?push_to_feishu=false",
                headers=service_token_headers(),
                json={"actor_id": "ou_operator_3"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0]["actor_id"], "ou_operator_3")

    def test_scheduler_evaluates_alert_after_persistence_without_default_push(self) -> None:
        from app.domain.competitor_prices import scheduler

        async def fake_resolve_targets(**kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                own_hotel_url="https://hotels.ctrip.com/hotels/detail/?hotelId=1",
                competitor_urls=["https://hotels.ctrip.com/hotels/detail/?hotelId=2"],
                own_hotel_alias="本店",
                competitor_aliases=["竞品 A"],
            )

        async def fake_save_result(**kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                model_dump=lambda mode: {"status": "success", "run_id": 42},
                run_id=42,
            )

        async def fake_update_push(**kwargs: Any) -> None:
            return None

        evaluate_calls: list[dict[str, Any]] = []
        orchestration_calls: list[dict[str, Any]] = []

        async def fake_evaluate(**kwargs: Any) -> SimpleNamespace:
            evaluate_calls.append(kwargs)
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "status": "suggestion_ready",
                    "summary": "ok",
                    "alert_record_id": 101,
                    "watch_group_key": "ops_watch",
                    "suggestions": [{"recommended_price_yuan": 450}],
                    "feishu_push": None,
                }
            )

        async def fake_orchestrate(**kwargs: Any) -> dict[str, Any]:
            orchestration_calls.append(kwargs)
            return {
                "outcome": "reference_card_sent",
                "preview_status": "preview_ready",
                "approval_status": "success",
                "preview_id": 201,
            }

        class FakeService:
            async def read_competitor_prices(self, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(status="success", summary="读取成功", data={"snapshots": []}, evidence={})

        with (
            patch(
                "app.domain.competitor_prices.scheduler.get_latest_competitor_price_run_meta",
                return_value=None,
            ),
            patch(
                "app.domain.competitor_prices.scheduler.resolve_competitor_browser_binding_for_group",
                new=AsyncMock(return_value=SimpleNamespace(
                    ok=True,
                    status="ready",
                    summary="ok",
                    evidence={},
                    next_action=None,
                    selection=SimpleNamespace(
                        platform_account_id="ctrip-public",
                        browser_session_id="browser-public",
                        evidence=lambda: {"selection": "configured"},
                    ),
                )),
            ),
            patch(
                "app.domain.competitor_prices.scheduler.resolve_configured_competitor_targets",
                fake_resolve_targets,
            ),
            patch(
                "app.domain.competitor_prices.scheduler.get_ctrip_ebooking_service",
                return_value=FakeService(),
            ),
            patch(
                "app.domain.competitor_prices.scheduler.save_ctrip_competitor_price_result",
                fake_save_result,
            ),
            patch(
                "app.domain.competitor_prices.scheduler.update_competitor_price_run_push_result",
                fake_update_push,
            ),
            patch(
                "app.domain.competitor_prices.scheduler.evaluate_price_change_alert",
                fake_evaluate,
            ),
            patch(
                "app.domain.competitor_prices.scheduler.try_auto_preview_and_approval_for_alert",
                fake_orchestrate,
            ),
        ):
            result = asyncio.run(
                scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    {
                        "group_key": "ops_watch",
                        "platform_account_id": "ctrip-public",
                        "browser_session_id": "browser-public",
                    },
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )
            )

        self.assertEqual(len(evaluate_calls), 1)
        self.assertEqual(evaluate_calls[0]["current_run_id"], 42)
        self.assertFalse(evaluate_calls[0]["push_to_feishu"])
        self.assertEqual(len(orchestration_calls), 1)
        self.assertEqual(orchestration_calls[0]["watch_group_key"], "ops_watch")
        self.assertEqual(orchestration_calls[0]["actor_id"], "operator")
        self.assertEqual(result["price_change_alert"]["status"], "suggestion_ready")
        self.assertEqual(
            result["price_change_alert"]["pricing_approval_auto_flow"]["outcome"],
            "reference_card_sent",
        )

    def test_auto_pricing_orchestration_sends_approval_card_to_group_chat(self) -> None:
        async def scenario() -> None:
            orchestration = _reload_module(
                "app.domain.competitor_prices.price_change_pricing_orchestration"
            )
            sent: list[dict[str, Any]] = []

            async def fake_group(**kwargs: Any) -> dict[str, Any]:
                self.assertTrue(kwargs["include_secrets"])
                return {
                    "group_key": "ops_watch",
                    "feishu_chat_id": "oc_group",
                }

            async def fake_reference_preview(**kwargs: Any) -> Any:
                return SimpleNamespace(
                    status="reference_ready",
                    summary="调价参考已生成。",
                    preview_id=201,
                    model_dump=lambda mode: {
                        "status": "reference_ready",
                        "summary": "调价参考已生成。",
                        "preview_id": 201,
                        "id": 201,
                        "watch_group_key": "ops_watch",
                        "can_auto_write_price": True,
                        "connection_mode": "non_direct",
                        "write_policy_message": "非直连账号可在 Feishu 审批通过后继续调价流程",
                        "preview_candidate": {
                            "source": "competitor_price_alert_reference",
                            "requires_operator_target_price": True,
                            "actions": [
                                {
                                    "room_type": "高级大床房",
                                    "date_label": "today",
                                    "checkin_date": "2026-06-24",
                                    "breakfast_kind": "none",
                                    "current_price_yuan": 420,
                                    "recommended_adjustment_yuan": -30,
                                    "proposed_price_yuan": 390,
                                    "trigger_reason": "竞品降价超过阈值。",
                                }
                            ],
                        },
                    },
                )

            created_chat_ids: list[str | None] = []

            class FakeClient:
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
                    return {"status": "success", "summary": "sent", "evidence": {"secret": "x"}}

            orchestration.get_competitor_watch_group_detail = fake_group
            orchestration.create_pricing_reference_preview_from_alert = fake_reference_preview
            orchestration.FeishuBotClient = FakeClient

            result = await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record={
                    "status": "suggestion_ready",
                    "alert_record_id": 101,
                    "watch_group_key": "ops_watch",
                    "connection_mode": "non_direct",
                    "can_auto_write_price": True,
                    "write_policy_message": "非直连账号可在 Feishu 审批通过后继续调价流程",
                    "suggestions": [{"recommended_price_yuan": 390}],
                },
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

            self.assertEqual(result["outcome"], "reference_card_sent")
            self.assertEqual(result["preview_status"], "reference_ready")
            self.assertEqual(result["preview_id"], 201)
            self.assertEqual(created_chat_ids, ["oc_group"])
            self.assertEqual(sent[0]["chat_id"], "oc_group")
            card_actions = sent[0]["card"]["elements"][-1]["actions"]
            action_values = [item["value"] for item in card_actions]
            self.assertEqual([item.get("action") for item in action_values], ["custom_price", "defer"])
            self.assertEqual(action_values[0]["preview_id"], 201)
            self.assertEqual(action_values[0]["pricing_preview_record_id"], 201)
            self.assertNotIn("approve", str(action_values))
            self.assertNotIn("secret", str(result))

        asyncio.run(scenario())

    def test_auto_pricing_orchestration_does_not_create_executable_preview(self) -> None:
        async def scenario() -> None:
            orchestration = _reload_module(
                "app.domain.competitor_prices.price_change_pricing_orchestration"
            )
            sent: list[dict[str, Any]] = []
            executable_preview_called = False
            reference_preview_called = False

            async def fake_group(**_: Any) -> dict[str, Any]:
                return {"group_key": "ops_watch", "feishu_chat_id": "oc_group"}

            async def fake_executable_preview(**_: Any) -> Any:
                nonlocal executable_preview_called
                executable_preview_called = True
                raise AssertionError("competitor-triggered reference card must not create executable preview")

            async def fake_reference_preview(**_: Any) -> Any:
                nonlocal reference_preview_called
                reference_preview_called = True
                return SimpleNamespace(
                    status="reference_ready",
                    summary="reference preview ready",
                    preview_id=301,
                    model_dump=lambda mode: {
                        "status": "reference_ready",
                        "summary": "reference preview ready",
                        "preview_id": 301,
                        "id": 301,
                        "watch_group_key": "ops_watch",
                        "can_auto_write_price": True,
                        "connection_mode": "non_direct",
                        "write_policy_message": "non-direct account",
                        "preview_candidate": {
                            "source": "competitor_price_alert_reference",
                            "requires_operator_target_price": True,
                            "actions": [
                                {
                                    "room_type": "Deluxe King",
                                    "date_label": "today",
                                    "checkin_date": "2026-06-24",
                                    "breakfast_kind": "none",
                                    "current_price_yuan": 238,
                                    "competitor_previous_price_yuan": 225,
                                    "competitor_current_price_yuan": 207,
                                    "recommended_adjustment_yuan": -10,
                                    "proposed_price_yuan": 228,
                                    "trigger_reason": "competitor dropped",
                                }
                            ],
                        },
                    },
                )

            class FakeClient:
                def __init__(
                    self,
                    *,
                    chat_id: str | None = None,
                    app_profile: str | None = None,
                ) -> None:
                    _ = chat_id, app_profile

                async def send_interactive_card(
                    self,
                    chat_id: str,
                    card: dict[str, Any],
                    *,
                    fallback_text: str | None = None,
                ) -> dict[str, Any]:
                    sent.append({"chat_id": chat_id, "card": card, "fallback_text": fallback_text})
                    return {"status": "success", "summary": "sent"}

            orchestration.get_competitor_watch_group_detail = fake_group
            orchestration.create_pricing_preview_from_alert = fake_executable_preview
            orchestration.create_pricing_reference_preview_from_alert = fake_reference_preview
            orchestration.FeishuBotClient = FakeClient

            result = await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record={
                    "status": "suggestion_ready",
                    "alert_record_id": 101,
                    "watch_group_key": "ops_watch",
                    "connection_mode": "non_direct",
                    "can_auto_write_price": True,
                    "write_policy_message": "non-direct account",
                    "suggestions": [
                        {
                            "room_type": "Deluxe King",
                            "date_label": "today",
                            "checkin_date": "2026-06-24",
                            "breakfast_kind": "none",
                            "current_own_price_yuan": 238,
                            "competitor_previous_price_yuan": 225,
                            "competitor_current_price_yuan": 207,
                            "recommended_adjustment_yuan": -10,
                            "recommended_price_yuan": 228,
                            "trigger_reason": "competitor dropped",
                        }
                    ],
                },
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

            self.assertFalse(executable_preview_called)
            self.assertTrue(reference_preview_called)
            self.assertEqual(result["outcome"], "reference_card_sent")
            self.assertEqual(result.get("preview_id"), 301)
            self.assertEqual(sent[0]["chat_id"], "oc_group")
            action_values = [item["value"] for item in sent[0]["card"]["elements"][-1]["actions"]]
            self.assertEqual([item.get("action") for item in action_values], ["custom_price", "defer"])
            self.assertEqual(action_values[0]["preview_id"], 301)
            self.assertNotIn("approve", str(action_values))

        asyncio.run(scenario())

    def test_auto_pricing_orchestration_falls_back_when_ai_summary_fails(self) -> None:
        async def scenario() -> None:
            orchestration = _reload_module(
                "app.domain.competitor_prices.price_change_pricing_orchestration"
            )
            sent: list[dict[str, Any]] = []

            async def fake_group(**_: Any) -> dict[str, Any]:
                return {"group_key": "ops_watch", "feishu_chat_id": "oc_group"}

            class FakeClient:
                def __init__(
                    self,
                    *,
                    chat_id: str | None = None,
                    app_profile: str | None = None,
                ) -> None:
                    _ = chat_id, app_profile

                async def send_interactive_card(
                    self,
                    chat_id: str,
                    card: dict[str, Any],
                    *,
                    fallback_text: str | None = None,
                ) -> dict[str, Any]:
                    sent.append({"chat_id": chat_id, "card": card, "fallback_text": fallback_text})
                    return {"status": "success", "summary": "sent"}

            class FailingSummaryClient:
                async def summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
                    raise RuntimeError("raw secret stack trace")

            orchestration.get_competitor_watch_group_detail = fake_group
            orchestration.FeishuBotClient = FakeClient
            orchestration.PricingReferenceSummaryClient = lambda: FailingSummaryClient()
            orchestration.create_pricing_reference_preview_from_alert = _fake_reference_preview

            result = await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record={
                    "status": "suggestion_ready",
                    "alert_record_id": 101,
                    "watch_group_key": "ops_watch",
                    "connection_mode": "non_direct",
                    "can_auto_write_price": True,
                    "write_policy_message": "non-direct account",
                    "suggestions": [
                        {
                            "room_type": "Deluxe King",
                            "date_label": "today",
                            "checkin_date": "2026-06-24",
                            "breakfast_kind": "none",
                            "current_own_price_yuan": 238,
                            "competitor_previous_price_yuan": 225,
                            "competitor_current_price_yuan": 207,
                            "recommended_adjustment_yuan": -10,
                            "recommended_price_yuan": 228,
                            "trigger_reason": "competitor dropped",
                        }
                    ],
                },
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

            self.assertEqual(result["outcome"], "reference_card_sent")
            summary = result["pricing_reference_summary"]
            self.assertEqual(summary["confidence_label"], "rule_fallback")
            self.assertIn("ai_fallback", summary["risk_flags"])
            self.assertNotIn("secret", str(result).lower())
            self.assertNotIn("stack", str(result).lower())
            self.assertTrue(sent)

        asyncio.run(scenario())

    def test_auto_pricing_orchestration_reference_card_has_no_approve_action(self) -> None:
        async def scenario() -> None:
            orchestration = _reload_module(
                "app.domain.competitor_prices.price_change_pricing_orchestration"
            )
            sent: list[dict[str, Any]] = []

            async def fake_group(**_: Any) -> dict[str, Any]:
                return {
                    "group_key": "ops_watch",
                    "feishu_chat_id": "oc_group",
                    "price_change_alert_title": "某某酒店竞对盯盘",
                }

            async def fake_reference_preview(**_: Any) -> Any:
                return SimpleNamespace(
                    status="reference_ready",
                    summary="调价参考已生成。",
                    preview_id=201,
                    model_dump=lambda mode: {
                        "status": "reference_ready",
                        "summary": "调价参考已生成。",
                        "preview_id": 201,
                        "id": 201,
                        "watch_group_key": "ops_watch",
                        "can_auto_write_price": True,
                        "connection_mode": "non_direct",
                        "write_policy_message": "非直连账号可在人工确认后改价",
                        "preview_candidate": {
                            "source": "competitor_price_alert_reference",
                            "requires_operator_target_price": True,
                            "actions": [
                                {
                                    "room_type": "高级大床房",
                                    "date_label": "today",
                                    "checkin_date": "2026-06-24",
                                    "breakfast_kind": "none",
                                    "current_price_yuan": 238,
                                    "competitor_previous_price_yuan": 225,
                                    "competitor_current_price_yuan": 207,
                                    "recommended_adjustment_yuan": -10,
                                    "proposed_price_yuan": 228,
                                    "trigger_reason": "竞品降价超过阈值。",
                                }
                            ],
                        },
                    },
                )

            class FakeClient:
                def __init__(
                    self,
                    *,
                    chat_id: str | None = None,
                    app_profile: str | None = None,
                ) -> None:
                    _ = chat_id, app_profile

                async def send_interactive_card(
                    self,
                    chat_id: str,
                    card: dict[str, Any],
                    *,
                    fallback_text: str | None = None,
                ) -> dict[str, Any]:
                    sent.append({"chat_id": chat_id, "card": card, "fallback_text": fallback_text})
                    return {"status": "success", "summary": "sent"}

            orchestration.get_competitor_watch_group_detail = fake_group
            orchestration.create_pricing_reference_preview_from_alert = fake_reference_preview
            orchestration.FeishuBotClient = FakeClient

            result = await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record={
                    "status": "suggestion_ready",
                    "alert_record_id": 101,
                    "watch_group_key": "ops_watch",
                    "connection_mode": "non_direct",
                    "can_auto_write_price": True,
                    "write_policy_message": "非直连账号可在人工确认后改价",
                    "suggestions": [{"recommended_price_yuan": 228}],
                },
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

            self.assertEqual(result["outcome"], "reference_card_sent")
            content = str(sent[0]["card"])
            self.assertIn("自定义调价", content)
            self.assertIn("暂不处理", content)
            self.assertEqual(sent[0]["card"]["elements"][-1]["actions"][0]["value"]["preview_id"], 201)
            self.assertNotIn("'decision': 'approve'", content)
            self.assertNotIn('"decision": "approve"', content)
            self.assertNotIn("'content': '通过'", content)
            self.assertNotIn("调价审批", content)

        asyncio.run(scenario())

    def test_auto_pricing_orchestration_skips_suggestion_only_accounts(self) -> None:
        async def scenario() -> None:
            orchestration = _reload_module(
                "app.domain.competitor_prices.price_change_pricing_orchestration"
            )
            preview_called = False

            async def fail_reference_preview(**_: Any) -> Any:
                nonlocal preview_called
                preview_called = True
                raise AssertionError("suggestion-only account should not create pricing preview")

            orchestration.create_pricing_reference_preview_from_alert = fail_reference_preview

            result = await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record={
                    "status": "suggestion_ready",
                    "watch_group_key": "ops_watch",
                    "connection_mode": "unknown",
                    "can_auto_write_price": False,
                    "write_policy_message": "账号类型未确认，默认按直连处理，仅生成调价建议，不会自动改价。",
                    "suggestions": [{"recommended_price_yuan": 390}],
                },
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

            self.assertFalse(preview_called)
            self.assertEqual(result["outcome"], "skipped")
            self.assertEqual(result["reason"], "suggestion_only_account")

        asyncio.run(scenario())

    def test_auto_pricing_orchestration_skips_when_alert_is_not_suggestion_ready(self) -> None:
        async def scenario() -> None:
            orchestration = _reload_module(
                "app.domain.competitor_prices.price_change_pricing_orchestration"
            )

            async def fail_reference_preview(**_: Any) -> Any:
                raise AssertionError("no_trigger should not create pricing preview")

            orchestration.create_pricing_reference_preview_from_alert = fail_reference_preview

            result = await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record={"status": "no_trigger", "watch_group_key": "ops_watch"},
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

            self.assertEqual(result["outcome"], "skipped")
            self.assertEqual(result["reason"], "alert_not_suggestion_ready")

        asyncio.run(scenario())

    def test_auto_pricing_orchestration_skips_change_detected_alerts(self) -> None:
        async def scenario() -> None:
            orchestration = _reload_module(
                "app.domain.competitor_prices.price_change_pricing_orchestration"
            )

            async def fail_reference_preview(**_: Any) -> Any:
                raise AssertionError("price-rise change alert should not create pricing preview")

            orchestration.create_pricing_reference_preview_from_alert = fail_reference_preview

            result = await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record={
                    "status": "change_detected",
                    "watch_group_key": "ops_watch",
                    "changed_items": [{"direction": "up", "signed_delta_yuan": 60}],
                    "suggestions": [],
                },
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

            self.assertEqual(result["outcome"], "skipped")
            self.assertEqual(result["reason"], "alert_not_suggestion_ready")

        asyncio.run(scenario())


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


async def _save_price_run(repository, *, captured_at: str, competitor_none: int):
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
                    "date_context": {"check_in": "2026-06-18", "check_out": "2026-06-19"},
                    "room_rates": [
                        {
                            "source": "room_rate_dom_display_price",
                            "room_type": "高级大床房",
                            "breakfast": "无早",
                            "availability": "正常",
                            "price": {"amount": 420, "currency": "CNY"},
                        }
                    ],
                },
                {
                    "role": "competitor_1",
                    "hotel_name": "竞品 A",
                    "status": "success",
                    "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                    "date_context": {"check_in": "2026-06-18", "check_out": "2026-06-19"},
                    "room_rates": [
                        {
                            "source": "room_rate_dom_display_price",
                            "room_type": "高级大床房",
                            "breakfast": "无早",
                            "availability": "正常",
                            "price": {"amount": competitor_none, "currency": "CNY"},
                        }
                    ],
                },
            ],
        },
        evidence={"parser_version": "test"},
    )


def _reference_preview_stub(
    *,
    preview_id: int = 201,
    watch_group_key: str = "ops_watch",
    actions: list[dict[str, Any]] | None = None,
) -> Any:
    preview_actions = actions or [
        {
            "room_type": "Deluxe King",
            "date_label": "today",
            "checkin_date": "2026-06-24",
            "breakfast_kind": "none",
            "current_price_yuan": 238,
            "competitor_previous_price_yuan": 225,
            "competitor_current_price_yuan": 207,
            "recommended_adjustment_yuan": -10,
            "proposed_price_yuan": 228,
            "trigger_reason": "competitor dropped",
        }
    ]
    payload = {
        "status": "reference_ready",
        "summary": "调价参考已生成。",
        "preview_id": preview_id,
        "id": preview_id,
        "watch_group_key": watch_group_key,
        "can_auto_write_price": True,
        "connection_mode": "non_direct",
        "write_policy_message": "非直连账号可在人工确认后改价",
        "preview_candidate": {
            "source": "competitor_price_alert_reference",
            "requires_operator_target_price": True,
            "actions": preview_actions,
        },
    }
    return SimpleNamespace(
        status="reference_ready",
        summary="调价参考已生成。",
        preview_id=preview_id,
        model_dump=lambda mode, payload=payload: payload,
    )


async def _fake_reference_preview(**_: Any) -> Any:
    return _reference_preview_stub()


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


def _reload_app():
    main = importlib.import_module("app.main")
    return importlib.reload(main).create_app()


def _reload_module(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


if __name__ == "__main__":
    unittest.main()
