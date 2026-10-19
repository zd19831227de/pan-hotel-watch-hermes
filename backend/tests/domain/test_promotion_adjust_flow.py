from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from app.domain.browser.sessions import BrowserSession
from app.domain.notifications import feishu_bot
from app.domain.promotion_adjust import execution as promotion_execution
from app.domain.promotion_adjust import pipeline as promotion_pipeline
from app.domain.promotion_adjust import preview as promotion_preview
from app.domain.promotion_adjust.approvals import (
    record_promotion_adjust_approval_decision,
)
from app.domain.promotion_adjust.commands import classify_promotion_adjust_command
from app.domain.promotion_adjust.execution import (
    execute_promotion_adjust_write,
    prepare_promotion_adjust_execution_preflight,
    prepare_promotion_adjust_page_write_dry_run,
)
from app.domain.promotion_adjust.preview import create_promotion_adjust_preview


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
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _browser_account(
    *,
    platform: str = "ctrip",
    connection_mode: str = "non_direct",
    chat_id: str = "chat-promotion",
) -> BrowserSession:
    return BrowserSession(
        browser_session_id="bs-promotion-1",
        platform_account_id="acct-promotion-1",
        display_name="促销测试账号",
        platform=platform,
        session_port=9221,
        intended_use="ebk",
        connection_mode=connection_mode,
        pricing_feishu_chat_id=chat_id,
        last_heartbeat_at="2026-09-08T10:00:00",
        takeover_url="https://example.invalid/takeover",
        last_action="test",
    )


def _promotions() -> list[dict[str, Any]]:
    return [
        {
            "promotion_name": "基础促销组 天天特价",
            "room_scope": "全房型（包括新增）",
            "date_text": "2026年9月6日-2026年9月12日 到期前自动延期",
            "discount_text": "立减 1%",
            "discount_percent": 1.0,
            "discount_kind": "single",
            "multi_rule": False,
        },
        {
            "promotion_name": "场景促销组 提前预订",
            "room_scope": "预露台双卧套房",
            "date_text": "2026年9月3日-2026年9月23日",
            "discount_text": "提前3天预订 立减 35% 提前1天预订 立减 38%",
            "discount_percent": None,
            "discount_kind": "multi",
            "multi_rule": True,
        },
        {
            "promotion_name": "场景促销组 学生专享",
            "room_scope": "全房型（包括新增）",
            "date_text": "2026年9月1日-2026年9月30日",
            "discount_text": "立减 8%",
            "discount_percent": 8.0,
            "discount_kind": "single",
            "multi_rule": False,
        },
    ]


class _FakePromotionBrowserService:
    def __init__(
        self,
        *,
        read_result: dict[str, Any] | None = None,
        dry_run_result: dict[str, Any] | None = None,
        execute_result: dict[str, Any] | None = None,
    ) -> None:
        self.read_result = read_result or {
            "status": "success",
            "summary": "读取到 3 个已参加促销。",
            "promotions": _promotions(),
        }
        self.dry_run_result = dry_run_result or {
            "status": "page_write_preview_ready",
            "summary": "促销编辑页核验通过。",
            "platform_account_id": "acct-promotion-1",
            "browser_session_id": "bs-promotion-1",
            "data": {
                "captured_at": "2026-09-08T10:00:00",
                "promotion_read": {
                    "promotion_name": "基础促销组 天天特价",
                    "current_discount_percent": 1.0,
                },
            },
            "evidence": {"dry_run": True, "discount_input_located": True},
        }
        self.execute_result = execute_result or {
            "status": "executed",
            "summary": "「基础促销组 天天特价」立减已调整为 5%，页面回读一致。",
            "platform_account_id": "acct-promotion-1",
            "browser_session_id": "bs-promotion-1",
            "data": {
                "captured_at": "2026-09-08T10:01:00",
                "promotion_read": {"promotion_name": "基础促销组 天天特价"},
                "discount_fill_result": {
                    "status": "filled",
                    "filled_value": "5",
                    "verified_value": "5",
                },
                "promotion_rules_agreement": {
                    "status": "checked",
                    "label_found": True,
                    "control_found": True,
                    "control_type": "custom",
                    "checked": True,
                    "clicked": True,
                },
                "submit_result": {"clicked": True, "left_edit_page": True},
                "readback_result": {
                    "readback_discount_percent": 5.0,
                    "target_discount_percent": 5.0,
                },
            },
            "evidence": {"browser_write_invoked": True, "readback_percent_matches_target": True},
        }
        self.read_calls: list[dict[str, Any]] = []
        self.dry_run_calls: list[dict[str, Any]] = []
        self.execute_calls: list[dict[str, Any]] = []

    async def read_promotion_enrollments(self, **kwargs: Any) -> dict[str, Any]:
        self.read_calls.append(kwargs)
        return self.read_result

    async def prepare_promotion_adjust_page_write_dry_run(self, **kwargs: Any) -> dict[str, Any]:
        self.dry_run_calls.append(kwargs)
        return self.dry_run_result

    async def execute_promotion_adjust_write(self, **kwargs: Any) -> dict[str, Any]:
        self.execute_calls.append(kwargs)
        return self.execute_result


