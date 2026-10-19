"""2026-09-11 立瑞推送人工审查四问题的修复测试。

问题1 携程实时起价多了「?」单位；问题2 美团同行榜单匹配到别家酒店 +
环比无变化文案 + 缺基线时保留页面较上期；问题3 促销效果「参加促销 5/15」
与促销中心列表（4 个）口径冲突；问题4 美团神券报名成功未进卡。
"""

from __future__ import annotations

import unittest


class RealtimeStartingPriceUnitTests(unittest.TestCase):
    def test_starting_price_unit_is_not_question_mark(self) -> None:
        from app.domain.ebk_global_push.adapters import _CTRIP_FIELD_LABELS

        label, value_type, unit = _CTRIP_FIELD_LABELS["realtime_starting_price"]
        self.assertEqual(label, "实时起价")
        self.assertEqual(value_type, "amount")
        self.assertEqual(unit, "")


class MeituanPeerBoardOwnHotelMatchTests(unittest.TestCase):
    """榜单本店匹配：立瑞店名前 4 字「云州江汉」与圈内别家酒店撞前缀，
    必须用完整店名匹配，不能用店名前缀。"""

    OWN = "云州澜江路立瑞酒店"
    DECOY = "云州澜江路市立医院华芙达安可酒店"

    def _tables(self):
        # 每个子榜 = 表头表 + 数据表；前三名渲染为图标（空名次格）。
        return [
            # 入住榜·入住间夜：本店第2，撞名酒店第4
            {"header": ["排名", "酒店名称", "入住间夜", "营销动作"], "rows": []},
            {
                "header": [],
                "rows": [
                    ["", "云州中心广场希尔硕环朋酒店", "15", ""],
                    ["4", self.DECOY, "12", "首住折扣"],
                    ["2", self.OWN, "8", ""],
                ],
            },
            # 入住榜·房费收入：本店第5
            {"header": ["排名", "酒店名称", "房费收入", "营销动作"], "rows": []},
            {
                "header": [],
                "rows": [
                    ["1", "云州中心广场希尔硕环朋酒店", "3800", ""],
                    ["5", self.OWN, "1200", ""],
                ],
            },
            # 销售榜·销售间夜：本店第3
            {"header": ["排名", "酒店名称", "销售间夜", "营销动作"], "rows": []},
            {
                "header": [],
                "rows": [
                    ["3", self.OWN, "9", ""],
                ],
            },
        ]

    def test_own_hotel_rank_per_board_not_decoy_prefix_match(self) -> None:
        from app.domain.browser.meituan_ebooking import _parse_meituan_peer_boards

        boards = _parse_meituan_peer_boards(self._tables(), self.OWN)

        self.assertEqual(len(boards), 3)
        by_metric = {board["metric"]: board for board in boards}
        self.assertEqual(by_metric["入住间夜"]["rank"], "2")
        self.assertEqual(by_metric["房费收入"]["rank"], "5")
        self.assertEqual(by_metric["销售间夜"]["rank"], "3")
        # 旧逻辑取到的撞名酒店名次（4）不得出现
        self.assertNotEqual(by_metric["入住间夜"]["rank"], "4")

    def test_parenthesized_hotel_name_still_matches(self) -> None:
        """页面店名带括号后缀（静里形态）时按去括号主体匹配。"""
        from app.domain.browser.meituan_ebooking import _parse_meituan_peer_boards

        own = "静里全套房酒店（云州澜江路望川路地铁站店）"
        tables = [
            {"header": ["排名", "酒店名称", "入住间夜", "营销动作"], "rows": []},
            {
                "header": [],
                "rows": [
                    ["", self.DECOY, "12", ""],
                    ["2", own, "8", ""],
                ],
            },
        ]
        boards = _parse_meituan_peer_boards(tables, own)
        self.assertEqual(boards[0]["rank"], "2")


