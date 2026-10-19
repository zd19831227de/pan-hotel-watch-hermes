from __future__ import annotations

import unittest

from client.competitor_trend_blocks import build_trend_grouped_mobile_blocks
from client.competitor_trend_model import summarize_price_process


class CompetitorTrendBlocksTests(unittest.TestCase):
    def test_groups_breakfast_kinds_with_process_trail(self) -> None:
        series = [
            {
                "hotel_config_id": 1,
                "hotel_name": "澜江路泉吉酒店",
                "hotel_role": "competitor",
                "date_label": "today",
                "breakfast_kind": "none",
                "room_type": "高级大床房",
                "sort_order": 1,
                "points": [
                    {"price_amount_yuan": 430, "bucket_at": "2026-06-27T09:00:00", "captured_at": "2026-06-27T09:00:00"},
                    {"price_amount_yuan": 410, "bucket_at": "2026-06-28T09:00:00", "captured_at": "2026-06-28T09:00:00"},
                    {"price_amount_yuan": 398, "bucket_at": "2026-06-29T09:00:00", "captured_at": "2026-06-29T09:00:00"},
                ],
            },
            {
                "hotel_config_id": 1,
                "hotel_name": "澜江路泉吉酒店",
                "hotel_role": "competitor",
                "date_label": "today",
                "breakfast_kind": "included",
                "room_type": "高级大床房",
                "sort_order": 1,
                "points": [
                    {"price_amount_yuan": 470, "bucket_at": "2026-06-27T09:00:00", "captured_at": "2026-06-27T09:00:00"},
                    {"price_amount_yuan": 438, "bucket_at": "2026-06-29T09:00:00", "captured_at": "2026-06-29T09:00:00"},
                ],
            },
        ]
        blocks = build_trend_grouped_mobile_blocks(series)
        self.assertEqual(len(blocks), 1)
        self.assertIn("无早", blocks[0])
        self.assertIn("含早", blocks[0])
        self.assertIn("次读价", blocks[0])
        self.assertIn("¥430", blocks[0])
        self.assertIn("¥398", blocks[0])
        self.assertNotIn("included", blocks[0])

    def test_summarize_shows_trail_not_period_endpoints_only(self) -> None:
        timeline = [
            ("2026-06-27T09:00:00", 430, "06-27 09:00"),
            ("2026-06-28T09:00:00", 410, "06-28 09:00"),
            ("2026-06-29T09:00:00", 398, "06-29 09:00"),
        ]
        text = summarize_price_process(timeline)
        self.assertIn("3次读价", text)
        self.assertIn("¥430 → ¥410 → ¥398", text)

    def test_room_type_placeholder_falls_back_to_real_room_types(self) -> None:
        # 2026-10-08 生产实证：backend 在同 series 出现多个房型时把 room_type
        # 置为「房型有变化」（内部状态标记，真实房型在 room_types 列表）。
        # 展示层不得把该标记直接渲染给店长，应回退到真实房型。
        series = [
            {
                "hotel_config_id": 6,
                "hotel_name": "静里全套房酒店",
                "hotel_role": "own",
                "date_label": "today",
                "breakfast_kind": "included",
                "room_type": "房型有变化",
                "room_types": [
                    "行政套房（全屋智能+独立会客厅+全套厨卫家电）",
                    "悦聚套房(大复式+独卧+会客厅+岛台+全套厨卫家电)",
                ],
                "room_type_changed": True,
                "sort_order": 0,
                "points": [
                    {"price_amount_yuan": 231, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"},
                    {"price_amount_yuan": 228, "bucket_at": "2026-10-08T11:00:00", "captured_at": "2026-10-08T11:00:00"},
                ],
            },
        ]
        blocks = build_trend_grouped_mobile_blocks(series)
        self.assertEqual(len(blocks), 1)
        self.assertIn("行政套房", blocks[0])
        self.assertIn("2个房型", blocks[0])
        self.assertNotIn("房型有变化", blocks[0])

    def test_room_type_placeholder_without_list_shows_dash(self) -> None:
        series = [
            {
                "hotel_config_id": 7,
                "hotel_name": "水云岸酒店",
                "hotel_role": "competitor",
                "date_label": "today",
                "breakfast_kind": "none",
                "room_type": "房型有变化",
                "room_types": [],
                "sort_order": 2,
                "points": [
                    {"price_amount_yuan": 262, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"},
                ],
            },
        ]
        blocks = build_trend_grouped_mobile_blocks(series)
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("房型有变化", blocks[0])
        self.assertIn("房型：-", blocks[0])


if __name__ == "__main__":
    unittest.main()
