"""lazy_page_collector 单测：虚拟滚动/折叠页面的逐步滚动采集与合并。

实测背景（2026-09-14 CDP 只读探查）：
- 携程房价日历房型表在 .calendar-wrap 内部容器虚拟滚动，任意时刻 DOM
  只有视口附近约 20 行价格块，不滚动只能读到第一屏。
- 美团批量改价页房型行默认可能折叠；飞猪产品树默认折叠需「全部展开」。
"""

from __future__ import annotations

import unittest

from app.domain.browser.lazy_page_collector import (
    reset_page_scroll,
    scroll_page_and_collect,
)


class FakeVirtualizedPage:
    """按滚动步揭示窗口的假页面：evaluate 只响应滚动/归位脚本标记。"""

    def __init__(self, windows: list[list[dict]]) -> None:
        self.windows = windows
        self.position = 0
        self.scroll_calls = 0
        self.reset_calls = 0
        self.timeouts: list[int] = []

    async def evaluate(self, script: str, arg: object | None = None) -> object:
        if "lazy_page_collector:scroll_step" in script:
            self.scroll_calls += 1
            if self.position >= len(self.windows) - 1:
                return {"moved": False, "atBottom": True, "containers": 1}
            self.position += 1
            return {"moved": True, "atBottom": False, "containers": 1}
        if "lazy_page_collector:reset_scroll" in script:
            self.reset_calls += 1
            self.position = 0
            return True
        raise AssertionError(f"unexpected evaluate script: {script[:80]}")

    async def wait_for_timeout(self, ms: float) -> None:
        self.timeouts.append(int(ms))


def _row_key(row: dict) -> tuple[str, bool]:
    return (str(row.get("row_text") or "")[:160], bool(row.get("is_parent")))


class ScrollPageAndCollectTests(unittest.IsolatedAsyncioTestCase):
    async def test_merges_overlapping_virtualized_windows(self) -> None:
        page = FakeVirtualizedPage(
            [
                [{"row_text": "豪华套房 卖价1100", "is_parent": True}],
                [
                    {"row_text": "豪华套房 卖价1100", "is_parent": True},
                    {"row_text": "行政套房 卖价980", "is_parent": True},
                ],
                [
                    {"row_text": "行政套房 卖价980", "is_parent": True},
                    {"row_text": "奢华三卧套房 卖价1500", "is_parent": True},
                ],
            ]
        )

        async def collect_fn() -> list[dict]:
            return page.windows[page.position]

        pool, debug = await scroll_page_and_collect(
            page, collect_fn, dedupe_key=_row_key
        )
        self.assertEqual(
            [row["row_text"] for row in pool],
            ["豪华套房 卖价1100", "行政套房 卖价980", "奢华三卧套房 卖价1500"],
        )
        self.assertEqual(debug["collected"], 3)
        self.assertGreaterEqual(debug["scroll_rounds"], 2)

    async def test_stops_after_one_round_when_nothing_scrollable(self) -> None:
        page = FakeVirtualizedPage([[{"row_text": "仅一屏", "is_parent": True}]])

        async def collect_fn() -> list[dict]:
            return page.windows[page.position]

        pool, debug = await scroll_page_and_collect(
            page, collect_fn, dedupe_key=_row_key
        )
        self.assertEqual(len(pool), 1)
        self.assertEqual(debug["scroll_rounds"], 1)
        self.assertEqual(page.scroll_calls, 1)

    async def test_respects_max_rounds_bound(self) -> None:
        page = FakeVirtualizedPage(
            [[{"row_text": f"房型{index}", "is_parent": True}] for index in range(50)]
        )

        async def collect_fn() -> list[dict]:
            return page.windows[page.position]

        pool, debug = await scroll_page_and_collect(
            page, collect_fn, dedupe_key=_row_key, max_rounds=4
        )
        self.assertEqual(debug["scroll_rounds"], 4)
        self.assertEqual(len(pool), 5)

    async def test_collect_fn_exception_stops_with_partial_pool(self) -> None:
        page = FakeVirtualizedPage(
            [
                [{"row_text": "A", "is_parent": True}],
                [{"row_text": "B", "is_parent": True}],
            ]
        )
        calls = {"n": 0}

        async def collect_fn() -> list[dict]:
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("evaluate failed")
            return page.windows[0]

        pool, _debug = await scroll_page_and_collect(
            page, collect_fn, dedupe_key=_row_key
        )
        self.assertEqual([row["row_text"] for row in pool], ["A"])


class ResetPageScrollTests(unittest.IsolatedAsyncioTestCase):
    async def test_resets_scroll_position(self) -> None:
        page = FakeVirtualizedPage([[{"row_text": "A", "is_parent": True}]])
        page.position = 3
        await reset_page_scroll(page)
        self.assertEqual(page.reset_calls, 1)
        self.assertEqual(page.position, 0)

    async def test_reset_swallows_evaluate_errors(self) -> None:
        class BrokenPage(FakeVirtualizedPage):
            async def evaluate(self, script: str, arg: object | None = None) -> object:
                raise RuntimeError("page gone")

        await reset_page_scroll(BrokenPage([]))


if __name__ == "__main__":
    unittest.main()
