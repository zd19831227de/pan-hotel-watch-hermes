from __future__ import annotations

import unittest

"""周/月维度页面解析器测试：fixture 均来自 2026-09-07 三平台真实页面文本采集。"""


class CtripBeneficialPeriodParserTests(unittest.TestCase):
    def test_parse_week_view_three_groups(self) -> None:
        from app.domain.browser.ebk_period_read_flows import (
            _parse_ctrip_beneficial_period_sections,
        )

        body = "\n".join(
            [
                "总平台 / 携程 / 同程旅行 / 去哪儿",
                "预订",
                "实时",
                "按日",
                "按周",
                "按月",
                "按季",
                "自定义",
                "预订订单数",
                "369",
                "去年同期 269",
                "去年 同比  37.17%",
                "间夜量",
                "694",
                "去年同期 429",
                "去年 同比  61.77%",
                "销售额",
                "24.01万",
                "去年同期 15.79万",
                "去年同比  52.04%",
                "本周预订订单数 369 去年 同期 269",
                "下载数据",
                "在店",
                "实时",
                "按日",
                "按周",
                "按月",
                "按季",
                "自定义",
                "间夜量",
                "366",
                "去年 同期 406",
                "去年 同比  -9.85%",
                "紧张度",
                "41.14%",
                "去年 同期 42.2%",
                "出租率",
                "29.88%",
                "去年 同期 33.14%",
                "本周间夜量 366 去年 同期 406",
                "下载数据",
                "离店",
                "按日",
                "按周",
                "按月",
                "按季",
                "自定义",
                "间夜量",
                "381",
                "去年 同期 432",
                "去年 同比  -11.81%",
                "销售额",
                "10.89万",
                "去年 同期 15.22万",
                "成交率",
                "59.72%",
                "去年 同期 69.68%",
                "平均卖价",
                "285.79",
                "去年 同期 352.24",
                "本周间夜量 381 去年 同期 432",
                "下载数据",
                "隐私政策规则中心",
            ]
        )
        sections = _parse_ctrip_beneficial_period_sections(body, period="week")
        self.assertEqual(
            sorted(sections.keys()), ["在店", "离店", "预订"], "三组指标都应解析出来"
        )
        booking = sections["预订"]
        self.assertEqual(booking["预订订单数"]["value"], "369")
        self.assertEqual(booking["预订订单数"]["comparison"]["去年同期"], "269")
        self.assertIn("37.17%", booking["预订订单数"]["comparison"]["去年同比"])
        self.assertEqual(booking["间夜量"]["value"], "694")
        self.assertEqual(booking["销售额"]["value"], "24.01万")
        in_house = sections["在店"]
        self.assertEqual(in_house["间夜量"]["value"], "366")
        self.assertEqual(in_house["出租率"]["value"], "29.88%")
        departure = sections["离店"]
        self.assertEqual(departure["间夜量"]["value"], "381")
        self.assertEqual(departure["平均卖价"]["value"], "285.79")
        # 周视图的证据行（「本周间夜量 …」）应被保留为口径佐证。
        self.assertEqual(len(sections["预订"]["_summary_lines"]), 1)

    def test_parse_month_view_period_label(self) -> None:
        from app.domain.browser.ebk_period_read_flows import (
            _parse_ctrip_beneficial_period_sections,
        )

        body = "\n".join(
            [
                "离店",
                "按日",
                "按周",
                "按月",
                "按季",
                "自定义",
                "2026年9月",
                "间夜量",
                "881",
                "去年 同期 2401",
                "去年 同比  -63.31%",
                "销售额",
                "26.64万",
                "去年 同期 72.89万",
                "本月间夜量 881 去年 同期 2401",
                "下载数据",
                "隐私政策规则中心",
            ]
        )
        sections = _parse_ctrip_beneficial_period_sections(body, period="month")
        departure = sections["离店"]
        self.assertEqual(departure["间夜量"]["value"], "881")
        self.assertEqual(departure["间夜量"]["comparison"]["去年同期"], "2401")
        # 月视图的「2026年9月」周期标记应被识别。
        self.assertEqual(departure["_period_label"], "2026年9月")


