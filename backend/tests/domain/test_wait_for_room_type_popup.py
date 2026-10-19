from __future__ import annotations

import asyncio
import importlib
import unittest


class _FakeLocator:
    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        return 0

    async def is_visible(self) -> bool:
        return False


class _FakePopupPage:
    """按 evaluate 调用次序返回 detect_popup 结果，模拟弹窗异步渲染。"""

    def __init__(self, *, results: list[dict]) -> None:
        self._results = list(results)
        self._index = 0
        self.wait_count = 0

    def locator(self, selector: str, *args, **kwargs):
        return _FakeLocator()

    async def evaluate(self, script: str, arg=None):
        if "detect_popup.js" in script:
            result = self._results[min(self._index, len(self._results) - 1)]
            self._index += 1
            return result
        return {}

    async def wait_for_timeout(self, ms: float) -> None:
        self.wait_count += 1
        await asyncio.sleep(0)


def _selector_module():
    return importlib.import_module("app.domain.browser.ctrip_room_type_selector")


class WaitForRoomTypePopupTests(unittest.TestCase):
    """2026-09-14：房型弹窗主内容（搜索框/房型列表）异步渲染，固定 1500ms
    后单次 detect 会漏掉未渲染完的搜索框，误报「No search input」。改为
    有界等待信号收敛，超时才判结构变化。"""

    def _wait(self, page) -> dict:
        selector = _selector_module()

        async def scenario() -> dict:
            return await selector.wait_for_room_type_popup(page, timeout_s=5.0, poll_s=0.01)

        return asyncio.run(scenario())

    def test_search_input_appears_after_slow_render(self) -> None:
        page = _FakePopupPage(
            results=[
                {"search_inputs": [], "checkboxes": [], "confirm_buttons": [], "room_items": []},
                {"search_inputs": [{"selector_hint": "#s"}], "checkboxes": [], "confirm_buttons": [], "room_items": []},
            ]
        )
        popup = self._wait(page)
        self.assertEqual(popup["search_inputs"], [{"selector_hint": "#s"}])
        self.assertGreaterEqual(page.wait_count, 1, "未渲染完时应轮询等待")

    def test_checkboxes_signal_also_settles(self) -> None:
        page = _FakePopupPage(
            results=[
                {"search_inputs": [], "checkboxes": [], "confirm_buttons": [], "room_items": []},
                {"search_inputs": [], "checkboxes": [{"selector_hint": "#cb"}], "confirm_buttons": [], "room_items": []},
            ]
        )
        popup = self._wait(page)
        self.assertEqual(popup["checkboxes"], [{"selector_hint": "#cb"}])

    def test_timeout_returns_last_empty_result(self) -> None:
        page = _FakePopupPage(
            results=[{"search_inputs": [], "checkboxes": [], "confirm_buttons": [], "room_items": []}]
        )
        popup = asyncio.run(
            _selector_module().wait_for_room_type_popup(page, timeout_s=0.05, poll_s=0.01)
        )
        self.assertEqual(popup["search_inputs"], [])


if __name__ == "__main__":
    unittest.main()
