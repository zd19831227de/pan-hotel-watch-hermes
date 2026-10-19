from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

FLIGGY_HOME_URL = "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/homeV1"
FLIGGY_DIAGNOSE_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk-sycm/dataCenter/diagnoseReport"
)
FLIGGY_OPERATING_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk-sycm/dataCenter/operatingData"
)
FLIGGY_PROMOTION_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk-promotion/promotionAgent"
)

FLIGGY_HOME_TEXT = "\n".join(
    [
        "飞猪酒店",
        "静里全套房酒店",
        "今日数据",
        "销售额",
        "1280.00元",
        "订单数",
        "5",
        "间夜",
        "6",
        "昨日数据",
        "销售额",
        "980.00元",
        "订单数",
        "4",
        "间夜",
        "4",
    ]
)

FLIGGY_DIAGNOSE_TEXT = "\n".join(
    [
        "诊断报告",
        "交易数据概览",
        "销售额",
        "2780.00元",
        "支付订单数",
        "7",
        "客人点评得分",
        "4.8",
        "MCI酒店竞争力指数",
        "86",
    ]
)

FLIGGY_OPERATING_TEXT = "\n".join(
    [
        "经营数据",
        "在店数据",
        "在店间夜",
        "12",
        "在店人数",
        "18",
        "预定数据",
        "预定间夜",
        "9",
        "预订单数",
        "8",
    ]
)

FLIGGY_PROMOTION_TEXT = "\n".join(
    [
        "促销活动",
        "已参加活动",
        "暑期特惠",
        "进行中",
        "周末连住减",
        "已结束",
    ]
)

# Real operator failure: SPA chrome polluted the DOM text and was accepted as business data.
FLIGGY_SHELL_POLLUTED_HOME = "\n".join(
    [
        "通知",
        "商家学堂",
        "联系飞猪",
        "任务中心",
        "静里公寓酒店（云州中心广场望川路地铁站店）",
        "99+",
        "今日数据",
        "销售额",
        "暂无",
        "昨日数据",
        "销售额",
        "暂无",
    ]
)

FLIGGY_SHELL_POLLUTED_DIAGNOSE = "\n".join(
    [
        "通知",
        "商家学堂",
        "联系飞猪",
        "任务中心",
        "MCI酒店竞争力指数",
        "任务中心",
        "客人点评得分",
        "通知",
    ]
)

FLIGGY_SHELL_POLLUTED_PROMOTION = "\n".join(
    [
        "静里公寓酒店（云州中心广场望川路地铁站店）",
        "99+",
        "通知",
        "商家学堂",
        "联系飞猪",
        "任务中心",
    ]
)

# Real 2026-07-16 DOM text (after home tab click / diagnose page / operating+预订昨日 / promotion empty).
FLIGGY_REAL_HOME_STITCHED = "\n".join(
    [
        "今日数据",
        "销售额",
        "暂无",
        "浏览人数",
        "差",
        "预订订单量",
        "暂无",
        "预订间夜",
        "暂无",
        "昨日数据",
        "销售额",
        "962.5元",
        "周环比167%",
        "浏览人数",
        "暂无",
        "暂无对比数据",
        "预订订单量",
        "3",
        "周环比200%",
        "预订间夜",
        "3",
        "周环比200%",
        "流失数据",
    ]
)

FLIGGY_REAL_DIAGNOSE = "\n".join(
    [
        "诊断报告",
        "交易数据概览",
        "更多详情",
        "实时数据",
        "今日在店",
        "2间夜",
        "暂无同行排名数据",
        "间夜单价",
        "369元",
        "今日预订",
        "暂无",
        "昨日数据",
        "在店间夜",
        "2间夜",
        "间夜单价",
        "369元",
        "预订订单",
        "3间夜",
        "客人点评得分",
        "更多详情",
        "点评得分",
        "4.8分",
        "MCI酒店竞争力指数",
        "更多详情",
        "总分值",
        "3.25分",
        "上周同期0%",
    ]
)

FLIGGY_REAL_OPERATING = "\n".join(
    [
        "在店数据",
        "在店间夜",
        "2间夜",
        "房费收入",
        "738元",
        "间夜单价",
        "369元",
        "预订数据",
        "预订间夜",
        "3间夜",
        "预订收入金额",
        "962.5元",
        "预订订单",
        "3单",
    ]
)

FLIGGY_REAL_PROMOTION_EMPTY = "\n".join(
    [
        "营销助手",
        "已参加活动",
        "活动报名",
        "暂无数据",
        "钱包余额",
    ]
)

