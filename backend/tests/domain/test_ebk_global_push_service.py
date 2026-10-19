"""EBK 全局推送编排服务单测（fake 依赖，不触真实浏览器/飞书）。"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from app.domain.ebk_global_push.adapters import (
    ctrip_rows_from_run_payload,
    fliggy_rows_from_snapshot_payload,
    meituan_rows_from_snapshot_payload,
)
from app.domain.ebk_global_push.projection import normalize_projection_rows
from app.domain.ebk_global_push.service import (
    GlobalPushDeps,
    default_deps,
    resolve_platform_targets,
    run_global_push,
)


def _anchor(**overrides):
    base = dict(
        group_key="anchor_group",
        name="示例门店",
        enabled=True,
        auto_poll_enabled=False,
        platform_account_id="ctrip_account",
        browser_session_id="ctrip_session",
        feishu_webhook_url="https://open.feishu.cn/hook/fake",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mounted(platform, **overrides):
    base = dict(
        platform=platform,
        enabled=True,
        auto_poll_enabled=False,
        platform_account_id=f"{platform}_account",
        browser_session_id=f"{platform}_session",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _config(**overrides):
    base = dict(
        id=7,
        ctrip_watch_group_key="anchor_group",
        enabled_platforms=["ctrip", "meituan", "fliggy"],
        card_title_business=None,
        card_title_promotion=None,
        card_title_advertising=None,
    )
    base.update(overrides)
    return base


def _deps(
    *,
    anchor=None,
    mounted=None,
    ctrip_payload=None,
    platform_payloads=None,
    push_results=None,
    read_ctrip_exc=None,
    platform_exc_map=None,
    cooldown=0,
):
    anchor = anchor if anchor is not None else _anchor()
    mounted_map = mounted if mounted is not None else {
        "meituan": _mounted("meituan"),
        "fliggy": _mounted("fliggy"),
    }
    platform_payloads = platform_payloads if platform_payloads is not None else {}
    push_results = push_results if push_results is not None else {"status": "success"}
    alerts: list[tuple] = []
    sleeps: list[float] = []
    platform_reads: list[tuple] = []

    async def get_ctrip_group(group_key):
        return anchor

    async def find_mounted_group(platform, anchor_key):
        return mounted_map.get(platform)

    async def read_ctrip(group, global_run_id=None):
        if read_ctrip_exc is not None:
            raise read_ctrip_exc
        return ctrip_payload or {}

    async def read_platform(platform, group, global_run_id=None):
        platform_reads.append((platform, global_run_id))
        if platform_exc_map and platform in platform_exc_map:
            raise platform_exc_map[platform]
        return platform_payloads.get(platform, {})

    async def push_card(title, card, webhook_url):
        return push_results

    async def send_failure_alert(*args):
        alerts.append(args)

    async def sleep(seconds):
        sleeps.append(seconds)

    deps = GlobalPushDeps(
        get_ctrip_group=get_ctrip_group,
        find_mounted_group=find_mounted_group,
        read_ctrip=read_ctrip,
        read_platform=read_platform,
        push_card=push_card,
        send_failure_alert=send_failure_alert,
        sleep=sleep,
        cooldown_seconds=cooldown,
        read_time_text="2026-08-28T08:00:32+08:00",
    )
    return deps, alerts, sleeps, platform_reads


CTRIP_PAYLOAD = {
    "status": "success",
    "datasets": {
        "business_outline": {
            "status": "success",
            "captured_at": "2026-08-28T08:00:32+08:00",
            "data": {
                "metrics": {
                    "psi_service_score": {"label": "PSI服务质量分", "value": "5.44"},
                    "hotel_review_score": {"label": "酒店点评分", "value": "4.79"},
                },
                "time_dimensions": {
                    "realtime": {
                        "status": "success",
                        "metrics": {
                            "realtime_booking_orders": {"label": "实时预订订单", "value": 12},
                            "realtime_in_house_room_nights": {"label": "实时在店间夜", "value": 8},
                            "realtime_visitors": {"label": "实时访客", "value": 235},
                            "realtime_visitor_rank": {"label": "实时访客排名", "value": 3},
                        },
                    }
                },
            },
        },
        "promotion_registered": {
            "status": "success",
            "data": {
                "joined_activities": [
                    {"name": "暑期早鸟特惠", "status": "进行中", "valid_period": "至 2026-08-31", "rules": ["提前7天预订 立减 15%"], "room_types": "预豪华套房(全屋智能)<无早>"}
                ]
            },
        },
        "ad_pyramid": {"status": "success", "data": {"metrics": {}}},
    },
}

MEITUAN_PAYLOAD = {
    "status": "success",
    "payload": {
        "datasets": {
            "workbench_home": {
                "status": "success",
                "data": {"realtime_data": {"实时销售额": 18650.5, "实时订单": 42}},
            },
            "data_center_trade": {"status": "success", "data": {"metrics": {"支付订单数": 56}}},
        }
    },
}

FLIGGY_PAYLOAD = {
    "status": "success",
    "payload": {
        "datasets": {
            "home_v1": {
                "status": "success",
                "data": {"today_data": {"销售额": 9876, "浏览人数": 865}},
            }
        }
    },
}


class AdapterTests(unittest.TestCase):
    def test_ctrip_adapter_realtime_and_scores_only(self):
        rows, needs_review = ctrip_rows_from_run_payload(CTRIP_PAYLOAD)
        freshness = {row["field_key"]: row["freshness"] for row in rows}
        self.assertEqual(freshness["realtime_booking_orders"], "realtime")
        self.assertEqual(freshness["psi_service_score"], "score_snapshot")
        self.assertIn("暑期早鸟特惠", {row["label"] for row in rows})
        # ad_pyramid 无今日证据 → needs_review，不进卡
        self.assertIn("ad_pyramid", needs_review)
        self.assertNotIn("ad_pyramid", {row["dataset_id"] for row in rows})

    def test_ctrip_adapter_projects_daily_departure_sales(self):
        """2026-09-21 需求：收益概览补「离店销售额」。经营概览页昨日数据块
        （与实时预订/在店间夜同页抓取，落在 daily 维度）已有抓取值，投影成经营行；
        其余 daily 指标（离店间夜量等）不进全局推送卡。"""
        payload = json.loads(json.dumps(CTRIP_PAYLOAD))
        outline = payload["datasets"]["business_outline"]["data"]
        outline["time_dimensions"]["daily"] = {
            "status": "success",
            "metrics": {
                "departure_sales_amount": {"label": "离店销售额", "value": "13976.98"},
                "departure_room_nights": {"label": "离店间夜量", "value": "53"},
            },
        }
        rows, _needs = ctrip_rows_from_run_payload(payload)
        by_key = {row["field_key"]: row for row in rows}
        self.assertIn("departure_sales_amount", by_key)
        row = by_key["departure_sales_amount"]
        self.assertEqual(row["dataset_id"], "business_outline")
        self.assertEqual(row["label"], "离店销售额")
        self.assertEqual(row["value"], "13976.98")
        self.assertNotIn("departure_room_nights", by_key)

    def test_ctrip_adapter_pyramid_bid_rows_carry_table_parts(self):
        """2026-09-10 需求：金字塔出价列表进全局推送广告卡，行携带表格列 parts。"""
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "pyramid_bid",
                    "status": "success",
                    "captured_at": "2026-09-10T17:00:00+08:00",
                    "data": {
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
                        ]
                    },
                }
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        pyramid_rows = [row for row in rows if row["dataset_id"] == "pyramid_bid"]
        self.assertEqual(len(pyramid_rows), 2)
        self.assertEqual(pyramid_rows[0]["label"], "周内0-2")
        self.assertEqual(
            pyramid_rows[0]["parts"], ["¥6.5", "¥300", "¥45.2", "推广中"]
        )
        self.assertEqual(pyramid_rows[1]["parts"][0], "-")
        self.assertNotIn("pyramid_bid", needs_review)
        # 投影层放行：广告分类
        accepted, _rejected = normalize_projection_rows(rows)
        self.assertTrue(accepted)
        self.assertTrue(all(row["category"] == "advertising" for row in accepted))

    def test_ctrip_adapter_pyramid_bid_success_empty_shows_placeholder(self):
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "pyramid_bid",
                    "status": "success",
                    "data": {"promotions": []},
                }
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        self.assertEqual(
            [(row["label"], row["value"]) for row in rows],
            [("推广计划", "暂无金字塔推广计划")],
        )
        self.assertNotIn("pyramid_bid", needs_review)

    def test_ctrip_adapter_pyramid_bid_failure_emits_nothing(self):
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "pyramid_bid",
                    "status": "human_required",
                    "summary": "需要人工登录。",
                    "data": {"promotions": []},
                }
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        self.assertEqual(rows, [])
        self.assertNotIn("pyramid_bid", needs_review)

    def test_ctrip_adapter_accepts_run_watch_group_shape(self):
        """生产形态：run_watch_group 返回 dataset_results 列表（非 datasets 字典）。"""
        payload = {
            "watch_group_key": "anchor_group",
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "business_outline",
                    "status": "success",
                    "captured_at": "2026-08-28T08:00:32+08:00",
                    "data": {
                        "time_dimensions": {
                            "realtime": {
                                "status": "success",
                                "metrics": {
                                    "realtime_booking_orders": {
                                        "label": "实时预订订单",
                                        "value": 12,
                                    }
                                },
                            }
                        }
                    },
                },
                {"dataset_id": "loss_analysis", "status": "success", "data": {}},
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        self.assertEqual([row["field_key"] for row in rows], ["realtime_booking_orders"])
        self.assertIn("loss_analysis", needs_review)

    def test_ctrip_adapter_accepts_real_persisted_metric_shape(self):
        """生产实读：实时指标在 realtime 维度，评分是带 value 的指标对象。"""
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "business_outline",
                    "status": "success",
                    "captured_at": "2026-08-28T15:01:04+08:00",
                    "data": {
                        "metrics": {
                            "psi_service_score": {
                                "label": "PSI服务质量分",
                                "value": "5.44",
                            },
                            "hotel_review_score": {
                                "label": "酒店点评分",
                                "value": "4.79",
                            },
                        },
                        "time_dimensions": {
                            "realtime": {
                                "status": "success",
                                "metrics": {
                                    "realtime_booking_orders": {
                                        "label": "实时预订订单",
                                        "value": "35",
                                    },
                                    "realtime_in_house_room_nights": {
                                        "label": "实时在店间夜",
                                        "value": "68",
                                    },
                                    "realtime_visitors": {
                                        "label": "实时访客量",
                                        "value": "153",
                                    },
                                    "realtime_visitor_rank": {
                                        "label": "实时排名",
                                        "value": "292",
                                    },
                                },
                            }
                        },
                    },
                }
            ],
        }

        rows, needs_review = ctrip_rows_from_run_payload(payload)

        values = {row["field_key"]: row["value"] for row in rows}
        self.assertEqual(values["realtime_booking_orders"], "35")
        self.assertEqual(values["realtime_in_house_room_nights"], "68")
        self.assertEqual(values["realtime_visitors"], "153")
        self.assertEqual(values["realtime_visitor_rank"], "292")
        self.assertEqual(values["psi_service_score"], "5.44")
        self.assertEqual(values["hotel_review_score"], "4.79")
        self.assertEqual(needs_review, {})

    def test_ctrip_registered_promotions_accept_real_parser_shape(self):
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "promotion_registered",
                    "status": "success",
                    "captured_at": "2026-08-28T15:01:04+08:00",
                    "data": {
                        "promotions": [
                            {
                                "name": "限时抢购",
                                "status": "",
                                "promotion_time": "2026年8月30日-2026年9月24日",
                                "rules": ["每天 07:00:00 至 22:00:00 立减 15%"],
                                "room_types": "预豪华套房(全屋智能+会客厅)<无早>(1741110287)",
                            }
                        ]
                    },
                }
            ],
        }

        rows, needs_review = ctrip_rows_from_run_payload(payload)

        promo_rows = [row for row in rows if row["dataset_id"] == "promotion_registered"]
        # 2026-09-02 需求：活动卡只写活动内容（房型+优惠规则），
        # 不再输出 已报名/有效期 行。
        self.assertEqual(len(promo_rows), 1)
        self.assertEqual(promo_rows[0]["label"], "限时抢购")
        self.assertNotIn("已报名", [row["value"] for row in promo_rows])
        self.assertNotIn("有效期", [row["label"] for row in promo_rows])
        self.assertEqual(needs_review, {})

    def test_ctrip_registered_promotion_content_composed(self):
        """活动内容 = 房型短名：优惠规则（多条用；连接），取自促销中心实测字段。"""
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "promotion_registered",
                    "status": "success",
                    "captured_at": "2026-09-02T20:00:00+08:00",
                    "data": {
                        "promotions": [
                            {
                                "name": "提前预订",
                                "status": "",
                                "rules": ["提前3天预订 立减 20%", "提前5天预订 立减 25%"],
                                "room_types": "预家庭双卧套房(独立卧室+豪华会客厅)<早餐>(1491465277)",
                                "promotion_time": "2026年9月2日-2026年9月23日",
                            },
                            {
                                "name": "万马聚财红包",
                                "status": "",
                                "rules": ["9折"],
                                "room_types": "所有预付房型(仅全日房)",
                            },
                        ]
                    },
                }
            ],
        }
        rows, _ = ctrip_rows_from_run_payload(payload)
        by_label = {row["label"]: row["value"] for row in rows}
        self.assertEqual(
            by_label["提前预订"],
            "预家庭双卧套房：提前3天预订 立减 20%；提前5天预订 立减 25%",
        )
        self.assertEqual(by_label["万马聚财红包"], "所有预付房型：9折")

    def test_ctrip_adapter_ad_pyramid_today_evidence_rows(self):
        """T1.4：ad_pyramid 页面今日筛选证据（page_filter.selected + date_preset=今日）→ 广告卡行。"""
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "ad_pyramid",
                    "status": "success",
                    "captured_at": "2026-08-28T13:02:00+08:00",
                    "data": {
                        "page_filter": {
                            "attempted": True,
                            "target": "今日",
                            "selected": True,
                            "method": "quick_range_button_js",
                        },
                        "ad_pyramid": {"date_preset": "今日"},
                        "summary_metrics": {
                            "clicks": {"key": "clicks", "label": "点击(次)", "value": "47"},
                            "impressions": {"key": "impressions", "label": "曝光(次)", "value": "592"},
                            "click_rate": {"key": "click_rate", "label": "点击率", "value": "7.94%"},
                            "total_spend_cny": {
                                "key": "total_spend_cny",
                                "label": "总花费(CNY)",
                                "value": "193.78",
                            },
                            "booking_amount_cny": {
                                "key": "booking_amount_cny",
                                "label": "预订金额(CNY)",
                                "value": "2869.00",
                            },
                            "roas": {"key": "roas", "label": "广告投产比(ROAS)", "value": "14.81"},
                        },
                    },
                }
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        field_keys = {row["field_key"] for row in rows if row["dataset_id"] == "ad_pyramid"}
        self.assertIn("clicks", field_keys)
        self.assertIn("total_spend_cny", field_keys)
        self.assertIn("roas", field_keys)
        for row in rows:
            if row["dataset_id"] == "ad_pyramid":
                self.assertEqual(row["freshness"], "realtime")
        self.assertNotIn("ad_pyramid", needs_review)

    def test_ctrip_adapter_ad_pyramid_without_evidence_still_filtered(self):
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "ad_pyramid",
                    "status": "success",
                    "data": {
                        "page_filter": {"attempted": True, "target": "今日", "selected": False},
                        "summary_metrics": {"clicks": {"label": "点击(次)", "value": "47"}},
                    },
                }
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        self.assertEqual(rows, [])
        self.assertIn("ad_pyramid", needs_review)

    def test_ctrip_adapter_promotion_effect_realtime_section(self):
        """T1.3：促销效果页「今日实时数据」区块指标（parser 标记 time_scope=realtime）进活动卡；
        其余昨日口径指标不进。2026-09-11 审查：participated_promotions 与促销中心
        列表口径冲突（效果页 5 vs 中心 4），计数指标不进卡；今日实时区块存在时
        也不计入 needs_review。"""
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "promotion_effect",
                    "status": "success",
                    "captured_at": "2026-08-28T13:02:00+08:00",
                    "data": {
                        "metrics": {
                            "participated_promotions": {
                                "key": "participated_promotions",
                                "label": "参加促销",
                                "value": "9",
                                "time_scope": "realtime",
                                "realtime_section_header": "今日实时数据",
                            },
                            "promotion_room_nights": {
                                "key": "promotion_room_nights",
                                "label": "含促销间夜",
                                "value": "976",
                                "time_scope": "realtime",
                                "denominator_label": "总间夜",
                                "denominator": "1000",
                            },
                        }
                    },
                }
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        promo_rows = [row for row in rows if row["dataset_id"] == "promotion_effect"]
        self.assertEqual([row["field_key"] for row in promo_rows], ["promotion_room_nights"])
        self.assertEqual(promo_rows[0]["freshness"], "realtime")
        # 昨日口径主表不因部分字段有证据而整集放行
        self.assertNotIn("promotion_effect", needs_review)

    def test_ctrip_adapter_promotion_effect_count_only_not_flagged(self):
        """2026-09-11：促销效果今日实时区块只剩被剔除的计数指标时，
        无行产出但不计 needs_review（证据成立，属刻意剔除而非读取失败）。"""
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "promotion_effect",
                    "status": "success",
                    "data": {
                        "metrics": {
                            "participated_promotions": {
                                "key": "participated_promotions",
                                "label": "参加促销",
                                "value": "5",
                                "time_scope": "realtime",
                            }
                        }
                    },
                }
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        self.assertFalse([row for row in rows if row["dataset_id"] == "promotion_effect"])
        self.assertNotIn("promotion_effect", needs_review)

    def test_ctrip_adapter_flow_data_traffic_exposure_fields(self):
        """需求4：流量转化分析的实时访客量/实时排名/实时起价进卡，带竞争圈排名。"""
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "flow_data",
                    "status": "success",
                    "captured_at": "2026-09-01T12:00:00+08:00",
                    "data": {
                        "time_dimensions": {
                            "realtime": {
                                "status": "success",
                                "metrics": {
                                    "realtime_visitors": {
                                        "label": "实时访客量",
                                        "value": "56",
                                        "competitive_rank": "13/21",
                                    },
                                    "realtime_visitor_rank": {
                                        "label": "实时排名",
                                        "value": "237",
                                        "competitive_rank": "16/21",
                                    },
                                    "realtime_starting_price": {
                                        "label": "实时起价",
                                        "value": "229",
                                        "competitive_rank": "3/21",
                                    },
                                },
                            }
                        }
                    },
                },
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        by_key = {row["field_key"]: row for row in rows}
        self.assertEqual(by_key["realtime_visitors"]["value"], "56")
        self.assertEqual(by_key["realtime_visitor_rank"]["value"], "237")
        self.assertEqual(by_key["realtime_visitor_rank"]["competitive_rank"], "16/21")
        self.assertEqual(by_key["realtime_starting_price"]["value"], "229")
        self.assertEqual(by_key["realtime_starting_price"]["competitive_rank"], "3/21")
        # flow_data 行进卡后不计 needs_review
        self.assertNotIn("flow_data", needs_review)

    def test_ctrip_adapter_competition_profile_realtime_dimension(self):
        """T1.5：competition_profile 的 time_dimensions.realtime（页面「实时」单选选中）六项经营对比进经营卡。"""
        payload = {
            "status": "success",
            "dataset_results": [
                {
                    "dataset_id": "competition_profile",
                    "status": "success",
                    "captured_at": "2026-08-28T13:02:00+08:00",
                    "data": {
                        "time_dimensions": {
                            "realtime": {
                                "label": "实时",
                                "status": "success",
                                "metrics": {
                                    "booking_orders": {"label": "预订订单量", "value": "253"},
                                    "booking_sales": {"label": "预订销售额", "value": "16.14万"},
                                    "in_house_room_nights": {"label": "在店间夜", "value": "530"},
                                    "occupancy_rate": {"label": "出租率", "value": "43.27%"},
                                    "ctrip_app_visitors": {"label": "携程APP访客", "value": "2,340"},
                                    "ctrip_app_conversion_rate": {"label": "携程APP转化率", "value": "3.63%"},
                                },
                            },
                            "yesterday": {"label": "昨日", "status": "success", "metrics": {}},
                        }
                    },
                }
            ],
        }
        rows, needs_review = ctrip_rows_from_run_payload(payload)
        profile_rows = {row["field_key"] for row in rows if row["dataset_id"] == "competition_profile"}
        self.assertEqual(
            profile_rows,
            {
                "booking_orders",
                "booking_sales",
                "in_house_room_nights",
                "occupancy_rate",
                "ctrip_app_visitors",
                "ctrip_app_conversion_rate",
            },
        )
        for row in rows:
            if row["dataset_id"] == "competition_profile":
                self.assertEqual(row["freshness"], "realtime")
        self.assertNotIn("competition_profile", needs_review)

    def test_meituan_adapter_realtime_block_and_scores(self):
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "workbench_home": {
                        "status": "success",
                        "data": {
                            "realtime_data": {"实时销售额": 18650.5},
                            "hos_score": {"value": "88"},
                        },
                    },
                    "comment_manage_home": {
                        "status": "success",
                        "data": {"review_score": 4.5},
                    },
                    "data_center_trade": {"status": "success", "data": {"metrics": {}}},
                }
            },
        }
        rows, needs_review = meituan_rows_from_snapshot_payload(payload)
        scores = {row["field_key"]: row["freshness"] for row in rows}
        self.assertEqual(scores.get("hos_score"), "score_snapshot")
        self.assertEqual(scores.get("review_score"), "score_snapshot")
        self.assertEqual(scores.get("实时销售额"), "realtime")
        # 已消费的数据集不再计入 needs_review
        self.assertNotIn("workbench_home", needs_review)
        self.assertNotIn("comment_manage_home", needs_review)
        self.assertIn("data_center_trade", needs_review)

    def test_fliggy_adapter_today_block_only(self):
        rows, needs_review = fliggy_rows_from_snapshot_payload(FLIGGY_PAYLOAD)
        self.assertEqual({row["dataset_id"] for row in rows}, {"home_v1"})
        self.assertEqual(needs_review, {})

    def test_fliggy_adapter_diagnose_realtime_excludes_yesterday(self):
        """diagnose_report：实时区块字段进卡，「昨日*」键被过滤；评分/MCI 入卡。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "diagnose_report": {
                        "status": "success",
                        "data": {
                            "trade_overview": {
                                "今日在店": 12,
                                "间夜单价": "328",
                                "销售额": "9876",
                                "昨日在店间夜": 9,
                                "昨日销售额": "8100",
                            },
                            "guest_review_score": 4.7,
                            "mci_index": 76,
                        },
                    },
                    "operating_data": {"status": "success", "data": {}},
                }
            },
        }
        rows, needs_review = fliggy_rows_from_snapshot_payload(payload)
        labels = {row["label"] for row in rows}
        self.assertIn("今日在店", labels)
        self.assertIn("客人点评得分", labels)
        self.assertIn("MCI 酒店竞争力指数", labels)
        self.assertNotIn("昨日在店间夜", labels)
        self.assertNotIn("昨日销售额", labels)
        freshness = {row["field_key"]: row["freshness"] for row in rows}
        self.assertEqual(freshness["guest_review_score"], "score_snapshot")
        self.assertEqual(freshness["mci_index"], "current_state")
        self.assertIn("operating_data", needs_review)

    def test_meituan_adapter_data_center_today_realtime_metrics(self):
        """T1.1：数据中心「今日实时」页签抓取的指标（带 selected_page_tab 证据）进经营卡；
        默认页签（昨日口径）指标不进。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "data_center_home": {
                        "status": "success",
                        "data": {
                            "basic_operation_metrics": {
                                "引流价": {"value": "220.71 元"},
                            },
                            "today_realtime_metrics": {
                                "引流价": {"value": "340.00 元"},
                                "曝光量": {"value": "0 次"},
                                "支付订单数": {"value": "5 单"},
                            },
                            "today_realtime_evidence": {
                                "selected_page_tab": "今日实时",
                                "page_tabs": ["今日实时", "昨日", "近7天", "近30天"],
                            },
                        },
                    },
                    "data_center_trade": {
                        "status": "success",
                        "data": {
                            "metrics": {"支付订单数": "56"},
                        },
                    },
                }
            },
        }
        rows, needs_review = meituan_rows_from_snapshot_payload(payload)
        home_rows = {row["label"]: row["value"] for row in rows if row["dataset_id"] == "data_center_home"}
        self.assertEqual(home_rows.get("引流价"), "340.00 元")
        self.assertIn("曝光量", home_rows)
        self.assertNotIn("支付订单数", [row["label"] for row in rows if row["dataset_id"] == "data_center_trade"])
        for row in rows:
            if row["dataset_id"] == "data_center_home":
                self.assertEqual(row["freshness"], "realtime")
        self.assertNotIn("data_center_home", needs_review)
        self.assertIn("data_center_trade", needs_review)

    def test_meituan_adapter_passes_peer_rank_and_week_over_week(self):
        """需求5/6：今日实时指标的同行排名(带总数)与较上期幅度透传到行。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "data_center_home": {
                        "status": "success",
                        "data": {
                            "today_realtime_metrics": {
                                "销售额": {"value": "7142.00 元", "week_over_week": "0.90%", "peer_rank": "2/11"},
                                "浏览人数": {"value": "273 人", "week_over_week": "16.26%", "peer_rank": "3/11"},
                                "支付转化率": {"value": "9.16 %", "week_over_week": "3.94%", "peer_rank": "2/11"},
                            },
                            "today_realtime_evidence": {
                                "selected_page_tab": "今日实时",
                                "page_tabs": ["今日实时", "昨日", "近7天", "近30天"],
                            },
                        },
                    },
                }
            },
        }
        rows, _ = meituan_rows_from_snapshot_payload(payload)
        by_label = {row["label"]: row for row in rows if row["dataset_id"] == "data_center_home"}
        self.assertEqual(by_label["销售额"]["competitive_rank"], "2/11")
        self.assertEqual(by_label["销售额"]["comparison"], "较上期 0.90%")
        self.assertEqual(by_label["浏览人数"]["competitive_rank"], "3/11")
        self.assertEqual(by_label["支付转化率"]["comparison"], "较上期 3.94%")

    def test_meituan_adapter_skips_dash_placeholder_values(self):
        """2026-09-01 实测：一般指标缺失显示「- 次」不进卡；
        收益概览指标（入住间夜）保留占位行（页面真实状态）。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "data_center_home": {
                        "status": "success",
                        "data": {
                            "today_realtime_metrics": {
                                "曝光量": {"value": "- 次", "week_over_week": "-", "peer_rank": "-"},
                                "入住间夜": {"value": "- 间夜", "week_over_week": "-", "peer_rank": "-"},
                                "销售额": {"value": "5437.00 元", "week_over_week": "51.66%", "peer_rank": "4/11"},
                            },
                            "today_realtime_evidence": {
                                "selected_page_tab": "今日实时",
                                "page_tabs": ["今日实时", "昨日", "近7天", "近30天"],
                            },
                        },
                    },
                }
            },
        }
        rows, _ = meituan_rows_from_snapshot_payload(payload)
        by_label = {
            row["label"]: row
            for row in rows
            if row["dataset_id"] == "data_center_home"
        }
        self.assertNotIn("曝光量", by_label)
        self.assertIn("入住间夜", by_label)
        self.assertEqual(by_label["入住间夜"]["value"], "- 间夜")
        self.assertNotIn("competitive_rank", by_label["入住间夜"])
        self.assertIn("销售额", by_label)

    def test_meituan_adapter_traffic_conversion_labels(self):
        """流量分析 metrics（2026-09-02 需求）：仅浏览人数/浏览-支付转化率
        归「流量趋势」维度，曝光人数/曝光-浏览转化率等不进卡。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "data_center_traffic": {
                        "status": "success",
                        "data": {
                            "metrics": {
                                "支付订单数": "15单",
                                "曝光人数": "1835人",
                                "浏览人数": "274人",
                                "曝光-浏览转化率": "14.93%",
                                "浏览-支付转化率": "5.47%",
                            },
                        },
                    },
                },
            },
        }
        rows, _ = meituan_rows_from_snapshot_payload(payload)
        traffic = {
            row["label"]: row
            for row in rows
            if row.get("dimension") == "流量趋势"
        }
        self.assertIn("浏览人数", traffic)
        self.assertIn("浏览-支付转化率", traffic)
        self.assertEqual(traffic["浏览人数"]["value"], "274人")
        self.assertEqual(traffic["浏览-支付转化率"]["value"], "5.47%")
        self.assertNotIn("曝光人数", traffic)
        self.assertNotIn("曝光-浏览转化率", traffic)

    def test_meituan_adapter_peer_boards_rows(self):
        """同行榜单：每榜签两张子榜（如 入住榜=入住间夜榜+房费收入榜），
        行 label=「榜签·子榜指标」，value=N/M，dimension=同行榜单。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "data_center_peer_ranking": {
                        "status": "success",
                        "data": {
                            "peer_rankings_by_tab": {
                                "入住榜": {"boards": [
                                    {"metric": "入住间夜", "rank": "5", "total": "11", "rank_text": "5/11"},
                                    {"metric": "房费收入", "rank": "4", "total": "11", "rank_text": "4/11"},
                                ]},
                                "转化榜": {"boards": [
                                    {"metric": "浏览转化率", "rank": "3", "total": "11", "rank_text": "3/11"},
                                    {"metric": "支付转化率", "rank": "6", "total": "11", "rank_text": "6/11"},
                                ]},
                            },
                        },
                    },
                }
            },
        }
        rows, _ = meituan_rows_from_snapshot_payload(payload)
        boards = {
            row["label"]: row
            for row in rows
            if row.get("dimension") == "同行榜单"
        }
        self.assertEqual(
            set(boards),
            {"入住榜·入住间夜", "入住榜·房费收入", "转化榜·浏览转化率", "转化榜·支付转化率"},
        )
        self.assertEqual(boards["入住榜·入住间夜"]["value"], "5/11")
        self.assertEqual(boards["转化榜·支付转化率"]["value"], "6/11")

    def test_fliggy_adapter_operating_data_today_evidence(self):
        """T1.6：经营数据默认「今日」页签 + 区块更新时间为当日 → 在店/预定区进经营卡。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "operating_data": {
                        "status": "success",
                        "data": {
                            "in_house_data": {"在店间夜": "6间夜", "房费收入": "3,148元"},
                            "booking_data": {"预订间夜": "9间夜"},
                            "today_evidence": {
                                "today_confirmed": True,
                                "section_update_times": ["2026/08/28 13:11"],
                                "default_page_tab": "今日",
                            },
                        },
                    }
                }
            },
        }
        rows, needs_review = fliggy_rows_from_snapshot_payload(payload)
        operating_rows = {
            row["label"]: row["value"]
            for row in rows
            if row["dataset_id"] == "operating_data"
        }
        self.assertIn("在店间夜", operating_rows)
        self.assertIn("房费收入", operating_rows)
        self.assertIn("预订间夜", operating_rows)
        for row in rows:
            if row["dataset_id"] == "operating_data":
                self.assertEqual(row["freshness"], "realtime")
        self.assertNotIn("operating_data", needs_review)

    def test_fliggy_flow_rows_use_traffic_dimension(self):
        """2026-09-01 需求：流量数据行 dimension=流量，进「流量曝光」模块。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "operating_data": {
                        "status": "success",
                        "data": {
                            "booking_data": {"预订间夜": "9间夜"},
                            "flow_data": {"详情页曝光人数": "532", "预订转化率": "4.5%"},
                            "today_evidence": {
                                "today_confirmed": True,
                                "section_update_times": ["2026/09/01 19:06"],
                                "default_page_tab": "今日",
                            },
                        },
                    }
                }
            },
        }
        rows, _ = fliggy_rows_from_snapshot_payload(payload)
        flow_rows = {
            row["label"]: row for row in rows
            if row["field_key"].startswith("flow_data.")
        }
        self.assertEqual(set(flow_rows), {"详情页曝光人数", "预订转化率"})
        for row in flow_rows.values():
            self.assertEqual(row["dimension"], "流量")

    def test_fliggy_adapter_operating_data_without_evidence_filtered(self):
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "operating_data": {
                        "status": "success",
                        "data": {"in_house_data": {"在店间夜": "6间夜"}},
                    }
                }
            },
        }
        rows, needs_review = fliggy_rows_from_snapshot_payload(payload)
        self.assertEqual(rows, [])
        self.assertIn("operating_data", needs_review)

    def test_fliggy_adapter_promotion_agent_current_state(self):
        """T1.6：已参加活动（页面明示已报名 + 有效期）按 current_state 进活动卡。"""
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "promotion_agent": {
                        "status": "success",
                        "data": {
                            "joined_activities": [
                                {
                                    "name": "全人群运营活动",
                                    "status": "已报名",
                                    "description": "面向全人群及全场景，灵活选择房型及日期",
                                    "valid_period": "2026-05-30至2027-05-29",
                                }
                            ]
                        },
                    }
                }
            },
        }
        rows, needs_review = fliggy_rows_from_snapshot_payload(payload)
        promo_rows = [row for row in rows if row["dataset_id"] == "promotion_agent"]
        # 2026-09-02 需求：活动卡一行一个活动，值=活动内容描述，
        # 不再输出 已报名/有效期。
        self.assertEqual(len(promo_rows), 1)
        self.assertEqual(promo_rows[0]["label"], "全人群运营活动")
        self.assertIn("面向全人群及全场景", promo_rows[0]["value"])
        self.assertNotIn("有效期", [row["label"] for row in promo_rows])
        self.assertEqual(promo_rows[0]["freshness"], "current_state")
        self.assertNotIn("promotion_agent", needs_review)

class PreviousSnapshotTodayFilterTests(unittest.TestCase):
    """2026-09-08 需求：每天第一次推送（默认 8 点）的环比参照应为空（显示「凌晨数据」）。

    背景：原凌晨时段读取（空数据）改到 23:30 后，昨日全天量成了当日首推的
    基线，今晨实时量误比昨日全天量出大幅负差。跨天快照不得作为当日基线。
    """

    def test_snapshot_from_today_is_valid_baseline(self):
        from datetime import datetime

        from app.domain.ebk_global_push import service

        snapshot = {"created_at": datetime.now().replace(microsecond=0).isoformat()}
        self.assertTrue(service._snapshot_is_from_today(snapshot))

    def test_yesterday_2330_snapshot_is_rejected(self):
        from datetime import datetime, timedelta

        from app.domain.ebk_global_push import service

        yesterday = (datetime.now() - timedelta(days=1)).replace(
            hour=23, minute=30, second=0, microsecond=0
        )
        snapshot = {"created_at": yesterday.isoformat()}
        self.assertFalse(service._snapshot_is_from_today(snapshot))

    def test_missing_or_invalid_created_at_is_rejected(self):
        from app.domain.ebk_global_push import service

        for snapshot in (None, {}, {"created_at": ""}, {"created_at": "not-a-date"}):
            self.assertFalse(service._snapshot_is_from_today(snapshot))


class InjectComparisonTests(unittest.TestCase):
    """2026-09-01 需求：收益概览环比改增减幅度（数值差+百分比），首拉显示「凌晨数据」。"""

    def _row(self, field_key, value):
        return {"field_key": field_key, "value": value, "label": field_key}

    def test_first_pull_shows_early_morning_marker(self):
        from app.domain.ebk_global_push import service
        rows = [self._row("booking_sales", "1280.50")]
        service._inject_comparison(rows, None)
        self.assertEqual(rows[0]["comparison"], "凌晨数据")

    def test_first_pull_empty_previous_shows_marker(self):
        from app.domain.ebk_global_push import service
        rows = [self._row("booking_sales", "1280.50")]
        service._inject_comparison(rows, {})
        self.assertEqual(rows[0]["comparison"], "凌晨数据")

    def test_change_shows_amount_and_percent(self):
        from app.domain.ebk_global_push import service
        rows = [self._row("booking_sales", "1400.00")]
        service._inject_comparison(rows, {"booking_sales": "1280.50"})
        cmp_text = rows[0]["comparison"]
        self.assertIn("+119.50", cmp_text)
        self.assertIn("%", cmp_text)

    def test_departure_sales_compared_against_last_run(self):
        """2026-09-21 需求：离店销售额接入「较上次」增减幅度（昨日口径，
        当日内多与上次持平，跨天首推为差值）。"""
        from app.domain.ebk_global_push import service
        rows = [self._row("departure_sales_amount", "13976.98")]
        service._inject_comparison(rows, {"departure_sales_amount": "13476.98"})
        self.assertIn("+500", rows[0]["comparison"])
        self.assertIn("%", rows[0]["comparison"])
        rows = [self._row("departure_sales_amount", "13976.98")]
        service._inject_comparison(rows, {"departure_sales_amount": "13976.98"})
        self.assertEqual(rows[0]["comparison"], "较上次无变化")

    def test_business_row_outside_whitelist_now_compared(self):
        """2026-09-04 运营反馈：白名单外的经营行也应注入环比（差值/凌晨数据），
        不再留空导致卡片「—」泛滥。realtime_visitors 是白名单外的经营行。"""
        from app.domain.ebk_global_push import service
        row = self._row("realtime_visitors", "235")
        row["category"] = "business"
        rows = [row]
        service._inject_comparison(rows, {"realtime_visitors": "200"})
        self.assertIn("+35", rows[0].get("comparison", ""))

    def test_business_row_no_baseline_shows_early_morning(self):
        from app.domain.ebk_global_push import service
        row = self._row("occupancy_rate", "26.86%")
        row["category"] = "business"
        rows = [row]
        service._inject_comparison(rows, {"some_other_key": "10%"})
        self.assertEqual(rows[0]["comparison"], "凌晨数据")

    def test_business_row_dash_value_keeps_dash(self):
        """当前值是页面占位（如「-间夜」）：对比列维持「—」，不误标凌晨数据。"""
        from app.domain.ebk_global_push import service
        row = self._row("in_house_room_nights", "-间夜")
        row["category"] = "business"
        rows = [row]
        service._inject_comparison(rows, {"in_house_room_nights": "10"})
        self.assertNotIn("comparison", rows[0])

    def test_meituan_sales_field_compared(self):
        from app.domain.ebk_global_push import service
        rows = [self._row("销售额", "5000元")]
        service._inject_comparison(rows, {"销售额": "4571元"})
        self.assertIn("+429", rows[0].get("comparison", ""))

    def test_meituan_traffic_field_compared(self):
        """2026-09-01 美团需求3：流量曝光行同样按上一次读取计算较上期幅度。"""
        from app.domain.ebk_global_push import service
        rows = [{
            "field_key": "浏览人数", "value": "184 人", "label": "浏览人数",
            "platform": "meituan", "dimension": "流量趋势",
        }]
        service._inject_comparison(rows, {"浏览人数": "144 人"})
        self.assertIn("+40", rows[0].get("comparison", ""))

    def test_meituan_traffic_first_pull_shows_marker(self):
        from app.domain.ebk_global_push import service
        rows = [{
            "field_key": "浏览人数", "value": "184 人", "label": "浏览人数",
            "platform": "meituan", "dimension": "流量趋势",
        }]
        service._inject_comparison(rows, None)
        self.assertEqual(rows[0]["comparison"], "凌晨数据")

    def test_to_number_supports_wan_unit(self):
        """携程竞争画像实时销售额为「23.59万」格式（2026-09-01 实测），环比需可算。"""
        from app.domain.ebk_global_push import service
        self.assertEqual(service._to_number("23.59万"), 235900.0)
        self.assertEqual(service._to_number("1.2亿"), 120000000.0)

    def test_fliggy_in_house_metrics_compared(self):
        """2026-09-02 复审#3：飞猪在店区（房费收入/间夜单价）同样参与较上次环比，
        收益概览模块不再出现「—」与「凌晨数据」混杂。"""
        from app.domain.ebk_global_push import service
        rows = [
            self._row("in_house_data.房费收入", "979元"),
            self._row("in_house_data.间夜单价", "245元"),
        ]
        service._inject_comparison(rows, {"房费收入": "860元", "间夜单价": "215元"})
        self.assertIn("+119", rows[0].get("comparison", ""))
        self.assertIn("+30", rows[1].get("comparison", ""))

    def test_fliggy_in_house_first_pull_all_show_marker(self):
        from app.domain.ebk_global_push import service
        rows = [
            self._row("in_house_data.在店间夜", "4间夜"),
            self._row("in_house_data.房费收入", "979元"),
            self._row("in_house_data.间夜单价", "245元"),
        ]
        service._inject_comparison(rows, None)
        for row in rows:
            self.assertEqual(row["comparison"], "凌晨数据")

    def test_wan_sales_change_computed(self):
        from app.domain.ebk_global_push import service
        rows = [self._row("booking_sales", "23.59万")]
        service._inject_comparison(rows, {"booking_sales": "22.15万"})
        self.assertIn("+14400", rows[0].get("comparison", ""))


class ResolveTargetsTests(unittest.TestCase):
    def test_default_deps_uses_runtime_group_lookup_for_mounted_platforms(self):
        """生产仓储 list_* 返回 dict；全局读取必须取得可供读取器使用的 runtime 对象。"""
        meituan = _mounted("meituan")
        fliggy = _mounted("fliggy")
        with (
            mock.patch(
                "app.domain.meituan_ebooking_watch.repository.get_meituan_watch_group_runtime_by_ctrip_anchor",
                new=mock.AsyncMock(return_value=meituan),
            ),
            mock.patch(
                "app.domain.fliggy_ebooking_watch.repository.get_fliggy_watch_group_runtime_by_ctrip_anchor",
                new=mock.AsyncMock(return_value=fliggy),
            ),
            mock.patch(
                "app.domain.meituan_ebooking_watch.repository.list_meituan_watch_groups",
                new=mock.AsyncMock(return_value=[]),
            ),
            mock.patch(
                "app.domain.fliggy_ebooking_watch.repository.list_fliggy_watch_groups",
                new=mock.AsyncMock(return_value=[]),
            ),
        ):
            deps = default_deps()
            resolved_meituan = asyncio.run(
                deps.find_mounted_group("meituan", "anchor_group")
            )
            resolved_fliggy = asyncio.run(
                deps.find_mounted_group("fliggy", "anchor_group")
            )
        self.assertIs(resolved_meituan, meituan)
        self.assertIs(resolved_fliggy, fliggy)

    def test_missing_mounted_group_is_config_required(self):
        deps, _, _, platform_reads = _deps(mounted={"meituan": _mounted("meituan")})
        targets = asyncio.run(
            resolve_platform_targets(config=_config(), anchor=_anchor(), deps=deps)
        )
        self.assertEqual(targets["meituan"].status, "resolved")
        self.assertEqual(targets["fliggy"].status, "config_required")
        # 店铺直连口径：缺组提示按店铺说明（店铺未接入+无旧挂载），不再只提挂载链
        self.assertIn("未找到该店铺的fliggy监控组", targets["fliggy"].summary)
        self.assertIn("未接入fliggy", targets["fliggy"].summary)
        self.assertIn("挂载到该携程组的旧组", targets["fliggy"].summary)

    def test_disabled_legacy_watch_group_still_resolves_for_global_read(self):
        """enabled 只控制旧平台监控；全局模式复用账号执行一次独立读取。"""
        deps, _, _, _ = _deps(
            mounted={
                "meituan": _mounted("meituan", enabled=False),
                "fliggy": _mounted("fliggy", enabled=False),
            }
        )
        targets = asyncio.run(
            resolve_platform_targets(config=_config(), anchor=_anchor(), deps=deps)
        )
        self.assertEqual(targets["meituan"].status, "resolved")
        self.assertEqual(targets["fliggy"].status, "resolved")

    def test_anchor_without_session_is_config_required(self):
        deps, _, _, platform_reads = _deps()
        targets = asyncio.run(
            resolve_platform_targets(
                config=_config(),
                anchor=_anchor(browser_session_id=None),
                deps=deps,
            )
        )
        self.assertEqual(targets["ctrip"].status, "config_required")


class PreviousMetricsMultiSnapshotMergeTests(unittest.TestCase):
    """2026-09-08 飞猪实测：单次读取可能缺个别模块（如 MCI/今日在店/流量区），
    基线只取最近一条快照会让这些行在下一轮推送误标「凌晨数据」。
    基线应按字段回溯当日更早的成功快照补齐（每字段取包含它的最新一条）。"""

    def _payload(self, in_house=None):
        return {
            "status": "success",
            "payload": {
                "datasets": {
                    "operating_data": {
                        "status": "success",
                        "data": {
                            "in_house_data": in_house
                            or {"在店间夜": "6间夜", "房费收入": "3,148元"},
                            "booking_data": {"预订间夜": "9间夜"},
                            "today_evidence": {
                                "today_confirmed": True,
                                "section_update_times": ["2026/09/08 13:11"],
                                "default_page_tab": "今日",
                            },
                        },
                    }
                },
            },
        }

    def test_missing_field_in_latest_snapshot_falls_back_to_earlier_today(self):
        from datetime import datetime

        from app.domain.ebk_global_push import service

        now = datetime.now()
        latest = {
            "created_at": now.replace(minute=50, microsecond=0).isoformat(),
            # 缺「房费收入」模块（模拟飞猪页面偶发漏渲染）。
            "payload_json": self._payload(in_house={"在店间夜": "7间夜"}),
        }
        earlier = {
            "created_at": now.replace(minute=10, microsecond=0).isoformat(),
            "payload_json": self._payload(),
        }
        merged = service._merge_previous_metrics_from_snapshots(
            [latest, earlier], platform="fliggy"
        )
        # 最新快照优先（在店间夜=7），缺失字段回溯更早当日快照补齐。
        self.assertEqual(merged.get("in_house_data.在店间夜"), "7间夜")
        self.assertEqual(merged.get("in_house_data.房费收入"), "3,148元")
        self.assertEqual(merged.get("booking_data.预订间夜"), "9间夜")

    def test_yesterday_snapshots_are_ignored_entirely(self):
        from datetime import datetime, timedelta

        from app.domain.ebk_global_push import service

        now = datetime.now()
        yesterday = {
            "created_at": (now - timedelta(days=1)).isoformat(),
            "payload_json": self._payload(),
        }
        merged = service._merge_previous_metrics_from_snapshots(
            [yesterday], platform="fliggy"
        )
        self.assertEqual(merged, {})

    def test_all_snapshots_missing_payload_yield_empty_map(self):
        from datetime import datetime

        from app.domain.ebk_global_push import service

        now = datetime.now()
        merged = service._merge_previous_metrics_from_snapshots(
            [{"created_at": now.isoformat(), "payload_json": None}],
            platform="fliggy",
        )
        self.assertEqual(merged, {})

    def test_duplicate_key_prefers_numeric_over_placeholder(self):
        """美团多数据集同 key 双行（2026-09-08 实测 workbench_home 销售额=-元、
        data_center_home 销售额=5361.00 元）：基线取可计算的真实值。"""
        from app.domain.ebk_global_push import service

        rows = [
            {"field_key": "销售额", "value": "-元"},
            {"field_key": "销售额", "value": "5361.00 元"},
            {"field_key": "销售间夜", "value": "21 间夜"},
        ]
        values = service._baseline_values_from_rows(rows)
        self.assertEqual(values.get("销售额"), "5361.00 元")
        self.assertEqual(values.get("销售间夜"), "21 间夜")
        # 全是占位时保留首个（下游 _change_text 解析失败按缺基线口径处理）。
        only_placeholder = service._baseline_values_from_rows(
            [{"field_key": "入住间夜", "value": "-间夜"}, {"field_key": "入住间夜", "value": "—"}]
        )
        self.assertEqual(only_placeholder.get("入住间夜"), "-间夜")


class RunGlobalPushTests(unittest.TestCase):
    def test_full_success_delivers_three_categories(self):
        deps, alerts, sleeps, platform_reads = _deps(
            ctrip_payload=CTRIP_PAYLOAD,
            platform_payloads={"meituan": MEITUAN_PAYLOAD, "fliggy": FLIGGY_PAYLOAD},
            cooldown=30,
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=1, deps=deps))
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["succeeded"])
        self.assertEqual(result["score_summary"]["psi"], "5.44")
        self.assertEqual(set(result["cards"].keys()), {"business", "promotion", "advertising"})
        # 梯队冷却发生在相邻平台之间（携程→美团→飞猪，共 2 次）
        self.assertEqual(sleeps, [30, 30])
        self.assertEqual(alerts, [])

    def test_one_platform_fails_others_continue_partial(self):
        deps, alerts, _, platform_reads = _deps(
            ctrip_payload=CTRIP_PAYLOAD,
            platform_payloads={"fliggy": FLIGGY_PAYLOAD},
            platform_exc_map={"meituan": RuntimeError("登录失效 login required")},
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=2, deps=deps))
        self.assertEqual(result["status"], "human_required")
        self.assertFalse(result["succeeded"])
        # 美团失败、携程/飞猪成功，均保留在结果里
        self.assertEqual(result["platform_results"]["meituan"]["status"], "human_required")
        self.assertEqual(result["platform_results"]["ctrip"]["status"], "success")
        # 失败平台触发告警（美团一次）
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][1], "meituan")

    def test_anchor_disabled_is_config_required_without_read(self):
        deps, _, _, platform_reads = _deps(anchor=_anchor(enabled=False))
        result = asyncio.run(run_global_push(config=_config(), run_id=3, deps=deps))
        self.assertEqual(result["status"], "config_required")
        self.assertIn("已停用", result["error_summary"])

    def test_missing_webhook_marks_delivery_config_required(self):
        deps, _, _, _ = _deps(
            anchor=_anchor(feishu_webhook_url=""),
            ctrip_payload=CTRIP_PAYLOAD,
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=4, deps=deps))
        self.assertEqual(result["card_results"].get("delivery"), "config_required")
        self.assertFalse(result["succeeded"])

    def test_read_platform_receives_global_run_id(self):
        """T2.1：run_id 直通平台读取函数，供快照 read_trigger 落库关联。"""
        deps, _, _, platform_reads = _deps(
            ctrip_payload=CTRIP_PAYLOAD,
            platform_payloads={"meituan": MEITUAN_PAYLOAD, "fliggy": FLIGGY_PAYLOAD},
        )
        asyncio.run(run_global_push(config=_config(id=77), run_id=77, deps=deps))
        reads = {platform: global_run_id for platform, global_run_id in platform_reads}
        self.assertEqual(reads.get("meituan"), "77")
        self.assertEqual(reads.get("fliggy"), "77")

    def test_ctrip_nested_persistence_run_id_is_linked(self):
        deps, _, _, _ = _deps(
            ctrip_payload={
                **CTRIP_PAYLOAD,
                "persistence": {"status": "saved", "run_id": 316},
            }
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=78, deps=deps))
        self.assertEqual(result["platform_results"]["ctrip"]["snapshot_id"], 316)

    def test_config_required_platform_is_visible_in_all_global_cards(self):
        deps, _, _, _ = _deps(
            mounted={"meituan": _mounted("meituan")},
            ctrip_payload=CTRIP_PAYLOAD,
            platform_payloads={"meituan": MEITUAN_PAYLOAD},
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=79, deps=deps))
        cards_text = json.dumps(result["cards"], ensure_ascii=False)
        self.assertIn("飞猪", cards_text)
        self.assertIn("需配置", cards_text)

    def test_read_platform_global_run_id_none_without_run(self):
        deps, _, _, platform_reads = _deps(platform_payloads={"meituan": MEITUAN_PAYLOAD})
        asyncio.run(run_global_push(config=_config(id=7), run_id=None, deps=deps))
        reads = {platform: global_run_id for platform, global_run_id in platform_reads}
        self.assertIsNone(reads.get("meituan"))

    def test_platform_failure_summary_masks_local_paths(self):
        """R4/设计 §8.4：异常摘要中的本地 profile 路径必须脱敏（如 ProcessSingleton 文本）。"""
        deps, _, _alerts, _ = _deps(
            anchor=_anchor(feishu_webhook_url=""),
            platform_exc_map={
                "meituan": RuntimeError(
                    'Lock file can not be created at E:\\profiles\\meituan-ebooking\\JL1\\lock'
                )
            },
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=9, deps=deps))
        summary = result["platform_results"]["meituan"]["summary"]
        self.assertIn("[本地路径已隐藏]", summary)
        self.assertNotIn("E:\\", summary)
        self.assertNotIn("profiles", summary)

    def test_push_card_handles_pydantic_result_object(self):
        """问题 2（实测）：真实 push_card 返回 FeishuPushResult 模型（无 .get），
        服务须兼容对象形态，不能因 outcome.get 抛 AttributeError 把 run-now 打成 500。"""

        class _FakePushResult:
            def __init__(self, status: str) -> None:
                self.status = status

        deps, _, _, _ = _deps(
            ctrip_payload=CTRIP_PAYLOAD,
            push_results=_FakePushResult("success"),
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=5, deps=deps))
        # 三类卡投递成功，不抛 AttributeError
        self.assertNotIn("delivery", result["card_results"])
        self.assertTrue(result["succeeded"])

    def test_push_card_handles_pydantic_result_failure(self):
        class _FakePushResult:
            def __init__(self, status: str) -> None:
                self.status = status

        deps, _, _, _ = _deps(
            ctrip_payload=CTRIP_PAYLOAD,
            push_results=_FakePushResult("failed"),
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=6, deps=deps))
        self.assertFalse(result["succeeded"])


class BaselineLoadOrderTests(unittest.TestCase):
    """数据层基线修复（2026-09-01）：基线必须先于本次读取加载。

    语义：基线 = 本次读取之前最近一条成功快照（不限来源）。
    旧缺陷：读取器在读取过程中会保存本次快照（save_ebk_snapshot），
    基线若晚于读取加载，get_latest_success_ebk_snapshot 查到的是本次
    自己刚存的快照 → 环比恒为 ±0（自比自）。
    """

    def test_previous_metrics_loaded_before_reads(self):
        events: list[str] = []
        snapshot_store: dict[str, dict] = {
            "ctrip": {"realtime_booking_orders": 10},
        }
        captured: dict[str, dict] = {}

        async def get_ctrip_group(group_key):
            return _anchor()

        mounted_map = {"meituan": _mounted("meituan"), "fliggy": _mounted("fliggy")}

        async def find_mounted_group(platform, anchor_key):
            return mounted_map.get(platform)

        async def get_previous_metrics(platform, account_id):
            events.append(f"previous:{platform}")
            snapshot = dict(snapshot_store.get(platform) or {})
            captured[platform] = snapshot
            return snapshot

        async def read_ctrip(group, global_run_id=None):
            events.append("read:ctrip")
            snapshot_store["ctrip"] = {"realtime_booking_orders": 12}
            return CTRIP_PAYLOAD

        async def read_platform(platform, group, global_run_id=None):
            events.append(f"read:{platform}")
            return MEITUAN_PAYLOAD if platform == "meituan" else FLIGGY_PAYLOAD

        async def push_card(title, card, webhook_url):
            return {"status": "success"}

        async def send_failure_alert(*args):
            pass

        deps = GlobalPushDeps(
            get_ctrip_group=get_ctrip_group,
            find_mounted_group=find_mounted_group,
            read_ctrip=read_ctrip,
            read_platform=read_platform,
            push_card=push_card,
            send_failure_alert=send_failure_alert,
            sleep=_async_noop,
            cooldown_seconds=0,
            get_previous_metrics=get_previous_metrics,
        )
        result = asyncio.run(run_global_push(config=_config(), run_id=31, deps=deps))

        # 基线加载必须发生在对应平台读取之前（否则查到本次自己刚存的快照）
        self.assertLess(events.index("previous:ctrip"), events.index("read:ctrip"))
        self.assertLess(events.index("previous:meituan"), events.index("read:meituan"))
        # 基线拿到的是 12:00 的旧值，而非本次读取值
        self.assertEqual(captured["ctrip"].get("realtime_booking_orders"), 10)
        # 构卡环比用旧基线：12 vs 上次 10 → +2 (+20.0%)，而不是自比 ±0
        cards_text = json.dumps(result["cards"], ensure_ascii=False)
        self.assertIn("+2 (+20.0%)", cards_text)
        self.assertNotIn("+0 (0.0%)", cards_text)


async def _async_noop(_seconds):
    return None


if __name__ == "__main__":
    unittest.main()


class MeituanSignupAdapterTests(unittest.TestCase):
    """T2：美团「活动报名」已报名活动按 current_state 进促销卡。"""

    def test_signup_enrolled_activities_enter_promotion_card(self):
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "promotion_coupon": {
                        "status": "success",
                        "data": {
                            "coupon_activities": [],
                            "signup_activities": [
                                {
                                    "name": "【平台主推】踏春赏花特惠",
                                    "status": "已报名",
                                    "description": "春季专享特惠活动，精准引流",
                                    "valid_period": "全年",
                                },
                                {
                                    "name": "【平台主推】早订多减",
                                    "status": "未报名",
                                    "description": "提前锁定未来订单，降低空房风险",
                                    "valid_period": "全年",
                                },
                                {
                                    "name": "【平台主推】本地【人群特惠】",
                                    "status": "需重新报名",
                                    "valid_period": "全年",
                                },
                            ],
                        },
                    }
                }
            },
        }
        rows, needs_review = meituan_rows_from_snapshot_payload(payload)
        promo_rows = [row for row in rows if row["dataset_id"] == "promotion_coupon"]
        labels = {row["label"] for row in promo_rows}
        # 仅已报名进卡；2026-09-02 需求：值=活动内容描述，不再有 已报名/有效期 行
        self.assertIn("【平台主推】踏春赏花特惠", labels)
        self.assertNotIn("【平台主推】早订多减", labels)
        self.assertNotIn("【平台主推】本地【人群特惠】", labels)
        self.assertNotIn("有效期", labels)
        content = {row["label"]: row["value"] for row in promo_rows}
        self.assertEqual(content["【平台主推】踏春赏花特惠"], "春季专享特惠活动，精准引流")
        for row in promo_rows:
            self.assertEqual(row["freshness"], "current_state")
        self.assertNotIn("promotion_coupon", needs_review)

    def test_signup_absent_keeps_needs_review(self):
        payload = {
            "status": "success",
            "payload": {
                "datasets": {
                    "promotion_coupon": {
                        "status": "success",
                        "data": {"coupon_activities": [], "signup_activities": []},
                    }
                }
            },
        }
        rows, needs_review = meituan_rows_from_snapshot_payload(payload)
        self.assertEqual(rows, [])
        self.assertIn("promotion_coupon", needs_review)
