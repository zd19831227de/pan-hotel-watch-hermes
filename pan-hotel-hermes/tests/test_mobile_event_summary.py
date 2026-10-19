from __future__ import annotations

import unittest

from client.mobile_event_summary import build_alert_mobile_summary, build_status_mobile_summary
from client.mobile_summary_contract import MOBILE_CONTRACT_VERSION, validate_mobile_summary


class MobileEventSummaryTests(unittest.TestCase):
    def test_competitor_increase_is_alert_only(self) -> None:
        summary = build_alert_mobile_summary(
            {
                "alert": {
                    "alert_direction": "increase",
                    "summary": "竞品上行 20 元",
                    "changed_items": [{"label": "高级大床房", "delta": "+20", "current": "420"}],
                },
                "operator_summary": {"latest_captured_at": "2026-07-20T16:05:00+08:00"},
            }
        )
        self.assertEqual(summary["contract_version"], MOBILE_CONTRACT_VERSION)
        self.assertIn("竞品涨价仅提醒", summary["risk_flags"])
        action_blob = " ".join(item.get("text", "") for item in summary["actions"])
        self.assertNotIn("涨价", action_blob)
        self.assertNotIn("调高本店", action_blob)
        evidence_index = {
            ref: {
                "evidence_ref": ref,
                "label": "竞品价格变化",
                "value": "竞品上行 20 元",
                "unit": "",
            }
            for ref in summary["evidence_refs"]
        }
        ok, errors = validate_mobile_summary(summary, evidence_index=evidence_index)
        self.assertTrue(ok, errors)

    def test_competitor_decrease_stays_reference_only(self) -> None:
        summary = build_alert_mobile_summary(
            {
                "alert": {
                    "alert_direction": "decrease",
                    "summary": "竞品下降 15 元",
                },
                "operator_summary": {"latest_captured_at": "2026-07-20T16:05:00+08:00"},
            }
        )
        action_blob = " ".join(item.get("text", "") for item in summary["actions"])
        self.assertIn("人工确认目标价", action_blob)
        self.assertIn("飞书审批", action_blob)
        self.assertNotIn("execute", action_blob.lower())
        self.assertNotIn("dry-run", action_blob.lower())

    def test_all_normal_status_is_quiet(self) -> None:
        summary = build_status_mobile_summary(
            {
                "operator_summary": {
                    "overall_status": "fresh",
                    "module_rows": [],
                    "latest_captured_at": "2026-07-20T12:00:00+08:00",
                },
                "checked_at": "2026-07-20T12:00:00+08:00",
            }
        )
        self.assertLessEqual(len(summary["findings"]), 1)
        self.assertIn("正常", summary["conclusion"])


if __name__ == "__main__":
    unittest.main()
