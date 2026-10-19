from __future__ import annotations

import unittest
from typing import Any

from app.domain.exports.ebk_business_metrics import (
    APP_CONVERSION_HEADERS,
    CORE_HEADERS,
    DETAIL_HEADERS,
    OPERATING_BRIEF_HEADERS,
    build_core_metric_rows,
    build_app_conversion_rows,
    build_competition_rank_rows,
    build_operating_brief_rows,
    extract_business_metric_rows,
)


def build_sample_snapshots() -> list[dict[str, Any]]:
    """三平台最小快照样例：携程(指标+表格+口径)、美团(中文指标)、飞猪(分段+活动)。"""

    return [
        {
            "id": 1,
            "platform": "ctrip",
            "platform_account_id": "ctrip_main",
            "status": "success",
            "created_at": "2026-08-21T10:00:00",
            "payload_json": {
                "platform": "ctrip",
                "datasets": {
                    "business_outline": {
                        "status": "success",
                        "data": {
                            "dataset_id": "business_outline",
                            "dataset_label": "收益概览",
                            "status": "success",
                            "metrics": {
                                "departure_sales_amount": {
                                    "key": "departure_sales_amount",
                                    "label": "离店销售额",
                                    "value": "12400",
                                },
                                "realtime_booking_orders": {
                                    "key": "realtime_booking_orders",
                                    "label": "实时预订订单",
                                    "value": "86",
                                    "section": "预订",
                                },
                                "occupancy_rate": {
                                    "key": "occupancy_rate",
                                    "label": "出租率",
                                    "value": "92%",
                                },
                            },
                            "metric_groups": {"core": ["departure_sales_amount"]},
                            "primary_time_dimension": {"value": "today", "label": "今日"},
                            "raw_snippets": ["should not appear"],
                            "captured_at": "2026-08-21T10:00:00",
                        },
                    },
                    "competition_list": {
                        "status": "success",
                        "data": {
                            "metrics": {},
                            "tables": [
                                {
                                    "name": "sales_ranking",
                                    "label": "销售排名",
                                    "columns": ["预订量排名"],
                                    "rows": [
                                        {
                                            "hotel_name": "汝家酒店",
                                            "rankings": {"预订量排名": "第2名"},
                                            "raw_values": ["1", "2"],
                                        }
                                    ],
                                }
                            ],
                        },
                    },
                    "loss_analysis": {"status": "error", "data": {"metrics": {}}},
                },
                "evidence": {"screenshot": "secret-screenshot-ref"},
            },
        },
        {
            "id": 2,
            "platform": "meituan",
            "platform_account_id": "mt_main",
            "status": "success",
            "created_at": "2026-08-21T10:00:00",
            "payload_json": {
                "platform": "meituan",
                "datasets": {
                    "data_center_trade": {
                        "status": "success",
                        "data": {
                            "hotel_name": "璞岸酒店",
                            "tab_label": "交易",
                            "metrics": {
                                "销售额": "8200",
                                "支付订单数": "64",
                                "满房率": "88%",
                                "平均房价": "298",
                            },
                            "business_performance": {"销售额": "8200"},
                            "visible_lines": ["noise-line"],
                        },
                    }
                },
            },
        },
        {
            "id": 3,
            "platform": "fliggy",
            "platform_account_id": "fliggy_main",
            "status": "success",
            "created_at": "2026-08-21T10:05:00",
            "payload_json": {
                "platform": "fliggy",
                "datasets": {
                    "operating_data": {
                        "status": "success",
                        "data": {
                            "in_house_data": {"在店间夜": "30", "入住率": "76%"},
                            "booking_data": {"预订单数": "21", "预订收入金额": "3100"},
                            "flow_data": {"预订转化率": "2.8%"},
                            "visible_lines": [],
                        },
                    },
                    "promotion_agent": {
                        "status": "success",
                        "data": {
                            "joined_activities": [
                                {
                                    "name": "提前订优惠",
                                    "activity_type": "折扣",
                                    "room_product": "大床房",
                                    "valid_period": "2026-08-01~08-31",
                                    "strength": "9折",
                                    "status": "生效中",
                                }
                            ],
                        },
                    },
                },
            },
        },
    ]


