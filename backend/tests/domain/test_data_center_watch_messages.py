from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from typing import Any

from app.domain.ctrip_ebooking_watch.messages import (
    build_daily_brief_push_card,
    build_data_center_watch_push_card,
    build_data_center_watch_push_text,
    compute_daily_brief_day_over_day,
)
from app.domain.ctrip_ebooking_watch import messages as messages_module
from app.domain.ctrip_ebooking_watch.repository import (
    DataCenterWatchDatasetRuntime,
    DataCenterWatchGroupRuntime,
)
from app.domain.ctrip_ebooking_watch.service import _push_to_feishu, _summary


class DataCenterWatchMessagesTests(unittest.TestCase):
    def test_long_metric_values_split_into_scrollable_detail_column(self) -> None:
        table = messages_module._field_value_table(
            [{"field": "service", "value": "5.35\nrank 8 / 21"}],
            page_size=1,
        )[0]

        self.assertGreaterEqual(len(table["columns"]), 3)
        self.assertEqual(table["rows"][0]["value"], "5.35")
        self.assertEqual(table["rows"][0]["detail"], "rank 8 / 21")
        total_width = sum(int(column["width"].removesuffix("px")) for column in table["columns"])
        self.assertGreaterEqual(total_width, 480)

    def test_over_ten_rows_split_into_stacked_non_paging_tables(self) -> None:
        """数据多的段落全部表格化：>10 行拆成多个 ≤10 行的堆叠表格，不翻页。"""

        rows = [{"field": f"metric-{index}", "value": str(index)} for index in range(12)]

        elements = messages_module._field_value_table(rows)

        tables = [element for element in elements if element.get("tag") == "table"]
        self.assertEqual([len(t["rows"]) for t in tables], [10, 2])
        for table in tables:
            self.assertEqual(table["page_size"], len(table["rows"]))
        seen = {row["field"] for table in tables for row in table["rows"]}
        self.assertEqual(seen, {f"metric-{index}" for index in range(12)})

        # 表格配额耗尽后剩余行才回退一列文本。
        budgeted = messages_module._field_value_table(rows, remaining_tables=1)
        budget_tables = [e for e in budgeted if e.get("tag") == "table"]
        budget_text = [e for e in budgeted if e.get("tag") == "div"]
        self.assertEqual(len(budget_tables), 1)
        self.assertEqual(len(budget_text), 1)
        self.assertIn("metric-11", budget_text[0]["text"]["content"])

    def test_dense_business_outline_renders_field_value_table_without_paging(self) -> None:
        metrics = {
            f"metric_{index}": {"label": f"metric-{index}", "value": str(index)}
            for index in range(6)
        }
        card = build_data_center_watch_push_card(
            watch_group_key="dc_default",
            status="success",
            summary="ok",
            dataset_results=[
                {
                    "dataset_id": "business_outline",
                    "dataset_label": "收益概览",
                    "status": "success",
                    "data": {"dataset": "business_outline", "metrics": metrics},
                }
            ],
        )

        tables = _table_elements(card)
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table["page_size"], 6)
        self.assertEqual(len(table["rows"]), 6)
        # 不再把「指标：值」合并进单个单元格；字段不带数据集前缀。
        self.assertEqual(table["rows"][0], {"field": "metric-0", "value": "0"})
        self.assertTrue(
            all("content" not in row for row in table["rows"])
        )

    def test_push_card_has_mobile_scan_summary_and_status_icon(self) -> None:
        cards = build_data_center_watch_push_card(
            watch_group_key="dc_default",
            status="success",
            summary="本次读取完成。",
            dataset_results=[
                {
                    "dataset_id": "promotion_effect",
                    "dataset_label": "促销效果",
                    "status": "success",
                    "data": {
                        "dataset": "promotion_effect",
                        "summary_metrics": {
                            "occupancy": {"label": "出租率", "value": "82%"}
                        },
                    },
                }
            ],
            title="静里 EBK 数据中心盯盘",
            captured_at="2026-08-22T10:20:00+08:00",
        )
        card = cards[0]
        text = _all_text(card)
        self.assertEqual(card["header"]["title"]["content"], "携程 EBK 数据中心盯盘")
        self.assertIn("🏠", text)
        self.assertIn("✅", text)
        self.assertIn("读取时间", text)
        self.assertIn("出租率", text)
        self.assertIn("table", _tags(card))

    def test_partial_push_card_uses_original_chinese_business_layout(self) -> None:
        cards = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="partial",
            summary="Ctrip EBK data-center watch partially completed: 5/9 datasets succeeded.",
            dataset_results=SAMPLE_DATASET_RESULTS,
            title="澜江路 EBK 数据中心盯盘",
            captured_at="2026-06-18T08:05:23",
        )
        text = build_data_center_watch_push_text(
            watch_group_key="ctrip_ebooking_data_center",
            status="partial",
            summary="Ctrip EBK data-center watch partially completed: 5/9 datasets succeeded.",
            dataset_results=SAMPLE_DATASET_RESULTS,
            title="澜江路 EBK 数据中心盯盘",
            captured_at="2026-06-18T08:05:23",
        )

        card_text = _all_text(cards)
        self.assertTrue(
            cards[0]["header"]["title"]["content"].startswith("携程 EBK 数据中心盯盘")
        )
        self.assertIn("（1/2）", cards[0]["header"]["title"]["content"])
        self.assertIn("来源**：ctrip\\_ebooking\\_data\\_center", card_text)
        self.assertIn("结果**：Ctrip EBK data-center watch partially completed", card_text)
        self.assertIn("读取时间**：2026-06-18 08:05", card_text)
        self.assertNotIn("数据集**：9", card_text)
        self.assertNotIn("成功**：5", card_text)
        self.assertIn("天天特价", card_text)
        self.assertIn("已参加促销", card_text)
        self.assertIn("广告金字塔", card_text)
        # 广告金字塔层级进「板块」列，字段只留指标名。
        self.assertIn("流量", card_text)
        self.assertIn("点击", card_text)
        self.assertIn("table", _tags(cards))
        self.assertNotIn("Pan Hotel", card_text)

        self.assertIn("澜江路 EBK 数据中心盯盘", text)
        self.assertIn("读取时间：2026-06-18 08:05", text)
        self.assertIn("状态：部分成功", text)
        self.assertIn("数据集：9，成功：5", text)
        self.assertIn("促销效果", text)
        self.assertNotIn("Pan Hotel", text)
        self.assertNotIn("partially completed", text)

    def test_full_watch_card_keeps_all_ten_configured_datasets_visible(self) -> None:
        dataset_results = [
            {
                "dataset_id": dataset_id,
                "dataset_label": dataset_label,
                "status": "success",
                "data": {
                    "dataset": dataset_id,
                    "metrics": {"metric": {"label": "指标", "value": "1"}},
                },
            }
            for dataset_id, dataset_label in (
                ("business_outline", "收益概览"),
                ("beneficial_data", "销售数据"),
                ("flow_data", "流量数据"),
                ("competition_profile", "经营对比"),
                ("loss_analysis", "流失订单"),
                ("competition_list", "销售排名"),
                ("promotion_effect", "促销效果"),
                ("promotion_registered", "已参加促销"),
                ("ad_pyramid", "广告金字塔"),
                ("comment_monitor", "点评分析"),
            )
        ]

        cards = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 10/10。",
            dataset_results=dataset_results,
            captured_at="2026-08-25T17:00:00+08:00",
        )

        # 10 个数据集每个独立小节独立表格：一张卡放不下时拆多张卡连续推送，
        # 全部数据集可见、无截断提示；每张卡守住飞书 ≤5 表格限制。
        card_text = _all_text(cards)
        for label in (
            "收益概览",
            "销售数据",
            "流量数据",
            "经营对比",
            "流失订单",
            "销售排名",
            "促销效果",
            "已参加促销",
            "广告金字塔",
            "点评分析",
        ):
            self.assertIn(label, card_text)
        self.assertGreater(len(cards), 1)
        for card in cards:
            self.assertLessEqual(sum(tag == "table" for tag in _tags(card)), 5)
        self.assertNotIn("未展示", card_text)
        self.assertNotIn("另有", card_text)

    def test_pyramid_bid_dataset_renders_campaign_table(self) -> None:
        """2026-09-10 需求：独立 EBK 推送新增「金字塔出价」数据集小节，表格化。

        列契约与指令「金字塔出价」卡一致：推广计划 | 点击出价 | 今日预算 |
        今日花费 | 状态；复用共享渲染器（≤10 行/表不翻页）。
        """
        cards = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 1/1。",
            dataset_results=[
                {
                    "dataset_id": "pyramid_bid",
                    "dataset_label": "金字塔出价",
                    "status": "success",
                    "data": {
                        "dataset": "pyramid_bid",
                        "promotions": [
                            {
                                "campaign_name": "周内0-2",
                                "campaign_status": "推广中",
                                "click_bid_cny": 6.5,
                                "daily_budget_cny": 300.0,
                                "today_spend_cny": 45.2,
                            },
                            {
                                "campaign_name": "智能计划",
                                "campaign_status": "推广中",
                                "click_bid_cny": None,
                                "daily_budget_cny": None,
                                "today_spend_cny": 0.0,
                            },
                        ],
                    },
                }
            ],
            captured_at="2026-09-10T17:00:00+08:00",
        )
        text = _all_text(cards)
        self.assertIn("金字塔出价", text)
        tables = [table for card in cards for table in _table_elements(card)]
        self.assertEqual(len(tables), 1)
        headers = [
            column["display_name"] for column in tables[0]["columns"]
        ]
        self.assertEqual(
            headers, ["推广计划", "点击出价", "今日预算", "今日花费", "状态"]
        )
        first = tables[0]["rows"][0]
        self.assertEqual(first["field"], "周内0-2")
        self.assertEqual(first["value"], "¥6.5")
        self.assertEqual(first["detail"], "¥300")
        self.assertEqual(first["detail_2"], "¥45.2")
        self.assertEqual(first["detail_3"], "推广中")
        smart = tables[0]["rows"][1]
        self.assertEqual(smart["value"], "-")

    def test_pyramid_bid_failed_dataset_renders_status_note_only(self) -> None:
        """金字塔出价读取失败：小节保留并如实展示读取状态，不虚构计划行。"""
        cards = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="partial",
            summary="EBK 数据中心读取部分成功：成功 0/1。",
            dataset_results=[
                {
                    "dataset_id": "pyramid_bid",
                    "dataset_label": "金字塔出价",
                    "status": "human_required",
                    "summary": "需要人工登录。",
                    "data": {},
                }
            ],
            captured_at="2026-09-10T17:00:00+08:00",
        )
        text = _all_text(cards)
        self.assertIn("金字塔出价", text)
        self.assertIn("需要人工处理", text)
        for card in cards:
            for table in _table_elements(card):
                for row in table["rows"]:
                    self.assertNotEqual(row.get("field"), "推广计划")

    def test_full_watch_card_keeps_pyramid_bid_visible_beyond_ten_datasets(self) -> None:
        """新增第 11 个数据集（金字塔出价）后不得被卡片小节数量上限截断。"""
        dataset_results = [
            {
                "dataset_id": dataset_id,
                "dataset_label": dataset_label,
                "status": "success",
                "data": {
                    "dataset": dataset_id,
                    "metrics": {"metric": {"label": "指标", "value": "1"}},
                },
            }
            for dataset_id, dataset_label in (
                ("business_outline", "收益概览"),
                ("beneficial_data", "销售数据"),
                ("flow_data", "流量数据"),
                ("competition_profile", "经营对比"),
                ("loss_analysis", "流失订单"),
                ("competition_list", "销售排名"),
                ("promotion_effect", "促销效果"),
                ("promotion_registered", "已参加促销"),
                ("ad_pyramid", "广告金字塔"),
                ("comment_monitor", "点评分析"),
            )
        ]
        dataset_results.append(
            {
                "dataset_id": "pyramid_bid",
                "dataset_label": "金字塔出价",
                "status": "success",
                "data": {
                    "dataset": "pyramid_bid",
                    "promotions": [
                        {
                            "campaign_name": "周内0-2",
                            "campaign_status": "推广中",
                            "click_bid_cny": 6.5,
                            "daily_budget_cny": 300.0,
                            "today_spend_cny": 45.2,
                        }
                    ],
                },
            }
        )
        cards = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 11/11。",
            dataset_results=dataset_results,
            captured_at="2026-09-10T17:00:00+08:00",
        )
        text = _all_text(cards)
        for label in ("收益概览", "点评分析", "金字塔出价"):
            self.assertIn(label, text)
        self.assertIn("周内0-2", str(cards))
        for card in cards:
            self.assertLessEqual(sum(tag == "table" for tag in _tags(card)), 5)

    def test_generic_metric_block_renders_item_only_field_rows(self) -> None:
        cards = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 1/1。",
            dataset_results=[
                {
                    "dataset_id": "business_outline",
                    "dataset_label": "收益概览",
                    "status": "success",
                    "data": {
                        "dataset": "business_outline",
                        "metrics": {
                            "revenue": {"label": "离店销售额", "value": "12,300 元"},
                            "nights": {"label": "离店间夜量", "value": "56"},
                            "orders": {"label": "预订订单数", "value": "40"},
                        },
                    },
                }
            ],
        )

        tables = _table_elements(cards)
        self.assertEqual(len(tables), 1)
        # 整表横向滑动：不冻结首列；行字段只保留具体条目，不再拼数据集前缀。
        self.assertFalse(tables[0].get("freeze_first_column"))
        self.assertEqual(tables[0]["rows"][0], {"field": "离店销售额", "value": "12,300 元"})
        self.assertNotIn("收益·", str(tables[0]["rows"]))
        self.assertNotIn("收益概览·", str(tables[0]["rows"]))

    def test_all_ctrip_metric_tables_slide_as_a_whole(self) -> None:
        cards = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 1/1。",
            dataset_results=[
                {
                    "dataset_id": "ad_pyramid",
                    "dataset_label": "广告金字塔",
                    "status": "success",
                    "data": {
                        "dataset": "ad_pyramid",
                        "ad_pyramid": {
                            "groups": [
                                {"label": "流量", "metrics": [{"label": "曝光量", "value": "1,669"}]}
                            ]
                        },
                    },
                }
            ],
        )

        tables = _table_elements(cards)
        self.assertTrue(tables)
        self.assertTrue(all(table.get("freeze_first_column") is False for table in tables))

    def test_fallback_text_can_omit_title_to_avoid_duplicate_feishu_title(self) -> None:
        title = "携程静里全套房酒店（云州澜江路望川路地铁站店）EBK盯盘"

        text = build_data_center_watch_push_text(
            watch_group_key="ctrip_ebooking_data_center",
            status="partial",
            summary="Ctrip EBK data-center watch partially completed: 5/9 datasets succeeded.",
            dataset_results=SAMPLE_DATASET_RESULTS,
            title=title,
            captured_at="2026-06-18T08:05:23",
            include_title=False,
        )

        feishu_fallback_body = f"{title}\n\n{text}"
        self.assertEqual(feishu_fallback_body.count(title), 1)
        self.assertTrue(feishu_fallback_body.startswith(title))
        self.assertIn("读取时间：2026-06-18 08:05", feishu_fallback_body)

    def test_push_without_webhook_returns_brief_chinese_config_message(self) -> None:
        group = DataCenterWatchGroupRuntime(
            group_key="ctrip_ebooking_data_center",
            name="携程 EBK 数据中心盯盘",
            brief_title="携程 EBK 数据中心盯盘",
            platform_account_id="ctrip-ebk",
            browser_session_id="browser-ebk",
            feishu_webhook_url=None,
            feishu_chat_id=None,
            timezone="Asia/Shanghai",
            retention_days=14,
            query_interval_minutes=60,
            auto_poll_enabled=False,
            enabled=True,
            last_polled_at=None,
            datasets=[
                DataCenterWatchDatasetRuntime(
                    dataset_id="business_outline",
                    dataset_label="收益概览",
                    enabled=True,
                    push_enabled=True,
                    sort_order=0,
                )
            ],
        )

        result = asyncio.run(
            _push_to_feishu(
                group,
                "partial",
                "Ctrip EBK data-center watch partially completed: 5/9 datasets succeeded.",
                SAMPLE_DATASET_RESULTS,
            )
        )

        self.assertEqual(result["status"], "config_required")
        self.assertEqual(result["summary"], "未配置飞书机器人地址，已保存本次读取结果。")
        self.assertIn("飞书机器人地址", result["next_action"])
        self.assertNotIn("No Feishu", str(result))
        self.assertNotIn("Configure", str(result))
        self.assertNotIn("webhook URL", str(result))

    def test_service_summary_is_operator_chinese(self) -> None:
        summary = _summary("partial", SAMPLE_DATASET_RESULTS)

        self.assertEqual(summary, "EBK 数据中心部分成功：成功 5/9。")
        self.assertNotIn("Ctrip EBK data-center", summary)
        self.assertNotIn("datasets", summary)

    def test_push_text_filters_repeated_promotion_status_count_prompts(self) -> None:
        dataset_results = [
            {
                "dataset_id": "promotion_registered",
                "dataset_label": "已参加促销",
                "status": "success",
                "data": {
                    "dataset": "promotion_registered",
                    "summary": {"visible_promotion_count": 1},
                    "warnings": ["已暂停 0"],
                    "page_prompts": ["已暂停 0", "余额不足，请及时充值"],
                    "promotions": [
                        {
                            "name": "天天特价",
                            "promotion_group": "基础促销组",
                            "status": "生效中",
                        }
                    ],
                },
                "push_enabled": True,
            }
        ]

        text = build_data_center_watch_push_text(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="ok",
            dataset_results=dataset_results,
            title="携程 EBK 数据中心盯盘",
            captured_at="2026-06-18T08:05:23",
        )

        self.assertNotIn("提示：已暂停 0", text)
        self.assertEqual(text.count("余额不足，请及时充值"), 1)

    def test_push_card_and_text_include_service_overview_and_review_analysis_metrics(self) -> None:
        cards = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 1/1。",
            dataset_results=[
                {
                    "dataset_id": "comment_monitor",
                    "dataset_label": "点评分析",
                    "status": "success",
                    "data": {
                        "query_type": "ctrip_ebooking_review_analysis",
                        "comment_monitor": {
                            "review_overview": {
                                "overall_score": {
                                    "label": "点评分",
                                    "value": "4.7",
                                    "competitive_rank": "超过34%同城同星酒店",
                                },
                                "total_comment_count": {"label": "点评数", "value": "2485"},
                                "positive_rate": {"label": "好评率", "value": "98.90%"},
                                "image_review_rate": {"label": "带图点评率", "value": "19.50%"},
                                "positive_reviews_30d": {"label": "30天内好评", "value": "37"},
                                "positive_reviews_yesterday": {"label": "昨日好评", "value": "1"},
                                "negative_reviews_30d": {"label": "30天内差评", "value": "2"},
                                "negative_reviews_yesterday": {"label": "昨日差评", "value": "0"},
                            },
                            "reply_overview": {
                                "reply_rate": {"label": "回复率", "value": "100.00%"},
                                "negative_reply_rate": {"label": "差评回复率", "value": "100.00%"},
                                "pending_negative_reviews": {"label": "待回复差评数", "value": "0"},
                            },
                        },
                    },
                    "push_enabled": True,
                },
                {
                    "dataset_id": "business_outline",
                    "dataset_label": "收益概览",
                    "status": "success",
                    "data": {
                        "dataset": "business_outline",
                        "updated_at": "2026/06/23的数据",
                        "metrics": {
                            "psi_service_score": {
                                "label": "PSI服务质量分",
                                "value": "5.57",
                                "competitive_rank": "8 / 21",
                            },
                            "hotel_review_score": {
                                "label": "酒店点评分",
                                "value": "4.78",
                            },
                            "five_minute_response_rate": {
                                "label": "5分钟回复率",
                                "value": "95.81%",
                                "competitive_rank": "16 / 21",
                            },
                            "hotel_favorites": {
                                "label": "酒店收藏数",
                                "value": "20334",
                                "competitive_rank": "12 / 21",
                            },
                        },
                    },
                    "push_enabled": True,
                }
            ],
            captured_at="2026-06-23T08:10:00+08:00",
        )
        text = build_data_center_watch_push_text(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 1/1。",
            dataset_results=[
                {
                    "dataset_id": "comment_monitor",
                    "dataset_label": "点评分析",
                    "status": "success",
                    "data": {
                        "query_type": "ctrip_ebooking_review_analysis",
                        "comment_monitor": {
                            "review_overview": {
                                "overall_score": {
                                    "label": "点评分",
                                    "value": "4.7",
                                    "competitive_rank": "超过34%同城同星酒店",
                                },
                                "total_comment_count": {"label": "点评数", "value": "2485"},
                                "positive_rate": {"label": "好评率", "value": "98.90%"},
                                "image_review_rate": {"label": "带图点评率", "value": "19.50%"},
                                "positive_reviews_30d": {"label": "30天内好评", "value": "37"},
                                "positive_reviews_yesterday": {"label": "昨日好评", "value": "1"},
                                "negative_reviews_30d": {"label": "30天内差评", "value": "2"},
                                "negative_reviews_yesterday": {"label": "昨日差评", "value": "0"},
                            },
                            "reply_overview": {
                                "reply_rate": {"label": "回复率", "value": "100.00%"},
                                "negative_reply_rate": {"label": "差评回复率", "value": "100.00%"},
                                "pending_negative_reviews": {"label": "待回复差评数", "value": "0"},
                            },
                        },
                    },
                    "push_enabled": True,
                },
                {
                    "dataset_id": "business_outline",
                    "dataset_label": "收益概览",
                    "status": "success",
                    "data": {
                        "dataset": "business_outline",
                        "updated_at": "2026/06/23的数据",
                        "metrics": {
                            "psi_service_score": {
                                "label": "PSI服务质量分",
                                "value": "5.57",
                                "competitive_rank": "8 / 21",
                            },
                            "hotel_review_score": {
                                "label": "酒店点评分",
                                "value": "4.78",
                            },
                            "five_minute_response_rate": {
                                "label": "5分钟回复率",
                                "value": "95.81%",
                                "competitive_rank": "16 / 21",
                            },
                            "hotel_favorites": {
                                "label": "酒店收藏数",
                                "value": "20334",
                                "competitive_rank": "12 / 21",
                            },
                        },
                    },
                    "push_enabled": True,
                }
            ],
            captured_at="2026-06-23T08:10:00+08:00",
        )

        card_text = _all_text(cards)
        # 不再合并「服务与点评速览」：每个数据集独立小节，指标回各自数据集表。
        self.assertNotIn("服务与点评速览", card_text)
        self.assertNotIn("📊 服务概览", card_text)
        # 数据集小节标题带图标与状态（💰 收益概览 / 💬 点评分析），不再统一 📦。
        self.assertIn("💰 收益概览", card_text)
        self.assertIn("💬 点评分析", card_text)
        self.assertNotIn("📦", card_text)
        self.assertIn("PSI服务质量分", card_text)
        self.assertIn("5.57", card_text)
        self.assertIn("竞争圈排名 8 / 21", card_text)
        self.assertIn("昨日好评", card_text)
        self.assertNotIn("跟进·", card_text)
        self.assertIn("回复率", card_text)
        self.assertIn("带图点评率", card_text)
        self.assertNotIn("点评·带图点评率", card_text)
        self.assertIn("服务概览", text)
        self.assertIn("PSI服务质量分：5.57", text)
        self.assertIn("昨日好评：1", text)
        self.assertIn("昨日差评：0", text)
        self.assertIn("点评分：4.7", text)
        self.assertIn("带图点评率：19.50%", text)

    def test_push_text_includes_both_section_labels(self) -> None:
        text = build_data_center_watch_push_text(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="ok",
            dataset_results=[
                {
                    "dataset_id": "business_outline",
                    "dataset_label": "收益概览",
                    "status": "success",
                    "data": {
                        "dataset": "business_outline",
                        "metrics": {"occupancy_rate": {"label": "出租率", "value": "30%"}},
                        "time_dimensions": {
                            "realtime": {
                                "label": "实时",
                                "status": "success",
                                "metrics": {
                                    "realtime_booking_orders": {
                                        "label": "实时预订订单",
                                        "value": "10",
                                    }
                                },
                            },
                            "daily": {
                                "label": "按日",
                                "status": "success",
                                "metrics": {
                                    "occupancy_rate": {"label": "出租率", "value": "30%"}
                                },
                            },
                        },
                    },
                }
            ],
        )
        self.assertIn("实时", text)
        self.assertIn("按日", text)
        self.assertIn("实时预订订单：10", text)
        self.assertIn("出租率：30%", text)

    def test_failed_dimension_shows_status_note_without_invented_metrics(self) -> None:
        dataset_results = [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "partial",
                "data": {
                    "dataset": "business_outline",
                    "metrics": {"occupancy_rate": {"label": "出租率", "value": "30%"}},
                    "time_dimensions": {
                        "realtime": {
                            "label": "实时",
                            "status": "success",
                            "metrics": {
                                "realtime_booking_orders": {
                                    "label": "实时预订订单",
                                    "value": "10",
                                }
                            },
                        },
                        "daily": {
                            "label": "按日",
                            "status": "filter_partial",
                            "metrics": {
                                "occupancy_rate": {
                                    "label": "出租率",
                                    "value": "99%",
                                }
                            },
                        },
                    },
                },
            }
        ]
        text = build_data_center_watch_push_text(
            watch_group_key="ctrip_ebooking_data_center",
            status="partial",
            summary="ok",
            dataset_results=dataset_results,
        )
        card = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="partial",
            summary="ok",
            dataset_results=dataset_results,
        )
        card_text = _all_text(card)

        self.assertIn("按日档未能读取", text)
        self.assertIn("filter_partial", text)
        self.assertNotIn("出租率：99%", text)
        self.assertIn("实时预订订单：10", text)
        self.assertIn("按日档未能读取", card_text)
        self.assertNotIn("出租率：99%", card_text)

    def test_projected_time_dimension_skips_dual_section_headers(self) -> None:
        dataset_results = [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "success",
                "data": {
                    "dataset": "business_outline",
                    "projected_time_dimension": "realtime",
                    "metrics": {
                        "realtime_booking_orders": {
                            "label": "实时预订订单",
                            "value": "10",
                        }
                    },
                    "time_dimensions": {
                        "realtime": {
                            "label": "实时",
                            "status": "success",
                            "metrics": {
                                "realtime_booking_orders": {
                                    "label": "实时预订订单",
                                    "value": "10",
                                }
                            },
                        },
                        "daily": {
                            "label": "按日",
                            "status": "success",
                            "metrics": {
                                "occupancy_rate": {"label": "出租率", "value": "30%"}
                            },
                        },
                    },
                },
            }
        ]
        text = build_data_center_watch_push_text(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="ok",
            dataset_results=dataset_results,
        )

        self.assertIn("实时预订订单：10", text)
        self.assertNotIn("【实时】", text)
        self.assertNotIn("【按日】", text)
        self.assertNotIn("出租率：30%", text)

    def test_dual_dimension_metrics_not_truncated_in_push_and_card(self) -> None:
        many_metrics = {
            f"metric_{index}": {"label": f"指标{index}", "value": str(index)}
            for index in range(1, 8)
        }
        dataset_results = [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "success",
                "data": {
                    "dataset": "business_outline",
                    "metrics": {"occupancy_rate": {"label": "出租率", "value": "30%"}},
                    "time_dimensions": {
                        "realtime": {
                            "label": "实时",
                            "status": "success",
                            "metrics": many_metrics,
                        },
                        "daily": {
                            "label": "按日",
                            "status": "success",
                            "metrics": {
                                "occupancy_rate": {"label": "出租率", "value": "30%"}
                            },
                        },
                    },
                },
            }
        ]
        text = build_data_center_watch_push_text(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="ok",
            dataset_results=dataset_results,
        )
        card = build_data_center_watch_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="ok",
            dataset_results=dataset_results,
        )
        card_text = _all_text(card)

        self.assertIn("指标7：7", text)
        self.assertIn("【按日】", text)
        self.assertIn("出租率：30%", text)
        # 卡片侧：指标7 与数值以表格行展示（不再有重复的「兼容阅读」文本块）。
        self.assertIn("指标7", card_text)
        self.assertIn("出租率", card_text)
        self.assertNotIn("兼容阅读", card_text)


