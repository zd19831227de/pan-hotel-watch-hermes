from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.domain.browser.sessions import BrowserSession
from backend.tests.domain.test_competitor_watch_group_config import (
    _isolated_sqlite_database,
    _reload_browser_sessions as _reload_database_browser_sessions,
    _reload_db_session,
    _reload_repository,
    _restore_ebk_browser_session,
)


class CompetitorBrowserPoolFeishuPricingBoundaryTests(unittest.TestCase):
    def test_competitor_pricing_binding_blocks_non_ctrip_platform_change(self) -> None:
        with _isolated_sqlite_database() as db_url:
            repository = _reload_repository(db_url)
            sessions = _reload_database_browser_sessions()
            _restore_ebk_browser_session(
                sessions,
                browser_session_id="browser-ebk-pricing",
                platform_account_id="ctrip-ebk-pricing",
                session_port=9323,
            )
            types = importlib.import_module("app.domain.competitor_prices.types")
            asyncio.run(
                repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_platform_guard",
                        name="Ops watch platform guard",
                        pricing_browser_session_id="browser-ebk-pricing",
                    )
                )
            )
            client = _client(db_url)

            response = client.patch(
                "/api/v1/browser-sessions/browser-ebk-pricing",
                json={"platform": "meituan"},
            )

            self.assertEqual(response.status_code, 422)
            group = asyncio.run(
                repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch_platform_guard"
                )
            )
            assert group is not None
            self.assertEqual(group["pricing_browser_session_id"], "browser-ebk-pricing")

    def test_competitor_pricing_binding_rejects_non_ctrip_ebk(self) -> None:
        repository = importlib.import_module("app.domain.competitor_prices.repository")
        for platform in ("meituan", "fliggy"):
            with self.subTest(platform=platform):
                account = _browser_session(
                    browser_session_id=f"browser-{platform}-ebk",
                    platform_account_id=f"{platform}-ebk",
                    intended_use="ebk",
                    platform=platform,
                )

                with self.assertRaisesRegex(ValueError, "携程"):
                    repository._validate_pricing_browser_session(account)

    def test_pool_competitor_account_does_not_control_pricing_policy(self) -> None:
        async def scenario() -> dict[str, Any]:
            orchestration = _reload_orchestration()
            sent: list[dict[str, Any]] = []
            orchestration.get_competitor_watch_group_detail = AsyncMock(
                return_value={"group_key": "ops_watch", "feishu_chat_id": "oc_delivery"}
            )
            orchestration.build_pricing_reference_summary = AsyncMock(
                return_value={"confidence_label": "rule", "risk_flags": []}
            )
            orchestration.create_pricing_reference_preview_from_alert = AsyncMock(
                return_value=_reference_preview()
            )
            orchestration.FeishuBotClient = lambda *args, **kwargs: _FakeFeishuClient(sent)

            result = await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record=_alert_record(
                    pricing_browser_session_id="browser-ebk-2",
                    competitor_browser_session_id="browser-trip3",
                    connection_mode="non_direct",
                    can_auto_write_price=True,
                ),
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )
            return {"result": result, "sent": sent}

        payload = asyncio.run(scenario())

        self.assertEqual(payload["result"]["outcome"], "reference_card_sent")
        self.assertEqual(payload["sent"][0]["chat_id"], "oc_delivery")
        self.assertNotIn("browser-trip3", str(payload["result"].get("account_policy", "")))

    def test_price_alert_uses_watch_group_pricing_ebk_binding_not_pool_member(self) -> None:
        alert = _alert_record(
            pricing_browser_session_id="browser-ebk-2",
            competitor_browser_session_id="browser-trip3",
            connection_mode="non_direct",
            can_auto_write_price=True,
        )

        policy = alert["evidence"]["account_write_policy"]

        self.assertEqual(policy["pricing_browser_session_id"], "browser-ebk-2")
        self.assertNotEqual(policy["pricing_browser_session_id"], "browser-trip3")
        self.assertEqual(alert["evidence"]["competitor_read_account"]["browser_session_id"], "browser-trip3")

    def test_competitor_watch_feishu_chat_id_remains_delivery_group(self) -> None:
        async def scenario() -> list[dict[str, Any]]:
            orchestration = _reload_orchestration()
            sent: list[dict[str, Any]] = []
            orchestration.get_competitor_watch_group_detail = AsyncMock(
                return_value={
                    "group_key": "ops_watch",
                    "feishu_chat_id": "oc_competitor_delivery_group",
                    "pricing_browser_session_id": "browser-ebk-2",
                }
            )
            orchestration.build_pricing_reference_summary = AsyncMock(
                return_value={"confidence_label": "rule", "risk_flags": []}
            )
            orchestration.create_pricing_reference_preview_from_alert = AsyncMock(
                return_value=_reference_preview()
            )
            orchestration.FeishuBotClient = lambda *args, **kwargs: _FakeFeishuClient(sent)

            await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record=_alert_record(
                    pricing_browser_session_id="browser-ebk-2",
                    competitor_browser_session_id="browser-trip3",
                    connection_mode="non_direct",
                    can_auto_write_price=True,
                ),
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )
            return sent

        sent = asyncio.run(scenario())

        self.assertEqual(sent[0]["chat_id"], "oc_competitor_delivery_group")

    def test_manual_pricing_command_uses_ebk_pricing_feishu_chat_not_competitor_group(self) -> None:
        with _isolated_browser_state():
            sessions = _reload_browser_sessions()
            sessions.get_browser_session_store().restore(
                _browser_session(
                    browser_session_id="browser-trip-pool",
                    platform_account_id="trip-pool",
                    intended_use="competitor_price",
                )
            )
            sessions.get_browser_session_store().restore(
                _browser_session(
                    browser_session_id="browser-ebk-manual",
                    platform_account_id="ebk-manual",
                    intended_use="ebk",
                    pricing_feishu_chat_id="oc_pricing_group",
                )
            )

            resolved = sessions.get_pricing_browser_session_for_feishu_chat_id(
                "oc_pricing_group"
            )

            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.browser_session_id, "browser-ebk-manual")
            self.assertNotEqual(resolved.browser_session_id, "browser-trip-pool")

    def test_direct_or_unknown_pricing_ebk_still_blocks_execution_with_pool_enabled(self) -> None:
        async def scenario() -> dict[str, Any]:
            orchestration = _reload_orchestration()
            orchestration.create_pricing_reference_preview_from_alert = AsyncMock(
                side_effect=AssertionError("suggestion-only account must not create preview")
            )
            return await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record=_alert_record(
                    pricing_browser_session_id="browser-ebk-unknown",
                    competitor_browser_session_id="browser-trip3",
                    connection_mode="unknown",
                    can_auto_write_price=False,
                ),
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

        result = asyncio.run(scenario())

        self.assertEqual(result["outcome"], "skipped")
        self.assertEqual(result["reason"], "suggestion_only_account")

    def test_competitor_price_increase_remains_alert_only_with_pool_enabled(self) -> None:
        async def scenario() -> dict[str, Any]:
            orchestration = _reload_orchestration()
            orchestration.create_pricing_reference_preview_from_alert = AsyncMock(
                side_effect=AssertionError("price increase must remain alert-only")
            )
            return await orchestration.try_auto_preview_and_approval_for_alert(
                alert_record={
                    "status": "change_detected",
                    "watch_group_key": "ops_watch",
                    "changed_items": [{"direction": "up", "signed_delta_yuan": 50}],
                    "suggestions": [],
                    "evidence": {
                        "browser_selection": {
                            "pool_enabled": True,
                            "browser_session_id": "browser-trip3",
                        }
                    },
                },
                watch_group_key="ops_watch",
                actor_id="scheduler",
            )

        result = asyncio.run(scenario())

        self.assertEqual(result["outcome"], "skipped")
        self.assertEqual(result["reason"], "alert_not_suggestion_ready")


