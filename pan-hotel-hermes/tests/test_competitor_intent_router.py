from __future__ import annotations

import unittest
from unittest import mock

from client.competitor_intent_router import (
    apply_competitor_card_gate,
    classify_competitor_query,
    is_calendar_tool_allowed,
    is_competitor_tool_allowed,
    plain_mcp_tool_name,
)


class CompetitorIntentRouterTests(unittest.TestCase):
    def test_acceptance_amplitude_one_day(self) -> None:
        d = classify_competitor_query("竞品最近一天的价格涨幅情况")
        self.assertEqual(d.intent, "competitor_trend")
        self.assertEqual(d.days, 1)
        self.assertTrue(is_competitor_tool_allowed(d, "get_competitor_price_trend"))
        self.assertFalse(is_competitor_tool_allowed(d, "get_competitor_latest_prices"))
        self.assertFalse(is_competitor_tool_allowed(d, "analyze_competitor_price_change"))
        self.assertTrue(is_competitor_tool_allowed(d, "resolve_feishu_chat_context"))

    def test_trend_three_days(self) -> None:
        d = classify_competitor_query("最近三天竞品价格趋势")
        self.assertEqual(d.intent, "competitor_trend")
        self.assertEqual(d.days, 3)

    def test_amplitude_without_days_defaults_to_one(self) -> None:
        d = classify_competitor_query("竞品涨幅情况怎么样")
        self.assertEqual(d.intent, "competitor_trend")
        self.assertEqual(d.days, 1)

    def test_change_alert_without_window(self) -> None:
        d = classify_competitor_query("竞品有没有明显变化，要不要关注")
        self.assertEqual(d.intent, "competitor_change_alert")
        self.assertTrue(is_competitor_tool_allowed(d, "analyze_competitor_price_change"))
        self.assertFalse(is_competitor_tool_allowed(d, "get_competitor_latest_prices"))
        self.assertFalse(is_competitor_tool_allowed(d, "get_competitor_price_trend"))

    def test_latest_price(self) -> None:
        d = classify_competitor_query("现在竞品多少钱")
        self.assertEqual(d.intent, "competitor_latest")
        self.assertTrue(is_competitor_tool_allowed(d, "get_competitor_latest_prices"))

    def test_own_hotel_room_price_from_last_round_is_competitor_latest(self) -> None:
        d = classify_competitor_query("你读取上一轮的数据，展示今天本店的房型价格")
        self.assertEqual(d.intent, "competitor_latest")
        self.assertEqual(d.reason, "own_room_spot")
        self.assertTrue(is_competitor_tool_allowed(d, "get_competitor_latest_prices"))
        self.assertFalse(is_calendar_tool_allowed(d, "get_calendar_price_latest"))
        self.assertFalse(is_calendar_tool_allowed(d, "run_calendar_price_watch_once"))
        self.assertFalse(
            is_calendar_tool_allowed(d, "mcp_pan_hotel_watch_get_calendar_price_latest")
        )

    def test_explicit_calendar_query_still_allows_calendar_tools(self) -> None:
        d = classify_competitor_query("未来15天日历价怎么样")
        self.assertEqual(d.intent, "calendar_price")
        self.assertTrue(is_calendar_tool_allowed(d, "get_calendar_price_latest"))
        self.assertFalse(is_competitor_tool_allowed(d, "get_competitor_latest_prices"))

    def test_priority_amplitude_beats_change_words(self) -> None:
        d = classify_competitor_query("竞品最近一天价格变化涨幅")
        self.assertEqual(d.intent, "competitor_trend")
        self.assertEqual(d.days, 1)

    def test_plain_mcp_tool_name(self) -> None:
        self.assertEqual(
            plain_mcp_tool_name("mcp_pan_hotel_watch_get_competitor_latest_prices"),
            "get_competitor_latest_prices",
        )

    def test_compatibility_classifier_is_self_contained(self) -> None:
        # 2026-10-09：主路由翻转后本模块自包含——不依赖 domain_intent_router，
        # 即便主路由不可用也能给出 advisory 分类。
        with mock.patch(
            "client.domain_intent_router.route_domain_intent",
            side_effect=RuntimeError("router unavailable"),
        ):
            decision = classify_competitor_query("现在竞品多少钱")

        self.assertEqual(decision.intent, "competitor_latest")
        self.assertEqual(
            decision.allowed_competitor_tools, frozenset({"get_competitor_latest_prices"})
        )

    def test_card_gate_is_advisory_and_does_not_rewrite_tool_result(self) -> None:
        payload = {
            "status": "success",
            "feishu_card": {"header": {"title": {"content": "竞品价格"}}},
        }
        out = apply_competitor_card_gate(
            payload,
            tool_name="get_competitor_latest_prices",
            user_text="竞品最近一天的价格涨幅情况",
        )
        self.assertIs(out, payload)
        self.assertEqual(out.get("status"), "success")

    def test_card_gate_noop_without_user_text(self) -> None:
        payload = {"status": "success", "feishu_card": {"x": 1}}
        out = apply_competitor_card_gate(
            payload,
            tool_name="get_competitor_latest_prices",
            user_text="",
        )
        self.assertIs(out, payload)


if __name__ == "__main__":
    unittest.main()