class InjectComparisonNoChangeTests(unittest.TestCase):
    def _row(self, field_key, value, platform="ctrip", comparison=None):
        row = {"field_key": field_key, "value": value, "label": field_key, "platform": platform}
        if comparison:
            row["comparison"] = comparison
        return row

    def test_meituan_no_change_shows_jiaoshangqi_wubianhua(self) -> None:
        from app.domain.ebk_global_push import service

        rows = [self._row("销售额", "5000元", platform="meituan")]
        service._inject_comparison(rows, {"销售额": "5000.00 元"})
        self.assertEqual(rows[0]["comparison"], "较上期无变化")

    def test_ctrip_no_change_shows_jishangci_wubianhua(self) -> None:
        from app.domain.ebk_global_push import service

        rows = [self._row("booking_sales", "1400.00")]
        service._inject_comparison(rows, {"booking_sales": "1400"})
        self.assertEqual(rows[0]["comparison"], "较上次无变化")

    def test_changed_value_still_shows_diff(self) -> None:
        from app.domain.ebk_global_push import service

        rows = [self._row("销售额", "5000元", platform="meituan")]
        service._inject_comparison(rows, {"销售额": "4571元"})
        self.assertIn("+429", rows[0]["comparison"])

    def test_missing_baseline_keeps_page_comparison(self) -> None:
        """基线缺失（首拉/当日尚无成功快照）时保留页面自带「较上期 X%」，
        不再用「凌晨数据」覆盖真实页面对比。"""
        from app.domain.ebk_global_push import service

        rows = [self._row("支付订单数", "5 单", platform="meituan", comparison="较上期 25.00%")]
        service._inject_comparison(rows, {})
        self.assertEqual(rows[0]["comparison"], "较上期 25.00%")

    def test_placeholder_previous_keeps_page_comparison(self) -> None:
        """上一条基线是页面占位（如「- 单」）时同样保留页面较上期。"""
        from app.domain.ebk_global_push import service

        rows = [self._row("支付订单数", "5 单", platform="meituan", comparison="较上期 25.00%")]
        service._inject_comparison(rows, {"支付订单数": "- 单"})
        self.assertEqual(rows[0]["comparison"], "较上期 25.00%")

    def test_missing_baseline_without_page_comparison_keeps_marker(self) -> None:
        from app.domain.ebk_global_push import service

        rows = [self._row("booking_sales", "1280.50")]
        service._inject_comparison(rows, None)
        self.assertEqual(rows[0]["comparison"], "凌晨数据")


class CtripPromotionCountTests(unittest.TestCase):
    def test_participated_promotions_metric_not_emitted(self) -> None:
        """促销效果页「已参加促销 5/15」与促销中心列表（4 个）口径冲突：
        卡片以促销中心已参加促销组为准，效果页计数不进卡。"""
        from app.domain.ebk_global_push.adapters import ctrip_rows_from_run_payload

        payload = {
            "dataset_results": [
                {
                    "dataset_id": "promotion_effect",
                    "status": "success",
                    "data": {
                        "metrics": {
                            "participated_promotions": {
                                "label": "参加促销",
                                "value": "5",
                                "time_scope": "realtime",
                                "denominator_label": "总促销数",
                                "denominator": "15",
                            },
                            "promotion_room_nights": {
                                "label": "含促销间夜",
                                "value": "46",
                                "time_scope": "realtime",
                                "denominator_label": "总间夜",
                                "denominator": "75",
                            },
                        }
                    },
                }
            ]
        }
        rows, _needs = ctrip_rows_from_run_payload(payload)

        field_keys = {row["field_key"] for row in rows}
        self.assertNotIn("participated_promotions", field_keys)
        self.assertIn("promotion_room_nights", field_keys)


class MeituanCouponActivityTests(unittest.TestCase):
    def _payload(self, data):
        return {"payload": {"datasets": {"promotion_coupon": {"status": "success", "data": data}}}}

    def test_registered_coupon_activity_emitted(self) -> None:
        """神券卡片（coupon_activities）里「报名成功」的活动进促销卡；
        内容用页面字段（活动时间+渠道），不编造描述。"""
        from app.domain.ebk_global_push.adapters import meituan_rows_from_snapshot_payload

        payload = self._payload(
            {
                "coupon_activities": [
                    {
                        "activity_name": "美团神券",
                        "status": "报名成功",
                        "activity_time": "长期在线",
                        "channel": "平台联动",
                    }
                ]
            }
        )
        rows, _needs = meituan_rows_from_snapshot_payload(payload)

        promo_rows = [row for row in rows if row["dataset_id"] == "promotion_coupon"]
        self.assertEqual(len(promo_rows), 1)
        self.assertEqual(promo_rows[0]["label"], "美团神券")
        self.assertEqual(promo_rows[0]["value"], "长期在线（平台联动）")
        self.assertEqual(promo_rows[0]["dimension"], "活动")

    def test_coupon_activity_deduped_with_signup_list(self) -> None:
        """活动报名页已列出的活动不因神券卡片重复出现。"""
        from app.domain.ebk_global_push.adapters import meituan_rows_from_snapshot_payload

        payload = self._payload(
            {
                "coupon_activities": [
                    {
                        "activity_name": "美团神券",
                        "status": "报名成功",
                        "activity_time": "长期在线",
                        "channel": "平台联动",
                    }
                ],
                "signup_activities": [
                    {"name": "美团神券", "status": "已报名", "description": "长期在线"}
                ],
            }
        )
        rows, _needs = meituan_rows_from_snapshot_payload(payload)

        promo_rows = [row for row in rows if row["dataset_id"] == "promotion_coupon"]
        self.assertEqual(len(promo_rows), 1)

    def test_non_registered_coupon_activity_ignored(self) -> None:
        from app.domain.ebk_global_push.adapters import meituan_rows_from_snapshot_payload

        payload = self._payload(
            {
                "coupon_activities": [
                    {"activity_name": "未报名活动", "status": "报名中", "activity_time": "x"}
                ]
            }
        )
        rows, _needs = meituan_rows_from_snapshot_payload(payload)
        self.assertFalse([row for row in rows if row["dataset_id"] == "promotion_coupon"])


if __name__ == "__main__":
    unittest.main()
