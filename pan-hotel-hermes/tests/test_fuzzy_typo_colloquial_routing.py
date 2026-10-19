"""模糊词/错别字/口语化路由验收（2026-10-09 通用 agent 模式口径）：每类 5 轮真实话术。

模拟真实网关链路（不真发飞书消息）：消息 → route_domain_intent → 插件
pre-LLM 路由注入 → 工具门禁（pre_tool_call）→ 现读预算。验收标准：

1. 每句话都落 generic_agent 通用模式：全量只读/现读/知识/文档工具在手，
   由 LLM 自主判断选工具——不存在「被确定性词表截走答非所问」的问法；
2. 意图核心工具必须在目录内（不断数据入口）；
3. 任何一句话都不放行写入/审批/暂停类执行工具（调价安全契约不变；
   send_operator_document 按新口径放行，仅在用户明确要文件产出时用）；
4. 通用轮现读预算生效（第 3 次现读 block），且探索成功的问法可被学习
   复用（探索→记录→复用回路，先验只是提示不是硬路由）。

预期矩阵取自 2026-10-08 实测探针语料（路由翻转后统一落通用模式），
改路由结构时本文件同步更新——它是「常规 agent 能力」的运营验收面。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client.domain_intent_router import (
    GENERIC_AGENT_REASON,
    build_pre_llm_route_context,
    is_generic_agent_route,
    is_tool_allowed,
    route_domain_intent,
)
from client import learned_routes

_MCP = "mcp_pan_hotel_watch_"

# (话术, 意图核心工具集) —— 通用模式下 intent/reason 统一，不再按话法分叉。
FUZZY_CASES = [
    ("最近怎么样", {"get_operator_hotel_overview"}),
    ("这几天生意如何", {"get_ebk_period_read"}),
    ("帮我看看我们店的情况", {"get_operator_recent_activity"}),
    ("说说最近的表现", {"get_ebk_latest_result"}),
    ("今天整体怎么样", {"get_operator_hotel_overview"}),
]

TYPO_CASES = [
    # 竞品→竞拼 / 退单→退但 / 间夜→间页 / 房价→方价 / 经营→经萤
    ("今天的竞拼价格多少", {"get_competitor_latest_prices"}),
    ("今天退但了多少", {"get_refund_status"}),
    ("昨天的间页量", {"get_ebk_period_read"}),
    ("查一下方价", {"get_competitor_latest_prices"}),
    ("近7天经萤数据", {"get_ebk_period_read"}),
]

COLLOQUIAL_CASES = [
    # 进单=今日预定 / 卖了多少钱=销售额 / 房卖得咋样=房态销售 / 退了多少=退单 / 啥价=房价
    ("今天进单多不多", {"get_today_reservations"}),
    ("今天卖了多少钱", {"read_ebk_datasets"}),
    ("房卖得咋样", {"read_ebk_datasets"}),
    ("昨天退了多少", {"get_refund_status"}),
    ("现在房间啥价", {"get_competitor_latest_prices"}),
]

# 任何话术任何路由都不得放行的执行/写侧工具（调价安全契约）。
# send_operator_document 不在禁区：通用模式按需放行（用户明确要文件时）。
# dispatch_watch_feishu_event 2026-10-14 起入通用目录：全量 LLM 先行后由
# LLM 判定转发运营原话（后端仍确定性解析+人工卡确认），不在禁区。
FORBIDDEN_TOOLS = (
    "create_manual_pricing_approval",
    "pause_watch",
    "resume_watch",
    "reply_feishu_operator_card",
)

# 2026-10-14 生产误触发回归语料：这些句子曾被子串词表劫持/或走确定性
# 直转通道，全量 LLM 先行后必须进通用模式由 LLM 判断。
HIJACK_REGRESSION_CASES = [
    (
        "你给我做个测算，如果按照携程的8-10月的经营规律，"
        "我如果所有房型价格提高20元，每天的进单情况会有什么影响",
        {"get_ebk_period_read"},
    ),
    ("查一下今天携程房价", {"dispatch_watch_feishu_event"}),
    ("把今天高级大床房调到420", {"dispatch_watch_feishu_event"}),
    ("图上需要加上本店，并且要准确到房价个位数", {"get_competitor_price_trend"}),
]


def _load_plugin_module():
    plugin_path = (
        Path(__file__).resolve().parents[1]
        / "hermes_plugin"
        / "pan-hotel-feishu-session"
        / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("pan_hotel_feishu_session_acceptance", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["pan_hotel_feishu_session_acceptance"] = module
    spec.loader.exec_module(module)
    return module


class FuzzyTypoColloquialAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = _load_plugin_module()

    def setUp(self) -> None:
        # 学习库隔离：验收矩阵按「学习库为空」断言，防本机已记录问法干扰。
        self._learned_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._learned_tmp.cleanup)
        patcher = mock.patch.object(
            learned_routes, "STORE_PATH", Path(self._learned_tmp.name) / "learned_routes.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_round(self, index: int, phrase: str, needed: set[str]) -> None:
        """一轮验收：通用模式分类 → 核心工具在目录 → 禁区全封 → 插件链路通。"""
        with self.subTest(round=index, phrase=phrase):
            route = route_domain_intent(phrase)
            self.assertEqual(route.intent, "generic_agent", f"{phrase} 路由意图变化：{route.reason}")
            self.assertEqual(route.reason, GENERIC_AGENT_REASON, f"{phrase} 路由原因变化")
            self.assertTrue(is_generic_agent_route(route))
            for tool in needed:
                self.assertTrue(
                    is_tool_allowed(route, tool),
                    f"{phrase}（{route.intent}/{route.reason}）缺少意图核心工具 {tool}",
                )
            for tool in FORBIDDEN_TOOLS:
                self.assertFalse(
                    is_tool_allowed(route, tool),
                    f"{phrase}（{route.intent}/{route.reason}）意外放行了禁区工具 {tool}",
                )

            # 网关插件链路（模拟真实消息）：pre-LLM 注入不炸、核心工具放行、
            # 写侧工具被门禁拦下。
            chat_id = f"oc_accept_{index}"
            session_id = f"sess_accept_{index}"
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=(f"om_acc_{index}", chat_id, "ou_user"),
            ), mock.patch.object(self.plugin, "_is_allowed_chat_id", return_value=True):
                pre = self.plugin._on_pre_llm_call(
                    session_id=session_id, user_message=phrase, conversation_history=[]
                )
                self.assertTrue(pre is None or "context" in pre)
                self.assertIn("通用", (pre or {}).get("context") or "")

                first_needed = sorted(needed)[0]
                allowed = self.plugin._on_pre_tool_call(
                    tool_name=f"{_MCP}{first_needed}", args={}, session_id=session_id
                )
                self.assertIsNone(
                    allowed, f"{phrase}：插件门禁意外拦截了 {first_needed}（{allowed}）"
                )
                blocked = self.plugin._on_pre_tool_call(
                    tool_name=f"{_MCP}create_manual_pricing_approval",
                    args={},
                    session_id=session_id,
                )
                self.assertEqual(blocked.get("action"), "block")

    # ---- 模糊词 5 轮 ----
    def test_fuzzy_phrases_round_1_to_5(self) -> None:
        for index, (phrase, needed) in enumerate(FUZZY_CASES, start=1):
            self._run_round(index, phrase, needed)

    # ---- 错别字 5 轮 ----
    def test_typo_phrases_round_1_to_5(self) -> None:
        for index, (phrase, needed) in enumerate(TYPO_CASES, start=1):
            self._run_round(index, phrase, needed)

    # ---- 口语 5 轮 ----
    def test_colloquial_phrases_round_1_to_5(self) -> None:
        for index, (phrase, needed) in enumerate(COLLOQUIAL_CASES, start=1):
            self._run_round(index, phrase, needed)

    # ---- 生产误触发回归（2026-10-14 全量 LLM 先行）----
    def test_hijack_regression_phrases_reach_llm(self) -> None:
        for index, (phrase, needed) in enumerate(HIJACK_REGRESSION_CASES, start=1):
            self._run_round(index, phrase, needed)

    def test_colloquial_generic_round_respects_live_read_budget(self) -> None:
        # 口语轮（进单）现读预算：今日预定读两次内放行、第三次 block。
        phrase = "今天进单多不多"
        session_id = "sess_accept_budget"
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_acc_b", "oc_accept_budget", "ou_user"),
        ), mock.patch.object(self.plugin, "_is_allowed_chat_id", return_value=True):
            self.plugin._on_pre_llm_call(
                session_id=session_id, user_message=phrase, conversation_history=[]
            )
            tool = f"{_MCP}get_today_reservations"
            self.assertIsNone(self.plugin._on_pre_tool_call(tool_name=tool, args={}, session_id=session_id))
            self.assertIsNone(self.plugin._on_pre_tool_call(tool_name=tool, args={}, session_id=session_id))
            blocked = self.plugin._on_pre_tool_call(tool_name=tool, args={}, session_id=session_id)
            self.assertEqual(blocked.get("action"), "block")
            self.assertIn("上限", blocked.get("message") or "")

    def test_colloquial_phrase_explored_then_learned_and_reused(self) -> None:
        # 探索→记录→复用回路：口语「昨天退了多少」探索成功后入库，再问
        # 仍落 generic_agent 但携带工具先验（白名单不变，仍全目录）。
        phrase = "昨天退了多少"
        route = route_domain_intent(phrase)
        self.assertTrue(is_generic_agent_route(route))
        self.assertEqual(route.learned_tools, ())

        self.assertTrue(learned_routes.record_learned_route(phrase, ["get_refund_status"]))
        reused = route_domain_intent(phrase)
        self.assertEqual(reused.intent, "generic_agent")
        self.assertEqual(reused.reason, GENERIC_AGENT_REASON)
        self.assertEqual(reused.learned_tools, ("get_refund_status",))
        self.assertTrue(is_tool_allowed(reused, "get_refund_status"))
        self.assertTrue(is_tool_allowed(reused, "get_ebk_period_read"))
        context = build_pre_llm_route_context(reused)
        self.assertIn("此前已成功回答过", context)
        self.assertIn("get_refund_status", context)
        for tool in FORBIDDEN_TOOLS:
            self.assertFalse(is_tool_allowed(reused, tool))


if __name__ == "__main__":
    unittest.main()
