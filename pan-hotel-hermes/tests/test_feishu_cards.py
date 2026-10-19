from __future__ import annotations

import json
import unittest

import client.feishu_cards as feishu_cards
from client.feishu_cards import (
    attach_ebk_intent_card,
    attach_feishu_card,
    build_review_summary_card,
    build_scheduler_status_card,
    pick_ebk_feishu_card_kind,
)
from client.watch_api import (
    enrich_calendar_price_latest_response,
    enrich_competitor_latest_response,
    enrich_runtime_health_response,
)


class FeishuCardBuilderTests(unittest.TestCase):
    def test_joint_brief_uses_icon_sections_and_compact_metric_table(self) -> None:
        payload = {
            "status": "success",
            "analysis_mode": "meituan",
            "operator_summary": {
                "store_label": "静里",
                "conclusion": "今日经营平稳。",
                "findings": [{"text": "销售额保持稳定"}],
                "actions": [{"text": "继续关注晚间转化"}],
                "platforms": {"meituan": {"status": "available"}},
                "read_times": {"meituan": "2026-08-22T10:00:00+08:00"},
                "meituan_workbench": {
                    "realtime_data": {"销售额": "879元", "支付订单数": "10单"}
                },
            },
        }
        card = feishu_cards.build_ebk_joint_brief_card(payload)
        text = json.dumps(card, ensure_ascii=False)
        self.assertIn("✅ 结论", text)
        self.assertIn("🔎 关键发现", text)
        self.assertIn("👉 建议", text)
        self.assertIn("🏠 美团", text)
        self.assertIn("🧮 关键指标", text)
        self.assertNotIn("📊 关键指标", text)
        self.assertTrue(any(item.get("tag") == "table" for item in card["elements"]))

    def test_platform_brief_section_icons_are_varied(self) -> None:
        operator = {
            "ctrip_ebk": {
                "monitor": {
                    "revenue_overview": {
                        "realtime": {"销售额": "100"},
                        "yesterday": {"销售额": "90"},
                    },
                    "sales_data": {"booking": {"订单": "2"}},
                }
            },
        }
        elements: list[dict] = []
        feishu_cards._append_ctrip_brief_elements(elements, operator)
        text = json.dumps(elements, ensure_ascii=False)
        self.assertIn("⏱️ 实时数据（今日）", text)
        self.assertIn("↩️ 昨日数据（对比参考）", text)
        self.assertIn("🛒 预订概况", text)
        self.assertNotIn("📊 实时数据（今日）", text)

    def test_build_ebk_joint_brief_card_meituan_title_and_sections(self) -> None:
        payload = {
            "status": "success",
            "analysis_mode": "meituan",
            "summary": "美团 EBK 分析可用。",
            "operator_summary": {
                "platforms": {"meituan": {"status": "available"}},
                "read_times": {"meituan": "2026-07-16T01:10:00+00:00"},
                "missing_data": [],
                "meituan_workbench": {
                    "available": True,
                    "realtime_data": {
                        "浏览人数": "87间",
                        "销售额": "879元",
                        "支付订单数": "10单",
                    },
                    "yesterday_data": {
                        "浏览人数": "305间",
                        "销售额": "3671元",
                        "支付订单数": "13单",
                    },
                    "hos_score": {"value": "4.56", "grade_tip": "再接再厉"},
                },
                "meituan_reviews": {"available": True, "review_score": "4.7"},
                "store_label": "静里",
                "usage_note": "只读分析，不触发调价。",
            },
        }

        self.assertTrue(hasattr(feishu_cards, "build_ebk_joint_brief_card"))
        card = feishu_cards.build_ebk_joint_brief_card(payload)
        card_text = str(card)

        self.assertEqual(card["header"]["title"]["content"], "静里 · 美团今日经营简报")
        self.assertIn("关键指标", card_text)
        self.assertIn("879元", card_text)
        self.assertNotIn("点评概况", card_text)
        self.assertNotIn("HOS", card_text)
        self.assertIn("只读", card_text)
        for forbidden in ("立即调价", "通过", "decision=approve"):
            self.assertNotIn(forbidden, card_text)

    def test_joint_brief_card_excludes_binding_and_promo_sections(self) -> None:
        payload = {
            "status": "success",
            "analysis_mode": "joint",
            "operator_summary": {
                "store_label": "曼馨",
                "platforms": {
                    "ctrip": {"status": "available"},
                    "meituan": {"status": "available"},
                    "fliggy": {"status": "available"},
                },
                "read_times": {
                    "ctrip": "2026-07-22T01:00:00+00:00",
                    "meituan": "2026-07-22T01:10:00+00:00",
                    "fliggy": "2026-07-22T01:20:00+00:00",
                },
                "missing_data": [],
                "usage_note": "只读分析。",
            },
        }
        card = feishu_cards.build_ebk_joint_brief_card(payload)
        blob = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("运营问答上下文群", blob)
        self.assertNotIn("盯盘配置总览", blob)
        self.assertNotIn("已参加促销", blob)
        self.assertNotIn("广告金字塔", blob)
        self.assertIn("经营简报", blob)

    def test_competitor_latest_card_excludes_binding_copy(self) -> None:
        payload = attach_feishu_card(
            {
                "operator_summary": {
                    "latest_captured_at": "2026-07-22T10:00:00+00:00",
                    "price_rows": [
                        {
                            "field": "竞品A · 今日",
                            "value": "大床房 / 无早 / ¥399",
                        }
                    ],
                },
                "run": {"status": "success", "summary": "读取成功"},
            },
            "competitor_latest",
        )
        blob = json.dumps(payload["feishu_card"], ensure_ascii=False)
        self.assertNotIn("调价 EBK", blob)
        self.assertNotIn("运营问答上下文群", blob)
        self.assertNotIn("盯盘配置总览", blob)

    def test_competitor_latest_card_has_mobile_status_summary(self) -> None:
        payload = attach_feishu_card(
            {
                "operator_summary": {
                    "latest_captured_at": "2026-07-22T10:00:00+00:00",
                    "price_rows": [{"field": "竞品A · 今日", "value": "大床房 / 无早 / ¥399"}],
                },
                "run": {"status": "success", "summary": "读取成功"},
            },
            "competitor_latest",
        )
        blob = json.dumps(payload["feishu_card"], ensure_ascii=False)
        self.assertIn("🕒 读取时间", blob)
        self.assertIn("✅ 状态", blob)
        self.assertIn("成功", blob)
        self.assertIn("📌 结果", blob)

    def test_build_ebk_joint_brief_card_meituan_nested_metric_values(self) -> None:
        payload = {
            "status": "success",
            "analysis_mode": "meituan",
            "operator_summary": {
                "platforms": {"meituan": {"status": "available"}},
                "read_times": {"meituan": "2026-07-16T01:10:00+00:00"},
                "meituan_workbench": {
                    "available": True,
                    "realtime_data": {"支付订单数": {"value": "3"}},
                    "yesterday_data": {"支付订单数": {"value": "7"}},
                },
            },
        }
        card_text = str(feishu_cards.build_ebk_joint_brief_card(payload))
        self.assertIn("关键指标", card_text)
        self.assertIn("支付订单数", card_text)
        self.assertIn("3", card_text)

    def test_build_ebk_joint_brief_card_joint_shows_missing_data(self) -> None:
        payload = {
            "status": "partial",
            "analysis_mode": "joint",
            "operator_summary": {
                "platforms": {
                    "ctrip": {"status": "available"},
                    "meituan": {"status": "available"},
                    "fliggy": {"status": "missing"},
                },
                "read_times": {
                    "ctrip": "2026-07-16T01:00:00+00:00",
                    "meituan": "2026-07-16T01:10:00+00:00",
                },
                "missing_data": ["飞猪 EBK 最新 snapshot 缺失"],
                "conclusion": "昨日美团销售额领先，飞猪数据缺失待补读。",
                "findings": [
                    {"text": "美团昨日销售额 3477 元", "evidence_refs": ["ev_mt"]},
                    {"text": "携程昨日离店销售额约 1.9 万", "evidence_refs": ["ev_ct"]},
                ],
                "actions": [{"text": "补读飞猪 EBK 后再做完整三端对比"}],
                "ctrip_ebk": {
                    "available": True,
                    "monitor": {
                        "revenue_overview": {
                            "yesterday": [
                                {
                                    "key": "departure_sales_amount",
                                    "label": "昨日离店销售额",
                                    "value": "19221.59",
                                },
                                {
                                    "key": "transaction_rate",
                                    "label": "成交率",
                                    "value": "77.61%",
                                },
                            ]
                        },
                        "sales_data": {
                            "in_house": [
                                {
                                    "key": "occupancy_rate",
                                    "label": "出租率",
                                    "value": "72%",
                                }
                            ]
                        },
                    },
                },
                "meituan_workbench": {
                    "available": True,
                    "realtime_data": {"今日订单": "3"},
                    "yesterday_data": {"销售额": "3477元"},
                },
                "store_label": "静里",
                "usage_note": "本卡片只读。",
            },
        }

        attached = attach_feishu_card(payload, "ebk_joint_brief")
        card = attached["feishu_card"]
        card_text = str(card)
        tags = [item.get("tag") for item in card["elements"]]

        self.assertEqual(attached["feishu_card_kind"], "ebk_joint_brief")
        self.assertEqual(
            card["header"]["title"]["content"],
            "静里 · 三端今日经营简报",
        )
        self.assertIn("飞猪 EBK 最新 snapshot 缺失", card_text)
        self.assertIn("结论", card_text)
        self.assertIn("昨日美团销售额领先", card_text)
        self.assertIn("建议", card_text)
        self.assertIn("关键指标", card_text)
        self.assertTrue("19221.59" in card_text or "3477" in card_text or "72%" in card_text)
        self.assertNotIn("点评概况", card_text)
        self.assertNotIn("table", tags)
        self.assertNotIn("realtime 1", card_text)
        self.assertNotIn("Ctrip data-center", card_text)
        self.assertNotIn("action", tags)

    def test_joint_brief_fliggy_uses_mobile_sections_not_table(self) -> None:
        payload = {
            "status": "success",
            "analysis_mode": "fliggy",
            "summary": "飞猪 EBK 最新 snapshot 已汇总。",
            "operator_summary": {
                "platforms": {"fliggy": {"status": "available"}},
                "read_times": {"fliggy": "2026-07-16T01:30:00+00:00"},
                "conclusion": "飞猪昨日预订偏弱，需关注曝光转化。",
                "findings": [{"text": "昨日销售额 1330 元，预订 2 单"}],
                "actions": [{"text": "核对飞猪首页引流价与活动曝光"}],
                "fliggy_home": {
                    "available": True,
                    "hotel_name": "曼馨酒店",
                    "today_data": {"浏览人数": "128"},
                    "yesterday_data": {"销售额": "1,330.2元", "预订订单量": "2"},
                },
                "fliggy_diagnose": {
                    "available": True,
                    "trade_overview": {"今日在店": "1间夜", "今日间夜单价": "413元"},
                },
                "store_label": "曼馨",
            },
        }
        card = feishu_cards.build_ebk_joint_brief_card(payload)
        card_text = str(card)
        tags = [item.get("tag") for item in card["elements"]]
        self.assertIn("结论", card_text)
        self.assertIn("关键指标", card_text)
        self.assertIn("1,330.2元", card_text)
        self.assertNotIn("诊断概况", card_text)
        self.assertNotIn("table", tags)
        self.assertNotIn("飞猪首页 ·", card_text)

    def test_joint_brief_shows_caliber_and_hides_unknown_english_metric_keys(self) -> None:
        payload = {
            "status": "success",
            "analysis_mode": "joint",
            "operator_summary": {
                "platforms": {
                    "ctrip": {"status": "available"},
                    "meituan": {"status": "available"},
                    "fliggy": {"status": "available"},
                },
                "read_times": {
                    "ctrip": "2026-07-22T09:00:00+08:00",
                    "meituan": "2026-07-22T09:01:00+08:00",
                    "fliggy": "2026-07-22T09:02:00+08:00",
                },
                "conclusion": "三端经营数据已汇总。",
                "findings": [{"text": "三端均有可用快照"}],
                "actions": [{"text": "结合各端流量和转化人工复核"}],
                "comparison_caliber_note": "各平台字段定义可能不同，仅作渠道表现参考。",
                "ctrip_ebk": {"monitor": {}},
                "meituan_workbench": {
                    "realtime_data": {"unknown_internal_metric": "12", "支付订单数": "3单"},
                    "yesterday_data": {},
                },
                "meituan_reviews": {},
                "fliggy_home": {
                    "today_data": {"raw_internal_field": "99", "浏览人数": "20"},
                    "yesterday_data": {},
                },
                "fliggy_diagnose": {},
                "fliggy_operating": {},
            },
        }

        card = feishu_cards.build_ebk_joint_brief_card(payload)
        card_text = str(card)
        tags = [item.get("tag") for item in card["elements"]]

        self.assertIn("结论", card_text)
        self.assertIn("关键发现", card_text)
        self.assertIn("建议", card_text)
        self.assertIn("口径说明", card_text)
        self.assertIn("字段定义可能不同", card_text)
        self.assertIn("携程", card_text)
        self.assertIn("美团", card_text)
        self.assertIn("飞猪", card_text)
        self.assertNotIn("table", tags)
        self.assertNotIn("unknown_internal_metric", card_text)
        self.assertNotIn("raw_internal_field", card_text)

    def test_review_card_contains_yesterday_fields(self) -> None:
        card = build_review_summary_card(
            review_summary={
                "available": True,
                "positive_reviews_yesterday": "6",
                "negative_reviews_yesterday": "0",
                "caliber_note": "昨日口径",
            },
            captured_at="2026-06-25T09:12:38+00:00",
        )
        self.assertEqual(card["header"]["title"]["content"], "点评概览")
        table = next(item for item in card["elements"] if item.get("tag") == "table")
        fields = {row["field"] for row in table["rows"]}
        self.assertIn("昨日好评", fields)

    def test_attach_feishu_card_adds_card_key(self) -> None:
        payload = attach_feishu_card(
            {
                "operator_summary": {
                    "overall_status": "fresh",
                    "module_rows": [{"field": "Backend API", "value": "fresh"}],
                }
            },
            "scheduler_status",
        )
        self.assertIn("feishu_card", payload)
        self.assertEqual(payload["feishu_card_kind"], "scheduler_status")

    def test_pick_ebk_card_kind_prefers_beneficial_for_run_now(self) -> None:
        snapshot = {
            "operator_summary": {
                "preferred_feishu_card_kind": "beneficial_data",
                "beneficial_metrics": [{"field": "出租率", "value": "48%"}],
                "review_summary": {"available": True},
            }
        }
        self.assertEqual(pick_ebk_feishu_card_kind(snapshot), "beneficial_data")

    def test_pick_ebk_card_kind_requires_explicit_preferred(self) -> None:
        snapshot = {
            "operator_summary": {
                "beneficial_metrics": [{"field": "出租率", "value": "48%"}],
                "review_summary": {"available": True},
            }
        }
        self.assertIsNone(pick_ebk_feishu_card_kind(snapshot))

    def test_attach_ebk_intent_card_review(self) -> None:
        payload = attach_ebk_intent_card(
            {
                "operator_summary": {
                    "review_summary": {
                        "available": True,
                        "positive_reviews_yesterday": "6",
                        "negative_reviews_yesterday": "0",
                    },
                    "latest_captured_at": "2026-06-30T09:00:00+00:00",
                }
            },
            card_kind="review",
        )
        self.assertEqual(payload["feishu_card_kind"], "ebk_latest")
        self.assertEqual(payload["feishu_card"]["header"]["title"]["content"], "点评概览")

    def test_attach_ebk_intent_card_beneficial(self) -> None:
        payload = attach_ebk_intent_card(
            {
                "operator_summary": {
                    "beneficial_metrics": [{"field": "出租率", "value": "48%"}],
                    "latest_captured_at": "2026-06-30T09:00:00+00:00",
                }
            },
            card_kind="beneficial_data",
        )
        self.assertEqual(payload["feishu_card_kind"], "beneficial_data")
        self.assertEqual(payload["feishu_card"]["header"]["title"]["content"], "经营数据")

    def test_competitor_price_trend_card_uses_operator_copy(self) -> None:
        payload = attach_feishu_card(
            {
                "days": 3,
                "series": [
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
                ],
                "operator_summary": {
                    "days": 3,
                    "intro": "最近3天竞品价格趋势",
                    "caliber_note": "仅使用可信页面价格。",
                },
            },
            "competitor_price_trend",
        )
        card = payload["feishu_card"]
        card_text = str(card)
        self.assertEqual(payload["feishu_card_kind"], "competitor_price_trend")
        self.assertIn("竞品价格趋势", card_text)
        self.assertIn("澜江路泉吉酒店", card_text)
        self.assertNotIn("room_rate_dom_display_price", card_text)

    def test_competitor_price_trend_card_uses_grouped_mobile_blocks(self) -> None:
        payload = attach_feishu_card(
            {
                "days": 3,
                "series": [
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
                            {"price_amount_yuan": 470, "captured_at": "2026-06-24T09:00:00"},
                            {"price_amount_yuan": 438, "captured_at": "2026-06-27T09:00:00"},
                        ],
                    },
                ],
                "operator_summary": {
                    "days": 3,
                    "intro": "最近3天竞品价格趋势",
                    "caliber_note": "仅使用可信页面价格。",
                },
            },
            "competitor_price_trend",
        )

        card = payload["feishu_card"]
        elements = card["elements"]
        self.assertNotIn("table", [item.get("tag") for item in elements])
        fallback = card.get("_feishu_fallback_elements") or elements
        card_text = "\n".join(
            item.get("content", "")
            for item in fallback
            if item.get("tag") == "markdown"
        )
        self.assertIn("🏪 澜江路泉吉酒店", card_text)
        self.assertIn("次读价", card_text)
        self.assertIn("¥430", card_text)
        self.assertIn("¥398", card_text)
        self.assertIn("无早", card_text)
        self.assertIn("含早", card_text)

    def test_competitor_latest_card_uses_mobile_blocks_not_table(self) -> None:
        payload = attach_feishu_card(
            {
                "operator_summary": {
                    "latest_captured_at": "2026-06-25T10:00:00+00:00",
                    "price_rows": [
                        {
                            "field": "测试竞品 · 今日",
                            "value": "大床房 / 无早 / ¥399",
                        }
                    ],
                },
                "run": {"status": "success", "summary": "读取成功"},
            },
            "competitor_latest",
        )
        elements = payload["feishu_card"]["elements"]
        self.assertNotIn("table", [item.get("tag") for item in elements])
        card_text = "\n".join(
            item.get("content", "")
            for item in elements
            if item.get("tag") == "markdown"
        )
        self.assertIn("🏪 测试竞品", card_text)
        self.assertIn("¥399", card_text)

    def test_calendar_price_card_is_read_only_alert_summary(self) -> None:
        payload = enrich_calendar_price_latest_response(
            {
                "status": "alert",
                "future_days": 15,
                "trusted_source": "calendar_dom_cell_price",
                "run": {"status": "alert", "captured_at": "2026-07-06T12:00:00+00:00"},
                "dates": [
                    {
                        "checkin_date": "2026-07-09",
                        "own": {"hotel_alias": "Own hotel", "price_amount_yuan": 411, "status": "success"},
                        "competitors": [
                            {"hotel_alias": "Competitor A", "price_amount_yuan": 398, "status": "success"},
                        ],
                        "alerts": [{"alert_type": "own_price_high", "gap_yuan": 13}],
                    }
                ],
            }
        )

        self.assertEqual(payload["feishu_card_kind"], "calendar_price_latest")
        card_text = str(payload["feishu_card"])
        self.assertIn("日历价监测", card_text)
        self.assertIn("2026-07-09", card_text)
        for forbidden in ("pricing_approval", "write_to_ctrip", "auto_execute", "confirm_price_change"):
            self.assertNotIn(forbidden, card_text)
        self.assertNotIn("action", [item.get("tag") for item in payload["feishu_card"]["elements"]])


