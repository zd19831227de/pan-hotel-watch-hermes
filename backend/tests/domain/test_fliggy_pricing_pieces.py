from __future__ import annotations

import unittest

from app.domain.browser import fliggy_pricing_page_reader as reader
from app.domain.browser import fliggy_pricing_page_writer as writer
from app.domain.browser import fliggy_pricing_executor as executor


class _FakeBodyLocator:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    async def inner_text(self, **_: object) -> str:
        return self._page.body_text


class _FakeInputLocator:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    @property
    def first(self) -> "_FakeInputLocator":
        return self

    async def wait_for(self, **_: object) -> None:
        return None

    async def click(self, **_: object) -> None:
        self._page.clicks += 1

    async def input_value(self, **_: object) -> str:
        return self._page.input_value_result


class _FakeKeyboard:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    async def press(self, key: str) -> None:
        self._page.presses.append(key)

    async def type(self, text: str, **_: object) -> None:
        self._page.typed = text


class _FakePage:
    url = "https://ebooking.fliggy.com/price"

    def __init__(self, body_text: str = "房价 房型 日历 库存") -> None:
        self.body_text = body_text
        self.clicks = 0
        self.presses: list[str] = []
        self.typed = ""
        self.input_value_result = ""
        self.keyboard = _FakeKeyboard(self)

    def locator(self, selector: str) -> object:
        if selector == "body":
            return _FakeBodyLocator(self)
        return _FakeInputLocator(self)

    async def screenshot(self, **_: object) -> None:
        return None

    async def wait_for_timeout(self, timeout: int) -> None:
        return None


class _FakeRequest:
    def __init__(self, post_data: str) -> None:
        self.method = "POST"
        self.post_data = post_data


class _FakeResponse:
    def __init__(self, url: str, post_data: str, status: int = 200) -> None:
        self.url = url
        self.status = status
        self.request = _FakeRequest(post_data)


PRICING_ACTIONS = [
    {
        "room_type": "高级大床房",
        "checkin_date": "2026-08-21",
        "proposed_base_price_yuan": "420.00",
    }
]


class FliggyPricingReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_classify_fliggy_pricing_page_state(self) -> None:
        self.assertEqual(
            reader.classify_fliggy_pricing_page_state(
                "https://ebooking.fliggy.com/price", "房价 房型 日历"
            ),
            "pricing_page",
        )
        self.assertEqual(
            reader.classify_fliggy_pricing_page_state(
                "https://passport.fliggy.com/login", "登录 密码"
            ),
            "login_required",
        )
        self.assertEqual(
            reader.classify_fliggy_pricing_page_state(
                "https://ebooking.fliggy.com/price", "滑动验证 拼图"
            ),
            "verification_required",
        )
        self.assertEqual(
            reader.classify_fliggy_pricing_page_state(
                "https://ebooking.fliggy.com/home", "工作台"
            ),
            "authenticated_non_pricing_page",
        )

    async def test_read_page_requires_login(self) -> None:
        page = _FakePage(body_text="登录 账号 密码")
        page.url = "https://passport.fliggy.com/login"
        result = await reader.read_fliggy_pricing_write_page(
            page, execute_request={"actions": PRICING_ACTIONS}
        )
        self.assertEqual(result["status"], "human_required")
        self.assertEqual(result["targets"], [])

    async def test_read_page_locates_targets_from_visible_text(self) -> None:
        page = _FakePage(body_text="房价房态 房型 日历 库存 高级大床房 420")
        result = await reader.read_fliggy_pricing_write_page(
            page, execute_request={"actions": PRICING_ACTIONS}
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["targets"]), 1)
        self.assertEqual(result["targets"][0]["room_type"], "高级大床房")
        self.assertEqual(result["targets"][0]["price_text_expected"], "420.00")


class FliggyPricingWriterTests(unittest.TestCase):
    def test_dry_run_preview_blocks_all_writes(self) -> None:
        page_read = {
            "status": "success",
            "targets": [
                {
                    "status": "located",
                    "operation": "set_room_price",
                    "platform": "fliggy",
                    "room_type": "高级大床房",
                    "checkin_date": "2026-08-21",
                    "current_price_yuan": 450,
                    "proposed_base_price_yuan": "420.00",
                    "input_candidates": [
                        {"selector_hint": "input.row-price"}
                    ],
                    "matching_lines": ["高级大床房 420"],
                }
            ],
        }
        preview = writer.build_fliggy_pricing_page_write_preview(
            execute_request={"request_id": "r1"},
            page_read=page_read,
        )
        self.assertEqual(preview["status"], "page_write_preview_ready")
        target = preview["targets"][0]
        self.assertEqual(target["actions_blocked"], {"fill": True, "click_save": True, "submit": True})
        evidence = preview["evidence"]
        self.assertFalse(evidence["browser_write_invoked"])
        self.assertFalse(evidence["ota_submit_invoked"])

    def test_dry_run_preview_fails_when_target_missing(self) -> None:
        page_read = {
            "status": "success",
            "targets": [
                {
                    "status": "not_found",
                    "room_type": "高级大床房",
                    "checkin_date": "2026-08-21",
                }
            ],
        }
        preview = writer.build_fliggy_pricing_page_write_preview(
            execute_request={"request_id": "r1"},
            page_read=page_read,
        )
        self.assertEqual(preview["status"], "page_structure_changed")


class FliggyPricingExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fill_price_success(self) -> None:
        page = _FakePage()
        page.input_value_result = "420"
        result = await executor.fill_fliggy_room_price(
            page,
            target={
                "room_type": "高级大床房",
                "checkin_date": "2026-08-21",
                "selector_hint": "input.row-price",
            },
            base_price="420",
        )
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["filled_value"], "420")

    async def test_fill_price_mismatch(self) -> None:
        page = _FakePage()
        page.input_value_result = "999"
        result = await executor.fill_fliggy_room_price(
            page,
            target={
                "room_type": "高级大床房",
                "checkin_date": "2026-08-21",
                "selector_hint": "input.row-price",
            },
            base_price="420",
        )
        self.assertEqual(result["status"], "mismatch")

    async def test_fill_price_not_located(self) -> None:
        page = _FakePage()
        result = await executor.fill_fliggy_room_price(
            page,
            target={
                "room_type": "高级大床房",
                "checkin_date": "2026-08-21",
                "selector_hint": "",
            },
            base_price="420",
        )
        self.assertEqual(result["status"], "not_located")

    def test_fliggy_save_response_matches_target_payload(self) -> None:
        response = _FakeResponse(
            "https://ebooking.fliggy.com/api/rate/save",
            '{"roomName":"高级大床房","date":"2026-08-21","price":420}',
        )
        self.assertTrue(executor._is_price_submit_response(response, PRICING_ACTIONS))

    def test_non_fliggy_host_response_is_not_confirmation(self) -> None:
        response = _FakeResponse(
            "https://example.com/api/rate/save",
            '{"roomName":"高级大床房","date":"2026-08-21","price":420}',
        )
        self.assertFalse(executor._is_price_submit_response(response, PRICING_ACTIONS))

    def test_wrong_price_payload_is_not_confirmation(self) -> None:
        response = _FakeResponse(
            "https://ebooking.fliggy.com/api/rate/save",
            '{"roomName":"其他房型","date":"2026-08-21","price":999}',
        )
        self.assertFalse(executor._is_price_submit_response(response, PRICING_ACTIONS))


if __name__ == "__main__":
    unittest.main()
