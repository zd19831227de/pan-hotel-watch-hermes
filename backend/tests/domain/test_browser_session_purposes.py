from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.domain.browser.sessions import (
    BrowserSession,
    BrowserSessionCreate,
    BrowserSessionPatch,
    write_policy_message_for_account,
    can_auto_write_price,
    get_pricing_browser_session_for_feishu_chat_id,
)
from app.domain.competitor_prices.repository import (
    _validate_browser_session as validate_competitor_browser_session,
)
from app.domain.ctrip_ebooking_watch.repository import (
    _validate_ebk_browser_session,
)


class BrowserSessionPurposeTests(unittest.TestCase):
    def test_create_allows_only_operator_browser_purposes(self) -> None:
        for intended_use in ("ebk", "competitor_price"):
            request = BrowserSessionCreate(
                platform_account_id=f"ctrip_{intended_use}",
                display_name=f"Ctrip {intended_use}",
                platform="ctrip",
                intended_use=intended_use,
                session_port=9300,
            )
            self.assertEqual(request.intended_use, intended_use)

        for legacy_or_invalid in ("ebk_data_center", "competitor", "public_competitor", "ai"):
            with self.subTest(intended_use=legacy_or_invalid):
                with self.assertRaises(ValidationError):
                    BrowserSessionCreate(
                        platform_account_id=f"ctrip_{legacy_or_invalid}",
                        display_name="Invalid",
                        platform="ctrip",
                        intended_use=legacy_or_invalid,
                        session_port=9301,
                    )

    def test_patch_allows_only_operator_browser_purposes(self) -> None:
        self.assertEqual(BrowserSessionPatch(intended_use="ebk").intended_use, "ebk")
        self.assertEqual(
            BrowserSessionPatch(intended_use="competitor_price").intended_use,
            "competitor_price",
        )

        for legacy_or_invalid in ("ebk_data_center", "competitor", "public_competitor", "demo"):
            with self.subTest(intended_use=legacy_or_invalid):
                with self.assertRaises(ValidationError):
                    BrowserSessionPatch(intended_use=legacy_or_invalid)

    def test_loaded_legacy_purposes_are_normalized(self) -> None:
        cases = {
            "ebk_data_center": "ebk",
            "ebk": "ebk",
            "public_competitor": "competitor_price",
            "competitor": "competitor_price",
            "competitor_price": "competitor_price",
        }

        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                session = BrowserSession.model_validate(
                    {
                        "browser_session_id": f"browser_{raw}",
                        "platform_account_id": f"ctrip_{raw}",
                        "display_name": raw,
                        "platform": "ctrip",
                        "session_port": 9302,
                        "intended_use": raw,
                        "login_state": "logged_out",
                        "human_verification_status": "required",
                        "last_heartbeat_at": "2026-06-17T00:00:00+00:00",
                        "takeover_url": "/browser-control",
                        "can_takeover": True,
                        "can_logout": False,
                        "last_action": "Loaded from legacy state.",
                    }
                )
                self.assertEqual(session.intended_use, expected)

    def test_competitor_binding_refreshes_browser_sessions_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            previous_browser_state_dir = os.environ.get("BROWSER_STATE_DIR")
            os.environ["BROWSER_STATE_DIR"] = tmp_dir
            try:
                settings = importlib.import_module("app.core.settings")
                settings.get_settings.cache_clear()
                sessions = importlib.import_module("app.domain.browser.sessions")
                importlib.reload(sessions)
                browser_binding = importlib.import_module(
                    "app.domain.competitor_prices.browser_binding"
                )
                browser_binding = importlib.reload(browser_binding)

                state_path = Path(tmp_dir) / "browser_sessions.json"
                state_path.write_text(
                    json.dumps(
                        {
                            "sessions": {
                                "browser-trip3": {
                                    "browser_session_id": "browser-trip3",
                                    "platform_account_id": "trip3",
                                    "display_name": "Ctrip account 3",
                                    "platform": "ctrip",
                                    "session_port": 9223,
                                    "intended_use": "competitor_price",
                                    "login_state": "logged_in",
                                    "human_verification_status": "none",
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )

                session = browser_binding.validate_competitor_browser_session_config(
                    "browser-trip3"
                )

                self.assertEqual(session.platform_account_id, "trip3")
                self.assertEqual(session.intended_use, "competitor_price")
            finally:
                if previous_browser_state_dir is None:
                    os.environ.pop("BROWSER_STATE_DIR", None)
                else:
                    os.environ["BROWSER_STATE_DIR"] = previous_browser_state_dir
                settings = importlib.import_module("app.core.settings")
                settings.get_settings.cache_clear()
                importlib.reload(importlib.import_module("app.domain.browser.sessions"))

    def test_connection_mode_controls_auto_write_capability(self) -> None:
        self.assertFalse(can_auto_write_price(_browser_session(connection_mode="direct")))
        self.assertFalse(can_auto_write_price(_browser_session(connection_mode="unknown")))
        self.assertTrue(can_auto_write_price(_browser_session(connection_mode="non_direct")))

    def test_non_direct_write_policy_message_uses_approval_flow_wording(self) -> None:
        self.assertEqual(
            write_policy_message_for_account(_browser_session(connection_mode="non_direct")),
            "非直连账号可在 Feishu 审批通过后继续调价流程。",
        )

    def test_meituan_non_direct_writable_after_b8_whitelist(self) -> None:
        session = BrowserSession.model_validate(
            {
                "browser_session_id": "browser-meituan",
                "platform_account_id": "meituan-ebk",
                "display_name": "Meituan EBK",
                "platform": "meituan",
                "session_port": 9501,
                "intended_use": "ebk",
                "connection_mode": "non_direct",
                "login_state": "logged_in",
                "human_verification_status": "none",
                "last_heartbeat_at": "2026-07-09T00:00:00+00:00",
                "takeover_url": "/browser-control",
                "can_takeover": True,
                "can_logout": True,
                "last_action": "Test session.",
            }
        )

        # B8 白名单放开后：美团 non_direct 允许进入写价链（执行仍需
        # dry-run 前置与 Feishu 审批）。
        self.assertTrue(can_auto_write_price(session))
        self.assertTrue(session.can_auto_write_price)

    def test_loaded_legacy_account_defaults_to_unknown_connection_mode(self) -> None:
        session = BrowserSession.model_validate(
            {
                "browser_session_id": "browser-legacy",
                "platform_account_id": "ctrip-legacy",
                "display_name": "legacy",
                "platform": "ctrip",
                "session_port": 9302,
                "intended_use": "ebk",
                "login_state": "logged_in",
                "human_verification_status": "none",
                "last_heartbeat_at": "2026-06-17T00:00:00+00:00",
                "takeover_url": "/browser-control",
                "can_takeover": True,
                "can_logout": True,
                "last_action": "Loaded from legacy state.",
            }
        )

        self.assertEqual(session.connection_mode, "unknown")
        self.assertFalse(session.can_auto_write_price)

    def test_create_and_patch_accept_connection_mode_without_defaulting_to_non_direct(self) -> None:
        request = BrowserSessionCreate(
            platform_account_id="ctrip_new",
            display_name="Ctrip New",
            platform="ctrip",
            intended_use="ebk",
            session_port=9300,
        )
        self.assertEqual(request.connection_mode, "unknown")

        self.assertEqual(
            BrowserSessionCreate(
                platform_account_id="ctrip_direct",
                display_name="Ctrip Direct",
                platform="ctrip",
                intended_use="ebk",
                session_port=9301,
                connection_mode="direct",
            ).connection_mode,
            "direct",
        )
        self.assertEqual(BrowserSessionPatch(connection_mode="non_direct").connection_mode, "non_direct")

    def test_data_center_watch_binds_only_ebk_browser_sessions(self) -> None:
        session = _browser_session(
            browser_session_id="browser-public",
            platform_account_id="ctrip-public",
            intended_use="competitor_price",
        )

        with self.assertRaisesRegex(ValueError, "EBK"):
            _validate_ebk_browser_session(session)

        ebk_session = _browser_session(
            browser_session_id="browser-ebk",
            platform_account_id="ctrip-ebk",
            intended_use="ebk",
        )
        self.assertIs(_validate_ebk_browser_session(ebk_session), ebk_session)

    def test_competitor_watch_binds_only_competitor_price_browser_sessions(self) -> None:
        ebk_session = _browser_session(
            browser_session_id="browser-ebk",
            platform_account_id="ctrip-ebk",
            intended_use="ebk",
        )

        with self.assertRaisesRegex(ValueError, "竞品价格"):
            validate_competitor_browser_session(ebk_session)

        competitor_session = _browser_session(
            browser_session_id="browser-public",
            platform_account_id="ctrip-public",
            intended_use="competitor_price",
        )
        self.assertIs(validate_competitor_browser_session(competitor_session), competitor_session)

    def test_browser_session_api_returns_connection_mode_and_write_capability(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)

            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_api",
                    "display_name": "API 账号",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "connection_mode": "direct",
                    "shop_key": state.shop_key,
                    "session_port": 9321,
                },
            )
            self.assertEqual(created.status_code, 200)
            payload = created.json()
            self.assertEqual(payload["connection_mode"], "direct")
            self.assertFalse(payload["can_auto_write_price"])

            detail = client.get(f"/api/v1/browser-sessions/{payload['browser_session_id']}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["connection_mode"], "direct")
            self.assertFalse(detail.json()["can_auto_write_price"])

            updated = client.patch(
                f"/api/v1/browser-sessions/{payload['browser_session_id']}",
                json={"connection_mode": "non_direct"},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["connection_mode"], "non_direct")
            self.assertTrue(updated.json()["can_auto_write_price"])

            listed = client.get("/api/v1/browser-sessions")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["sessions"][0]["connection_mode"], "non_direct")

    def test_ebk_browser_session_binds_pricing_feishu_chat_without_exposing_raw_id(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)

            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_pricing",
                    "display_name": "Pricing EBK",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "connection_mode": "non_direct",
                    "pricing_feishu_chat_id": "oc_pricing_room_a",
                    "shop_key": state.shop_key,
                    "session_port": 9331,
                },
            )
            self.assertEqual(created.status_code, 200)
            payload = created.json()
            self.assertNotIn("pricing_feishu_chat_id", payload)
            self.assertTrue(payload["pricing_feishu_chat_id_configured"])
            self.assertIn("***", payload["pricing_feishu_chat_id_masked"])

            sessions = importlib.import_module("app.domain.browser.sessions")
            resolved = sessions.get_pricing_browser_session_for_feishu_chat_id(
                "oc_pricing_room_a"
            )
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.browser_session_id, payload["browser_session_id"])

            updated = client.patch(
                f"/api/v1/browser-sessions/{payload['browser_session_id']}",
                json={"pricing_feishu_chat_id": ""},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertFalse(updated.json()["pricing_feishu_chat_id_configured"])
            self.assertIsNone(
                sessions.get_pricing_browser_session_for_feishu_chat_id("oc_pricing_room_a")
            )

    def test_pricing_feishu_chat_binding_is_ebk_only_and_unique(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)

            public = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_public",
                    "display_name": "Public",
                    "platform": "ctrip",
                    "intended_use": "competitor_price",
                    "pricing_feishu_chat_id": "oc_pricing_room_a",
                    "session_port": 9332,
                },
            )
            self.assertEqual(public.status_code, 422)

            first = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_ebk_a",
                    "display_name": "EBK A",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "pricing_feishu_chat_id": "oc_pricing_room_a",
                    "shop_key": state.shop_key,
                    "session_port": 9333,
                },
            )
            self.assertEqual(first.status_code, 200)

            second = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_ebk_b",
                    "display_name": "EBK B",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "pricing_feishu_chat_id": "oc_pricing_room_a",
                    "shop_key": state.shop_key,
                    "session_port": 9334,
                },
            )
            self.assertEqual(second.status_code, 422)

    def test_non_ctrip_ebk_can_bind_pricing_feishu_chat_under_v9(self) -> None:
        """V9 Step 4 — 美团/飞猪 EBK 可绑定调价飞书群；写价仍被平台白名单拦截。"""
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)

            for index, platform in enumerate(("meituan", "fliggy"), start=1):
                with self.subTest(platform=platform):
                    response = client.post(
                        "/api/v1/browser-sessions",
                        json={
                            "platform_account_id": f"{platform}_pricing",
                            "display_name": f"{platform} pricing",
                            "platform": platform,
                            "intended_use": "ebk",
                            "pricing_feishu_chat_id": f"oc_{platform}_pricing",
                            "shop_key": state.shop_key,
                            "session_port": 9340 + index,
                        },
                    )

                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(response.json()["can_auto_write_price"])

    def test_pricing_feishu_chat_binding_blocks_non_ctrip_platform_change(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)

            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_pricing_platform_guard",
                    "display_name": "Pricing platform guard",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "pricing_feishu_chat_id": "oc_pricing_platform_guard",
                    "shop_key": state.shop_key,
                    "session_port": 9343,
                },
            )
            self.assertEqual(created.status_code, 200)

            response = client.patch(
                f"/api/v1/browser-sessions/{created.json()['browser_session_id']}",
                json={"platform": "meituan"},
            )

            self.assertEqual(response.status_code, 422)
            detail = client.get(
                f"/api/v1/browser-sessions/{created.json()['browser_session_id']}"
            )
            self.assertEqual(detail.json()["platform"], "ctrip")
            self.assertTrue(detail.json()["pricing_feishu_chat_id_configured"])

    def test_non_ctrip_legacy_binding_does_not_reserve_pricing_chat(self) -> None:
        with _isolated_browser_state() as state:
            sessions = importlib.import_module("app.domain.browser.sessions")
            sessions.get_browser_session_store().restore(
                BrowserSession.model_validate(
                    {
                        "browser_session_id": "browser-meituan-legacy-pricing",
                        "platform_account_id": "meituan-legacy-pricing",
                        "display_name": "Legacy Meituan pricing",
                        "platform": "meituan",
                        "session_port": 9344,
                        "intended_use": "ebk",
                        "pricing_feishu_chat_id": "oc_shared_pricing_chat",
                        "login_state": "logged_in",
                        "human_verification_status": "none",
                        "last_heartbeat_at": "2026-08-05T00:00:00+00:00",
                        "takeover_url": "/browser-control",
                        "can_takeover": True,
                        "can_logout": True,
                        "last_action": "Loaded legacy invalid binding.",
                    }
                )
            )
            client = TestClient(_reload_app())

            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_valid_pricing",
                    "display_name": "Valid Ctrip pricing",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "pricing_feishu_chat_id": "oc_shared_pricing_chat",
                    "shop_key": state.shop_key,
                    "session_port": 9345,
                },
            )

            self.assertEqual(created.status_code, 200)


