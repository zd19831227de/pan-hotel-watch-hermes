from __future__ import annotations

import asyncio
import unittest
from typing import Any

from app.domain.browser.pricing_page_product_selection import (
    PlaywrightSelectionActions,
    SelectionCounter,
    run_product_selection,
)
from app.domain.browser.pricing_product_selection import ProductRow

FLIGGY_ROWS = [
    ProductRow(
        row_text="豪华套房（全屋声控+智能马桶+小冰箱+洗烘一体机） 含5个",
        is_parent=True,
        selector_hint="#row-parent",
    ),
    ProductRow(row_text="双早-限时退", is_parent=False, selector_hint="#row-c1"),
    ProductRow(row_text="单早-限时退【F1面纱专享】 子价格", is_parent=False, selector_hint="#row-c2"),
    ProductRow(row_text="无早-任意退【F2面纱专享】 主价格", is_parent=False, selector_hint="#row-c3"),
]


class FakeActions:
    def __init__(
        self,
        rows: list[ProductRow],
        *,
        counter_after_clear: int = 0,
        counter_after_check: int | None = None,
        clear_ok: bool = True,
        check_ok: bool = True,
        date_ok: bool = True,
        counter_missing: bool = False,
    ) -> None:
        self.rows = rows
        self.counter_after_clear = counter_after_clear
        self.counter_after_check = (
            counter_after_check if counter_after_check is not None else counter_after_clear
        )
        self.clear_ok = clear_ok
        self.check_ok = check_ok
        self.date_ok = date_ok
        self.counter_missing = counter_missing
        self.checked: list[str] = []
        self.clear_clicked = False
        self.dates_set: tuple[str, str] | None = None

    async def collect_rows(self) -> list[ProductRow]:
        return self.rows

    async def read_counter(self) -> SelectionCounter | None:
        if self.counter_missing:
            return None
        selected = len(self.checked) if self.checked else self.counter_after_clear
        if self.checked:
            selected = self.counter_after_check
        return SelectionCounter(selected=selected, total=44, raw=f"已选{selected}个/共44个")

    async def clear_selection(self) -> bool:
        self.clear_clicked = True
        self.checked = []
        return self.clear_ok

    async def check_row(self, row: ProductRow) -> bool:
        if not self.check_ok:
            return False
        self.checked.append(row.row_text)
        return True

    async def set_date_range(self, start: str, end: str) -> bool:
        if not self.date_ok:
            return False
        self.dates_set = (start, end)
        return True