FLIGGY_REAL_PROMOTION_TABLE = "\n".join(
    [
        "营销助手",
        "已参加活动",
        "活动类型:",
        "全部",
        "活动状态:",
        "进行中",
        "查 询",
        "活动类型\t房型产品\t活动有效期\t活动力度(活动时段)",
        "报名时间",
        "最后修改时间",
        "活动状态",
        "操作",
        "分场景运营活动\t39个\t2026-05-30至2027-05-29",
        "2026-05-29至2027-05-29分场景运营活动立减5%",
        "投放规则预览\t2026-05-30 00:23\t2026-05-30 00:23\t进行中",
        "查看",
        "取消",
        "日志",
        "分人群运营活动\t酒店维度报名\t2026-01-22至2059-12-31",
        "立减5%",
        "2026-05-29 22:37\t2026-05-29 22:37\t进行中",
        "查看",
        "取消",
        "日志",
        "全人群运营活动\t39个\t2026-03-21至2027-04-30",
        "2026-03-21至2027-04-30全人群运营活动立减5%",
        "2026-05-29 22:37\t2026-05-29 22:37\t进行中",
        "查看",
        "取消",
        "日志",
    ]
)

FLIGGY_REAL_DIAGNOSE_WITH_ADVICE = "\n".join(
    [
        "经营建议",
        "客人点评得分低于4.9分",
        "做好线下服务，提升服务质量和客人满意度",
        "查看客人评价",
        "交易数据概览",
        "实时数据",
        "更新时间：2026/07/16 09:36",
        "今日在店",
        "2间夜",
        "间夜单价",
        "369元",
        "昨日数据",
        "在店间夜",
        "2间夜",
        "预订订单",
        "3间夜",
        "客人点评得分",
        "更多详情",
        "点评得分",
        "4.8分",
        "新增 5 分评价数：暂无",
        "新增差评数：暂无",
        "MCI酒店竞争力指数",
        "更多详情",
        "总分值",
        "3.25分",
    ]
)

FLIGGY_REAL_OPERATING_WITH_FLOW = "\n".join(
    [
        "经营数据",
        "交易数据",
        "流量数据",
        "在店数据",
        "在店间夜",
        "2间夜",
        "房费收入",
        "738元",
        "间夜单价",
        "369元",
        "预订数据",
        "预订间夜",
        "3间夜",
        "预订收入金额",
        "962.5元",
        "预订订单",
        "3单",
        "流量数据概览",
        "列表页曝光人数",
        "49",
        "详情页曝光人数",
        "26",
        "线上预订人数",
        "2",
        "浏览转化率",
        "52.6%",
        "预订转化率",
        "7.8%",
    ]
)