class _FakeFeishuClient:
    def __init__(self, sent: list[dict[str, Any]]) -> None:
        self.sent = sent

    async def send_interactive_card(
        self,
        chat_id: str,
        card: dict[str, Any],
        *,
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        self.sent.append({"chat_id": chat_id, "card": card, "fallback_text": fallback_text})
        self.assert_no_approve_action(card)
        return {"status": "success", "summary": "sent", "secret": "removed"}

    @staticmethod
    def assert_no_approve_action(card: dict[str, Any]) -> None:
        content = str(card).lower()
        if "decision': 'approve" in content or '"decision": "approve' in content:
            raise AssertionError("reference card must not contain approve action")


def _reload_orchestration():
    module = importlib.import_module(
        "app.domain.competitor_prices.price_change_pricing_orchestration"
    )
    return importlib.reload(module)


def _reference_preview() -> Any:
    payload = {
        "status": "reference_ready",
        "summary": "reference ready",
        "preview_id": 501,
        "id": 501,
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
                    "checkin_date": "2026-07-06",
                    "breakfast_kind": "none",
                    "current_price_yuan": 420,
                    "recommended_adjustment_yuan": -20,
                    "proposed_price_yuan": 400,
                    "trigger_reason": "competitor dropped",
                }
            ],
        },
    }
    return SimpleNamespace(
        status="reference_ready",
        summary="reference ready",
        preview_id=501,
        model_dump=lambda mode: payload,
    )


