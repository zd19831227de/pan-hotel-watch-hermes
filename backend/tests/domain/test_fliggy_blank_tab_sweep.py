from __future__ import annotations

import asyncio
import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock


def _fliggy_module():
    return importlib.import_module("app.domain.browser.fliggy_ebooking")


class _FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False
        self.close = AsyncMock(side_effect=self._close)

    def _close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


def _fake_playwright_with(pages: list[_FakePage]):
    context = MagicMock()
    context.pages = list(pages)
    context.new_page = AsyncMock(side_effect=lambda: _FakePage("about:blank"))
    browser = MagicMock()
    browser.contexts = [context]
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    return playwright


class FliggyBlankTabSweepTests(unittest.TestCase):
    """2026-09-14：冷启动参数 --restore-last-session + about:blank 会在恢复
    会话标签之外多开一个空白标签；连接复用飞猪页时空白标签永远留下，
    每次打开浏览器都多一个「空白网页」。连接后应清扫空白兄弟标签。"""

    def _connect(self, pages: list[_FakePage]):
        fliggy = _fliggy_module()
        service = fliggy.FliggyEbookingBrowserService()
        playwright = _fake_playwright_with(pages)

        async def run():
            return await service._connect_existing_debug_page(
                playwright=playwright,
                platform_account_id="fliggy_api",
                browser_session_id="browser-fliggy",
                port=9531,
            )

        return asyncio.run(run())

    def test_blank_sibling_closed_and_fliggy_page_kept(self):
        blank = _FakePage("about:blank")
        order = _FakePage(
            "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/order/listV2"
        )
        page = self._connect([blank, order])
        self.assertIs(page, order)
        self.assertTrue(blank.closed, "冷启动残留的 about:blank 标签应被关闭")
        self.assertFalse(order.closed)

    def test_newtab_sibling_closed(self):
        order = _FakePage("https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/x")
        newtab = _FakePage("chrome://new-tab-page/")
        page = self._connect([order, newtab])
        self.assertIs(page, order)
        self.assertTrue(newtab.closed)
        self.assertFalse(order.closed)

    def test_sole_blank_page_is_selected_not_closed(self):
        blank = _FakePage("about:blank")
        page = self._connect([blank])
        self.assertIs(page, blank)
        self.assertFalse(blank.closed, "唯一的空白标签是后续 goto 的载体，不能关")

    def test_non_blank_siblings_never_closed(self):
        order = _FakePage("https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/a")
        other = _FakePage("https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/b")
        page = self._connect([order, other])
        self.assertIs(page, order)
        self.assertFalse(order.closed)
        self.assertFalse(other.closed, "非空白标签绝不允许被清扫")


if __name__ == "__main__":
    unittest.main()
