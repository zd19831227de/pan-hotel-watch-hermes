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
from app.domain.pyramid_bidding import execution as pyramid_execution
from app.domain.pyramid_bidding import pipeline as pyramid_pipeline
from app.domain.pyramid_bidding import preview as pyramid_preview
from app.domain.pyramid_bidding.approvals import record_pyramid_bid_approval_decision
from app.domain.pyramid_bidding.cards import build_pyramid_bid_approval_card
from app.domain.pyramid_bidding.commands import classify_pyramid_bid_command
from app.domain.pyramid_bidding.execution import (
    execute_pyramid_bid_write,
    prepare_pyramid_bid_execution_preflight,
    prepare_pyramid_bid_page_write_dry_run,
)
from app.domain.pyramid_bidding.preview import create_pyramid_bid_preview


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
    chat_id: str = "chat-pyramid",
) -> BrowserSession:
    return BrowserSession(
        browser_session_id="bs-pyramid-1",
        platform_account_id="acct-pyramid-1",
        display_name="金字塔测试账号",
        platform=platform,
        session_port=9221,
        intended_use="ebk",
        connection_mode=connection_mode,
        pricing_feishu_chat_id=chat_id,
        last_heartbeat_at="2026-09-03T10:00:00",
        takeover_url="https://example.invalid/takeover",
        last_action="test",
    )


def _promotions() -> list[dict[str, Any]]:
    return [
        {
            "campaign_name": "14--16",
            "campaign_status": "推广中",
            "promotion_date_text": "2025年8月20日 - 持续推广",
            "click_bid_cny": 5.5,
            "daily_budget_cny": 300.0,
            "today_spend_cny": 300.0,
            "smart_campaign": False,
            "has_modify_button": True,
        },
        {
            "campaign_name": "22--24",
            "campaign_status": "推广中",
            "promotion_date_text": "2025年8月20日 - 持续推广",
            "click_bid_cny": 5.5,
            "daily_budget_cny": 400.0,
            "today_spend_cny": 4.66,
            "smart_campaign": False,
            "has_modify_button": True,
        },
    ]


class _FakePyramidBrowserService:
    def __init__(
        self,
        *,
        read_result: dict[str, Any] | None = None,
        dry_run_result: dict[str, Any] | None = None,
        execute_result: dict[str, Any] | None = None,
    ) -> None:
        self.read_result = read_result or {
            "status": "success",
            "summary": "读取到 2 个金字塔推广计划。",
            "promotions": _promotions(),
        }
        self.dry_run_result = dry_run_result or {
            "status": "page_write_preview_ready",
            "summary": "金字塔页面核验通过。",
            "platform_account_id": "acct-pyramid-1",
            "browser_session_id": "bs-pyramid-1",
            "data": {
                "captured_at": "2026-09-03T10:00:00",
                "campaign_read": {"campaign_name": "14--16", "floor_price_cny": 2.1},
            },
            "evidence": {"dry_run": True, "bid_input_located": True},
        }
        self.execute_result = execute_result or {
            "status": "executed",
            "summary": "「14--16」点击出价已调整为 ¥6，页面回读一致。",
            "platform_account_id": "acct-pyramid-1",
            "browser_session_id": "bs-pyramid-1",
            "data": {
                "captured_at": "2026-09-03T10:01:00",
                "campaign_read": {"campaign_name": "14--16"},
                "bid_fill_result": {"status": "filled", "filled_value": "6", "verified_value": "6"},
                "submit_result": {"clicked": True, "left_edit_page": True},
                "readback_result": {"readback_bid_cny": 6.0, "target_bid_cny": 6.0},
            },
            "evidence": {"browser_write_invoked": True, "readback_bid_matches_target": True},
        }
        self.read_calls: list[dict[str, Any]] = []
        self.dry_run_calls: list[dict[str, Any]] = []
        self.execute_calls: list[dict[str, Any]] = []

    async def read_pyramid_promotions(self, **kwargs: Any) -> dict[str, Any]:
        self.read_calls.append(kwargs)
        return self.read_result

    async def prepare_pyramid_bid_page_write_dry_run(self, **kwargs: Any) -> dict[str, Any]:
        self.dry_run_calls.append(kwargs)
        return self.dry_run_result

    async def execute_pyramid_bid_write(self, **kwargs: Any) -> dict[str, Any]:
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


