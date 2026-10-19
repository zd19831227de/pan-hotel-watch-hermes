from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.settings import get_settings
from app.domain.config.service import IntegrationConfigUpdate
from app.main import app


class ApiSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            key: os.environ.get(key)
            for key in (
                "ENVIRONMENT",
                "PAN_HOTEL_ENFORCE_API_AUTH",
                "WATCH_API_SHARED_SECRET",
                "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY",
            )
        }
        get_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    def test_feishu_operator_card_requires_service_token_in_production(self) -> None:
        os.environ["ENVIRONMENT"] = "production"
        os.environ["WATCH_API_SHARED_SECRET"] = "service-secret-123456"
        get_settings.cache_clear()

        with mock.patch("app.api.v1.feishu.deliver_operator_text_as_card"):
            response = TestClient(app).post(
                "/api/v1/feishu/operator-card-sends",
                json={"chat_id": "oc_test", "text": "hello"},
            )

        self.assertEqual(response.status_code, 401)

    def test_run_now_requires_operator_api_key_in_production(self) -> None:
        os.environ["ENVIRONMENT"] = "production"
        os.environ["PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"] = "supervisor-secret-123456"
        get_settings.cache_clear()

        response = TestClient(app).post(
            "/api/v1/ctrip-ebooking/competitor-watch-groups/missing/run-now?push_to_feishu=false",
            json={"actor_id": "operator"},
        )

        self.assertEqual(response.status_code, 401)

    def test_run_now_accepts_hermes_service_token_in_production(self) -> None:
        os.environ["ENVIRONMENT"] = "production"
        os.environ["WATCH_API_SHARED_SECRET"] = "service-secret-123456"
        get_settings.cache_clear()

        with mock.patch(
            "app.api.v1.competitor_watch.CompetitorPriceScheduler.run_watch_group_now",
            new=mock.AsyncMock(return_value={"status": "success", "watch_group_key": "test"}),
        ):
            response = TestClient(app).post(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/test/run-now?push_to_feishu=false",
                headers={"X-Pan-Hotel-Service-Token": "service-secret-123456"},
                json={"actor_id": "hermes"},
            )

        self.assertEqual(response.status_code, 200, response.text)

    def test_llm_update_requires_supervisor_api_key_in_production(self) -> None:
        os.environ["ENVIRONMENT"] = "production"
        os.environ["PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"] = "supervisor-secret-123456"
        get_settings.cache_clear()

        response = TestClient(app).post(
            "/api/v1/config/integrations",
            json={"llm_model": "deepseek-v4-flash"},
        )

        self.assertEqual(response.status_code, 401)

    def test_llm_base_url_rejects_local_and_metadata_addresses(self) -> None:
        for base_url in (
            "http://127.0.0.1:8000/v1",
            "http://169.254.169.254/latest/meta-data",
            "http://localhost:8000/v1",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValidationError):
                    IntegrationConfigUpdate(llm_base_url=base_url)

    def test_llm_base_url_accepts_https_public_provider(self) -> None:
        update = IntegrationConfigUpdate(llm_base_url="https://api.deepseek.com/v1")
        self.assertEqual(update.llm_base_url, "https://api.deepseek.com/v1")


if __name__ == "__main__":
    unittest.main()
