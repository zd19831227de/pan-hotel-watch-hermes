from __future__ import annotations

import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest import mock


class WriteNotAllowedReasonTests(unittest.TestCase):
    """B2 — WRITE_NOT_ALLOWED_REASON 按平台生成，默认保持携程文案。"""

    def _safety(self):
        return importlib.import_module("app.domain.pricing_safety")

    def test_default_platform_keeps_legacy_ctrip_copy(self) -> None:
        safety = self._safety()
        self.assertEqual(safety.write_not_allowed_reason(), safety.WRITE_NOT_ALLOWED_REASON)
        self.assertEqual(safety.write_not_allowed_reason(None), safety.WRITE_NOT_ALLOWED_REASON)
        self.assertEqual(safety.write_not_allowed_reason("ctrip"), safety.WRITE_NOT_ALLOWED_REASON)
        self.assertIn("携程", safety.WRITE_NOT_ALLOWED_REASON)
        self.assertIn("不允许自动化改价", safety.WRITE_NOT_ALLOWED_REASON)

    def test_meituan_reason_uses_meituan_copy(self) -> None:
        reason = self._safety().write_not_allowed_reason("meituan")
        self.assertIn("美团", reason)
        self.assertNotIn("携程", reason)
        self.assertIn("直连或未确认账号", reason)
        self.assertIn("不允许自动化改价", reason)

    def test_fliggy_reason_uses_fliggy_copy(self) -> None:
        reason = self._safety().write_not_allowed_reason("fliggy")
        self.assertIn("飞猪", reason)
        self.assertNotIn("携程", reason)
        self.assertIn("直连或未确认账号", reason)

    def test_unknown_or_empty_platform_falls_back_to_ctrip(self) -> None:
        safety = self._safety()
        self.assertEqual(safety.write_not_allowed_reason("ota9"), safety.WRITE_NOT_ALLOWED_REASON)
        self.assertEqual(safety.write_not_allowed_reason(""), safety.WRITE_NOT_ALLOWED_REASON)

    def test_platform_value_is_normalized(self) -> None:
        safety = self._safety()
        self.assertEqual(
            safety.write_not_allowed_reason(" Meituan "),
            safety.write_not_allowed_reason("meituan"),
        )


class PricingPlatformResolutionTests(unittest.TestCase):
    """B2 — 拒绝文案的平台来源解析。"""

    def _safety(self):
        return importlib.import_module("app.domain.pricing_safety")

    def _sessions(self):
        return importlib.import_module("app.domain.browser.sessions")

    def test_browser_session_platform_resolution(self) -> None:
        safety = self._safety()
        sessions = self._sessions()
        store = mock.Mock()
        store.get.return_value = SimpleNamespace(platform="Fliggy")
        with mock.patch.object(sessions, "get_browser_session_store", return_value=store):
            self.assertEqual(safety.pricing_platform_for_browser_session("bs1"), "fliggy")

    def test_missing_session_falls_back_to_ctrip(self) -> None:
        safety = self._safety()
        sessions = self._sessions()
        store = mock.Mock()
        store.get.side_effect = KeyError("no session")
        with mock.patch.object(sessions, "get_browser_session_store", return_value=store):
            self.assertEqual(safety.pricing_platform_for_browser_session("bs1"), "ctrip")
        self.assertEqual(safety.pricing_platform_for_browser_session(None), "ctrip")
        self.assertEqual(safety.pricing_platform_for_browser_session(""), "ctrip")

    def test_preview_payload_platform_resolution(self) -> None:
        safety = self._safety()
        with mock.patch.object(
            safety, "pricing_platform_for_browser_session", return_value="meituan"
        ) as resolver:
            platform = safety.pricing_platform_from_preview_payload(
                {"preview_candidate": {"pricing_account": {"browser_session_id": "bs1"}}}
            )
        self.assertEqual(platform, "meituan")
        resolver.assert_called_once_with("bs1")

    def test_preview_payload_without_account_falls_back_to_ctrip(self) -> None:
        safety = self._safety()
        self.assertEqual(safety.pricing_platform_from_preview_payload({}), "ctrip")
        self.assertEqual(safety.pricing_platform_from_preview_payload(None), "ctrip")
        self.assertEqual(
            safety.pricing_platform_from_preview_payload(
                {"preview_candidate": {"pricing_account": {}}}
            ),
            "ctrip",
        )