class EnrichmentCardTests(unittest.TestCase):
    def test_competitor_enrichment_builds_price_rows(self) -> None:
        payload = enrich_competitor_latest_response(
            {
                "watch_group_key": "default_competitor_watch",
                "run": {"status": "success", "captured_at": "2026-06-25T10:00:00+00:00"},
                "hotels": [
                    {
                        "hotel_config": {"display_name": "测试竞品"},
                        "snapshots": {
                            "today": {
                                "rates": [
                                    {
                                        "room_type": "大床房",
                                        "breakfast_text": "无早",
                                        "price_amount_yuan": 399,
                                        "is_trusted": True,
                                    }
                                ]
                            }
                        },
                    }
                ],
            }
        )
        rows = payload["operator_summary"]["price_rows"]
        self.assertEqual(len(rows), 1)
        self.assertIn("feishu_card", payload)

    def test_runtime_health_enrichment_marks_hermes_feishu(self) -> None:
        payload = enrich_runtime_health_response(
            {
                "checked_at": "2026-06-25T10:00:00+00:00",
                "summary": {"status": "fresh", "attention_modules": []},
                "modules": {
                    "feishu_listener": {
                        "display_name": "飞书入口（Hermes 接管）",
                        "freshness_status": "managed_external",
                    }
                },
            }
        )
        card = build_scheduler_status_card(payload)
        self.assertEqual(card["header"]["template"], "green")
        self.assertIn("managed_external", payload["operator_summary"]["usage_note"])


