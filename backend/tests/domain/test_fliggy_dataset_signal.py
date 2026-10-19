from __future__ import annotations

import importlib
import unittest

# 夹具取自生产快照实文（生产机A ebk_read_snapshots）：
# - HOME_*: 快照 525（2026-09-24 08:18 立瑞 needs_review）与 477（09-22 08:16 成功）。
#   早上「今日数据」全「暂无」是正常形态，成功页面靠昨日区数值撑起 signal；
#   失败页面今日+昨日都只有「暂无/暂无对比数据」骨架。
# - PROMOTION_*: 快照 525（菜单 tab 名 + 报名推广文案、无列表区）与 477/522
#   （表头「活动状态/报名时间」或「暂无数据」空确认）。菜单里的「已参加活动」
#   tab 名在页面初始 DOM 就存在，不能作为列表区已渲染的信号。

# signal 作用于轮询时的原始页面全文：「今日数据/昨日数据」是相邻 tab 头，
# 下方只渲染当前选中 tab 的数据区（解析层再按 tab 拼接成快照里的两段形态）。
HOME_LINES_ALL_EMPTY = [
    "云州澜江路立瑞酒店",
    "今日数据",
    "昨日数据",
    "销售额",
    "暂无",
    "暂无对比数据",
    "浏览人数",
    "良好",
    "竞 4/6",
    "预订订单量",
    "暂无",
    "暂无对比数据",
    "预订间夜",
    "暂无",
    "暂无对比数据",
    "流失数据",
]

HOME_LINES_YESTERDAY_FILLED = [
    "云州澜江路立瑞酒店",
    "今日数据",
    "昨日数据",
    "销售额",
    "1,122.0元",
    "周环比74%",
    "浏览人数",
    "413",
    "周环比35%",
    "预订订单量",
    "3",
    "周环比持平",
    "预订间夜",
    "4",
    "周环比56%",
    "流失数据",
]

PROMOTION_MENU_LINES = [
    "云州澜江路立瑞酒店",
    "通知",
    "商家学堂",
    "联系飞猪",
    "下载APP",
    "意见反馈",
    "中文",
    "hotel1100001",
    "首页",
    "交易管理",
    "房价房量管理",
    "营销管理",
    "评价管理",
    "财务管理",
    "数据中心",
    "诊断报告",
    "经营数据",
    "同行动态",
    "客源分析",
    "小程序数据",
    "MCI",
    "任务中心",
    "酒店管理",
    "门店店铺",
    "酒店信息维护",
    "宝贝套餐",
    "飞猪商家学堂",
    "付费推广-直通车",
    "营销助手",
    "活动报名",
    "已参加活动",
    "活动报名",
    "报名以下活动日均转化率预计提升：",
    "0%",
    "低价单风险保护 报名后可实时退出",
    "全人群运营活动",
    "面向全人群及全场景，灵活选择房型及日期，快速管理酒店价格",
    "去报名",
    "相当于原天天特惠+飞猪酒店红包/飞猪立减特惠",
    "分场景运营活动",
    "面向不同场景，获得更精准的预订机会",
    "去报名",
]

PROMOTION_LIST_HEADER_LINES = [
    "云州澜江路立瑞酒店",
    "通知",
    "营销助手",
    "活动报名",
    "已参加活动",
    "活动类型:",
    "全部",
    "活动状态:",
    "进行中",
    "查 询",
    "活动类型 房型产品 活动有效期 活动力度(活动时段)",
    "报名时间",
    "最后修改时间",
    "活动状态",
    "操作",
    "暂无数据",
]

PROMOTION_WITH_ACTIVITIES_LINES = [
    "静里公寓酒店（云州中心广场望川路地铁站店）",
    "营销助手",
    "活动报名",
    "已参加活动",
    "活动状态:",
    "进行中",
    "查 询",
    "活动类型 房型产品 活动有效期 活动力度(活动时段)",
    "报名时间",
    "分场景运营活动 36个 2026-07-24至2027-07-24",
    "2026-07-24至2026-07-24分场景运营活动立减5%",
    "投放规则预览 2026-07-24 09:59 2026-08-05 11:05 进行中",
    "查看",
    "取消",
    "日志",
]


def _fliggy():
    return importlib.import_module("app.domain.browser.fliggy_ebooking")


class FliggyHomeDatasetSignalTests(unittest.TestCase):
    def test_all_placeholder_values_do_not_signal_ready(self):
        fliggy = _fliggy()
        text = "\n".join(HOME_LINES_ALL_EMPTY)
        self.assertFalse(fliggy._has_fliggy_dataset_signal(text, dataset="home_v1"))

    def test_yesterday_values_signal_ready(self):
        fliggy = _fliggy()
        text = "\n".join(HOME_LINES_YESTERDAY_FILLED)
        self.assertTrue(fliggy._has_fliggy_dataset_signal(text, dataset="home_v1"))

    def test_rank_line_is_not_treated_as_metric_value(self):
        fliggy = _fliggy()
        # 「竞 4/6」是竞争排名展示，不是指标值；只有它时不应放行。
        text = "\n".join(
            [
                "今日数据",
                "昨日数据",
                "销售额",
                "暂无",
                "浏览人数",
                "竞 4/6",
                "流失数据",
            ]
        )
        self.assertFalse(fliggy._has_fliggy_dataset_signal(text, dataset="home_v1"))


class FliggyPromotionDatasetSignalTests(unittest.TestCase):
    def test_menu_tab_with_promo_copy_does_not_signal_ready(self):
        fliggy = _fliggy()
        text = "\n".join(PROMOTION_MENU_LINES)
        self.assertFalse(fliggy._has_fliggy_dataset_signal(text, dataset="promotion_agent"))

    def test_empty_list_header_signals_ready(self):
        fliggy = _fliggy()
        text = "\n".join(PROMOTION_LIST_HEADER_LINES)
        self.assertTrue(fliggy._has_fliggy_dataset_signal(text, dataset="promotion_agent"))

    def test_activity_rows_signal_ready(self):
        fliggy = _fliggy()
        text = "\n".join(PROMOTION_WITH_ACTIVITIES_LINES)
        self.assertTrue(fliggy._has_fliggy_dataset_signal(text, dataset="promotion_agent"))


class FliggyOtherDatasetSignalRegressionTests(unittest.TestCase):
    def test_operating_and_ad_markers_unchanged(self):
        fliggy = _fliggy()
        self.assertTrue(
            fliggy._has_fliggy_dataset_signal("在店数据\n在店间夜\n2间夜", dataset="operating_data")
        )
        self.assertTrue(
            fliggy._has_fliggy_dataset_signal("数据总览\n还未开通商业化权限", dataset="ad_p4p")
        )
        self.assertTrue(
            fliggy._has_fliggy_dataset_signal("交易数据概览\n点评得分", dataset="diagnose_report")
        )


if __name__ == "__main__":
    unittest.main()