class PyramidBidPreviewTests(unittest.TestCase):
    def test_config_required_without_bound_account(self) -> None:
        async def scenario() -> Any:
            return await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=None,
            )

        with _isolated_sqlite_database():
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "config_required")
        self.assertIn("绑定", result.summary)

    def test_preview_ready_with_current_bid(self) -> None:
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            return await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
                reason="金字塔调价 14--16 出价调到6",
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "preview_ready")
        self.assertTrue(result.can_auto_write_price)
        self.assertEqual(result.connection_mode, "non_direct")
        action = result.preview_candidate["actions"][0]
        self.assertEqual(action["campaign_name"], "14--16")
        self.assertEqual(action["current_bid_cny"], 5.5)
        self.assertEqual(action["target_bid_cny"], 6.0)
        self.assertEqual(action["write_intent"]["arguments"]["target_bid"], 6.0)
        self.assertEqual(len(fake.read_calls), 1)
        self.assertIsNotNone(result.preview_id)

    def test_campaign_not_found_lists_available(self) -> None:
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            return await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="不存在的计划",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "campaign_not_found")
        self.assertIn("14--16", result.summary)

    def test_dash_variant_campaign_name_still_matches(self) -> None:
        """2026-09-08 线上问题：用户说「周内0-2」而页面计划名是「周内0--2」等
        横线变体时，预览阶段也要匹配成功，不判 campaign_not_found。"""
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            return await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14-16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "preview_ready")
        action = result.preview_candidate["actions"][0]
        self.assertEqual(action["campaign_name"], "14--16")

    def test_no_change_required_when_bid_equal(self) -> None:
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            return await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=5.5,
                pricing_account=_browser_account(),
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "no_change_required")

    def test_read_failed_blocks_preview(self) -> None:
        fake = _FakePyramidBrowserService(
            read_result={"status": "human_required", "summary": "需要登录。", "promotions": []}
        )

        async def scenario() -> Any:
            return await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "read_failed")

    def test_non_ctrip_account_rejected(self) -> None:
        async def scenario() -> Any:
            return await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(platform="meituan"),
            )

        with _isolated_sqlite_database():
            result = asyncio.run(scenario())
        self.assertEqual(result.status, "write_not_allowed")
        self.assertIn("携程", result.summary)


class PyramidApprovalAndExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current_store = mock.Mock()
        self.current_store.get.return_value = _browser_account()
        self.current_store_patch = mock.patch.object(
            pyramid_execution,
            "get_browser_session_store",
            return_value=self.current_store,
            create=True,
        )
        self.current_store_patch.start()
        self.addCleanup(self.current_store_patch.stop)

    def _approved_preview_id(self, fake: _FakePyramidBrowserService, *, account: BrowserSession | None = None) -> int:
        preview = asyncio.run(
            create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=account or _browser_account(),
            )
        )
        assert preview.preview_id is not None
        return int(preview.preview_id)

    def test_full_chain_preflight_dry_run_execute(self) -> None:
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
                actor_id="op-1",
            )
            preflight = await prepare_pyramid_bid_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0)
            )
            dry_run = await prepare_pyramid_bid_page_write_dry_run(
                execution_record_id=int(preflight.execution_record_id or 0)
            )
            execute = await execute_pyramid_bid_write(
                execution_record_id=int(dry_run.execution_record_id or 0)
            )
            return preview, approval, preflight, dry_run, execute

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            preview, approval, preflight, dry_run, execute = asyncio.run(scenario())
        self.assertEqual(approval.status, "approved")
        self.assertEqual(preflight.status, "preflight_ready")
        self.assertEqual(dry_run.status, "page_write_preview_ready")
        self.assertEqual(execute.status, "executed")
        self.assertEqual(len(fake.dry_run_calls), 1)
        self.assertEqual(len(fake.execute_calls), 1)
        executed_request = fake.execute_calls[0]["execute_request"]
        self.assertEqual(executed_request["target_bid_cny"], 6.0)
        self.assertEqual(preview.preview_id, preflight.preview_id)

    def test_browser_exception_details_are_not_exposed_or_persisted(self) -> None:
        fake = _FakePyramidBrowserService()
        fake.prepare_pyramid_bid_page_write_dry_run = mock.AsyncMock(
            side_effect=RuntimeError(
                r"C:\pan-hotel-watch-runtime\ctrip-profile "
                "token=abc webhook=https://secret"
            )
        )

        async def scenario() -> tuple[Any, dict[str, Any]]:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            preflight = await prepare_pyramid_bid_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0)
            )
            failed = await prepare_pyramid_bid_page_write_dry_run(
                execution_record_id=int(preflight.execution_record_id or 0)
            )
            from app.domain.pyramid_bidding.execution import get_pyramid_bid_execution

            persisted = await get_pyramid_bid_execution(int(failed.execution_record_id or 0))
            return failed, persisted or {}

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            failed, persisted = asyncio.run(scenario())

        combined = f"{failed.model_dump(mode='json')} {persisted}"
        self.assertEqual(failed.status, "failed")
        self.assertIn("RuntimeError", combined)
        self.assertNotIn("ctrip-profile", combined)
        self.assertNotIn("token=abc", combined)
        self.assertNotIn("https://secret", combined)

    def test_api_source_approval_cannot_preflight(self) -> None:
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="api",
                actor_id="op-1",
            )
            assert approval.approval_record_id is not None
            return await prepare_pyramid_bid_execution_preflight(
                approval_record_id=approval.approval_record_id
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            preflight = asyncio.run(scenario())
        self.assertEqual(preflight.status, "not_approved")

    def test_direct_account_blocked_at_preflight(self) -> None:
        fake = _FakePyramidBrowserService()
        self.current_store.get.return_value = _browser_account(connection_mode="direct")

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(connection_mode="direct"),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
                actor_id="op-1",
            )
            assert approval.approval_record_id is not None
            return approval, await prepare_pyramid_bid_execution_preflight(
                approval_record_id=approval.approval_record_id
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            approval, preflight = asyncio.run(scenario())
        self.assertEqual(approval.status, "write_not_allowed")
        self.assertEqual(preflight.status, "write_not_allowed")
        self.assertEqual(len(fake.dry_run_calls), 0)
        self.assertEqual(len(fake.execute_calls), 0)

    def test_account_downgrade_after_approval_blocks_preflight(self) -> None:
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(connection_mode="non_direct"),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            self.current_store.get.return_value = _browser_account(connection_mode="direct")
            return await prepare_pyramid_bid_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0)
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            preflight = asyncio.run(scenario())

        self.assertEqual(preflight.status, "write_not_allowed")
        self.assertEqual(len(fake.dry_run_calls), 0)
        self.assertEqual(len(fake.execute_calls), 0)

    def test_account_downgrade_blocks_dry_run_and_execute_stages(self) -> None:
        fake = _FakePyramidBrowserService()

        async def approved_preflight() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(connection_mode="non_direct"),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            return await prepare_pyramid_bid_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0)
            )

        async def scenario() -> tuple[Any, Any]:
            preflight_one = await approved_preflight()
            self.current_store.get.return_value = _browser_account(connection_mode="direct")
            blocked_dry_run = await prepare_pyramid_bid_page_write_dry_run(
                execution_record_id=int(preflight_one.execution_record_id or 0)
            )

            self.current_store.get.return_value = _browser_account(connection_mode="non_direct")
            preflight_two = await approved_preflight()
            dry_run = await prepare_pyramid_bid_page_write_dry_run(
                execution_record_id=int(preflight_two.execution_record_id or 0)
            )
            self.current_store.get.return_value = _browser_account(connection_mode="unknown")
            blocked_execute = await execute_pyramid_bid_write(
                execution_record_id=int(dry_run.execution_record_id or 0)
            )
            return blocked_dry_run, blocked_execute

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            blocked_dry_run, blocked_execute = asyncio.run(scenario())

        self.assertEqual(blocked_dry_run.status, "write_not_allowed")
        self.assertEqual(blocked_execute.status, "write_not_allowed")
        self.assertEqual(len(fake.dry_run_calls), 1)
        self.assertEqual(len(fake.execute_calls), 0)

    def test_account_binding_change_blocks_preflight(self) -> None:
        fake = _FakePyramidBrowserService()
        self.current_store.get.return_value = _browser_account(chat_id="chat-other")

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            return await prepare_pyramid_bid_execution_preflight(
                approval_record_id=int(approval.approval_record_id or 0)
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            preflight = asyncio.run(scenario())

        self.assertEqual(preflight.status, "write_not_allowed")
        self.assertEqual(len(fake.dry_run_calls), 0)
        self.assertEqual(len(fake.execute_calls), 0)

    def test_duplicate_decision_is_idempotent(self) -> None:
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )
            first = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            second = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="reject",
                source="feishu_card",
            )
            return first, second

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            first, second = asyncio.run(scenario())
        self.assertEqual(first.status, "approved")
        self.assertEqual(second.status, "already_decided")

    def test_execute_price_lock_against_tampered_preview(self) -> None:
        fake = _FakePyramidBrowserService()

        async def scenario() -> Any:
            from app.db.session import session_scope
            from app.domain.pyramid_bidding.models import PyramidBidPreviewRecord

            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            assert approval.approval_record_id is not None
            preflight = await prepare_pyramid_bid_execution_preflight(
                approval_record_id=approval.approval_record_id
            )
            dry_run = await prepare_pyramid_bid_page_write_dry_run(
                execution_record_id=int(preflight.execution_record_id or 0)
            )
            # 预检锁定目标价之后篡改预览，执行链必须拒绝
            async with session_scope() as session:
                record = await session.get(PyramidBidPreviewRecord, preview.preview_id)
                candidate = dict(record.preview_candidate_json)
                candidate["actions"] = [
                    {**candidate["actions"][0], "target_bid_cny": 9.9}
                ]
                record.preview_candidate_json = candidate
            execute = await execute_pyramid_bid_write(
                execution_record_id=int(dry_run.execution_record_id or 0)
            )
            return execute

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            execute = asyncio.run(scenario())
        self.assertEqual(execute.status, "data_required")
        self.assertEqual(len(fake.execute_calls), 0)

    def test_dry_run_floor_violation_blocks(self) -> None:
        fake = _FakePyramidBrowserService(
            dry_run_result={
                "status": "floor_price_violation",
                "summary": "目标出价 ¥1 低于页面起价 ¥2.1，已停止调整。",
                "platform_account_id": "acct-pyramid-1",
                "browser_session_id": "bs-pyramid-1",
                "data": {},
                "evidence": {"floor_price_cny": 2.1},
                "next_action": "请把目标出价提高到页面起价之上后重新发起。",
            }
        )

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=1.0,
                pricing_account=_browser_account(),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            assert approval.approval_record_id is not None
            preflight = await prepare_pyramid_bid_execution_preflight(
                approval_record_id=approval.approval_record_id
            )
            return await prepare_pyramid_bid_page_write_dry_run(
                execution_record_id=int(preflight.execution_record_id or 0)
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            dry_run = asyncio.run(scenario())
        self.assertEqual(dry_run.status, "floor_price_violation")

    def test_execute_mismatch_needs_review(self) -> None:
        fake = _FakePyramidBrowserService(
            execute_result={
                "status": "mismatch",
                "summary": "提交后页面回读出价 ¥5.5 与目标 ¥6 不一致，需人工复核。",
                "platform_account_id": "acct-pyramid-1",
                "browser_session_id": "bs-pyramid-1",
                "data": {
                    "bid_fill_result": {"status": "filled", "filled_value": "6", "verified_value": "6"},
                    "readback_result": {"readback_bid_cny": 5.5},
                },
                "evidence": {"readback_bid_matches_target": False},
                "next_action": "请人工登录 EBK 金字塔页面复核。",
            }
        )

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            assert approval.approval_record_id is not None
            preflight = await prepare_pyramid_bid_execution_preflight(
                approval_record_id=approval.approval_record_id
            )
            dry_run = await prepare_pyramid_bid_page_write_dry_run(
                execution_record_id=int(preflight.execution_record_id or 0)
            )
            return await execute_pyramid_bid_write(
                execution_record_id=int(dry_run.execution_record_id or 0)
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake
        ):
            execute = asyncio.run(scenario())
        self.assertEqual(execute.status, "mismatch")


class FeishuBotPyramidCommandTests(unittest.TestCase):
    def _message_payload(self, text: str, *, chat_id: str = "chat-pyramid") -> dict[str, Any]:
        return {
            "header": {"event_id": "evt-pyramid", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_id": "om_pyramid",
                    "chat_id": chat_id,
                    "content": f'{{"text":"@机器人 {text}"}}',
                },
                "sender": {"sender_id": {"open_id": "ou_op"}},
            },
        }

    def _card_action_payload(
        self,
        *,
        preview_id: int,
        decision: str = "approve",
        chat_id: str = "chat-pyramid",
    ) -> dict[str, Any]:
        return {
            "header": {"event_id": "evt-card-pyramid", "event_type": "card.action.trigger"},
            "event": {
                "action": {
                    "value": {
                        "workflow": "pyramid_bid_approval",
                        "preview_id": preview_id,
                        "decision": decision,
                    }
                },
                "context": {"open_chat_id": chat_id},
                "operator": {"open_id": "ou_op", "union_id": "un_op"},
                "message_id": "om_card",
            },
        }

    def test_ready_command_replies_approval_card(self) -> None:
        fake_service = _FakePyramidBrowserService()
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            return await feishu_bot.handle_event(self._message_payload("金字塔调价 14--16 出价调到6"))

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=fake_client
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "approval_card_replied")
        self.assertFalse(result["evidence"]["execute_triggered"])
        card = fake_client.replied_cards[0]
        buttons_text = str(card)
        self.assertIn("确认调整出价", buttons_text)
        self.assertIn("14--16", buttons_text)

    def test_list_query_replies_list_card(self) -> None:
        fake_service = _FakePyramidBrowserService()
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            return await feishu_bot.handle_event(self._message_payload("金字塔出价"))

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=fake_client
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "pyramid_bid_list_replied")
        self.assertEqual(len(fake_service.read_calls), 1)
        self.assertIn("14--16", str(fake_client.replied_cards[0]))

    def test_needs_confirmation_replies_confirmation_card(self) -> None:
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            return await feishu_bot.handle_event(self._message_payload("金字塔出价调到6"))

        with mock.patch.object(feishu_bot, "_feishu_client_factory", return_value=fake_client):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(len(fake_client.replied_cards), 1)

    def test_unbound_chat_returns_config_required(self) -> None:
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = lambda chat_id: None
            return await feishu_bot.handle_event(self._message_payload("金字塔调价 14--16 出价调到6"))

        with _isolated_sqlite_database(), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=fake_client
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "config_required")
        self.assertEqual(len(fake_client.replied_texts), 1)

    def test_approval_card_action_queues_pipeline(self) -> None:
        fake_service = _FakePyramidBrowserService()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            command_result = await feishu_bot.handle_event(
                self._message_payload("金字塔调价 14--16 出价调到6")
            )
            preview_payload = command_result["pyramid_bid_preview"]
            return await feishu_bot.handle_event(
                self._card_action_payload(preview_id=int(preview_payload["preview_id"]))
            )

        queued: list[dict[str, Any]] = []

        def fake_queue(**kwargs: Any) -> bool:
            queued.append(kwargs)
            return True

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "queue_pyramid_bid_execution_pipeline", side_effect=fake_queue
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=_FakeFeishuClient()
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "approved")
        self.assertTrue(result["evidence"]["execute_triggered"])
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["chat_id"], "chat-pyramid")

    def test_approval_card_action_direct_account_not_queued(self) -> None:
        fake_service = _FakePyramidBrowserService()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = lambda chat_id: _browser_account(
                chat_id=chat_id or "", connection_mode="direct"
            )
            command_result = await feishu_bot.handle_event(
                self._message_payload("金字塔调价 14--16 出价调到6")
            )
            preview_payload = command_result["pyramid_bid_preview"]
            return await feishu_bot.handle_event(
                self._card_action_payload(preview_id=int(preview_payload["preview_id"]))
            )

        queued: list[dict[str, Any]] = []

        def fake_queue(**kwargs: Any) -> bool:
            queued.append(kwargs)
            return True

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "queue_pyramid_bid_execution_pipeline", side_effect=fake_queue
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=_FakeFeishuClient()
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "write_not_allowed")
        self.assertFalse(result["evidence"]["execute_triggered"])
        self.assertEqual(len(queued), 0)

    def test_approval_card_reject_returns_feedback_toast(self) -> None:
        """2026-09-08 需求：金字塔审批卡点「暂不调整」后必须返回飞书回执提示，
        不能只落库无任何可见反馈。"""
        fake_service = _FakePyramidBrowserService()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            command_result = await feishu_bot.handle_event(
                self._message_payload("金字塔调价 14--16 出价调到6")
            )
            preview_payload = command_result["pyramid_bid_preview"]
            return await feishu_bot.handle_event(
                self._card_action_payload(
                    preview_id=int(preview_payload["preview_id"]), decision="reject"
                )
            )

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=_FakeFeishuClient()
        ):
            result = asyncio.run(scenario())
        self.assertEqual(result["status"], "rejected")
        toast = result["card_callback_response"]["toast"]
        self.assertEqual(toast["type"], "info")
        self.assertIn("暂不调整", toast["content"])
        self.assertFalse(result["evidence"]["execute_triggered"])

    def test_approval_card_repeat_decision_returns_toast(self) -> None:
        fake_service = _FakePyramidBrowserService()

        async def scenario() -> Any:
            feishu_bot.get_pricing_browser_session_for_feishu_chat_id = (
                lambda chat_id: _browser_account(chat_id=chat_id or "")
            )
            command_result = await feishu_bot.handle_event(
                self._message_payload("金字塔调价 14--16 出价调到6")
            )
            preview_id = int(command_result["pyramid_bid_preview"]["preview_id"])
            first = await feishu_bot.handle_event(
                self._card_action_payload(preview_id=preview_id, decision="reject")
            )
            second = await feishu_bot.handle_event(
                self._card_action_payload(preview_id=preview_id, decision="approve")
            )
            return first, second

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake_service
        ), mock.patch.object(
            feishu_bot, "_feishu_client_factory", return_value=_FakeFeishuClient()
        ):
            first, second = asyncio.run(scenario())
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(second["status"], "already_decided")
        self.assertIn("已处理过", second["card_callback_response"]["toast"]["content"])
        self.assertFalse(second["evidence"]["execute_triggered"])

    def test_pyramid_command_takes_priority_over_manual_price(self) -> None:
        """含「金字塔」的调价指令不应落进房型调价的补全分支。"""
        classification = classify_pyramid_bid_command("把金字塔14--16的出价调到6元")
        self.assertEqual(classification["status"], "ready")


