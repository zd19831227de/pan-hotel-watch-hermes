from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.browser.ctrip_ebooking_parsers import (
    _clean_dataset_lines,
    _extract_comment_monitor_snapshot,
    _extract_dataset_snapshot,
    _resolve_dataset_definition,
)
from app.domain.browser.ctrip_ebooking_dataset_parsers import (
    _extract_beneficial_monthly_metrics,
    _extract_business_outline_app_funnel_metrics,
    _monitor_metric,
)
from app.domain.browser.ctrip_ebooking_read_flows import (
    _ebk_dataset_page_switch_delay_ms,
    _read_beneficial_monthly_supplement,
    _read_business_outline_app_funnel,
    goto_dataset_page_with_recovery,
    read_dataset_flow,
)
from app.domain.browser.ctrip_ebooking_session_helpers import _dismiss_ebk_read_blocking_popups


class EbookingDatasetReadFlowTests(unittest.TestCase):
    def test_business_outline_app_funnel_uses_page_post_response(self) -> None:
        payload = [
            {
                "date": "2026-08-23",
                "listExposure": 1498,
                "detailExposure": 301,
                "orderFillingNum": 25,
                "orderSubmitNum": 12,
                "hotelId": 114727375,
            },
            {
                "date": "2026-08-23",
                "listExposure": 2728,
                "detailExposure": 399,
                "orderFillingNum": 30,
                "orderSubmitNum": 17,
                "hotelId": -1,
            },
        ]
        page = _FakeOutlineFunnelPage(payload)

        result = asyncio.run(_read_business_outline_app_funnel(page))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["metrics"]["list_exposure"]["value"], 1498)
        self.assertEqual(
            result["metrics"]["list_exposure"]["competition_average"],
            2728,
        )
        self.assertEqual(page.reload_calls, 1)
        self.assertTrue(page.response_predicate(page.response))
        self.assertFalse(page.response_predicate(page.get_response))

    def test_ebk_dataset_page_switch_delay_is_random_two_to_three_seconds(self) -> None:
        with patch(
            "app.domain.browser.ctrip_ebooking_read_flows.random.randint",
            return_value=2450,
        ) as randint:
            delay = _ebk_dataset_page_switch_delay_ms()

        self.assertEqual(delay, 2450)
        randint.assert_called_once_with(2_000, 3_000)

    def test_goto_dataset_page_waits_after_page_switch(self) -> None:
        service = _FakeDatasetService()
        page = _FakeDatasetPage()

        async def run() -> None:
            service.pages.append(page)
            with patch(
                "app.domain.browser.ctrip_ebooking_read_flows._ebk_dataset_page_switch_delay_ms",
                return_value=2300,
            ):
                result = await goto_dataset_page_with_recovery(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    url="https://ebooking.ctrip.com/datacenter/inland/businessreport/outline?microJump=true",
                )
            self.assertIs(result, page)

        asyncio.run(run())

        self.assertEqual(
            page.goto_calls,
            [
                {
                    "url": "https://ebooking.ctrip.com/datacenter/inland/businessreport/outline?microJump=true",
                    "wait_until": "domcontentloaded",
                    "timeout": 45_000,
                }
            ],
        )
        self.assertEqual(page.waits, [2300])


    def test_goto_dataset_page_waits_after_recovery_switch(self) -> None:
        service = _FakeDatasetService()
        stale_page = _FakeDatasetPage(stale=True)
        recovered_page = _FakeDatasetPage()

        async def run() -> None:
            service.pages.extend([stale_page, recovered_page])
            with patch(
                "app.domain.browser.ctrip_ebooking_read_flows._ebk_dataset_page_switch_delay_ms",
                return_value=2750,
            ):
                result = await goto_dataset_page_with_recovery(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    url="https://ebooking.ctrip.com/ebkgrowth/datacenter/competition/competitionprofile?microJump=true",
                )
            self.assertIs(result, recovered_page)

        asyncio.run(run())

        self.assertEqual(service.discard_calls, [("ebk-1", "browser-ebk-1")])
        self.assertEqual(stale_page.waits, [])
        self.assertEqual(recovered_page.waits, [2750])

    def test_goto_dataset_page_retries_navigation_abort_once(self) -> None:
        service = _FakeDatasetService()
        page = _FakeDatasetPage(abort_once=True)

        async def run() -> None:
            service.pages.append(page)
            with patch(
                "app.domain.browser.ctrip_ebooking_read_flows.random.randint",
                side_effect=[700, 2300],
            ):
                result = await goto_dataset_page_with_recovery(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    url="https://ebooking.ctrip.com/datacenter/inland/businessreport/outline?microJump=true",
                )
            self.assertIs(result, page)

        asyncio.run(run())

        self.assertEqual(page.goto_attempts, 2)
        self.assertEqual(len(page.goto_calls), 1)
        self.assertEqual(page.waits, [700, 2300])

    def test_goto_dataset_page_retries_two_navigation_aborts(self) -> None:
        service = _FakeDatasetService()
        page = _FakeDatasetPage(abort_count=2)

        async def run() -> None:
            service.pages.append(page)
            with patch(
                "app.domain.browser.ctrip_ebooking_read_flows.random.randint",
                side_effect=[700, 800, 2300],
            ):
                result = await goto_dataset_page_with_recovery(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    url="https://ebooking.ctrip.com/datacenter/inland/businessreport/outline?microJump=true",
                )
            self.assertIs(result, page)

        asyncio.run(run())

        self.assertEqual(page.goto_attempts, 3)
        self.assertEqual(len(page.goto_calls), 1)
        self.assertEqual(page.waits, [700, 800, 2300])

    def test_dismisses_voice_notification_autoplay_popup_before_reading(self) -> None:
        page = _FakePopupPage(
            popup_text="语音通知自动播放失败",
            button_text="知道了",
        )

        result = asyncio.run(_dismiss_ebk_read_blocking_popups(page))

        self.assertTrue(result["dismissed"])
        self.assertEqual(result["popup_type"], "voice_notification_autoplay_failed")
        self.assertEqual(result["button_text"], "知道了")
        self.assertEqual(page.clicks, ["知道了"])
        self.assertEqual(page.waits, [800])
        self.assertNotIn("语音通知自动播放失败", page.visible_text())

    def test_waits_for_delayed_voice_notification_autoplay_popup(self) -> None:
        page = _FakePopupPage(
            popup_text="语音通知自动播放失败",
            button_text="知道了",
            appear_after_waits=1,
        )

        result = asyncio.run(
            _dismiss_ebk_read_blocking_popups(page, timeout_ms=1_000, poll_ms=50)
        )

        self.assertTrue(result["dismissed"])
        self.assertEqual(result["popup_type"], "voice_notification_autoplay_failed")
        self.assertEqual(result["button_text"], "知道了")
        self.assertEqual(page.clicks, ["知道了"])
        self.assertEqual(page.waits, [50, 800])
        self.assertNotIn("语音通知自动播放失败", page.visible_text())


