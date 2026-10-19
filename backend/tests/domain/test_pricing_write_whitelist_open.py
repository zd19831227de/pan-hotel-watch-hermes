from __future__ import annotations

import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest import mock


def _account(platform: str, mode: str) -> SimpleNamespace:
    return SimpleNamespace(platform=platform, connection_mode=mode)


class WriteCapablePlatformsTests(unittest.TestCase):
    """B8 — 美团/飞猪写价白名单开放。"""

    def _sessions(self):
        return importlib.import_module("app.domain.browser.sessions")

    def test_whitelist_now_covers_three_platforms(self) -> None:
        sessions = self._sessions()
        self.assertEqual(
            sessions.WRITE_CAPABLE_PLATFORMS, ("ctrip", "meituan", "fliggy")
        )

    def test_non_direct_accounts_can_auto_write_on_all_three(self) -> None:
        sessions = self._sessions()
        for platform in ("ctrip", "meituan", "fliggy"):
            self.assertTrue(sessions.can_auto_write_price(_account(platform, "non_direct")))

    def test_direct_and_unknown_still_denied_on_all_three(self) -> None:
        sessions = self._sessions()
        for platform in ("ctrip", "meituan", "fliggy"):
            self.assertFalse(sessions.can_auto_write_price(_account(platform, "direct")))
            self.assertFalse(sessions.can_auto_write_price(_account(platform, "unknown")))

    def test_unknown_platform_still_denied(self) -> None:
        sessions = self._sessions()
        self.assertFalse(sessions.can_auto_write_price(_account("ota9", "non_direct")))

    def test_account_write_policy_reflects_whitelist(self) -> None:
        sessions = self._sessions()
        policy = sessions.account_write_policy(_account("meituan", "non_direct"))
        self.assertTrue(policy["can_auto_write_price"])
        policy2 = sessions.account_write_policy(_account("fliggy", "direct"))
        self.assertFalse(policy2["can_auto_write_price"])


class ExecuteDispatchAfterWhitelistTests(unittest.TestCase):
    """B8 — 白名单放开后，non_direct 美团/飞猪账号可走 dry-run → execute。"""

    def _service(self):
        return importlib.import_module("app.domain.pricing_execution.service")

    def _sessions(self):
        return importlib.import_module("app.domain.browser.sessions")

    def test_meituan_non_direct_dispatches_to_browser_service(self) -> None:
        service = self._service()
        sessions = self._sessions()
        source = SimpleNamespace(
            approval_record_id=2,
            preview_id=1,
            execute_request_json={"request_id": "r1", "actions": []},
            status="preflight_ready",
            event_type="preflight",
            platform_account_id="mt_acc",
            browser_session_id="mt_session",
        )
        policy = sessions.account_write_policy(_account("meituan", "non_direct"))
        browser_result = {
            "status": "page_write_preview_ready",
            "summary": "Meituan EBK price-write dry-run preview is ready.",
            "platform_account_id": "mt_acc",
            "browser_session_id": "mt_session",
            "data": {"query_type": "meituan_ebooking_pricing_page_write_dry_run"},
            "evidence": {"browser_write_invoked": False},
        }
        calls: list[str] = []

        class _FakeMeituanService:
            async def prepare_pricing_page_write_dry_run(self, **kwargs):
                calls.append("meituan-dry-run")
                return browser_result

        meituan_module = importlib.import_module("app.domain.browser.meituan_ebooking")
        store = mock.Mock()
        store.get.return_value = SimpleNamespace(platform="meituan", browser_session_id="mt_session")

        with (
            mock.patch.object(
                service,
                "_load_execution_with_policy",
                new=mock.AsyncMock(return_value=(source, policy)),
            ),
            mock.patch.object(
                service,
                "_save_execution_result",
                new=mock.AsyncMock(side_effect=lambda **kwargs: kwargs["result"]),
            ),
            mock.patch.object(
                service,
                "get_browser_session_id_for_platform_account",
                return_value="mt_session",
            ),
            mock.patch.object(sessions, "get_browser_session_store", return_value=store),
            mock.patch.object(
                meituan_module,
                "get_meituan_ebooking_service",
                return_value=_FakeMeituanService(),
            ),
        ):
            result = asyncio.run(
                service.prepare_pricing_page_write_dry_run(
                    execution_record_id=21,
                    account_policy=policy,
                    platform_account_id="mt_acc",
                    browser_session_id="mt_session",
                )
            )
        self.assertEqual(calls, ["meituan-dry-run"])
        self.assertEqual(result.status, "page_write_preview_ready")

    def test_execute_without_ready_dry_run_is_not_preflight_ready(self) -> None:
        service = self._service()
        source = SimpleNamespace(
            approval_record_id=2,
            preview_id=1,
            execute_request_json={"request_id": "r1", "actions": []},
            status="failed",
            event_type="preflight",
            platform_account_id="mt_acc",
            browser_session_id="mt_session",
        )
        sessions = self._sessions()
        policy = sessions.account_write_policy(_account("meituan", "non_direct"))
        with (
            mock.patch.object(
                service,
                "_load_execution_with_policy",
                new=mock.AsyncMock(return_value=(source, policy)),
            ),
            mock.patch.object(
                service,
                "_save_execution_result",
                new=mock.AsyncMock(side_effect=lambda **kwargs: kwargs["result"]),
            ),
        ):
            result = asyncio.run(
                service.prepare_pricing_page_write_dry_run(
                    execution_record_id=22,
                    account_policy=policy,
                )
            )
        self.assertEqual(result.status, "not_preflight_ready")

    def test_direct_meituan_account_denied_with_platform_copy(self) -> None:
        service = self._service()
        sessions = self._sessions()
        store = mock.Mock()
        store.get.return_value = SimpleNamespace(platform="meituan")
        with mock.patch.object(
            sessions, "get_browser_session_store", return_value=store
        ):
            result = asyncio.run(
                service.execute_pricing_page_write(
                    execution_record_id=23,
                    account_policy={
                        "connection_mode": "direct",
                        "can_auto_write_price": False,
                    },
                    browser_session_id="mt_session",
                )
            )
        self.assertEqual(result.status, "write_not_allowed")
        self.assertIn("美团", result.summary)
        self.assertNotIn("携程", result.summary)


if __name__ == "__main__":
    unittest.main()
