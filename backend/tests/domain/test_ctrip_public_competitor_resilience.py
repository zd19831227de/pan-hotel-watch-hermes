from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from app.domain.browser import ctrip_public_price_reader as public_reader_module
from app.domain.browser import ctrip_public_page_reader as public_page_reader_module
from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService
from app.domain.browser.ctrip_ebooking_session_helpers import _ebooking_cache_key
from app.domain.browser.ctrip_public_competitor_comparison import (
    _public_competitor_price_status,
    _public_competitor_price_summary,
)
from app.domain.browser.ctrip_public_price_reader import (
    CtripPublicCompetitorPriceReader,
    _public_competitor_cache_key,
    _public_competitor_cdp_browser_key,
)
from app.domain.browser.ctrip_public_page_reader import (
    _ensure_public_room_rates_loaded,
    _safe_public_room_rates,
)


class PublicCompetitorStatusTests(unittest.TestCase):
    def test_tomorrow_read_failure_makes_overall_status_partial(self) -> None:
        snapshots = [
            {
                "role": "own",
                "hotel_name": "本店",
                "status": "success",
                "price": {"amount": 416, "display": "¥416"},
                "date_context": {"check_in": "2026-06-18", "check_out": "2026-06-19"},
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "breakfast": "无早",
                        "price": {"amount": 416, "currency": "CNY"},
                    }
                ],
                "tomorrow": {
                    "status": "needs_review",
                    "review_reason": "public_tomorrow_price_read_failed",
                    "date_context": {"check_in": "2026-06-19", "check_out": "2026-06-20"},
                    "room_rates": [],
                },
            },
            {
                "role": "competitor_1",
                "hotel_name": "竞品A",
                "status": "success",
                "price": {"amount": 415, "display": "¥415"},
                "date_context": {"check_in": "2026-06-18", "check_out": "2026-06-19"},
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "breakfast": "无早",
                        "price": {"amount": 415, "currency": "CNY"},
                    }
                ],
                "tomorrow": {
                    "status": "success",
                    "date_context": {"check_in": "2026-06-19", "check_out": "2026-06-20"},
                    "room_rates": [
                        {
                            "source": "room_rate_dom_display_price",
                            "breakfast": "含早",
                            "price": {"amount": 584, "currency": "CNY"},
                        }
                    ],
                },
            },
        ]

        status = _public_competitor_price_status(snapshots)

        self.assertEqual(status, "partial")
        self.assertIn(
            "部分成功",
            _public_competitor_price_summary(
                status,
                snapshots,
                {"priced_count": 2},
            ),
        )


