from __future__ import annotations

import asyncio
import importlib
import unittest


class _FakePage:
    """按调用次序返回 canned evaluate 结果，模拟弹窗筛选切换+房型匹配。"""

    def __init__(self, *, evaluate_results: list[dict]) -> None:
        self._results = list(evaluate_results)
        self._index = 0
        self.wait_calls: list[int] = []

    async def evaluate(self, script: str, arg=None):
        # popup_container 脚本返回容器选择器；check_matching_rooms 按序返回策略。
        if "popup_container.js" in script:
            return "#room-popup"
        if "check_matching_rooms.js" in script:
            result = self._results[min(self._index, len(self._results) - 1)]
            self._index += 1
            return result
        return {}

    async def wait_for_timeout(self, ms: float) -> None:
        self.wait_calls.append(int(ms))
        await asyncio.sleep(0)


def _selector_module():
    return importlib.import_module("app.domain.browser.ctrip_room_type_selector")


class SelectMatchingRoomResultsTests(unittest.TestCase):
    """2026-09-14：弹窗默认量态「非直连」会过滤掉房型，改为切到「全部房型」
    （或退而取消「非直连」）后再匹配；筛选切换需等待重渲染再重试。"""

    def _select(self, page) -> bool:
        selector = _selector_module()

        async def scenario() -> bool:
            return await selector.select_matching_room_results(
                page, "#search", "奢华三卧套房"
            )

        return asyncio.run(scenario())

    def test_all_room_types_filter_then_match(self) -> None:
        page = _FakePage(
            evaluate_results=[
                {"checked": True, "strategy": "all_room_types_filter_clicked", "count": 0},
                {"checked": True, "strategy": "exact_match", "count": 1},
            ]
        )
        self.assertTrue(self._select(page))
        # 首轮筛选切换等 2000ms 重渲染，次轮匹配后再等 800ms 收尾。
        self.assertEqual(page.wait_calls, [2000, 800])

    def test_non_direct_filter_cleared_then_match(self) -> None:
        page = _FakePage(
            evaluate_results=[
                {"checked": True, "strategy": "non_direct_filter_cleared", "count": 0},
                {"checked": True, "strategy": "exact_match", "count": 1},
            ]
        )
        self.assertTrue(self._select(page))
        self.assertEqual(page.wait_calls, [2000, 800])

    def test_all_rooms_already_active_matches_directly(self) -> None:
        page = _FakePage(
            evaluate_results=[
                {"checked": True, "strategy": "exact_match", "count": 1},
            ]
        )
        self.assertTrue(self._select(page))
        self.assertEqual(page.wait_calls, [800])

    def test_no_matching_checkbox_returns_false(self) -> None:
        page = _FakePage(
            evaluate_results=[
                {"checked": False, "strategy": "no_matching_checkbox", "count": 0},
            ]
        )
        self.assertFalse(self._select(page))


if __name__ == "__main__":
    unittest.main()