class _FakeFeishuClient:
    def __init__(self, chat_id: str | None = None) -> None:
        self.chat_id = chat_id
        self.replied_texts: list[str] = []
        self.replied_cards: list[dict[str, Any]] = []
        self.sent_cards: list[dict[str, Any]] = []

    async def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        self.replied_texts.append(text)
        return {"status": "ok", "message_id": message_id}

    async def reply_interactive_card(
        self,
        message_id: str,
        card: dict[str, Any],
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        self.replied_cards.append(card)
        return {"status": "ok", "message_id": message_id}

    async def send_interactive_card(
        self,
        chat_id: str,
        card: dict[str, Any],
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        self.sent_cards.append(card)
        return {"status": "ok", "chat_id": chat_id}


def _current_browser_account_patch(
    *,
    connection_mode: str = "non_direct",
    platform: str = "ctrip",
    chat_id: str = "chat-promotion",
):
    current_store = mock.Mock()
    current_store.get.return_value = _browser_account(
        platform=platform,
        connection_mode=connection_mode,
        chat_id=chat_id,
    )
    return mock.patch.object(
        promotion_execution,
        "get_browser_session_store",
        return_value=current_store,
    )


class PromotionAdjustPreviewTests(unittest.TestCase):
    def test_partial_name_matches_full_page_name(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            return await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(),
                actor_id="tester",
                reason="测试",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "preview_ready")
        action = result.preview_candidate["actions"][0]
        self.assertEqual(action["promotion_name"], "基础促销组 天天特价")
        self.assertEqual(action["current_discount_percent"], 1.0)
        self.assertEqual(action["target_discount_percent"], 5.0)

    def test_multi_rule_promotion_blocked_at_preview(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            return await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="提前预订",
                target_discount_percent=10,
                pricing_account=_browser_account(),
                actor_id="tester",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "multi_rule_promotion")
        self.assertIn("多条促销规则", result.summary)

    def test_no_change_required_when_percent_equal(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            return await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=1,
                pricing_account=_browser_account(),
                actor_id="tester",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "no_change_required")

    def test_non_ctrip_account_rejected(self) -> None:
        async def scenario() -> Any:
            return await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(platform="meituan"),
                actor_id="tester",
            )

        with _isolated_sqlite_database():
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "write_not_allowed")
        self.assertIn("仅支持携程", result.summary)