class PublicCompetitorNavigationTests(unittest.TestCase):
    def test_login_page_skips_room_rate_dom_wait(self) -> None:
        service = _FakePublicReaderService()
        reader = CtripPublicCompetitorPriceReader(service)  # type: ignore[arg-type]
        page = _FakePublicPricePage(
            url_sequence=[
                "https://passport.ctrip.com/user/login?backurl=https%3A%2F%2Fhotels.ctrip.com"
            ]
        )
        asyncio.run(
            page.goto(
                "https://hotels.ctrip.com/hotels/detail/?hotelId=1",
                wait_until="commit",
                timeout=45_000,
            )
        )
        room_rate_loader = AsyncMock(return_value=[])

        async def run() -> dict[str, Any]:
            with (
                patch.object(public_reader_module, "_ensure_public_room_rates_loaded", room_rate_loader),
                patch.object(public_reader_module, "_safe_public_date_evidence_text", _fake_empty_text),
                patch.object(public_reader_module, "_safe_public_room_rates", _fake_room_rates),
                patch.object(
                    public_reader_module,
                    "_classify_public_hotel_page_state",
                    return_value="login_required",
                ),
                patch("app.domain.browser.ctrip_ebooking_session_helpers._settle_page", _noop_page),
                patch("app.domain.browser.ctrip_ebooking_session_helpers._safe_title", _fake_title),
                patch(
                    "app.domain.browser.ctrip_ebooking_session_helpers._safe_visible_text",
                    _fake_visible_text,
                ),
                patch.object(
                    public_reader_module,
                    "_extract_public_hotel_price_snapshot",
                    _fake_public_snapshot_from_page_state,
                ),
            ):
                parsed, _ = await reader._parse_public_price_target_page(
                    page=page,  # type: ignore[arg-type]
                    platform_account_id="acct-1",
                    target={
                        "role": "own",
                        "label": "Own",
                        "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=1",
                    },
                    require_visible_date_match=False,
                )
                return parsed

        parsed = asyncio.run(run())

        self.assertEqual(parsed["page_state"], "login_required")
        room_rate_loader.assert_not_awaited()

    def test_batch_continues_after_retried_target_still_requires_login(self) -> None:
        service = _FakePublicReaderService()
        reader = CtripPublicCompetitorPriceReader(service)  # type: ignore[arg-type]
        page = _FakePublicPricePage()
        login_snapshot = {
            "role": "own",
            "hotel_name": "Own",
            "status": "human_required",
            "page_state": "login_required",
            "current_url": "https://passport.ctrip.com/user/login",
            "date_context": {},
            "room_rates": [],
        }
        async def fake_read_target(**kwargs: Any) -> tuple[object, dict[str, Any], str]:
            target = kwargs["target"]
            if target["role"] == "own":
                return page, login_snapshot, "login.png"
            return page, {
                "role": target["role"],
                "hotel_name": target["label"],
                "status": "success",
                "page_state": "authenticated",
                "requested_url": target["url"],
                "current_url": target["url"],
                "date_context": {"check_in": "2026-08-03"},
                "room_rates": [],
            }, "competitor.png"

        read_target = AsyncMock(side_effect=fake_read_target)
        new_page = AsyncMock(return_value=_FakePublicPricePage())
        reader._get_or_create_public_price_page_with_recovery = AsyncMock(  # type: ignore[method-assign]
            return_value=page
        )
        reader._read_public_price_target = read_target  # type: ignore[method-assign]
        reader._new_public_price_page = new_page  # type: ignore[method-assign]

        async def run() -> Any:
            with (
                patch.object(
                    public_reader_module,
                    "_sync_public_competitor_browser_session_state",
                    return_value=None,
                ),
                patch.object(
                    public_reader_module,
                    "_public_competitor_browser_mode_evidence",
                    return_value={},
                ),
            ):
                return await reader.read_competitor_prices(
                    platform_account_id="acct-1",
                    browser_session_id="browser-1",
                    own_hotel_url=(
                        "https://hotels.ctrip.com/hotels/detail/?hotelId=122575330"
                    ),
                    competitor_urls=[
                        "https://hotels.ctrip.com/hotels/detail/?hotelId=133146885"
                    ],
                    check_dates=["2026-08-03"],
                    actor_id="test",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "human_required")
        self.assertEqual(len(result.data["snapshots"]), 2)
        self.assertEqual(result.data["snapshots"][0]["status"], "human_required")
        self.assertEqual(result.data["snapshots"][1]["status"], "success")
        self.assertEqual(read_target.await_count, 3)
        new_page.assert_not_awaited()

    def test_successful_batch_reads_today_and_tomorrow_serially_on_one_page(self) -> None:
        service = _FakePublicReaderService()
        reader = CtripPublicCompetitorPriceReader(service)  # type: ignore[arg-type]
        shared_page = _FakePublicPricePage()
        extra_page = _FakePublicPricePage()
        read_pages: list[object] = []

        async def fake_read_target(**kwargs: Any) -> tuple[object, dict[str, Any], str]:
            read_pages.append(kwargs["page"])
            target = kwargs["target"]
            is_tomorrow = bool(kwargs.get("require_visible_date_match"))
            check_in = "2026-08-04" if is_tomorrow else "2026-08-03"
            snapshot = {
                "role": target["role"],
                "hotel_name": target["label"],
                "status": "success",
                "page_state": "authenticated",
                "requested_url": target["url"],
                "current_url": target["url"],
                "date_context": {"check_in": check_in},
                "price": {"amount": 416, "display": "CNY 416", "currency": "CNY"},
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "price": {"amount": 416, "currency": "CNY"},
                    }
                ],
            }
            return kwargs["page"], snapshot, f"{target['role']}.png"

        read_target = AsyncMock(side_effect=fake_read_target)
        new_page = AsyncMock(return_value=extra_page)
        reader._get_or_create_public_price_page_with_recovery = AsyncMock(  # type: ignore[method-assign]
            return_value=shared_page
        )
        reader._recover_public_price_page_if_closed = AsyncMock(  # type: ignore[method-assign]
            return_value=shared_page
        )
        reader._read_public_price_target = read_target  # type: ignore[method-assign]
        reader._new_public_price_page = new_page  # type: ignore[method-assign]

        async def run() -> Any:
            with (
                patch.object(public_reader_module, "_public_competitor_read_delay_ms", return_value=0),
                patch.object(
                    public_reader_module,
                    "_sync_public_competitor_browser_session_state",
                    return_value=None,
                ),
                patch.object(
                    public_reader_module,
                    "_public_competitor_browser_mode_evidence",
                    return_value={},
                ),
            ):
                return await reader.read_competitor_prices(
                    platform_account_id="acct-1",
                    browser_session_id="browser-1",
                    own_hotel_url=(
                        "https://hotels.ctrip.com/hotels/detail/?hotelId=122575330"
                    ),
                    competitor_urls=[
                        "https://hotels.ctrip.com/hotels/detail/?hotelId=133146885"
                    ],
                    check_dates=["2026-08-03"],
                    actor_id="test",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.data["snapshots"]), 2)
        self.assertTrue(all(item["tomorrow"]["status"] == "success" for item in result.data["snapshots"]))
        self.assertEqual(len(read_pages), 4)
        self.assertTrue(all(page is shared_page for page in read_pages))
        new_page.assert_not_awaited()

    def test_batch_continues_when_tomorrow_retry_still_requires_login(self) -> None:
        service = _FakePublicReaderService()
        reader = CtripPublicCompetitorPriceReader(service)  # type: ignore[arg-type]
        shared_page = _FakePublicPricePage()
        calls: list[dict[str, Any]] = []

        async def fake_read_target(**kwargs: Any) -> tuple[object, dict[str, Any], str]:
            calls.append(kwargs)
            target = kwargs["target"]
            if kwargs.get("require_visible_date_match"):
                return kwargs["page"], {
                    "role": target["role"],
                    "hotel_name": target["label"],
                    "status": "human_required",
                    "page_state": "login_required",
                    "current_url": "https://passport.ctrip.com/user/login",
                    "date_context": {},
                    "room_rates": [],
                }, "login.png"
            return kwargs["page"], {
                "role": target["role"],
                "hotel_name": target["label"],
                "status": "success",
                "page_state": "authenticated",
                "requested_url": target["url"],
                "current_url": target["url"],
                "date_context": {"check_in": "2026-08-03"},
                "price": {"amount": 416, "display": "CNY 416", "currency": "CNY"},
                "room_rates": [],
            }, "today.png"

        reader._get_or_create_public_price_page_with_recovery = AsyncMock(  # type: ignore[method-assign]
            return_value=shared_page
        )
        reader._recover_public_price_page_if_closed = AsyncMock(  # type: ignore[method-assign]
            return_value=shared_page
        )
        reader._read_public_price_target = AsyncMock(  # type: ignore[method-assign]
            side_effect=fake_read_target
        )

        async def run() -> Any:
            with (
                patch.object(public_reader_module, "_public_competitor_read_delay_ms", return_value=0),
                patch.object(
                    public_reader_module,
                    "_sync_public_competitor_browser_session_state",
                    return_value=None,
                ),
                patch.object(
                    public_reader_module,
                    "_public_competitor_browser_mode_evidence",
                    return_value={},
                ),
            ):
                return await reader.read_competitor_prices(
                    platform_account_id="acct-1",
                    browser_session_id="browser-1",
                    own_hotel_url=(
                        "https://hotels.ctrip.com/hotels/detail/?hotelId=122575330"
                    ),
                    competitor_urls=[
                        "https://hotels.ctrip.com/hotels/detail/?hotelId=133146885"
                    ],
                    check_dates=["2026-08-03"],
                    actor_id="test",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "human_required")
        self.assertEqual(len(result.data["snapshots"]), 2)
        self.assertEqual(result.data["snapshots"][0]["tomorrow"]["status"], "human_required")
        self.assertEqual(result.data["snapshots"][1]["tomorrow"]["status"], "human_required")
        self.assertEqual(len(calls), 4)

    def test_ensure_public_room_rates_loaded_expands_remaining_room_cards(self) -> None:
        page = _ExpandableRoomRatePage()

        asyncio.run(_ensure_public_room_rates_loaded(page))  # type: ignore[arg-type]

        self.assertEqual(page.clicked_texts, ["展示其余5个房型"])
        self.assertEqual(
            page.waited_selectors[0],
            '[class*="saleRoomItemBox"] [class*="displayPrice"]',
        )

    def test_ensure_public_room_rates_loaded_waits_for_late_remaining_room_button(self) -> None:
        page = _DelayedExpandableRoomRatePage()

        asyncio.run(_ensure_public_room_rates_loaded(page))  # type: ignore[arg-type]

        self.assertEqual(page.clicked_texts, ["展示其余2个房型"])
        self.assertGreaterEqual(len(page.waits), 2)

    def test_ensure_public_room_rates_loaded_uses_jittered_waits_and_scroll_steps(self) -> None:
        page = _JitteredScrollableRoomRatePage()
        wait_values = iter([317, 421, 389, 443, 467, 503, 541] + [557] * 20)
        scroll_jitter = {
            "windowFactor": 0.73,
            "windowMin": 731,
            "containerFactor": 0.79,
            "containerMin": 479,
        }

        with (
            patch.object(
                public_page_reader_module,
                "_public_room_rate_wait_ms",
                side_effect=lambda min_ms, max_ms: next(wait_values),
                create=True,
            ),
            patch.object(
                public_page_reader_module,
                "_public_room_rate_scroll_jitter",
                return_value=scroll_jitter,
                create=True,
            ),
        ):
            asyncio.run(_ensure_public_room_rates_loaded(page))  # type: ignore[arg-type]

        self.assertIn(317, page.waits)
        self.assertIn(421, page.waits)
        self.assertTrue(page.evaluate_args)
        scroll_args = [args for args in page.evaluate_args if args]
        self.assertTrue(scroll_args)
        self.assertTrue(all(args == (scroll_jitter,) for args in scroll_args))

    def test_safe_public_room_rates_ignores_nested_class_matches_and_keeps_all_room_cards(
        self,
    ) -> None:
        rates = asyncio.run(_read_rates_from_nested_room_card_html())

        self.assertEqual(len(rates), 10)
        self.assertEqual(
            [rate["room_type"] for rate in rates[::2]],
            [
                "高级大床房（安睡床品+特色茶具）",
                "商务大床房（安睡床品+特色茶具）",
                "豪华大床房（高空城景+超大落地窗）",
                "江景大床房（高空江景+超大落地窗）",
                "江景双床房（高空江景+2张大床+超大落地窗）",
            ],
        )
        self.assertNotIn("1张1.8米大床", {rate["room_type"] for rate in rates})
        self.assertEqual(
            [(rate["breakfast"], rate["price"]["amount"]) for rate in rates[:4]],
            [
                ("无早餐", 423),
                ("含单早", 461),
                ("无早餐", 441),
                ("含单早", 480),
            ],
        )

    def test_safe_public_room_rates_keeps_visible_standard_room_card(self) -> None:
        rates = asyncio.run(
            _read_rates_from_room_card_html(
                [
                    ("云夕·逸致标准房丨记忆床垫+无线投屏", "1张1.5米床", 409, 431),
                ]
            )
        )

        self.assertEqual(
            [(rate["room_type"], rate["breakfast"], rate["price"]["amount"]) for rate in rates],
            [
                ("云夕·逸致标准房丨记忆床垫+无线投屏", "无早餐", 409),
                ("云夕·逸致标准房丨记忆床垫+无线投屏", "含单早", 431),
            ],
        )

    def test_safe_public_room_rates_keeps_structured_card_title_without_room_keywords(
        self,
    ) -> None:
        rates = asyncio.run(
            _read_rates_from_room_card_html(
                [
                    ("森泊·云隐庭院丨浴缸+投影+茶台", "1张1.8米床", 699, 738),
                ]
            )
        )

        self.assertEqual(
            [(rate["room_type"], rate["breakfast"], rate["price"]["amount"]) for rate in rates],
            [
                ("森泊·云隐庭院丨浴缸+投影+茶台", "无早餐", 699),
                ("森泊·云隐庭院丨浴缸+投影+茶台", "含单早", 738),
            ],
        )

    def test_ensure_public_room_rates_loaded_scrolls_lazy_room_cards(self) -> None:
        rates = asyncio.run(_read_rates_from_lazy_room_card_html())

        room_types = [rate["room_type"] for rate in rates[::2]]
        self.assertEqual(len(room_types), 10)
        self.assertEqual(
            room_types,
            [
                "岚夕·雅致大床房丨记忆床垫+徕芬吹风",
                "岚夕·丽致大床房丨MINI冰箱+记忆床垫",
                "岚夕·丽致双床房丨MINI冰箱+记忆床垫",
                "云夕·城景大床房丨阔景视野+无线投屏",
                "云夕·逸致标准房丨记忆床垫+无线投屏",
                "云夕·逸致商务大床房丨MINI冰箱+语音音响",
                "水夕·行政大床房丨空气净化器+软榻沙发+MINI冰箱",
                "云夕丨逸致家庭房",
                "水夕·180度视界套房丨浴缸+空气净化器+徕芬吹风",
                "海底小纵队丨亲子家庭房",
            ],
        )

    def test_ensure_public_room_rates_loaded_accumulates_unfolded_virtual_room_cards(self) -> None:
        rates = asyncio.run(_read_rates_from_virtualized_room_card_html())

        room_types = [rate["room_type"] for rate in rates[::2]]
        self.assertEqual(len(room_types), 10)
        self.assertEqual(
            room_types,
            [
                "岚夕·雅致大床房丨记忆床垫+徕芬吹风",
                "岚夕·丽致大床房丨MINI冰箱+记忆床垫",
                "岚夕·丽致双床房丨MINI冰箱+记忆床垫",
                "云夕·城景大床房丨阔景视野+无线投屏",
                "云夕·逸致标准房丨记忆床垫+无线投屏",
                "云夕·逸致商务大床房丨MINI冰箱+语音音响",
                "水夕·行政大床房丨空气净化器+软榻沙发+MINI冰箱",
                "云夕丨逸致家庭房",
                "水夕·180度视界套房丨浴缸+空气净化器+徕芬吹风",
                "海底小纵队丨亲子家庭房",
            ],
        )

    def test_login_redirect_waits_and_retries_same_target_once(self) -> None:
        service = _FakePublicReaderService()
        reader = CtripPublicCompetitorPriceReader(service)  # type: ignore[arg-type]
        page = _FakePublicPricePage(
            url_sequence=[
                "https://passport.ctrip.com/user/login?backurl=https%3A%2F%2Fhotels.ctrip.com%2Fhotels%2Fdetail%2F%3FhotelId%3D1",
                "https://hotels.ctrip.com/hotels/detail/?hotelId=1",
            ]
        )

        async def run() -> None:
            with (
                patch.object(public_reader_module, "_public_competitor_read_delay_ms", return_value=2400),
                patch.object(public_reader_module, "_ensure_public_room_rates_loaded", _noop_page),
                patch.object(public_reader_module, "_safe_public_date_evidence_text", _fake_empty_text),
                patch.object(public_reader_module, "_safe_public_room_rates", _fake_room_rates),
                patch.object(
                    public_reader_module,
                    "_classify_public_hotel_page_state",
                    side_effect=["login_required", "authenticated"],
                ),
                patch("app.domain.browser.ctrip_ebooking_session_helpers._settle_page", _noop_page),
                patch("app.domain.browser.ctrip_ebooking_session_helpers._safe_title", _fake_title),
                patch("app.domain.browser.ctrip_ebooking_session_helpers._safe_visible_text", _fake_visible_text),
                patch.object(
                    public_reader_module,
                    "_extract_public_hotel_price_snapshot",
                    _fake_public_snapshot_from_page_state,
                ),
            ):
                read_page, parsed, screenshot_path = await reader._read_public_price_target(
                    page=page,  # type: ignore[arg-type]
                    platform_account_id="acct-1",
                    browser_session_id="browser-1",
                    target={
                        "role": "own",
                        "label": "鏈簵",
                        "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=1",
                    },
                )
            self.assertIs(read_page, page)
            self.assertEqual(parsed["status"], "success")
            self.assertEqual(screenshot_path, "shot.png")

        asyncio.run(run())

        self.assertEqual(service.discard_calls, [])
        self.assertEqual(page.goto_count, 2)
        self.assertEqual(page.waits, [2400])

    def test_goto_timeout_discards_cached_page_waits_and_retries_once(self) -> None:
        service = _FakePublicReaderService()
        reader = CtripPublicCompetitorPriceReader(service)  # type: ignore[arg-type]
        stale_page = _FakePublicPricePage(fail_with_timeout=True)
        recovered_page = _FakePublicPricePage()
        recovery_calls: list[tuple[str, str]] = []

        async def recover(
            *,
            platform_account_id: str,
            browser_session_id: str,
        ) -> _FakePublicPricePage:
            recovery_calls.append((platform_account_id, browser_session_id))
            return recovered_page

        reader._get_or_create_public_price_page_with_recovery = recover  # type: ignore[method-assign]

        async def run() -> None:
            with (
                patch.object(public_reader_module, "_public_competitor_read_delay_ms", return_value=2300),
                patch.object(public_reader_module, "_ensure_public_room_rates_loaded", _noop_page),
                patch.object(public_reader_module, "_safe_public_date_evidence_text", _fake_empty_text),
                patch.object(public_reader_module, "_safe_public_room_rates", _fake_room_rates),
                patch("app.domain.browser.ctrip_ebooking_session_helpers._settle_page", _noop_page),
                patch("app.domain.browser.ctrip_ebooking_session_helpers._safe_title", _fake_title),
                patch("app.domain.browser.ctrip_ebooking_session_helpers._safe_visible_text", _fake_visible_text),
                patch.object(
                    public_reader_module,
                    "_extract_public_hotel_price_snapshot",
                    _fake_public_snapshot,
                ),
            ):
                page, parsed, screenshot_path = await reader._read_public_price_target(
                    page=stale_page,  # type: ignore[arg-type]
                    platform_account_id="acct-1",
                    browser_session_id="browser-1",
                    target={
                        "role": "own",
                        "label": "本店",
                        "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=1",
                    },
                )
            self.assertIs(page, recovered_page)
            self.assertEqual(parsed["status"], "success")
            self.assertEqual(screenshot_path, "shot.png")

        asyncio.run(run())

        self.assertEqual(service.discard_calls, [("acct-1", "browser-1")])
        self.assertEqual(recovery_calls, [("acct-1", "browser-1")])
        self.assertEqual(stale_page.goto_count, 1)
        self.assertEqual(stale_page.waits, [])
        self.assertEqual(recovered_page.goto_count, 1)
        self.assertEqual(recovered_page.waits, [2300])


