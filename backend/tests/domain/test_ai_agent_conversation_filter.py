from __future__ import annotations

import unittest

from app.domain.ai_agent.conversation import _is_non_authoritative


class AiAgentConversationFilterTests(unittest.TestCase):
    def test_failed_tool_turns_are_not_authoritative_context(self) -> None:
        for text in (
            "tool_not_allowed: wrong route",
            "工具调用失败，请稍后重试",
            "浏览器读取超时",
            "invalid_arguments",
        ):
            with self.subTest(text=text):
                self.assertTrue(_is_non_authoritative(text, tool_calls='[{"name":"tool"}]'))

    def test_successful_business_answer_remains_authoritative(self) -> None:
        self.assertFalse(_is_non_authoritative("当前出租率为 78%，数据时间 10:30。"))

    def test_plain_reply_that_mentions_timeout_is_not_filtered(self) -> None:
        self.assertFalse(_is_non_authoritative("上一次读取超时，但当前数据已经恢复。"))


if __name__ == "__main__":
    unittest.main()
