"""美团推广通 / 飞猪直通车 广告数据集测试（2026-09-11 立瑞新店需求）。

fixture 文本取自真实页面 CDP 抓取：
- 美团推广通内容帧（ebmidas.dianping.com/shopdiy-node/main）：
  立瑞已开通（推广中 1 个）与静里未开通（推广中 0 个）两份。
- 飞猪付费推广-直通车（#/bp/ebk-home）：立瑞已开通（智能投放托管卡）
  与静里未开通（重定向 self-service-application，页面明示未开通商业化权限）。
"""

from __future__ import annotations

import importlib
import unittest

MEITUAN_AD_CPC_ACTIVE_TEXT = """
财务概览
自动续费待开启
详情
推广通昨日总花费(元)
0.00
推广通今日总花费(元)
0.00
推广通账户余额(元)
0.00
充值
我的优惠
推广通
新建推广
进入
推广概览
查看数据
花费(元)
0.00
曝光(次)
0
点击(次)
0
今日预算(元)
220
推广中(个)
1
待优化(个)
0
推广课堂
查看全部
【推广通】服务规则
餐饮
学习培训
休闲娱乐
""".strip()

MEITUAN_AD_CPC_INACTIVE_TEXT = """
财务概览
自动续费待开启
详情
推广通昨日总花费(元)
0.00
推广通今日总花费(元)
0.00
推广通账户余额(元)
0.00
充值
我的优惠
推广通
新建推广
进入
推广概览
查看数据
花费(元)
0.00
曝光(次)
0
点击(次)
0
今日预算(元)
0
推广中(个)
0
待优化(个)
0
推广课堂
查看全部
【推广通】服务规则
""".strip()

FLIGGY_AD_P4P_ACTIVE_TEXT = """
付费推广-直通车
首页
效果推广
智能投放
资产管理中心
我的授权
基础信息
待办通知
数据中心
反馈问题！
数据总览
今日
昨日
过去7日
总花费
/元
0.00
广告引导浏览量
0
广告引导订单金额
/元
0.00
ROI
0
间夜量
0
过去7日ROI趋势
去充值
广告效果转化概况更新频率：小时级更新
今日
昨日
过去7日
浏览量
0
点击量
0
购买量
0
点击率
0%
转化率
0%
生意诊断
查看诊断
过去7日间夜量
18
周环比-25.00%
过去7日浏览量
1539
周环比11.04%
过去7日预定转化率
1.61%
周环比-0.53%
弱
健康
健康
诊断建议
您酒店近一周间夜量增速较同商圈竞对酒店落后22.98%。建议持续开启智能投放。
智能投放
小搜直通车
托管配置
智能投放
酒店智能营销托管_2026-06-24
暂停
修改
开始时间
2026.06/24-2222.01/01
今日预估花费 / 元
￥362
累计花费
￥1260.78
累计浏览量
29689
累计引导间夜数
27
商圈榜单排名
过去7日
过去30日
过去90日
酒店名称
商圈曝光排名
""".strip()

FLIGGY_AD_P4P_INACTIVE_TEXT = """
付费推广-直通车
抱歉，您还未入驻商业化
亲，您目前登陆的账户是 【hotel1144352】，还未开通商业化权限。
关联酒店为 【静里公寓酒店（云州中心广场望川路地铁站店）】，HID【841107102433】
您可立即申请开通权限，同时需要填写一个手机号接收开通结果，后续结果将通过短信通知您
行业
请选择
请输入联系方式
立即申请
我要反馈
""".strip()


class MeituanAdCpcParserTests(unittest.TestCase):
    def _parse(self, text: str):
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        return meituan.parse_meituan_dataset_text(
            text,
            current_url=(
                "https://me.meituan.com/ebooking/merchant/fullPathIframe?"
                "iUrl=https%3A%2F%2Febmidas.dianping.com%2Fshopdiy%2Faccount%2FpcCpcEntry"
            ),
            dataset="ad_cpc",
        )

    def test_active_account_extracts_finance_and_overview(self) -> None:
        payload = self._parse(MEITUAN_AD_CPC_ACTIVE_TEXT)

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["platform"], "meituan")
        self.assertEqual(payload["dataset"], "ad_cpc")
        data = payload["data"]
        self.assertTrue(data["activated"])
        self.assertEqual(
            data["finance_overview"],
            {
                "昨日总花费(元)": "0.00",
                "今日总花费(元)": "0.00",
                "账户余额(元)": "0.00",
            },
        )
        self.assertEqual(data["promotion_overview"]["花费(元)"], "0.00")
        self.assertEqual(data["promotion_overview"]["曝光(次)"], "0")
        self.assertEqual(data["promotion_overview"]["点击(次)"], "0")
        self.assertEqual(data["promotion_overview"]["今日预算(元)"], "220")
        self.assertEqual(data["promotion_overview"]["推广中(个)"], "1")
        self.assertEqual(data["promotion_overview"]["待优化(个)"], "0")

    def test_inactive_account_is_success_without_activation(self) -> None:
        payload = self._parse(MEITUAN_AD_CPC_INACTIVE_TEXT)

        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertFalse(data["activated"])
        self.assertEqual(data["promotion_overview"]["推广中(个)"], "0")
        self.assertEqual(data["promotion_overview"]["今日预算(元)"], "0")

    def test_unrecognized_page_is_needs_review(self) -> None:
        payload = self._parse("工作台 暂无数据")

        self.assertEqual(payload["status"], "needs_review")


