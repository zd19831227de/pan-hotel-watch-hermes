from __future__ import annotations

import unittest

from client.mobile_summary_contract import (
    MAX_ACTIONS,
    MAX_CHINESE_CHARS,
    MAX_FINDINGS,
    MOBILE_CONTRACT_VERSION,
    build_rule_mobile_summary,
    count_chinese_chars,
    normalize_mobile_summary,
    validate_mobile_summary,
)


class MobileSummaryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence_index = {
            "ev_occ": {
                "evidence_ref": "ev_occ",
                "source": "ctrip_ebk",
                "label": "出租率",
                "value": "82",
                "unit": "%",
                "captured_at": "2026-07-20T09:20:00+08:00",
                "freshness": "fresh",
                "trust": "trusted",
            },
            "ev_comp": {
                "evidence_ref": "ev_comp",
                "source": "ctrip_competitor",
                "label": "竞品最低价",
                "value": "399",
                "unit": "元",
                "captured_at": "2026-07-20T16:06:00+08:00",
                "freshness": "fresh",
                "trust": "trusted",
            },
        }

    def test_normalize_sets_version_and_caps(self) -> None:
        raw = {
            "status": "success",
            "conclusion": "今日经营正常偏强。",
            "findings": [
                {"text": "出租率 82%", "evidence_refs": ["ev_occ"]},
                {"text": "竞品最低 399 元", "evidence_refs": ["ev_comp"]},
                {"text": "多余3", "evidence_refs": ["ev_occ"]},
                {"text": "多余4", "evidence_refs": ["ev_occ"]},
            ],
            "actions": [
                {"text": "继续观察晚间入住"},
                {"text": "核对低价房型曝光"},
                {"text": "多余动作"},
            ],
            "data_time": "2026-07-20 09:20",
            "freshness": "fresh",
            "missing_data": [],
            "risk_flags": [],
            "evidence_refs": ["ev_occ", "ev_comp"],
            "detail_intents": ["查看明细"],
            "safety_boundary": "read_only",
            "response_mode": "summary",
        }
        result = normalize_mobile_summary(raw, evidence_index=self.evidence_index)
        self.assertEqual(result["contract_version"], MOBILE_CONTRACT_VERSION)
        self.assertLessEqual(len(result["findings"]), MAX_FINDINGS)
        self.assertLessEqual(len(result["actions"]), MAX_ACTIONS)
        self.assertEqual(result["response_mode"], "summary")

    def test_finding_without_evidence_ref_is_rejected(self) -> None:
        summary = {
            "status": "success",
            "conclusion": "出租率很好。",
            "findings": [{"text": "出租率 99%", "evidence_refs": []}],
            "actions": [],
            "data_time": "2026-07-20 09:20",
            "freshness": "fresh",
            "missing_data": [],
            "risk_flags": [],
            "evidence_refs": [],
            "detail_intents": [],
            "safety_boundary": "read_only",
            "response_mode": "summary",
        }
        ok, errors = validate_mobile_summary(summary, evidence_index=self.evidence_index)
        self.assertFalse(ok)
        self.assertTrue(any("evidence" in e for e in errors))

    def test_number_without_evidence_ref_cannot_enter_card(self) -> None:
        summary = {
            "status": "success",
            "conclusion": "今日偏弱。",
            "findings": [{"text": "订单 120 间", "evidence_refs": ["ev_missing"]}],
            "actions": [{"text": "先刷新 EBK"}],
            "data_time": "未知",
            "freshness": "unknown",
            "missing_data": ["订单证据缺失"],
            "risk_flags": [],
            "evidence_refs": ["ev_missing"],
            "detail_intents": ["查看明细"],
            "safety_boundary": "read_only",
            "response_mode": "summary",
        }
        ok, errors = validate_mobile_summary(summary, evidence_index=self.evidence_index)
        self.assertFalse(ok)

    def test_chinese_budget_enforced(self) -> None:
        long_text = "经" * (MAX_CHINESE_CHARS + 20)
        summary = {
            "status": "success",
            "conclusion": long_text,
            "findings": [{"text": "出租率 82%", "evidence_refs": ["ev_occ"]}],
            "actions": [{"text": "观察"}],
            "data_time": "2026-07-20 09:20",
            "freshness": "fresh",
            "missing_data": [],
            "risk_flags": [],
            "evidence_refs": ["ev_occ"],
            "detail_intents": [],
            "safety_boundary": "read_only",
            "response_mode": "summary",
        }
        normalized = normalize_mobile_summary(summary, evidence_index=self.evidence_index)
        visible = (
            str(normalized["conclusion"])
            + "".join(str(f.get("text") or "") for f in normalized["findings"])
            + "".join(str(a.get("text") or "") for a in normalized["actions"])
            + str(normalized.get("data_time") or "")
        )
        self.assertLessEqual(count_chinese_chars(visible), MAX_CHINESE_CHARS)

    def test_rule_fallback_uses_same_structure(self) -> None:
        items = list(self.evidence_index.values())
        summary = build_rule_mobile_summary(
            evidence_items=items,
            status="success",
            missing_data=[],
            risk_flags=[],
            safety_boundary="read_only",
            detail_intents=["查看明细", "展开竞品价格"],
        )
        self.assertEqual(summary["contract_version"], MOBILE_CONTRACT_VERSION)
        ok, errors = validate_mobile_summary(summary, evidence_index=self.evidence_index)
        self.assertTrue(ok, errors)
        self.assertLessEqual(len(summary["findings"]), MAX_FINDINGS)

    def test_sensitive_markers_stripped(self) -> None:
        summary = {
            "status": "failed",
            "conclusion": "读取失败",
            "findings": [
                {
                    "text": "webhook https://open.feishu.cn/open-apis/bot/v2/hook/secret",
                    "evidence_refs": ["ev_occ"],
                }
            ],
            "actions": [],
            "data_time": "2026-07-20 09:20",
            "freshness": "failed",
            "missing_data": [],
            "risk_flags": ["读取失败"],
            "evidence_refs": ["ev_occ"],
            "detail_intents": [],
            "safety_boundary": "read_only",
            "response_mode": "summary",
        }
        normalized = normalize_mobile_summary(summary, evidence_index=self.evidence_index)
        blob = str(normalized)
        self.assertNotIn("open-apis/bot", blob)
        self.assertNotIn("hook/secret", blob)


if __name__ == "__main__":
    unittest.main()