class CtripCompetitionRankingParserTests(unittest.TestCase):
    def test_parse_last_week_rankings(self) -> None:
        from app.domain.browser.ebk_period_read_flows import (
            _parse_ctrip_competition_rankings,
        )

        body = "\n".join(
            [
                "销售排名",
                "实时",
                "昨日",
                "上周",
                "上月",
                "自定义",
                "下载数据",
                "过去一周，您的「在店间夜量」排名下降5名，「出租率」排名下降4名",
                "酒店名称",
                "预订订单量排名",
                "预订销售额排名",
                "在店间夜排名",
                "出租率排名",
                "栖酒店(云鹤楼长江大桥店)",
                "5",
                "5",
                "我的酒店",
                "4",
                "6",
                "12",
                "14",
                "查看更多",
                "流量排名",
                "携程",
                "去哪儿",
                "实时",
                "昨日",
                "上周",
                "上月",
                "自定义",
                "下载数据",
                "过去一周，您的「APP详情页转化率」排名下降10名",
                "酒店名称",
                "APP详情页访客量排名",
                "APP详情页转化率排名",
                "云州汉埠喜隆登大酒店",
                "4",
                "13",
                "我的酒店",
                "13",
                "12",
                "查看更多",
                "服务排名",
                "下载数据",
            ]
        )
        result = _parse_ctrip_competition_rankings(body)
        self.assertEqual(result["overview"], "过去一周，您的「在店间夜量」排名下降5名，「出租率」排名下降4名")
        self.assertEqual(result["rankings"]["预订订单量排名"], "4")
        self.assertEqual(result["rankings"]["预订销售额排名"], "6")
        self.assertEqual(result["rankings"]["在店间夜排名"], "12")
        self.assertEqual(result["rankings"]["出租率排名"], "14")
        self.assertEqual(result["rankings"]["APP详情页访客量排名"], "13")
        self.assertEqual(result["rankings"]["APP详情页转化率排名"], "12")


class MeituanTradePeriodParserTests(unittest.TestCase):
    def test_parse_week_metric_cards(self) -> None:
        from app.domain.browser.ebk_period_read_flows import (
            _parse_meituan_trade_period_metrics,
        )

        body = "\n".join(
            [
                "门店",
                "静里全套房酒店（云州澜江路望川路地铁站店）",
                "经营表现数据",
                "今日实时按日按周按月自定义",
                "销售间夜",
                "155",
                "较上期",
                "23.02%",
                "较去年",
                "-",
                "同行排名",
                "3/11",
                "销售额",
                "4.48万元",
                "较上期",
                "4.26%",
                "较去年",
                "-",
                "同行排名",
                "3/11",
                "支付订单数",
                "116",
                "较上期",
                "17.17%",
                "同行排名",
                "3/11",
                "平均房价",
                "276.27元",
                "较上期",
                "21.50%",
                "同行排名",
                "3/11",
                "间夜",
                "房型销售排行",
            ]
        )
        metrics = _parse_meituan_trade_period_metrics(body)
        self.assertEqual(metrics["销售间夜"]["value"], "155")
        self.assertEqual(metrics["销售间夜"]["vs_prev_period"], "23.02%")
        self.assertEqual(metrics["销售间夜"]["vs_last_year"], "-")
        self.assertEqual(metrics["销售间夜"]["peer_rank"], "3/11")
        self.assertEqual(metrics["销售额"]["value"], "4.48万元")
        self.assertEqual(metrics["支付订单数"]["peer_rank"], "3/11")
        self.assertEqual(metrics["平均房价"]["value"], "276.27元")
        self.assertNotIn("间夜", metrics, "表头片段不应被当作指标")