class PublicCompetitorBrowserCacheTests(unittest.TestCase):
    def test_discard_cached_browser_state_clears_public_competitor_cache(self) -> None:
        service = CtripEbookingBrowserService()
        ebk_cache_key = _ebooking_cache_key(
            platform_account_id="acct-1",
            browser_session_id="browser-1",
        )
        public_cache_key = _public_competitor_cache_key(
            platform_account_id="acct-1",
            browser_session_id="browser-1",
        )
        public_browser_key = _public_competitor_cdp_browser_key("browser-1")
        ebk_page = _Closable()
        ebk_context = _Closable()
        ebk_browser = _Closable()
        public_page = _Closable()
        public_context = _Closable()
        public_browser = _Closable()
        service._pages[ebk_cache_key] = ebk_page  # type: ignore[assignment]
        service._contexts[ebk_cache_key] = ebk_context  # type: ignore[assignment]
        service._cdp_browsers["browser-1"] = ebk_browser  # type: ignore[assignment]
        service._pages[public_cache_key] = public_page  # type: ignore[assignment]
        service._contexts[public_cache_key] = public_context  # type: ignore[assignment]
        service._cdp_browsers[public_browser_key] = public_browser  # type: ignore[assignment]

        async def run() -> None:
            await service._discard_cached_browser_state(
                platform_account_id="acct-1",
                browser_session_id="browser-1",
            )

        asyncio.run(run())

        self.assertNotIn(ebk_cache_key, service._pages)
        self.assertNotIn(ebk_cache_key, service._contexts)
        self.assertNotIn("browser-1", service._cdp_browsers)
        self.assertNotIn(public_cache_key, service._pages)
        self.assertNotIn(public_cache_key, service._contexts)
        self.assertNotIn(public_browser_key, service._cdp_browsers)
        self.assertFalse(ebk_page.closed)
        self.assertFalse(ebk_context.closed)
        self.assertFalse(ebk_browser.closed)
        self.assertFalse(public_page.closed)
        self.assertFalse(public_context.closed)
        self.assertTrue(public_browser.closed)


