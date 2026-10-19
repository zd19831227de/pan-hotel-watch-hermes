from __future__ import annotations

import unittest

from client.operator_analysis_orchestrator import build_operator_analysis_context


class OperatorAnalysisOrchestratorTests(unittest.TestCase):
    def test_builds_provider_neutral_evidence_package(self) -> None:
        payload = build_operator_analysis_context(
            chat_context={
                "binding_mode": "single_store",
                "resolution": {
                    "status": "resolved",
                    "store_name": "静里全套房酒店",
                    "ebk_watch_group_key": "ctrip_ebooking_data_center",
                    "competitor_watch_group_key": "watch_1782109266413",
                },
            },
            overview={
                "operator_summary": {
                    "overview_rows": [
                        {"field": "出租率", "value": "82%", "source": "ctrip_ebk"},
                        {"field": "竞品A 路 今天", "value": "大床 / 无早 / ¥399", "source": "ctrip_competitor"},
                        {"field": "数据时间", "value": "EBK 09:20 / 竞品 16:06"},
                    ],
                    "next_actions": ["如需最新 EBK，请现读销售数据。"],
                }
            },
            recent_activity={
                "operator_summary": {
                    "overview_rows": [
                        {"field": "最近 EBK", "value": "09:20 成功", "source": "operator_recent_activity"}
                    ]
                }
            },
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["source"], "hermes_read_only_analysis_context")
        operator = payload["operator_summary"]
        self.assertEqual(operator["store_name"], "静里全套房酒店")

        source_by_key = {source["source_key"]: source for source in operator["sources"]}
        self.assertEqual(source_by_key["ctrip_ebk"]["status"], "available")
        self.assertEqual(source_by_key["ctrip_competitor"]["status"], "available")
        self.assertEqual(source_by_key["operator_recent_activity"]["status"], "available")
        self.assertEqual(source_by_key["meituan_ebk"]["status"], "snapshot_backed")
        self.assertEqual(source_by_key["fliggy_ebk"]["status"], "snapshot_backed")
        self.assertIn("get_ebk_joint_analysis_context", source_by_key["meituan_ebk"]["usage_note"])
        self.assertIn("get_ebk_joint_analysis_context", source_by_key["fliggy_ebk"]["usage_note"])

        self.assertEqual(operator["evidence_rows"][0]["value"], "82%")
        self.assertEqual(operator["evidence_rows"][1]["value"], "大床 / 无早 / ¥399")
        self.assertIn("可能原因", operator["analysis_sections"])
        self.assertTrue(operator["hypothesis_prompts"])
        self.assertIn("如需最新 EBK，请现读销售数据。", operator["recommended_next_reads"])

        evidence = payload["mobile_evidence"]
        self.assertTrue(evidence)
        self.assertEqual(evidence, operator["mobile_evidence"])
        first = evidence[0]
        for key in ("evidence_ref", "source", "label", "value", "captured_at", "freshness", "trust", "priority"):
            self.assertIn(key, first)
        refs = {item["evidence_ref"] for item in evidence}
        self.assertEqual(len(refs), len(evidence))

    def test_future_provider_slots_are_separate_from_current_missing_data(self) -> None:
        payload = build_operator_analysis_context(
            chat_context={"binding_mode": "unbound", "resolution": {"status": "resolved"}},
            overview={"operator_summary": {"overview_rows": []}},
            recent_activity={"operator_summary": {"overview_rows": []}},
        )

        operator = payload["operator_summary"]
        self.assertIn("今日概览暂无可用关键数据。", operator["missing_data"])
        self.assertIn("最近活动暂无可用摘要。", operator["missing_data"])
        future_keys = {slot["provider_key"] for slot in operator["future_provider_slots"]}
        self.assertEqual(future_keys, {"meituan_ebk", "fliggy_ebk"})
        future_by_key = {slot["provider_key"]: slot for slot in operator["future_provider_slots"]}
        self.assertEqual(future_by_key["meituan_ebk"]["status"], "snapshot_backed")
        self.assertEqual(future_by_key["fliggy_ebk"]["status"], "snapshot_backed")
        self.assertIn("get_ebk_joint_analysis_context", future_by_key["meituan_ebk"]["usage_note"])
        self.assertNotIn("美团", " ".join(operator["missing_data"]))
        self.assertNotIn("飞猪", " ".join(operator["missing_data"]))

    def test_includes_calendar_competitor_and_alert_evidence(self) -> None:
        payload = build_operator_analysis_context(
            chat_context={"binding_mode": "single_store", "resolution": {"status": "resolved", "store_name": "静里"}},
            overview={"operator_summary": {"overview_rows": [{"field": "出租率", "value": "70%", "source": "ctrip_ebk"}]}},
            recent_activity={"operator_summary": {"overview_rows": []}},
            calendar_latest={
                "operator_summary": {
                    "calendar_rows": [{"field": "2026-07-21", "value": "本店 411元，竞品 398元"}],
                    "latest_captured_at": "2026-07-20T10:00:00+08:00",
                }
            },
            competitor_trend={
                "operator_summary": {
                    "trend_rows": [{"field": "近3天", "value": "399→390→385"}],
                    "latest_captured_at": "2026-07-20T16:00:00+08:00",
                }
            },
            price_change_alert={
                "alert": {
                    "alert_direction": "increase",
                    "summary": "竞品上涨 20 元",
                    "changed_items": [{"label": "高级大床房", "delta": "+20"}],
                },
                "operator_summary": {"latest_captured_at": "2026-07-20T16:05:00+08:00"},
            },
        )
        refs = {item["evidence_ref"] for item in payload["mobile_evidence"]}
        self.assertTrue(any(ref.startswith("ev_calendar_") for ref in refs))
        self.assertTrue(any(ref.startswith("ev_trend_") for ref in refs))
        self.assertTrue(any(ref.startswith("ev_alert_") for ref in refs))
        self.assertIn("竞品涨价仅提醒", payload["operator_summary"]["risk_flags"])
        self.assertEqual(payload["operator_summary"]["safety_boundary"], "read_only")
        action_blob = " ".join(str(a) for a in payload["operator_summary"].get("candidate_actions") or [])
        self.assertNotIn("涨价", action_blob)
        self.assertNotIn("调高本店", action_blob)
        priorities = [int(item["priority"]) for item in payload["mobile_evidence"]]
        self.assertEqual(priorities, sorted(priorities))


if __name__ == "__main__":
    unittest.main()