class FliggyAdP4pParserTests(unittest.TestCase):
    def _parse(self, text: str):
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        return fliggy.parse_fliggy_dataset_text(
            text,
            current_url="https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/bp/ebk-home",
            dataset="ad_p4p",
        )

    def test_active_account_extracts_overview_conversion_and_campaign(self) -> None:
        payload = self._parse(FLIGGY_AD_P4P_ACTIVE_TEXT)

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["platform"], "fliggy")
        self.assertEqual(payload["dataset"], "ad_p4p")
        data = payload["data"]
        self.assertTrue(data["activated"])
        self.assertEqual(
            data["overview"],
            {
                "总花费(元)": "0.00",
                "广告引导浏览量": "0",
                "广告引导订单金额(元)": "0.00",
                "ROI": "0",
                "间夜量": "0",
            },
        )
        self.assertEqual(
            data["conversion"],
            {
                "浏览量": "0",
                "点击量": "0",
                "购买量": "0",
                "点击率": "0%",
                "转化率": "0%",
            },
        )
        campaign = data["campaign"]
        self.assertEqual(campaign["name"], "酒店智能营销托管_2026-06-24")
        self.assertEqual(campaign["status"], "暂停")
        self.assertEqual(campaign["start_time"], "2026.06/24-2222.01/01")
        self.assertEqual(campaign["today_estimated_spend"], "￥362")
        self.assertEqual(campaign["total_spend"], "￥1260.78")
        self.assertEqual(campaign["total_views"], "29689")
        self.assertEqual(campaign["total_room_nights"], "27")

    def test_inactive_account_is_success_without_activation(self) -> None:
        payload = self._parse(FLIGGY_AD_P4P_INACTIVE_TEXT)

        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertFalse(data["activated"])
        self.assertEqual(data.get("overview") or {}, {})
        self.assertEqual(data.get("campaign") or {}, {})

    def test_nonzero_spend_without_campaign_still_counts_activated(self) -> None:
        """只投「小搜直通车」（无智能投放托管计划）但有消耗：仍算已开通。"""
        text = FLIGGY_AD_P4P_ACTIVE_TEXT.replace(
            "酒店智能营销托管_2026-06-24", "暂停"
        ).replace("总花费\n/元\n0.00", "总花费\n/元\n18.50", 1)
        payload = self._parse(text)

        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertEqual(data.get("campaign"), {})
        self.assertTrue(data["activated"])
        self.assertEqual(data["overview"]["总花费(元)"], "18.50")

    def test_unrecognized_page_is_needs_review(self) -> None:
        payload = self._parse("首页 交易管理 暂无数据")

        self.assertEqual(payload["status"], "needs_review")


class AdDatasetReadAllListsTests(unittest.TestCase):
    def test_read_all_dataset_lists_include_ad_datasets(self) -> None:
        reader = importlib.import_module("app.domain.ebk_snapshots.ebk_snapshot_reader")

        self.assertIn("ad_cpc", reader.MEITUAN_READ_ALL_DATASETS)
        self.assertIn("ad_p4p", reader.FLIGGY_READ_ALL_DATASETS)


def _ad_dataset(dataset_id: str, data: dict) -> dict:
    return {"status": "success", "label": dataset_id, "data": data}