class _FakePublicReaderService:
    def __init__(self) -> None:
        self.discard_calls: list[tuple[str, str]] = []
        self._pages: dict[str, object] = {}

    async def _discard_cached_browser_state(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> None:
        self.discard_calls.append((platform_account_id, browser_session_id))

    async def _capture_screenshot(self, page: object, platform_account_id: str) -> str:
        return "shot.png"


class _FakePublicPricePage:
    def __init__(
        self,
        *,
        fail_with_timeout: bool = False,
        url_sequence: list[str] | None = None,
    ) -> None:
        self.fail_with_timeout = fail_with_timeout
        self.url_sequence = list(url_sequence or [])
        self.goto_count = 0
        self.waits: list[int] = []
        self.url = "about:blank"
        self.closed = False

    async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_count += 1
        if self.fail_with_timeout:
            raise TimeoutError(
                "Page.goto: Timeout 45000ms exceeded. waiting until \"commit\""
            )
        if self.url_sequence:
            self.url = self.url_sequence.pop(0)
        else:
            self.url = url

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)

    def is_closed(self) -> bool:
        return False

    async def close(self) -> None:
        self.closed = True


class _ExpandableRoomRatePage:
    def __init__(self) -> None:
        self.clicked_texts: list[str] = []
        self.waited_selectors: list[str] = []
        self.waits: list[int] = []

    def locator(self, selector: str) -> "_ExpandableRoomRateLocator":
        return _ExpandableRoomRateLocator(self, selector)

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


