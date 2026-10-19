from __future__ import annotations

import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest import mock


class PricingPlatformResolverTests(unittest.TestCase):
    """V9 Step 3 — 执行链按账号平台分发到对应浏览器服务。"""

    def _service(self):
        return importlib.import_module("app.domain.pricing_execution.service")

    def test_resolver_exists(self) -> None:
        service = self._service()
        self.assertTrue(hasattr(service, "_pricing_browser_service_for"))
        self.assertTrue(hasattr(service, "_pricing_platform_for_browser_session"))

    def test_resolver_maps_platforms_to_services(self) -> None:
        service = self._service()
        meituan_module = importlib.import_module("app.domain.browser.meituan_ebooking")
        fliggy_module = importlib.import_module("app.domain.browser.fliggy_ebooking")
        ctrip_module = importlib.import_module("app.domain.browser.ctrip_ebooking")
        meituan_sentinel = object()
        fliggy_sentinel = object()
        ctrip_sentinel = object()
        with mock.patch.object(
            meituan_module, "get_meituan_ebooking_service", return_value=meituan_sentinel
        ), mock.patch.object(
            fliggy_module, "get_fliggy_ebooking_service", return_value=fliggy_sentinel
        ), mock.patch.object(
            ctrip_module, "get_ctrip_ebooking_service", return_value=ctrip_sentinel
        ):
            self.assertIs(service._pricing_browser_service_for("meituan"), meituan_sentinel)
            self.assertIs(service._pricing_browser_service_for("fliggy"), fliggy_sentinel)
            self.assertIs(service._pricing_browser_service_for("ctrip"), ctrip_sentinel)
            self.assertIs(service._pricing_browser_service_for(None), ctrip_sentinel)
            self.assertIs(service._pricing_browser_service_for("unknown"), ctrip_sentinel)

    def test_platform_label_map_covers_three_platforms(self) -> None:
        service = self._service()
        labels = service._PRICING_PLATFORM_LABELS
        self.assertEqual(labels["ctrip"], "携程")
        self.assertEqual(labels["meituan"], "美团")
        self.assertEqual(labels["fliggy"], "飞猪")


class DryRunDispatchTests(unittest.TestCase):
    def _service(self):
        return importlib.import_module("app.domain.pricing_execution.service")

    def test_dry_run_dispatches_to_meituan_browser_service(self) -> None:
        service = self._service()
        sessions_module = importlib.import_module("app.domain.browser.sessions")
        meituan_module = importlib.import_module("app.domain.browser.meituan_ebooking")
        source = SimpleNamespace(
            approval_record_id=2,
            preview_id=1,
            execute_request_json={"request_id": "r1", "actions": []},
            status="preflight_ready",
            event_type="preflight",
            platform_account_id="mt_acc",
            browser_session_id="mt_session",
        )
        policy = {"connection_mode": "non_direct", "can_auto_write_price": True}

        browser_result = {
            "status": "page_write_preview_ready",
            "summary": "Meituan EBK price-write dry-run preview is ready.",
            "platform_account_id": "mt_acc",
            "browser_session_id": "mt_session",
            "data": {"query_type": "meituan_ebooking_pricing_page_write_dry_run"},
            "evidence": {"browser_write_invoked": False},
        }

        calls: list[str] = []

        class _FakeMeituanService:
            async def prepare_pricing_page_write_dry_run(self, **kwargs):
                calls.append("meituan-dry-run")
                return browser_result

        class _FakeSessionStore:
            def get(self, session_id: str):
                return SimpleNamespace(platform="meituan", browser_session_id=session_id)

        with mock.patch.object(
            service,
            "_load_execution_with_policy",
            new=mock.AsyncMock(return_value=(source, policy)),
        ), mock.patch.object(
            service,
            "_save_execution_result",
            new=mock.AsyncMock(side_effect=lambda **kwargs: kwargs["result"]),
        ), mock.patch.object(
            service,
            "get_browser_session_id_for_platform_account",
            return_value="mt_session",
        ), mock.patch.object(
            sessions_module,
            "get_browser_session_store",
            return_value=_FakeSessionStore(),
        ), mock.patch.object(
            meituan_module,
            "get_meituan_ebooking_service",
            return_value=_FakeMeituanService(),
        ):
            result = asyncio.run(
                service.prepare_pricing_page_write_dry_run(
                    execution_record_id=11,
                    account_policy=policy,
                    platform_account_id="mt_acc",
                    browser_session_id="mt_session",
                )
            )
        self.assertEqual(calls, ["meituan-dry-run"])
        self.assertEqual(result.status, "page_write_preview_ready")

    def test_dry_run_meituan_non_direct_passes_whitelist_gate_after_b8(self) -> None:
        """B8 白名单放开后：美团 non_direct 策略过闸（dry-run 分发见上面的 dispatch 测试）。"""
        sessions = importlib.import_module("app.domain.browser.sessions")
        policy = sessions.account_write_policy(
            SimpleNamespace(platform="meituan", connection_mode="non_direct")
        )
        self.assertTrue(policy["can_auto_write_price"])
        # 未加载到执行记录时不得被账号闸门拦截（区分 not_found 与 write_not_allowed）。
        service = self._service()
        result = asyncio.run(
            service.prepare_pricing_page_write_dry_run(
                execution_record_id=12,
                account_policy=policy,
            )
        )
        self.assertEqual(result.status, "not_found")


if __name__ == "__main__":
    unittest.main()
