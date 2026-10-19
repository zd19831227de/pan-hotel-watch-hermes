"""EBK 全局推送投影层单测（设计文档 §5/§6）。"""

from __future__ import annotations

import unittest

from app.domain.ebk_global_push.projection import (
    CATEGORY_ADVERTISING,
    CATEGORY_BUSINESS,
    CATEGORY_PROMOTION,
    extract_ctrip_score_summary,
    format_row_value,
    normalize_projection_rows,
)


def _row(**overrides):
    base = {
        "platform": "ctrip",
        "dataset_id": "business_outline",
        "field_key": "realtime_booking_orders",
        "label": "实时预订订单",
        "value": 12,
        "value_type": "integer",
        "unit": "单",
        "time_scope": "realtime",
        "freshness": "realtime",
        "captured_at": "2026-08-28T08:00:32+08:00",
        "source_path": "datasets.business_outline.data.metrics.realtime_booking_orders",
    }
    base.update(overrides)
    return base


class NormalizeProjectionRowsTests(unittest.TestCase):
    def test_accepts_rows_with_valid_freshness_and_category(self):
        accepted, rejected = normalize_projection_rows([_row()])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_rejects_unknown_freshness(self):
        accepted, rejected = normalize_projection_rows(
            [_row(freshness="unknown")]
        )
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "freshness_unknown")

    def test_rejects_dataset_without_category_whitelist(self):
        accepted, rejected = normalize_projection_rows(
            [_row(dataset_id="loss_analysis")]
        )
        self.assertEqual(accepted, [])
        self.assertEqual(rejected[0]["reason"], "dataset_not_whitelisted")

    def test_rejects_row_missing_label_or_value(self):
        accepted, rejected = normalize_projection_rows(
            [_row(label=""), _row(value=None)]
        )
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 2)

    def test_rejects_non_realtime_app_dimension_rows_for_business_card(self):
        """昨日口径 / 历史趋势不能进实时卡（freshness 必须 realtime/current_state/score_snapshot）。"""
        accepted, _ = normalize_projection_rows(
            [
                _row(field_key="yesterday_orders", label="昨日订单", freshness="yesterday"),
                _row(dataset_id="promotion_registered", field_key="joined",
                     label="已报名活动", freshness="current_state"),
            ]
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["category"], CATEGORY_PROMOTION)

    def test_assigns_categories_per_whitelist(self):
        accepted, _ = normalize_projection_rows(
            [
                _row(),
                _row(dataset_id="competition_profile", field_key="rank", label="竞争圈排名"),
                _row(dataset_id="ad_pyramid", field_key="cost", label="花费",
                     freshness="realtime"),
                _row(platform="meituan", dataset_id="workbench_home",
                     field_key=" realtime_sales", label="今日实时销售额", freshness="realtime"),
                _row(platform="fliggy", dataset_id="home_v1",
                     field_key="today_sales", label="今日销售额", freshness="realtime"),
            ]
        )
        categories = [row["category"] for row in accepted]
        self.assertEqual(
            categories,
            [CATEGORY_BUSINESS, CATEGORY_BUSINESS, CATEGORY_ADVERTISING,
             CATEGORY_BUSINESS, CATEGORY_BUSINESS],
        )


class FormatRowValueTests(unittest.TestCase):
    def test_integer_gets_thousands_separator_and_unit(self):
        self.assertEqual(format_row_value(_row(value=12345)), "12,345 单")

    def test_amount_keeps_two_decimals(self):
        self.assertEqual(
            format_row_value(_row(value=18650.5, value_type="amount", unit="元")),
            "18,650.50 元",
        )

    def test_percent_not_rescaled(self):
        self.assertEqual(
            format_row_value(_row(value=4.6, value_type="percent", unit="%")),
            "4.6 %",
        )

    def test_text_value_passthrough(self):
        self.assertEqual(
            format_row_value(_row(value="进行中", value_type="text", unit="")),
            "进行中",
        )


class CtripScoreSummaryTests(unittest.TestCase):
    def test_both_scores_present(self):
        rows = [
            _row(field_key="psi_service_score", label="PSI 服务质量分", value=4.72,
                 value_type="score", unit="", freshness="score_snapshot"),
            _row(field_key="hotel_review_score", label="酒店点评分", value=4.6,
                 value_type="score", unit="", freshness="score_snapshot"),
        ]
        summary = extract_ctrip_score_summary(rows)
        self.assertEqual(summary["psi"], "4.72")
        self.assertEqual(summary["review_score"], "4.6")
        self.assertEqual(summary["title_suffix"], "携程 PSI 4.72｜携程店铺评分 4.6")

    def test_missing_score_shows_placeholder_without_blocking(self):
        rows = [_row(field_key="psi_service_score", label="PSI 服务质量分",
                     value=4.7, value_type="score", unit="", freshness="score_snapshot")]
        summary = extract_ctrip_score_summary(rows)
        self.assertEqual(summary["review_score"], "未读取")
        self.assertEqual(summary["title_suffix"], "携程 PSI 4.7｜携程店铺评分 未读取")

    def test_psi_uses_platform_scale_while_review_score_remains_five_point_scale(self):
        rows = [
            _row(field_key="psi_service_score", label="PSI 服务质量分", value=5.57,
                 value_type="score", unit="", freshness="score_snapshot"),
            _row(field_key="hotel_review_score", label="酒店点评分", value=5.2,
                 value_type="score", unit="", freshness="score_snapshot"),
        ]
        summary = extract_ctrip_score_summary(rows)
        self.assertEqual(summary["psi"], "5.57")
        self.assertEqual(summary["review_score"], "未读取")

    def test_scores_preserve_the_actual_page_precision(self):
        rows = [
            _row(field_key="psi_service_score", label="PSI 服务质量分", value="5.44",
                 value_type="score", unit="", freshness="score_snapshot"),
            _row(field_key="hotel_review_score", label="酒店点评分", value="4.79",
                 value_type="score", unit="", freshness="score_snapshot"),
        ]
        summary = extract_ctrip_score_summary(rows)
        self.assertEqual(summary["psi"], "5.44")
        self.assertEqual(summary["review_score"], "4.79")
        self.assertEqual(summary["title_suffix"], "携程 PSI 5.44｜携程店铺评分 4.79")

    def test_out_of_range_or_non_score_value_rejected(self):
        rows = [
            _row(field_key="psi_service_score", label="PSI", value=96,
                 value_type="score", unit="", freshness="score_snapshot"),
            _row(field_key="hotel_review_score", label="点评分", value="4.6分",
                 value_type="text", unit="", freshness="score_snapshot"),
        ]
        summary = extract_ctrip_score_summary(rows)
        self.assertEqual(summary["psi"], "未读取")
        self.assertEqual(summary["review_score"], "未读取")
        self.assertEqual(
            summary["title_suffix"], "携程 PSI 未读取｜携程店铺评分 未读取")

    def test_empty_rows_all_placeholder(self):
        summary = extract_ctrip_score_summary([])
        self.assertEqual(summary["psi"], "未读取")
        self.assertEqual(summary["review_score"], "未读取")


if __name__ == "__main__":
    unittest.main()
