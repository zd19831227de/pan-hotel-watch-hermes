from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

from app.domain.browser.ctrip_ebooking import CtripBrowserProfileLockedError
from app.domain.competitor_prices.types import SCREENSHOT_KEYS
from app.domain.operator_payload import (
    sanitize_operator_payload,
    sanitize_persisted_payload,
    sanitize_supervisor_payload,
)


class OperatorPayloadSanitizationTests(unittest.TestCase):
    def test_three_sanitization_scopes_share_sensitive_field_contract(self) -> None:
        self.assertIn("before_screenshot", SCREENSHOT_KEYS)
        self.assertIn("after_screenshot", SCREENSHOT_KEYS)
        payload = {
            "before_screenshot": r"C:\tmp\before.png",
            "after_screenshot": [r"C:\tmp\after.png"],
            "browser_profile_path": r"C:\runtime\profile",
            "verification_code": "123456",
            "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/secret",
        }

        persisted = sanitize_persisted_payload(payload)
        operator = sanitize_operator_payload(payload)
        supervisor = sanitize_supervisor_payload(payload)

        self.assertEqual(persisted["before_screenshot"], "artifact_not_persisted")
        self.assertEqual(persisted["after_screenshot"], ["artifact_not_persisted"])
        self.assertEqual(persisted["browser_profile_path"], "profile_path_not_persisted")
        self.assertEqual(operator["before_screenshot"], "artifact_not_returned")
        self.assertEqual(operator["after_screenshot"], ["artifact_not_returned"])
        self.assertEqual(operator["browser_profile_path"], "profile_path_not_returned")
        self.assertEqual(supervisor, operator)
        for result in (persisted, operator, supervisor):
            self.assertEqual(result["verification_code"], "***")
            self.assertEqual(result["feishu_webhook_url"], "***")

    def test_response_payload_hides_artifacts_profiles_and_secret_urls(self) -> None:
        payload = {
            "status": "human_required",
            "evidence": {
                "screenshot_path": r"C:\Users\Administrator\Desktop\pan-hotel-watch\output\playwright\ctrip.png",
                "screenshot_paths": [
                    "output/playwright/ctrip-a.png",
                    r"C:\pan-hotel-watch-runtime\ctrip-b.png",
                ],
                "persistent_profile_dir": r"C:\pan-hotel-watch-runtime\ctrip-ebk-profile",
                "profile_path_returned": False,
                "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/secret-token",
                "webhook_called": False,
                "password_returned": False,
                "browser_session_id": "browser-ebk",
            },
            "data": {
                "snapshot": {
                    "raw_snippets": ["真实页面文本可以保留"],
                    "local_artifact": r"C:\tmp\page.png",
                }
            },
        }

        sanitized = sanitize_operator_payload(payload)
        serialized = json.dumps(sanitized, ensure_ascii=False)

        self.assertNotIn("C:\\Users\\Administrator", serialized)
        self.assertNotIn("C:\\pan-hotel-watch-runtime", serialized)
        self.assertNotIn("output/playwright", serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertEqual(sanitized["evidence"]["screenshot_path"], "artifact_not_returned")
        self.assertEqual(
            sanitized["evidence"]["screenshot_paths"],
            ["artifact_not_returned", "artifact_not_returned"],
        )
        self.assertEqual(
            sanitized["evidence"]["persistent_profile_dir"],
            "profile_path_not_returned",
        )
        self.assertEqual(sanitized["evidence"]["feishu_webhook_url"], "***")
        self.assertIs(sanitized["evidence"]["profile_path_returned"], False)
        self.assertIs(sanitized["evidence"]["webhook_called"], False)
        self.assertIs(sanitized["evidence"]["password_returned"], False)
        self.assertEqual(sanitized["data"]["snapshot"]["raw_snippets"], ["真实页面文本可以保留"])
        self.assertEqual(sanitized["data"]["snapshot"]["local_artifact"], "artifact_not_returned")

    def test_public_https_login_urls_are_preserved(self) -> None:
        payload = {
            "login_url": "https://eb.meituan.com/ebk/login/login.html",
            "current_url": "https://ebooking.ctrip.com/login/index",
        }

        sanitized = sanitize_operator_payload(payload)

        self.assertEqual(sanitized["login_url"], "https://eb.meituan.com/ebk/login/login.html")
        self.assertEqual(sanitized["current_url"], "https://ebooking.ctrip.com/login/index")


class OperatorPayloadRouteSanitizationTests(unittest.TestCase):
    def test_ctrip_read_api_sanitizes_browser_result_payload(self) -> None:
        fake_service = _FakeCtripService()

        with patch("app.api.v1.ctrip_ebooking.get_ctrip_ebooking_service", return_value=fake_service):
            response = TestClient(app).post(
                "/api/v1/ctrip-ebooking/sessions/ctrip-ebk/read-dataset",
                json={"dataset_id": "business_outline", "browser_session_id": "browser-ebk"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("output/playwright", serialized)
        self.assertEqual(payload["evidence"]["screenshot_path"], "artifact_not_returned")
        self.assertIs(payload["evidence"]["profile_path_returned"], False)
        self.assertIs(payload["evidence"]["password_returned"], False)

    def test_ctrip_read_api_returns_conflict_when_profile_is_locked(self) -> None:
        fake_service = _LockedCtripService()

        with patch("app.api.v1.ctrip_ebooking.get_ctrip_ebooking_service", return_value=fake_service):
            response = TestClient(app).post(
                "/api/v1/ctrip-ebooking/sessions/ctrip-ebk/read-dataset",
                json={"dataset_id": "business_outline", "browser_session_id": "browser-ebk"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("浏览器账号", response.json()["detail"])


class _FakeBrowserResult:
    def model_dump(self, *, mode: str) -> dict[str, object]:
        return {
            "status": "success",
            "summary": "读取成功",
            "platform": "ctrip",
            "platform_account_id": "ctrip-ebk",
            "browser_session_id": "browser-ebk",
            "dataset_id": "business_outline",
            "data": {"captured_at": "2026-06-18T08:05:00+08:00"},
            "evidence": {
                "screenshot_path": "output/playwright/ebk.png",
                "profile_path_returned": False,
                "password_returned": False,
            },
        }


class _FakeCtripService:
    async def read_dataset(self, **kwargs: object) -> _FakeBrowserResult:
        return _FakeBrowserResult()


class _LockedCtripService:
    async def read_dataset(self, **kwargs: object) -> _FakeBrowserResult:
        raise CtripBrowserProfileLockedError("浏览器账号正在被另一个后台任务占用，请稍后重试。")


class PyramidBidDatasetReadApiTests(unittest.TestCase):
    """2026-09-22：金字塔推广计划（计划名=投放时段）并入按需单数据集现读。

    Hermes 侧 read_ebk_datasets 只透传 data 字段，promotions 必须落 data。
    """

    def _post(self, service: object) -> object:
        with patch("app.api.v1.ctrip_ebooking.get_ctrip_ebooking_service", return_value=service):
            return TestClient(app).post(
                "/api/v1/ctrip-ebooking/sessions/ctrip-ebk/read-dataset",
                json={"dataset_id": "pyramid_bid", "browser_session_id": "browser-ebk"},
            )

    def test_ctrip_read_dataset_dispatches_pyramid_bid_to_promotions_flow(self) -> None:
        class _FakePyramidService:
            kwargs: dict[str, object] = {}

            async def read_pyramid_promotions(self, **kwargs: object) -> dict[str, object]:
                type(self).kwargs.update(kwargs)
                return {
                    "status": "success",
                    "summary": "读取到 2 个金字塔推广计划。",
                    "platform_account_id": kwargs["platform_account_id"],
                    "browser_session_id": kwargs["browser_session_id"],
                    "promotions": [
                        {
                            "campaign_name": "周内0-2",
                            "campaign_status": "推广中",
                            "click_bid_cny": 6.5,
                            "today_spend_cny": 45.2,
                            "today_impressions": 1200,
                            "today_clicks": 8,
                        },
                        {
                            "campaign_name": "周末14-16",
                            "campaign_status": "推广中",
                            "click_bid_cny": 5.0,
                            "today_spend_cny": 12.0,
                        },
                    ],
                    "data": {
                        "query_type": "ctrip_pyramid_promotions_read",
                        "captured_at": "2026-09-22T17:00:00+08:00",
                    },
                    "evidence": {"page_state": "list", "row_count": 2, "read_only": True},
                }

        fake_service = _FakePyramidService()
        response = self._post(fake_service)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        promotions = payload["data"]["promotions"]
        self.assertEqual(len(promotions), 2)
        self.assertEqual(promotions[0]["campaign_name"], "周内0-2")
        self.assertEqual(promotions[0]["today_spend_cny"], 45.2)
        self.assertEqual(fake_service.kwargs["platform_account_id"], "ctrip-ebk")
        self.assertEqual(fake_service.kwargs["browser_session_id"], "browser-ebk")

    def test_ctrip_read_dataset_pyramid_bid_human_required_passthrough(self) -> None:
        class _FakeHumanRequiredService:
            async def read_pyramid_promotions(self, **kwargs: object) -> dict[str, object]:
                return {
                    "status": "human_required",
                    "summary": "EBK 需要人工完成登录验证。",
                    "promotions": [],
                    "data": {"captured_at": "2026-09-22T17:00:00+08:00"},
                    "evidence": {"page_state": "login", "read_only": True},
                }

        response = self._post(_FakeHumanRequiredService())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "human_required")
        self.assertEqual(payload["data"].get("promotions"), [])


if __name__ == "__main__":
    unittest.main()