class PromotionApprovalAndExecutionTests(unittest.TestCase):
    def test_browser_exception_message_is_not_returned_or_persisted(self) -> None:
        fake_service = _FakePromotionBrowserService()
        fake_service.prepare_promotion_adjust_page_write_dry_run = mock.AsyncMock(
            side_effect=RuntimeError(
                r"C:\pan-hotel-watch-runtime\ctrip-profile "
                "token=abc webhook=https://secret"
            )
        )

        async def scenario() -> tuple[Any, dict[str, Any]]:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            preflight = await prepare_promotion_adjust_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0),
                actor_id="tester",
            )
            failed = await prepare_promotion_adjust_page_write_dry_run(
                execution_record_id=int(preflight.execution_record_id or 0),
                actor_id="tester",
            )
            from app.domain.promotion_adjust.execution import get_promotion_adjust_execution

            persisted = await get_promotion_adjust_execution(int(failed.execution_record_id or 0))
            return failed, persisted or {}

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_execution, "_promotion_browser_service", return_value=fake_service
        ), _current_browser_account_patch():
            failed, persisted = asyncio.run(scenario())

        combined = f"{failed.model_dump(mode='json')} {persisted}"
        self.assertEqual(failed.status, "failed")
        self.assertIn("RuntimeError", combined)
        self.assertNotIn("ctrip-profile", combined)
        self.assertNotIn("token=abc", combined)
        self.assertNotIn("https://secret", combined)

    def test_full_chain_preflight_dry_run_execute(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            preflight = await prepare_promotion_adjust_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0),
                actor_id="tester",
            )
            dry_run = await prepare_promotion_adjust_page_write_dry_run(
                execution_record_id=int(preflight.execution_record_id or 0),
                actor_id="tester",
            )
            execute = await execute_promotion_adjust_write(
                execution_record_id=int(dry_run.execution_record_id or 0),
                actor_id="tester",
            )
            return preview, approval, preflight, dry_run, execute

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_execution, "_promotion_browser_service", return_value=fake_service
        ), _current_browser_account_patch():
            preview, approval, preflight, dry_run, execute = asyncio.run(scenario())
        self.assertEqual(preview.status, "preview_ready")
        self.assertEqual(approval.status, "approved")
        self.assertEqual(preflight.status, "preflight_ready")
        self.assertEqual(dry_run.status, "page_write_preview_ready")
        self.assertEqual(execute.status, "executed")
        self.assertEqual(
            execute.data["promotion_rules_agreement"]["control_type"], "custom"
        )
        self.assertTrue(execute.data["promotion_rules_agreement"]["checked"])
        executed_request = fake_service.execute_calls[0]["execute_request"]
        self.assertEqual(executed_request["target_discount_percent"], 5)
        self.assertEqual(
            executed_request["actions"][0]["promotion_name"], "基础促销组 天天特价"
        )

    def test_api_source_approval_cannot_preflight(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="api",
            )
            return await prepare_promotion_adjust_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0),
                actor_id="tester",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), _current_browser_account_patch():
            preflight = asyncio.run(scenario())
        self.assertEqual(preflight.status, "not_approved")

    def test_direct_account_blocked_at_preflight(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(connection_mode="direct"),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            return await prepare_promotion_adjust_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0),
                actor_id="tester",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), _current_browser_account_patch(connection_mode="direct"):
            preflight = asyncio.run(scenario())
        self.assertEqual(preflight.status, "write_not_allowed")
        self.assertIn("直连或未确认账号", preflight.summary)

    def test_account_downgrade_after_approval_blocks_preflight(self) -> None:
        fake_service = _FakePromotionBrowserService()
        current_store = mock.Mock()
        current_store.get.return_value = _browser_account(connection_mode="direct")

        async def scenario() -> Any:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(connection_mode="non_direct"),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            return await prepare_promotion_adjust_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0),
                actor_id="tester",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_execution,
            "get_browser_session_store",
            return_value=current_store,
            create=True,
        ):
            preflight = asyncio.run(scenario())

        self.assertEqual(preflight.status, "write_not_allowed")
        self.assertEqual(len(fake_service.dry_run_calls), 0)
        self.assertEqual(len(fake_service.execute_calls), 0)

    def test_account_downgrade_blocks_dry_run_and_execute_stages(self) -> None:
        fake_service = _FakePromotionBrowserService()
        current_store = mock.Mock()
        current_store.get.return_value = _browser_account(connection_mode="non_direct")

        async def approved_preflight() -> Any:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(connection_mode="non_direct"),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            return await prepare_promotion_adjust_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0),
                actor_id="tester",
            )

        async def scenario() -> tuple[Any, Any]:
            preflight_one = await approved_preflight()
            current_store.get.return_value = _browser_account(connection_mode="direct")
            blocked_dry_run = await prepare_promotion_adjust_page_write_dry_run(
                execution_record_id=int(preflight_one.execution_record_id or 0),
                actor_id="tester",
            )

            current_store.get.return_value = _browser_account(connection_mode="non_direct")
            preflight_two = await approved_preflight()
            dry_run = await prepare_promotion_adjust_page_write_dry_run(
                execution_record_id=int(preflight_two.execution_record_id or 0),
                actor_id="tester",
            )
            current_store.get.return_value = _browser_account(connection_mode="unknown")
            blocked_execute = await execute_promotion_adjust_write(
                execution_record_id=int(dry_run.execution_record_id or 0),
                actor_id="tester",
            )
            return blocked_dry_run, blocked_execute

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_execution, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_execution, "get_browser_session_store", return_value=current_store
        ):
            blocked_dry_run, blocked_execute = asyncio.run(scenario())

        self.assertEqual(blocked_dry_run.status, "write_not_allowed")
        self.assertEqual(blocked_execute.status, "write_not_allowed")
        self.assertEqual(len(fake_service.dry_run_calls), 1)
        self.assertEqual(len(fake_service.execute_calls), 0)

    def test_execute_percent_lock_against_tampered_request(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            preflight = await prepare_promotion_adjust_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0),
                actor_id="tester",
            )
            dry_run = await prepare_promotion_adjust_page_write_dry_run(
                execution_record_id=int(preflight.execution_record_id or 0),
                actor_id="tester",
            )
            # 篡改 dry-run 记录中的目标立减，执行段必须拒绝。
            from app.db.session import session_scope
            from app.domain.promotion_adjust.models import PromotionAdjustExecutionRecord

            async with session_scope() as session:
                record = await session.get(
                    PromotionAdjustExecutionRecord, int(dry_run.execution_record_id or 0)
                )
                request = dict(record.execute_request_json or {})
                request["target_discount_percent"] = 50
                record.execute_request_json = request
            return await execute_promotion_adjust_write(
                execution_record_id=int(dry_run.execution_record_id or 0),
                actor_id="tester",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_execution, "_promotion_browser_service", return_value=fake_service
        ), _current_browser_account_patch():
            execute = asyncio.run(scenario())
        self.assertEqual(execute.status, "data_required")
        self.assertEqual(len(fake_service.execute_calls), 0)

    def test_reject_returns_feedback_toast_via_feishu_event(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            command_result = await feishu_bot.handle_event(
                _message_payload("促销调整 天天特价 立减调到5")
            )
            preview_id = int(command_result["promotion_adjust_preview"]["preview_id"])
            return await feishu_bot.handle_event(
                _card_action_payload(preview_id=preview_id, decision="reject")
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=_FakeFeishuClient()
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "rejected")
        toast = result["card_callback_response"]["toast"]
        self.assertEqual(toast["type"], "info")
        self.assertIn("暂不调整", toast["content"])
        self.assertFalse(result["evidence"]["execute_triggered"])


def _message_payload(text: str, *, chat_id: str = "chat-promotion") -> dict[str, Any]:
    return {
        "header": {"event_id": "evt-promotion", "event_type": "im.message.receive_v1"},
        "event": {
            "message": {
                "message_id": "om_promotion",
                "chat_id": chat_id,
                "content": f'{{"text":"@机器人 {text}"}}',
            },
            "sender": {"sender_id": {"open_id": "ou_op"}},
        },
    }


def _card_action_payload(
    *,
    preview_id: int,
    decision: str = "approve",
    chat_id: str = "chat-promotion",
) -> dict[str, Any]:
    return {
        "header": {"event_id": "evt-card-promotion", "event_type": "card.action.trigger"},
        "event": {
            "action": {
                "value": {
                    "workflow": "promotion_adjust_approval",
                    "preview_id": preview_id,
                    "decision": decision,
                }
            },
            "context": {"open_chat_id": chat_id},
            "operator": {"open_id": "ou_op", "union_id": "un_op"},
            "message_id": "om_card",
        },
    }


class FeishuBotPromotionCommandTests(unittest.TestCase):
    def test_list_query_replies_list_card(self) -> None:
        fake_service = _FakePromotionBrowserService()
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            return await feishu_bot.handle_event(_message_payload("促销列表"))

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=fake_client
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "promotion_list_replied")
        self.assertEqual(len(fake_service.read_calls), 1)
        card_text = str(fake_client.replied_cards[0])
        self.assertIn("天天特价", card_text)
        self.assertIn("多条规则", card_text)

    def test_ready_command_replies_approval_card(self) -> None:
        fake_service = _FakePromotionBrowserService()
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            return await feishu_bot.handle_event(
                _message_payload("促销调整 天天特价 立减调到5")
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=fake_client
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "approval_card_replied")
        card_text = str(fake_client.replied_cards[0])
        self.assertIn("确认调整促销", card_text)
        self.assertIn("暂不调整", card_text)

    def test_approval_card_action_queues_pipeline(self) -> None:
        fake_service = _FakePromotionBrowserService()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            command_result = await feishu_bot.handle_event(
                _message_payload("促销调整 天天特价 立减调到5")
            )
            preview_id = int(command_result["promotion_adjust_preview"]["preview_id"])
            return await feishu_bot.handle_event(
                _card_action_payload(preview_id=preview_id)
            )

        queued: list[dict[str, Any]] = []

        def fake_queue(**kwargs: Any) -> bool:
            queued.append(kwargs)
            return True

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "queue_promotion_adjust_execution_pipeline", side_effect=fake_queue
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=_FakeFeishuClient()
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "approved")
        self.assertTrue(result["evidence"]["execute_triggered"])
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["chat_id"], "chat-promotion")

    def test_data_qa_phrase_not_intercepted(self) -> None:
        """「促销效果」等数据中心问答不得被促销指令层截胡（走 ignore/AI 链路）。"""

        async def scenario() -> Any:
            return await feishu_bot.handle_event(_message_payload("促销效果怎么样"))

        with mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=_FakeFeishuClient()
        ):
            result = asyncio.run(scenario())
        self.assertNotIn(
            result["status"],
            {"promotion_list_replied", "approval_card_replied", "needs_confirmation"},
        )


