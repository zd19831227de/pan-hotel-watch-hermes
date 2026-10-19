from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
import yaml
from fastapi.testclient import TestClient

BOOTSTRAP_SUPERVISOR_API_KEY = "task-v5-step1-bootstrap-supervisor-key"


class LlmModelConfigTests(unittest.TestCase):
    """V5 Step 1 — multi-model config list, fallback chain, health check."""

    def test_model_config_crud_persists_and_masks_api_key(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())

            listed = client.get("/api/v1/config/integrations/llm/model-configs")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["models"], [])

            created = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "primary-deepseek-secret-key",
                    "priority": 0,
                    "enabled": True,
                },
            )
            self.assertEqual(created.status_code, 201)
            model = created.json()["model"]
            self.assertEqual(model["provider"], "deepseek")
            self.assertEqual(model["model_name"], "deepseek-v4-flash")
            self.assertEqual(model["priority"], 0)
            self.assertTrue(model["enabled"])
            self.assertTrue(model["api_key_configured"])
            self.assertEqual(model["api_key_masked"], "prim...-key")
            self.assertNotIn("primary-deepseek-secret-key", created.text)

            model_id = model["id"]
            updated = client.put(
                f"/api/v1/config/integrations/llm/model-configs/{model_id}",
                json={"priority": 5, "enabled": False},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["model"]["priority"], 5)
            self.assertFalse(updated.json()["model"]["enabled"])

            deleted = client.delete(
                f"/api/v1/config/integrations/llm/model-configs/{model_id}"
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.json()["status"], "deleted")

            after_delete = client.get(
                "/api/v1/config/integrations/llm/model-configs"
            )
            self.assertEqual(after_delete.json()["models"], [])

    def test_model_config_endpoints_require_supervisor_key(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            anonymous = TestClient(app)

            denied = anonymous.get(
                "/api/v1/config/integrations/llm/model-configs"
            )
            self.assertEqual(denied.status_code, 401)

    def test_model_catalog_returns_not_found_for_stale_model_config_id(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())

            response = client.post(
                "/api/v1/config/integrations/llm/models",
                json={"model_config_id": 999999},
            )

            self.assertEqual(response.status_code, 404)

    def test_connection_test_returns_not_found_for_stale_model_config_id(
        self,
    ) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())

            response = client.post(
                "/api/v1/config/integrations/llm/test",
                json={"model_config_id": 999999},
            )

            self.assertEqual(response.status_code, 404)

    def test_model_config_validates_every_base_url_against_ssrf_allowlist(
        self,
    ) -> None:
        settings_module = importlib.import_module("app.core.settings")
        with (
            _isolated_sqlite_database() as db_url,
            mock.patch.dict(os.environ, {"LLM_ALLOWED_HOSTS": ""}, clear=False),
        ):
            settings_module.get_settings.cache_clear()
            try:
                _reload_db_session(db_url)
                client = _supervisor_client(_reload_app())

                rejected = client.post(
                    "/api/v1/config/integrations/llm/model-configs",
                    json={
                        "provider": "relay",
                        "model_name": "relay-model",
                        "base_url": "https://internal-relay.intranet/v1",
                        "api_key": "some-key",
                        "priority": 0,
                        "enabled": True,
                    },
                )
                self.assertEqual(rejected.status_code, 422)
                self.assertNotIn("some-key", rejected.text)
            finally:
                settings_module.get_settings.cache_clear()

    def test_model_config_rejects_query_credentials_in_base_url(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            response = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com?api_key=url-secret",
                    "api_key": "header-secret",
                    "priority": 0,
                    "enabled": True,
                },
            )

            self.assertEqual(response.status_code, 422)
            self.assertNotIn("url-secret", response.text)
            self.assertNotIn("header-secret", response.text)

    def test_model_catalog_can_reuse_saved_model_credentials(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            created = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "saved-model-catalog-secret",
                    "priority": 0,
                    "enabled": True,
                },
            )
            model_id = created.json()["model"]["id"]
            config_service = importlib.import_module("app.domain.config.service")

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(str(request.url), "https://api.deepseek.com/models")
                self.assertEqual(
                    request.headers["Authorization"],
                    "Bearer saved-model-catalog-secret",
                )
                return httpx.Response(
                    200,
                    json={"data": [{"id": "deepseek-v4-flash"}]},
                )

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            original = config_service.httpx.AsyncClient
            config_service.httpx.AsyncClient = (
                lambda *args, **kwargs: real_async_client(transport=transport)
            )
            try:
                response = client.post(
                    "/api/v1/config/integrations/llm/models",
                    json={"model_config_id": model_id},
                )
            finally:
                config_service.httpx.AsyncClient = original

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            self.assertEqual(response.json()["models"], ["deepseek-v4-flash"])
            self.assertNotIn("saved-model-catalog-secret", response.text)

    def test_connection_test_can_reuse_saved_model_credentials(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            created = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "saved-model-test-secret",
                    "priority": 0,
                    "enabled": True,
                },
            )
            model_id = created.json()["model"]["id"]
            config_service = importlib.import_module("app.domain.config.service")

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(
                    str(request.url),
                    "https://api.deepseek.com/chat/completions",
                )
                self.assertEqual(
                    request.headers["Authorization"],
                    "Bearer saved-model-test-secret",
                )
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "ok"}}]},
                )

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            original = config_service.httpx.AsyncClient
            config_service.httpx.AsyncClient = (
                lambda *args, **kwargs: real_async_client(transport=transport)
            )
            try:
                response = client.post(
                    "/api/v1/config/integrations/llm/test",
                    json={"model_config_id": model_id},
                )
            finally:
                config_service.httpx.AsyncClient = original

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            self.assertNotIn("saved-model-test-secret", response.text)

    def test_connection_test_never_reuses_saved_key_for_a_different_endpoint(
        self,
    ) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            created = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "saved-test-key-must-not-leak",
                    "priority": 0,
                    "enabled": True,
                },
            )
            model_id = created.json()["model"]["id"]
            response = client.post(
                "/api/v1/config/integrations/llm/test",
                json={
                    "model_config_id": model_id,
                    "llm_provider": "openai",
                    "llm_base_url": "https://api.openai.com/v1",
                    "llm_model": "gpt-4.1-mini",
                    "llm_api_key": "",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "config_required")
            self.assertFalse(response.json()["evidence"]["request_sent"])
            self.assertNotIn("saved-test-key-must-not-leak", response.text)

    def test_connection_test_redacts_a_key_echoed_by_the_provider(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            config_service = importlib.import_module("app.domain.config.service")
            secret = "provider-echoed-secret-key"

            async def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": secret}}]},
                )

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            with mock.patch.object(
                config_service.httpx,
                "AsyncClient",
                side_effect=lambda *args, **kwargs: real_async_client(
                    transport=transport
                ),
            ):
                response = client.post(
                    "/api/v1/config/integrations/llm/test",
                    json={
                        "llm_provider": "deepseek",
                        "llm_base_url": "https://api.deepseek.com",
                        "llm_model": "deepseek-v4-flash",
                        "llm_api_key": secret,
                    },
                )

            self.assertEqual(response.status_code, 200)
            self.assertNotIn(secret, response.text)
            self.assertNotIn("response_preview", response.json()["evidence"])

    def test_model_catalog_does_not_expose_http_exception_messages(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            config_service = importlib.import_module("app.domain.config.service")
            secret = "exception-contained-secret-key"

            with mock.patch.object(
                config_service.httpx,
                "AsyncClient",
                side_effect=httpx.ConnectError(f"failed with {secret}"),
            ):
                response = client.post(
                    "/api/v1/config/integrations/llm/models",
                    json={
                        "llm_provider": "deepseek",
                        "llm_base_url": "https://api.deepseek.com",
                        "llm_model": "deepseek-v4-flash",
                        "llm_api_key": secret,
                    },
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "failed")
            self.assertNotIn(secret, response.text)
            self.assertNotIn("error", response.json()["evidence"])

    def test_unsaved_provider_form_never_reuses_key_for_a_different_endpoint(
        self,
    ) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            saved = client.post(
                "/api/v1/config/integrations",
                json={
                    "llm_provider": "deepseek",
                    "llm_base_url": "https://api.deepseek.com",
                    "llm_model": "deepseek-v4-flash",
                    "llm_api_key": "must-not-leak-to-openai",
                },
            )
            self.assertEqual(saved.status_code, 200)

            response = client.post(
                "/api/v1/config/integrations/llm/models",
                json={
                    "llm_provider": "openai",
                    "llm_base_url": "https://api.openai.com/v1",
                    "llm_model": "gpt-4.1-mini",
                    "llm_api_key": "",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "config_required")
            self.assertFalse(response.json()["evidence"]["request_sent"])
            self.assertNotIn("must-not-leak-to-openai", response.text)

    def test_saved_model_id_never_reuses_key_for_a_different_endpoint(
        self,
    ) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            created = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "saved-key-must-not-leak",
                    "priority": 0,
                    "enabled": True,
                },
            )
            model_id = created.json()["model"]["id"]
            config_service = importlib.import_module("app.domain.config.service")

            async def handler(request: httpx.Request) -> httpx.Response:
                self.fail(f"unexpected request to {request.url}")

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            original = config_service.httpx.AsyncClient
            config_service.httpx.AsyncClient = (
                lambda *args, **kwargs: real_async_client(transport=transport)
            )
            try:
                response = client.post(
                    "/api/v1/config/integrations/llm/models",
                    json={
                        "model_config_id": model_id,
                        "llm_provider": "openai",
                        "llm_base_url": "https://api.openai.com/v1",
                        "llm_model": "gpt-4.1-mini",
                        "llm_api_key": "",
                    },
                )
            finally:
                config_service.httpx.AsyncClient = original

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "config_required")
            self.assertFalse(response.json()["evidence"]["request_sent"])
            self.assertNotIn("saved-key-must-not-leak", response.text)

    def test_model_edit_requires_a_new_key_when_endpoint_changes(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            created = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "saved-edit-key-must-not-leak",
                    "priority": 0,
                    "enabled": True,
                },
            )
            model_id = created.json()["model"]["id"]

            updated = client.put(
                f"/api/v1/config/integrations/llm/model-configs/{model_id}",
                json={
                    "provider": "openai",
                    "model_name": "gpt-4.1-mini",
                    "base_url": "https://api.openai.com/v1",
                },
            )

            self.assertEqual(updated.status_code, 422)
            self.assertNotIn("saved-edit-key-must-not-leak", updated.text)
            listed = client.get(
                "/api/v1/config/integrations/llm/model-configs"
            ).json()["models"]
            self.assertEqual(listed[0]["provider"], "deepseek")
            self.assertEqual(listed[0]["base_url"], "https://api.deepseek.com")

    def test_model_library_syncs_enabled_chain_to_all_enabled_hermes_profiles(
        self,
    ) -> None:
        with (
            _isolated_sqlite_database() as db_url,
            tempfile.TemporaryDirectory() as hermes_temp,
            mock.patch.dict(
                os.environ,
                {"PAN_HOTEL_HERMES_BASE_PATH": hermes_temp},
                clear=False,
            ),
        ):
            _reload_db_session(db_url)
            config_repository = importlib.import_module(
                "app.domain.config.repository"
            )
            for profile_key in ("jingli-ai", "manxin-ai"):
                asyncio.run(
                    config_repository.create_feishu_agent_profile(
                        profile_key=profile_key,
                        display_name=profile_key,
                        enabled=True,
                        credential_configured=True,
                        app_id_fingerprint="configured",
                    )
                )
                profile_root = Path(hermes_temp) / "profiles" / profile_key
                profile_root.mkdir(parents=True)
                initial_model: object = (
                    "legacy/model"
                    if profile_key == "jingli-ai"
                    else {"default": "legacy-model", "provider": "legacy"}
                )
                (profile_root / "config.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "model": initial_model,
                            "display": {"language": "zh"},
                        },
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                (profile_root / ".env").write_text(
                    "PRESERVE_ME=yes\n",
                    encoding="utf-8",
                )

            client = _supervisor_client(_reload_app())
            first = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "primary-hermes-secret",
                    "priority": 0,
                    "enabled": True,
                },
            )
            second = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-pro",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "fallback-hermes-secret",
                    "priority": 1,
                    "enabled": True,
                },
            )

            self.assertEqual(first.status_code, 201)
            self.assertEqual(second.status_code, 201)
            sync = second.json()["hermes_sync"]
            self.assertEqual(sync["status"], "synced")
            self.assertEqual(sync["profile_keys"], ["jingli-ai", "manxin-ai"])
            self.assertNotIn("primary-hermes-secret", second.text)
            self.assertNotIn("fallback-hermes-secret", second.text)

            for profile_key in ("jingli-ai", "manxin-ai"):
                profile_root = Path(hermes_temp) / "profiles" / profile_key
                config = yaml.safe_load(
                    (profile_root / "config.yaml").read_text(encoding="utf-8")
                )
                self.assertEqual(config["display"], {"language": "zh"})
                self.assertEqual(config["model"]["default"], "deepseek-v4-flash")
                self.assertEqual(
                    config["model"]["provider"],
                    f"custom:pan-hotel-llm-{first.json()['model']['id']}",
                )
                self.assertEqual(
                    config["fallback_providers"],
                    [
                        {
                            "provider": (
                                f"custom:pan-hotel-llm-"
                                f"{second.json()['model']['id']}"
                            ),
                            "model": "deepseek-v4-pro",
                        }
                    ],
                )
                providers = config["providers"]
                self.assertEqual(len(providers), 2)
                self.assertEqual(
                    providers[f"pan-hotel-llm-{first.json()['model']['id']}"][
                        "api"
                    ],
                    "https://api.deepseek.com",
                )
                env_text = (profile_root / ".env").read_text(encoding="utf-8")
                self.assertIn("PRESERVE_ME=yes", env_text)
                self.assertIn("primary-hermes-secret", env_text)
                self.assertIn("fallback-hermes-secret", env_text)

            disabled = client.put(
                (
                    "/api/v1/config/integrations/llm/model-configs/"
                    f"{first.json()['model']['id']}"
                ),
                json={"enabled": False},
            )
            self.assertEqual(disabled.status_code, 200)
            config = yaml.safe_load(
                (
                    Path(hermes_temp)
                    / "profiles"
                    / "jingli-ai"
                    / "config.yaml"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(config["model"]["default"], "deepseek-v4-pro")
            self.assertEqual(config["fallback_providers"], [])

    def test_hermes_config_is_not_switched_when_credential_write_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as hermes_temp,
            mock.patch.dict(
                os.environ,
                {"PAN_HOTEL_HERMES_BASE_PATH": hermes_temp},
                clear=False,
            ),
        ):
            profile_root = Path(hermes_temp) / "profiles" / "jingli-ai"
            profile_root.mkdir(parents=True)
            config_path = profile_root / "config.yaml"
            config_path.write_text(
                "model: legacy/model\ndisplay:\n  language: zh\n",
                encoding="utf-8",
            )
            profile_root.joinpath(".env").write_text("PRESERVE_ME=yes\n", encoding="utf-8")
            record = mock.Mock(
                id=7,
                provider="deepseek",
                model_name="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                api_key="secret-that-cannot-be-written",
            )
            config_service = importlib.import_module("app.domain.config.service")

            with mock.patch.object(
                config_service,
                "set_key",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(OSError):
                    config_service._sync_hermes_profile_llm_config(
                        profile_root,
                        [record],
                    )

            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                "model: legacy/model\ndisplay:\n  language: zh\n",
            )

    def test_empty_model_chain_clears_only_pan_hotel_hermes_settings(self) -> None:
        with (
            tempfile.TemporaryDirectory() as hermes_temp,
            mock.patch.dict(
                os.environ,
                {"PAN_HOTEL_HERMES_BASE_PATH": hermes_temp},
                clear=False,
            ),
        ):
            profile_root = Path(hermes_temp) / "profiles" / "jingli-ai"
            profile_root.mkdir(parents=True)
            config_path = profile_root / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "model": {
                            "default": "old-managed-model",
                            "provider": "custom:pan-hotel-llm-7",
                            "base_url": "https://old.example.com",
                        },
                        "providers": {
                            "pan-hotel-llm-7": {"name": "managed"},
                            "operator-provider": {"name": "preserve"},
                        },
                        "fallback_providers": [
                            {
                                "provider": "custom:pan-hotel-llm-8",
                                "model": "old-fallback",
                            }
                        ],
                        "display": {"language": "zh"},
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            env_path = profile_root / ".env"
            env_path.write_text(
                "PRESERVE_ME=yes\nPAN_HOTEL_LLM_MODEL_7_API_KEY=remove-me\n",
                encoding="utf-8",
            )
            config_service = importlib.import_module("app.domain.config.service")

            config_service._sync_hermes_profile_llm_config(profile_root, [])

            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                config["model"],
                {"default": "", "provider": "auto"},
            )
            self.assertEqual(
                config["providers"],
                {"operator-provider": {"name": "preserve"}},
            )
            self.assertEqual(config["fallback_providers"], [])
            self.assertEqual(config["display"], {"language": "zh"})
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("PRESERVE_ME=yes", env_text)
            self.assertNotIn("PAN_HOTEL_LLM_MODEL_7_API_KEY", env_text)

    def test_hermes_sync_skips_enabled_models_without_api_keys(self) -> None:
        with (
            _isolated_sqlite_database() as db_url,
            tempfile.TemporaryDirectory() as hermes_temp,
            mock.patch.dict(
                os.environ,
                {"PAN_HOTEL_HERMES_BASE_PATH": hermes_temp},
                clear=False,
            ),
        ):
            _reload_db_session(db_url)
            config_repository = importlib.import_module(
                "app.domain.config.repository"
            )
            asyncio.run(
                config_repository.create_feishu_agent_profile(
                    profile_key="jingli-ai",
                    display_name="jingli-ai",
                    enabled=True,
                    credential_configured=True,
                    app_id_fingerprint="configured",
                )
            )
            profile_root = Path(hermes_temp) / "profiles" / "jingli-ai"
            profile_root.mkdir(parents=True)
            (profile_root / "config.yaml").write_text(
                "model:\n  default: legacy\n  provider: legacy\n",
                encoding="utf-8",
            )
            (profile_root / ".env").write_text("PRESERVE_ME=yes\n", encoding="utf-8")
            client = _supervisor_client(_reload_app())

            usable = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "usable-hermes-key",
                    "priority": 1,
                    "enabled": True,
                },
            )
            unusable = client.post(
                "/api/v1/config/integrations/llm/model-configs",
                json={
                    "provider": "openai",
                    "model_name": "gpt-without-key",
                    "base_url": "https://api.openai.com/v1",
                    "priority": 0,
                    "enabled": True,
                },
            )

            self.assertEqual(unusable.status_code, 201)
            self.assertEqual(
                unusable.json()["hermes_sync"]["primary_model"],
                "deepseek-v4-flash",
            )
            self.assertEqual(unusable.json()["hermes_sync"]["fallback_count"], 0)
            config = yaml.safe_load(
                (profile_root / "config.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(config["model"]["default"], "deepseek-v4-flash")
            self.assertEqual(
                set(config["providers"]),
                {f"pan-hotel-llm-{usable.json()['model']['id']}"},
            )

    def test_hermes_sync_excludes_models_rejected_by_runtime_dns_safety(self) -> None:
        with (
            _isolated_sqlite_database() as db_url,
            tempfile.TemporaryDirectory() as hermes_temp,
            mock.patch.dict(
                os.environ,
                {"PAN_HOTEL_HERMES_BASE_PATH": hermes_temp},
                clear=False,
            ),
        ):
            _reload_db_session(db_url)
            config_repository = importlib.import_module(
                "app.domain.config.repository"
            )
            config_service = importlib.import_module("app.domain.config.service")
            asyncio.run(
                config_repository.create_feishu_agent_profile(
                    profile_key="jingli-ai",
                    display_name="jingli-ai",
                    enabled=True,
                    credential_configured=True,
                    app_id_fingerprint="configured",
                )
            )
            profile_root = Path(hermes_temp) / "profiles" / "jingli-ai"
            profile_root.mkdir(parents=True)
            (profile_root / "config.yaml").write_text(
                "model:\n  default: legacy\n  provider: legacy\n",
                encoding="utf-8",
            )
            (profile_root / ".env").write_text("PRESERVE_ME=yes\n", encoding="utf-8")
            record = asyncio.run(
                config_repository.create_persisted_llm_model_config(
                    provider="deepseek",
                    model_name="unsafe-runtime-model",
                    base_url="https://api.deepseek.com",
                    api_key="must-not-sync",
                    priority=0,
                    enabled=True,
                )
            )
            runtime = config_service.RuntimeIntegrationConfig()

            with mock.patch(
                "socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("169.254.169.254", 443))],
            ):
                chain = asyncio.run(runtime.llm_runtime_chain())
                sync = asyncio.run(
                    runtime.sync_hermes_model_configs(records=[record])
                )

            self.assertEqual(chain, [])
            self.assertEqual(sync.status, "synced")
            self.assertIsNone(sync.primary_model)
            config = yaml.safe_load(
                (profile_root / "config.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(config["model"], {"default": "", "provider": "auto"})
            self.assertEqual(config["providers"], {})
            self.assertNotIn(
                "PAN_HOTEL_LLM_MODEL_",
                (profile_root / ".env").read_text(encoding="utf-8"),
            )

    def test_hermes_sync_rolls_back_all_profiles_when_one_profile_fails(self) -> None:
        with (
            _isolated_sqlite_database() as db_url,
            tempfile.TemporaryDirectory() as hermes_temp,
            mock.patch.dict(
                os.environ,
                {"PAN_HOTEL_HERMES_BASE_PATH": hermes_temp},
                clear=False,
            ),
        ):
            _reload_db_session(db_url)
            config_repository = importlib.import_module(
                "app.domain.config.repository"
            )
            config_service = importlib.import_module("app.domain.config.service")
            for profile_key in ("jingli-ai", "manxin-ai"):
                asyncio.run(
                    config_repository.create_feishu_agent_profile(
                        profile_key=profile_key,
                        display_name=profile_key,
                        enabled=True,
                        credential_configured=True,
                        app_id_fingerprint="configured",
                    )
                )
                profile_root = Path(hermes_temp) / "profiles" / profile_key
                profile_root.mkdir(parents=True)
                (profile_root / "config.yaml").write_text(
                    f"model:\n  default: {profile_key}-legacy\n  provider: legacy\n",
                    encoding="utf-8",
                )
                (profile_root / ".env").write_text(
                    f"PRESERVE_PROFILE={profile_key}\n",
                    encoding="utf-8",
                )

            record = asyncio.run(
                config_repository.create_persisted_llm_model_config(
                    provider="deepseek",
                    model_name="deepseek-v4-flash",
                    base_url="https://api.deepseek.com",
                    api_key="rollback-hermes-secret",
                    priority=0,
                    enabled=True,
                )
            )
            originals = {
                profile_key: {
                    filename: (
                        Path(hermes_temp)
                        / "profiles"
                        / profile_key
                        / filename
                    ).read_bytes()
                    for filename in ("config.yaml", ".env")
                }
                for profile_key in ("jingli-ai", "manxin-ai")
            }
            real_sync = config_service._sync_hermes_profile_llm_config

            def fail_second_profile(profile_root: Path, records: list[object]) -> None:
                if profile_root.name == "manxin-ai":
                    raise OSError("disk full")
                real_sync(profile_root, records)

            runtime = config_service.RuntimeIntegrationConfig()
            with mock.patch.object(
                config_service,
                "_sync_hermes_profile_llm_config",
                side_effect=fail_second_profile,
            ):
                result = asyncio.run(
                    runtime.sync_hermes_model_configs(records=[record])
                )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.profile_keys, [])
            for profile_key in ("jingli-ai", "manxin-ai"):
                profile_root = Path(hermes_temp) / "profiles" / profile_key
                for filename in ("config.yaml", ".env"):
                    self.assertEqual(
                        (profile_root / filename).read_bytes(),
                        originals[profile_key][filename],
                    )

    def test_model_config_reorder_by_ordered_ids(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())

            ids: list[int] = []
            for index, provider in enumerate(("deepseek", "glm", "openai")):
                response = client.post(
                    "/api/v1/config/integrations/llm/model-configs",
                    json={
                        "provider": provider,
                        "model_name": f"{provider}-model",
                        "base_url": "https://api.deepseek.com",
                        "api_key": f"key-{provider}",
                        "priority": index,
                        "enabled": True,
                    },
                )
                self.assertEqual(response.status_code, 201)
                ids.append(response.json()["model"]["id"])

            reordered = ids[::-1]
            reorder_response = client.post(
                "/api/v1/config/integrations/llm/model-configs/reorder",
                json={"ordered_ids": reordered},
            )
            self.assertEqual(reorder_response.status_code, 200)
            priorities = [
                m["priority"] for m in reorder_response.json()["models"]
            ]
            self.assertEqual(priorities, [0, 1, 2])
            returned_ids = [m["id"] for m in reorder_response.json()["models"]]
            self.assertEqual(returned_ids, reordered)

            listed = client.get(
                "/api/v1/config/integrations/llm/model-configs"
            )
            self.assertEqual(
                [m["id"] for m in listed.json()["models"]], reordered
            )

    def test_health_check_marks_each_enabled_model_without_exposing_key(
        self,
    ) -> None:
        with (
            _isolated_sqlite_database() as db_url,
            mock.patch.dict(
                os.environ,
                {"LLM_ALLOWED_HOSTS": "api.deepseek.com,glm.example.test"},
                clear=False,
            ),
        ):
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)

            configs = {
                "deepseek": "https://api.deepseek.com",
                "glm": "https://glm.example.test",
            }
            for provider, base_url in configs.items():
                client.post(
                    "/api/v1/config/integrations/llm/model-configs",
                    json={
                        "provider": provider,
                        "model_name": f"{provider}-model",
                        "base_url": base_url,
                        "api_key": f"health-secret-{provider}",
                        "priority": 0,
                        "enabled": True,
                    },
                )

            config_service = importlib.import_module("app.domain.config.service")

            async def handler(request: httpx.Request) -> httpx.Response:
                # glm fails, deepseek succeeds
                if "glm.example.test" in str(request.url):
                    return httpx.Response(500, text="upstream boom")
                return httpx.Response(
                    200, json={"choices": [{"message": {"content": "ok"}}]}
                )

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            original = config_service.httpx.AsyncClient
            config_service.httpx.AsyncClient = (
                lambda *args, **kwargs: real_async_client(transport=transport)
            )
            try:
                response = client.post(
                    "/api/v1/config/integrations/llm/model-configs/health"
                )
            finally:
                config_service.httpx.AsyncClient = original

            self.assertEqual(response.status_code, 200)
            by_provider = {
                m["provider"]: m for m in response.json()["models"]
            }
            self.assertEqual(by_provider["deepseek"]["last_health_status"], "ok")
            self.assertEqual(by_provider["glm"]["last_health_status"], "fail")
            self.assertNotIn("health-secret-deepseek", response.text)
            self.assertNotIn("health-secret-glm", response.text)

    def test_llm_chat_falls_back_to_next_model_when_primary_times_out(self) -> None:
        llm_module = importlib.import_module("app.domain.ai_agent.llm")
        config_service = importlib.import_module("app.domain.config.service")

        chain = [
            config_service.LlmRuntimeConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
                api_key="primary-secret-key",
            ),
            config_service.LlmRuntimeConfig(
                provider="glm",
                base_url="https://glm.example.test",
                model="glm-model",
                api_key="fallback-secret-key",
            ),
        ]

        call_log: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            call_log.append(str(request.url))
            if "api.deepseek.com" in str(request.url):
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "fallback reply"}}
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient
        original = llm_module.httpx.AsyncClient
        llm_module.httpx.AsyncClient = (
            lambda *args, **kwargs: real_async_client(transport=transport)
        )
        try:
            response = asyncio.run(
                llm_module.chat_with_fallback_chain(
                    chain,
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[],
                )
            )
        finally:
            llm_module.httpx.AsyncClient = original

        self.assertEqual(response.content, "fallback reply")
        self.assertEqual(
            call_log,
            [
                "https://api.deepseek.com/chat/completions",
                "https://glm.example.test/chat/completions",
            ],
        )

    def test_llm_chat_all_models_fail_raises_clear_error_without_key(self) -> None:
        llm_module = importlib.import_module("app.domain.ai_agent.llm")
        config_service = importlib.import_module("app.domain.config.service")

        chain = [
            config_service.LlmRuntimeConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
                api_key="primary-secret-key",
            ),
            config_service.LlmRuntimeConfig(
                provider="glm",
                base_url="https://glm.example.test",
                model="glm-model",
                api_key="fallback-secret-key",
            ),
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient
        original = llm_module.httpx.AsyncClient
        llm_module.httpx.AsyncClient = (
            lambda *args, **kwargs: real_async_client(transport=transport)
        )
        try:
            with self.assertRaises(llm_module.AgentLlmConfigError) as ctx:
                asyncio.run(
                    llm_module.chat_with_fallback_chain(
                        chain,
                        messages=[{"role": "user", "content": "hi"}],
                        tools=[],
                    )
                )
        finally:
            llm_module.httpx.AsyncClient = original

        message = str(ctx.exception)
        self.assertIn("不可用", message)
        self.assertNotIn("primary-secret-key", message)
        self.assertNotIn("fallback-secret-key", message)

    def test_llm_chat_fallback_logs_do_not_leak_api_key(self) -> None:
        llm_module = importlib.import_module("app.domain.ai_agent.llm")
        config_service = importlib.import_module("app.domain.config.service")

        secret_primary = "leak-guard-primary-secret"
        secret_fallback = "leak-guard-fallback-secret"
        chain = [
            config_service.LlmRuntimeConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
                api_key=secret_primary,
            ),
            config_service.LlmRuntimeConfig(
                provider="glm",
                base_url="https://glm.example.test",
                model="glm-model",
                api_key=secret_fallback,
            ),
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            if "api.deepseek.com" in str(request.url):
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient
        original = llm_module.httpx.AsyncClient
        llm_module.httpx.AsyncClient = (
            lambda *args, **kwargs: real_async_client(transport=transport)
        )

        records: list[logging.LogRecord] = []
        logger = logging.getLogger("app.domain.ai_agent.llm")
        handler_attach = logging.Handler()
        handler_attach.emit = records.append  # type: ignore[assignment]
        logger.addHandler(handler_attach)
        try:
            asyncio.run(
                llm_module.chat_with_fallback_chain(
                    chain,
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[],
                )
            )
        finally:
            logger.removeHandler(handler_attach)
            llm_module.httpx.AsyncClient = original

        rendered = "\n".join(r.getMessage() for r in records)
        self.assertNotIn(secret_primary, rendered)
        self.assertNotIn(secret_fallback, rendered)
        # A fallback warning must have been logged for the failed primary.
        fallback_records = [r for r in records if "fallback" in r.getMessage().lower()]
        self.assertTrue(fallback_records)

    def test_llm_chat_does_not_bypass_an_empty_model_library(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)
            client.post(
                "/api/v1/config/integrations",
                json={"llm_api_key": "legacy-single-primary-key"},
            )

            llm_module = importlib.import_module("app.domain.ai_agent.llm")
            request_sent = False

            async def handler(request: httpx.Request) -> httpx.Response:
                nonlocal request_sent
                request_sent = True
                return httpx.Response(
                    200, json={"choices": [{"message": {"content": "legacy ok"}}]}
                )

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            original = llm_module.httpx.AsyncClient
            llm_module.httpx.AsyncClient = (
                lambda *args, **kwargs: real_async_client(transport=transport)
            )
            try:
                with self.assertRaises(llm_module.AgentLlmConfigError):
                    asyncio.run(_call_client_chat(llm_module))
            finally:
                llm_module.httpx.AsyncClient = original

            self.assertFalse(request_sent)

    def test_llm_runtime_chain_returns_enabled_models_ordered_by_priority(
        self,
    ) -> None:
        with (
            _isolated_sqlite_database() as db_url,
            mock.patch.dict(
                os.environ,
                {"LLM_ALLOWED_HOSTS": "api.deepseek.com,glm.example.test"},
                clear=False,
            ),
            mock.patch(
                "socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
            ),
        ):
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())
            for provider, priority, enabled in (
                ("glm", 2, True),
                ("deepseek", 0, True),
                ("disabled-one", 1, False),
            ):
                client.post(
                    "/api/v1/config/integrations/llm/model-configs",
                    json={
                        "provider": provider,
                        "model_name": f"{provider}-model",
                        "base_url": (
                            "https://glm.example.test"
                            if provider == "glm"
                            else "https://api.deepseek.com"
                        ),
                        "api_key": f"key-{provider}",
                        "priority": priority,
                        "enabled": enabled,
                    },
                )

            config_service = importlib.import_module("app.domain.config.service")
            chain = asyncio.run(config_service.get_runtime_config().llm_runtime_chain())

            providers = [entry.provider for entry in chain]
            self.assertEqual(providers, ["deepseek", "glm"])
            self.assertNotIn("disabled-one", providers)


