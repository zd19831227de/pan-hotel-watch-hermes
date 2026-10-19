from __future__ import annotations

import unittest

from app.domain.browser.pricing_room_rates import (
    ctrip_room_rates_from_candidates,
    extract_room_label,
    parse_room_rate_text,
    product_tree_rates_from_rows,
    room_rate_table_rows,
)


class RoomRateParsingTests(unittest.TestCase):
    def test_extract_room_label_cuts_price_noise(self) -> None:
        self.assertEqual(
            extract_room_label("奢华三卧套房 卖价1100 底价970 已开房 不限量"),
            "奢华三卧套房",
        )

    def test_extract_room_label_strips_parent_suffix(self) -> None:
        self.assertEqual(extract_room_label("江景大床房 含2个"), "江景大床房")

    def test_parse_labelled_prices(self) -> None:
        parsed = parse_room_rate_text("卖价 1100 底价 970 已开房")
        self.assertEqual(parsed["sell_price_yuan"], 1100)
        self.assertEqual(parsed["base_price_yuan"], 970)
        self.assertEqual(parsed["status_text"], "已开房")

    def test_parse_money_fallback(self) -> None:
        parsed = parse_room_rate_text("双早-任意退 520 458")
        self.assertEqual(parsed["sell_price_yuan"], 520)
        self.assertEqual(parsed["base_price_yuan"], 458)

    def test_parse_without_prices_keeps_none(self) -> None:
        parsed = parse_room_rate_text("双早-限时退")
        self.assertIsNone(parsed["sell_price_yuan"])
        self.assertIsNone(parsed["base_price_yuan"])

    def test_ctrip_candidates_grouped_by_room(self) -> None:
        candidates = [
            {
                "room_text": "奢华三卧套房<乐享旅程><无早>(1741110268) 直连 预付",
                "text": "卖价1100",
                "nearby_text": "底价970 已开房",
                "score": 30,
            },
            {
                "room_text": "高级大床房<乐享旅程><无早>(1741110287) 直连 预付",
                "text": "卖价520 底价458",
                "nearby_text": "",
                "score": 20,
            },
        ]
        rows = ctrip_room_rates_from_candidates(candidates)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["room_label"], "奢华三卧套房")
        self.assertEqual(rows[0]["sell_price_yuan"], 1100)
        self.assertEqual(rows[0]["base_price_yuan"], 970)
        self.assertEqual(rows[1]["room_label"], "高级大床房")

    def test_large_room_count_not_truncated_at_60(self) -> None:
        """2026-09-14 实测：多房型门店（单列 60 个价格块候选）曾被
        _MAX_ROOM_RATES=60 截断，卡片丢失下半段房型；上限放宽到 120。
        房型名用字母组合，避免数字被噪声正则当价格截断。"""
        letters = "abcdefghijklmnopqrstuvwxyz"
        candidates = [
            {
                "room_text": f"测试房型{letters[index // 26]}{letters[index % 26]} 卖价{300 + index} 底价{200 + index}",
                "text": f"卖价{300 + index}",
                "nearby_text": f"底价{200 + index} 已开房",
                "score": 20,
            }
            for index in range(80)
        ]
        rows = ctrip_room_rates_from_candidates(candidates)
        self.assertEqual(len(rows), 80)
        self.assertEqual(rows[0]["room_label"], "测试房型aa")
        self.assertEqual(rows[-1]["room_label"], "测试房型db")

    def test_product_tree_rows_keep_parent_child_structure(self) -> None:
        """房型×无早口径：父行提供房型名，子行的无早价格进同一行。"""

        rows = product_tree_rates_from_rows(
            [
                {"row_text": "江景大床房 含2个", "is_parent": True},
                {"row_text": "无早-任意退 现价520 成本价458 主价格", "is_parent": False},
                {"row_text": "双早-限时退 现价600", "is_parent": False},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["room_label"], "江景大床房")
        self.assertEqual(rows[0]["sell_price_yuan"], 520)
        self.assertEqual(rows[0]["base_price_yuan"], 458)

    def test_product_tree_rows_accept_product_row_dataclass(self) -> None:
        """collect_rows 返回 ProductRow dataclass；解析层只认 dict 会把
        美团/飞猪查房价整链读成 not_found（2026-09-14 实测复现）。"""

        from app.domain.browser.pricing_product_selection import ProductRow

        rows = product_tree_rates_from_rows(
            [
                ProductRow(row_text="江景大床房 含2个", is_parent=True),
                ProductRow(row_text="无早-任意退 现价520 主价格", is_parent=False),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["room_label"], "江景大床房")
        self.assertEqual(rows[0]["sell_price_yuan"], 520)

    def test_table_rows_render_child_indent_and_missing_prices(self) -> None:
        rows = room_rate_table_rows(
            [
                {
                    "room_label": "江景大床房",
                    "is_parent": True,
                    "sell_price_yuan": None,
                    "base_price_yuan": None,
                    "status_text": None,
                },
                {
                    "room_label": "双早-任意退",
                    "is_parent": False,
                    "sell_price_yuan": 520,
                    "base_price_yuan": 458,
                    "status_text": None,
                },
            ]
        )
        self.assertEqual(rows[0]["parts"][0], "页面未展示")
        self.assertTrue(rows[1]["field"].startswith("　└"))
        self.assertEqual(rows[1]["parts"][0], "¥520")


class _FakeVirtualizedPage:
    """按滚动步揭示窗口的假页面：evaluate 只响应滚动/归位/采集脚本标记。"""

    def __init__(self, windows: list[list[dict]]) -> None:
        self.windows = windows
        self.position = 0
        self.reset_calls = 0
        self.meituan_expand_calls = 0
        self.expand_all_clicks = 0

    async def evaluate(self, script: str, arg: object | None = None) -> object:
        if "lazy_page_collector:fliggy_row_count" in script:
            return 2
        if "lazy_page_collector:scroll_step" in script:
            if self.position >= len(self.windows) - 1:
                return {"moved": False, "atBottom": True, "containers": 1}
            self.position += 1
            return {"moved": True, "atBottom": False, "containers": 1}
        if "lazy_page_collector:reset_scroll" in script:
            self.reset_calls += 1
            self.position = 0
            return True
        if "lazy_page_collector:meituan_expand" in script:
            self.meituan_expand_calls += 1
            return 0
        if ".room-goods-item-room" in script or 'input[type="checkbox"]' in script:
            return self.windows[self.position]
        raise AssertionError(f"unexpected evaluate script: {script[:80]}")

    def get_by_text(self, text: str, exact: bool = True) -> "_FakeTextControl":
        return _FakeTextControl(self)

    async def wait_for_timeout(self, ms: float) -> None:
        return None


class _FakeTextControl:
    def __init__(self, page: "_FakeVirtualizedPage") -> None:
        self._page = page

    @property
    def first(self) -> "_FakeTextControl":
        return self

    async def count(self) -> int:
        return 1

    async def click(self, timeout: float = 0) -> None:
        self._page.expand_all_clicks += 1
        return None


class ReadCtripCalendarScrollTests(unittest.IsolatedAsyncioTestCase):
    """2026-09-14 实测：携程日历房型表在内部容器虚拟滚动，DOM 任意时刻
    只有视口附近窗口；读取必须逐步滚动合并窗口，否则丢房型。"""

    async def test_read_scrolls_and_merges_all_windows(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.domain.browser.pricing_room_rates import (
            read_ctrip_calendar_room_rates,
        )

        page = _FakeVirtualizedPage(
            [
                [
                    {
                        "room_text": "豪华套房 卖价1100 底价970 已开房",
                        "text": "卖价1100",
                        "nearby_text": "底价970 已开房",
                        "score": 30,
                    }
                ],
                [
                    {
                        "room_text": "豪华套房 卖价1100 底价970 已开房",
                        "text": "卖价1100",
                        "nearby_text": "底价970 已开房",
                        "score": 30,
                    },
                    {
                        "room_text": "奢华三卧套房 卖价1500 底价1300 已开房",
                        "text": "卖价1500",
                        "nearby_text": "底价1300 已开房",
                        "score": 30,
                    },
                ],
            ]
        )
        detect = AsyncMock(
            return_value={
                "status": "success",
                "candidates": None,  # 由 side_effect 动态给
            }
        )
        detect.side_effect = (
            lambda *args, **kwargs: {
                "status": "success",
                "candidates": list(page.windows[page.position]),
            }
        )

        with patch(
            "app.domain.browser.ctrip_room_type_selector.detect_price_block_candidates",
            detect,
        ):
            read = await read_ctrip_calendar_room_rates(page, checkin_date="2026-09-15")

        self.assertEqual(read["status"], "success")
        labels = [row["room_label"] for row in read["room_rates"]]
        self.assertIn("豪华套房", labels)
        self.assertIn("奢华三卧套房", labels)
        self.assertEqual(len(labels), 2)
        # 读完全量后把页面滚回顶部，避免把操作者的日历留在页尾。
        self.assertGreaterEqual(page.reset_calls, 1)


class ProductTreeCollectRowsTests(unittest.IsolatedAsyncioTestCase):
    """美团/飞猪 collect_rows 需先展开折叠行，再滚动合并虚拟窗口。"""

    def _meituan_windows(self) -> list[list[dict]]:
        return [
            [
                {"row_text": "豪华套房 卖价1100", "is_parent": True, "selector_hint": "a"},
                {"row_text": "行政套房 卖价980", "is_parent": True, "selector_hint": "b"},
            ],
            [
                {"row_text": "行政套房 卖价980", "is_parent": True, "selector_hint": "b"},
                {"row_text": "奢华三卧套房 卖价1500", "is_parent": True, "selector_hint": "c"},
            ],
        ]

    async def test_meituan_collect_expands_then_merges_windows(self) -> None:
        from app.domain.browser.pricing_page_product_selection import (
            PlaywrightSelectionActions,
        )

        page = _FakeVirtualizedPage(self._meituan_windows())
        rows = await PlaywrightSelectionActions(page, platform="meituan").collect_rows()

        self.assertEqual(
            [row.row_text for row in rows],
            ["豪华套房 卖价1100", "行政套房 卖价980", "奢华三卧套房 卖价1500"],
        )
        self.assertGreaterEqual(page.meituan_expand_calls, 1)
        self.assertGreaterEqual(page.reset_calls, 1)

    async def test_fliggy_collect_clicks_expand_all_and_merges(self) -> None:
        from app.domain.browser.pricing_page_product_selection import (
            PlaywrightSelectionActions,
        )

        page = _FakeVirtualizedPage(self._meituan_windows())
        rows = await PlaywrightSelectionActions(page, platform="fliggy").collect_rows()

        self.assertEqual(len(rows), 3)
        self.assertGreaterEqual(page.expand_all_clicks, 1)
        self.assertGreaterEqual(page.reset_calls, 1)

    async def test_fliggy_identical_child_texts_across_suites_not_deduped(self) -> None:
        """2026-09-14 实测：飞猪多个套房下的子行文案完全相同（「双早-限时退」
        等），按 (文本,是否父行) 去重会把 9 个房型的主价格行折叠成 1 条、
        匹配落到父行。去重键必须带 selector_hint。"""

        from app.domain.browser.pricing_page_product_selection import (
            PlaywrightSelectionActions,
        )

        page = _FakeVirtualizedPage(
            [
                [
                    {"row_text": "豪华套房（全屋声控） 含5个", "is_parent": True, "selector_hint": "#p1"},
                    {"row_text": "无早-任意退【F2面纱专享】 主价格", "is_parent": False, "selector_hint": "#c1"},
                    {"row_text": "双早-限时退", "is_parent": False, "selector_hint": "#c2"},
                    {"row_text": "精英套房（大复式） 含5个", "is_parent": True, "selector_hint": "#p2"},
                    {"row_text": "无早-任意退【F2面纱专享】 主价格", "is_parent": False, "selector_hint": "#c3"},
                    {"row_text": "双早-限时退", "is_parent": False, "selector_hint": "#c4"},
                ]
            ]
        )

        async def evaluate(script: str, arg: object | None = None) -> object:
            if "lazy_page_collector:fliggy_row_count" in script:
                return {"rows": 6, "parents": 2, "children": 4}
            if "lazy_page_collector:scroll_step" in script:
                return {"moved": False, "atBottom": True, "containers": 1}
            if 'input[type="checkbox"]' in script:
                return page.windows[0]
            raise AssertionError(f"unexpected evaluate script: {script[:80]}")

        page.evaluate = evaluate  # type: ignore[method-assign]
        page.get_by_text = lambda *a, **k: _FakeTextControl(page)  # type: ignore[method-assign]
        page.locator = lambda selector: _EmptyLocator()  # type: ignore[method-assign]

        rows = await PlaywrightSelectionActions(page, platform="fliggy").collect_rows()
        children = [row for row in rows if not row.is_parent]
        self.assertEqual(len(children), 4)
        main_price = [row for row in children if "主价格" in row.row_text]
        self.assertEqual(len(main_price), 2)

    async def test_empty_first_pass_retries_after_spa_delay(self) -> None:
        from app.domain.browser.pricing_page_product_selection import (
            PlaywrightSelectionActions,
        )

        windows = [
            [
                {
                    "row_text": "豪华套房 卖价1100",
                    "is_parent": True,
                    "selector_hint": "a",
                }
            ]
        ]
        page = _FakeVirtualizedPage(windows)
        # 第一整段采集（reset 之前）返回空，模拟 SPA 渲染慢；重试段返回行。
        original_evaluate = page.evaluate

        async def evaluate_with_empty_first_pass(
            script: str, arg: object | None = None
        ) -> object:
            if ".room-goods-item-room" in script and page.reset_calls == 0:
                return []
            return await original_evaluate(script, arg)

        page.evaluate = evaluate_with_empty_first_pass  # type: ignore[method-assign]

        rows = await PlaywrightSelectionActions(page, platform="meituan").collect_rows()
        self.assertEqual([row.row_text for row in rows], ["豪华套房 卖价1100"])


class CtripBreakfastNoneCardTests(unittest.TestCase):
    """2026-09-14 业务核对：卡片按房型×无早口径展示（单早/双早随无早联动）。"""

    def test_suite_keeps_only_breakfast_none_price(self) -> None:
        candidates = [
            {
                "room_text": "豪华套房(全屋智能)<乐享旅程><单早>(1494481) 直连 预付",
                "text": "卖价456",
                "nearby_text": "底价387.6 已开房",
                "score": 30,
            },
            {
                "room_text": "豪华套房(全屋智能)<乐享旅程><无早><高级大床房>(1741110287) 直连 预付",
                "text": "卖价432",
                "nearby_text": "底价380.16 已开房",
                "score": 30,
            },
            {
                "room_text": "豪华套房(全屋智能)<乐享旅程><双早>(1741110290) 直连 预付",
                "text": "卖价480",
                "nearby_text": "底价408 已开房",
                "score": 30,
            },
        ]
        rows = ctrip_room_rates_from_candidates(candidates)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["room_label"], "豪华套房(全屋智能)")
        self.assertEqual(rows[0]["sell_price_yuan"], 432)
        self.assertEqual(rows[0]["base_price_yuan"], 380.16)

    def test_long_suite_variants_do_not_collapse(self) -> None:
        """回归：房型名 >30 字时 <无早>/<单早> 变体曾被 30 字截断合并成一行；
        无早口径下同套房型的变体必须只保留无早一行，且不同长名套房不互相合并。"""

        long_a = "行政套房（全屋智能+独立会客厅+全套厨卫家电）"
        long_b = "总裁双卧套房（复式双层双卧+豪华会客厅+全套厨卫家电）"
        rows = ctrip_room_rates_from_candidates(
            [
                {
                    "room_text": f"{long_a}<乐享旅程><无早>(1741110272) 已开房",
                    "text": "卖价457",
                    "nearby_text": "底价402.16 已开房",
                    "score": 30,
                },
                {
                    "room_text": f"{long_a}<乐享旅程><单早>(1497890324) 已开房",
                    "text": "卖价480",
                    "nearby_text": "底价430 已开房",
                    "score": 30,
                },
                {
                    "room_text": f"{long_b}<乐享旅程><无早>(1741110245) 已开房",
                    "text": "卖价667",
                    "nearby_text": "底价586.96 已开房",
                    "score": 30,
                },
            ]
        )
        self.assertEqual(
            [(row["room_label"], row["sell_price_yuan"]) for row in rows],
            [(long_a, 457), (long_b, 667)],
        )

    def test_prefers_clean_breakfast_none_over_presale(self) -> None:
        suite = "悦聚套房(大复式+独卧+会客厅+岛台)"
        rows = ctrip_room_rates_from_candidates(
            [
                {
                    "room_text": f"{suite}<2人入住><无早><预售产品><产品ID:113775851> 已开房",
                    "text": "卖价270",
                    "nearby_text": "底价242.55 已开房",
                    "score": 30,
                },
                {
                    "room_text": f"{suite}<乐享旅程><无早>(1750655977) 已开房",
                    "text": "卖价513",
                    "nearby_text": "底价451.44 已开房",
                    "score": 30,
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sell_price_yuan"], 513)

    def test_falls_back_to_marked_breakfast_none_when_no_clean(self) -> None:
        suite = "行政套房（全屋智能+独立会客厅+全套厨卫家电）"
        rows = ctrip_room_rates_from_candidates(
            [
                {
                    "room_text": f"{suite}<无早>(钟点房)<08:00到18:00> 已开房",
                    "text": "卖价30",
                    "nearby_text": "底价118 已开房",
                    "score": 30,
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sell_price_yuan"], 30)

    def test_suite_without_breakfast_none_keeps_unpriced_row(self) -> None:
        rows = ctrip_room_rates_from_candidates(
            [
                {
                    "room_text": "女王套房(豪华会客厅)<乐享旅程><双早>(1741110268) 已开房",
                    "text": "卖价600",
                    "nearby_text": "底价520 已开房",
                    "score": 30,
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["room_label"], "女王套房(豪华会客厅)")
        self.assertIsNone(rows[0]["sell_price_yuan"])

    def test_available_canonical_preferred_within_same_tier(self) -> None:
        suite = "童趣双床套房(客厅+游乐帐篷)"
        rows = ctrip_room_rates_from_candidates(
            [
                {
                    "room_text": f"{suite}<乐享旅程><无早>(1540612280) 已关房",
                    "text": "卖价1064",
                    "nearby_text": "底价904.4 已关房",
                    "score": 30,
                },
                {
                    "room_text": f"{suite}<乐享旅程><无早>标准(1540612281) 已开房",
                    "text": "卖价1100",
                    "nearby_text": "底价940 已开房",
                    "score": 20,
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sell_price_yuan"], 1100)
        self.assertEqual(rows[0]["status_text"], "已开房")


class ProductTreeSuiteRowTests(unittest.TestCase):
    """产品树（美团/飞猪）卡片按房型×无早口径：母产品/主价格=无早价格。"""

    def test_meituan_suite_row_from_mother_product(self) -> None:
        rows = product_tree_rates_from_rows(
            [
                {"row_text": "悦聚套房", "is_parent": True},
                {
                    "row_text": "悦聚套房-不含早-入住当天18:00前免费取消 母产品",
                    "is_parent": False,
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["room_label"], "悦聚套房")

    def test_fliggy_suite_row_prefers_main_price_tag(self) -> None:
        rows = product_tree_rates_from_rows(
            [
                {"row_text": "豪华套房（全屋声控+智能马桶） 含5个", "is_parent": True},
                {"row_text": "双早-限时退 卖价600", "is_parent": False},
                {"row_text": "无早-任意退 卖价520 底价458 主价格", "is_parent": False},
                {"row_text": "单早-限时退 卖价560 子价格", "is_parent": False},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["room_label"], "豪华套房（全屋声控+智能马桶）")
        self.assertEqual(rows[0]["sell_price_yuan"], 520)
        self.assertEqual(rows[0]["base_price_yuan"], 458)

    def test_fliggy_untagged_breakfast_none_still_canonical(self) -> None:
        rows = product_tree_rates_from_rows(
            [
                {"row_text": "悦聚套房(大复式) 含4个", "is_parent": True},
                {"row_text": "无早-任意退 卖价513 底价451", "is_parent": False},
                {"row_text": "双早-限时退 卖价600", "is_parent": False},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sell_price_yuan"], 513)

    def test_unavailable_canonical_prefers_available_sibling(self) -> None:
        rows = product_tree_rates_from_rows(
            [
                {"row_text": "女王套房（豪华会客厅） 含5个", "is_parent": True},
                {"row_text": "无早-任意退 卖价568 满房 主价格", "is_parent": False},
                {"row_text": "无早-任意退标准 卖价600 主价格", "is_parent": False},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sell_price_yuan"], 600)

    def test_parent_only_suite_keeps_unpriced_row(self) -> None:
        rows = product_tree_rates_from_rows(
            [
                {"row_text": "奢华三卧套房（超大复式） 含4个", "is_parent": True},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["room_label"], "奢华三卧套房（超大复式）")
        self.assertIsNone(rows[0]["sell_price_yuan"])

    def test_suite_without_canonical_child_shows_unpriced_row(self) -> None:
        rows = product_tree_rates_from_rows(
            [
                {"row_text": "家庭双卧套房（独立卧室） 含3个", "is_parent": True},
                {"row_text": "双早-限时退 卖价630", "is_parent": False},
                {"row_text": "单早-任意退 卖价600", "is_parent": False},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["sell_price_yuan"])


class _EmptyLocator:
    async def count(self) -> int:
        return 0

    @property
    def first(self) -> "_EmptyLocator":
        return self

    async def click(self, timeout: float = 0) -> None:
        return None


class FliggyRobustExpandTests(unittest.IsolatedAsyncioTestCase):
    """飞猪「全部展开」异步生效（实测 ~2.5s）：展开后须轮询行数稳定，
    残余未展开父行逐个点箭头兜底，不能只等固定 1.2s。"""

    def _make_page(self, *, with_expand_button: bool, with_icons: bool):
        page = _FakeVirtualizedPage([[]])
        page._fliggy_expanded = False
        page._has_expand_button = with_expand_button
        page._icon_clicks_left = 2 if with_icons else 0

        async def evaluate(script: str, arg: object | None = None) -> object:
            if "lazy_page_collector:fliggy_row_count" in script:
                if page._fliggy_expanded:
                    return {"rows": 54, "parents": 9, "children": 44}
                return {"rows": 10, "parents": 9, "children": 0}
            if "lazy_page_collector:scroll_step" in script:
                return {"moved": False, "atBottom": True, "containers": 1}
            if 'input[type="checkbox"]' in script:
                rows = [
                    {"row_text": "豪华套房（全屋声控） 含5个", "is_parent": True},
                ]
                if page._fliggy_expanded:
                    rows.append(
                        {"row_text": "无早-任意退【F2面纱专享】 主价格", "is_parent": False}
                    )
                return rows
            raise AssertionError(f"unexpected evaluate script: {script[:80]}")

        page.evaluate = evaluate  # type: ignore[method-assign]

        def get_by_text(text: str, exact: bool = True) -> "_FakeTextControl":
            control = _FakeTextControl(page)

            async def count() -> int:
                return 1 if page._has_expand_button and not page._fliggy_expanded else 0

            control.count = count  # type: ignore[method-assign]
            return control

        page.get_by_text = get_by_text  # type: ignore[method-assign]

        class _IconLocator:
            async def count(self) -> int:
                return 1 if page._icon_clicks_left > 0 else 0

            @property
            def first(self) -> "_IconLocator":
                return self

            async def click(self, timeout: float = 0) -> None:
                page._icon_clicks_left -= 1
                page._fliggy_expanded = True

        def locator(selector: str):
            assert ".ant-table-row-expand-icon-collapse" in selector
            return _IconLocator()

        if with_icons:
            page.locator = locator  # type: ignore[method-assign]
            # 图标兜底路径：点箭头才展开。
            page._has_expand_button = False
        else:
            def locator_empty(selector: str):
                class _Empty:
                    async def count(self) -> int:
                        return 0

                    @property
                    def first(self) -> "_Empty":
                        return self

                    async def click(self, timeout: float = 0) -> None:
                        return None

                return _Empty()

            page.locator = locator_empty  # type: ignore[method-assign]
        return page

    async def test_expand_button_waits_for_stable_row_count(self) -> None:
        from app.domain.browser.pricing_page_product_selection import (
            PlaywrightSelectionActions,
        )

        page = self._make_page(with_expand_button=True, with_icons=False)
        original_click = _FakeTextControl.click

        async def click(self, timeout: float = 0) -> None:  # noqa: ANN001
            page._fliggy_expanded = True
            page.expand_all_clicks += 1

        _FakeTextControl.click = click  # type: ignore[method-assign]
        try:
            rows = await PlaywrightSelectionActions(page, platform="fliggy").collect_rows()
        finally:
            _FakeTextControl.click = original_click  # type: ignore[method-assign]
        self.assertEqual(len(rows), 2)
        self.assertIn("主价格", rows[1].row_text)

    async def test_per_row_expand_icon_fallback(self) -> None:
        from app.domain.browser.pricing_page_product_selection import (
            PlaywrightSelectionActions,
        )

        page = self._make_page(with_expand_button=False, with_icons=True)
        rows = await PlaywrightSelectionActions(page, platform="fliggy").collect_rows()
        self.assertEqual(len(rows), 2)
        self.assertIn("主价格", rows[1].row_text)


class _FakeRenderPage:
    """模拟慢渲染页：每次 wait 后 body 文本前进一步。"""

    def __init__(
        self,
        bodies: list[str],
        url: str = "https://me.meituan.com/ebooking/merchant/product/batch-price",
    ) -> None:
        self._bodies = list(bodies)
        self.url = url
        self.waits = 0

    async def wait_for_timeout(self, ms: float) -> None:
        self.waits += 1

    def locator(self, selector: str):  # noqa: ANN201
        parent = self

        class _BodyLocator:
            async def inner_text(self, timeout: float = 6000) -> str:
                index = min(parent.waits, len(parent._bodies) - 1)
                return parent._bodies[index]

        return _BodyLocator()


class FinalizeTreeReadTests(unittest.TestCase):
    """整树无价格/无房态 → 明确失败口径，不再伪成功（2026-09-23 修复）。"""

    def test_all_priceless_rows_downgrade_to_structure_changed(self) -> None:
        from app.domain.browser.pricing_room_rates import finalize_product_tree_read

        read = {
            "status": "success",
            "platform": "fliggy",
            "summary": "飞猪批量改价页读取到 9 个房型（无早口径）。",
            "room_rates": [
                {"room_label": "豪华套房", "sell_price_yuan": None, "base_price_yuan": None, "status_text": None},
                {"room_label": "精英套房", "sell_price_yuan": None, "base_price_yuan": None, "status_text": None},
            ],
        }
        result = finalize_product_tree_read(read)
        self.assertEqual(result["status"], "page_structure_changed")
        self.assertIn("未展示", result["summary"])
        # 降级文案必须带平台名，不能出现「未知批量改价页」。
        self.assertIn("飞猪", result["summary"])
        # 房型名单保留，卡片仍能展示缺口内容。
        self.assertEqual(len(result["room_rates"]), 2)

    def test_partial_prices_keep_success(self) -> None:
        from app.domain.browser.pricing_room_rates import finalize_product_tree_read

        read = {
            "status": "success",
            "summary": "ok",
            "room_rates": [
                {"room_label": "豪华套房", "sell_price_yuan": 432, "base_price_yuan": 380, "status_text": "已开房"},
                {"room_label": "精英套房", "sell_price_yuan": None, "base_price_yuan": None, "status_text": None},
            ],
        }
        self.assertEqual(finalize_product_tree_read(read)["status"], "success")

    def test_status_only_rows_keep_success(self) -> None:
        from app.domain.browser.pricing_room_rates import finalize_product_tree_read

        read = {
            "status": "success",
            "summary": "ok",
            "room_rates": [
                {"room_label": "豪华套房", "sell_price_yuan": None, "base_price_yuan": None, "status_text": "满房"},
            ],
        }
        self.assertEqual(finalize_product_tree_read(read)["status"], "success")

    def test_not_found_unchanged(self) -> None:
        from app.domain.browser.pricing_room_rates import finalize_product_tree_read

        read = {"status": "not_found", "summary": "x", "room_rates": []}
        self.assertEqual(finalize_product_tree_read(read)["status"], "not_found")

    def test_tree_read_downgrade_summary_carries_platform_label(self) -> None:
        # 2026-09-23 复核修复：read_product_tree_room_rates 的返回 dict 此前
        # 不带 platform 键，finalize 降级文案只能写「未知批量改价页」。
        # 美团产品树改版后每次读取都走降级路径，平台名必须如实带上。
        import asyncio

        from app.domain.browser.pricing_room_rates import (
            read_product_tree_room_rates,
        )

        page = _FakeVirtualizedPage(
            [
                [
                    {"row_text": "豪华套房 母产品", "is_parent": True, "selector_hint": "a"},
                    {"row_text": "行政套房 母产品", "is_parent": True, "selector_hint": "b"},
                ]
            ]
        )
        read = asyncio.run(read_product_tree_room_rates(page, platform="meituan"))
        self.assertEqual(read["status"], "page_structure_changed")
        self.assertIn("美团", read["summary"])
        self.assertNotIn("未知", read["summary"])


class MeituanCalendarRateTests(unittest.TestCase):
    """2026-09-23 改版：美团现价在「房价房量日历」产品子行（结构化单元格）。"""

    def _collected(self) -> dict:
        return {
            "pickers": ["2026-09-25", "2026-09-25"],
            "rooms": [
                {
                    "name": "悦聚套房",
                    "status": "已开房",
                    "count": "不限/0/0",
                    "products": [
                        {
                            "name": "悦聚套房-不含早-入住当天18:00前免费取消",
                            "tags": "母产品",
                            "cells": [
                                {
                                    "status": "已开房",
                                    "count": "不限",
                                    "sell": "506.00",
                                    "base": "440.22",
                                }
                            ],
                        },
                        {
                            "name": "悦聚套房-含双早-入住当天18:00前免费取消",
                            "tags": "联动",
                            "cells": [
                                {
                                    "status": "已开房",
                                    "count": "不限",
                                    "sell": "554.00",
                                    "base": "481.98",
                                }
                            ],
                        },
                    ],
                },
                {
                    "name": "豪华套房",
                    "status": "已关房",
                    "count": "5/0/5",
                    "products": [],
                },
            ],
        }

    def test_mother_product_price_with_status(self) -> None:
        from app.domain.browser.pricing_room_rates import meituan_calendar_room_rates

        rows = meituan_calendar_room_rates(self._collected(), checkin_date="2026-09-25")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["room_label"], "悦聚套房")
        self.assertEqual(rows[0]["sell_price_yuan"], 506)
        self.assertEqual(rows[0]["base_price_yuan"], 440.22)
        self.assertEqual(rows[0]["status_text"], "已开房·不限")

    def test_room_without_products_keeps_room_level_status(self) -> None:
        from app.domain.browser.pricing_room_rates import meituan_calendar_room_rates

        rows = meituan_calendar_room_rates(self._collected(), checkin_date="2026-09-25")
        self.assertEqual(rows[1]["room_label"], "豪华套房")
        self.assertIsNone(rows[1]["sell_price_yuan"])
        self.assertIsNone(rows[1]["base_price_yuan"])
        self.assertEqual(rows[1]["status_text"], "已关房·5/0/5")

    def test_target_date_picked_from_multi_day_window(self) -> None:
        from app.domain.browser.pricing_room_rates import meituan_calendar_room_rates

        collected = self._collected()
        collected["pickers"] = ["2026-09-23", "2026-09-26"]
        collected["rooms"][0]["products"][0]["cells"] = [
            {"status": "已开房", "count": "5/0/5", "sell": "506.00", "base": "440.22"},
            {"status": "已开房", "count": "不限", "sell": "536.00", "base": "466.32"},
            {"status": "已开房", "count": "不限", "sell": "680.00", "base": "591.60"},
            {"status": "已关房", "count": "不限", "sell": "680.00", "base": "591.60"},
        ]
        rows = meituan_calendar_room_rates(collected, checkin_date="2026-9-24")
        self.assertEqual(rows[0]["sell_price_yuan"], 536)
        self.assertEqual(rows[0]["status_text"], "已开房·不限")

    def test_request_date_outside_window_falls_back_to_first_column(self) -> None:
        from app.domain.browser.pricing_room_rates import meituan_calendar_room_rates

        rows = meituan_calendar_room_rates(self._collected(), checkin_date="2030-01-01")
        self.assertEqual(rows[0]["sell_price_yuan"], 506)

    def test_closed_canonical_loses_to_open_sibling(self) -> None:
        from app.domain.browser.pricing_room_rates import meituan_calendar_room_rates

        collected = self._collected()
        collected["rooms"][0]["products"] = [
            {
                "name": "悦聚套房-不含早-标准",
                "tags": "",
                "cells": [{"status": "已关房", "count": "不限", "sell": "700.00", "base": "609.00"}],
            },
            {
                "name": "悦聚套房-不含早-限时退",
                "tags": "",
                "cells": [{"status": "已开房", "count": "不限", "sell": "680.00", "base": "591.60"}],
            },
        ]
        rows = meituan_calendar_room_rates(collected, checkin_date="2026-09-25")
        self.assertEqual(rows[0]["sell_price_yuan"], 680)
        self.assertEqual(rows[0]["status_text"], "已开房·不限")

    def test_suite_without_breakfast_none_keeps_unpriced_row(self) -> None:
        from app.domain.browser.pricing_room_rates import meituan_calendar_room_rates

        collected = self._collected()
        collected["rooms"][0]["products"] = [
            {
                "name": "悦聚套房-含双早-入住当天18:00前免费取消",
                "tags": "联动",
                "cells": [{"status": "已开房", "count": "不限", "sell": "554.00", "base": "481.98"}],
            },
        ]
        rows = meituan_calendar_room_rates(collected, checkin_date="2026-09-25")
        self.assertEqual(rows[0]["room_label"], "悦聚套房")
        self.assertIsNone(rows[0]["sell_price_yuan"])
        self.assertEqual(rows[0]["status_text"], "已开房·不限")


class FliggyCalendarRateTests(unittest.TestCase):
    """2026-09-24 改版：飞猪现价/房态在「房价房量管理」日历页产品子行。"""

    def _collected(self) -> dict:
        return {
            "dates": ["09-24", "09-25"],
            "rooms": [
                {
                    "name": "豪华套房（全屋声控+智能马桶）",
                    "cells": ["满", "有"],
                    "products": [
                        {
                            "name": "双早-限时退",
                            "tags": "",
                            "cells": [
                                {"status": "满房", "count": "0 | 0 | 5", "sell": "545.00"},
                                {"status": "满房", "count": "0 | 0 | 5", "sell": "575.00"},
                            ],
                        },
                        {
                            "name": "无早-任意退【F2面纱专享】",
                            "tags": "主价格",
                            "cells": [
                                {"status": "满房", "count": "0 | 0 | 5", "sell": "448.00"},
                                {"status": "有房", "count": "0 | 0 | 5", "sell": "648.00"},
                            ],
                        },
                    ],
                },
                {
                    "name": "女王套房（豪华会客厅）",
                    "cells": ["满", "满"],
                    "products": [],
                },
            ],
        }

    def test_main_price_with_status_and_count(self) -> None:
        from app.domain.browser.pricing_room_rates import fliggy_calendar_room_rates

        rows = fliggy_calendar_room_rates(self._collected(), checkin_date="2026-09-24")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["room_label"], "豪华套房（全屋声控+智能马桶）")
        self.assertEqual(rows[0]["sell_price_yuan"], 448)
        self.assertIsNone(rows[0]["base_price_yuan"])
        self.assertEqual(rows[0]["status_text"], "满房·0 | 0 | 5")

    def test_target_date_picked_by_month_day(self) -> None:
        from app.domain.browser.pricing_room_rates import fliggy_calendar_room_rates

        rows = fliggy_calendar_room_rates(self._collected(), checkin_date="2026-9-25")
        self.assertEqual(rows[0]["sell_price_yuan"], 648)
        self.assertEqual(rows[0]["status_text"], "有房·0 | 0 | 5")

    def test_date_outside_window_falls_back_to_first_column(self) -> None:
        from app.domain.browser.pricing_room_rates import fliggy_calendar_room_rates

        rows = fliggy_calendar_room_rates(self._collected(), checkin_date="2030-01-01")
        self.assertEqual(rows[0]["sell_price_yuan"], 448)

    def test_room_without_products_keeps_room_level_status(self) -> None:
        from app.domain.browser.pricing_room_rates import fliggy_calendar_room_rates

        rows = fliggy_calendar_room_rates(self._collected(), checkin_date="2026-09-24")
        self.assertEqual(rows[1]["room_label"], "女王套房（豪华会客厅）")
        self.assertIsNone(rows[1]["sell_price_yuan"])
        self.assertEqual(rows[1]["status_text"], "满")

    def test_suite_without_breakfast_none_keeps_unpriced_row(self) -> None:
        from app.domain.browser.pricing_room_rates import fliggy_calendar_room_rates

        collected = self._collected()
        collected["rooms"][0]["products"] = [
            {
                "name": "双早-限时退",
                "tags": "",
                "cells": [
                    {"status": "有房", "count": "0 | 0 | 5", "sell": "545.00"},
                    {"status": "有房", "count": "0 | 0 | 5", "sell": "575.00"},
                ],
            },
        ]
        rows = fliggy_calendar_room_rates(collected, checkin_date="2026-09-24")
        self.assertEqual(rows[0]["room_label"], "豪华套房（全屋声控+智能马桶）")
        self.assertIsNone(rows[0]["sell_price_yuan"])
        self.assertEqual(rows[0]["status_text"], "有房·0 | 0 | 5")

    def test_non_dict_input_returns_empty(self) -> None:
        from app.domain.browser.pricing_room_rates import fliggy_calendar_room_rates

        self.assertEqual(fliggy_calendar_room_rates(None, checkin_date="2026-09-24"), [])


class _FakeFliggyCalendarPage:
    """支撑 read_fliggy_calendar_room_rates 交互面的假页面。"""

    def __init__(self, collected: dict, *, date_in_window: bool = True) -> None:
        self.url = "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk-rp/roomsVsManage"
        self._collected = collected
        self._date_in_window = date_in_window
        self.date_fills: list[str] = []
        self.expand_clicks = 0
        self.keyboard = self

    async def wait_for_selector(self, selector: str, timeout: float = 0) -> None:
        return None

    async def evaluate(self, script: str, arg: object | None = None) -> object:
        if "fliggy_cal_toggle" in script:
            return "already-on"
        if "fliggy_cal_collect" in script:
            return self._collected
        if "priceCell" in script:
            return 1
        raise AssertionError(f"unexpected evaluate script: {script[:80]}")

    def locator(self, selector: str):  # noqa: ANN201
        page = self

        class _DateInput:
            async def count(self) -> int:
                return 1

            @property
            def first(self) -> "_DateInput":
                return self

            async def click(self, timeout: float = 0) -> None:
                return None

            async def fill(self, value: str, timeout: float = 0) -> None:
                page.date_fills.append(value)

        return _DateInput()

    def get_by_text(self, text: str, exact: bool = True):  # noqa: ANN201
        page = self

        class _Expand:
            @property
            def first(self) -> "_Expand":
                return self

            async def count(self) -> int:
                return 1

            async def click(self, timeout: float = 0) -> None:
                page.expand_clicks += 1

        return _Expand()

    async def press(self, key: str) -> None:
        return None

    async def wait_for_timeout(self, ms: float) -> None:
        return None


class FliggyCalendarFlowTests(unittest.IsolatedAsyncioTestCase):
    """日历页读取：确保显示开关 → 必要时平移日期 → 点「全部展开」→ 收集。"""

    def _collected(self) -> dict:
        return {
            "dates": ["09-24", "09-25"],
            "rooms": [
                {
                    "name": "豪华套房（全屋声控+智能马桶）",
                    "cells": ["满", "有"],
                    "products": [
                        {
                            "name": "无早-任意退【F2面纱专享】",
                            "tags": "主价格",
                            "cells": [
                                {"status": "有房", "count": "0 | 0 | 5", "sell": "448.00"},
                                {"status": "有房", "count": "0 | 0 | 5", "sell": "648.00"},
                            ],
                        }
                    ],
                }
            ],
        }

    async def test_date_in_window_reads_without_picker_shift(self) -> None:
        import asyncio

        from app.domain.browser.pricing_room_rates import (
            read_fliggy_calendar_room_rates,
        )

        page = _FakeFliggyCalendarPage(self._collected(), date_in_window=True)
        read = await asyncio.wait_for(
            read_fliggy_calendar_room_rates(page, checkin_date="2026-09-24"),
            timeout=5,
        )
        self.assertEqual(read["status"], "success")
        self.assertEqual(read["room_rates"][0]["sell_price_yuan"], 448)
        self.assertEqual(read["room_rates"][0]["status_text"], "有房·0 | 0 | 5")
        self.assertEqual(page.expand_clicks, 1)
        self.assertEqual(page.date_fills, [])

    async def test_date_outside_window_shifts_picker(self) -> None:
        import asyncio

        from app.domain.browser.pricing_room_rates import (
            read_fliggy_calendar_room_rates,
        )

        page = _FakeFliggyCalendarPage(self._collected(), date_in_window=False)
        read = await asyncio.wait_for(
            read_fliggy_calendar_room_rates(page, checkin_date="2026-10-01"),
            timeout=5,
        )
        self.assertEqual(read["status"], "success")
        self.assertEqual(page.date_fills, ["2026-10-01"])


class _FakeMeituanCalendarPage:
    """支撑 read_meituan_calendar_room_rates 交互面的假页面。"""

    def __init__(self, collected: dict, *, bulk_expand: bool = False) -> None:
        self.url = "https://me.meituan.com/ebooking/merchant/product#/index"
        self._collected = collected
        self._bulk_expand = bulk_expand
        self._expand_rounds = 2
        self.serial_expands = 0
        self.date_fills: list[str] = []
        self.keyboard = self

    async def wait_for_selector(self, selector: str, timeout: float = 0) -> None:
        return None

    def locator(self, selector: str, **kwargs):  # noqa: ANN201
        page = self

        if selector == "input.lz-date-picker-editor":
            class _Picker:
                async def count(self) -> int:
                    return 2

                def nth(self, index: int) -> "_Picker":
                    return self

                async def click(self, timeout: float = 0) -> None:
                    return None

                async def fill(self, value: str, timeout: float = 0) -> None:
                    page.date_fills.append(value)

            return _Picker()

        if selector == ".lz-checkbox":
            class _Toggle:
                @property
                def first(self) -> "_Toggle":
                    return self

                async def click(self, timeout: float = 0) -> None:
                    return None

            return _Toggle()

        raise AssertionError(f"unexpected locator: {selector}")

    async def press(self, key: str) -> None:
        return None

    async def wait_for_timeout(self, ms: float) -> None:
        return None

    async def evaluate(self, script: str, arg: object | None = None) -> object:
        if "meituan_expand_all" in script:
            return 1 if self._bulk_expand else 0
        if "meituan_level1_count" in script:
            return 3 if self._bulk_expand else 0
        if "meituan_expand_one" in script:
            if self._bulk_expand:
                return 0  # 总开关已展开全部房型，没有仍折叠的行。
            if self._expand_rounds > 0:
                self._expand_rounds -= 1
                self.serial_expands += 1
                return 1
            return 0
        if "meituan_collect" in script:
            return self._collected
        if "saleprice-wrapper" in script:
            return 1
        raise AssertionError(f"unexpected evaluate script: {script[:80]}")


class MeituanCalendarFlowTests(unittest.IsolatedAsyncioTestCase):
    """日历页读取：设日期窗口 → 表头「展开全部房型」一键展开（残余房型
    逐个点房型名兜底）→ 确认「显示价格」开关 → 结构化收集。"""

    def _collected(self) -> dict:
        return {
            "pickers": ["2026-09-24", "2026-09-24"],
            "rooms": [
                {
                    "name": "悦聚套房",
                    "status": "已开房",
                    "count": "不限/0/0",
                    "products": [
                        {
                            "name": "悦聚套房-不含早-入住当天18:00前免费取消",
                            "tags": "母产品",
                            "cells": [
                                {
                                    "status": "已开房",
                                    "count": "不限",
                                    "sell": "536.00",
                                    "base": "466.32",
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    async def test_full_flow_returns_success(self) -> None:
        import asyncio

        from app.domain.browser.pricing_room_rates import (
            read_meituan_calendar_room_rates,
        )

        page = _FakeMeituanCalendarPage(self._collected())
        read = await asyncio.wait_for(
            read_meituan_calendar_room_rates(page, checkin_date="2026-09-24"),
            timeout=5,
        )
        self.assertEqual(read["status"], "success")
        self.assertEqual(read["room_rates"][0]["sell_price_yuan"], 536)
        self.assertEqual(read["room_rates"][0]["status_text"], "已开房·不限")
        # 日期窗口两侧都填了入住日。
        self.assertEqual(page.date_fills, ["2026-09-24", "2026-09-24"])
        # 总开关不可用时走逐房型兜底展开。
        self.assertEqual(page.serial_expands, 2)

    async def test_bulk_expand_button_short_circuits_serial_fallback(self) -> None:
        import asyncio

        from app.domain.browser.pricing_room_rates import (
            read_meituan_calendar_room_rates,
        )

        page = _FakeMeituanCalendarPage(self._collected(), bulk_expand=True)
        read = await asyncio.wait_for(
            read_meituan_calendar_room_rates(page, checkin_date="2026-09-24"),
            timeout=5,
        )
        self.assertEqual(read["status"], "success")
        # 表头总开关一键展开后，不再逐房型点房型名。
        self.assertEqual(page.serial_expands, 0)


class BoundedPageStateWaitTests(unittest.TestCase):
    """有界等待 SPA 渲染：半渲染壳不再误判 page_structure_changed。"""

    def test_waits_until_pricing_page_definitive(self) -> None:
        import asyncio

        from app.domain.browser.meituan_pricing_page_reader import (
            classify_meituan_pricing_page_state,
        )
        from app.domain.browser.pricing_room_rates import (
            wait_for_definitive_page_state,
        )

        page = _FakeRenderPage(
            bodies=[
                "加载中",
                "批量改房价 工作台",
                "批量改房价 房价 房型 日历 选择房型/产品",
            ]
        )
        state = asyncio.run(
            wait_for_definitive_page_state(page, classify_meituan_pricing_page_state)
        )
        self.assertEqual(state, "pricing_page")
        self.assertEqual(page.waits, 2)

    def test_returns_login_state_without_extra_wait(self) -> None:
        import asyncio

        from app.domain.browser.meituan_pricing_page_reader import (
            classify_meituan_pricing_page_state,
        )
        from app.domain.browser.pricing_room_rates import (
            wait_for_definitive_page_state,
        )

        page = _FakeRenderPage(bodies=["登录 账号 密码"], url="https://passport.meituan.com/login")
        state = asyncio.run(
            wait_for_definitive_page_state(page, classify_meituan_pricing_page_state)
        )
        self.assertEqual(state, "login_required")
        self.assertEqual(page.waits, 0)

    def test_login_redirected_after_read(self) -> None:
        from app.domain.browser.pricing_room_rates import login_redirected_after_read

        self.assertTrue(
            login_redirected_after_read("https://hotel.fliggy.com/ebooking/login.htm#/x")
        )
        self.assertFalse(
            login_redirected_after_read(
                "https://me.meituan.com/ebooking/merchant/product/batch-price"
            )
        )


if __name__ == "__main__":
    unittest.main()