class PromotionPipelineTests(unittest.TestCase):
    def test_background_task_error_log_does_not_include_exception_message(self) -> None:
        async def scenario() -> None:
            async def fail() -> None:
                raise RuntimeError(
                    r"C:\pan-hotel-watch-runtime\ctrip-profile "
                    "token=abc webhook=https://secret"
                )

            task = asyncio.create_task(fail())
            await asyncio.sleep(0)
            with mock.patch.object(promotion_pipeline.logger, "error") as error_log:
                promotion_pipeline._log_background_task_outcome(task)

            logged = " ".join(str(value) for call in error_log.call_args_list for value in call.args)
            self.assertIn("RuntimeError", logged)
            self.assertNotIn("ctrip-profile", logged)
            self.assertNotIn("token=abc", logged)
            self.assertNotIn("https://secret", logged)

        asyncio.run(scenario())

    def test_pipeline_sends_result_card_on_success(self) -> None:
        fake_service = _FakePromotionBrowserService()
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            await promotion_pipeline.run_promotion_adjust_execution_pipeline(
                approval_record_id=int(approval.approval_record_id or 0),
                chat_id="chat-promotion",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_execution, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_pipeline, "FeishuBotClient", return_value=fake_client
        ), _current_browser_account_patch():
            asyncio.run(scenario())
        self.assertEqual(len(fake_service.execute_calls), 1)
        self.assertEqual(len(fake_client.sent_cards), 1)
        self.assertIn("促销调整执行结果", str(fake_client.sent_cards[0]))

    def test_pipeline_sends_blocked_card_when_preflight_fails(self) -> None:
        fake_service = _FakePromotionBrowserService()
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            preview = await create_promotion_adjust_preview(
                chat_id="chat-promotion",
                promotion_name="天天特价",
                target_discount_percent=5,
                pricing_account=_browser_account(connection_mode="direct"),
                actor_id="tester",
            )
            approval = await record_promotion_adjust_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            await promotion_pipeline.run_promotion_adjust_execution_pipeline(
                approval_record_id=int(approval.approval_record_id or 0),
                chat_id="chat-promotion",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            promotion_preview, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_execution, "_promotion_browser_service", return_value=fake_service
        ), mock.patch.object(
            promotion_pipeline, "FeishuBotClient", return_value=fake_client
        ), _current_browser_account_patch(connection_mode="direct"):
            asyncio.run(scenario())
        self.assertEqual(len(fake_client.sent_cards), 1)
        card_text = str(fake_client.sent_cards[0])
        self.assertIn("促销调整流程阻断", card_text)
        self.assertIn("不允许自动调整促销", card_text)


if __name__ == "__main__":
    unittest.main()
