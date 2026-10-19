from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from app.domain.ebk_snapshots import feishu_push as push_mod


class EbkSnapshotFeishuCardTests(unittest.TestCase):
    def test_non_core_platform_dataset_is_rendered_as_scrollable_table(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="ok",
            captured_at="2026-08-25T10:00:00+08:00",
            datasets={
                "comment_manage_home": {
                    "status": "success",
                    "data": {
                        "review_score": "4.91",
                        "review_total": "128",
                        "pending_reply_count": "3",
                    },
                }
            },
        )
        card = cards[0]

        tables = _tables(card)
        self.assertEqual(len(tables), 1)
        self.assertGreaterEqual(
            sum(int(column["width"].removesuffix("px")) for column in tables[0]["columns"]),
            480,
        )

    def test_meituan_card_includes_dataset_metrics(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="美团 EBK 已读取 2 个数据集并保存。",
            captured_at="2026-07-15T10:00:00+08:00",
            datasets={
                "workbench_home": {
                    "status": "success",
                    "label": "workbench_home",
                    "data": {
                        "realtime_data": {"销售额": "1280"},
                        "yesterday_data": {"订单数": "4"},
                    },
                },
                "promotion_coupon": {
                    "status": "success",
                    "label": "promotion_coupon",
                    "data": {
                        "coupon_activities": [{"activity_name": "神券", "status": "进行中"}],
                    },
                },
                "data_center_trade": {
                    "status": "success",
                    "data": {"metrics": {"支付订单数": "4"}},
                },
            },
            store_name="静里酒店",
        )
        card = cards[0]
        self.assertEqual(card["header"]["title"]["content"], "美团 EBK 读取结果")
        blob = str(card)
        self.assertIn("工作台", blob)
        self.assertIn("🏨", blob)
        self.assertIn("🧭", blob)
        self.assertIn("✅", blob)
        self.assertIn("销售额", blob)
        self.assertIn("神券", blob)
        self.assertIn("'tag': 'table'", blob)
        self.assertNotIn("立即调价", blob)
        self.assertNotIn("确认按此价格调价", blob)

    def test_meituan_promotion_coupon_card_includes_activity_data_metrics(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="美团 EBK 神券活动已读取。",
            captured_at="2026-07-20T14:20:00+08:00",
            datasets={
                "promotion_coupon": {
                    "status": "success",
                    "data": {
                        "coupon_activities": [
                            {
                                "activity_name": "美团神券",
                                "status": "报名成功",
                                "activity_metrics": {
                                    "用神券支付金额": "1,582.28元",
                                    "用神券支付订单数": "5个",
                                    "用神券核销金额": "2,580.15元",
                                    "用神券离店订单数": "10个",
                                    "用神券核销客单价": "258.02元",
                                },
                            }
                        ],
                    },
                },
            },
            store_name="静里酒店",
        )
        card = cards[0]
        blob = str(card)
        rows = _table_rows(card)
        self.assertIn("神券活动", blob)
        self.assertIn("美团神券", blob)
        self.assertIn("报名成功", blob)
        self.assertNotIn("美团神券（报名成功）", blob)
        # 活动名进「板块」维度列，行字段只保留指标名。
        self.assertIn({"field": "用神券支付金额", "dimension": "美团神券", "value": "1,582.28元"}, rows)
        self.assertIn({"field": "用神券支付订单数", "dimension": "美团神券", "value": "5个"}, rows)
        self.assertIn({"field": "用神券核销金额", "dimension": "美团神券", "value": "2,580.15元"}, rows)
        self.assertIn({"field": "用神券离店订单数", "dimension": "美团神券", "value": "10个"}, rows)
        self.assertIn({"field": "用神券核销客单价", "dimension": "美团神券", "value": "258.02元"}, rows)

    def test_meituan_data_center_trade_includes_checkin_amount_adr_and_conversion(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="美团 EBK 数据中心交易已读取。",
            captured_at="2026-07-20T14:10:00+08:00",
            datasets={
                "data_center_trade": {
                    "status": "success",
                    "data": {
                        "metrics": {
                            "销售间夜": "18",
                            "销售额": "5767.0元",
                            "支付订单数": "14",
                            "入住间夜": "12",
                            "销售均价": "320.39元",
                            "入住金额": "4320.0元",
                            "平均房价": "360.00元",
                            "成交率": "4.17%",
                        },
                    },
                },
            },
        )
        card = cards[0]
        blob = str(card)
        self.assertIn("数据中心·交易", blob)
        rows = _table_rows(card)
        self.assertIn({"field": "销售间夜", "value": "18"}, rows)
        self.assertIn({"field": "入住金额", "value": "4320.0元"}, rows)
        self.assertIn({"field": "平均房价", "value": "360.00元"}, rows)
        self.assertIn({"field": "成交率", "value": "4.17%"}, rows)
        self.assertNotIn("销售间夜占比", blob)
        # 整表横向滑动：不冻结首列。
        self.assertTrue(
            all(table.get("freeze_first_column") is False for table in _tables(card))
        )

    def test_meituan_data_center_traffic_includes_funnel_metrics_without_ratio_toggle(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="美团 EBK 数据中心流量已读取。",
            captured_at="2026-07-20T14:20:00+08:00",
            datasets={
                "data_center_traffic": {
                    "status": "success",
                    "data": {
                        "metrics": {
                            "曝光人数": "1669人",
                            "浏览人数": "336人",
                            "支付订单数": "14单",
                            "曝光-浏览转化率": "20.13%",
                            "浏览-支付转化率": "4.17%",
                        },
                    },
                },
            },
        )
        card = cards[0]
        blob = str(card)
        self.assertIn("数据中心·流量", blob)
        rows = _table_rows(card)
        self.assertIn({"field": "曝光人数", "value": "1669人"}, rows)
        self.assertIn({"field": "浏览人数", "value": "336人"}, rows)
        self.assertIn({"field": "支付订单数", "value": "14单"}, rows)
        self.assertIn({"field": "曝光-浏览转化率", "value": "20.13%"}, rows)
        self.assertIn({"field": "浏览-支付转化率", "value": "4.17%"}, rows)
        self.assertNotIn("曝光量：占比", blob)

    def test_meituan_data_center_home_includes_sales_metrics_and_week_over_week(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="美团 EBK 数据中心已读取。",
            captured_at="2026-07-20T14:00:00+08:00",
            datasets={
                "data_center_home": {
                    "status": "success",
                    "data": {
                        "basic_operation_metrics": {
                            "引流价": {"value": "211.84 元", "week_over_week": "1.20%"},
                            "曝光量": {"value": "1669 次", "week_over_week": "10.00%"},
                            "浏览人数": {"value": "336 人", "week_over_week": "5.00%"},
                            "支付转化率": {"value": "4.17 %", "week_over_week": "2.00%"},
                            "支付订单数": {"value": "14 单", "week_over_week": "3.00%"},
                            "销售间夜": {"value": "18 间夜", "week_over_week": "4.00%"},
                            "销售均价": {"value": "347.50 元", "week_over_week": "8.52%"},
                            "销售额": {"value": "6255.00 元", "week_over_week": "12.30%"},
                            "入住间夜": {"value": "16 间夜", "week_over_week": "36.00%"},
                            "满房率": {"value": "56.92 %", "week_over_week": "184.62%"},
                        },
                    },
                },
            },
        )
        card = cards[0]
        blob = str(card)
        self.assertIn("数据中心·首页", blob)
        rows = _table_rows(card)
        self.assertIn({"field": "销售均价", "value": "347.50 元（较上周 8.52%）"}, rows)
        self.assertIn({"field": "销售额", "value": "6255.00 元（较上周 12.30%）"}, rows)
        self.assertIn({"field": "入住间夜", "value": "16 间夜（较上周 36.00%）"}, rows)
        self.assertIn({"field": "满房率", "value": "56.92 %（较上周 184.62%）"}, rows)
        self.assertIn({"field": "引流价", "value": "211.84 元（较上周 1.20%）"}, rows)

    def test_meituan_workbench_card_includes_occupancy_lost_orders_without_summary_dup(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="美团 EBK 工作台已读取。",
            captured_at="2026-07-20T10:00:00+08:00",
            datasets={
                "workbench_home": {
                    "status": "success",
                    "data": {
                        "realtime_data": {
                            "浏览人数": "123人",
                            "销售额": "1847元",
                            "入住间夜": "7间夜",
                            "满房率": "56.92%",
                            "流失订单量": "3",
                        },
                        "yesterday_data": {
                            "浏览人数": "336人",
                            "销售额": "5767元",
                            "入住间夜": "12间夜",
                            "满房率": "48.00%",
                            "流失订单量": "5",
                        },
                        "hos_score": {"value": "4.50"},
                        "business_summary": {"浏览人数": "123人"},
                    },
                },
            },
        )
        card = cards[0]
        blob = str(card)
        rows = _table_rows(card)
        # 今日/昨日进「板块」维度列，行字段只保留条目名。
        self.assertIn({"field": "满房率", "dimension": "今日", "value": "56.92%"}, rows)
        self.assertIn({"field": "流失订单量", "dimension": "今日", "value": "3"}, rows)
        self.assertIn({"field": "满房率", "dimension": "昨日", "value": "48.00%"}, rows)
        self.assertIn({"field": "流失订单量", "dimension": "昨日", "value": "5"}, rows)
        self.assertIn({"field": "HOS", "value": "4.50"}, rows)
        self.assertIn({"field": "浏览人数", "dimension": "今日", "value": "123人"}, rows)
        self.assertNotIn("今日·满房率", blob)
        self.assertNotIn("- 浏览人数：123人", blob)
        # 11 条指标 >10 → 拆成两个 ≤10 行的堆叠表格（不翻页、全部表格化）。
        tables = _tables(card)
        self.assertEqual([len(t["rows"]) for t in tables], [10, 1])
        for table in tables:
            self.assertEqual(table["page_size"], len(table["rows"]))

    def test_fliggy_card_includes_home_and_mci(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="fliggy",
            watch_group_key="dc_default",
            status="partial",
            summary="飞猪部分可读",
            captured_at="2026-07-15T10:00:00+08:00",
            datasets={
                "home_v1": {
                    "status": "success",
                    "data": {"today_data": {"销售额": "980"}, "yesterday_data": {"订单数": "3"}},
                },
                "diagnose_report": {
                    "status": "success",
                    "data": {"guest_review_score": "4.8", "mci_index": "86"},
                },
            },
            store_name="静里酒店",
        )
        card = cards[0]
        self.assertEqual(card["header"]["title"]["content"], "飞猪 EBK 读取结果")
        blob = str(card)
        self.assertIn("🏨", blob)
        self.assertIn("🏠", blob)
        self.assertIn("🩺", blob)
        self.assertIn("⚠️", blob)
        self.assertIn("'tag': 'table'", blob)
        self.assertIn("MCI", blob)
        self.assertIn("4.8", blob)

    def test_fliggy_operating_data_dense_section_renders_tables(self) -> None:
        """飞猪「经营数据」：在店/预定/流量三组指标全部表格化，维度列区分子板块。"""

        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="fliggy",
            watch_group_key="dc_default",
            status="success",
            summary="ok",
            captured_at="2026-08-26T10:00:00+08:00",
            datasets={
                "operating_data": {
                    "status": "success",
                    "data": {
                        "in_house_data": {
                            f"在店指标{index}": str(index) for index in range(8)
                        },
                        "booking_data": {
                            f"预定指标{index}": str(index) for index in range(8)
                        },
                        "flow_data": {
                            f"流量指标{index}": str(index) for index in range(8)
                        },
                    },
                }
            },
        )
        card = cards[0]
        tables = _tables(card)
        # 真正数据多的板块必须生成表格，而不是回退纯文本。
        self.assertTrue(tables)
        rows = _table_rows(card)
        self.assertIn({"field": "在店指标0", "dimension": "在店", "value": "0"}, rows)
        self.assertIn({"field": "预定指标7", "dimension": "预定", "value": "7"}, rows)
        self.assertIn({"field": "流量指标5", "dimension": "流量", "value": "5"}, rows)
        blob = str(card)
        self.assertNotIn("在店·在店指标0", blob)
        # 24 行拆成 10+10+4 三张不翻页堆叠表格，预算内全部展示。
        self.assertEqual([len(table["rows"]) for table in tables], [10, 10, 4])
        for table in tables:
            self.assertEqual(table["page_size"], len(table["rows"]))


    def test_platform_card_tables_never_paginate(self) -> None:
        """page_size 恒等于行数（≤10）→ 飞书不翻页；>10 行拆堆叠表格而非文本。"""

        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="ok",
            captured_at="2026-08-26T10:00:00+08:00",
            datasets={
                "data_center_trade": {
                    "status": "success",
                    "data": {
                        "metrics": {f"指标{index}": str(index) for index in range(8)}
                    },
                }
            },
        )
        card = cards[0]
        tables = _tables(card)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["page_size"], len(tables[0]["rows"]))
        self.assertLessEqual(tables[0]["page_size"], 10)

        overflow_card = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="ok",
            captured_at="2026-08-26T10:00:00+08:00",
            datasets={
                "data_center_trade": {
                    "status": "success",
                    "data": {
                        "metrics": {f"指标{index}": str(index) for index in range(12)}
                    },
                }
            },
        )
        # 数据段较多的也全部表格化：12 行拆成 10 + 2 两个不翻页的表格。
        overflow_tables = _tables(overflow_card)
        self.assertEqual([len(t["rows"]) for t in overflow_tables], [10, 2])
        for table in overflow_tables:
            self.assertEqual(table["page_size"], len(table["rows"]))
        blob = str(overflow_card)
        for index in range(12):
            self.assertIn(f"指标{index}", blob)

    def test_long_metric_value_spreads_across_slide_columns(self) -> None:
        """字段过长时按显示宽度切块铺到多列，靠横向滑动查看（不进单元格省略）。"""

        long_text = "这段服务概览说明文字非常长" * 6
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="ok",
            captured_at="2026-08-26T10:00:00+08:00",
            datasets={
                "data_center_service": {
                    "status": "success",
                    "data": {"metrics": {"服务概览": f"5.35\n{long_text}"}},
                }
            },
        )
        card = cards[0]
        tables = _tables(card)
        self.assertEqual(len(tables), 1)
        table = tables[0]
        keys = [column["name"] for column in table["columns"]]
        self.assertIn("value", keys)
        self.assertTrue(any(key.startswith("detail") for key in keys))
        self.assertEqual(table["rows"][0]["value"], "5.35")
        total_width = sum(
            int(column["width"].removesuffix("px")) for column in table["columns"]
        )
        # 列宽总和超过卡片画布 → 表格内容区可横向滑动
        self.assertGreaterEqual(total_width, 480)
        # 每格内容都在该列一行的可视宽度内（不再依赖点开查看）
        from app.domain.notifications.feishu_card_tables import display_width

        for column in table["columns"]:
            budget = (int(column["width"].removesuffix("px")) - 24) // 7
            for row in table["rows"]:
                cell = row.get(column["name"])
                if cell:
                    self.assertLessEqual(display_width(cell), budget)

    def test_unknown_dataset_icon_fallback_is_not_package_emoji(self) -> None:
        cards = push_mod.build_platform_snapshot_feishu_card(
            platform="meituan",
            watch_group_key="dc_default",
            status="success",
            summary="ok",
            captured_at="2026-08-26T10:00:00+08:00",
            datasets={
                "unknown_module": {
                    "status": "success",
                    "label": "未知模块",
                    "data": {"metrics": {"指标": "1"}},
                }
            },
        )
        card = cards[0]
        blob = str(card)
        self.assertNotIn("📦", blob)
        self.assertIn("📋", blob)