class PyramidPipelineTests(unittest.TestCase):
    def test_background_task_error_log_does_not_include_exception_message(self) -> None:
        async def scenario() -> None:
            async def fail() -> None:
                raise RuntimeError(
                    r"C:\pan-hotel-watch-runtime\ctrip-profile "
                    "token=abc webhook=https://secret"
                )

            task = asyncio.create_task(fail())
            await asyncio.sleep(0)
            with mock.patch.object(pyramid_pipeline.logger, "error") as error_log:
                pyramid_pipeline._log_background_task_outcome(task)

            logged = " ".join(str(value) for call in error_log.call_args_list for value in call.args)
            self.assertIn("RuntimeError", logged)
            self.assertNotIn("ctrip-profile", logged)
            self.assertNotIn("token=abc", logged)
            self.assertNotIn("https://secret", logged)

        asyncio.run(scenario())

    def test_pipeline_sends_result_card_on_success(self) -> None:
        fake_service = _FakePyramidBrowserService()
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            assert approval.approval_record_id is not None
            await pyramid_pipeline.run_pyramid_bid_execution_pipeline_guarded(
                approval_record_id=approval.approval_record_id,
                chat_id="chat-pyramid",
            )
            return fake_client.sent_cards

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_execution, "_pyramid_browser_service", return_value=fake_service
        ), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=fake_service
        ), mock.patch.object(
            pyramid_pipeline, "FeishuBotClient", return_value=fake_client
        ), mock.patch.object(
            pyramid_execution,
            "get_browser_session_store",
            return_value=mock.Mock(get=mock.Mock(return_value=_browser_account())),
        ):
            sent_cards = asyncio.run(scenario())
        self.assertEqual(len(sent_cards), 1)
        self.assertIn("金字塔调价执行结果", str(sent_cards[0]))

    def test_pipeline_sends_blocked_card_when_preflight_fails(self) -> None:
        fake_client = _FakeFeishuClient()

        async def scenario() -> Any:
            preview = await create_pyramid_bid_preview(
                chat_id="chat-pyramid",
                campaign_name="14--16",
                target_bid=6.0,
                pricing_account=_browser_account(connection_mode="direct"),
            )
            approval = await record_pyramid_bid_approval_decision(
                preview_id=int(preview.preview_id or 0),
                decision="approve",
                source="feishu_card",
            )
            assert approval.approval_record_id is not None
            await pyramid_pipeline.run_pyramid_bid_execution_pipeline_guarded(
                approval_record_id=approval.approval_record_id,
                chat_id="chat-pyramid",
            )
            return fake_client.sent_cards

        with _isolated_sqlite_database(), mock.patch.object(
            pyramid_preview, "_pyramid_browser_service", return_value=_FakePyramidBrowserService()
        ), mock.patch.object(
            pyramid_pipeline, "FeishuBotClient", return_value=fake_client
        ):
            sent_cards = asyncio.run(scenario())
        self.assertEqual(len(sent_cards), 1)
        self.assertIn("金字塔调价流程阻断", str(sent_cards[0]))
        self.assertIn("不允许自动调整出价", str(sent_cards[0]))


if __name__ == "__main__":
    unittest.main()