async def _call_client_chat(llm_module):
    client = llm_module.OpenAiCompatibleAgentLlmClient()
    return await client.chat(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watch-llm-model-test.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        self._previous_llm_key = os.environ.get("LLM_API_KEY")
        self._previous_bootstrap_key = os.environ.get(
            "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"
        )
        self._previous_startup_health = os.environ.get(
            "LLM_STARTUP_HEALTH_CHECK_ENABLED"
        )
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        os.environ[
            "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"
        ] = BOOTSTRAP_SUPERVISOR_API_KEY
        # Disable the startup background health probe so it cannot race the
        # explicit, mocked probe these tests run.
        os.environ["LLM_STARTUP_HEALTH_CHECK_ENABLED"] = "false"
        os.environ.pop("LLM_API_KEY", None)
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        if self._previous_llm_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = self._previous_llm_key
        if self._previous_bootstrap_key is None:
            os.environ.pop("PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY", None)
        else:
            os.environ[
                "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"
            ] = self._previous_bootstrap_key
        if self._previous_startup_health is None:
            os.environ.pop("LLM_STARTUP_HEALTH_CHECK_ENABLED", None)
        else:
            os.environ[
                "LLM_STARTUP_HEALTH_CHECK_ENABLED"
            ] = self._previous_startup_health
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db_session(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    config_service = importlib.import_module("app.domain.config.service")
    config_service.reset_runtime_config()
    return db_session


def _reload_app():
    main = importlib.import_module("app.main")
    return importlib.reload(main).create_app()


def _supervisor_client(app) -> TestClient:
    client = TestClient(app)
    client.headers["X-Pan-Hotel-API-Key"] = BOOTSTRAP_SUPERVISOR_API_KEY
    return client


# Keep an alias so unused `json` import does not trip strict linters during
# incremental edits; the import is used by other test cases that grow later.
_ = json

if __name__ == "__main__":
    unittest.main()