class FliggyWeeklyReportParserTests(unittest.TestCase):
    def test_parse_weekly_dom_tables(self) -> None:
        from app.domain.browser.ebk_period_read_flows import _parse_fliggy_weekly_tables

        tables = [
            {
                "header": ["", "在店间夜", "房费收入", "间夜单价"],
                "rows": [
                    ["上周 08/31-09/06", "14间夜", "3,477.0元", "248.4元"],
                    ["上上周 08/24-08/30", "38间夜", "15,719.0元", "413.7元"],
                    ["环比", "63%", "78%", "40%"],
                    ["同行均值", "0间夜", "0.0元", "0.0元"],
                    ["同行排名", "", "", ""],
                ],
            },
            {
                "header": ["", "预订间夜", "预订收入金额", "预订订单"],
                "rows": [
                    ["上周 08/31-09/06", "23间夜", "5,702.2元", "16单"],
                    ["上上周 08/24-08/30", "37间夜", "14,522.8元", "32单"],
                    ["环比", "38%", "61%", "50%"],
                    ["同行均值", "0间夜", "0.0元", "0单"],
                    ["同行排名", "", "", ""],
                ],
            },
            {
                "header": ["酒店名称", "在店间夜", "预订订单", "预订转化率"],
                "rows": [["我的酒店", "", "", ""]],
            },
        ]
        result = _parse_fliggy_weekly_tables(tables)
        self.assertEqual(result["weekly_period_label"], "08/31-09/06")
        stay = result["sections"]["在店数据"]
        self.assertEqual(stay["columns"], ["在店间夜", "房费收入", "间夜单价"])
        self.assertEqual(stay["上周"]["在店间夜"], "14间夜")
        self.assertEqual(stay["上上周"]["房费收入"], "15,719.0元")
        self.assertEqual(stay["环比"]["间夜单价"], "40%")
        self.assertEqual(stay["同行排名"]["在店间夜"], "")
        booking = result["sections"]["预订数据"]
        self.assertEqual(booking["上周"]["预订订单"], "16单")
        self.assertEqual(booking["环比"]["预订间夜"], "38%")
        # 同行榜单表（我的酒店行）单独透出。
        self.assertEqual(result["peer_board"]["我的酒店"]["在店间夜"], "")

    def test_parse_flattened_dom_shape(self) -> None:
        """已挂载 SPA 内切换周报时，整组数据被压平进一个 tr 且重复多行。"""
        from app.domain.browser.ebk_period_read_flows import _parse_fliggy_weekly_tables

        flattened_row = [
            "上周 08/31-09/06",
            "14间夜",
            "3,477.0元",
            "248.4元",
            "上上周 08/24-08/30",
            "38间夜",
            "15,719.0元",
            "413.7元",
            "环比",
            "63%",
            "78%",
            "40%",
            "同行均值",
            "0间夜",
            "0.0元",
            "0.0元",
            "同行排名",
            "",
            "",
            "",
        ]
        tables = [
            {
                "header": ["", "在店间夜", "房费收入", "间夜单价"],
                "rows": [flattened_row, flattened_row],
            }
        ]
        result = _parse_fliggy_weekly_tables(tables)
        stay = result["sections"]["在店数据"]
        self.assertEqual(result["weekly_period_label"], "08/31-09/06")
        self.assertEqual(stay["上周"]["在店间夜"], "14间夜")
        self.assertEqual(stay["上上周"]["房费收入"], "15,719.0元")
        self.assertEqual(stay["环比"]["间夜单价"], "40%")
        self.assertEqual(stay["同行均值"]["房费收入"], "0.0元")
        self.assertEqual(stay["同行排名"]["在店间夜"], "")


class FliggyOperatingPeriodParserTests(unittest.TestCase):
    def test_parse_operating_sections(self) -> None:
        from app.domain.browser.ebk_period_read_flows import (
            _parse_fliggy_operating_period_sections,
        )

        body = "\n".join(
            [
                "在店数据",
                "数据更新时间 2026/09/06",
                "全渠道 / 阿里商旅",
                "今日 / 昨日 / 近7天 / 近30天",
                "在店间夜",
                "4间夜",
                "暂无同期数据",
                "暂无同行对比",
                "房费收入",
                "880元",
                "暂无同期数据",
                "暂无同行对比",
                "间夜单价",
                "220元",
                "近期数据趋势",
                "预订数据",
                "数据更新时间 2026/09/07 14:18",
                "今日 / 昨日 / 近7天 / 近30天",
                "预订间夜",
                "1间夜",
                "暂无同期数据",
                "暂无同行对比",
                "预订收入金额",
                "205元",
                "预订订单",
                "1单",
                "近期数据趋势",
            ]
        )
        sections = _parse_fliggy_operating_period_sections(body)
        self.assertEqual(sections["在店数据"]["在店间夜"]["value"], "4间夜")
        self.assertIn(
            "暂无同行对比", sections["在店数据"]["在店间夜"]["context_lines"]
        )
        self.assertIn(
            "暂无同期数据", sections["在店数据"]["在店间夜"]["context_lines"]
        )
        self.assertEqual(sections["预订数据"]["预订收入金额"]["value"], "205元")
        self.assertEqual(sections["预订数据"]["预订订单"]["value"], "1单")