SAMPLE_DATASET_RESULTS: list[dict[str, Any]] = [
    {
        "dataset_id": "business_outline",
        "dataset_label": "收益概览",
        "status": "success",
        "data": {
            "dataset": "business_outline",
            "metrics": {
                "today_revenue": {"label": "今日收益", "display_value": "¥12,300"},
            },
        },
        "push_enabled": True,
    },
    {
        "dataset_id": "beneficial_data",
        "dataset_label": "销售数据",
        "status": "success",
        "data": {
            "dataset": "beneficial_data",
            "metrics": {
                "booking_orders": {"label": "预订订单数", "display_value": "40"},
            },
        },
        "push_enabled": True,
    },
    {
        "dataset_id": "promotion_effect",
        "dataset_label": "促销效果",
        "status": "success",
        "data": {
            "dataset": "promotion_effect",
            "summary_metrics": {
                "participated_promotions": {
                    "label": "参加促销",
                    "display_value": "2/14",
                    "competition_average": "4",
                }
            },
            "promotion_metrics": [
                {
                    "name": "天天特价",
                    "metrics": {
                        "room_nights": "196",
                        "room_night_share": "80%",
                        "revenue": "117746",
                        "revenue_share": "84.59%",
                        "discount_amount": "16589",
                    },
                }
            ],
        },
        "push_enabled": True,
    },
    {
        "dataset_id": "promotion_registered",
        "dataset_label": "已参加促销",
        "status": "success",
        "data": {
            "dataset": "promotion_registered",
            "summary": {"visible_promotion_count": 1},
            "promotions": [
                {
                    "promotion_group": "基础促销组",
                    "name": "天天特价",
                    "room_types": "预商务大床房",
                    "promotion_time": "2026年5月25日-2027年5月25日",
                    "auto_extend": True,
                    "room_quota": "不限",
                    "promotion_offer": "立减16元",
                    "order_reserve_fund": "使用，余额4640.28元",
                }
            ],
        },
        "push_enabled": True,
    },
    {
        "dataset_id": "ad_pyramid",
        "dataset_label": "广告金字塔",
        "status": "success",
        "data": {
            "dataset": "ad_pyramid",
            "ad_pyramid": {
                "groups": [
                    {
                        "label": "流量",
                        "metrics": [
                            {"key": "clicks", "label": "点击", "value": "40"},
                            {"key": "impressions", "label": "曝光", "value": "478"},
                        ],
                    }
                ]
            },
        },
        "push_enabled": True,
    },
    {
        "dataset_id": "flow_data",
        "dataset_label": "流量数据",
        "status": "failed",
        "failure_reason": "页面结构变化",
        "data": {"dataset": "flow_data"},
        "push_enabled": True,
    },
    {
        "dataset_id": "competition_profile",
        "dataset_label": "经营对比",
        "status": "needs_review",
        "failure_reason": "缺少关键指标",
        "data": {"dataset": "competition_profile"},
        "push_enabled": True,
    },
    {
        "dataset_id": "loss_analysis",
        "dataset_label": "流失订单",
        "status": "failed",
        "failure_reason": "读取失败",
        "data": {"dataset": "loss_analysis"},
        "push_enabled": True,
    },
    {
        "dataset_id": "competition_list",
        "dataset_label": "销售排名",
        "status": "failed",
        "failure_reason": "读取失败",
        "data": {"dataset": "competition_list"},
        "push_enabled": True,
    },
]


