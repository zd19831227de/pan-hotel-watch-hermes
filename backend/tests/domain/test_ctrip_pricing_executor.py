from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.domain.browser import ctrip_pricing_executor as executor


class _FakeBodyLocator:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    async def inner_text(self, **_: object) -> str:
        return self._page.body_text


class _FakeButtonLocator:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    @property
    def first(self) -> "_FakeButtonLocator":
        return self

    async def click(self, **_: object) -> None:
        self._page.clicked = True


class _FakePage:
    url = "https://ebooking.ctrip.com/price"

    def __init__(self, body_text: str = "房价房态 房型 日历") -> None:
        self.body_text = body_text
        self.clicked = False
        self.waits: list[int] = []

    def locator(self, selector: str) -> _FakeBodyLocator | _FakeButtonLocator:
        if selector == "body":
            return _FakeBodyLocator(self)
        return _FakeButtonLocator(self)

    async def screenshot(self, **_: object) -> None:
        return None

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


class _FakeRequest:
    def __init__(self, post_data: str) -> None:
        self.method = "POST"
        self.post_data = post_data


class _FakeResponse:
    def __init__(self, url: str, post_data: str, status: int = 200) -> None:
        self.url = url
        self.status = status
        self.request = _FakeRequest(post_data)


class CtripPricingExecutorTests(unittest.IsolatedAsyncioTestCase):
    def _targets(self) -> list[dict[str, object]]:
        return [
            {
                "room_type": "高级大床房",
                "checkin_date": "2026-08-11",
                "proposed_base_price_yuan": "420.00",
            },
            {
                "room_type": "高级双床房",
                "checkin_date": "2026-08-12",
                "proposed_base_price_yuan": "430.00",
            },
        ]

    def test_unrelated_room_calendar_response_is_not_submit_confirmation(self) -> None:
        response = _FakeResponse(
            "https://ebooking.ctrip.com/api/room/rate/calendar",
            '{"operation":"refresh","room":"all"}',
        )

        self.assertFalse(
            executor._is_price_submit_response(response, self._targets())
        )

    def test_save_response_without_target_payload_is_not_submit_confirmation(self) -> None:
        response = _FakeResponse(
            "https://ebooking.ctrip.com/api/rate/save",
            '{"operation":"save","room":"其他房型","price":999}',
        )

        self.assertFalse(
            executor._is_price_submit_response(response, self._targets())
        )

    def test_ctrip_save_response_with_target_payload_is_submit_confirmation(self) -> None:
        response = _FakeResponse(
            "https://ebooking.ctrip.com/api/rate/save",
            '{"roomName":"高级大床房","date":"2026-08-11","price":420}',
        )

        self.assertTrue(
            executor._is_price_submit_response(response, self._targets())
        )

    async def test_submit_requires_each_target_to_read_back_to_target_price(self) -> None:
        page = _FakePage()
        readbacks = AsyncMock(
            side_effect=[
                {"status": "located", "current_base_price_yuan": 420.0},
                {"status": "located", "current_base_price_yuan": 429.0},
            ]
        )
        with (
            patch.object(
                executor,
                "_locate_price_modal_submit_button",
                new=AsyncMock(return_value={"selector": "#save", "button_text": "保存"}),
            ),
            patch.object(executor, "_read_success_toast", new=AsyncMock(return_value="保存成功")),
            patch.object(executor, "_safe_current_rate_readback", new=readbacks),
        ):
            result = await executor.submit_ctrip_price_changes(page, targets=self._targets())

        self.assertEqual(result["status"], "needs_review")
        self.assertIn("高级双床房", result["message"])
        self.assertEqual(readbacks.await_count, 2)

    async def test_submit_returns_executed_only_for_explicit_success_and_matching_readbacks(self) -> None:
        page = _FakePage("房价房态 房型 日历 其他说明 success")
        readbacks = AsyncMock(
            side_effect=[
                {"status": "located", "current_base_price_yuan": "420.00"},
                {"status": "located", "current_base_price_yuan": 430},
            ]
        )
        with (
            patch.object(
                executor,
                "_locate_price_modal_submit_button",
                new=AsyncMock(return_value={"selector": "#save", "button_text": "保存"}),
            ),
            patch.object(executor, "_read_success_toast", new=AsyncMock(return_value="保存成功")),
            patch.object(executor, "_safe_current_rate_readback", new=readbacks),
        ):
            result = await executor.submit_ctrip_price_changes(page, targets=self._targets())

        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["readback_count"], 2)

    async def test_body_wide_success_text_without_toast_is_not_success(self) -> None:
        page = _FakePage("房价房态 房型 日历 success")
        with patch.object(
            executor,
            "_locate_price_modal_submit_button",
            new=AsyncMock(return_value={"selector": "#save", "button_text": "保存"}),
        ), patch.object(executor, "_read_success_toast", new=AsyncMock(return_value="")):
            result = await executor.submit_ctrip_price_changes(page, targets=self._targets())

        self.assertEqual(result["status"], "needs_review")

    async def test_submit_preserves_human_required_for_login_or_risk_control(self) -> None:
        page = _FakePage("登录后才能保存")
        with patch.object(
            executor,
            "_locate_price_modal_submit_button",
            new=AsyncMock(return_value={"selector": "#save", "button_text": "保存"}),
        ):
            result = await executor.submit_ctrip_price_changes(page, targets=self._targets())

        self.assertEqual(result["status"], "human_required")
        self.assertEqual(page.clicked, False)


if __name__ == "__main__":
    unittest.main()