class MeituanAdAdapterTests(unittest.TestCase):
    def _rows(self, datasets: dict):
        adapters = importlib.import_module("app.domain.ebk_global_push.adapters")
        return adapters.meituan_rows_from_snapshot_payload(
            {"payload": {"datasets": datasets}}
        )

    def test_active_ad_account_emits_advertising_rows(self) -> None:
        datasets = {
            "workbench_home": _ad_dataset(
                "workbench_home", {"realtime_data": {"今日实时销售额": "100"}}
            ),
            "ad_cpc": _ad_dataset(
                "ad_cpc",
                {
                    "activated": True,
                    "finance_overview": {
                        "今日总花费(元)": "0.00",
                        "昨日总花费(元)": "12.30",
                        "账户余额(元)": "56.78",
                    },
                    "promotion_overview": {
                        "花费(元)": "0.00",
                        "曝光(次)": "0",
                        "点击(次)": "0",
                        "今日预算(元)": "220",
                        "推广中(个)": "1",
                        "待优化(个)": "0",
                    },
                },
            ),
        }

        rows, needs_review = self._rows(datasets)

        ad_rows = [row for row in rows if row["dataset_id"] == "ad_cpc"]
        self.assertTrue(ad_rows)
        by_label = {row["label"]: row for row in ad_rows}
        self.assertEqual(by_label["今日总花费(元)"]["value"], "0.00")
        self.assertEqual(by_label["曝光(次)"]["value"], "0")
        self.assertEqual(by_label["推广中(个)"]["value"], "1")
        # 概览「花费(元)」与财务「今日总花费(元)」重复，不再输出
        self.assertNotIn("花费(元)", by_label)
        self.assertEqual(by_label["今日总花费(元)"]["dimension"], "广告")
        self.assertNotIn("ad_cpc", needs_review)

    def test_inactive_ad_account_emits_no_rows_and_no_needs_review(self) -> None:
        datasets = {
            "workbench_home": _ad_dataset(
                "workbench_home", {"realtime_data": {"今日实时销售额": "100"}}
            ),
            "ad_cpc": _ad_dataset(
                "ad_cpc",
                {
                    "activated": False,
                    "finance_overview": {
                        "今日总花费(元)": "0.00",
                        "昨日总花费(元)": "0.00",
                        "账户余额(元)": "0.00",
                    },
                    "promotion_overview": {"推广中(个)": "0"},
                },
            ),
        }

        rows, needs_review = self._rows(datasets)

        self.assertFalse([row for row in rows if row["dataset_id"] == "ad_cpc"])
        # 未开通是合法读取结果，不计入 needs_review 汇总
        self.assertNotIn("ad_cpc", needs_review)


class FliggyAdAdapterTests(unittest.TestCase):
    def _rows(self, datasets: dict):
        adapters = importlib.import_module("app.domain.ebk_global_push.adapters")
        return adapters.fliggy_rows_from_snapshot_payload(
            {"payload": {"datasets": datasets}}
        )

    def test_active_ad_account_emits_advertising_rows(self) -> None:
        datasets = {
            "home_v1": _ad_dataset("home_v1", {"today_data": {"销售额": "100"}}),
            "ad_p4p": _ad_dataset(
                "ad_p4p",
                {
                    "activated": True,
                    "overview": {
                        "总花费(元)": "0.00",
                        "广告引导浏览量": "0",
                        "广告引导订单金额(元)": "0.00",
                        "ROI": "0",
                        "间夜量": "0",
                    },
                    "conversion": {"点击率": "0%", "转化率": "0%"},
                    "campaign": {
                        "name": "酒店智能营销托管_2026-06-24",
                        "status": "暂停",
                        "today_estimated_spend": "￥362",
                        "total_spend": "￥1260.78",
                        "total_views": "29689",
                        "total_room_nights": "27",
                    },
                    "today_evidence": {"today_confirmed": True},
                },
            ),
        }

        rows, needs_review = self._rows(datasets)

        ad_rows = [row for row in rows if row["dataset_id"] == "ad_p4p"]
        self.assertTrue(ad_rows)
        by_label = {row["label"]: row for row in ad_rows}
        self.assertEqual(by_label["智能投放计划"]["value"], "酒店智能营销托管_2026-06-24")
        self.assertEqual(by_label["投放状态"]["value"], "暂停")
        self.assertEqual(by_label["总花费(元)"]["value"], "0.00")
        self.assertEqual(by_label["总花费(元)"]["freshness"], "realtime")
        self.assertEqual(by_label["点击率"]["value"], "0%")
        self.assertNotIn("ad_p4p", needs_review)
        # 2026-09-16 运营要求：直通车只展示实时数据，累计/预估口径四项不再进卡。
        for legacy_label in (
            "今日预估花费(元)",
            "累计花费(元)",
            "累计浏览量",
            "累计引导间夜数",
        ):
            self.assertNotIn(legacy_label, by_label)

    def test_active_without_today_evidence_uses_current_state(self) -> None:
        datasets = {
            "ad_p4p": _ad_dataset(
                "ad_p4p",
                {
                    "activated": True,
                    "overview": {"总花费(元)": "1.50"},
                    "conversion": {},
                    "campaign": {"name": "托管A", "status": "投放中"},
                },
            ),
        }

        rows, _needs = self._rows(datasets)

        by_label = {row["label"]: row for row in rows}
        self.assertEqual(by_label["总花费(元)"]["freshness"], "current_state")

    def test_inactive_ad_account_emits_no_rows_and_no_needs_review(self) -> None:
        datasets = {
            "home_v1": _ad_dataset("home_v1", {"today_data": {"销售额": "100"}}),
            "ad_p4p": _ad_dataset("ad_p4p", {"activated": False}),
        }

        rows, needs_review = self._rows(datasets)

        self.assertFalse([row for row in rows if row["dataset_id"] == "ad_p4p"])
        self.assertNotIn("ad_p4p", needs_review)


