from __future__ import annotations

import unittest

from app.domain.browser.pricing_product_selection import (
    ProductRow,
    match_platform_products,
    normalize_room_type,
)

# B5.1 实测 fixture：飞猪批量修改-价格页展开后的产品树行文本（节选）。
FLIGGY_ROWS = [
    ProductRow(row_text="豪华套房（全屋声控+智能马桶+小冰箱+洗烘一体机） 含5个", is_parent=True),
    ProductRow(row_text="双早-限时退", is_parent=False),
    ProductRow(row_text="单早-限时退【F1面纱专享】 子价格", is_parent=False),
    ProductRow(row_text="双早-任意退【F1面纱专享】 子价格", is_parent=False),
    ProductRow(row_text="无早-任意退【F2面纱专享】 主价格", is_parent=False),
    ProductRow(row_text="双早-限时退【商旅专享】", is_parent=False),
    ProductRow(row_text="精英套房（大复式+独卧+会客厅+独立办公+全套厨卫家电） 含5个", is_parent=True),
    ProductRow(row_text="单早-限时退【F1面纱专享】 子价格", is_parent=False),
    ProductRow(row_text="双早-任意退【F1面纱专享】 子价格", is_parent=False),
    ProductRow(row_text="无早-任意退【F2面纱专享】 主价格", is_parent=False),
    ProductRow(row_text="悦聚套房(大复式+独卧+会客厅+岛台+全套厨卫家电) 含5个", is_parent=True),
    ProductRow(row_text="双早-限时退", is_parent=False),
    ProductRow(row_text="无早-任意退【F2面纱专享】 主价格", is_parent=False),
]

# B5.1 实测 fixture：美团批量改房价页房型行（room-goods-item-room 文本）。
MEITUAN_ROWS = [
    ProductRow(row_text="悦聚套房", is_parent=True),
    ProductRow(row_text="悦聚套房-不含早", is_parent=False),
    ProductRow(row_text="悦聚套房-含双早", is_parent=False),
    ProductRow(row_text="豪华套房", is_parent=True),
    ProductRow(row_text="豪华套房-不含早", is_parent=False),
    ProductRow(row_text="行政套房", is_parent=True),
]


class NormalizeRoomTypeTests(unittest.TestCase):
    def test_normalizes_whitespace_and_case(self) -> None:
        self.assertEqual(normalize_room_type("  豪华 套房 "), "豪华套房")
        self.assertEqual(normalize_room_type("Deluxe Suite"), "deluxesuite")
        self.assertEqual(normalize_room_type(None), "")


class FliggyMappingTests(unittest.TestCase):
    """B5.2 — 审批房型 → 飞猪平台产品三级匹配。

    2026-09-14 业务核对：统一无早口径——只调无早（母产品/主价格）价格，
    单早/双早随无早自动联动，breakfast_kind 不再驱动子产品筛选。
    """

    def test_exact_parent_match_without_breakfast_matches_main_price(self) -> None:
        """无 breakfast_kind 也直接锁定「主价格」（无早）子产品，不再要求人工补早餐粒度。"""
        result = match_platform_products("豪华套房", FLIGGY_ROWS, breakfast_kind=None)
        self.assertEqual(result.status, "matched")
        self.assertEqual(len(result.matched_rows), 1)
        self.assertIn("主价格", result.matched_rows[0].row_text)
        self.assertIn("无早", result.matched_rows[0].row_text)

    def test_non_none_breakfast_kind_blocked_for_audit_consistency(self) -> None:
        """审批显式带非无早粒度（如双早）时不静默改选无早执行：双早目标价
        直接写进无早会让实际双早价（无早+早餐加价）偏离审批值，必须拦截
        请运营按无早口径重新发起。"""
        result = match_platform_products("豪华套房", FLIGGY_ROWS, breakfast_kind="双早")
        self.assertEqual(result.status, "needs_review")
        self.assertIn("无早", str(result.reason))

    def test_breakfast_keyword_narrowing_returns_unique_match(self) -> None:
        result = match_platform_products(
            "悦聚套房", FLIGGY_ROWS, breakfast_kind="无早"
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(len(result.matched_rows), 1)
        self.assertIn("无早-任意退", result.matched_rows[0].row_text)

    def test_unknown_room_type_returns_not_found_with_parent_candidates(self) -> None:
        result = match_platform_products("海底捞套房", FLIGGY_ROWS, breakfast_kind="双早")
        self.assertEqual(result.status, "not_found")
        self.assertTrue(result.candidates)

    def test_ambiguous_parent_match_returns_needs_review(self) -> None:
        rows = [
            ProductRow(row_text="套房A（江景） 含2个", is_parent=True),
            ProductRow(row_text="套房B（山景） 含2个", is_parent=True),
            ProductRow(row_text="豪华套房 含3个", is_parent=True),
        ]
        result = match_platform_products("套房", rows, breakfast_kind=None)
        # 「套房」无精确命中，前缀同时命中「套房A」「套房B」→ 多匹配 needs_review
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(len(result.candidates), 2)

    def test_multiple_canonical_children_need_review(self) -> None:
        rows = [
            ProductRow(row_text="江景大床房 含3个", is_parent=True),
            ProductRow(row_text="无早-任意退 主价格", is_parent=False),
            ProductRow(row_text="无早-限时退 主价格", is_parent=False),
        ]
        result = match_platform_products("江景大床房", rows)
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(len(result.candidates), 2)

    def test_no_canonical_child_needs_review(self) -> None:
        rows = [
            ProductRow(row_text="家庭双卧套房 含3个", is_parent=True),
            ProductRow(row_text="双早-限时退", is_parent=False),
            ProductRow(row_text="单早-任意退 子价格", is_parent=False),
        ]
        result = match_platform_products("家庭双卧套房", rows)
        self.assertEqual(result.status, "needs_review")
        self.assertIn("无早", str(result.reason))


class MeituanMappingTests(unittest.TestCase):
    """B5.2 — 美团房型行匹配（子产品行文本含房型前缀）。"""

    def test_meituan_exact_parent_with_breakfast(self) -> None:
        result = match_platform_products(
            "悦聚套房", MEITUAN_ROWS, breakfast_kind="不含早"
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.matched_rows[0].row_text, "悦聚套房-不含早")

    def test_meituan_no_breakfast_returns_parent_with_candidates(self) -> None:
        result = match_platform_products("行政套房", MEITUAN_ROWS, breakfast_kind=None)
        # 行政套房无子产品行：返回母房型行本身供状态机勾选
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.matched_rows[0].row_text, "行政套房")

    def test_meituan_non_none_breakfast_blocked_like_fliggy(self) -> None:
        """非无早审批粒度一律拦截（执行必须与审批一致），美团同样适用。"""
        result = match_platform_products(
            "悦聚套房", MEITUAN_ROWS, breakfast_kind="双早"
        )
        self.assertEqual(result.status, "needs_review")
        self.assertIn("无早", str(result.reason))


class EvidenceShapeTests(unittest.TestCase):
    def test_result_carries_reason_for_review(self) -> None:
        result = match_platform_products("豪华套房", FLIGGY_ROWS, breakfast_kind=None)
        self.assertTrue(result.reason)
        result2 = match_platform_products("海底捞", FLIGGY_ROWS)
        self.assertTrue(result2.reason)


if __name__ == "__main__":
    unittest.main()