class EbkBusinessMetricsTests(unittest.TestCase):
    """整体业务数据导出规范化：三平台指标统一为中文明细长表 + 跨平台核心宽表。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshots = build_sample_snapshots()
        cls.rows = extract_business_metric_rows(cls.snapshots)

    def _row(self, **match: Any) -> dict[str, Any]:
        for row in self.rows:
            if all(row.get(key) == value for key, value in match.items()):
                return row
        self.fail(f"row not found: {match}")
        raise AssertionError

    def test_detail_headers_are_chinese_operator_facing(self) -> None:
        self.assertEqual(
            DETAIL_HEADERS,
            [
                "快照时间",
                "平台",
                "账号",
                "数据来源",
                "酒店",
                "数据集",
                "维度",
                "指标",
                "数值",
                "环比",
                "同比",
                "竞争圈平均",
                "竞争圈排名",
                "排名环比",
                "数据口径",
                "读取状态",
            ],
        )

    def test_ctrip_metrics_use_chinese_labels_and_sections(self) -> None:
        row = self._row(平台="携程", 指标="离店销售额")
        self.assertEqual(row["数值"], 12400)
        self.assertEqual(row["数据集"], "收益概览")
        self.assertEqual(row["数据口径"], "今日")
        self.assertEqual(row["快照时间"], "2026-08-21T10:00:00")

        booking = self._row(平台="携程", 指标="实时预订订单")
        self.assertEqual(booking["维度"], "预订")
        self.assertEqual(booking["数值"], 86)

    def test_ctrip_table_rows_expand_per_hotel(self) -> None:
        row = self._row(平台="携程", 指标="预订量排名")
        self.assertEqual(row["数值"], "第2名")
        self.assertIn("销售排名", row["维度"])
        self.assertIn("汝家酒店", row["维度"])

    def test_failed_datasets_and_noise_keys_are_skipped(self) -> None:
        datasets = {row["数据集"] for row in self.rows}
        self.assertNotIn("流失订单", datasets)
        joined = "\n".join(str(row) for row in self.rows)
        self.assertNotIn("should not appear", joined)
        self.assertNotIn("noise-line", joined)
        self.assertNotIn("secret-screenshot-ref", joined)

    def test_meituan_metrics_carry_hotel_name(self) -> None:
        row = self._row(平台="美团", 指标="销售额")
        self.assertEqual(row["数值"], 8200)
        self.assertEqual(row["酒店"], "璞岸酒店")
        self.assertEqual(row["数据集"], "交易数据")
        self.assertEqual(row["读取状态"], "成功")

    def test_fliggy_sections_and_joined_activities(self) -> None:
        occupancy = self._row(平台="飞猪", 指标="入住率")
        self.assertEqual(occupancy["维度"], "在店数据")
        self.assertEqual(occupancy["数值"], "76%")
        self.assertEqual(occupancy["数据集"], "经营数据")

        activity = self._row(平台="飞猪", 指标="提前订优惠")
        self.assertEqual(activity["维度"], "已参加活动")
        self.assertIn("9折", activity["数值"])

    def test_core_rows_align_cross_platform_metrics(self) -> None:
        rows = build_core_metric_rows(self.snapshots)
        self.assertEqual([row["平台"] for row in rows], ["携程", "美团", "飞猪"])
        ctrip, meituan, fliggy = rows
        self.assertEqual(ctrip["销售额"], 12400)
        self.assertEqual(ctrip["订单量"], 86)
        self.assertEqual(ctrip["出租率"], "92%")
        self.assertEqual(ctrip["间夜量"], "")
        self.assertEqual(meituan["平均房价"], 298)
        self.assertEqual(meituan["订单量"], 64)
        self.assertEqual(fliggy["销售额"], 3100)
        self.assertEqual(fliggy["间夜量"], 30)
        self.assertEqual(fliggy["转化率"], "2.8%")

    def test_core_headers(self) -> None:
        self.assertEqual(
            CORE_HEADERS,
            [
                "快照时间",
                "平台",
                "账号",
                "数据来源",
                "销售额",
                "订单量",
                "间夜量",
                "出租率",
                "平均房价",
                "转化率",
                "城市排名",
                "PSI",
                "点评分",
            ],
        )

    def test_empty_snapshots_produce_empty_tables(self) -> None:
        self.assertEqual(extract_business_metric_rows([]), [])
        self.assertEqual(build_core_metric_rows([]), [])

    def test_operating_brief_rows_follow_official_brief_sections(self) -> None:
        snapshot = _ctrip_full_snapshot()
        snapshot["account_display_name"] = "静里携程ebk"
        rows = build_operating_brief_rows([snapshot])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["平台"], "携程")
        self.assertEqual(row["账号"], "静里携程ebk")
        self.assertEqual(row["城市排名"], 280)
        self.assertEqual(row["APP访客"], 341)
        self.assertEqual(row["PSI"], 4.91)
        self.assertEqual(row["点评分"], 4.79)
        self.assertEqual(row["本月间夜量"], 2000)
        self.assertEqual(row["本月平均卖价"], 362.83)
        self.assertEqual(row["列表页曝光量-我的酒店"], 1210)
        self.assertEqual(row["列表页曝光量-竞争圈平均"], 3216)
        self.assertEqual(row["详情页访客量-我的酒店"], 231)
        self.assertEqual(row["详情页访客量-竞争圈平均"], 484)
        self.assertEqual(row["订单页访客量-我的酒店"], 20)
        self.assertEqual(row["订单提交人数-我的酒店"], 8)

    def test_operating_brief_competition_module_uses_rank_positions_only(self) -> None:
        row = build_operating_brief_rows([_ctrip_full_snapshot()])[0]

        self.assertEqual(
            {key: row[key] for key in (
                "离店销售额排名",
                "离店间夜量排名",
                "预订订单数排名",
                "APP访客排名",
                "APP转化率排名",
                "点评分排名",
            )},
            {
                "离店销售额排名": 4,
                "离店间夜量排名": 2,
                "预订订单数排名": 14,
                "APP访客排名": 19,
                "APP转化率排名": 8,
                "点评分排名": 3,
            },
        )
        self.assertNotIn("离店销售额", OPERATING_BRIEF_HEADERS)
        self.assertNotIn("离店间夜量", OPERATING_BRIEF_HEADERS)
        self.assertNotIn("预订订单数", OPERATING_BRIEF_HEADERS)

    def test_app_funnel_headers_keep_each_hotel_and_benchmark_pair_adjacent(self) -> None:
        for label in (
            "列表页曝光量",
            "详情页访客量",
            "订单页访客量",
            "订单提交人数",
            "曝光转化率",
            "下单转化率",
            "成交转化率",
        ):
            mine = APP_CONVERSION_HEADERS.index(f"{label}-我的酒店")
            self.assertEqual(
                APP_CONVERSION_HEADERS[mine + 1],
                f"{label}-竞争圈平均",
            )

    def test_export_account_label_falls_back_to_id_when_name_is_missing(self) -> None:
        snapshot = _ctrip_full_snapshot()
        rows = build_operating_brief_rows([snapshot])
        self.assertEqual(rows[0]["账号"], "ctrip_main")

    def test_all_business_sheets_use_operator_account_name(self) -> None:
        snapshot = _ctrip_full_snapshot()
        snapshot["account_display_name"] = "静里携程ebk"
        builders = (
            extract_business_metric_rows,
            build_core_metric_rows,
            build_operating_brief_rows,
            build_app_conversion_rows,
            build_competition_rank_rows,
        )
        for builder in builders:
            with self.subTest(builder=builder.__name__):
                rows = builder([snapshot])
                self.assertTrue(rows)
                self.assertTrue(all(row["账号"] == "静里携程ebk" for row in rows))

    def test_app_conversion_rows_keep_real_funnel_values_only(self) -> None:
        rows = build_app_conversion_rows([_ctrip_full_snapshot()])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["列表页曝光量-我的酒店"], 1210)
        self.assertEqual(row["列表页曝光量-竞争圈平均"], 3216)
        self.assertEqual(row["详情页访客量-我的酒店"], 231)
        self.assertEqual(row["详情页访客量-竞争圈平均"], 484)
        self.assertEqual(row["曝光转化率-我的酒店"], "19.09%")
        self.assertEqual(row["曝光转化率-竞争圈平均"], "15.05%")

    def test_competition_rank_rows_include_only_page_provided_context(self) -> None:
        rows = build_competition_rank_rows([_ctrip_full_snapshot()])
        exposure = next(row for row in rows if row["指标"] == "列表页曝光")
        self.assertEqual(exposure["本酒店"], "1.12万")
        self.assertEqual(exposure["竞争圈平均"], "2.38万")
        self.assertEqual(exposure["竞争圈排名"], "19")


def _ctrip_full_snapshot() -> dict[str, Any]:
    """含 time_dimensions/ monthly/ 上下文字段的携程统一快照（生产结构）。"""

    return {
        "id": 2,
        "platform": "ctrip",
        "platform_account_id": "ctrip_main",
        "status": "success",
        "created_at": "2026-08-22T22:00:40",
        "payload_json": {
            "platform": "ctrip",
            "datasets": {
                "business_outline": {
                    "status": "success",
                    "data": {
                        "dataset_id": "business_outline",
                        "metrics": {
                            "psi_service_score": {
                                "label": "PSI服务质量分",
                                "value": "4.91",
                                "competitive_rank": "20/21",
                            },
                            "hotel_review_score": {"label": "酒店点评分", "value": "4.79"},
                            "departure_sales_amount": {
                                "label": "离店销售额",
                                "value": "33057.49",
                                "competition_average": "28000",
                                "competitive_rank": "4 / 21",
                            },
                            "departure_room_nights": {
                                "label": "离店间夜量",
                                "value": "89",
                                "competitive_rank": "2 / 21",
                            },
                            "list_exposure": {
                                "label": "列表页曝光量",
                                "value": 1210,
                                "competition_average": 3216,
                                "section": "APP流量转化（昨日）",
                            },
                            "app_detail_visitors": {
                                "label": "详情页访客量",
                                "value": 231,
                                "competition_average": 484,
                                "section": "APP流量转化（昨日）",
                            },
                            "order_page_visitors": {
                                "label": "订单页访客量",
                                "value": 20,
                                "competition_average": 35,
                                "section": "APP流量转化（昨日）",
                            },
                            "order_submitters": {
                                "label": "订单提交人数",
                                "value": 8,
                                "competition_average": 20,
                                "section": "APP流量转化（昨日）",
                            },
                        },
                        "primary_time_dimension": {"value": "daily", "label": "按日"},
                        "time_dimensions": {
                            "realtime": {
                                "label": "实时",
                                "metrics": {
                                    "realtime_visitor_rank": {
                                        "label": "实时排名",
                                        "value": "280",
                                        "competitive_rank": "13/21",
                                    },
                                    "realtime_booking_orders": {
                                        "label": "实时预订订单",
                                        "value": "18",
                                        "week_over_week": "-43.75%",
                                    },
                                }
                            }
                        },
                    },
                },
                "beneficial_data": {
                    "status": "success",
                    "data": {
                        "dataset_id": "beneficial_data",
                        "metrics": {
                            "booking_orders": {
                                "label": "预订订单数",
                                "value": "57",
                                "week_over_week": "-5.00%",
                            }
                        },
                        "monthly": {
                            "status": "success",
                            "metrics": {
                                "monthly_departure_room_nights": {
                                    "label": "本月间夜量",
                                    "value": "2000",
                                    "last_year_same_period": "2443",
                                    "year_over_year": "-18.13%",
                                    "section": "本月离店（按月）",
                                },
                                "monthly_average_selling_price": {
                                    "label": "本月平均卖价",
                                    "value": "362.83",
                                    "last_year_same_period": "378.1",
                                    "year_over_year": "-4.04%",
                                    "section": "本月离店（按月）",
                                },
                            },
                        },
                    },
                },
                "flow_data": {
                    "status": "success",
                    "data": {
                        "dataset_id": "flow_data",
                        "metrics": {
                            "exposure_conversion_rate": {
                                "label": "曝光转化率",
                                "value": "19.09%",
                                "competition_average": "15.05%",
                            },
                            "booking_conversion_rate": {
                                "label": "下单转化率",
                                "value": "8.66%",
                                "competition_average": "7.23%",
                            },
                            "transaction_conversion_rate": {
                                "label": "成交转化率",
                                "value": "40%",
                                "competition_average": "57.14%",
                            },
                        },
                    },
                },
                "competition_profile": {
                    "status": "success",
                    "data": {
                        "dataset_id": "competition_profile",
                        "metrics": {
                            "list_exposure": {
                                "label": "列表页曝光",
                                "value": "1.12万",
                                "change": "较去年同期56.37%",
                                "competition_average": "2.38万",
                                "competitive_rank": "19",
                            },
                            "ctrip_app_visitors": {
                                "label": "携程APP访客",
                                "value": "341",
                                "competitive_rank": "19",
                            },
                            "ctrip_app_conversion_rate": {
                                "label": "携程APP转化率",
                                "value": "4.34%",
                                "competitive_rank": "8",
                            },
                            "booking_orders": {
                                "label": "预订订单量",
                                "value": "57",
                                "competitive_rank": "14",
                            },
                            "ctrip_review_score": {
                                "label": "携程点评分",
                                "value": "4.7",
                                "competitive_rank": "3",
                            },
                            "psi": {
                                "label": "PSI",
                                "value": "5.01",
                                "change": "较上周同期8.07%",
                                "competitive_rank": "20",
                                "competitive_rank_change": "13",
                            },
                        },
                    },
                },
            },
        },
    }


class EbkBusinessMetricsExtendedExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = extract_business_metric_rows([_ctrip_full_snapshot()])

    def _row(self, **filters: Any) -> dict[str, Any]:
        for row in self.rows:
            if all(str(row.get(k)) == str(v) for k, v in filters.items()):
                return row
        raise AssertionError(f"row not found: {filters}\n{self.rows}")

    def test_detail_expands_realtime_dimension_metrics(self) -> None:
        rank = self._row(指标="实时排名")
        self.assertEqual(rank["数值"], 280)
        self.assertEqual(rank["竞争圈排名"], "13/21")
        self.assertEqual(rank["数据口径"], "按日 · 实时")

    def test_detail_expands_monthly_block_with_year_over_year(self) -> None:
        monthly = self._row(指标="本月间夜量")
        self.assertEqual(monthly["数值"], 2000)
        self.assertEqual(monthly["同比"], "-18.13%")
        self.assertIn("本月离店", monthly["维度"])
        self.assertIn("本月", monthly["数据口径"])

    def test_detail_metric_context_columns_are_populated(self) -> None:
        psi = self._row(指标="PSI")
        self.assertEqual(psi["环比"], "较上周同期8.07%")
        self.assertEqual(psi["竞争圈排名"], "20")
        self.assertEqual(psi["排名环比"], "13")

        exposure = self._row(指标="列表页曝光")
        self.assertEqual(exposure["同比"], "较去年同期56.37%")
        self.assertEqual(exposure["竞争圈平均"], "2.38万")

    def test_core_rows_include_city_rank_psi_and_review_score(self) -> None:
        rows = build_core_metric_rows([_ctrip_full_snapshot()])
        self.assertEqual(len(rows), 1)
        ctrip = rows[0]
        self.assertEqual(ctrip["城市排名"], 280)
        self.assertEqual(ctrip["PSI"], 4.91)
        self.assertEqual(ctrip["点评分"], 4.79)


if __name__ == "__main__":
    unittest.main()
