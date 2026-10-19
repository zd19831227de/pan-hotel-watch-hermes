from __future__ import annotations

import unittest

from client.competitor_trend_chart import (
    CJK_FONT_CANDIDATES,
    _build_chart_with_matplotlib,
    try_build_competitor_trend_chart_png,
)


def _sample_series() -> list[dict]:
    return [
        {
            "hotel_config_id": 1,
            "hotel_alias": "静里全套房酒店（云州澜江路望川路地铁站店）",
            "hotel_role": "own",
            "sort_order": 0,
            "date_label": "today",
            "breakfast_kind": "none",
            "room_type": "行政套房（全屋智能）",
            "points": [
                {"price_amount_yuan": 231, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"},
                {"price_amount_yuan": 228, "bucket_at": "2026-10-08T11:00:00", "captured_at": "2026-10-08T11:00:00"},
            ],
        },
        {
            "hotel_config_id": 2,
            "hotel_alias": "菲想酒店",
            "hotel_role": "competitor",
            "sort_order": 1,
            "date_label": "today",
            "breakfast_kind": "none",
            "room_type": "高级大床房",
            "points": [
                {"price_amount_yuan": 217, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"},
                {"price_amount_yuan": 219, "bucket_at": "2026-10-08T11:00:00", "captured_at": "2026-10-08T11:00:00"},
            ],
        },
    ]


class CompetitorTrendChartTests(unittest.TestCase):
    def test_matplotlib_configures_cjk_font(self) -> None:
        # 2026-10-08 生产乱码修复：标题/店名是中文，matplotlib 必须配置 CJK 字体，
        # 否则全部画成豆腐块。
        payload = _build_chart_with_matplotlib(_sample_series(), days=3)
        self.assertIsInstance(payload, bytes)
        self.assertGreater(len(payload), 5000)

        import matplotlib.pyplot as plt

        sans = list(plt.rcParams["font.sans-serif"])
        self.assertTrue(
            any(font in CJK_FONT_CANDIDATES for font in sans[:3]),
            f"font.sans-serif 首选应含 CJK 候选，实际 {sans[:3]}",
        )
        self.assertFalse(plt.rcParams["axes.unicode_minus"])

    def test_png_pipeline_returns_bytes(self) -> None:
        payload = try_build_competitor_trend_chart_png(_sample_series(), days=3)
        self.assertIsInstance(payload, bytes)
        self.assertGreater(len(payload), 5000)

    def _lirui_style_series(self) -> list[dict]:
        # 生产实证形态（2026-10-08 立瑞）：本店只有含早序列、水云岸只有
        # 无早序列——按早次拆面板两家永不同框，min 口径才能同图对比。
        return [
            {
                "hotel_config_id": 1,
                "hotel_alias": "云州澜江路立瑞酒店",
                "hotel_role": "own",
                "sort_order": 0,
                "date_label": "today",
                "breakfast_kind": "included",
                "points": [
                    {"price_amount_yuan": 234, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"},
                    {"price_amount_yuan": 240, "bucket_at": "2026-10-08T11:00:00", "captured_at": "2026-10-08T11:00:00"},
                ],
            },
            {
                "hotel_config_id": 2,
                "hotel_alias": "云州澜江路步行街水云岸酒店",
                "hotel_role": "competitor",
                "sort_order": 1,
                "date_label": "today",
                "breakfast_kind": "none",
                "points": [
                    {"price_amount_yuan": 262, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"},
                    {"price_amount_yuan": 223, "bucket_at": "2026-10-08T11:00:00", "captured_at": "2026-10-08T11:00:00"},
                ],
            },
            {
                "hotel_config_id": 2,
                "hotel_alias": "云州澜江路步行街水云岸酒店",
                "hotel_role": "competitor",
                "sort_order": 1,
                "date_label": "today",
                "breakfast_kind": "included",
                "points": [
                    {"price_amount_yuan": 300, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"},
                    {"price_amount_yuan": 280, "bucket_at": "2026-10-08T11:00:00", "captured_at": "2026-10-08T11:00:00"},
                ],
            },
            {
                "hotel_config_id": 3,
                "hotel_alias": "其他竞店",
                "hotel_role": "competitor",
                "sort_order": 2,
                "date_label": "today",
                "breakfast_kind": "none",
                "points": [
                    {"price_amount_yuan": 199, "bucket_at": "2026-10-08T09:00:00", "captured_at": "2026-10-08T09:00:00"},
                ],
            },
        ]

    def test_filter_trend_series_by_hotels_matches_own_token_and_alias(self) -> None:
        from client.competitor_trend_model import filter_trend_series_by_hotels

        series = self._lirui_style_series()
        kept = filter_trend_series_by_hotels(series, ["本店", "水云岸"])
        self.assertEqual(len(kept), 3)  # 本店1 + 水云岸2（无早+含早）
        self.assertTrue(all("立瑞" in str(s.get("hotel_alias")) or "水云岸" in str(s.get("hotel_alias")) for s in kept))
        # 空过滤原样返回
        self.assertEqual(filter_trend_series_by_hotels(series, []), series)

    def test_build_min_comparison_series_merges_breakfasts_per_bucket_min(self) -> None:
        from client.competitor_trend_model import (
            MIN_COMPARE_BREAKFAST,
            build_min_comparison_series,
        )

        merged = build_min_comparison_series(self._lirui_style_series())
        # 每店×日期一条序列：本店(today)、水云岸(today)、其他竞店(today)
        self.assertEqual(len(merged), 3)
        by_alias = {str(s["hotel_alias"]): s for s in merged}
        own = by_alias["云州澜江路立瑞酒店"]
        self.assertEqual(own["breakfast_kind"], MIN_COMPARE_BREAKFAST)
        prices = {p["bucket_at"][11:16]: p["price_amount_yuan"] for p in own["points"]}
        self.assertEqual(prices, {"09:00": 234, "11:00": 240})
        # 水云岸同桶跨早次取最低：09:00 min(262,300)=262；11:00 min(223,280)=223
        comp = by_alias["云州澜江路步行街水云岸酒店"]
        comp_prices = {p["bucket_at"][11:16]: p["price_amount_yuan"] for p in comp["points"]}
        self.assertEqual(comp_prices, {"09:00": 262, "11:00": 223})

    def test_min_comparison_chart_renders_own_and_competitor_same_png(self) -> None:
        from client.competitor_trend_model import (
            MIN_COMPARE_BREAKFAST,
            build_min_comparison_series,
            filter_trend_series_by_hotels,
        )
        from client.competitor_trend_chart import is_min_comparison_series

        series = build_min_comparison_series(
            filter_trend_series_by_hotels(self._lirui_style_series(), ["本店", "水云岸"])
        )
        self.assertTrue(is_min_comparison_series(series))
        self.assertTrue(all(s["breakfast_kind"] == MIN_COMPARE_BREAKFAST for s in series))
        # 本店与水云岸（不同早次序列）归并后出现在同一张图里
        roles = {s["hotel_role"] for s in series}
        self.assertEqual(roles, {"own", "competitor"})
        png = try_build_competitor_trend_chart_png(series, days=1)
        self.assertIsInstance(png, bytes)
        self.assertGreater(len(png), 5000)
        # 标准视图（无 min 标记）不受影响，仍走四面板渲染
        self.assertFalse(is_min_comparison_series(_sample_series()))
        standard_png = try_build_competitor_trend_chart_png(_sample_series(), days=3)
        self.assertGreater(len(standard_png), 5000)


if __name__ == "__main__":
    unittest.main()
