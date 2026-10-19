from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


WRITE_NOT_ALLOWED_REASON = (
    "当前携程 EBK 账号为直连或未确认账号，仅允许生成调价建议，不允许自动化改价"
)


class PricingPipelineRestartRecoveryTests(unittest.TestCase):
    def test_stuck_running_pipeline_is_marked_failed_and_card_pushed(self) -> None:
        """重启恢复：queued/running 执行链标记失败并按 chat_id 补推重发卡。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                db_session = _reload_db_session(db_url)
                await db_session.ensure_db_ready()
                from app.db.session import session_scope
                from app.domain.pricing_execution.models import (
                    PricingApprovalRecord,
                    PricingPreviewRecord,
                )
                from app.domain.pricing_safety import strip_sensitive
                from app.domain.pricing_approvals import service as approval_service

                async with session_scope() as session:
                    preview = PricingPreviewRecord(
                        watch_group_key="watch_jingli",
                        status="preview_ready",
                        summary="preview",
                        connection_mode="non_direct",
                        can_auto_write_price=True,
                        preview_candidate_json={},
                        source_alert_json={},
                        actor_id="operator",
                    )
                    session.add(preview)
                    await session.flush()
                    record = PricingApprovalRecord(
                        preview_id=int(preview.id),
                        approval_id="pricing-approval-restart",
                        source="feishu_card",
                        decision="approve",
                        status="approved",
                        summary="approved",
                        actor_id="operator",
                        execute_triggered=True,
                        result_json=strip_sensitive(
                            {
                                "decision": "approve",
                                "feishu_chat_id": "oc_restart_group",
                                "pipeline": {"status": "running"},
                            }
                        ),
                    )
                    session.add(record)
                    await session.flush()
                    record_id = int(record.id)

                sent: list[dict[str, Any]] = []

                class FakeClient:
                    def __init__(self, *, chat_id=None, app_profile=None) -> None:
                        pass

                    async def send_interactive_card(
                        self,
                        chat_id: str,
                        card: dict[str, Any],
                        *,
                        fallback_text: str | None = None,
                    ) -> dict[str, Any]:
                        sent.append({"chat_id": chat_id, "card": card})
                        return {"status": "success", "summary": "sent"}

                from app.domain.notifications import feishu_client

                with mock.patch.object(
                    feishu_client, "FeishuBotClient", FakeClient
                ):
                    stuck = await approval_service.recover_stuck_pricing_approval_pipelines()

                self.assertEqual(stuck, [record_id])
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0]["chat_id"], "oc_restart_group")
                self.assertIn("重新发起", str(sent[0]["card"]))

                async with session_scope() as session:
                    reloaded = await session.get(PricingApprovalRecord, record_id)
                    pipeline = ((reloaded.result_json or {}).get("pipeline") or {})
                self.assertEqual(pipeline.get("status"), "failed")
                self.assertEqual(pipeline.get("error_type"), "pipeline_lost_on_restart")

                # 幂等：再次恢复不再命中。
                with mock.patch.object(
                    feishu_client, "FeishuBotClient", FakeClient
                ):
                    again = await approval_service.recover_stuck_pricing_approval_pipelines()
                self.assertEqual(again, [])
                self.assertEqual(len(sent), 1)

            asyncio.run(scenario())


class FeishuPricingExecutionFlowTests(unittest.TestCase):
    def test_pricing_reference_defer_action_returns_toast(self) -> None:
        """回归：2026-09-04 死代码清理（9cc5263）误删四个竞品降价参考卡回调处理
        函数，handle_event 调用点仍在，运营点「暂不处理」即 NameError/500。"""

        async def scenario() -> Any:
            from app.domain.notifications import feishu_bot

            return await feishu_bot.handle_event(
                {
                    "header": {
                        "event_id": "evt-ref-defer",
                        "event_type": "card.action.trigger",
                    },
                    "event": {
                        "action": {
                            "value": {
                                "workflow": "pricing_reference",
                                "action": "defer",
                                "preview_id": 12,
                            }
                        },
                        "context": {"open_chat_id": "oc_ref"},
                        "operator": {"open_id": "ou_op", "union_id": "un_op"},
                        "message_id": "om_ref_card",
                    },
                }
            )

        result = asyncio.run(scenario())
        self.assertEqual(result["status"], "pricing_reference_deferred")
        toast = result["card_callback_response"]["toast"]
        self.assertEqual(toast["type"], "info")
        self.assertTrue(toast["content"])
        self.assertFalse(result["evidence"]["execute_triggered"])

    def test_manual_preview_requires_explicit_pricing_ebk_account_binding(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                sessions = _reload_browser_sessions()
                repository = _reload_module("app.domain.competitor_prices.repository")
                types = _reload_module("app.domain.competitor_prices.types")
                previews = _reload_module("app.domain.pricing_suggestions.preview")

                _restore_browser_session(
                    sessions,
                    browser_session_id="browser-public-1",
                    platform_account_id="ctrip-public-1",
                    intended_use="competitor_price",
                    connection_mode="direct",
                    session_port=9321,
                )
                _restore_browser_session(
                    sessions,
                    browser_session_id="browser-ebk-direct",
                    platform_account_id="ctrip-ebk-direct",
                    intended_use="ebk",
                    connection_mode="direct",
                    session_port=9421,
                )
                _restore_browser_session(
                    sessions,
                    browser_session_id="browser-ebk-non-direct",
                    platform_account_id="ctrip-ebk-non-direct",
                    intended_use="ebk",
                    connection_mode="non_direct",
                    session_port=9422,
                )

                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="竞品监控组",
                        browser_session_id="browser-public-1",
                    )
                )

                missing = await previews.create_manual_pricing_preview(
                    watch_group_key="ops_watch",
                    room_type="奢华三卧套房",
                    date_label="today",
                    target_price=702,
                    actor_id="test",
                )
                self.assertEqual(missing.status, "config_required")
                self.assertFalse(missing.can_auto_write_price)
                self.assertIn("EBK", missing.summary)
                self.assertFalse(missing.evidence["pricing_account_configured"])

                ready = await previews.create_manual_pricing_preview(
                    watch_group_key="ops_watch",
                    room_type="奢华三卧套房",
                    date_label="today",
                    target_price=702,
                    pricing_account=sessions.get_browser_session_store().get("browser-ebk-non-direct"),
                    actor_id="test",
                )
                self.assertEqual(ready.status, "preview_ready")
                self.assertEqual(ready.connection_mode, "non_direct")
                self.assertTrue(ready.can_auto_write_price)
                self.assertEqual(
                    ready.preview_candidate["pricing_account"]["platform_account_id"],
                    "ctrip-ebk-non-direct",
                )
                self.assertEqual(
                    ready.preview_candidate["pricing_account"]["browser_session_id"],
                    "browser-ebk-non-direct",
                )

            asyncio.run(scenario())

    def test_direct_and_unknown_accounts_do_not_get_approval_buttons(self) -> None:
        approval = _reload_module("app.domain.pricing_approvals.cards")

        for mode in ("direct", "unknown"):
            preview = _sample_preview_payload(connection_mode=mode, can_auto_write_price=False)
            card = approval.build_pricing_approval_card(preview)
            content = str(card)
            self.assertNotIn("通过并执行", content)
            self.assertNotIn("'content': '通过'", content)
            self.assertNotIn("拒绝", content)
            self.assertNotIn("自定义调价", content)
            self.assertIn("今日预警", content)
            self.assertIn("直连/未确认门店无法自动调价", content)
            self.assertNotIn("room_rate_dom_display_price", content)

    def test_non_direct_account_gets_feishu_execute_approval_button(self) -> None:
        approval = _reload_module("app.domain.pricing_approvals.cards")

        preview = _sample_preview_payload(connection_mode="non_direct", can_auto_write_price=True)
        card = approval.build_pricing_approval_card(preview)
        content = str(card)

        self.assertIn("通过", content)
        self.assertIn("拒绝", content)
        self.assertIn("自定义调价", content)
        self.assertIn("非直连审批", content)
        self.assertIn("今日预警", content)
        self.assertNotIn("通过并执行", content)
        self.assertIn("pricing_approval", content)
        self.assertIn("approve", content)

    def test_pricing_reference_card_uses_operator_friendly_summary_copy(self) -> None:
        approval = _reload_module("app.domain.pricing_approvals.cards")

        preview = {
            **_sample_preview_payload(connection_mode="non_direct", can_auto_write_price=True),
            "preview_id": 501,
            "id": 501,
            "pricing_reference_summary": {
                "summary_cn": "today competitors dropped; confirm a target price",
                "suggested_price_yuan": 228,
                "suggested_price_range": {"min": 228, "max": 238},
                "risk_flags": ["bait_room_type"],
                "evidence_lines": ["today no breakfast: competitor 225 -> 207, ours 238"],
                "confidence_label": "medium",
            },
        }
        preview["preview_candidate"]["pricing_reference_summary"] = preview["pricing_reference_summary"]

        card = approval.build_pricing_reference_card(preview, title="ops watch")
        content = str(card)

        first_block = card["elements"][0]["text"]["content"]
        self.assertIn("today competitors dropped", first_block)
        self.assertIn("bait", first_block)
        self.assertIn("today no breakfast", first_block)
        self.assertIn("228", content)
        self.assertIn("238", content)
        actions = card["elements"][-1]["actions"]
        self.assertEqual([item["value"]["action"] for item in actions], ["custom_price", "defer"])
        self.assertEqual(actions[0]["value"]["preview_id"], 501)
        self.assertNotIn("run_id", content)
        self.assertNotIn("raw JSON", content)
        self.assertNotIn("decision': 'approve'", content)
        self.assertNotIn("'content': '??'", content)

    def test_non_direct_data_required_preview_does_not_get_execute_button(self) -> None:
        approval = _reload_module("app.domain.pricing_approvals.cards")

        preview = {
            **_sample_preview_payload(connection_mode="non_direct", can_auto_write_price=True),
            "status": "data_required",
            "summary": "缺少可执行的可信价格变化建议，未生成调价预览。",
            "preview_candidate": {"actions": []},
        }
        card = approval.build_pricing_approval_card(preview)
        content = str(card)

        self.assertNotIn("通过并执行", content)
        self.assertIn("暂无可审批的调价动作。", content)
        self.assertNotIn("'content': '通过'", content)
        self.assertNotIn("拒绝", content)
        self.assertNotIn("自定义调价", content)

    def test_api_direct_approval_records_decision_but_does_not_queue_execute(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                previews = _reload_module("app.domain.pricing_suggestions.preview")
                approvals = _reload_module("app.domain.pricing_approvals.service")
                pipeline = _reload_module("app.domain.notifications.pricing_execution_pipeline")

                queued: list[int] = []
                original_queue = pipeline.queue_pricing_execution_pipeline
                pipeline.queue_pricing_execution_pipeline = lambda *, approval_record_id: queued.append(approval_record_id) or True
                try:
                    preview = await previews.create_pricing_preview_from_alert(
                        alert_record=_sample_alert_record(can_auto_write_price=True),
                        actor_id="test",
                    )
                    decision = await approvals.record_pricing_approval_decision(
                        preview_id=preview.preview_id,
                        decision="approve",
                        actor_id="api-user",
                        source="api",
                    )
                finally:
                    pipeline.queue_pricing_execution_pipeline = original_queue

                self.assertEqual(decision.status, "approved")
                self.assertFalse(decision.execute_triggered)
                self.assertEqual(queued, [])

            asyncio.run(scenario())

    def test_only_feishu_approve_action_queues_pipeline(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                previews = _reload_module("app.domain.pricing_suggestions.preview")
                feishu_bot = _reload_module("app.domain.notifications.feishu_bot")
                pipeline = _reload_module("app.domain.notifications.pricing_execution_pipeline")

                queued: list[int] = []
                original_queue = pipeline.queue_pricing_execution_pipeline
                pipeline.queue_pricing_execution_pipeline = lambda *, approval_record_id: queued.append(approval_record_id) or True
                try:
                    preview = await previews.create_pricing_preview_from_alert(
                        alert_record=_sample_alert_record(can_auto_write_price=True),
                        actor_id="test",
                    )
                    result = await feishu_bot.handle_pricing_approval_card_action(
                        {
                            "event_id": "evt-approve-1",
                            "open_id": "ou_1234567890abcdef",
                            "preview_id": preview.preview_id,
                            "decision": "approve",
                        }
                    )
                finally:
                    pipeline.queue_pricing_execution_pipeline = original_queue

                self.assertEqual(result["status"], "approved")
                self.assertTrue(result["evidence"]["execution_pipeline_queued"])
                self.assertEqual(len(queued), 1)

            asyncio.run(scenario())

    def test_reference_preview_cannot_be_directly_approved(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                previews = _reload_module("app.domain.pricing_suggestions.preview")
                approvals = _reload_module("app.domain.pricing_approvals.service")

                preview = await previews.create_pricing_reference_preview_from_alert(
                    alert_record=_sample_alert_record(can_auto_write_price=True),
                    actor_id="test",
                )
                decision = await approvals.record_pricing_approval_decision(
                    preview_id=preview.preview_id,
                    decision="approve",
                    actor_id="feishu-card",
                    source="feishu_card",
                )

                self.assertEqual(preview.status, "reference_ready")
                self.assertEqual(decision.status, "operator_target_price_required")
                self.assertFalse(decision.execute_triggered)

            asyncio.run(scenario())

    def test_connection_mode_is_checked_at_preflight_dry_run_and_execute(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                previews = _reload_module("app.domain.pricing_suggestions.preview")
                approvals = _reload_module("app.domain.pricing_approvals.service")
                execution = _reload_module("app.domain.pricing_execution.service")

                direct_preview = await previews.create_pricing_preview_from_alert(
                    alert_record=_sample_alert_record(can_auto_write_price=False, connection_mode="direct"),
                    actor_id="test",
                )
                direct_approval = await approvals.record_pricing_approval_decision(
                    preview_id=direct_preview.preview_id,
                    decision="approve",
                    actor_id="feishu-card",
                    source="feishu_card",
                    open_id="ou_direct",
                )

                preflight = await execution.prepare_pricing_execution_preflight(
                    approval_record_id=direct_approval.approval_record_id,
                    actor_id="test",
                )
                dry_run = await execution.prepare_pricing_page_write_dry_run(
                    execution_record_id=999,
                    actor_id="test",
                    account_policy={"connection_mode": "unknown", "can_auto_write_price": False},
                )
                execute = await execution.execute_pricing_page_write(
                    execution_record_id=999,
                    actor_id="test",
                    account_policy={"connection_mode": "direct", "can_auto_write_price": False},
                )

                self.assertEqual(preflight.status, "write_not_allowed")
                self.assertEqual(dry_run.status, "write_not_allowed")
                self.assertEqual(execute.status, "write_not_allowed")
                self.assertEqual(preflight.summary, WRITE_NOT_ALLOWED_REASON)

            asyncio.run(scenario())

    def test_execute_request_price_must_equal_approved_preview_price(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                previews = _reload_module("app.domain.pricing_suggestions.preview")
                approvals = _reload_module("app.domain.pricing_approvals.service")
                execution = _reload_module("app.domain.pricing_execution.service")

                preview = await previews.create_pricing_preview_from_alert(
                    alert_record=_sample_alert_record(can_auto_write_price=True),
                    actor_id="test",
                )
                approval = await approvals.record_pricing_approval_decision(
                    preview_id=preview.preview_id,
                    decision="approve",
                    actor_id="feishu-card",
                    source="feishu_card",
                    open_id="ou_non_direct",
                )
                preflight = await execution.prepare_pricing_execution_preflight(
                    approval_record_id=approval.approval_record_id,
                    actor_id="test",
                )

                self.assertEqual(preflight.status, "preflight_ready")
                action = preflight.execute_request["actions"][0]
                approved_action = preview.preview_candidate["actions"][0]
                self.assertEqual(action["proposed_price_yuan"], approved_action["proposed_price_yuan"])
                self.assertEqual(action["proposed_price_yuan"], 450)

            asyncio.run(scenario())

    def test_no_trusted_suggestion_does_not_create_executable_preview(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                previews = _reload_module("app.domain.pricing_suggestions.preview")

                preview = await previews.create_pricing_preview_from_alert(
                    alert_record={**_sample_alert_record(), "status": "data_required", "suggestions": []},
                    actor_id="test",
                )

                self.assertEqual(preview.status, "data_required")
                self.assertFalse(preview.preview_candidate.get("actions"))

            asyncio.run(scenario())

    def test_execution_result_and_blocked_cards_render_success_and_failure(self) -> None:
        cards = _reload_module("app.domain.notifications.pricing_execution_card")

        success = cards.build_pricing_execution_result_card(
            {
                "status": "executed",
                "summary": "提交成功",
                "data": {
                    "captured_at": "2026-06-23 10:00",
                    "action_results": [
                        {
                            "status": "filled",
                            "room_type": "高级大床房",
                            "checkin_date": "2026-06-24",
                            "fill_result": {"filled_value": "401.79", "verified_value": "401.79"},
                        }
                    ],
                    "submit_result": {"status": "executed", "message": "保存成功"},
                },
            }
        )
        blocked = cards.build_pricing_execution_blocked_card(
            blocked_step="P6 执行预检",
            blocked_status="human_required",
            blocked_summary="需要人工登录",
            next_action="请先处理登录",
        )

        self.assertIn("调价执行结果", str(success))
        self.assertIn("高级大床房", str(success))
        self.assertIn("保存成功", str(success))
        self.assertIn("调价流程阻断", str(blocked))
        self.assertIn("需要人工登录", str(blocked))

    def test_browser_human_required_and_page_structure_change_block_execution(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                previews = _reload_module("app.domain.pricing_suggestions.preview")
                approvals = _reload_module("app.domain.pricing_approvals.service")
                execution = _reload_module("app.domain.pricing_execution.service")
                ctrip_module = importlib.import_module("app.domain.browser.ctrip_ebooking")

                preview = await previews.create_pricing_preview_from_alert(
                    alert_record=_sample_alert_record(can_auto_write_price=True),
                    actor_id="test",
                )
                approval = await approvals.record_pricing_approval_decision(
                    preview_id=preview.preview_id,
                    decision="approve",
                    actor_id="feishu-card",
                    source="feishu_card",
                    open_id="ou_non_direct",
                )
                preflight = await execution.prepare_pricing_execution_preflight(
                    approval_record_id=approval.approval_record_id,
                    actor_id="test",
                )

                original_session_resolver = execution.get_browser_session_id_for_platform_account
                original_service_factory = ctrip_module.get_ctrip_ebooking_service
                execution.get_browser_session_id_for_platform_account = lambda _account: "browser-1"
                fake_service = _FakePricingBrowserService(
                    dry_run_status="human_required",
                    execute_status="page_structure_changed",
                )
                ctrip_module.get_ctrip_ebooking_service = lambda: fake_service
                try:
                    dry_run = await execution.prepare_pricing_page_write_dry_run(
                        execution_record_id=preflight.execution_record_id,
                        actor_id="test",
                    )
                    execute = await execution.execute_pricing_page_write(
                        execution_record_id=dry_run.execution_record_id,
                        actor_id="test",
                    )
                finally:
                    execution.get_browser_session_id_for_platform_account = original_session_resolver
                    ctrip_module.get_ctrip_ebooking_service = original_service_factory

                self.assertEqual(dry_run.status, "human_required")
                self.assertIn("登录", dry_run.summary)
                self.assertEqual(execute.status, "not_preflight_ready")
                self.assertFalse(fake_service.execute_called)

            asyncio.run(scenario())

    def test_backend_does_not_expose_public_pricing_execute_api(self) -> None:
        main = _reload_module("app.main")
        app = main.create_app()
        forbidden_tokens = (
            "pricing-executions",
            "page-write-dry-run",
            "dry-run",
            "execute",
        )
        routes = [
            getattr(route, "path", "")
            for route in app.routes
            if str(getattr(route, "path", "")).startswith("/api/v1")
        ]
        public_execute_routes = [
            route
            for route in routes
            if any(token in route for token in forbidden_tokens)
            and not route.startswith("/api/v1/pricing-executions/{execution_id}")
        ]
        self.assertEqual(public_execute_routes, [])

    def test_guarded_pipeline_records_failure_and_sends_blocked_card_on_unexpected_error(
        self,
    ) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db_session(db_url)
                previews = _reload_module("app.domain.pricing_suggestions.preview")
                approvals = _reload_module("app.domain.pricing_approvals.service")
                pipeline = _reload_module("app.domain.notifications.pricing_execution_pipeline")

                sent_cards: list[dict[str, Any]] = []
                original_send = pipeline._send_pipeline_card

                async def fake_send(card, *, chat_id=None, fallback_text=None):
                    sent_cards.append({"card": card, "chat_id": chat_id})
                    return {"status": "success"}

                async def exploding_pipeline(**_: Any) -> None:
                    raise RuntimeError(
                        r"C:\pan-hotel-watch-runtime\ctrip-profile token=abc webhook=https://secret"
                    )

                pipeline._send_pipeline_card = fake_send
                pipeline.run_pricing_execution_pipeline = exploding_pipeline
                try:
                    preview = await previews.create_pricing_preview_from_alert(
                        alert_record=_sample_alert_record(can_auto_write_price=True),
                        actor_id="test",
                    )
                    decision = await approvals.record_pricing_approval_decision(
                        preview_id=preview.preview_id,
                        decision="approve",
                        actor_id="feishu-card",
                        source="feishu_card",
                        open_id="ou_non_direct",
                    )
                    with self.assertLogs(pipeline.logger, level="ERROR") as captured_logs:
                        await pipeline.run_pricing_execution_pipeline_guarded(
                            approval_record_id=decision.approval_record_id,
                            chat_id="oc_test_chat",
                        )
                    record = await approvals.get_pricing_approval(decision.approval_record_id)
                finally:
                    pipeline._send_pipeline_card = original_send

                self.assertIsNotNone(record)
                pipeline_state = (record or {}).get("result", {}).get("pipeline", {})
                self.assertEqual(pipeline_state.get("status"), "failed")
                self.assertEqual(pipeline_state.get("error_type"), "RuntimeError")
                self.assertEqual(
                    pipeline_state.get("error_message"),
                    pipeline.PIPELINE_UNEXPECTED_FAILURE_MESSAGE,
                )
                serialized = str(record)
                self.assertNotIn("pan-hotel-watch-runtime", serialized)
                self.assertNotIn("token=abc", serialized)
                self.assertNotIn("webhook=https://secret", serialized)
                logged = " ".join(captured_logs.output)
                self.assertIn("RuntimeError", logged)
                self.assertNotIn("pan-hotel-watch-runtime", logged)
                self.assertNotIn("token=abc", logged)
                self.assertNotIn("webhook=https://secret", logged)
                self.assertEqual(len(sent_cards), 1)
                self.assertIn("调价流程阻断", str(sent_cards[0]["card"]))

            asyncio.run(scenario())


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


def _reload_module(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


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


def _sample_alert_record(
    *,
    can_auto_write_price: bool = False,
    connection_mode: str | None = None,
) -> dict[str, Any]:
    mode = connection_mode or ("non_direct" if can_auto_write_price else "unknown")
    return {
        "id": 101,
        "alert_record_id": 101,
        "status": "suggestion_ready",
        "summary": "检测到竞品价格变化，已生成规则化建议。",
        "watch_group_key": "ops_watch",
        "current_run_id": 12,
        "previous_run_id": 11,
        "connection_mode": mode,
        "can_auto_write_price": can_auto_write_price,
        "write_policy_message": (
            "非直连账号可在 Feishu 审批通过后继续调价流程"
            if can_auto_write_price
            else WRITE_NOT_ALLOWED_REASON
        ),
        "suggestions": [
            {
                "hotel_config_id": 2,
                "hotel_name": "竞品 A",
                "date_label": "today",
                "checkin_date": "2026-06-24",
                "checkout_date": "2026-06-25",
                "room_type": "高级大床房",
                "normalized_room_type": "高级大床房",
                "breakfast_kind": "none",
                "breakfast_text": "无早",
                "current_own_price_yuan": 420,
                "competitor_previous_price_yuan": 400,
                "competitor_current_price_yuan": 460,
                "change_amount_yuan": 60,
                "change_percent": 15.0,
                "recommended_adjustment_yuan": 30,
                "recommended_price_yuan": 450,
                "trigger_reason": "竞品上涨超过阈值，建议按规则上调。",
                "needs_human_attention": False,
                "trusted_source_statement": (
                    "仅使用可信 DOM 房价 room_rate_dom_display_price 生成建议"
                ),
                "connection_mode": mode,
                "can_auto_write_price": can_auto_write_price,
                "write_policy_message": (
                    "非直连账号可在 Feishu 审批通过后继续调价流程"
                    if can_auto_write_price
                    else WRITE_NOT_ALLOWED_REASON
                ),
            }
        ],
        "evidence": {
            "trusted_price_source": "room_rate_dom_display_price",
            "account_write_policy": {
                "connection_mode": mode,
                "can_auto_write_price": can_auto_write_price,
                "write_policy_message": (
                    "非直连账号可在 Feishu 审批通过后继续调价流程"
                    if can_auto_write_price
                    else WRITE_NOT_ALLOWED_REASON
                ),
            },
        },
    }


def _sample_preview_payload(*, connection_mode: str, can_auto_write_price: bool) -> dict[str, Any]:
    return {
        "preview_id": 1,
        "approval_id": "pricing-approval-test",
        "status": "preview_ready",
        "summary": "调价预览已生成。",
        "watch_group_key": "ops_watch",
        "connection_mode": connection_mode,
        "can_auto_write_price": can_auto_write_price,
        "write_policy_message": (
            "非直连账号可在 Feishu 审批通过后继续调价流程"
            if can_auto_write_price
            else WRITE_NOT_ALLOWED_REASON
        ),
        "preview_candidate": {
            "actions": [
                {
                    "room_type": "高级大床房",
                    "date_label": "today",
                    "checkin_date": "2026-06-24",
                    "breakfast_kind": "none",
                    "current_price_yuan": 420,
                    "proposed_price_yuan": 450,
                    "recommended_adjustment_yuan": 30,
                    "trigger_reason": "竞品上涨超过阈值。",
                }
            ]
        },
    }


class _FakePricingBrowserService:
    def __init__(self, *, dry_run_status: str, execute_status: str) -> None:
        self.dry_run_status = dry_run_status
        self.execute_status = execute_status
        self.execute_called = False

    async def prepare_pricing_page_write_dry_run(self, **_: Any) -> dict[str, Any]:
        return {
            "status": self.dry_run_status,
            "summary": "携程 EBK 需要登录或人工验证。",
            "platform_account_id": "acct-1",
            "browser_session_id": "browser-1",
            "data": {"captured_at": "2026-06-23 10:00"},
            "evidence": {"human_required_reason": "login_required"},
            "next_action": "请先人工登录携程 EBK。",
        }

    async def execute_pricing_page_write(self, **_: Any) -> dict[str, Any]:
        self.execute_called = True
        return {
            "status": self.execute_status,
            "summary": "携程 EBK 房价页结构不确定。",
            "platform_account_id": "acct-1",
            "browser_session_id": "browser-1",
            "data": {"captured_at": "2026-06-23 10:01"},
            "evidence": {"blocked_reason": "pricing_page_not_recognized"},
            "next_action": "人工复核 EBK 房价页。",
        }


if __name__ == "__main__":
    unittest.main()