def _browser_session(
    *,
    browser_session_id: str = "browser-test",
    platform_account_id: str = "ctrip-test",
    intended_use: str = "ebk",
    connection_mode: str = "unknown",
) -> BrowserSession:
    return BrowserSession.model_validate(
        {
            "browser_session_id": browser_session_id,
            "platform_account_id": platform_account_id,
            "display_name": platform_account_id,
            "platform": "ctrip",
            "session_port": 9302,
            "intended_use": intended_use,
            "connection_mode": connection_mode,
            "login_state": "logged_in",
            "human_verification_status": "none",
            "last_heartbeat_at": "2026-06-17T00:00:00+00:00",
            "takeover_url": "/browser-control",
            "can_takeover": True,
            "can_logout": True,
            "last_action": "Test session.",
        }
    )


class _isolated_browser_state:
    def __enter__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_browser_state_dir = os.environ.get("BROWSER_STATE_DIR")
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["BROWSER_STATE_DIR"] = str(Path(self._tmp.name) / "browser-state")
        # P2 起 EBK 建号要求店铺存在：这里给隔离 shops 库并预建一家测试店铺。
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'shops.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        db_session = importlib.import_module("app.db.session")
        importlib.reload(db_session)
        asyncio.run(db_session.ensure_db_ready())
        from app.domain.shops import service as shops_service

        self.shop_key = asyncio.run(
            shops_service.create_shop({"name": "测试店铺"})
        )["shop_key"]
        sessions = importlib.import_module("app.domain.browser.sessions")
        importlib.reload(sessions)
        browser_sessions_api = importlib.import_module("app.api.v1.browser_sessions")
        importlib.reload(browser_sessions_api)
        return self

    def __exit__(self, exc_type: str, exc: str, tb: str) -> None:
        if self._previous_browser_state_dir is None:
            os.environ.pop("BROWSER_STATE_DIR", None)
        else:
            os.environ["BROWSER_STATE_DIR"] = self._previous_browser_state_dir
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        db_session = importlib.import_module("app.db.session")
        importlib.reload(db_session)
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        sessions = importlib.import_module("app.domain.browser.sessions")
        importlib.reload(sessions)
        self._tmp.cleanup()


def _reload_app():
    main = importlib.import_module("app.main")
    return importlib.reload(main).create_app()


if __name__ == "__main__":
    unittest.main()
