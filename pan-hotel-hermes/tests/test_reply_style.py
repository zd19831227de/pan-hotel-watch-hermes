from __future__ import annotations

import json
import unittest

from client.reply_style import HERMES_REPLY_STYLE_HINT, attach_reply_style_hint
from pan_hotel_mcp.server import _text_result


class ReplyStyleTests(unittest.TestCase):
    def test_attach_reply_style_hint_adds_guidance(self) -> None:
        payload = {"status": "success", "operator_summary": {"overall_status": "fresh"}}
        enriched = attach_reply_style_hint(payload)
        self.assertIn("reply_style_hint", enriched)
        self.assertIn("简体中文", enriched["reply_style_hint"])

    def test_attach_reply_style_hint_is_idempotent(self) -> None:
        payload = {"reply_style_hint": "已有提示"}
        enriched = attach_reply_style_hint(payload)
        self.assertEqual(enriched["reply_style_hint"], "已有提示")

    def test_text_result_includes_reply_style_hint(self) -> None:
        blocks = _text_result({"status": "success"})
        body = json.loads(blocks[0].text)
        self.assertEqual(body["reply_style_hint"], HERMES_REPLY_STYLE_HINT)
        self.assertIn("Markdown", body["reply_style_hint"])

    def test_reply_style_hint_disambiguates_feishu_group_roles(self) -> None:
        self.assertIn("运营问答上下文群", HERMES_REPLY_STYLE_HINT)
        self.assertIn("手动调价指令群", HERMES_REPLY_STYLE_HINT)
        self.assertIn("价格变化提醒群", HERMES_REPLY_STYLE_HINT)

    def test_reply_style_mentions_compact_analysis_sections(self) -> None:
        self.assertIn("结论", HERMES_REPLY_STYLE_HINT)
        self.assertIn("依据", HERMES_REPLY_STYLE_HINT)
        self.assertIn("可能原因", HERMES_REPLY_STYLE_HINT)
        self.assertIn("建议", HERMES_REPLY_STYLE_HINT)
        self.assertIn("数据缺口", HERMES_REPLY_STYLE_HINT)

    def test_reply_style_uses_text_first_contract(self) -> None:
        self.assertIn("一律短文字", HERMES_REPLY_STYLE_HINT)
        self.assertIn("response_mode=detail", HERMES_REPLY_STYLE_HINT)
        self.assertIn("无效", HERMES_REPLY_STYLE_HINT)
        self.assertNotIn("MOBILE_SUMMARY_JSON", HERMES_REPLY_STYLE_HINT)


if __name__ == "__main__":
    unittest.main()