def _tags(value: Any) -> list[str]:
    tags: list[str] = []
    if isinstance(value, dict):
        tag = value.get("tag")
        if isinstance(tag, str):
            tags.append(tag)
        for child in value.values():
            tags.extend(_tags(child))
    elif isinstance(value, list):
        for child in value:
            tags.extend(_tags(child))
    return tags


def _table_elements(value: Any) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("tag") == "table":
            tables.append(value)
        for child in value.values():
            tables.extend(_table_elements(child))
    elif isinstance(value, list):
        for child in value:
            tables.extend(_table_elements(child))
    return tables


def _all_text(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(_all_text(child) for child in value.values())
    if isinstance(value, list):
        return "\n".join(_all_text(child) for child in value)
    return str(value)


def _daily_brief_dataset_results(*, include_monthly: bool = True) -> list[dict[str, Any]]:
    beneficial_data: dict[str, Any] = {
        "dataset": "beneficial_data",
        "metrics": {
            "booking_orders": {"label": "预订订单数", "value": "57", "section": "预订"},
        },
    }
    if include_monthly:
        beneficial_data["monthly"] = {
            "status": "success",
            "metrics": {
                "monthly_departure_room_nights": {
                    "label": "本月间夜量",
                    "value": "2000",
                    "last_year_same_period": "2443",
                    "year_over_year": "-18.13%",
                    "section": "本月离店（按月）",
                },
                "monthly_departure_sales_amount": {
                    "label": "本月销售额",
                    "value": "72.57万",
                    "last_year_same_period": "92.37万",
                    "year_over_year": "-21.44%",
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
        }
    return [
        {
            "dataset_id": "business_outline",
            "dataset_label": "收益概览",
            "status": "success",
            "data": {
                "dataset": "business_outline",
                "metrics": {
                    "realtime_visitor_rank": {
                        "label": "实时排名",
                        "value": "280",
                        "competitive_rank": "13/21",
                    },
                    "departure_sales_amount": {
                        "label": "离店销售额",
                        "value": "33057.49",
                        "competitive_rank": "8/21",
                    },
                    "departure_room_nights": {
                        "label": "离店间夜量",
                        "value": "89",
                        "competitive_rank": "5/21",
                    },
                    "psi_service_score": {
                        "label": "PSI服务质量分",
                        "value": "4.91",
                        "competitive_rank": "20/21",
                    },
                    "hotel_review_score": {"label": "酒店点评分", "value": "4.79"},
                    "five_minute_response_rate": {
                        "label": "5分钟回复率",
                        "value": "96.8%",
                        "competitive_rank": "8/21",
                    },
                    "hotel_favorites": {
                        "label": "酒店收藏数",
                        "value": "21466",
                        "competitive_rank": "11/21",
                    },
                },
            },
            "push_enabled": True,
        },
        {
            "dataset_id": "beneficial_data",
            "dataset_label": "预订销售数据",
            "status": "success",
            "data": beneficial_data,
            "push_enabled": True,
        },
        {
            "dataset_id": "flow_data",
            "dataset_label": "流量数据",
            "status": "success",
            "data": {
                "dataset": "flow_data",
                "metrics": {
                    "exposure_conversion_rate": {
                        "label": "曝光转化率",
                        "value": "20.21%",
                        "competition_average": "14.66%",
                    },
                    "booking_conversion_rate": {
                        "label": "下单转化率",
                        "value": "10.20%",
                        "competition_average": "7.05%",
                    },
                    "transaction_conversion_rate": {
                        "label": "成交转化率",
                        "value": "48.00%",
                        "competition_average": "53.13%",
                    },
                },
            },
            "push_enabled": True,
        },
        {
            "dataset_id": "competition_profile",
            "dataset_label": "竞争画像",
            "status": "success",
            "data": {
                "dataset": "competition_profile",
                "metrics": {
                    "ctrip_app_visitors": {
                        "label": "携程APP访客",
                        "value": "326",
                        "competition_average": "621",
                        "change": "较上周同期29.28%",
                    },
                    "ctrip_app_conversion_rate": {
                        "label": "携程APP转化率",
                        "value": "4.60%",
                        "competition_average": "4.38%",
                    },
                    "list_exposure": {
                        "label": "列表页曝光",
                        "value": "1.12万",
                        "competition_average": "2.38万",
                        "change": "较去年同期56.37%",
                    },
                    "app_detail_visitors": {
                        "label": "APP详情页访客",
                        "value": "2,148",
                        "competition_average": "3,746",
                        "change": "较去年同期36.97%",
                    },
                    "order_page_visitors": {
                        "label": "订单页访客",
                        "value": "153",
                        "competition_average": "272",
                        "change": "较去年同期38.55%",
                    },
                },
            },
            "push_enabled": True,
        },
        {
            "dataset_id": "comment_monitor",
            "dataset_label": "点评分析",
            "status": "success",
            "data": {
                "query_type": "ctrip_ebooking_review_analysis",
                "comment_monitor": {
                    "review_overview": {
                        "overall_score": {"label": "点评分", "value": "4.7"},
                        "positive_reviews_yesterday": {"label": "昨日好评", "value": "3"},
                        "negative_reviews_yesterday": {"label": "昨日差评", "value": "0"},
                    },
                },
            },
            "push_enabled": True,
        },
    ]


_DAILY_BRIEF_DAY_OVER_DAY = {
    "realtime_visitor_rank": {"previous": "285", "current": "280", "diff": "-5"},
    "psi_service_score": {"previous": "4.86", "current": "4.91", "diff": "+0.05"},
}


class DailyBriefPushCardTests(unittest.TestCase):
    def test_daily_brief_auto_layout_uses_dashboard_for_full_success(self) -> None:
        card = build_daily_brief_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 5/5。",
            dataset_results=_daily_brief_dataset_results(),
            title="EBK 数据简报",
            captured_at="2026-08-22T22:00:40",
            day_over_day=_DAILY_BRIEF_DAY_OVER_DAY,
            layout="auto",
        )

        self.assertEqual(
            sum(element.get("tag") == "table" for element in card["elements"]),
            3,
        )
        card_text = _all_text(card)
        for complete_field in (
            "PSI服务质量分",
            "酒店点评分",
            "5分钟回复率",
            "酒店收藏数",
            "列表页曝光量",
            "详情页访客量",
            "订单页访客量",
            "曝光转化率",
            "下单转化率",
            "成交转化率",
        ):
            self.assertIn(complete_field, card_text)
        self.assertIn("4.91", card_text)
        self.assertIn("1.12万", card_text)
        self.assertIn("竞争圈平均", card_text)

    def test_daily_brief_templates_stay_within_feishu_table_limit(self) -> None:
        table_counts: dict[str, int] = {}
        for layout in ("dashboard", "balanced", "compact"):
            card = build_daily_brief_push_card(
                watch_group_key="ctrip_ebooking_data_center",
                status="success",
                summary="EBK 数据中心读取成功：成功 5/5。",
                dataset_results=_daily_brief_dataset_results(),
                title="EBK 数据简报",
                captured_at="2026-08-22T22:00:40",
                day_over_day=_DAILY_BRIEF_DAY_OVER_DAY,
                layout=layout,
            )
            card_text = _all_text(card)
            table_counts[layout] = sum(
                element.get("tag") == "table" for element in card["elements"]
            )
            self.assertIn("本月经营概览", card_text)
            self.assertIn("APP流量转化", card_text)
            self.assertIn("昨日好评", card_text)

        # >10 行的合并块（balanced 核心区）改为一列文本，不翻页。
        self.assertEqual(table_counts, {"dashboard": 3, "balanced": 2, "compact": 0})
        # 所有保留下来的表格都不分页：page_size == 行数 ≤ 10。
        card = build_daily_brief_push_card(
            watch_group_key="dc_default",
            status="success",
            summary="EBK 数据中心读取成功：成功 5/5。",
            dataset_results=_daily_brief_dataset_results(),
            title="EBK 数据简报",
            captured_at="2026-08-22T22:00:40",
            layout="dashboard",
        )
        for table in _table_elements(card):
            self.assertEqual(table["page_size"], len(table["rows"]))
            self.assertLessEqual(table["page_size"], 10)

    def test_daily_brief_auto_layout_prefers_compact_for_attention_status(self) -> None:
        card = build_daily_brief_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="human_required",
            summary="需要人工处理。",
            dataset_results=_daily_brief_dataset_results(),
            title="EBK 数据简报",
            captured_at="2026-08-22T22:00:40",
            layout="auto",
        )
        self.assertFalse(any(element.get("tag") == "table" for element in card["elements"]))
        self.assertIn("- **PSI服务质量分**：4.91", _all_text(card))

    def test_daily_brief_card_renders_official_brief_sections(self) -> None:
        card = build_daily_brief_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 5/5。",
            dataset_results=_daily_brief_dataset_results(),
            title="携程静里全套房酒店（云州澜江路望川路地铁站店）EBK日报",
            captured_at="2026-08-22T22:00:40",
            day_over_day=_DAILY_BRIEF_DAY_OVER_DAY,
        )

        self.assertEqual(
            card["header"]["title"]["content"],
            "携程静里全套房酒店（云州澜江路望川路地铁站店）EBK日报",
        )
        self.assertEqual(
            sum(element.get("tag") == "table" for element in card["elements"]),
            3,
        )
        card_text = _all_text(card)
        # 城市排名 + DOD
        self.assertIn("城市排名", card_text)
        self.assertIn("280", card_text)
        self.assertIn("较前一日 -5", card_text)
        # 本月经营概览（含页面原文同比）
        self.assertIn("本月经营概览", card_text)
        self.assertIn("2000", card_text)
        self.assertIn("72.57万", card_text)
        self.assertIn("去年同期 2443", card_text)
        self.assertIn("去年同比 -18.13%", card_text)
        # 昨日经营
        self.assertIn("昨日经营", card_text)
        self.assertIn("33057.49", card_text)
        self.assertIn("预订订单数", card_text)
        self.assertIn("326", card_text)
        # 服务概览 + PSI DOD
        self.assertIn("PSI服务质量分", card_text)
        self.assertIn("较前一日 +0.05", card_text)
        # 漏斗：绝对值 + 较去年同期 + 转化率对比
        self.assertIn("APP流量转化漏斗", card_text)
        self.assertIn("1.12万", card_text)
        self.assertIn("较去年同期56.37%", card_text)
        self.assertIn("20.21%", card_text)
        self.assertIn("14.66%", card_text)
        # 点评
        self.assertIn("昨日好评", card_text)
        self.assertIn("昨日差评", card_text)
        # 指标说明尾注
        self.assertIn("指标说明", card_text)
        self.assertIn("离店维度全平台数据", card_text)

    def test_daily_brief_card_hides_missing_sections_without_inventing_data(self) -> None:
        card = build_daily_brief_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 5/5。",
            dataset_results=_daily_brief_dataset_results(include_monthly=False),
            title="EBK 数据简报",
            captured_at="2026-08-22T22:00:40",
            day_over_day=None,
        )

        card_text = _all_text(card)
        self.assertNotIn("本月经营概览", card_text)
        self.assertNotIn("去年同比", card_text)
        # 尾注口径文案保留，但不渲染任何 DOD 数值行
        self.assertNotIn("较前一日 -5", card_text)
        self.assertNotIn("较前一日 +0.05", card_text)

    def test_compute_day_over_day_uses_previous_business_outline_values(self) -> None:
        current_results = _daily_brief_dataset_results()
        previous_run = {
            "run_id": 284,
            "captured_at": datetime(2026, 8, 21, 22, 0, 33),
            "data": {
                "metrics": {
                    "realtime_visitor_rank": {"value": "285"},
                    "psi_service_score": {"value": "4.86"},
                }
            },
        }

        day_over_day = compute_daily_brief_day_over_day(
            current_results=current_results,
            previous_run=previous_run,
        )

        self.assertEqual(day_over_day["realtime_visitor_rank"]["diff"], "-5")
        self.assertEqual(day_over_day["psi_service_score"]["diff"], "+0.05")
        self.assertEqual(day_over_day["realtime_visitor_rank"]["previous"], "285")
        self.assertEqual(day_over_day["realtime_visitor_rank"]["current"], "280")

    def test_compute_day_over_day_omits_metrics_without_both_values(self) -> None:
        current_results = _daily_brief_dataset_results()
        previous_run = {
            "run_id": 284,
            "captured_at": datetime(2026, 8, 21, 22, 0, 33),
            "data": {"metrics": {"psi_service_score": {"value": "4.86"}}},
        }

        day_over_day = compute_daily_brief_day_over_day(
            current_results=current_results,
            previous_run=previous_run,
        )

        self.assertNotIn("realtime_visitor_rank", day_over_day)
        self.assertEqual(day_over_day["psi_service_score"]["diff"], "+0.05")

    def test_daily_brief_card_reads_rank_from_realtime_dimension_payload(self) -> None:
        """生产结构：realtime_visitor_rank 位于 time_dimensions.realtime.metrics，
        不是顶层 metrics。城市排名分区必须能取到。"""
        dataset_results = _daily_brief_dataset_results()
        for result in dataset_results:
            if result.get("dataset_id") != "business_outline":
                continue
            metrics = result["data"]["metrics"]
            rank_metric = metrics.pop("realtime_visitor_rank")
            result["data"]["time_dimensions"] = {
                "realtime": {"metrics": {"realtime_visitor_rank": rank_metric}}
            }

        card = build_daily_brief_push_card(
            watch_group_key="ctrip_ebooking_data_center",
            status="success",
            summary="EBK 数据中心读取成功：成功 5/5。",
            dataset_results=dataset_results,
            title="EBK 数据简报",
            captured_at="2026-08-22T22:00:40",
            day_over_day=_DAILY_BRIEF_DAY_OVER_DAY,
        )

        card_text = _all_text(card)
        self.assertIn("城市排名", card_text)
        self.assertIn("280", card_text)

    def test_compute_day_over_day_reads_rank_from_realtime_dimension(self) -> None:
        current_results = _daily_brief_dataset_results()
        for result in current_results:
            if result.get("dataset_id") != "business_outline":
                continue
            metrics = result["data"]["metrics"]
            rank_metric = metrics.pop("realtime_visitor_rank")
            result["data"]["time_dimensions"] = {
                "realtime": {"metrics": {"realtime_visitor_rank": rank_metric}}
            }
        previous_run = {
            "run_id": 284,
            "captured_at": datetime(2026, 8, 21, 22, 0, 33),
            "data": {
                "metrics": {"psi_service_score": {"value": "4.86"}},
                "time_dimensions": {
                    "realtime": {"metrics": {"realtime_visitor_rank": {"value": "285"}}}
                },
            },
        }

        day_over_day = compute_daily_brief_day_over_day(
            current_results=current_results,
            previous_run=previous_run,
        )

        self.assertEqual(day_over_day["realtime_visitor_rank"]["diff"], "-5")


if __name__ == "__main__":
    unittest.main()
