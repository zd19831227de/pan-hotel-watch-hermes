from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.domain.browser import ctrip_pricing_executor as executor


class _FillFakeLocator:
    """记录输入动作的假价格输入框（模拟 JPrice_CalculateSell）。"""

    def __init__(self, page: "_FillFakePage") -> None:
        self._page = page

    @property
    def first(self) -> "_FillFakeLocator":
        return self

    async def wait_for(self, **_: object) -> None:
        return None

    async def click(self, **_: object) -> None:
        self._page.clicks += 1

    async def input_value(self) -> str:
        return self._page.typed[-1] if self._page.typed else ""


class _FillFakePage:
    url = "https://ebooking.ctrip.com/ebkovsroom/inventory/calendar"

    def __init__(self) -> None:
        self.typed: list[str] = []
        self.clicks = 0

    def locator(self, selector: str) -> _FillFakeLocator:
        return _FillFakeLocator(self)

    @property
    def keyboard(self):
        return self

    async def press(self, *_args, **_kwargs) -> None:
        return None

    async def type(self, text: str, **_kwargs) -> None:
        self.typed.append(text)

    async def wait_for_timeout(self, *_args, **_kwargs) -> None:
        return None


_SELL_INPUT_CANDIDATES = [
    {
        "index": 0,
        "selector_hint": "ul.pricetplul > li > div.pop-set > div.rule-number-box.pop-price-input > input.JPrice_CalculateSell",
        "tag": "input",
        "type": "text",
        "aria_label": "",
        "placeholder": "",
        "value": "",
        "nearby_text": "卖价(元)",
        "match_score": 2,
    }
]
_BASE_INPUT_CANDIDATES = [
    {
        "index": 0,
        "selector_hint": "#baseInput",
        "tag": "input",
        "type": "text",
        "aria_label": "",
        "placeholder": "底价",
        "value": "",
        "nearby_text": "底价(元)",
        "match_score": 2,
    }
]


class FillPriceSemanticsTests(unittest.IsolatedAsyncioTestCase):
    """2026-09-15 实测（修改价格弹窗 live CDP）：价格模式=卖价+服务费，
    唯一可编辑价格输入框是「卖价(元)」（JPrice_CalculateSell），底价由平台
    按卖价×(1-12%) 派生（原卖价1012->原底价890.56 实证）。

    旧逻辑无条件把 proposed_base（=目标价/1.12，与本页底价口径也不一致）
    填进卖价框——目标1013会被写成公开卖价904.46，属写错价。修复后：
    卖价/售价标注的输入框填店长目标卖价；底价标注的输入框才填底价。
    """

    async def test_sell_labelled_input_receives_target_sell_price(self) -> None:
        page = _FillFakePage()
        with patch.object(
            executor,
            "find_price_input_candidates",
            new=AsyncMock(return_value=list(_SELL_INPUT_CANDIDATES)),
        ):
            result = await executor.fill_ctrip_room_price(
                page,
                target={
                    "room_type": "奢华三卧套房",
                    "checkin_date": "2026-09-16",
                    "proposed_price_yuan": 1013,
                    "proposed_base_price_yuan": "904.46",
                },
                base_price="904.46",
            )
        self.assertEqual(result["status"], "filled")
        self.assertEqual(page.typed, ["1013"])

    async def test_base_labelled_input_receives_base_price(self) -> None:
        page = _FillFakePage()
        with patch.object(
            executor,
            "find_price_input_candidates",
            new=AsyncMock(return_value=list(_BASE_INPUT_CANDIDATES)),
        ):
            result = await executor.fill_ctrip_room_price(
                page,
                target={
                    "room_type": "奢华三卧套房",
                    "checkin_date": "2026-09-16",
                    "proposed_price_yuan": 1013,
                    "proposed_base_price_yuan": "904.46",
                },
                base_price="904.46",
            )
        self.assertEqual(result["status"], "filled")
        self.assertEqual(page.typed, ["904.46"])

    async def test_unlabelled_input_falls_back_to_sell_price(self) -> None:
        page = _FillFakePage()
        candidates = [dict(_SELL_INPUT_CANDIDATES[0], nearby_text="", selector_hint="#plain")]
        with patch.object(
            executor,
            "find_price_input_candidates",
            new=AsyncMock(return_value=candidates),
        ):
            result = await executor.fill_ctrip_room_price(
                page,
                target={
                    "room_type": "奢华三卧套房",
                    "checkin_date": "2026-09-16",
                    "proposed_price_yuan": 1013,
                    "proposed_base_price_yuan": "904.46",
                },
                base_price="904.46",
            )
        self.assertEqual(result["status"], "filled")
        self.assertEqual(page.typed, ["1013"])

    async def test_submit_verifies_sell_price_against_calendar_sell(self) -> None:
        """提交后校验对日历「卖价」列（店长目标价），而非底价派生口径。"""

        class _Page:
            url = "https://ebooking.ctrip.com/price"
            body_text = "房价房态 房型 日历"

            def locator(self, _selector: str):
                class _L:
                    async def inner_text(self, **_kw: object) -> str:
                        return _Page.body_text

                    @property
                    def first(self):
                        return self

                    async def click(self, **_kw: object) -> None:
                        return None

                return _L()

            async def screenshot(self, **_kw: object) -> None:
                return None

            async def wait_for_timeout(self, _t: int) -> None:
                return None

        readbacks = AsyncMock(
            return_value={
                "status": "located",
                "current_price_yuan": 1013.0,
                "current_base_price_yuan": 891.44,
            }
        )
        targets = [
            {
                "room_type": "奢华三卧套房",
                "checkin_date": "2026-09-16",
                "proposed_price_yuan": 1013,
                "proposed_base_price_yuan": "904.46",
            }
        ]
        with (
            patch.object(
                executor,
                "_locate_price_modal_submit_button",
                new=AsyncMock(return_value={"selector": "#save", "button_text": "保存"}),
            ),
            patch.object(executor, "_read_success_toast", new=AsyncMock(return_value="保存成功")),
            patch.object(executor, "_safe_current_rate_readback", new=readbacks),
        ):
            result = await executor.submit_ctrip_price_changes(page=_Page(), targets=targets)

        self.assertEqual(result["status"], "executed")


if __name__ == "__main__":
    unittest.main()
