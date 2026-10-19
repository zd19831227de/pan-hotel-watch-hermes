from __future__ import annotations

import asyncio
import unittest
from typing import Any

from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService
from app.domain.browser.ctrip_public_price_reader import (
    CtripPublicCompetitorPriceReader,
    _public_competitor_cache_key,
)
from app.domain.browser.sessions import BrowserSession, get_browser_session_store

_BROWSER_SESSION = "browser-hygiene-test-a"
_PLATFORM_ACCOUNT = "ctrip-hygiene-test-a"


class _FakeHygienePage:
    def __init__(self, url: str, *, evaluate_delay: float = 0.0) -> None:
        self.url = url
        self.closed = False
        self.bring_to_front_count = 0
        self.evaluate_count = 0
        self._evaluate_delay = evaluate_delay

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    async def bring_to_front(self) -> None:
        self.bring_to_front_count += 1

    async def evaluate(self, expression: str) -> int:
        self.evaluate_count += 1
        if self._evaluate_delay:
            await asyncio.sleep(self._evaluate_delay)
        return 1


class _FakeHygieneContext:
    def __init__(self, pages: list[_FakeHygienePage]) -> None:
        self.pages = list(pages)

    async def new_page(self) -> _FakeHygienePage:
        page = _FakeHygienePage("about:blank")
        self.pages.append(page)
        return page


class _FakeHygieneBrowser:
    def __init__(self, context: _FakeHygieneContext) -> None:
        self.contexts = [context]

    async def close(self) -> None:
        pass


class _FakeHygieneChromium:
    def __init__(self, browser: _FakeHygieneBrowser) -> None:
        self._browser = browser

    async def connect_over_cdp(self, url: str, timeout: int = 0) -> _FakeHygieneBrowser:
        return self._browser


class _FakeHygienePlaywright:
    def __init__(self, browser: _FakeHygieneBrowser) -> None:
        self.chromium = _FakeHygieneChromium(browser)


def _install_fake_playwright(
    reader: CtripPublicCompetitorPriceReader, browser: _FakeHygieneBrowser
) -> None:
    async def fake_get_playwright() -> _FakeHygienePlaywright:
        return _FakeHygienePlaywright(browser)

    reader._get_playwright = fake_get_playwright  # type: ignore[method-assign]


def _make_reader(browser: _FakeHygieneBrowser) -> CtripPublicCompetitorPriceReader:
    service = CtripEbookingBrowserService()
    reader = service._public_price_reader
    _install_fake_playwright(reader, browser)
    return reader


def _restore_hygiene_session() -> None:
    get_browser_session_store().restore(
        BrowserSession.model_validate(
            {
                "browser_session_id": _BROWSER_SESSION,
                "platform_account_id": _PLATFORM_ACCOUNT,
                "display_name": _PLATFORM_ACCOUNT,
                "platform": "ctrip",
                "session_port": 9321,
                "intended_use": "competitor_price",
                "login_state": "logged_in",
                "human_verification_status": "none",
                "last_heartbeat_at": "2026-09-22T00:00:00+00:00",
                "takeover_url": f"/browser-control?session={_BROWSER_SESSION}",
                "manual_takeover_url": (
                    f"/browser-control?session={_BROWSER_SESSION}&mode=manual"
                ),
                "can_takeover": True,
                "can_logout": True,
                "last_action": "Test session.",
            }
        )
    )


class PublicCompetitorPageHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        _restore_hygiene_session()

    def test_cdp_connect_closes_extra_public_pages_but_keeps_login_page(self) -> None:
        work = _FakeHygienePage("https://hotels.ctrip.com/hotels/detail/?hotelId=1")
        extra_competitor = _FakeHygienePage(
            "https://hotels.ctrip.com/hotels/131305310.html?cityid=477"
        )
        extra_blank = _FakeHygienePage("about:blank")
        login = _FakeHygienePage(
            "https://passport.ctrip.com/user/login?backurl=https%3A%2F%2Fhotels.ctrip.com"
        )
        context = _FakeHygieneContext([work, extra_competitor, extra_blank, login])
        reader = _make_reader(_FakeHygieneBrowser(context))

        async def run() -> Any:
            return await reader._connect_public_competitor_cdp_page_unlocked(
                platform_account_id=_PLATFORM_ACCOUNT,
                browser_session_id=_BROWSER_SESSION,
                cdp_url="http://127.0.0.1:9222",
            )

        page = asyncio.run(run())

        self.assertIs(page, work)
        self.assertFalse(work.closed)
        self.assertTrue(extra_competitor.closed)
        self.assertTrue(extra_blank.closed)
        self.assertFalse(login.closed)

    def test_cdp_connect_brings_work_page_to_front(self) -> None:
        work = _FakeHygienePage("https://hotels.ctrip.com/hotels/detail/?hotelId=2")
        context = _FakeHygieneContext([work])
        reader = _make_reader(_FakeHygieneBrowser(context))

        async def run() -> Any:
            return await reader._connect_public_competitor_cdp_page_unlocked(
                platform_account_id=_PLATFORM_ACCOUNT,
                browser_session_id=_BROWSER_SESSION,
                cdp_url="http://127.0.0.1:9222",
            )

        page = asyncio.run(run())

        self.assertIs(page, work)
        self.assertGreaterEqual(work.bring_to_front_count, 1)

    def test_recovery_reuses_healthy_cached_page_with_front_refresh(self) -> None:
        cached = _FakeHygienePage("https://hotels.ctrip.com/hotels/detail/?hotelId=3")
        reader = _make_reader(_FakeHygieneBrowser(_FakeHygieneContext([])))
        cache_key = _public_competitor_cache_key(
            platform_account_id=_PLATFORM_ACCOUNT,
            browser_session_id=_BROWSER_SESSION,
        )
        reader._pages[cache_key] = cached  # type: ignore[assignment]

        async def run() -> Any:
            return await reader._get_or_create_public_price_page_with_recovery(
                platform_account_id=_PLATFORM_ACCOUNT,
                browser_session_id=_BROWSER_SESSION,
            )

        page = asyncio.run(run())

        self.assertIs(page, cached)
        self.assertGreaterEqual(cached.bring_to_front_count, 1)
        self.assertGreaterEqual(cached.evaluate_count, 1)

    def test_recovery_discards_cached_page_when_channel_hangs(self) -> None:
        from unittest.mock import patch

        fresh = _FakeHygienePage("https://hotels.ctrip.com/hotels/detail/?hotelId=4")
        hung = _FakeHygienePage(
            "https://hotels.ctrip.com/hotels/detail/?hotelId=5",
            evaluate_delay=5.0,
        )
        context = _FakeHygieneContext([fresh])
        reader = _make_reader(_FakeHygieneBrowser(context))
        cache_key = _public_competitor_cache_key(
            platform_account_id=_PLATFORM_ACCOUNT,
            browser_session_id=_BROWSER_SESSION,
        )
        reader._pages[cache_key] = hung  # type: ignore[assignment]

        async def run() -> Any:
            with patch(
                "app.domain.browser.ctrip_public_price_reader"
                "._PUBLIC_PAGE_LIVENESS_TIMEOUT_SECONDS",
                0.05,
            ):
                return await reader._get_or_create_public_price_page_with_recovery(
                    platform_account_id=_PLATFORM_ACCOUNT,
                    browser_session_id=_BROWSER_SESSION,
                )

        page = asyncio.run(run())

        self.assertIs(page, fresh)
        self.assertGreaterEqual(hung.evaluate_count, 1)
        self.assertIs(reader._pages.get(cache_key), fresh)


if __name__ == "__main__":
    unittest.main()