class ExecutionRejectionCopyTests(unittest.TestCase):
    """B2 — 执行链拒绝点传入当前平台。"""

    def _service(self):
        return importlib.import_module("app.domain.pricing_execution.service")

    def _sessions(self):
        return importlib.import_module("app.domain.browser.sessions")

    def _patched_store(self, platform: str):
        store = mock.Mock()
        store.get.return_value = SimpleNamespace(platform=platform)
        return mock.patch.object(
            self._sessions(), "get_browser_session_store", return_value=store
        )

    def test_dry_run_rejection_uses_meituan_copy(self) -> None:
        service = self._service()
        with self._patched_store("meituan"):
            result = asyncio.run(
                service.prepare_pricing_page_write_dry_run(
                    execution_record_id=99,
                    account_policy={"connection_mode": "direct", "can_auto_write_price": False},
                    browser_session_id="bs1",
                )
            )
        self.assertEqual(result.status, "write_not_allowed")
        self.assertIn("美团", result.summary)
        self.assertNotIn("携程", result.summary)

    def test_execute_rejection_uses_fliggy_copy(self) -> None:
        service = self._service()
        with self._patched_store("fliggy"):
            result = asyncio.run(
                service.execute_pricing_page_write(
                    execution_record_id=99,
                    account_policy={"connection_mode": "unknown", "can_auto_write_price": False},
                    browser_session_id="bs1",
                )
            )
        self.assertEqual(result.status, "write_not_allowed")
        self.assertIn("飞猪", result.summary)
        self.assertNotIn("携程", result.summary)

    def test_execute_rejection_without_session_keeps_ctrip_copy(self) -> None:
        service = self._service()
        result = asyncio.run(
            service.execute_pricing_page_write(
                execution_record_id=99,
                account_policy={"connection_mode": "direct", "can_auto_write_price": False},
            )
        )
        self.assertEqual(result.status, "write_not_allowed")
        self.assertIn("携程", result.summary)

    def test_write_not_allowed_from_source_uses_platform_copy(self) -> None:
        service = self._service()
        source = SimpleNamespace(
            approval_record_id=None,
            preview_id=1,
            browser_session_id=None,
            execute_request_json={"pricing_account": {"browser_session_id": "bs2"}},
            result_json={},
        )
        with self._patched_store("fliggy"):
            result = service._write_not_allowed_from_source(
                source, {"connection_mode": "direct", "can_auto_write_price": False}
            )
        self.assertEqual(result.status, "write_not_allowed")
        self.assertIn("飞猪", result.summary)

    def test_write_not_allowed_result_uses_preview_platform_copy(self) -> None:
        service = self._service()
        preview = SimpleNamespace(
            id=1,
            watch_group_key="g1",
            preview_candidate_json={"pricing_account": {"browser_session_id": "bs3"}},
        )
        with self._patched_store("meituan"):
            result = service._write_not_allowed_result(
                event_type="dry_run",
                approval_record_id=None,
                preview=preview,
                policy={"connection_mode": "direct", "can_auto_write_price": False},
            )
        self.assertEqual(result.status, "write_not_allowed")
        self.assertIn("美团", result.summary)


class FeishuCustomPriceRejectionCopyTests(unittest.TestCase):
    """B2 — 自定义调价卡拒绝响应的平台文案。"""

    def _bot(self):
        return importlib.import_module("app.domain.notifications.feishu_bot")

    def test_custom_price_rejection_uses_platform_copy(self) -> None:
        bot = self._bot()
        payload = bot._custom_price_write_not_allowed_response(7, platform="fliggy")
        self.assertEqual(payload["status"], "write_not_allowed")
        self.assertIn("飞猪", payload["summary"])
        self.assertNotIn("携程", payload["summary"])

    def test_custom_price_rejection_defaults_to_ctrip(self) -> None:
        bot = self._bot()
        payload = bot._custom_price_write_not_allowed_response(7)
        self.assertEqual(payload["status"], "write_not_allowed")
        self.assertIn("携程", payload["summary"])


if __name__ == "__main__":
    unittest.main()
