"""通用 agent 模式路由契约（2026-10-09 翻转，2026-10-14 全量 LLM 先行）。

背景：问法无限、关键词词表加不完，且词表子串匹配会劫持测算/图表追问等
问法（2026-10-14 生产实证：「房型价格提高20元的进单测算」被劫持成房价
查询）→ 路由从「词表确定性优先、未命中兜底 LLM」翻转为「一切问法先进
LLM」：

- 不再存在任何词表硬路由（查房价/手动调价/促销/金字塔直转通道全部
  退役）；进站层只直转卡片回调与 url_verification。
- generic_agent 放行全部只读落库 + 按需现读 + 知识 + 文档 + 事件转发
  （dispatch_watch_feishu_event）工具，由 LLM 自主判断选工具。
- dispatch_watch_feishu_event 在通用目录内：LLM 判定是价目表问法或
  固定格式指令（手动调价/促销调整/金字塔出价）时转发**运营原话**给
  后端，由后端确定性解析并出权威卡/人工审批卡。
- 写侧/执行侧工具（create_manual_pricing_approval、暂停/恢复监控、
  卡片回执）永远拒绝：目标价不得由 LLM 手拼字段，只能转发原文。
- 学习库先验照常：generic_agent 携带「该问法此前用过哪些工具」提示，
  白名单始终是全目录，先验只是软提示。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client.domain_intent_router import (
    GENERIC_AGENT_REASON,
    GENERIC_AGENT_TOOLS,
    PRIVILEGED_EXECUTION_TOOLS,
    build_misroute_message,
    build_pre_llm_route_context,
    is_generic_agent_route,
    is_tool_allowed,
    route_domain_intent,
)
from client import learned_routes

# 各业务域代表性问法（取自旧词表路由测试语料）：翻转后全部应落 generic_agent，
# 且该域核心工具必须在目录内（数据入口不断）。
GENERIC_PHRASE_CASES = [
    # (话法, 意图核心工具)
    ("今天退了几单", "get_refund_status"),
    ("今天订了多少间", "get_today_reservations"),
    ("退单和今日预订一起看看", "get_refund_status"),
    ("这几天生意如何", "get_ebk_period_read"),
    ("10.1-7 经营情况怎么样", "get_ebk_period_read"),
    ("国庆节经营情况", "get_ebk_period_read"),
    ("近7天金字塔投放效果", "get_ebk_period_read"),
    ("上周经营怎么样", "get_ebk_period_read"),
    ("最近三天竞品价格趋势", "get_competitor_price_trend"),
    ("竞品有没有明显变化，要不要关注", "analyze_competitor_price_change"),
    ("现在竞品多少钱", "get_competitor_latest_prices"),
    ("刷新一下竞品再分析变化", "run_competitor_watch_once"),
    ("本店现在价格啥样", "get_competitor_latest_prices"),
    ("日历价现在什么样", "get_calendar_price_latest"),
    ("携程待回复评价有几条", "pending_review_count"),
    ("今天好评多少", "get_ebk_latest_result"),
    ("今天整体怎么样", "get_operator_hotel_overview"),
    ("给我今天三端简报", "get_ebk_joint_analysis_context"),
    ("结合经营和排名分析下问题点", "read_ebk_datasets"),
    ("现在读一次出租率", "read_ebk_datasets"),
    ("盯盘状态", "get_watch_scheduler_status"),
    ("今日简报", "get_operator_hotel_overview"),
    ("本群绑定了哪家店", "resolve_feishu_chat_context"),
    ("生成一份国庆经营汇报文档", "send_operator_document"),
    ("出个周报", "send_operator_document"),
    ("酒店退款政策是什么", "rag_search"),
    ("入住退房流程是怎样的", "rag_search"),
    ("图上需要加上本店，并且要准确到房价个位数", "get_competitor_price_trend"),
    # 模糊/错别字/口语（原 fuzzy/typo/colloquial 语料）
    ("最近怎么样", "get_operator_hotel_overview"),
    ("帮我看看我们店的情况", "get_operator_recent_activity"),
    ("今天的竞拼价格多少", "get_competitor_latest_prices"),
    ("今天退但了多少", "get_refund_status"),
    ("近7天经萤数据", "get_ebk_period_read"),
    ("今天进单多不多", "get_today_reservations"),
    ("今天卖了多少钱", "read_ebk_datasets"),
    ("房卖得咋样", "read_ebk_datasets"),
    ("现在房间啥价", "get_competitor_latest_prices"),
    # 非酒店闲聊也进通用模式（目录在手、按需才用）
    ("今天天气怎么样", "get_operator_hotel_overview"),
]

EXECUTION_TOOLS = sorted(PRIVILEGED_EXECUTION_TOOLS)

CATALOG_SPOT_CHECK_TOOLS = (
    "resolve_feishu_chat_context",
    "get_ebk_period_read",
    "read_ebk_datasets",
    "get_ebk_latest_result",
    "get_latest_ebk_snapshot",
    "get_ebk_joint_analysis_context",
    "run_ebk_watch_once",
    "run_competitor_watch_once",
    "run_calendar_price_watch_once",
    "get_competitor_latest_prices",
    "get_competitor_price_trend",
    "analyze_competitor_price_change",
    "get_calendar_price_latest",
    "get_refund_status",
    "get_today_reservations",
    "pending_review_count",
    "get_watch_scheduler_status",
    "get_operator_watch_configuration",
    "get_operator_hotel_overview",
    "get_operator_analysis_context",
    "get_operator_recent_activity",
    "get_weekly_operator_briefing",
    "list_watch_groups",
    "query_hotel_analytics",
    "rag_search",
    "distill_external_source",
    "create_distill_source",
    "list_distill_sources",
    "send_operator_document",
)


class GenericAgentRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        # 学习库隔离：每用例指向空临时文件，防止本机/生产已记录问法泄进断言。
        self._learned_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._learned_tmp.cleanup)
        patcher = mock.patch.object(
            learned_routes, "STORE_PATH", Path(self._learned_tmp.name) / "learned_routes.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_manual_pricing_phrase_falls_to_generic_agent(self) -> None:
        # 2026-10-14 全量 LLM 先行：手动调价句不再有词表硬路由。审批卡
        # 字段由后端解析运营原话生成，create_manual_pricing_approval
        # 保持特权（目标价不得由 LLM 手拼），转发用 dispatch。
        route = route_domain_intent("把今天高级大床房调到420")
        self.assertEqual(route.intent, "generic_agent")
        self.assertEqual(route.reason, GENERIC_AGENT_REASON)
        self.assertFalse(is_tool_allowed(route, "create_manual_pricing_approval"))
        self.assertTrue(is_tool_allowed(route, "dispatch_watch_feishu_event"))

    def test_pricing_query_phrase_falls_to_generic_agent(self) -> None:
        route = route_domain_intent("查一下今天携程房价")
        self.assertEqual(route.intent, "generic_agent")
        self.assertEqual(route.reason, GENERIC_AGENT_REASON)
        self.assertTrue(is_tool_allowed(route, "dispatch_watch_feishu_event"))
        # 通用目录不窄化：测算/分析类数据工具同轮可用。
        self.assertTrue(is_tool_allowed(route, "get_competitor_price_trend"))

    def test_production_calculation_question_reaches_llm(self) -> None:
        # 2026-10-14 生产事故回归：原句含「房型价格」被词表劫持成房价查询，
        # 全量 LLM 先行后必须落通用模式由 LLM 做测算分析。
        route = route_domain_intent(
            "你给我做个测算，如果按照携程的8-10月的经营规律，"
            "我如果所有房型价格提高20元，每天的进单情况会有什么影响"
        )
        self.assertEqual(route.intent, "generic_agent")
        self.assertEqual(route.reason, GENERIC_AGENT_REASON)
        self.assertTrue(is_generic_agent_route(route))
        self.assertTrue(is_tool_allowed(route, "get_ebk_period_read"))
        self.assertTrue(is_tool_allowed(route, "get_bi_daily_report"))

    def test_vague_adjustment_wording_falls_to_generic_agent(self) -> None:
        # 「怎么改价」无目标价数字/房型：不是查询、也构不成审批指令，
        # 由通用 agent 解释调价指令格式与参考口径。
        route = route_domain_intent("怎么改价")
        self.assertEqual(route.intent, "generic_agent")
        self.assertEqual(route.reason, GENERIC_AGENT_REASON)

    def test_all_data_phrasings_fall_to_generic_agent(self) -> None:
        for phrase, needed_tool in GENERIC_PHRASE_CASES:
            with self.subTest(phrase=phrase):
                route = route_domain_intent(phrase)
                self.assertEqual(route.intent, "generic_agent", f"reason={route.reason}")
                self.assertEqual(route.reason, GENERIC_AGENT_REASON)
                self.assertTrue(
                    is_tool_allowed(route, needed_tool),
                    f"{phrase}：通用目录缺少核心工具 {needed_tool}",
                )

    def test_generic_agent_catalog_covers_read_live_knowledge_document(self) -> None:
        for tool in CATALOG_SPOT_CHECK_TOOLS:
            self.assertIn(tool, GENERIC_AGENT_TOOLS, f"通用目录缺少 {tool}")
        route = route_domain_intent("帮我看看店里情况")
        for tool in CATALOG_SPOT_CHECK_TOOLS:
            self.assertTrue(is_tool_allowed(route, tool), f"目录工具 {tool} 未放行")

    def test_generic_agent_never_allows_execution_tools(self) -> None:
        for phrase, _ in GENERIC_PHRASE_CASES:
            route = route_domain_intent(phrase)
            for tool in EXECUTION_TOOLS:
                self.assertFalse(
                    is_tool_allowed(route, tool),
                    f"{phrase}：通用模式意外放行执行侧工具 {tool}",
                )

    def test_empty_text_stays_ambiguous(self) -> None:
        route = route_domain_intent("")
        self.assertEqual(route.intent, "ambiguous")
        self.assertEqual(route.reason, "empty")

    def test_is_generic_agent_route_helper(self) -> None:
        self.assertTrue(is_generic_agent_route(route_domain_intent("今天生意怎么样")))
        self.assertTrue(is_generic_agent_route(route_domain_intent("查一下今天携程房价")))


class LearnedRoutePriorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._learned_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._learned_tmp.cleanup)
        patcher = mock.patch.object(
            learned_routes, "STORE_PATH", Path(self._learned_tmp.name) / "learned_routes.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_learned_prior_attaches_to_generic_agent_route(self) -> None:
        phrase = "昨天退了多少"
        self.assertTrue(learned_routes.record_learned_route(phrase, ["get_refund_status"]))
        route = route_domain_intent(phrase)
        self.assertEqual(route.intent, "generic_agent")
        self.assertEqual(route.reason, GENERIC_AGENT_REASON)
        self.assertEqual(route.learned_tools, ("get_refund_status",))
        # 白名单仍是全目录：先验只是提示，不是硬路由。
        self.assertTrue(is_tool_allowed(route, "get_refund_status"))
        self.assertTrue(is_tool_allowed(route, "get_ebk_period_read"))

    def test_learned_prior_never_unlocks_privileged_tools(self) -> None:
        # 2026-10-14：安全通道已退役，安全边界=特权工具永远拒绝；
        # 学习先验只提示工具复用，不解锁执行侧。
        phrase = "把今天高级大床房调到420"
        learned_routes.record_learned_route(phrase, ["get_refund_status"])
        route = route_domain_intent(phrase)
        self.assertEqual(route.intent, "generic_agent")
        self.assertTrue(is_tool_allowed(route, "get_refund_status"))
        self.assertFalse(is_tool_allowed(route, "create_manual_pricing_approval"))
        self.assertFalse(is_tool_allowed(route, "pause_watch"))

    def test_learned_lookup_normalizes_punctuation_and_width(self) -> None:
        learned_routes.record_learned_route("昨天退了多少", ["get_refund_status"])
        route = route_domain_intent("昨天，退了多少！")
        self.assertEqual(route.learned_tools, ("get_refund_status",))

    def test_learned_lookup_failure_falls_back_silently(self) -> None:
        with mock.patch.object(
            learned_routes, "lookup_learned_tools", side_effect=RuntimeError("boom")
        ):
            route = route_domain_intent("昨天退了多少")
        self.assertEqual(route.intent, "generic_agent")
        self.assertEqual(route.learned_tools, ())


class ToolGateAndContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._learned_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._learned_tmp.cleanup)
        patcher = mock.patch.object(
            learned_routes, "STORE_PATH", Path(self._learned_tmp.name) / "learned_routes.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pre_llm_context_is_generic_capability_brief(self) -> None:
        route = route_domain_intent("帮我盘一下最近的整体表现给个结论")
        context = build_pre_llm_route_context(route)
        # 先解析群上下文，再按目录自主选工具。
        self.assertIn("resolve_feishu_chat_context", context)
        self.assertIn("get_ebk_period_read", context)
        self.assertIn("get_competitor_price_trend", context)
        self.assertIn("rag_search", context)
        # 口径与安全规则。
        self.assertIn("不编", context)
        self.assertIn("2 次", context)
        self.assertIn("禁止", context)

    def test_pre_llm_context_includes_learned_prior(self) -> None:
        learned_routes.record_learned_route("昨天退了多少", ["get_refund_status"])
        route = route_domain_intent("昨天退了多少")
        context = build_pre_llm_route_context(route)
        self.assertIn("此前已成功回答过", context)
        self.assertIn("get_refund_status", context)

    def test_pre_llm_context_includes_dispatch_guidance_for_fixed_commands(self) -> None:
        # 2026-10-14 全量 LLM 先行：固定格式指令（调价/促销/金字塔）与
        # 价目表问法由 LLM 判定后 dispatch 转发运营原话，后端解析出卡。
        route = route_domain_intent("把今天高级大床房调到420")
        context = build_pre_llm_route_context(route)
        self.assertIn("dispatch_watch_feishu_event", context)
        self.assertIn("原话", context)

    def test_misroute_message_for_execution_tool_points_to_dispatch(self) -> None:
        route = route_domain_intent("今天生意怎么样")
        message = build_misroute_message(route, "mcp_pan_hotel_watch_create_manual_pricing_approval")
        self.assertIn("调价", message)
        self.assertIn("dispatch_watch_feishu_event", message)

    def test_is_tool_allowed_passes_non_pan_hotel_tools(self) -> None:
        route = route_domain_intent("今天生意怎么样")
        self.assertTrue(is_tool_allowed(route, "web_search"))
        self.assertFalse(is_tool_allowed(route, "mcp_pan_hotel_watch_definitely_not_a_tool"))


if __name__ == "__main__":
    unittest.main()
