from __future__ import annotations

import unittest

from client.feishu_cards import attach_feishu_card, build_mobile_summary_card


class MobileSummaryCardTests(unittest.TestCase):
    def test_renders_conclusion_findings_actions_and_time(self) -> None:
        card = build_mobile_summary_card(
            {
                "status": "success",
                "conclusion": "今日经营正常偏强。",
                "findings": [
                    {"text": "出租率 82%", "evidence_refs": ["ev_occ"]},
                    {"text": "竞品最低 399 元", "evidence_refs": ["ev_comp"]},
                    {"text": "第三条发现", "evidence_refs": ["ev_third"]},
                    {"text": "不应展示的第四条", "evidence_refs": ["ev_fourth"]},
                ],
                "actions": [
                    {"text": "继续观察晚间入住"},
                    {"text": "核对明日房量"},
                    {"text": "不应展示的第三项行动"},
                ],
                "data_time": "2026-07-20 09:20",
                "freshness": "fresh",
                "missing_data": ["缺少渠道明细", "不应展示的第二个缺口"],
                "risk_flags": ["需关注库存", "不应展示的第二个风险"],
                "contract_version": "mobile_summary.v1",
                "safety_boundary": "read_only",
                "tool_name": "internal_summary_tool",
                "webhook": "https://example.invalid/secret",
            }
        )
        title = card["header"]["title"]["content"]
        self.assertIn("经营摘要", title)
        blob = str(card)
        self.assertIn("今日经营正常偏强", blob)
        self.assertIn("出租率 82%", blob)
        self.assertIn("2026-07-20 09:20", blob)
        self.assertNotIn("不应展示的第四条", blob)
        self.assertNotIn("不应展示的第三项行动", blob)
        self.assertNotIn("不应展示的第二个缺口", blob)
        self.assertNotIn("不应展示的第二个风险", blob)
        self.assertNotIn("evidence_refs", blob)
        self.assertNotIn("contract_version", blob)
        self.assertNotIn("safety_boundary", blob)
        self.assertNotIn("internal_summary_tool", blob)
        self.assertNotIn("example.invalid", blob)

    def test_attach_mobile_summary_kind(self) -> None:
        payload = attach_feishu_card(
            {
                "mobile_summary": {
                    "status": "success",
                    "conclusion": "ok",
                    "findings": [],
                    "actions": [],
                    "data_time": "—",
                }
            },
            "mobile_summary",
        )
        self.assertEqual(payload["feishu_card_kind"], "mobile_summary")
        self.assertIn("feishu_card", payload)


if __name__ == "__main__":
    unittest.main()