def _table_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("tag") == "table" and isinstance(value.get("rows"), list):
            rows.extend(row for row in value["rows"] if isinstance(row, dict))
        for child in value.values():
            rows.extend(_table_rows(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_table_rows(child))
    return rows


def _tables(value: Any) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("tag") == "table":
            tables.append(value)
        for child in value.values():
            tables.extend(_tables(child))
    elif isinstance(value, list):
        for child in value:
            tables.extend(_tables(child))
    return tables


class EbkSnapshotFeishuPushTests(unittest.IsolatedAsyncioTestCase):
    async def test_push_skipped_without_watch_group_key(self) -> None:
        result = await push_mod.push_platform_snapshot_to_ctrip_webhook(
            platform="meituan",
            platform_result={
                "status": "success",
                "summary": "ok",
                "payload": {"datasets": {}, "read_at": "2026-07-15T10:00:00+08:00"},
            },
            feishu_watch_group_key=None,
        )
        self.assertEqual(result["status"], "skipped")

    async def test_push_config_required_when_group_has_no_webhook(self) -> None:
        fake_group = type(
            "G",
            (),
            {"group_key": "dc_default", "feishu_webhook_url": None, "name": "默认组"},
        )()
        with patch.object(
            push_mod,
            "get_data_center_watch_group_runtime",
            new=AsyncMock(return_value=fake_group),
        ):
            result = await push_mod.push_platform_snapshot_to_ctrip_webhook(
                platform="fliggy",
                platform_result={
                    "status": "success",
                    "summary": "ok",
                    "payload": {"datasets": {"home_v1": {"status": "success", "data": {}}}},
                },
                feishu_watch_group_key="dc_default",
            )
        self.assertEqual(result["status"], "config_required")

    async def test_push_uses_ctrip_group_webhook(self) -> None:
        fake_group = type(
            "G",
            (),
            {
                "group_key": "dc_default",
                "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
                "name": "默认组",
            },
        )()
        fake_push = type(
            "R",
            (),
            {
                "status": "success",
                "data": {},
                "model_dump": lambda self, mode="json": {
                    "status": "success",
                    "summary": "pushed",
                    "data": {},
                    "evidence": {},
                },
            },
        )()
        with (
            patch.object(
                push_mod,
                "get_data_center_watch_group_runtime",
                new=AsyncMock(return_value=fake_group),
            ),
            patch.object(
                push_mod,
                "push_interactive_card_to_feishu_webhook",
                new=AsyncMock(return_value=fake_push),
            ) as mocked,
        ):
            result = await push_mod.push_platform_snapshot_to_ctrip_webhook(
                platform="meituan",
                platform_result={
                    "status": "success",
                    "summary": "美团已读",
                    "payload": {
                        "read_at": "2026-07-15T10:00:00+08:00",
                        "datasets": {
                            "workbench_home": {
                                "status": "success",
                                "data": {"realtime_data": {"销售额": "1"}},
                            }
                        },
                    },
                },
                feishu_watch_group_key="dc_default",
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["delivery"], "webhook")
        kwargs = mocked.await_args.kwargs
        self.assertEqual(kwargs["title"], "美团 EBK 读取结果")
        self.assertEqual(
            kwargs["webhook_url"],
            "https://open.feishu.cn/open-apis/bot/v2/hook/test",
        )


if __name__ == "__main__":
    unittest.main()