class AdProjectionWhitelistTests(unittest.TestCase):
    def test_ad_datasets_map_to_advertising_category(self) -> None:
        projection = importlib.import_module("app.domain.ebk_global_push.projection")
        from app.domain.ebk_global_push.adapters import _row  # noqa: PLC2701

        meituan_row = _row(
            platform="meituan",
            dataset_id="ad_cpc",
            field_key="曝光(次)",
            label="曝光(次)",
            value="0",
            freshness="current_state",
            captured_at=None,
        )
        fliggy_row = _row(
            platform="fliggy",
            dataset_id="ad_p4p",
            field_key="总花费(元)",
            label="总花费(元)",
            value="0.00",
            freshness="current_state",
            captured_at=None,
        )

        accepted, rejected = projection.normalize_projection_rows(
            [meituan_row, fliggy_row]
        )

        self.assertEqual(len(accepted), 2)
        self.assertTrue(all(row["category"] == "advertising" for row in accepted))
        self.assertEqual(rejected, [])


class AdCardRenderingTests(unittest.TestCase):
    @staticmethod
    def _row(platform, dataset_id, label, value, *, freshness="current_state"):
        return {
            "platform": platform,
            "dataset_id": dataset_id,
            "field_key": label,
            "label": label,
            "value": value,
            "value_type": "text",
            "unit": "",
            "time_scope": freshness,
            "freshness": freshness,
            "captured_at": None,
            "source_path": f"datasets.{dataset_id}.data.{label}",
            "dimension": "广告",
        }

    def _build(self, sections):
        cards_module = importlib.import_module(
            "app.domain.ebk_global_push.global_push_cards"
        )
        return cards_module.build_global_push_cards(
            store_name="示例门店",
            read_time="2026-09-11T12:00:00+08:00",
            overall_status="success",
            overall_summary="读取成功",
            platform_sections=sections,
        )

    @staticmethod
    def _text(cards) -> str:
        parts: list[str] = []
        for card in cards:
            for element in card["elements"]:
                if not isinstance(element, dict):
                    continue
                if element.get("tag") == "div":
                    parts.append(str(element.get("text", {}).get("content", "")))
                elif element.get("tag") == "table":
                    for column in element.get("columns", []):
                        parts.append(str(column.get("display_name") or ""))
                    for row in element.get("rows", []):
                        parts.append(" ".join(str(v) for v in row.values()))
        return "\n".join(parts)

    def test_meituan_fliggy_ad_rows_render_real_data(self) -> None:
        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [
                    self._row("ctrip", "ad_pyramid", "点击(次)", "12",
                              freshness="realtime"),
                ],
            },
            {
                "platform": "meituan",
                "status": "success",
                "rows": [
                    self._row("meituan", "ad_cpc", "今日总花费(元)", "0.00"),
                    self._row("meituan", "ad_cpc", "推广中(个)", "1"),
                ],
            },
            {
                "platform": "fliggy",
                "status": "success",
                "rows": [
                    self._row("fliggy", "ad_p4p", "总花费(元)", "0.00"),
                    self._row("fliggy", "ad_p4p", "智能投放计划", "酒店智能营销托管"),
                ],
            },
        ]

        cards = self._build(sections)
        text = self._text(cards["advertising"])

        self.assertIn("美团·推广通", text)
        self.assertIn("飞猪·直通车", text)
        # 「今日」前缀按卡片口径统一剥离（与携程实时指标同规则）
        self.assertIn("总花费(元)", text)
        self.assertIn("推广中(个)", text)
        self.assertIn("智能投放计划", text)
        # 有真实广告数据的平台不再显示「未投放」
        self.assertNotIn("未投放", text)

    def test_platforms_without_ad_rows_fall_back_to_not_placed(self) -> None:
        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [
                    self._row("ctrip", "ad_pyramid", "点击(次)", "12",
                              freshness="realtime"),
                ],
            },
            {
                "platform": "meituan",
                "status": "success",
                "rows": [
                    self._row("meituan", "workbench_home", "今日实时销售额", "100",
                              freshness="realtime"),
                ],
            },
            {
                "platform": "fliggy",
                "status": "success",
                "rows": [
                    self._row("fliggy", "home_v1", "销售额", "100",
                              freshness="realtime"),
                ],
            },
        ]

        cards = self._build(sections)
        text = self._text(cards["advertising"])

        self.assertIn("未投放", text)
        self.assertEqual(text.count("未投放"), 2)


if __name__ == "__main__":
    unittest.main()
