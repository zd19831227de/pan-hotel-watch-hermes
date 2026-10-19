from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from typing import Any
from unittest.mock import patch

from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService
from app.domain.browser.ctrip_public_price_reader import (
    CtripPublicCompetitorPriceReader,
    _PublicCompetitorCdpUnavailableError,
)
from app.domain.browser.sessions import BrowserSession, get_browser_session_store
from app.core.settings import get_settings


class PublicCompetitorCdpPriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        _restore_competitor_browser_session(
            browser_session_id="browser-competitor-a",
            platform_account_id="ctrip-competitor-a",
            session_port=9321,
        )

    def tearDown(self) -> None:
        _clear_settings_cache()

    def test_public_competitor_page_prefers_session_port_cdp(self) -> None:
        service = CtripEbookingBrowserService()
        page = _FakePublicCdpPage()
        seen: dict[str, Any] = {}
        persistent_calls: list[tuple[str, str]] = []
        ebk_page_calls: list[tuple[str, str]] = []

        async def fake_connect_public_cdp_page(
            *,
            platform_account_id: str,
            browser_session_id: str,
            cdp_url: str,
        ) -> _FakePublicCdpPage:
            seen["platform_account_id"] = platform_account_id
            seen["browser_session_id"] = browser_session_id
            seen["cdp_url"] = cdp_url
            return page

        async def fail_persistent_page(
            *,
            platform_account_id: str,
            browser_session_id: str,
        ) -> _FakePublicCdpPage:
            persistent_calls.append((platform_account_id, browser_session_id))
            raise AssertionError("public persistent page should not be used when CDP works")

        async def fail_ebk_page(**kwargs: Any) -> _FakePublicCdpPage:
            ebk_page_calls.append((kwargs["platform_account_id"], kwargs["browser_session_id"]))
            raise AssertionError("EBK _get_or_create_page should not be used for competitor reads")

        reader = service._public_price_reader
        reader._get_or_create_public_persistent_page = fail_persistent_page  # type: ignore[method-assign]
        service._get_or_create_page = fail_ebk_page  # type: ignore[method-assign]

        async def run() -> _FakePublicCdpPage:
            with patch.object(
                reader,
                "_connect_public_competitor_cdp_page",
                fake_connect_public_cdp_page,
            ):
                return await reader._get_or_create_public_price_page_with_recovery(
                    platform_account_id="ctrip-competitor-a",
                    browser_session_id="browser-competitor-a",
                )

        result = asyncio.run(run())

        self.assertIs(result, page)
        self.assertEqual(
            seen,
            {
                "platform_account_id": "ctrip-competitor-a",
                "browser_session_id": "browser-competitor-a",
                "cdp_url": "http://127.0.0.1:9321",
            },
        )
        self.assertEqual(persistent_calls, [])
        self.assertEqual(ebk_page_calls, [])

    def test_public_competitor_page_uses_configured_global_cdp_after_session_port_fails(
        self,
    ) -> None:
        _clear_settings_cache()
        os.environ["CTRIP_PUBLIC_COMPETITOR_ENABLE_CDP"] = "true"
        os.environ["CTRIP_PUBLIC_COMPETITOR_CDP_URL"] = "http://127.0.0.1:9222"
        get_settings.cache_clear()

        service = CtripEbookingBrowserService()
        page = _FakePublicCdpPage()
        seen_urls: list[str] = []

        async def fake_connect_public_cdp_page(
            *,
            platform_account_id: str,
            browser_session_id: str,
            cdp_url: str,
        ) -> _FakePublicCdpPage | None:
            seen_urls.append(cdp_url)
            if cdp_url == "http://127.0.0.1:9222":
                return page
            return None

        async def fail_persistent_page(**kwargs: Any) -> _FakePublicCdpPage:
            raise AssertionError("persistent page should not be used when global CDP works")

        reader = service._public_price_reader
        reader._get_or_create_public_persistent_page = fail_persistent_page  # type: ignore[method-assign]
        reader._connect_public_competitor_cdp_page = fake_connect_public_cdp_page  # type: ignore[method-assign]

        async def run() -> _FakePublicCdpPage:
            return await reader._get_or_create_public_price_page_with_recovery(
                platform_account_id="ctrip-competitor-a",
                browser_session_id="browser-competitor-a",
            )

        result = asyncio.run(run())

        self.assertIs(result, page)
        self.assertEqual(
            seen_urls,
            ["http://127.0.0.1:9321", "http://127.0.0.1:9222"],
        )

    def test_public_competitor_page_falls_back_to_public_persistent_when_cdp_unavailable(
        self,
    ) -> None:
        service = CtripEbookingBrowserService()
        page = _FakePublicCdpPage()
        persistent_calls: list[tuple[str, str]] = []
        ebk_page_calls: list[tuple[str, str]] = []

        async def fake_connect_public_cdp_page(**kwargs: Any) -> None:
            return None

        async def fake_persistent_page(
            *,
            platform_account_id: str,
            browser_session_id: str,
        ) -> _FakePublicCdpPage:
            persistent_calls.append((platform_account_id, browser_session_id))
            return page

        async def fail_ebk_page(**kwargs: Any) -> _FakePublicCdpPage:
            ebk_page_calls.append((kwargs["platform_account_id"], kwargs["browser_session_id"]))
            raise AssertionError("EBK _get_or_create_page should not be used for competitor reads")

        reader = service._public_price_reader
        reader._get_or_create_public_persistent_page = fake_persistent_page  # type: ignore[method-assign]
        service._get_or_create_page = fail_ebk_page  # type: ignore[method-assign]

        async def run() -> _FakePublicCdpPage:
            with patch.object(
                reader,
                "_connect_public_competitor_cdp_page",
                fake_connect_public_cdp_page,
            ):
                return await reader._get_or_create_public_price_page_with_recovery(
                    platform_account_id="ctrip-competitor-a",
                    browser_session_id="browser-competitor-a",
                )

        result = asyncio.run(run())

        self.assertIs(result, page)
        self.assertEqual(
            persistent_calls,
            [("ctrip-competitor-a", "browser-competitor-a")],
        )
        self.assertEqual(ebk_page_calls, [])

    def test_public_competitor_page_can_require_cdp_without_persistent_fallback(self) -> None:
        _clear_settings_cache()
        os.environ["CTRIP_PUBLIC_COMPETITOR_ENABLE_CDP"] = "true"
        os.environ["CTRIP_PUBLIC_COMPETITOR_CDP_FALLBACK_ENABLED"] = "false"
        get_settings.cache_clear()

        service = CtripEbookingBrowserService()
        reader = service._public_price_reader

        async def fake_connect_public_cdp_page(**kwargs: Any) -> None:
            return None

        async def fail_persistent_page(**kwargs: Any) -> _FakePublicCdpPage:
            raise AssertionError("persistent page should not be used when CDP is required")

        reader._get_or_create_public_persistent_page = fail_persistent_page  # type: ignore[method-assign]
        reader._connect_public_competitor_cdp_page = fake_connect_public_cdp_page  # type: ignore[method-assign]

        async def run() -> None:
            with self.assertRaises(_PublicCompetitorCdpUnavailableError):
                await reader._get_or_create_public_price_page_with_recovery(
                    platform_account_id="ctrip-competitor-a",
                    browser_session_id="browser-competitor-a",
                )

        asyncio.run(run())

    def test_read_competitor_prices_requires_human_when_cdp_unhealthy_and_no_fallback(
        self,
    ) -> None:
        _clear_settings_cache()
        os.environ["CTRIP_PUBLIC_COMPETITOR_ENABLE_CDP"] = "true"
        os.environ["CTRIP_PUBLIC_COMPETITOR_CDP_URL"] = "http://127.0.0.1:9222"
        os.environ["CTRIP_PUBLIC_COMPETITOR_CDP_FALLBACK_ENABLED"] = "false"
        get_settings.cache_clear()

        service = CtripEbookingBrowserService()
        reader = service._public_price_reader

        async def fake_connect_public_cdp_page(**kwargs: Any) -> None:
            return None

        async def fail_persistent_page(**kwargs: Any) -> _FakePublicCdpPage:
            raise AssertionError("persistent page should not be used when CDP is required")

        reader._get_or_create_public_persistent_page = fail_persistent_page  # type: ignore[method-assign]
        reader._connect_public_competitor_cdp_page = fake_connect_public_cdp_page  # type: ignore[method-assign]

        async def run() -> Any:
            return await service.read_competitor_prices(
                platform_account_id="ctrip-competitor-a",
                browser_session_id="browser-competitor-a",
                own_hotel_url="https://hotels.ctrip.com/hotels/detail/?hotelId=122575330",
                competitor_urls=[
                    "https://hotels.ctrip.com/hotels/detail/?hotelId=133146885"
                ],
                check_dates=["2026-06-05"],
                actor_id="tester",
                push_to_feishu=False,
            )

        result = asyncio.run(run())

        self.assertEqual(result.status, "human_required")
        self.assertEqual(
            result.evidence.get("human_required_reason"),
            "ctrip_public_competitor_cdp_unavailable",
        )
        self.assertEqual(result.data["query_type"], "ctrip_public_competitor_price_snapshot")


class _FakePublicCdpPage:
    def is_closed(self) -> bool:
        return False


def _clear_settings_cache() -> None:
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    for key in (
        "CTRIP_PUBLIC_COMPETITOR_ENABLE_CDP",
        "CTRIP_PUBLIC_COMPETITOR_CDP_URL",
        "CTRIP_PUBLIC_COMPETITOR_CDP_FALLBACK_ENABLED",
    ):
        os.environ.pop(key, None)


def _restore_competitor_browser_session(
    *,
    browser_session_id: str,
    platform_account_id: str,
    session_port: int,
) -> None:
    get_browser_session_store().restore(
        BrowserSession.model_validate(
            {
                "browser_session_id": browser_session_id,
                "platform_account_id": platform_account_id,
                "display_name": platform_account_id,
                "platform": "ctrip",
                "session_port": session_port,
                "intended_use": "competitor_price",
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


if __name__ == "__main__":
    unittest.main()