class AnalyticsReportCardTests(unittest.TestCase):
    """V5 Step 5 — fixed-format analytics report card + webhook byte guard."""

    def _report_payload(self, *, hotel_count: int = 2, analysis: str = "") -> dict:
        hotels = []
        for index in range(hotel_count):
            hotels.append(
                {
                    "profile_key": f"hotel-{index}",
                    "display_name": f"酒店 {index}",
                    "competitor_groups": [
                        {
                            "name": "竞品",
                            "latest_trusted": {
                                "captured_at": "2026-08-12T08:00:00",
                                "hotels": [
                                    {"name": "本店", "role": "own", "min_trusted_price_yuan": 420 + index},
                                    {"name": "竞品A", "role": "competitor", "min_trusted_price_yuan": 399},
                                ],
                            },
                        }
                    ],
                    "ebk_groups": [{"name": "EBK"}],
                    "note": None,
                }
            )
        return {
            "report": {"scope": "all", "generated_at": "2026-08-12T09:00:00+08:00", "hotels": hotels},
            "operator_summary": {"analysis": analysis},
        }

    def test_card_renders_fixed_layout_from_structured_report(self) -> None:
        payload = self._report_payload(analysis="静里本店高于竞品，建议关注。")
        card = feishu_cards.build_analytics_report_card(payload)
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertEqual(card["header"]["title"]["content"], "跨店数据报表")
        self.assertIn("全部店铺", rendered)
        self.assertIn("酒店 0", rendered)
        self.assertIn("本店最低 ¥420", rendered)
        # AI content is poured into a fixed 简析 block, escaped, not used for layout.
        self.assertIn("静里本店高于竞品", rendered)
        self.assertIn("📝 简析（AI 内容）", rendered)

    def test_ai_analysis_is_capped_and_does_not_drive_format(self) -> None:
        long_analysis = "竞品价格回落。" * 200  # ~1400 chars
        payload = self._report_payload(analysis=long_analysis)
        card = feishu_cards.build_analytics_report_card(payload)
        analysis_block = next(
            el for el in card["elements"] if "简析" in str(el.get("content", ""))
        )
        self.assertLessEqual(
            len(analysis_block["content"]), feishu_cards.WEBHOOK_ANALYSIS_CHAR_LIMIT + 80
        )

    def test_card_stays_under_webhook_byte_limit_even_with_many_hotels(self) -> None:
        payload = self._report_payload(hotel_count=60, analysis="x" * 2000)
        card = feishu_cards.build_analytics_report_card(payload)
        size = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(size, feishu_cards.WEBHOOK_BODY_BYTE_LIMIT)
        # The header + lead element are never dropped, so the card is never empty.
        self.assertTrue(card["elements"])

    def test_enforce_card_byte_limit_drops_trailing_elements(self) -> None:
        huge = {
            "config": {"wide_screen_mode": True},
            "header": {"template": "turquoise", "title": {"tag": "plain_text", "content": "报表"}},
            "elements": [
                {"tag": "markdown", "content": "lead"},
                {"tag": "markdown", "content": "x" * 40000},
                {"tag": "markdown", "content": "y" * 40000},
            ],
        }
        trimmed = feishu_cards.enforce_card_byte_limit(huge, limit=2000)
        size = len(json.dumps(trimmed, ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(size, 2000)
        # The lead element is preserved.
        self.assertEqual(trimmed["elements"][0]["content"], "lead")

    def test_attach_feishu_card_dispatches_analytics_report(self) -> None:
        payload = self._report_payload()
        attached = attach_feishu_card(payload, "analytics_report")
        self.assertEqual(attached["feishu_card_kind"], "analytics_report")
        self.assertEqual(attached["feishu_card"]["header"]["title"]["content"], "跨店数据报表")


if __name__ == "__main__":
    unittest.main()
