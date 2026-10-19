from __future__ import annotations

import importlib
import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi.testclient import TestClient


@dataclass
class _FakeAccount:
    platform: str
    connection_mode: str


class WritePlatformGateTests(unittest.TestCase):
    """V9 Step 0 — can_auto_write_price 白名单化；B8（2026-08-24）起含三平台。"""

    def _sessions(self):
        return importlib.import_module("app.domain.browser.sessions")

    def test_write_capable_platforms_whitelist_covers_three_platforms(self) -> None:
        """B8（2026-08-24）后白名单含三平台；其它平台仍被拒。"""
        sessions = self._sessions()
        self.assertTrue(hasattr(sessions, "WRITE_CAPABLE_PLATFORMS"))
        self.assertEqual(
            tuple(sessions.WRITE_CAPABLE_PLATFORMS), ("ctrip", "meituan", "fliggy")
        )

    def test_ctrip_non_direct_can_write(self) -> None:
        sessions = self._sessions()
        self.assertTrue(
            sessions.can_auto_write_price(
                _FakeAccount(platform="ctrip", connection_mode="non_direct")
            )
        )

    def test_meituan_fliggy_non_direct_allowed_after_b8(self) -> None:
        """B8 白名单放开：美团/飞猪 non_direct 可写（execute 仍需 dry-run 前置）。"""
        sessions = self._sessions()
        for platform in ("meituan", "fliggy"):
            self.assertTrue(
                sessions.can_auto_write_price(
                    _FakeAccount(platform=platform, connection_mode="non_direct")
                ),
                f"{platform} non_direct must be writable after the B8 whitelist opening",
            )

    def test_direct_and_unknown_blocked_on_all_platforms(self) -> None:
        sessions = self._sessions()
        for platform in ("ctrip", "meituan", "fliggy"):
            for mode in ("direct", "unknown"):
                self.assertFalse(
                    sessions.can_auto_write_price(
                        _FakeAccount(platform=platform, connection_mode=mode)
                    )
                )

    def test_missing_platform_defaults_to_ctrip_semantics(self) -> None:
        sessions = self._sessions()
        # 与旧行为一致：缺 platform 属性按 ctrip 处理
        account = SimpleNamespace(connection_mode="non_direct")
        self.assertTrue(sessions.can_auto_write_price(account))
        account_blocked = SimpleNamespace(connection_mode="direct")
        self.assertFalse(sessions.can_auto_write_price(account_blocked))


class ConnectionModeStorageTests(unittest.TestCase):
    """P2 契约：新建 EBK 账号必须归属店铺（shop_key）。"""

    def test_patch_meituan_session_accepts_non_direct_storage(self) -> None:
        from tests._db_isolation import create_isolated_test_shop, isolated_test_db

        with isolated_test_db():
            shop_key = create_isolated_test_shop("美团闸门店")
            main = importlib.import_module("app.main")
            client = TestClient(main.create_app())
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "mt_gate",
                    "display_name": "MT Gate",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "connection_mode": "unknown",
                    "session_port": 9561,
                    "shop_key": shop_key,
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            patched = client.patch(
                f"/api/v1/browser-sessions/{created.json()['browser_session_id']}",
                json={"connection_mode": "non_direct"},
            )
            self.assertEqual(patched.status_code, 200, patched.text)
            body = patched.json()
            self.assertEqual(body["connection_mode"], "non_direct")
            self.assertTrue(body["can_auto_write_price"])

    def test_patch_fliggy_session_accepts_non_direct_storage(self) -> None:
        from tests._db_isolation import create_isolated_test_shop, isolated_test_db

        with isolated_test_db():
            shop_key = create_isolated_test_shop("飞猪闸门店")
            main = importlib.import_module("app.main")
            client = TestClient(main.create_app())
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "fl_gate",
                    "display_name": "FL Gate",
                    "platform": "fliggy",
                    "intended_use": "ebk",
                    "connection_mode": "unknown",
                    "session_port": 9562,
                    "shop_key": shop_key,
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            patched = client.patch(
                f"/api/v1/browser-sessions/{created.json()['browser_session_id']}",
                json={"connection_mode": "non_direct"},
            )
            self.assertEqual(patched.status_code, 200, patched.text)
            self.assertTrue(patched.json()["can_auto_write_price"])


class PricingChatBindingPlatformTests(unittest.TestCase):
    """V9 Step 4 — 手动调价账号解析支持美团/飞猪 EBK 绑定（闸门照常拦截写价）。"""

    def test_resolver_accepts_meituan_and_fliggy_bindings(self) -> None:
        from tests._db_isolation import create_isolated_test_shop, isolated_test_db

        with isolated_test_db():
            shop_key = create_isolated_test_shop("解析店铺")
            sessions = importlib.import_module("app.domain.browser.sessions")
            store = sessions.get_browser_session_store()
            for platform, port in (("meituan", 9571), ("fliggy", 9572)):
                created = store.create(
                    sessions.BrowserSessionCreate(
                        platform_account_id=f"{platform}_pricing",
                        display_name=f"{platform} pricing",
                        platform=platform,
                        intended_use="ebk",
                        session_port=port,
                        shop_key=shop_key,
                        pricing_feishu_chat_id=f"oc_{platform}_room",
                    )
                )
                resolved = sessions.get_pricing_browser_session_for_feishu_chat_id(
                    f"oc_{platform}_room"
                )
                self.assertIsNotNone(resolved)
                assert resolved is not None
                self.assertEqual(resolved.platform, platform)

    def test_resolver_rejects_empty_chat_id(self) -> None:
        sessions = self._sessions_module()
        self.assertIsNone(sessions.get_pricing_browser_session_for_feishu_chat_id(None))
        self.assertIsNone(sessions.get_pricing_browser_session_for_feishu_chat_id("  "))

    @staticmethod
    def _sessions_module():
        return importlib.import_module("app.domain.browser.sessions")


if __name__ == "__main__":
    unittest.main()
