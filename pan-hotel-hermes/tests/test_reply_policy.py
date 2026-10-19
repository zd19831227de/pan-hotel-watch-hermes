import os
import unittest
from unittest import mock

from client import reply_policy


class ReplyPolicyTests(unittest.TestCase):
    def test_text_first_enabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PAN_HOTEL_REPLY_POLICY", None)
            self.assertTrue(reply_policy.is_reply_policy_v2())

    def test_v2_enabled_by_env(self) -> None:
        with mock.patch.dict(os.environ, {"PAN_HOTEL_REPLY_POLICY": "v2"}):
            self.assertTrue(reply_policy.is_reply_policy_v2())

    def test_v2_defaults_all_read_tools_to_text(self) -> None:
        with mock.patch.dict(os.environ, {"PAN_HOTEL_REPLY_POLICY": "v2"}):
            self.assertEqual(
                reply_policy.default_reply_mode_for_tool("read_ebk_datasets"),
                "text",
            )
            self.assertEqual(
                reply_policy.default_reply_mode_for_tool("resolve_feishu_chat_context"),
                "text",
            )
            self.assertEqual(
                reply_policy.default_reply_mode_for_tool("get_ebk_joint_analysis_context"),
                "text",
            )
            self.assertEqual(
                reply_policy.default_reply_mode_for_tool("get_competitor_price_trend"),
                "text",
            )
            self.assertEqual(
                reply_policy.default_reply_mode_for_tool("create_manual_pricing_approval"),
                "action_card",
            )

    def test_v1_env_no_longer_disables_text_first(self) -> None:
        """reply_policy v2 is the sole policy; env cannot roll back to legacy cards."""
        with mock.patch.dict(os.environ, {"PAN_HOTEL_REPLY_POLICY": "v1"}):
            self.assertTrue(reply_policy.is_reply_policy_v2())
            self.assertEqual(
                reply_policy.default_reply_mode_for_tool("read_ebk_datasets"),
                "text",
            )
            self.assertEqual(
                reply_policy.default_reply_mode_for_tool("create_manual_pricing_approval"),
                "action_card",
            )

    def test_hermes_qna_never_sends_briefing_cards(self) -> None:
        """Hermes answers are text-only; model/user detail asks cannot force briefing cards."""
        for tool in (
            "get_watch_scheduler_status",
            "get_competitor_latest_prices",
            "resolve_feishu_chat_context",
            "get_competitor_price_trend",
            "analyze_competitor_price_change",
            "get_ebk_latest_result",
        ):
            self.assertEqual(
                reply_policy.resolve_tool_response_mode(
                    tool,
                    requested_mode="detail",
                    user_text="请发卡片展开明细看趋势图",
                ),
                "text",
                tool,
            )

    def test_approval_tools_still_use_action_cards(self) -> None:
        self.assertEqual(
            reply_policy.resolve_tool_response_mode(
                "create_manual_pricing_approval",
                requested_mode="text",
                user_text="把今天大床房调到420",
            ),
            "detail",
        )
        self.assertEqual(
            reply_policy.resolve_tool_response_mode(
                "dispatch_watch_feishu_event",
                requested_mode="detail",
                user_text="",
            ),
            "detail",
        )


if __name__ == "__main__":
    unittest.main()