class EbookingDatasetParserRegressionTests(unittest.TestCase):
    def test_business_outline_app_funnel_keeps_integer_values_and_benchmark_pairs(self) -> None:
        metrics = _extract_business_outline_app_funnel_metrics(
            [
                {
                    "date": "2026-08-23",
                    "listExposure": 1498,
                    "detailExposure": 301,
                    "flowRate": 20.09,
                    "orderFillingNum": 25,
                    "orderSubmitNum": 12,
                    "hotelId": 114727375,
                },
                {
                    "date": "2026-08-23",
                    "listExposure": 2728,
                    "detailExposure": 399,
                    "flowRate": 14.63,
                    "orderFillingNum": 30,
                    "orderSubmitNum": 17,
                    "hotelId": -1,
                },
            ]
        )

        self.assertEqual(metrics["list_exposure"]["value"], 1498)
        self.assertEqual(metrics["list_exposure"]["competition_average"], 2728)
        self.assertEqual(metrics["app_detail_visitors"]["value"], 301)
        self.assertEqual(metrics["app_detail_visitors"]["competition_average"], 399)
        self.assertEqual(metrics["order_page_visitors"]["value"], 25)
        self.assertEqual(metrics["order_page_visitors"]["competition_average"], 30)
        self.assertEqual(metrics["order_submitters"]["value"], 12)
        self.assertEqual(metrics["order_submitters"]["competition_average"], 17)
        self.assertTrue(all(isinstance(metric["value"], int) for metric in metrics.values()))
        self.assertEqual(
            {metric["section"] for metric in metrics.values()},
            {"APP流量转化（昨日）"},
        )

    def test_flow_realtime_metric_labels_are_parsed(self) -> None:
        dataset = _resolve_dataset_definition("flow_data")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            流量数据
            今日实时
            实时访客量 落后
            100
            较上周同期 -6.21%
            竞争圈平均 558
            实时下单转化率(APP)
            4.66%
            较上周同期 -28.84%
            """,
            filters={},
        )

        self.assertTrue(parsed["structure_ok"])
        self.assertEqual(parsed["metrics"]["realtime_visitors"]["value"], "100")
        self.assertEqual(
            parsed["metrics"]["realtime_booking_conversion_rate"]["value"],
            "4.66%",
        )

    def test_flow_realtime_rank_and_starting_price_parsed(self) -> None:
        dataset = _resolve_dataset_definition("flow_data")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            流量转化分析
            实时排名 落后
            237
            竞争圈排名 16/21
            实时起价
            229
            竞争圈排名 3/21
            """,
            filters={},
        )

        metrics = parsed["metrics"]
        self.assertEqual(metrics["realtime_visitor_rank"]["value"], "237")
        self.assertEqual(metrics["realtime_visitor_rank"]["competitive_rank"], "16/21")
        self.assertEqual(metrics["realtime_starting_price"]["value"], "229")
        self.assertEqual(metrics["realtime_starting_price"]["competitive_rank"], "3/21")

    def test_competition_profile_inline_rows_are_successful_structure(self) -> None:
        dataset = _resolve_dataset_definition("competition_profile")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            竞争画像
            经营对比
            我的酒店 竞争圈平均 竞争圈排名
            预订订单量 我的酒店 229 竞争圈平均 217 竞争圈排名 11
            预订销售额 我的酒店 15.98万 竞争圈平均 14.61万 竞争圈排名 11
            在店间夜 我的酒店 264 竞争圈平均 321 竞争圈排名 16
            出租率 我的酒店 30.41% 竞争圈平均 31.80% 竞争圈排名 12
            携程APP访客 我的酒店 2,122 竞争圈平均 2,440 竞争圈排名 16
            携程APP转化率 我的酒店 4.34% 竞争圈平均 5.27% 竞争圈排名 20
            """,
            filters={},
        )

        self.assertTrue(parsed["structure_ok"])
        self.assertEqual(parsed["metrics"]["booking_orders"]["value"], "229")
        self.assertEqual(parsed["metrics"]["booking_orders"]["competition_average"], "217")
        self.assertEqual(parsed["metrics"]["booking_orders"]["competitive_rank"], "11")
        self.assertEqual(parsed["metrics"]["ctrip_app_conversion_rate"]["value"], "4.34%")

    def test_loss_analysis_table_rows_are_successful_structure(self) -> None:
        dataset = _resolve_dataset_definition("loss_analysis")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            流失分析
            流失订单量
            1147个
            流失间夜量
            1362
            流失订单金额
            960,302.24
            发现同行
            流失酒店TOP40
            共同浏览率
            下单转化率
            流失订单数
            操作
            云州云栖酒店
            澜江路步行街
            11.90% 3.06% 36
            查看
            """,
            filters={},
        )

        self.assertTrue(parsed["structure_ok"])
        self.assertTrue(parsed["metrics"]["lost_orders"]["value"].startswith("1147"))
        self.assertEqual(parsed["metrics"]["lost_order_amount"]["value"], "960,302.24")
        self.assertEqual(parsed["tables"][0]["rows"][0]["lost_orders"], "36")

    def test_competition_list_rank_table_rows_are_successful_structure(self) -> None:
        dataset = _resolve_dataset_definition("competition_list")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            竞争排名
            销售排名
            酒店名称
            预订订单量排名
            预订销售额排名
            在店间夜排名
            出租率排名
            我的酒店
            6
            5
            7
            8
            流量排名
            酒店名称
            APP详情页访客量排名
            APP详情页转化率排名
            我的酒店
            6
            9
            服务排名
            酒店名称
            PSI分排名
            携程点评分
            去哪儿点评分
            同程点评分
            智行点评分
            我的酒店
            7
            4.8
            4.7
            4.9
            4.6
            隐私政策规则中心
            """,
            filters={},
        )

        self.assertTrue(parsed["structure_ok"])
        self.assertEqual(parsed["metrics"]["sales_ranking_booking_order_rank"]["value"], "6")
        self.assertEqual(parsed["metrics"]["traffic_ranking_app_detail_visitor_rank"]["value"], "6")
        self.assertEqual(parsed["metrics"]["service_ranking_psi_rank"]["value"], "7")

    def test_ebk_navigation_shell_is_not_successful_business_dataset(self) -> None:
        dataset = _resolve_dataset_definition("competition_profile")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            允许浏览器通知以便快速接收订单信息及响应客人咨询
            联系携程
            订单管理
            房价房态
            促销推广
            点评问答
            """,
            filters={},
        )

        self.assertFalse(parsed["structure_ok"])
        self.assertEqual(parsed["metrics"], {})
        self.assertEqual(parsed["tables"], [])

    def test_review_analysis_snapshot_extracts_overview_reply_and_benchmark(self) -> None:
        parsed = _extract_comment_monitor_snapshot(
            body_text="""
            点评分析
            点评总体印象
            认可度最高
            酒店位置佳购物饮食方便
            客人反馈中关于酒店位置佳购物饮食方便的夸奖是最多的, 继续保持
            口碑最差Top1
            房间看不到景或景色差
            建议 准确描述酒店景观房情况，避免客诉产生
            点评概览
            点评分
            4.7
            超过34%同城同星酒店
            点评数
            2485
            好评率98.90%
            带图点评率19.50%
            30天内
            仅昨日
            好评
            37
            1
            差评
            2
            0
            回复概览
            回复率
            100.00%
            差评回复率：100.00%
            待回复差评数
            0
            上次回复：4天前
            差评回复针对性
            44.00%
            有内容的差评：25条
            点评统计
            1
            卫生
            68
            房间很干净
            酒店无异味
            2
            服务
            42
            服务态度赞
            入离店效率高体验好
            点评用户画像
            女40-50岁商务出差479至549元
            珍惜愿意写点评的客人，及时回复，形成良好循环。
            竞争圈对比
            竞争圈平均	4.76	4.79	4.78	4.73	4.77
            我的酒店	4.7	4.8	4.7	4.7	4.7
            """,
            filters={"scope": "review_analysis"},
        )

        self.assertTrue(parsed["structure_ok"])
        self.assertEqual(parsed["status_hint"], "success")
        self.assertEqual(parsed["review_overview"]["overall_score"]["value"], "4.7")
        self.assertEqual(parsed["review_overview"]["overall_score"]["competitive_rank"], "超过34%同城同星酒店")
        self.assertEqual(parsed["review_overview"]["positive_reviews_yesterday"]["value"], "1")
        self.assertEqual(parsed["review_overview"]["negative_reviews_30d"]["value"], "2")
        self.assertEqual(parsed["reply_overview"]["reply_rate"]["value"], "100.00%")
        self.assertEqual(parsed["reply_overview"]["pending_negative_reviews"]["value"], "0")
        self.assertEqual(parsed["overall_impression"]["top_positive"]["topic"], "酒店位置佳购物饮食方便")
        self.assertEqual(parsed["keyword_statistics"][0]["category"], "卫生")
        self.assertEqual(parsed["user_profile"]["profile"], "女40-50岁商务出差479至549元")
        self.assertEqual(parsed["competition_benchmark"]["competition_average"]["overall_score"], "4.76")
        self.assertEqual(parsed["competition_benchmark"]["my_hotel"]["service"], "4.7")

    def test_registered_promotions_without_visible_dates_keep_original_partial_hint(
        self,
    ) -> None:
        dataset = _resolve_dataset_definition("promotion_registered")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            促销中心
            已参加促销 2
            天天特价
            基础促销组
            生效中
            预 商务大床房（安睡床品+特色茶具）
            到期前自动延期
            不限
            立减16元
            暑期连住优惠
            场景促销组
            已报名
            高级大床房
            连住2晚享9折
            """,
            filters={},
        )

        self.assertTrue(parsed["structure_ok"])
        self.assertEqual(parsed["status_hint"], "partial")
        self.assertGreaterEqual(parsed["summary"]["visible_promotion_count"], 2)
        self.assertTrue(parsed["data_quality_notes"])

    def test_registered_promotion_table_keeps_all_offer_lines_after_name(self) -> None:
        dataset = _resolve_dataset_definition("promotion_registered")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            促销中心
            已参加促销
            促销名称 参与房型 促销房量 促销时间 促销优惠 订单储备金 操作
            场景促销组
            门店新客立减
            全房型（包括新增）
            不限
            2026年9月6日-2026年9月12日
            到期前自动延期
            立减 35%
            未使用
            暂停
            修改
            更多
            场景促销组
            连住优惠
            预露台双卧套房(复式双层双卧+豪华会客厅)(1741110276)
            不限
            2025年10月15日-2026年10月15日
            到期前自动延期
            连住2天 立减 2%
            连住3天 立减 3%
            未使用
            暂停
            修改
            更多
            场景促销组
            多间立减
            预露台双卧套房(复式双层双卧+豪华会客厅)(1741110276)
            不限
            2025年10月15日-2026年10月15日
            到期前自动延期
            3间起订 立减 3%
            2间起订 立减 2%
            未使用
            暂停
            修改
            更多
            """,
            filters={},
        )

        promotions = {item["name"]: item for item in parsed["promotions"]}
        self.assertEqual(promotions["门店新客立减"]["rules"], ["立减 35%"])
        self.assertEqual(
            promotions["连住优惠"]["rules"],
            ["连住2天 立减 2%", "连住3天 立减 3%"],
        )
        self.assertEqual(
            promotions["多间立减"]["rules"],
            ["3间起订 立减 3%", "2间起订 立减 2%"],
        )

    def test_registered_promotion_status_counts_are_not_operator_prompts(self) -> None:
        dataset = _resolve_dataset_definition("promotion_registered")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            促销中心
            已参加促销 1
            生效中 1
            已暂停 0
            基础促销组
            天天特价
            生效中
            预 商务大床房
            2026年5月25日-2027年5月25日
            不限
            立减16元
            余额不足，请及时充值
            """,
            filters={},
        )

        self.assertTrue(parsed["structure_ok"])
        self.assertNotIn("已暂停 0", parsed.get("warnings", []))
        self.assertNotIn("已暂停 0", parsed.get("page_prompts", []))
        self.assertIn("余额不足，请及时充值", parsed.get("page_prompts", []))


class EbookingDatasetParserBusinessGapTests(unittest.TestCase):
    """页面实读核验（2026-08-22）发现的解析缺口，body_text 均取自真实页面。"""

    def test_business_outline_extracts_realtime_visitor_rank(self) -> None:
        dataset = _resolve_dataset_definition("business_outline")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            流量概览
            携程
            去哪儿
            查看更多
            实时数据
            更新时间 今日 13:48
            实时访客量
            130
            上周同期 352
            实时排名 查看21天排名
            280
            竞争圈排名 13 / 21
            查看更多
            """,
            filters={},
        )

        self.assertEqual(parsed["metrics"]["realtime_visitor_rank"]["value"], "280")
        self.assertEqual(
            parsed["metrics"]["realtime_visitor_rank"]["competitive_rank"],
            "13/21",
        )

    def test_competition_profile_metric_change_not_overwritten_by_rank_change(self) -> None:
        dataset = _resolve_dataset_definition("competition_profile")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            竞争画像
            经营对比
            预订订单量
            您的酒店
            57
            较上周同期5.00%
            竞争圈平均
            48
            竞争圈排名
            5
            较上周同期1
            PSI
            您的酒店
            5.01
            较上周同期8.07%
            竞争圈平均
            5.29
            竞争圈排名
            20
            较上周同期13
            """,
            filters={},
        )

        booking_orders = parsed["metrics"]["booking_orders"]
        self.assertEqual(booking_orders["value"], "57")
        self.assertEqual(booking_orders["change"], "较上周同期5.00%")
        self.assertEqual(booking_orders["competitive_rank_change"], "1")
        psi = parsed["metrics"]["psi"]
        self.assertEqual(psi["change"], "较上周同期8.07%")
        self.assertEqual(psi["competitive_rank_change"], "13")

    def test_monitor_metric_projection_keeps_new_context_fields(self) -> None:
        projected = _monitor_metric(
            {
                "psi": {
                    "key": "psi",
                    "label": "PSI",
                    "value": "5.01",
                    "competitive_rank_change": "13",
                    "last_year_same_period": "4.9",
                    "year_over_year": "2.27%",
                }
            },
            "psi",
        )

        self.assertEqual(projected["competitive_rank_change"], "13")
        self.assertEqual(projected["last_year_same_period"], "4.9")
        self.assertEqual(projected["year_over_year"], "2.27%")

    def test_flow_data_funnel_competition_average_pairs_by_label(self) -> None:
        dataset = _resolve_dataset_definition("flow_data")
        parsed = _extract_dataset_snapshot(
            dataset=dataset,
            body_text="""
            流量数据
            流量数据概况
            访客量
            326
            较上周同期 -29.28%
            竞争圈平均 621
            APP流量转化
            2026/08/21的数据
            我的酒店
            曝光转化率
            20.21%
            下单转化率
            10.20%
            成交转化率
            48.00%
            竞争圈平均
            曝光转化率
            14.66%
            下单转化率
            7.05%
            成交转化率
            53.13%
            """,
            filters={},
        )

        metrics = parsed["metrics"]
        self.assertEqual(metrics["exposure_conversion_rate"]["value"], "20.21%")
        self.assertEqual(
            metrics["exposure_conversion_rate"]["competition_average"],
            "14.66%",
        )
        self.assertEqual(
            metrics["booking_conversion_rate"]["competition_average"],
            "7.05%",
        )
        self.assertEqual(metrics["transaction_conversion_rate"]["value"], "48.00%")
        self.assertEqual(
            metrics["transaction_conversion_rate"]["competition_average"],
            "53.13%",
        )

    def test_beneficial_monthly_metrics_parse_with_year_over_year(self) -> None:
        metrics = _extract_beneficial_monthly_metrics(
            _clean_dataset_lines(
                """
                离店
                按日
                按周
                按月
                按季
                自定义
                2026年8月
                间夜量
                2000
                去年 同期 2443
                去年 同比  -18.13%
                销售额
                72.57万
                去年 同期 92.37万
                去年 同比  -21.44%
                成交率
                57.36%
                去年 同期 61.43%
                去年 同比  -4.07%
                平均卖价
                362.83
                去年 同期 378.1
                去年 同比  -4.04%
                本月间夜量 2000 去年 同期 2443
                下载数据
                """
            )
        )

        self.assertEqual(metrics["monthly_departure_room_nights"]["value"], "2000")
        self.assertEqual(
            metrics["monthly_departure_room_nights"]["last_year_same_period"],
            "2443",
        )
        self.assertEqual(
            metrics["monthly_departure_room_nights"]["year_over_year"],
            "-18.13%",
        )
        self.assertEqual(metrics["monthly_departure_sales_amount"]["value"], "72.57万")
        self.assertEqual(
            metrics["monthly_departure_sales_amount"]["year_over_year"],
            "-21.44%",
        )
        self.assertEqual(metrics["monthly_average_selling_price"]["value"], "362.83")
        self.assertEqual(metrics["monthly_transaction_rate"]["value"], "57.36%")
        self.assertEqual(metrics["monthly_period_label"]["value"], "2026年8月")


_BENEFICIAL_MAIN_BODY = """
销售数据
酒店
房型
总平台
携程
同程旅行
去哪儿
预订
实时
按日
按周
按月
按季
自定义
预订订单数
57
上周同期 60
上周 同比  -5.00%
间夜量
104
上周同期 104
上周 同比 0.00%
销售额
4.62万
上周同期 3.51万
上周同比  31.43%
下载数据
"""

_BENEFICIAL_MONTHLY_BODY = """
销售数据
酒店
房型
总平台
携程
同程旅行
去哪儿
离店
按日
按周
按月
按季
自定义
2026年8月
间夜量
2000
去年 同期 2443
去年 同比  -18.13%
销售额
72.57万
去年 同期 92.37万
去年 同比  -21.44%
成交率
57.36%
去年 同期 61.43%
去年 同比  -4.07%
平均卖价
362.83
去年 同期 378.1
去年 同比  -4.04%
本月间夜量 2000 去年 同期 2443
下载数据
"""


class _FakeMonthlyTab:
    def __init__(self, page: "_FakeMonthlyPage", text: str, index: int) -> None:
        self.page = page
        self.text = text
        self.index = index

    async def click(self, *, timeout: int) -> None:
        self.page.clicks.append((self.text, self.index))
        if self.text == "按月":
            self.page.monthly_selected = True


class _FakeMonthlyLocator:
    def __init__(self, page: "_FakeMonthlyPage", text: str) -> None:
        self.page = page
        self.text = text

    async def count(self) -> int:
        return self.page.tab_counts.get(self.text, 0)

    def nth(self, index: int) -> _FakeMonthlyTab:
        return _FakeMonthlyTab(self.page, self.text, index)

    @property
    def first(self) -> _FakeMonthlyTab:
        return _FakeMonthlyTab(self.page, self.text, 0)


class _FakeMonthlyPage:
    def __init__(self, *, tab_counts: dict[str, int] | None = None) -> None:
        self.tab_counts = tab_counts or {"总平台": 1, "按月": 3}
        self.clicks: list[tuple[str, int]] = []
        self.monthly_selected = False
        self.waits: list[int] = []
        self.url = (
            "https://ebooking.ctrip.com/datacenter/inland/businessreport/beneficialdata?microJump=true"
        )

    def get_by_text(self, text: str, *, exact: bool) -> _FakeMonthlyLocator:
        return _FakeMonthlyLocator(self, text)

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


class BeneficialMonthlySupplementFlowTests(unittest.TestCase):
    def test_read_dataset_flow_attaches_outline_app_funnel_to_daily_metrics(self) -> None:
        service = MagicMock()
        service._capture_screenshot = AsyncMock(return_value="screenshot.png")
        page = MagicMock()
        page.url = (
            "https://ebooking.ctrip.com/datacenter/inland/businessreport/outline?microJump=true"
        )
        parsed = {
            "structure_ok": True,
            "status_hint": None,
            "metrics": {
                "realtime_booking_orders": {"label": "实时预订订单", "value": "42"},
                "departure_sales_amount": {"label": "离店销售额", "value": "36328.96"},
            },
            "tables": [],
            "rankings": [],
            "metric_groups": {},
            "warnings": [],
            "raw_snippets": [],
            "data_quality_notes": [],
            "updated_at": "t1",
            "matched_expected_terms": ["离店销售额"],
        }
        funnel_metrics = _extract_business_outline_app_funnel_metrics(
            [
                {
                    "date": "2026-08-23",
                    "listExposure": 1498,
                    "detailExposure": 301,
                    "orderFillingNum": 25,
                    "orderSubmitNum": 12,
                    "hotelId": 114727375,
                },
                {
                    "date": "2026-08-23",
                    "listExposure": 2728,
                    "detailExposure": 399,
                    "orderFillingNum": 30,
                    "orderSubmitNum": 17,
                    "hotelId": -1,
                },
            ]
        )

        async def run() -> object:
            with (
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows.goto_dataset_page_with_recovery",
                    new=AsyncMock(return_value=page),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._settle_page",
                    new=AsyncMock(),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._dismiss_ebk_read_blocking_popups",
                    new=AsyncMock(return_value={"dismissed": False}),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._select_dataset_time_dimension",
                    new=AsyncMock(return_value={"selected": True, "method": "selector"}),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_visible_text",
                    new=AsyncMock(return_value="收益概览 离店销售额 36328.96"),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_title",
                    new=AsyncMock(return_value="经营报告"),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._classify_page_state",
                    new=lambda url, body_text: "authenticated",
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._extract_dataset_snapshot",
                    return_value=parsed,
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._read_business_outline_app_funnel",
                    new=AsyncMock(
                        return_value={"status": "success", "metrics": funnel_metrics}
                    ),
                ),
            ):
                return await read_dataset_flow(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    dataset_id="business_outline",
                    filters={},
                    actor_id="tester",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["metrics"]["list_exposure"]["value"], 1498)
        self.assertEqual(
            result.data["metrics"]["list_exposure"]["competition_average"],
            2728,
        )
        self.assertEqual(
            result.data["time_dimensions"]["daily"]["metrics"]["order_submitters"]["value"],
            12,
        )

    def test_monthly_supplement_clicks_platform_then_last_monthly_tab(self) -> None:
        page = _FakeMonthlyPage()

        async def run() -> dict[str, Any]:
            with (
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._settle_page",
                    new=AsyncMock(),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_visible_text",
                    new=AsyncMock(return_value=_BENEFICIAL_MONTHLY_BODY),
                ),
            ):
                return await _read_beneficial_monthly_supplement(page)

        payload = asyncio.run(run())

        self.assertEqual(payload["status"], "success")
        # 离店区块的「按月」是页面最后一个按月 tab（预订/在店/离店）
        self.assertEqual(page.clicks[0], ("总平台", 0))
        self.assertEqual(page.clicks[-1], ("按月", 2))
        metrics = payload["metrics"]
        self.assertEqual(metrics["monthly_departure_room_nights"]["value"], "2000")
        self.assertEqual(
            metrics["monthly_departure_room_nights"]["year_over_year"],
            "-18.13%",
        )

    def test_monthly_supplement_returns_unavailable_when_tab_missing(self) -> None:
        page = _FakeMonthlyPage(tab_counts={"总平台": 1, "按月": 0})

        payload = asyncio.run(_read_beneficial_monthly_supplement(page))

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason"], "monthly_tab_not_found")

    def test_monthly_supplement_returns_unavailable_when_metrics_missing(self) -> None:
        page = _FakeMonthlyPage()

        async def run() -> dict[str, Any]:
            with (
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._settle_page",
                    new=AsyncMock(),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_visible_text",
                    new=AsyncMock(return_value=_BENEFICIAL_MAIN_BODY),
                ),
            ):
                return await _read_beneficial_monthly_supplement(page)

        payload = asyncio.run(run())

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason"], "monthly_metrics_not_found")

    def test_read_dataset_flow_attaches_monthly_supplement_for_beneficial_data(self) -> None:
        service = MagicMock()
        service._capture_screenshot = AsyncMock(return_value="screenshot.png")
        page = MagicMock()
        page.url = (
            "https://ebooking.ctrip.com/datacenter/inland/businessreport/beneficialdata?microJump=true"
        )
        parsed = {
            "structure_ok": True,
            "status_hint": None,
            "metrics": {"booking_orders": {"value": "57"}},
            "tables": [],
            "rankings": [],
            "metric_groups": {},
            "warnings": [],
            "raw_snippets": [],
            "data_quality_notes": [],
            "updated_at": "t1",
            "matched_expected_terms": ["预订订单数"],
        }
        monthly_payload = {
            "status": "success",
            "metrics": {
                "monthly_departure_room_nights": {
                    "value": "2000",
                    "last_year_same_period": "2443",
                    "year_over_year": "-18.13%",
                }
            },
        }

        async def run() -> object:
            with (
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows.goto_dataset_page_with_recovery",
                    new=AsyncMock(return_value=page),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._settle_page",
                    new=AsyncMock(),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._dismiss_ebk_read_blocking_popups",
                    new=AsyncMock(return_value={"dismissed": False}),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_visible_text",
                    new=AsyncMock(return_value=_BENEFICIAL_MAIN_BODY),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_title",
                    new=AsyncMock(return_value="经营报告"),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._classify_page_state",
                    new=lambda url, body_text: "authenticated",
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._extract_dataset_snapshot",
                    return_value=parsed,
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._read_beneficial_monthly_supplement",
                    new=AsyncMock(return_value=monthly_payload),
                ),
            ):
                return await read_dataset_flow(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    dataset_id="beneficial_data",
                    filters={},
                    actor_id="tester",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["monthly"]["status"], "success")
        self.assertEqual(
            result.data["monthly"]["metrics"]["monthly_departure_room_nights"]["value"],
            "2000",
        )

    def test_read_dataset_flow_keeps_success_when_monthly_supplement_unavailable(self) -> None:
        service = MagicMock()
        service._capture_screenshot = AsyncMock(return_value="screenshot.png")
        page = MagicMock()
        page.url = (
            "https://ebooking.ctrip.com/datacenter/inland/businessreport/beneficialdata?microJump=true"
        )
        parsed = {
            "structure_ok": True,
            "status_hint": None,
            "metrics": {"booking_orders": {"value": "57"}},
            "tables": [],
            "rankings": [],
            "metric_groups": {},
            "warnings": [],
            "raw_snippets": [],
            "data_quality_notes": [],
            "updated_at": "t1",
            "matched_expected_terms": ["预订订单数"],
        }
        unavailable = {"status": "unavailable", "reason": "monthly_tab_not_found", "metrics": {}}

        async def run() -> object:
            with (
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows.goto_dataset_page_with_recovery",
                    new=AsyncMock(return_value=page),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._settle_page",
                    new=AsyncMock(),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._dismiss_ebk_read_blocking_popups",
                    new=AsyncMock(return_value={"dismissed": False}),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_visible_text",
                    new=AsyncMock(return_value=_BENEFICIAL_MAIN_BODY),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_title",
                    new=AsyncMock(return_value="经营报告"),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._classify_page_state",
                    new=lambda url, body_text: "authenticated",
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._extract_dataset_snapshot",
                    return_value=parsed,
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._read_beneficial_monthly_supplement",
                    new=AsyncMock(return_value=unavailable),
                ),
            ):
                return await read_dataset_flow(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    dataset_id="beneficial_data",
                    filters={},
                    actor_id="tester",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["monthly"]["status"], "unavailable")


class _FakeOutlineFunnelResponse:
    def __init__(self, payload: list[dict[str, Any]], *, method: str = "POST") -> None:
        self.url = (
            "https://ebooking.ctrip.com/datacenter/api/inland/marketanalysis/"
            "flowanalysis/queryFlowTransforNewV1?hostType=Ebooking"
        )
        self.status = 200
        self.request = MagicMock(method=method)
        self._payload = payload

    async def json(self) -> list[dict[str, Any]]:
        return self._payload


class _FakeOutlineFunnelResponseInfo:
    def __init__(self, response: _FakeOutlineFunnelResponse) -> None:
        self._response = response

    @property
    def value(self):
        async def resolve() -> _FakeOutlineFunnelResponse:
            return self._response

        return resolve()


class _FakeOutlineFunnelExpectation:
    def __init__(self, response: _FakeOutlineFunnelResponse) -> None:
        self.info = _FakeOutlineFunnelResponseInfo(response)

    async def __aenter__(self) -> _FakeOutlineFunnelResponseInfo:
        return self.info

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeOutlineFunnelPage:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self.response = _FakeOutlineFunnelResponse(payload)
        self.get_response = _FakeOutlineFunnelResponse(payload, method="GET")
        self.response_predicate = None
        self.reload_calls = 0

    def expect_response(self, predicate, *, timeout: int):
        self.response_predicate = predicate
        return _FakeOutlineFunnelExpectation(self.response)

    async def reload(self, *, wait_until: str, timeout: int) -> None:
        self.reload_calls += 1


class _FakeDatasetService:
    def __init__(self) -> None:
        self.pages: list[_FakeDatasetPage] = []
        self.discard_calls: list[tuple[str, str]] = []

    async def _get_or_create_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        headless: bool,
    ) -> "_FakeDatasetPage":
        return self.pages.pop(0)

    async def _discard_cached_browser_state(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> None:
        self.discard_calls.append((platform_account_id, browser_session_id))


class _FakeDatasetPage:
    def __init__(
        self,
        *,
        stale: bool = False,
        abort_once: bool = False,
        abort_count: int = 0,
    ) -> None:
        self.stale = stale
        self.abort_once = abort_once
        self.abort_count = abort_count
        self.goto_attempts = 0
        self.goto_calls: list[dict[str, Any]] = []
        self.waits: list[int] = []

    async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_attempts += 1
        if self.stale:
            raise RuntimeError("Target page, context or browser has been closed")
        if self.abort_once or self.abort_count > 0:
            self.abort_once = False
            self.abort_count = max(0, self.abort_count - 1)
            raise RuntimeError("Page.goto: net::ERR_ABORTED")
        self.goto_calls.append(
            {"url": url, "wait_until": wait_until, "timeout": timeout}
        )

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


class _FakePopupPage:
    def __init__(
        self,
        *,
        popup_text: str,
        button_text: str,
        appear_after_waits: int = 0,
    ) -> None:
        self.popup_text = popup_text
        self.button_text = button_text
        self.appear_after_waits = appear_after_waits
        self.dismissed = False
        self.clicks: list[str] = []
        self.waits: list[int] = []

    def locator(self, selector: str) -> "_FakeLocator":
        return _FakeLocator(self, selector, scope="page")

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)

    def visible_text(self) -> str:
        if self.dismissed:
            return "收益概览\n今日收益\n¥12,300"
        if len(self.waits) < self.appear_after_waits:
            return "收益概览\n今日收益\n¥12,300"
        return f"{self.popup_text}\n您可以尝试在浏览器设置页打开设置\n{self.button_text}"


class _FakeLocator:
    def __init__(self, page: _FakePopupPage, selector: str, *, scope: str) -> None:
        self.page = page
        self.selector = selector
        self.scope = scope
        self.has_text: str | None = None

    @property
    def first(self) -> "_FakeLocator":
        return self

    def filter(self, *, has_text: str) -> "_FakeLocator":
        clone = _FakeLocator(self.page, self.selector, scope="popup")
        clone.has_text = has_text
        return clone

    def locator(self, selector: str) -> "_FakeLocator":
        return _FakeLocator(self.page, selector, scope="button")

    async def count(self) -> int:
        if self.scope == "popup":
            return int((not self.page.dismissed) and self.has_text in self.page.visible_text())
        if self.scope == "button":
            return int((not self.page.dismissed) and self.page.button_text in self.selector)
        return 1

    async def is_visible(self) -> bool:
        return await self.count() > 0

    async def click(self, *, timeout: int) -> None:
        if self.scope != "button":
            raise AssertionError("only button locator can be clicked")
        self.page.clicks.append(self.page.button_text)
        self.page.dismissed = True


if __name__ == "__main__":
    unittest.main()


class PromotionEffectRealtimeSectionTests(unittest.TestCase):
    """T1.3：促销效果页顶部「今日实时数据」区块与「数据来源=昨日」主表拆分证据。"""

    REAL_PAGE_LINES = [
        "已参加促销",
        "促销效果",
        "今日实时数据",
        "参加促销 /总促销数",
        "9/ 15",
        "竞争圈平均：4",
        "数据来源",
        "昨日",
        "过去7天",
        "过去30天",
        " 2026-08-21 至 2026-08-27",
        "含促销间夜 /总间夜",
        "976/ 986",
        "竞争圈平均 ：359",
        "含促销间夜占比",
        "98.99%",
    ]

    def test_participated_promotions_marked_realtime_when_under_today_header(self) -> None:
        from app.domain.browser.ctrip_ebooking_dataset_parsers import (
            _extract_promotion_effect_dataset_metrics,
        )

        metrics = _extract_promotion_effect_dataset_metrics(self.REAL_PAGE_LINES)
        self.assertIn("participated_promotions", metrics)
        realtime = metrics["participated_promotions"]
        self.assertEqual(realtime["value"], "9")
        self.assertEqual(realtime.get("time_scope"), "realtime")
        self.assertEqual(realtime.get("realtime_section_header"), "今日实时数据")
        # 主表（昨日口径）不带实时标记
        self.assertNotIn("time_scope", metrics.get("promotion_room_nights", {}))

    def test_no_realtime_marker_when_today_header_absent(self) -> None:
        from app.domain.browser.ctrip_ebooking_dataset_parsers import (
            _extract_promotion_effect_dataset_metrics,
        )

        lines = [line for line in self.REAL_PAGE_LINES if line != "今日实时数据"]
        metrics = _extract_promotion_effect_dataset_metrics(lines)
        self.assertNotIn("time_scope", metrics.get("participated_promotions", {}))