def _alert_record(
    *,
    pricing_browser_session_id: str,
    competitor_browser_session_id: str,
    connection_mode: str,
    can_auto_write_price: bool,
) -> dict[str, Any]:
    return {
        "status": "suggestion_ready",
        "watch_group_key": "ops_watch",
        "connection_mode": connection_mode,
        "can_auto_write_price": can_auto_write_price,
        "write_policy_message": "policy",
        "suggestions": [{"recommended_price_yuan": 400}],
        "evidence": {
            "browser_selection": {
                "source": "watch_group_pool",
                "pool_enabled": True,
                "browser_session_id": competitor_browser_session_id,
            },
            "competitor_read_account": {
                "purpose": "competitor_price",
                "browser_session_id": competitor_browser_session_id,
            },
            "account_write_policy": {
                "connection_mode": connection_mode,
                "can_auto_write_price": can_auto_write_price,
                "pricing_browser_session_id": pricing_browser_session_id,
            },
        },
    }


def _browser_session(
    *,
    browser_session_id: str,
    platform_account_id: str,
    intended_use: str,
    pricing_feishu_chat_id: str | None = None,
    platform: str = "ctrip",
) -> BrowserSession:
    return BrowserSession.model_validate(
        {
            "browser_session_id": browser_session_id,
            "platform_account_id": platform_account_id,
            "display_name": platform_account_id,
            "platform": platform,
            "session_port": 9221,
            "intended_use": intended_use,
            "pricing_feishu_chat_id": pricing_feishu_chat_id,
            "login_state": "logged_in",
            "human_verification_status": "none",
            "last_heartbeat_at": "2026-07-06T00:00:00+00:00",
            "takeover_url": f"/browser-control?session={browser_session_id}",
            "manual_takeover_url": f"/browser-control?session={browser_session_id}&mode=manual",
            "can_takeover": True,
            "can_logout": True,
            "last_action": "Test session.",
        }
    )


class _isolated_browser_state:
    def __enter__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_browser_state_dir = os.environ.get("BROWSER_STATE_DIR")
        os.environ["BROWSER_STATE_DIR"] = str(Path(self._tmp.name) / "browser-state")
        _reload_browser_sessions()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_browser_state_dir is None:
            os.environ.pop("BROWSER_STATE_DIR", None)
        else:
            os.environ["BROWSER_STATE_DIR"] = self._previous_browser_state_dir
        _reload_browser_sessions()
        self._tmp.cleanup()


def _reload_browser_sessions():
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    sessions = importlib.import_module("app.domain.browser.sessions")
    return importlib.reload(sessions)


def _client(db_url: str) -> TestClient:
    _reload_db_session(db_url)
    app = importlib.import_module("app.main")
    return TestClient(importlib.reload(app).create_app())


if __name__ == "__main__":
    unittest.main()