class FliggyDatasetParserTests(unittest.TestCase):
    def test_home_parser_extracts_today_and_yesterday(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_home_text(FLIGGY_HOME_TEXT, current_url=FLIGGY_HOME_URL)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["dataset"], "home_v1")
        data = payload["data"]
        self.assertEqual(data["today_data"]["销售额"], "1280.00元")
        self.assertEqual(data["yesterday_data"]["订单数"], "4")
        self.assertEqual(payload["evidence"]["parser"], "fliggy_home_dom_v1")

    def test_diagnose_parser_extracts_trade_review_and_mci(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_diagnose_text(
            FLIGGY_DIAGNOSE_TEXT,
            current_url=FLIGGY_DIAGNOSE_URL,
        )
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["dataset"], "diagnose_report")
        data = payload["data"]
        self.assertEqual(data["trade_overview"]["销售额"], "2780.00元")
        self.assertEqual(data["guest_review_score"], "4.8")
        self.assertEqual(data["mci_index"], "86")

    def test_operating_parser_extracts_inhouse_and_booking(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_operating_text(
            FLIGGY_OPERATING_TEXT,
            current_url=FLIGGY_OPERATING_URL,
        )
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["dataset"], "operating_data")
        data = payload["data"]
        self.assertEqual(data["in_house_data"]["在店间夜"], "12")
        self.assertEqual(data["booking_data"]["预订单数"], "8")

    def test_promotion_parser_extracts_joined_activities(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_promotion_text(
            FLIGGY_PROMOTION_TEXT,
            current_url=FLIGGY_PROMOTION_URL,
        )
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["dataset"], "promotion_agent")
        activities = payload["data"]["joined_activities"]
        self.assertEqual(activities[0]["name"], "暑期特惠")
        self.assertEqual(activities[0]["status"], "进行中")
        self.assertEqual(activities[1]["name"], "周末连住减")

    def test_promotion_parser_reads_real_card_layout_with_description(self) -> None:
        """2026-09-01 实测：营销助手-已参加活动为卡片版式（名称/描述/已报名 三行），
        名称与状态间隔着描述行，仍需提出全部活动。"""
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        text = "\n".join(
            [
                "静里公寓酒店（云州中心广场望川路地铁站店）",
                "营销助手",
                "活动报名",
                "已参加活动",
                "活动报名",
                "报名以下活动日均转化率预计提升：",
                "13%",
                "低价单风险保护 报名后可实时退出",
                "全人群运营活动",
                "面向全人群及全场景，灵活选择房型及日期，快速管理酒店价格",
                "已报名",
                "5%",
                "相当于原天天特惠+飞猪酒店红包/飞猪立减特惠",
                "分场景运营活动",
                "面向不同场景，获得更精准的预订机会",
                "已报名",
                "5%",
                "相当于原出行特惠、门店新客、早订特惠、连住特惠、今夜甩卖、限时特惠",
                "分人群运营活动",
                "面向分人群投放，提升人群转化",
                "已报名",
                "5%",
                "相当于原膨胀红包+酒店神券",
                "钱包余额",
                "查看明细",
            ]
        )
        payload = fliggy.parse_fliggy_promotion_text(
            text, current_url=FLIGGY_PROMOTION_URL
        )
        self.assertEqual(payload["status"], "success")
        activities = payload["data"]["joined_activities"]
        names = [a["name"] for a in activities]
        self.assertIn("全人群运营活动", names)
        self.assertIn("分场景运营活动", names)
        self.assertIn("分人群运营活动", names)
        for activity in activities:
            self.assertEqual(activity["status"], "已报名")
        # 2026-09-02 需求：活动内容=名称下的描述行（面向...），进活动卡
        by_name = {a["name"]: a for a in activities}
        self.assertEqual(
            by_name["全人群运营活动"]["description"],
            "面向全人群及全场景，灵活选择房型及日期，快速管理酒店价格",
        )
        self.assertEqual(
            by_name["分场景运营活动"]["description"],
            "面向不同场景，获得更精准的预订机会",
        )

    def test_home_parser_needs_review_when_unrecognized(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_home_text("登录 暂无数据", current_url="about:blank")
        self.assertEqual(payload["status"], "needs_review")

    def test_home_parser_rejects_placeholder_zanwu_metrics(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_home_text(
            FLIGGY_SHELL_POLLUTED_HOME,
            current_url=FLIGGY_HOME_URL,
        )
        self.assertEqual(payload["status"], "needs_review")
        self.assertEqual(payload["data"]["today_data"], {})
        self.assertEqual(payload["data"]["yesterday_data"], {})

    def test_diagnose_parser_rejects_nav_chrome_as_mci_or_score(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_diagnose_text(
            FLIGGY_SHELL_POLLUTED_DIAGNOSE,
            current_url=FLIGGY_DIAGNOSE_URL,
        )
        self.assertEqual(payload["status"], "needs_review")
        self.assertIsNone(payload["data"]["mci_index"])
        self.assertIsNone(payload["data"]["guest_review_score"])

    def test_promotion_parser_rejects_shell_chrome_as_activities(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_promotion_text(
            FLIGGY_SHELL_POLLUTED_PROMOTION,
            current_url=FLIGGY_PROMOTION_URL,
        )
        self.assertEqual(payload["status"], "needs_review")
        self.assertEqual(payload["data"]["joined_activities"], [])
        names = [item.get("name") for item in payload["data"]["joined_activities"]]
        self.assertNotIn("通知", names)
        self.assertNotIn("商家学堂", names)
        self.assertNotIn("联系飞猪", names)

    def test_home_parser_reads_real_tab_metric_labels(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_home_text(
            FLIGGY_REAL_HOME_STITCHED,
            current_url=FLIGGY_HOME_URL,
        )
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["data"]["today_data"], {})
        self.assertEqual(payload["data"]["yesterday_data"]["销售额"], "962.5元")
        self.assertEqual(payload["data"]["yesterday_data"]["预订订单量"], "3")
        self.assertEqual(payload["data"]["yesterday_data"]["预订间夜"], "3")

    def test_diagnose_parser_reads_real_trade_review_mci_layout(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_diagnose_text(
            FLIGGY_REAL_DIAGNOSE,
            current_url=FLIGGY_DIAGNOSE_URL,
        )
        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertEqual(data["trade_overview"]["今日在店"], "2间夜")
        self.assertEqual(data["trade_overview"]["昨日预订订单"], "3间夜")
        self.assertEqual(data["guest_review_score"], "4.8分")
        self.assertEqual(data["mci_index"], "3.25分")

    def test_operating_parser_reads_real_inhouse_and_booking_labels(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_operating_text(
            FLIGGY_REAL_OPERATING,
            current_url=FLIGGY_OPERATING_URL,
        )
        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertEqual(data["in_house_data"]["房费收入"], "738元")
        self.assertEqual(data["in_house_data"]["间夜单价"], "369元")
        self.assertEqual(data["booking_data"]["预订间夜"], "3间夜")
        self.assertEqual(data["booking_data"]["预订收入金额"], "962.5元")
        self.assertEqual(data["booking_data"]["预订订单"], "3单")

    def test_operating_parser_zanwu_booking_read_as_zero(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        text = "\n".join(
            [
                "在店数据",
                "在店间夜",
                "1间夜",
                "预订数据",
                "预订间夜",
                "暂无",
                "预订收入金额",
                "暂无",
                "预订订单",
                "暂无",
            ]
        )
        payload = fliggy.parse_fliggy_operating_text(
            text,
            current_url=FLIGGY_OPERATING_URL,
        )
        self.assertEqual(payload["status"], "success")
        booking = payload["data"]["booking_data"]
        # 「暂无」即当日暂无预订数据，正常显示 0（2026-09-02 运营确认）。
        self.assertEqual(booking["预订间夜"], "0")
        self.assertEqual(booking["预订收入金额"], "0")
        self.assertEqual(booking["预订订单"], "0")

    def test_promotion_parser_empty_section_is_success(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_promotion_text(
            FLIGGY_REAL_PROMOTION_EMPTY,
            current_url=FLIGGY_PROMOTION_URL,
        )
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["data"]["joined_activities"], [])

    def test_promotion_parser_reads_registered_list_table_rows(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_promotion_text(
            FLIGGY_REAL_PROMOTION_TABLE,
            current_url=FLIGGY_PROMOTION_URL,
        )
        self.assertEqual(payload["status"], "success")
        activities = payload["data"]["joined_activities"]
        self.assertEqual(
            [item["name"] for item in activities],
            ["分场景运营活动", "分人群运营活动", "全人群运营活动"],
        )
        self.assertTrue(all(item["status"] == "进行中" for item in activities))
        first = activities[0]
        self.assertEqual(first["activity_type"], "分场景运营活动")
        self.assertEqual(first["room_product"], "39个")
        self.assertEqual(first["valid_period"], "2026-05-30至2027-05-29")
        self.assertIn("立减5%", first["strength"])

    def test_diagnose_parser_ignores_advice_and_update_time_for_review_score(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_diagnose_text(
            FLIGGY_REAL_DIAGNOSE_WITH_ADVICE,
            current_url=FLIGGY_DIAGNOSE_URL,
        )
        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertEqual(data["guest_review_score"], "4.8分")
        self.assertNotIn("2026/07/16", str(data["guest_review_score"]))
        self.assertEqual(data["review_extras"].get("新增5分评价数"), "暂无")
        self.assertEqual(data["review_extras"].get("新增差评数"), "暂无")
        self.assertEqual(data["mci_index"], "3.25分")

    def test_operating_parser_reads_flow_funnel_metrics(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_operating_text(
            FLIGGY_REAL_OPERATING_WITH_FLOW,
            current_url=FLIGGY_OPERATING_URL,
        )
        self.assertEqual(payload["status"], "success")
        flow = payload["data"]["flow_data"]
        self.assertEqual(flow["列表页曝光人数"], "49")
        self.assertEqual(flow["详情页曝光人数"], "26")
        self.assertEqual(flow["线上预订人数"], "2")
        self.assertEqual(flow["浏览转化率"], "52.6%")
        self.assertEqual(flow["预订转化率"], "7.8%")


class FliggyReadDatasetApiTests(unittest.TestCase):
    def test_read_dataset_uses_requested_fliggy_dataset(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "fliggy_ds",
                    "display_name": "Fliggy DS",
                    "platform": "fliggy",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9541,
                },
            )
            self.assertEqual(created.status_code, 200)
            fliggy_api = importlib.import_module("app.api.v1.fliggy_ebooking")

            class _Fake:
                async def read_dataset(self, **kwargs: Any) -> dict[str, Any]:
                    return {
                        "status": "success",
                        "summary": "ok",
                        "platform": "fliggy",
                        "dataset": kwargs["dataset"],
                        "platform_account_id": kwargs["platform_account_id"],
                        "browser_session_id": kwargs["browser_session_id"],
                        "data": {"today_data": {"销售额": "1"}},
                        "evidence": {
                            "profile_path_returned": False,
                            "password_returned": False,
                            "parser": "fliggy_home_dom_v1",
                        },
                    }

            original = fliggy_api.get_fliggy_ebooking_service
            fliggy_api.get_fliggy_ebooking_service = lambda: _Fake()
            try:
                response = client.post(
                    "/api/v1/fliggy-ebooking/sessions/fliggy_ds/read-dataset",
                    json={
                        "browser_session_id": created.json()["browser_session_id"],
                        "dataset": "home_v1",
                    },
                )
            finally:
                fliggy_api.get_fliggy_ebooking_service = original

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["dataset"], "home_v1")
            self.assertEqual(response.json()["platform"], "fliggy")

    def test_read_all_snapshots_supports_fliggy(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "fliggy_all",
                    "display_name": "Fliggy All",
                    "platform": "fliggy",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9542,
                },
            )
            self.assertEqual(created.status_code, 200)

            reader = importlib.import_module("app.domain.ebk_snapshots.ebk_snapshot_reader")

            class _FakeService:
                async def read_dataset(self, **kwargs: Any) -> dict[str, Any]:
                    return {
                        "status": "success",
                        "summary": f"read {kwargs['dataset']}",
                        "platform": "fliggy",
                        "dataset": kwargs["dataset"],
                        "data": {"ok": True},
                        "evidence": {"parser": f"fliggy_{kwargs['dataset']}_dom_v1"},
                    }

            original = reader.get_fliggy_ebooking_service
            reader.get_fliggy_ebooking_service = lambda: _FakeService()
            try:
                response = client.post(
                    "/api/v1/ebk-snapshots/read-all",
                    json={
                        "platforms": ["fliggy"],
                        "fliggy_platform_account_id": "fliggy_all",
                    },
                )
            finally:
                reader.get_fliggy_ebooking_service = original

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertIn("fliggy", payload["results"])
            self.assertEqual(payload["results"]["fliggy"]["status"], "success")
            self.assertEqual(
                payload["results"]["fliggy"]["dataset_count"],
                len(reader.FLIGGY_READ_ALL_DATASETS),
            )


class _isolated_browser_state:
    def __enter__(self) -> "_isolated_browser_state":
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_browser_state_dir = os.environ.get("BROWSER_STATE_DIR")
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["BROWSER_STATE_DIR"] = str(Path(self._tmp.name) / "browser-state")
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'shops.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        importlib.reload(importlib.import_module("app.db.session"))
        from tests._db_isolation import create_isolated_test_shop

        self.shop_key = create_isolated_test_shop("EBK 接口测试店铺")
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))
        importlib.reload(importlib.import_module("app.api.v1.browser_sessions"))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_browser_state_dir is None:
            os.environ.pop("BROWSER_STATE_DIR", None)
        else:
            os.environ["BROWSER_STATE_DIR"] = self._previous_browser_state_dir
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))
        self._tmp.cleanup()


def _reload_app():
    main = importlib.import_module("app.main")
    return importlib.reload(main).create_app()


if __name__ == "__main__":
    unittest.main()


class FliggyOperatingTodayEvidenceTests(unittest.TestCase):
    """T1.6：经营数据「今日」页签证据（区块更新时间为当日）由 parser 提取。"""

    def test_operating_parser_marks_today_evidence_when_update_time_is_today(self) -> None:
        from datetime import datetime

        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        today = datetime.now().strftime("%Y/%m/%d")
        text = "\n".join(
            [
                "在店数据",
                f"数据更新时间 {today} 13:11",
                "在店间夜",
                "6间夜",
                "房费收入",
                "3,148元",
                "间夜单价",
                "525元",
                "预订数据",
                f"数据更新时间 {today} 13:11",
                "预订间夜",
                "9间夜",
            ]
        )
        payload = fliggy.parse_fliggy_operating_text(text, current_url=FLIGGY_OPERATING_URL)
        self.assertEqual(payload["status"], "success")
        evidence = payload["data"]["today_evidence"]
        self.assertTrue(evidence["today_confirmed"])
        self.assertEqual(len(evidence["section_update_times"]), 2)
        self.assertIn(today, evidence["section_update_times"][0])

    def test_operating_parser_no_today_evidence_when_update_time_is_stale(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        payload = fliggy.parse_fliggy_operating_text(
            FLIGGY_REAL_OPERATING,
            current_url=FLIGGY_OPERATING_URL,
        )
        self.assertEqual(payload["status"], "success")
        self.assertNotIn("today_evidence", payload["data"])
