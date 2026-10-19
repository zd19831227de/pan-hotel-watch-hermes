from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


def _load_plugin_module():
    plugin_path = (
        Path(__file__).resolve().parents[1]
        / "hermes_plugin"
        / "pan-hotel-feishu-session"
        / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("pan_hotel_feishu_session_mobile", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["pan_hotel_feishu_session_mobile"] = module
    spec.loader.exec_module(module)
    return module


class FeishuSessionMobileSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = _load_plugin_module()

    def setUp(self) -> None:
        self.plugin._CARD_SENT_BY_SESSION.clear()
        self.plugin._TEXT_REPLY_BY_SESSION.clear()

    def test_does_not_stage_summary_evidence_under_sole_text_first(self) -> None:
        payload = {
            "response_mode": "summary",
            "feishu_card_sent": False,
            "mobile_evidence": [
                {
                    "evidence_ref": "ev_occ",
                    "label": "出租率",
                    "value": "82",
                    "unit": "%",
                    "source": "ctrip_ebk",
                    "captured_at": "2026-07-20T09:20:00+08:00",
                    "freshness": "fresh",
                    "trust": "trusted",
                }
            ],
            "detail_intents": ["查看明细"],
            "safety_boundary": "read_only",
        }
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_get_operator_hotel_overview",
            result=json.dumps(payload, ensure_ascii=False),
            session_id="sess_summary",
        )
        self.assertFalse(hasattr(self.plugin, "_EVIDENCE_BY_SESSION") and "sess_summary" in getattr(self.plugin, "_EVIDENCE_BY_SESSION", {}))
        self.assertNotIn("sess_summary", self.plugin._CARD_SENT_BY_SESSION)

    def test_mobile_summary_json_never_auto_sends_card(self) -> None:
        text = (
            "<<<MOBILE_SUMMARY_JSON>>>\n"
            + json.dumps(
                {
                    "status": "success",
                    "conclusion": "今日经营正常偏强。",
                    "findings": [{"text": "出租率 82%", "evidence_refs": ["ev_occ"]}],
                    "actions": [{"text": "继续观察晚间入住"}],
                    "data_time": "2026-07-20 09:20",
                    "freshness": "fresh",
                    "missing_data": [],
                    "risk_flags": [],
                    "evidence_refs": ["ev_occ"],
                    "detail_intents": ["查看明细"],
                    "safety_boundary": "read_only",
                    "response_mode": "summary",
                },
                ensure_ascii=False,
            )
            + "\n<<<END_MOBILE_SUMMARY_JSON>>>"
        )
        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
            return_value=True,
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text=text,
                session_id="sess_ai",
                platform="feishu",
            )
        send_mock.assert_not_called()
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_detail_card_sent_hides_llm_without_summary_card(self) -> None:
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_get_operator_hotel_overview",
            result=json.dumps({"feishu_card_sent": True, "response_mode": "detail"}),
            session_id="sess_detail",
        )
        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
            return_value=True,
        ) as text_mock:
            result = self.plugin._on_transform_llm_output(
                response_text="明细已发，再补一段。",
                session_id="sess_detail",
                platform="feishu",
            )
        # 新行为：工具已发卡后 LLM 文字仍经 _send_text_as_card 发卡。
        text_mock.assert_called_once()
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)


if __name__ == "__main__":
    unittest.main()
