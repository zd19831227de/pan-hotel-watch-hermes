from __future__ import annotations

import asyncio
import importlib
import unittest


class _ConfirmFakeLocator:
    """模拟 playwright 1.49 的 Locator：filter() 没有 visible 参数。

    2026-09-14 实测根因：click_room_selection_confirm 用了
    ``.filter(visible=True)``，在 playwright 1.49 上直接抛 TypeError 被
    ``except Exception`` 吞掉，四个候选文案全部静默失败，execute 阶段
    永远报「Could not confirm room selection」（execution 记录 8/11/14）。

    DOM 语义（live CDP 实测 2026-09-14）：
    - ``#j-chooseRatePlan-submit``：房型选择弹窗确认按钮，可见可点。
    - ``[class*='dialog'] button:has-text('确认')``：页面别处存在一个
      隐藏的「确认」按钮，不带 :visible 时会被误选中（count=1 但不可见）。
    """

    def __init__(self, selector: str, page: "_ConfirmFakePage") -> None:
        self._selector = selector
        self._page = page

    @property
    def first(self) -> "_ConfirmFakeLocator":
        return self

    def filter(self, *args, **kwargs):
        if "visible" in kwargs:
            raise TypeError(
                "Locator.filter() got an unexpected keyword argument 'visible'"
            )
        return self

    def _matches(self) -> bool:
        selector = self._selector
        visible_only = ":visible" in selector
        if "#j-chooseRatePlan-submit" in selector:
            return True  # 真实弹窗确认按钮，始终可见
        if "has-text('确认')" in selector or "has-text('确定')" in selector:
            # 隐藏的伪装按钮：不带 :visible 的选择器会匹配到它。
            return not visible_only
        return False

    async def count(self) -> int:
        return 1 if self._matches() else 0

    async def is_visible(self) -> bool:
        if not self._matches():
            return False
        return ":visible" not in self._selector or "#j-chooseRatePlan-submit" in self._selector

    async def click(self, *args, **kwargs):
        if not await self.is_visible():
            raise AssertionError(f"clicked invisible element: {self._selector}")
        self._page.clicked_selectors.append(self._selector)


class _ConfirmFakePage:
    def __init__(self) -> None:
        self.clicked_selectors: list[str] = []
        self.requested_selectors: list[str] = []

    def locator(self, selector: str, *args, **kwargs):
        self.requested_selectors.append(selector)
        return _ConfirmFakeLocator(selector, self)

    async def wait_for_timeout(self, ms: float) -> None:
        await asyncio.sleep(0)


def _selector_module():
    return importlib.import_module("app.domain.browser.ctrip_room_type_selector")


class ClickRoomSelectionConfirmTests(unittest.TestCase):
    def _click(self, page) -> bool:
        selector = _selector_module()
        return asyncio.run(selector.click_room_selection_confirm(page))

    def test_clicks_visible_confirm_button(self):
        """弹窗确认按钮存在且可见时必须点中（现网回归：filter(visible) TypeError）。"""

        page = _ConfirmFakePage()
        ok = self._click(page)
        self.assertTrue(ok)
        self.assertTrue(
            any("#j-chooseRatePlan-submit" in s for s in page.clicked_selectors),
            f"应点击 j-chooseRatePlan-submit，实际: {page.clicked_selectors}",
        )

    def test_never_clicks_invisible_decoy_button(self):
        """隐藏的「确认」按钮（不带 :visible 的文本选择器命中）不允许被点。"""

        class _NoIdPage(_ConfirmFakePage):
            def locator(self, selector: str, *args, **kwargs):
                # id 按钮不存在：只有伪装的隐藏「确认」按钮。
                if "#j-chooseRatePlan-submit" in selector:
                    return _ConfirmFakeLocator(selector, self)
                return super().locator(selector, *args, **kwargs)

        class _NoIdLocator(_ConfirmFakeLocator):
            def _matches(self) -> bool:
                if "#j-chooseRatePlan-submit" in self._selector:
                    return False
                return super()._matches()

        page = _NoIdPage()
        page.locator = lambda selector, *a, **k: _NoIdLocator(selector, page)  # type: ignore[method-assign]
        ok = self._click(page)
        self.assertFalse(ok)
        self.assertEqual(page.clicked_selectors, [])

    def test_selector_list_uses_visible_pseudo(self):
        """生成选择器不允许再依赖 .filter(visible=...)（1.49 无此参数）。"""

        page = _ConfirmFakePage()
        self._click(page)
        self.assertTrue(page.requested_selectors)
        for selector in page.requested_selectors:
            self.assertNotIn("has-text('确认')", selector.replace(":visible", ""))


if __name__ == "__main__":
    unittest.main()
