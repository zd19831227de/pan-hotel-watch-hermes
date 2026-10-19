from __future__ import annotations

import unittest
from typing import Any

from app.domain.pyramid_bidding.cards import (
    build_pyramid_bid_list_card,
    build_pyramid_bid_list_fallback_text,
    build_pyramid_execution_blocked_card,
)


def _promotions(count: int) -> list[dict[str, Any]]:
    return [
        {
            "campaign_name": f"计划{i:02d}--{i:02d}",
            "campaign_status": "推广中",
            "click_bid_cny": float(i),
            "daily_budget_cny": 300.0,
            "today_spend_cny": 0.0,
        }
        for i in range(count)
    ]


class PyramidBidListCardTests(unittest.TestCase):
    def _card_text(self, card: dict[str, Any]) -> str:
        elements = card.get("elements") or []
        parts = []
        for element in elements:
            text = element.get("text") if isinstance(element, dict) else None
            if isinstance(text, dict):
                parts.append(str(text.get("content") or ""))
        return "\n".join(parts)

    @staticmethod
    def _tables(card: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            element
            for element in (card.get("elements") or [])
            if isinstance(element, dict) and element.get("tag") == "table"
        ]

    @staticmethod
    def _campaign_names(card: dict[str, Any]) -> set[str]:
        return {
            str(row.get("field") or "")
            for table in PyramidBidListCardTests._tables(card)
            for row in (table.get("rows") or [])
        }

    def test_ten_promotions_all_rendered(self) -> None:
        """2026-09-08 线上问题：列表卡 promotions[:8] 截断，10 个计划只显示 8 个。

        读取层已抓全（稳定采集 10/10），卡片必须完整展示，不得静默砍行。
        """
        card = build_pyramid_bid_list_card({"promotions": _promotions(10)})
        names = self._campaign_names(card)
        for i in range(10):
            self.assertIn(f"计划{i:02d}--{i:02d}", names)

    def test_over_limit_promotions_show_overflow_note(self) -> None:
        card = build_pyramid_bid_list_card({"promotions": _promotions(25)})
        names = self._campaign_names(card)
        self.assertIn("计划19--19", names)  # 展示上限 20
        self.assertNotIn("计划20--20", names)
        self.assertIn("另有 5 个未展示", self._card_text(card))

    def test_list_card_renders_metric_table_with_bid_columns(self) -> None:
        """2026-09-10 需求：指令「金字塔出价」回卡表格化，与其他推送表格同构。

        列：推广计划 | 点击出价 | 今日预算 | 今日花费 | 状态；
        ≤10 行单表不翻页（飞书表格组件 page_size 上限）。
        """
        card = build_pyramid_bid_list_card(
            {
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
            }
        )
        tables = self._tables(card)
        self.assertEqual(len(tables), 1)
        table = tables[0]
        headers = [column["display_name"] for column in table["columns"]]
        self.assertEqual(
            headers, ["推广计划", "点击出价", "今日预算", "今日花费", "状态"]
        )
        self.assertEqual(table["page_size"], 2)
        first = table["rows"][0]
        self.assertEqual(first["field"], "周内0-2")
        self.assertEqual(first["value"], "¥6.5")
        self.assertEqual(first["detail"], "¥300")
        self.assertEqual(first["detail_2"], "¥45.2")
        self.assertEqual(first["detail_3"], "推广中")
        smart = table["rows"][1]
        self.assertEqual(smart["value"], "-")  # 智能投放无固定出价
        # 调整示例提示保留在表格下方文本
        self.assertIn("调整示例：金字塔调价 14--16 出价调到6", self._card_text(card))

    def test_list_card_over_ten_rows_splits_into_stacked_tables(self) -> None:
        card = build_pyramid_bid_list_card({"promotions": _promotions(14)})
        tables = self._tables(card)
        self.assertEqual([len(t["rows"]) for t in tables], [10, 4])
        for table in tables:
            self.assertEqual(table["page_size"], len(table["rows"]))
            self.assertLessEqual(table["page_size"], 10)

    def test_list_card_failure_keeps_text_not_empty_table(self) -> None:
        card = build_pyramid_bid_list_card(
            {"status": "human_required", "summary": "需要人工登录。", "promotions": []},
            read_status="human_required",
        )
        self.assertEqual(self._tables(card), [])
        self.assertIn("需要人工登录。", self._card_text(card))

    def test_fallback_text_ten_promotions(self) -> None:
        fallback = build_pyramid_bid_list_fallback_text({"promotions": _promotions(10)})
        self.assertIn("计划09--09", fallback)
        self.assertIn("计划00--00", fallback)


class PyramidBlockedCardStatusLabelTests(unittest.TestCase):
    def _card_text(self, card: dict[str, Any]) -> str:
        parts = []
        for element in card.get("elements") or []:
            text = element.get("text") if isinstance(element, dict) else None
            if isinstance(text, dict):
                parts.append(str(text.get("content") or ""))
        return "\n".join(parts)

    def test_block_statuses_render_chinese_labels(self) -> None:
        """阻断卡「状态」行不得把内部英文状态码直接露给运营。"""
        cases = {
            "floor_price_violation": "低于页面起价",
            "bid_input_not_located": "未定位出价输入框",
            "submit_failed": "提交未生效",
            "pipeline_failed": "执行链异常",
        }
        for status, label in cases.items():
            with self.subTest(status=status):
                card = build_pyramid_execution_blocked_card(
                    blocked_step="金字塔出价写入",
                    blocked_status=status,
                    blocked_summary="测试摘要",
                )
                text = self._card_text(card)
                self.assertIn(label, text)
                self.assertNotIn(status, text)


if __name__ == "__main__":
    unittest.main()
