"""Hermes Q&A text-first acceptance matrix (no live Feishu / no DB writes)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from client.reply_policy import default_reply_mode_for_tool, is_reply_policy_v2

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "skills" / "pan-hotel-watch" / "SKILL.md").read_text(encoding="utf-8")
MANXIN = (ROOT / "config" / "profiles" / "manxin-ai" / "SOUL.md").read_text(encoding="utf-8")
JINGLI = (ROOT / "config" / "profiles" / "jingli-ai" / "SOUL.md").read_text(encoding="utf-8")
SOUL = (ROOT / "config" / "hermes.SOUL.md").read_text(encoding="utf-8")


class HermesQnaAcceptanceTests(unittest.TestCase):
    def test_reply_policy_defaults_to_v2_text_first(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            # Explicit unset of rollback switches.
            env = {k: v for k, v in __import__("os").environ.items() if k != "PAN_HOTEL_REPLY_POLICY"}
            with mock.patch.dict("os.environ", env, clear=True):
                self.assertTrue(is_reply_policy_v2())
                for tool in (
                    "read_ebk_datasets",
                    "get_ebk_joint_analysis_context",
                    "get_competitor_latest_prices",
                    "get_competitor_price_trend",
                    "run_ebk_watch_once",
                    "resolve_feishu_chat_context",
                ):
                    self.assertEqual(default_reply_mode_for_tool(tool), "text", tool)
                self.assertEqual(
                    default_reply_mode_for_tool("create_manual_pricing_approval"),
                    "action_card",
                )
                self.assertEqual(
                    default_reply_mode_for_tool("dispatch_watch_feishu_event"),
                    "action_card",
                )

    def test_question_matrix_documents_expected_tools(self) -> None:
        cases = (
            ("今天出租率多少", "read_ebk_datasets"),
            ("各平台", "get_ebk_joint_analysis_context"),
            ("综合分析今日经营", "get_ebk_joint_analysis_context"),
            # 2026-10-14 全量 LLM 先行：问价与固定格式指令由 LLM 判断后
            # dispatch 转发原话；竞品/公开页现价仍走竞品持久化读数。
            ("本店房型现价", "get_competitor_latest_prices"),
            ("竞品", "get_competitor_latest_prices"),
            ("问价目表现状", "dispatch_watch_feishu_event"),
            ("最近变化", "analyze_competitor_price_change"),
            ("最近三天", "get_competitor_price_trend"),
            ("波动图", "get_competitor_price_trend"),
            ("run_ebk_watch_once", "run_ebk_watch_once"),
            ("为什么订单弱", "get_operator_analysis_context"),
            ("把今天某房型调到420", "dispatch_watch_feishu_event"),
            ("dispatch_watch_feishu_event", "dispatch_watch_feishu_event"),
            ("web_search", "web_search"),
        )
        for needle, tool in cases:
            with self.subTest(needle=needle, tool=tool):
                self.assertIn(needle, SKILL)
                self.assertIn(tool, SKILL)

    def test_text_first_policy_in_soul_and_profiles(self) -> None:
        for text, label in (
            (SOUL, "public"),
            (MANXIN, "manxin"),
            (JINGLI, "jingli"),
        ):
            with self.subTest(profile=label):
                self.assertIn("短文字", text)
                self.assertNotIn("<<<MOBILE_SUMMARY_JSON>>>", text)
        self.assertIn("禁止**发状态卡", SOUL)
        self.assertIn("`response_mode=detail` 对问答工具无效", SOUL)
        self.assertIn("reply_policy", MANXIN)
        self.assertIn("reply_policy", JINGLI)
        self.assertIn("禁止简报卡", MANXIN)
        self.assertIn("禁止简报卡", JINGLI)

    def test_skill_forbids_invented_metrics_and_keeps_pricing_safety(self) -> None:
        self.assertIn("禁止编造", SKILL)
        self.assertIn("竞品涨价仅提醒", SKILL)
        self.assertIn("立即调价", SKILL)
        self.assertIn("写入携程", SKILL)
        self.assertIn("禁止输出 `MOBILE_SUMMARY_JSON`", SKILL)

    def test_plugin_requires_current_route_before_allowing_read_tools(self) -> None:
        import importlib.util

        path = ROOT / "hermes_plugin" / "pan-hotel-feishu-session" / "__init__.py"
        spec = importlib.util.spec_from_file_location("pan_hotel_feishu_session_plugin", path)
        assert spec and spec.loader
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)

        with mock.patch.object(
            plugin,
            "_session_fields",
            return_value=("om_acc", "oc_acc", "ou_user"),
        ), mock.patch.object(plugin, "_inject_feishu_session"):
            cases = (
                ("今天出租率多少", "mcp_pan_hotel_watch_read_ebk_datasets"),
                ("各平台经营情况", "mcp_pan_hotel_watch_get_ebk_joint_analysis_context"),
                ("现在竞品多少钱", "mcp_pan_hotel_watch_get_competitor_latest_prices"),
                ("最近三天竞品趋势", "mcp_pan_hotel_watch_get_competitor_price_trend"),
                ("刷新 EBK", "mcp_pan_hotel_watch_run_ebk_watch_once"),
            )
            for index, (user_text, tool) in enumerate(cases):
                with self.subTest(tool=tool):
                    session_id = f"sess_acceptance_{index}"
                    plugin._on_pre_llm_call(
                        session_id=session_id,
                        user_message=user_text,
                        conversation_history=[],
                    )
                    self.assertIsNone(
                        plugin._on_pre_tool_call(
                            tool_name=tool,
                            args={},
                            session_id=session_id,
                        )
                    )

    def test_backend_price_change_card_builder_still_present(self) -> None:
        # Acceptance: do not regress competitor polling cards in this Q&A change set.
        from client import feishu_cards

        self.assertTrue(callable(feishu_cards.build_price_change_card))
        self.assertTrue(callable(feishu_cards.build_manual_price_confirmation_card))


if __name__ == "__main__":
    unittest.main()
