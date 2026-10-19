from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.async_api import Error as PlaywrightError

from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService
from app.domain.browser.ctrip_ebooking_session_helpers import (
    _is_stale_playwright_page_error,
)


class StalePlaywrightDriverMatcherTests(unittest.TestCase):
    def test_driver_pipe_death_plain_exception_is_stale(self) -> None:
        exc = Exception(
            "BrowserType.connect_over_cdp: Connection closed while reading from the driver"
        )

        self.assertTrue(_is_stale_playwright_page_error(exc))

    def test_socket_send_failure_plain_exception_is_stale(self) -> None:
        exc = Exception("BrowserType.connect_over_cdp: socket.send() raised exception.")

        self.assertTrue(_is_stale_playwright_page_error(exc))

    def test_playwright_error_closed_target_is_stale(self) -> None:
        self.assertTrue(
            _is_stale_playwright_page_error(
                PlaywrightError("Target page, context or browser has been closed")
            )
        )

    def test_unrelated_errors_are_not_stale(self) -> None:
        self.assertFalse(_is_stale_playwright_page_error(Exception("boom")))
        self.assertFalse(
            _is_stale_playwright_page_error(PlaywrightError("Timeout 2000ms exceeded"))
        )


class StalePlaywrightDriverRecoveryTests(unittest.TestCase):
    def test_get_or_create_page_resets_driver_and_retries_on_driver_pipe_death(self) -> None:
        """登录窗口打开时 driver 管道已死：第一次 connect 抛裸 Exception，
        服务应重置 Playwright 实例并在第二次尝试中恢复，而不是把 500 抛给前端。"""
        from app.domain.browser import ctrip_ebooking as ctrip_ebooking_module

        service = CtripEbookingBrowserService()
        fake_page = SimpleNamespace()
        attempts: list[str] = []

        async def fake_connect(_self, *, playwright, platform_account_id, browser_session_id, port, timeout_ms=2000):
            attempts.append(f"connect:{port}")
            if len(attempts) == 1:
                raise Exception(
                    "BrowserType.connect_over_cdp: Connection closed while reading from the driver"
                )
            return fake_page

        reset_calls: list[int] = []

        async def fake_reset(_self) -> None:
            reset_calls.append(1)
            service._pages.clear()
            service._contexts.clear()
            service._cdp_browsers.clear()
            service._playwright = None

        async def fake_get_playwright(_self):
            return SimpleNamespace()

        fake_session = SimpleNamespace(session_port=9221)
        fake_store = SimpleNamespace(get=lambda _session_id: fake_session)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                CtripEbookingBrowserService,
                "_connect_existing_debug_page",
                fake_connect,
            ),
            patch.object(
                CtripEbookingBrowserService,
                "_reset_playwright_driver",
                fake_reset,
            ),
            patch.object(
                CtripEbookingBrowserService,
                "_get_playwright",
                fake_get_playwright,
            ),
            patch.object(
                CtripEbookingBrowserService,
                "_profile_dir",
                lambda _self, _account_id: Path(temp_dir) / "ctrip-profile",
            ),
            patch.object(
                ctrip_ebooking_module,
                "get_browser_session_store",
                return_value=fake_store,
            ),
        ):
            page = asyncio.run(
                service._get_or_create_page_unlocked(
                    platform_account_id="acct",
                    browser_session_id="sess",
                    headless=False,
                )
            )

        self.assertIs(page, fake_page)
        self.assertEqual(len(attempts), 2, "应在重置 driver 后重试一次")
        self.assertEqual(len(reset_calls), 1, "应触发一次 driver 重置")


if __name__ == "__main__":
    unittest.main()
