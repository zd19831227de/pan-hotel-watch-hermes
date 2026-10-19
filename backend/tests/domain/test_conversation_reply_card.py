from __future__ import annotations

import unittest

from app.domain.notifications.operator_feishu_card import (
    _sanitize_conversation_reply_text,
    build_conversation_reply_card,
)


def _card_text_content(card: dict) -> str:
    chunks: list[str] = []
    for element in card.get("elements", []):
        if element.get("tag") == "markdown":
            chunks.append(str(element.get("content") or ""))
        elif element.get("tag") == "table":
            for row in element.get("rows", []):
                chunks.extend(str(value) for value in row.values())
        elif element.get("tag") == "note":
            chunks.extend(
                str(item.get("content") or "") for item in element.get("elements", [])
            )
    return "\n".join(chunks)


class ConversationReplyCardTests(unittest.TestCase):
    def test_build_conversation_reply_card_has_briefing_shape(self) -> None:
        card = build_conversation_reply_card("静里上周出租率 82%。", title="运营助手")
        self.assertEqual(card["header"]["template"], "green")
        self.assertIn("运营助手", card["header"]["title"]["content"])
        self.assertEqual(card["elements"][0]["tag"], "markdown")
        self.assertIn("82%", card["elements"][0]["content"])

    def test_build_conversation_reply_card_auto_selects_data_report_template(self) -> None:
        card = build_conversation_reply_card(
            "本周经营速览：\n出租率：82%\n均价：431 元\n间夜：56",
            title="运营助手",
        )
        payload = _card_text_content(card)
        self.assertTrue(any(element.get("tag") == "table" for element in card["elements"]))
        self.assertIn("82%", payload)

    def test_build_conversation_reply_card_keeps_markdown_structure(self) -> None:
        structured_text = "# 今日速览\n- 出租率 82%\n**重点关注**明早早餐房"
        card = build_conversation_reply_card(structured_text, title="运营助手")
        payload = _card_text_content(card)
        self.assertIn("# 今日速览", payload)
        self.assertIn("- 出租率 82%", payload)
        self.assertIn("**重点关注**", payload)

    def test_build_conversation_reply_card_cleans_internal_leaks_but_keeps_structure(self) -> None:
        noisy_text = """
# 运营助手输出
静里今天出租率 82%，建议关注明早早餐房。
```
{"operator_summary": {"mcp_tool": "get_operator_hotel_overview"}}
```
| 字段 | 值 |
| --- | --- |
| mcp_tool | operator_summary |
skill_view: internal
mcp_route: resolve_feishu_chat_context
C:\\Users\\hotel\\.runtime\\profile
sk-abcdefghijklmnop1234567890
请继续观察竞品价格变化。
"""

        card = build_conversation_reply_card(noisy_text, title="运营助手")
        payload = _card_text_content(card)

        self.assertIn("静里今天出租率 82%", payload)
        self.assertIn("请继续观察竞品价格变化", payload)
        self.assertIn("# 运营助手输出", payload)
        self.assertIn("| 字段 | 值 |", payload)
        self.assertNotIn("operator_summary", payload)
        self.assertNotIn("mcp_", payload)
        self.assertNotIn("skill_view", payload)
        self.assertNotIn("{", payload)
        self.assertNotIn("}", payload)
        self.assertNotIn("sk-abcdefghijklmnop", payload)
        self.assertNotIn("profile", payload)

    def test_build_conversation_reply_card_preserves_temperate_emoji(self) -> None:
        card = build_conversation_reply_card("📊 今日订单 16 单\n✅ 数据已更新", title="运营助手")
        payload = _card_text_content(card)
        self.assertIn("📊 今日订单 16 单", payload)
        self.assertIn("✅ 数据已更新", payload)


class ConversationReplySanitizerTests(unittest.TestCase):
    def test_sanitizer_allows_markdown_structure(self) -> None:
        text = "# 标题\n| a | b |\n| --- | --- |\n| 1 | 2 |\n- 列表项\n**加粗** `code`"
        cleaned = _sanitize_conversation_reply_text(text)
        self.assertIn("# 标题", cleaned)
        self.assertIn("| a | b |", cleaned)
        self.assertIn("- 列表项", cleaned)
        self.assertIn("**加粗** `code`", cleaned)

    def test_sanitizer_still_blocks_leaks(self) -> None:
        cleaned = _sanitize_conversation_reply_text(
            "\n".join(
                [
                    '{"json": "leak"}',
                    "mcp_tool called operator_summary",
                    "skill_view: internal",
                    "path is C:\\Users\\hotel\\profiles\\default",
                    "key sk-abcdefghijklmnop1234",
                    "webhook https://open.feishu.cn/open-apis/bot/hook/abcdef",
                    "正常内容保留",
                ]
            )
        )
        self.assertIn("正常内容保留", cleaned)
        self.assertNotIn("{", cleaned)
        self.assertNotIn("mcp_", cleaned)
        self.assertNotIn("skill_view", cleaned)
        self.assertNotIn("C:\\", cleaned)
        self.assertNotIn("sk-", cleaned)
        self.assertNotIn("hook/abcdef", cleaned)

    def test_sanitizer_blocks_multiline_json_block(self) -> None:
        text = "\n".join(
            [
                "正常段落一",
                "{",
                '  "operator": "静里",',
                '  "rooms": 42,',
                "}",
                "正常段落二",
            ]
        )
        cleaned = _sanitize_conversation_reply_text(text)
        self.assertIn("正常段落一", cleaned)
        self.assertIn("正常段落二", cleaned)
        self.assertNotIn("{", cleaned)
        self.assertNotIn('"operator"', cleaned)
        self.assertNotIn("rooms", cleaned)

    def test_sanitizer_blocks_fenced_json_block(self) -> None:
        text = "\n".join(
            [
                "回答如下：",
                "```json",
                '{"a": 1,',
                ' "b": 2}',
                "```",
                "结束",
            ]
        )
        cleaned = _sanitize_conversation_reply_text(text)
        self.assertIn("回答如下：", cleaned)
        self.assertIn("结束", cleaned)
        self.assertNotIn('"a"', cleaned)
        self.assertNotIn("```json", cleaned)

    def test_sanitizer_allows_non_json_code_fence(self) -> None:
        text = "说明\n```\nSELECT * FROM table\n```"
        cleaned = _sanitize_conversation_reply_text(text)
        self.assertIn("SELECT * FROM table", cleaned)


if __name__ == "__main__":
    unittest.main()