class CtripFlowPeriodParserTests(unittest.TestCase):
    """携程流量数据页（flowdata）指标解析：fixture 来自 2026-09-16 真实页面文本采集。"""

    def test_parse_flow_metrics_from_real_page_lines(self) -> None:
        from app.domain.browser.ebk_period_read_flows import _parse_ctrip_flow_section

        body = "\n".join(
            [
                "携程",
                "去哪儿",
                "今日实时",
                "昨天",
                "过去7天",
                "过去30天",
                "自定义",
                "全部",
                "手机版APP",
                "流量数据概况",
                "访客量",
                "554",
                "较上周同期 15.66%",
                "竞争圈平均 626",
                "转化率",
                "5.05%",
                "较上周同期 27.20%",
                "竞争圈平均 4.19%",
                "APP流量转化",
                "APP漏斗指标说明",
                "订单页访客量",
                "统计周期内，订单填写页浏览人数，一个用户访问多次仅计算一次",
                "来自列表页",
                "来自非列表页",
                "我的酒店",
                "曝光转化率",
                "18.48%",
                "下单转化率",
                "8.18%",
                "成交转化率",
                "43.75%",
                "竞争圈平均",
                "曝光转化率",
                "12.34%",
                "下单转化率",
                "8.00%",
                "成交转化率",
                "50.00%",
                "列表页",
                "详情页",
                "订单页",
                "订单提交量",
                "流量来源",
                "查看更多",
            ]
        )
        section = _parse_ctrip_flow_section(body)
        metrics = section["流量"]
        self.assertEqual(metrics["访客量"]["value"], "554")
        self.assertIn("15.66%", metrics["访客量"]["change"])
        self.assertEqual(metrics["访客量"]["competition_average"], "626")
        self.assertEqual(metrics["转化率"]["value"], "5.05%")
        self.assertEqual(metrics["转化率"]["competition_average"], "4.19%")
        self.assertEqual(metrics["曝光转化率"]["value"], "18.48%")
        self.assertEqual(metrics["曝光转化率"]["competition_average"], "12.34%")
        self.assertEqual(metrics["下单转化率"]["value"], "8.18%")
        self.assertEqual(metrics["成交转化率"]["value"], "43.75%")

    def test_returns_empty_section_when_page_empty(self) -> None:
        from app.domain.browser.ebk_period_read_flows import _parse_ctrip_flow_section

        self.assertEqual(_parse_ctrip_flow_section("隐私政策\n规则中心"), {"流量": {}})


class MeituanDayDataLabelTests(unittest.TestCase):
    def test_prefers_update_keyword_line(self) -> None:
        from app.domain.browser.ebk_period_read_flows import _extract_meituan_day_data_label
        self.assertEqual(
            _extract_meituan_day_data_label(
                "销售额\n数据更新时间 2026/09/08 08:00\n按日"
            ),
            "2026/09/08",
        )

    def test_falls_back_to_first_date(self) -> None:
        from app.domain.browser.ebk_period_read_flows import _extract_meituan_day_data_label
        self.assertEqual(
            _extract_meituan_day_data_label("2026-09-08 销售额"),
            "2026-09-08",
        )

    def test_returns_none_when_no_date(self) -> None:
        from app.domain.browser.ebk_period_read_flows import _extract_meituan_day_data_label
        self.assertIsNone(_extract_meituan_day_data_label("销售额\n按日"))


if __name__ == "__main__":
    unittest.main()
