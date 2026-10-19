"""携程写价价格块点击中心解析单测：滚动后旧视口坐标失效的安全取舍。

2026-09-14 实测：日历房型表虚拟滚动，滚动后 DOM 重渲染，检测时的
center_x/center_y 可能指向别的房型块。已滚动但重检定位不到目标时，
必须放弃点击（宁停不点错），返回 None 由上层走 page_structure_changed。
"""

from __future__ import annotations

import unittest

from app.domain.browser.ctrip_room_type_selector import (
    order_candidates_by_canonical_breakfast,
    resolve_price_block_click_center,
)


_CANDIDATE = {
    "room_text": "奢华三卧套房 卖价1500",
    "text": "卖价1500",
    "center_x": 320,
    "center_y": 4800,
}


class ResolveClickCenterTests(unittest.TestCase):
    def test_not_scrolled_keeps_detected_coords(self) -> None:
        center = resolve_price_block_click_center(_CANDIDATE, scrolled=False)
        self.assertEqual(center, (320.0, 4800.0))

    def test_scrolled_uses_refreshed_coords_when_relocated(self) -> None:
        center = resolve_price_block_click_center(
            _CANDIDATE,
            scrolled=True,
            fresh_candidates=[
                {
                    "room_text": "奢华三卧套房 卖价1500",
                    "text": "卖价1500",
                    "center_x": 318,
                    "center_y": 410,
                }
            ],
        )
        self.assertEqual(center, (318.0, 410.0))

    def test_scrolled_without_relocation_refuses_to_click(self) -> None:
        center = resolve_price_block_click_center(
            _CANDIDATE,
            scrolled=True,
            fresh_candidates=[
                {
                    "room_text": "豪华套房 卖价1100",
                    "text": "卖价1100",
                    "center_x": 322,
                    "center_y": 420,
                }
            ],
        )
        self.assertIsNone(center)

    def test_scrolled_without_fresh_candidates_refuses_to_click(self) -> None:
        self.assertIsNone(
            resolve_price_block_click_center(_CANDIDATE, scrolled=True, fresh_candidates=None)
        )

    def test_missing_coords_return_none(self) -> None:
        candidate = dict(_CANDIDATE)
        candidate.pop("center_y")
        self.assertIsNone(resolve_price_block_click_center(candidate, scrolled=False))


class OrderCandidatesByCanonicalBreakfastTests(unittest.TestCase):
    """统一无早口径：目标房型的价格块候选优先点无早（预售/钟点类靠后，
    单早/双早最后），同组内保持原有 score 顺序。"""

    def test_breakfast_none_ranked_first(self) -> None:
        candidates = [
            {"room_text": "豪华套房<乐享旅程><双早>", "text": "卖价480", "score": 50},
            {"room_text": "豪华套房<乐享旅程><单早>", "text": "卖价456", "score": 40},
            {"room_text": "豪华套房<乐享旅程><无早>", "text": "卖价432", "score": 30},
        ]
        ordered = order_candidates_by_canonical_breakfast(candidates)
        self.assertIn("<无早>", ordered[0]["room_text"])
        self.assertEqual(
            [("<无早>" in c["room_text"]) for c in ordered], [True, False, False]
        )

    def test_clean_breakfast_none_before_presale(self) -> None:
        candidates = [
            {
                "room_text": "悦聚套房<2人入住><无早><预售产品>",
                "text": "卖价270",
                "score": 50,
            },
            {
                "room_text": "悦聚套房<乐享旅程><无早>",
                "text": "卖价513",
                "score": 20,
            },
        ]
        ordered = order_candidates_by_canonical_breakfast(candidates)
        self.assertIn("<乐享旅程><无早>", ordered[0]["room_text"])

    def test_stable_within_same_group(self) -> None:
        candidates = [
            {"room_text": "A<无早>", "text": "卖价1", "score": 10},
            {"room_text": "B<无早>", "text": "卖价2", "score": 90},
        ]
        ordered = order_candidates_by_canonical_breakfast(candidates)
        self.assertEqual([c["room_text"] for c in ordered], ["A<无早>", "B<无早>"])


if __name__ == "__main__":
    unittest.main()