class _ExpandableRoomRateLocator:
    def __init__(self, page: _ExpandableRoomRatePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> "_ExpandableRoomRateLocator":
        return self

    async def wait_for(self, *, timeout: int, state: str) -> None:
        self._page.waited_selectors.append(self._selector)

    async def count(self) -> int:
        return 1 if "展示其余" in self._selector else 0

    def nth(self, index: int) -> "_ExpandableRoomRateLocator":
        return self

    async def inner_text(self, *, timeout: int) -> str:
        return "展示其余5个房型"

    async def click(self, *, timeout: int) -> None:
        self._page.clicked_texts.append("展示其余5个房型")


class _DelayedExpandableRoomRatePage(_ExpandableRoomRatePage):
    def locator(self, selector: str) -> "_DelayedExpandableRoomRateLocator":
        return _DelayedExpandableRoomRateLocator(self, selector)


class _DelayedExpandableRoomRateLocator(_ExpandableRoomRateLocator):
    async def count(self) -> int:
        if "展示其余" not in self._selector:
            return 0
        return 1 if len(self._page.waits) >= 2 else 0

    async def inner_text(self, *, timeout: int) -> str:
        return "展示其余2个房型"

    async def click(self, *, timeout: int) -> None:
        self._page.clicked_texts.append("展示其余2个房型")


class _JitteredScrollableRoomRatePage(_ExpandableRoomRatePage):
    def __init__(self) -> None:
        super().__init__()
        self.evaluate_args: list[tuple[object, ...]] = []

    async def evaluate(self, script: str, *args: object) -> dict[str, object]:
        self.evaluate_args.append(args)
        return {"cardCount": len(self.evaluate_args), "moved": len(self.evaluate_args) < 2}


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def _noop_page(page: object) -> None:
    return None


async def _fake_title(page: object) -> str:
    return "本店"


async def _fake_visible_text(page: object) -> str:
    return "本店 高级大床房 无早 ¥416"


async def _fake_empty_text(page: object) -> str:
    return ""


async def _fake_room_rates(page: object) -> list[dict[str, Any]]:
    return [
        {
            "source": "room_rate_dom_display_price",
            "room_type": "高级大床房",
            "breakfast": "无早",
            "price": {"amount": 416, "currency": "CNY", "display": "¥416"},
        }
    ]


async def _read_rates_from_nested_room_card_html() -> list[dict[str, Any]]:
    rooms = [
        ("高级大床房（安睡床品+特色茶具）", "1张1.8米大床", 423, 461),
        ("商务大床房（安睡床品+特色茶具）", "1张1.8米大床", 441, 480),
        ("豪华大床房（高空城景+超大落地窗）", "1张1.8米大床", 469, 507),
        ("江景大床房（高空江景+超大落地窗）", "1张1.8米大床", 496, 536),
        ("江景双床房（高空江景+2张大床+超大落地窗）", "2张1.5米双人床", 515, 553),
    ]
    return await _read_rates_from_room_card_html(rooms)


async def _read_rates_from_lazy_room_card_html() -> list[dict[str, Any]]:
    rooms = [
        ("岚夕·雅致大床房丨记忆床垫+徕芬吹风", "1张1.8米大床", 269, 304),
        ("岚夕·丽致大床房丨MINI冰箱+记忆床垫", "1张1.8米大床", 297, 332),
        ("岚夕·丽致双床房丨MINI冰箱+记忆床垫", "2张1.2米单人床", 306, 341),
        ("云夕·城景大床房丨阔景视野+无线投屏", "1张1.8米大床", 394, 430),
        ("云夕·逸致标准房丨记忆床垫+无线投屏", "2张1.2米单人床", 409, 444),
        ("云夕·逸致商务大床房丨MINI冰箱+语音音响", "1张1.8米大床", 409, 444),
        ("水夕·行政大床房丨空气净化器+软榻沙发+MINI冰箱", "1张1.8米大床", 453, 490),
        ("云夕丨逸致家庭房", "1张大床 及 1张单人床", 551, 590),
        ("水夕·180度视界套房丨浴缸+空气净化器+徕芬吹风", "1张1.8米大床", 567, 604),
        ("海底小纵队丨亲子家庭房", "1张特大床 及 1张单人床", 640, 677),
    ]
    initial_html = _room_card_html(rooms[:5])
    lazy_html = _room_card_html(rooms[5:]).replace("\\", "\\\\").replace("`", "\\`")
    html = f"""
    <html>
      <head>
        <style>
          body {{ margin: 0; }}
          .commonRoomCard__root {{ display: block; width: 800px; min-height: 190px; margin: 8px; }}
          .commonRoomCard-title__text {{ display: block; }}
          .commonRoomCard-content__body {{ display: block; }}
          .saleRoom__list {{ display: block; }}
          .saleRoomItemBox__row {{ display: flex; width: 720px; min-height: 48px; }}
          .saleRoomItemBox-reservationNotesInfoBox__nested,
          .saleRoomItemBox-priceInfoBox__nested {{ display: block; }}
          .displayPrice__amount,
          .deletePrice__amount,
          .discount__amount {{ display: inline-block; }}
        </style>
      </head>
      <body>
        <div class="mainRoomList__root">
          {initial_html}
        </div>
        <div style="height: 900px;"></div>
        <script>
          let appended = false;
          window.addEventListener('scroll', () => {{
            if (appended || window.scrollY < 100) {{
              return;
            }}
            appended = true;
            document.querySelector('.mainRoomList__root').insertAdjacentHTML(
              'beforeend',
              `{lazy_html}`
            );
          }});
        </script>
      </body>
    </html>
    """
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.set_content(html)
            await _ensure_public_room_rates_loaded(page)
            return await _safe_public_room_rates(page)
        finally:
            await browser.close()


async def _read_rates_from_virtualized_room_card_html() -> list[dict[str, Any]]:
    rooms = [
        ("岚夕·雅致大床房丨记忆床垫+徕芬吹风", "1张1.8米大床", 269, 304),
        ("岚夕·丽致大床房丨MINI冰箱+记忆床垫", "1张1.8米大床", 297, 332),
        ("岚夕·丽致双床房丨MINI冰箱+记忆床垫", "2张1.2米单人床", 306, 341),
        ("云夕·城景大床房丨阔景视野+无线投屏", "1张1.8米大床", 394, 430),
        ("云夕·逸致标准房丨记忆床垫+无线投屏", "2张1.2米单人床", 409, 444),
        ("云夕·逸致商务大床房丨MINI冰箱+语音音响", "1张1.8米大床", 409, 444),
        ("水夕·行政大床房丨空气净化器+软榻沙发+MINI冰箱", "1张1.8米大床", 453, 490),
        ("云夕丨逸致家庭房", "1张大床 及 1张单人床", 551, 590),
        ("水夕·180度视界套房丨浴缸+空气净化器+徕芬吹风", "1张1.8米大床", 567, 604),
        ("海底小纵队丨亲子家庭房", "1张特大床 及 1张单人床", 640, 677),
    ]
    chunks = [
        _room_card_html(rooms[:5]),
        _room_card_html(rooms[5:]),
    ]
    escaped_chunks = [chunk.replace("\\", "\\\\").replace("`", "\\`") for chunk in chunks]
    html = f"""
    <html>
      <head>
        <style>
          body {{ margin: 0; min-height: 2400px; }}
          .commonRoomCard__root {{ display: block; width: 800px; min-height: 190px; margin: 8px; }}
          .commonRoomCard-title__text {{ display: block; }}
          .commonRoomCard-content__body {{ display: block; }}
          .saleRoom__list {{ display: block; }}
          .saleRoomItemBox__row {{ display: flex; width: 720px; min-height: 48px; }}
          .saleRoomItemBox-reservationNotesInfoBox__nested,
          .saleRoomItemBox-priceInfoBox__nested {{ display: block; }}
          .displayPrice__amount,
          .deletePrice__amount,
          .discount__amount {{ display: inline-block; }}
        </style>
      </head>
      <body>
        <div class="mainRoomList__root">{escaped_chunks[0]}</div>
        <div style="height: 1800px;"></div>
        <script>
          const chunks = [`{escaped_chunks[0]}`, `{escaped_chunks[1]}`];
          let currentChunk = 0;
          const render = (index) => {{
            if (index === currentChunk) {{
              return;
            }}
            currentChunk = index;
            document.querySelector('.mainRoomList__root').innerHTML = chunks[index];
          }};
          window.addEventListener('scroll', () => {{
            if (window.scrollY > 900) {{
              render(1);
            }} else {{
              render(0);
            }}
          }});
        </script>
      </body>
    </html>
    """
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.set_content(html)
            loaded_rates = await _ensure_public_room_rates_loaded(page)
            return loaded_rates or await _safe_public_room_rates(page)
        finally:
            await browser.close()


async def _read_rates_from_room_card_html(
    rooms: list[tuple[str, str, int, int]],
) -> list[dict[str, Any]]:
    html = """
    <html>
      <head>
        <style>
          .commonRoomCard__root { display: block; width: 800px; margin: 8px; }
          .commonRoomCard-title__text { display: block; }
          .commonRoomCard-content__body { display: block; }
          .saleRoom__list { display: block; }
          .saleRoomItemBox__row { display: flex; width: 720px; min-height: 48px; }
          .saleRoomItemBox-reservationNotesInfoBox__nested,
          .saleRoomItemBox-priceInfoBox__nested { display: block; }
          .displayPrice__amount,
          .deletePrice__amount,
          .discount__amount { display: inline-block; }
        </style>
      </head>
      <body>
        <div class="mainRoomList__root">
          __ROOMS__
        </div>
      </body>
    </html>
    """
    room_html = _room_card_html(rooms)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.set_content(html.replace("__ROOMS__", room_html))
            return await _safe_public_room_rates(page)
        finally:
            await browser.close()


def _room_card_html(rooms: list[tuple[str, str, int, int]]) -> str:
    return "\n".join(
        f"""
        <div class="commonRoomCard__root">
          <span class="commonRoomCard-title__text">{room_name}</span>
          <div class="commonRoomCard-content__body">
            <div class="baseRoom__summary">{bed_line}</div>
            <div class="saleRoom__list">
              <div class="saleRoomItemBox-title__header">
                <span class="saleRoomItemBox-title_notesTitle_text__nested">房型摘要</span>
                <span class="saleRoomItemBox-title_priceTitle__nested">今日价格</span>
              </div>
              <div class="saleRoomItemBox__row">
                <div class="saleRoomItemBox-reservationNotesInfoBox__nested">无早餐</div>
                <div class="saleRoomItemBox-priceInfoBox__nested">
                  <span class="displayPrice__amount">¥{no_breakfast}</span>
                </div>
              </div>
              <div class="saleRoomItemBox__row">
                <div class="saleRoomItemBox-reservationNotesInfoBox__nested">1份早餐</div>
                <div class="saleRoomItemBox-priceInfoBox__nested">
                  <span class="discount__amount">优惠29</span>
                  <span class="deletePrice__amount">¥{with_breakfast + 29}</span>
                  <span class="displayPrice__amount">¥{with_breakfast}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        """
        for room_name, bed_line, no_breakfast, with_breakfast in rooms
    )


def _fake_public_snapshot(**kwargs: Any) -> dict[str, Any]:
    return {
        "role": kwargs["role"],
        "hotel_name": kwargs["label"],
        "status": "success",
        "page_state": "authenticated",
        "price": {"amount": 416, "display": "¥416"},
        "room_rates": kwargs.get("structured_room_rates") or [],
    }


def _fake_public_snapshot_from_page_state(**kwargs: Any) -> dict[str, Any]:
    if kwargs["page_state"] == "login_required":
        return {
            "role": kwargs["role"],
            "hotel_name": "鐧诲綍棣栭〉",
            "status": "needs_review",
            "page_state": "login_required",
            "current_url": kwargs["current_url"],
            "room_rates": [],
        }
    return _fake_public_snapshot(**kwargs)


if __name__ == "__main__":
    unittest.main()
