from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.domain.browser import ctrip_pricing_executor as executor


class _ConfirmLocator:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    @property
    def first(self) -> "_ConfirmLocator":
        return self

    async def count(self) -> int:
        return 1 if self._visible else 0

    async def is_visible(self) -> bool:
        return self._visible


class _ModalPage:
    """带 #JPrice_Confirm 的假页面（2026-09-15 live 实测弹窗结构）。"""

    def __init__(self, confirm_visible: bool = True) -> None:
        self.confirm_visible = confirm_visible
        self.requested: list[str] = []

    def locator(self, selector: str) -> _ConfirmLocator:
        self.requested.append(selector)
        if "#JPrice_Confirm" in selector:
            return _ConfirmLocator(self.confirm_visible)
        raise AssertionError(f"unexpected selector: {selector}")

    async def evaluate(self, *_args, **_kwargs):
        return None


class PriceModalSubmitButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_jprice_confirm_fast_path(self) -> None:
        page = _ModalPage(confirm_visible=True)
        info = await executor._locate_price_modal_submit_button(page)
        self.assertEqual(info["selector"], "#JPrice_Confirm:visible")
        self.assertEqual(info["button_text"], "确定")

    async def test_falls_back_to_generic_js_when_id_absent(self) -> None:
        page = _ModalPage(confirm_visible=False)
        with patch.object(
            executor,
            "load_js",
            return_value="() => null",
        ):
            # evaluate 返回 None -> locate 失败（通用兜底也没找到）
            info = await executor._locate_price_modal_submit_button(page)
        self.assertIsNone(info)

    def test_submit_labels_include_determine_word(self) -> None:
        """词表必须含「确定」——修改价格弹窗按钮文案是确定不是确认。"""

        self.assertIn("\u786e\u5b9a", executor._SUBMIT_BUTTON_LABELS)


# 2026-09-15 live CDP 实抓 batchsetroomprice 保存请求体（脱长 roomName）。
_REAL_SAVE_PAYLOAD = (
    '{"roomPriceInfoList":[{"roomTypeID":1741110266,'
    '"roomName":"奢华三卧套房(超大复式+独立三卧室+客歺厅+书房+厨卫)<乐享旅程>'
    '<无早><复式三房豪华套房>(1741110266) 直连-量态 预付","hotelID":114727377,'
    '"payType":"PP","currency":"RMB","mealNum":0,"costPrice":891.44,"salePrice":1013,'
    '"commissionRate":0.12}],"dateRangeInfo":[{"startDate":"2026-09-16",'
    '"endDate":"2026-09-16"}],"priceChangeMode":2}'
)


class RealSavePayloadMatchTests(unittest.TestCase):
    """保存确认必须认出真实保存 API 的 payload：卖价+房型名/日期。

    2026-09-15 实测：旧匹配只找 proposed_base（卖价/1.12 旧口径），而真实
    payload 里是 salePrice=目标卖价、costPrice=平台派生底价——价格匹配
    必须以 proposed_price_yuan 为主。
    """

    def _target(self) -> dict:
        return {
            "room_type": "奢华三卧套房",
            "checkin_date": "2026-09-16",
            "proposed_price_yuan": 1013,
            "proposed_base_price_yuan": "904.46",
        }

    def test_real_payload_matches_by_sell_price(self) -> None:
        self.assertTrue(
            executor._payload_matches_price_target(_REAL_SAVE_PAYLOAD, self._target())
        )

    def test_wrong_price_does_not_match(self) -> None:
        target = self._target()
        target["proposed_price_yuan"] = 999
        target["proposed_base_price_yuan"] = "888.39"
        self.assertFalse(
            executor._payload_matches_price_target(_REAL_SAVE_PAYLOAD, target)
        )

    def test_base_only_target_still_matches_via_cost_price(self) -> None:
        """旧 target 只有底价字段时，payload 的平台派生底价可作匹配。"""

        target = {
            "room_type": "奢华三卧套房",
            "checkin_date": "2026-09-16",
            "proposed_price_yuan": None,
            "proposed_base_price_yuan": "891.44",
        }
        self.assertTrue(
            executor._payload_matches_price_target(_REAL_SAVE_PAYLOAD, target)
        )

    def test_is_price_submit_response_accepts_real_save_response(self) -> None:
        class _Req:
            method = "POST"
            post_data = _REAL_SAVE_PAYLOAD

        class _Resp:
            url = "https://ebooking.ctrip.com/ebkovsroom/api/inventory/batchsetroomprice"
            status = 200
            request = _Req()

        self.assertTrue(executor._is_price_submit_response(_Resp(), [self._target()]))


class TwoStageSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_save_clicker_invoked_once_after_grace(self) -> None:
        """弹窗确定后若无保存信号，第 8 轮起兜底点一次页面级保存。"""

        class _Page:
            def __init__(self) -> None:
                self.calls = 0

            def locator(self, _selector: str):
                class _L:
                    async def inner_text(self, **_kw: object) -> str:
                        return "房价房态 日历"

                return _L()

            async def wait_for_timeout(self, _t: int) -> None:
                return None

        page = _Page()
        saver = AsyncMock(side_effect=lambda _p: True)
        with (
            patch.object(executor, "_read_body_text", new=AsyncMock(return_value="日历")),
            patch.object(executor, "_read_success_toast", new=AsyncMock(return_value=None)),
        ):
            result = await executor._wait_for_submit_confirmation(
                page, responses=[], targets=[], page_save_clicker=saver
            )

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(saver.await_count, 1, "兜底保存只允许点一次")

    async def test_save_clicker_not_invoked_when_confirmation_arrives(self) -> None:
        saver = AsyncMock(side_effect=lambda _p: True)

        class _Page:
            def locator(self, _selector: str):
                class _L:
                    async def inner_text(self, **_kw: object) -> str:
                        return "日历"

                return _L()

            async def wait_for_timeout(self, _t: int) -> None:
                return None

        with (
            patch.object(executor, "_read_body_text", new=AsyncMock(return_value="日历")),
            patch.object(executor, "_read_success_toast", new=AsyncMock(return_value="保存成功")),
        ):
            result = await executor._wait_for_submit_confirmation(
                page=_Page(), responses=[], targets=[], page_save_clicker=saver
            )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(saver.await_count, 0)

    async def test_page_level_save_click_wraps_evaluate(self) -> None:
        class _Page:
            async def evaluate(self, *_args, **_kwargs) -> bool:
                return True

        self.assertTrue(await executor._click_page_level_save_button(_Page()))

        class _FailPage:
            async def evaluate(self, *_args, **_kwargs):
                raise RuntimeError("evaluate failed")

        self.assertFalse(await executor._click_page_level_save_button(_FailPage()))


if __name__ == "__main__":
    unittest.main()