class ProductSelectionFlowTests(unittest.TestCase):
    """B5.3 — 产品选定状态机：成功路径与失败即停。"""

    def _run(self, actions: FakeActions, **overrides: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "room_type": "豪华套房",
            "breakfast_kind": "无早",
            "checkin_date": "2026-08-25",
            "checkout_date": "2026-08-25",
        }
        kwargs.update(overrides)
        return asyncio.run(run_product_selection(actions, **kwargs))

    def test_success_flow_clears_then_checks_target_and_sets_dates(self) -> None:
        actions = FakeActions(
            FLIGGY_ROWS, counter_after_clear=0, counter_after_check=1
        )
        result = self._run(actions)
        self.assertEqual(result["status"], "success")
        self.assertTrue(actions.clear_clicked)
        self.assertEqual(len(actions.checked), 1)
        self.assertIn("无早-任意退", actions.checked[0])
        self.assertEqual(actions.dates_set, ("2026-08-25", "2026-08-25"))
        self.assertFalse(result["evidence"]["price_fill_invoked"])
        self.assertFalse(result["evidence"]["submit_invoked"])
        kinds = [step["kind"] for step in result["steps"]]
        self.assertIn("clear_selection", kinds)
        self.assertIn("verify_cleared", kinds)
        self.assertIn("verify_selected", kinds)
        self.assertIn("set_dates", kinds)

    def test_empty_rows_fail_fast(self) -> None:
        result = self._run(FakeActions([]))
        self.assertEqual(result["status"], "page_structure_changed")
        self.assertIn("产品树为空", result["summary"])

    def test_unmapped_room_returns_needs_review_with_candidates(self) -> None:
        result = self._run(FakeActions(FLIGGY_ROWS), room_type="不存在套房")
        self.assertEqual(result["status"], "page_structure_changed")

    def test_unified_none_rule_matches_without_breakfast(self) -> None:
        """2026-09-14 统一无早口径：无早餐粒度也直接锁定主价格子产品走完选定。"""
        result2 = self._run(
            FakeActions(FLIGGY_ROWS, counter_after_check=1), breakfast_kind=None
        )
        self.assertEqual(result2["status"], "success")
        self.assertTrue(result2.get("matched_rows"))
        self.assertIn("主价格", result2["matched_rows"][0])

    def test_fliggy_parent_row_match_needs_review(self) -> None:
        """飞猪母房型勾选会联动全部子产品（含双早），违反无早口径：
        只读到父行时必须 needs_review，不得勾父行。美团父行≈母产品可放行。"""
        rows = [
            ProductRow(
                row_text="豪华套房（全屋声控+智能马桶） 含5个",
                is_parent=True,
                selector_hint="#row-parent",
            )
        ]
        fliggy = self._run(FakeActions(rows), platform="fliggy")
        self.assertEqual(fliggy["status"], "needs_review")
        self.assertIn("联动", fliggy["summary"])
        meituan = self._run(
            FakeActions(rows, counter_after_check=1), platform="meituan"
        )
        self.assertEqual(meituan["status"], "success")

    def test_clear_failure_stops_before_checking(self) -> None:
        actions = FakeActions(FLIGGY_ROWS, clear_ok=False)
        result = self._run(actions)
        self.assertEqual(result["status"], "page_structure_changed")
        self.assertEqual(actions.checked, [])
        self.assertEqual(result["evidence"]["stopped_at"], "clear_selection")

    def test_counter_not_zero_after_clear_stops(self) -> None:
        actions = FakeActions(FLIGGY_ROWS, counter_after_clear=3, counter_after_check=1)
        result = self._run(actions)
        self.assertEqual(result["status"], "page_structure_changed")
        self.assertIn("不为 0", result["summary"])
        self.assertEqual(actions.checked, [])

    def test_counter_missing_after_clear_stops(self) -> None:
        actions = FakeActions(
            FLIGGY_ROWS, counter_after_clear=0, counter_missing=True
        )
        result = self._run(actions)
        self.assertEqual(result["status"], "page_structure_changed")

    def test_check_failure_stops_without_dates(self) -> None:
        actions = FakeActions(
            FLIGGY_ROWS, counter_after_clear=0, counter_after_check=1, check_ok=False
        )
        result = self._run(actions)
        self.assertEqual(result["status"], "page_structure_changed")
        self.assertIsNone(actions.dates_set)

    def test_linkage_overflow_stops(self) -> None:
        """勾选后计数超过目标数（母房型联动波及其它产品）必须失败即停。"""
        actions = FakeActions(
            FLIGGY_ROWS, counter_after_clear=0, counter_after_check=5
        )
        result = self._run(actions)
        self.assertEqual(result["status"], "page_structure_changed")
        self.assertIn("联动", result["summary"])

    def test_date_failure_stops_after_selection(self) -> None:
        actions = FakeActions(
            FLIGGY_ROWS,
            counter_after_clear=0,
            counter_after_check=1,
            date_ok=False,
        )
        result = self._run(actions)
        self.assertEqual(result["status"], "page_structure_changed")
        self.assertIn("日期区间", result["summary"])


class PlaywrightActionsUnitTests(unittest.TestCase):
    def test_counter_parsing_fliggy_and_meituan(self) -> None:
        actions = PlaywrightSelectionActions(object(), platform="fliggy")

        class FakeBody:
            def __init__(self, text: str) -> None:
                self._text = text

            async def inner_text(self, timeout: int = 0) -> str:
                return self._text

        class FakeLocator:
            def __init__(self, text: str) -> None:
                self._body = FakeBody(text)

            async def inner_text(self, timeout: int = 0) -> str:
                return await self._body.inner_text(timeout)

            def __getattr__(self, name: str):
                raise AssertionError(f"unexpected locator call: {name}")

        class FakePage:
            def __init__(self, text: str) -> None:
                self.locator = lambda selector: FakeLocator(text)

        page = FakePage("全部房型产品 (已选3个/共44个) 取消 反选")
        counter = asyncio.run(actions.read_counter())
        self.assertIsNone(counter)  # object() page has no locator -> None

        actions2 = PlaywrightSelectionActions(FakePage("已选3个/共44个"), platform="fliggy")
        counter2 = asyncio.run(actions2.read_counter())
        self.assertIsNotNone(counter2)
        self.assertEqual(counter2.selected, 3)
        self.assertEqual(counter2.total, 44)

        actions3 = PlaywrightSelectionActions(FakePage("已选2个产品"), platform="meituan")
        counter3 = asyncio.run(actions3.read_counter())
        self.assertIsNotNone(counter3)
        self.assertEqual(counter3.selected, 2)
        self.assertIsNone(counter3.total)


if __name__ == "__main__":
    unittest.main()
