from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from app.core.settings import get_settings


class FeishuAppCredentialResolverTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def _with_env(self, values: dict[str, str]):
        keys = {
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            "FEISHU_APP_CREDENTIALS_JSON",
            "FEISHU_CHAT_APP_MAP_JSON",
            "FEISHU_STRICT_TENANT_ROUTING",
        }
        merged = {key: "" for key in keys}
        merged["FEISHU_STRICT_TENANT_ROUTING"] = "false"
        merged.update(values)
        return mock.patch.dict(os.environ, merged, clear=False)

    def test_resolves_named_app_from_chat_id(self) -> None:
        with self._with_env(
            {
                "FEISHU_APP_ID": "cli_default",
                "FEISHU_APP_SECRET": "default_secret",
                "FEISHU_APP_CREDENTIALS_JSON": json.dumps(
                    {
                        "quanji-ai": {
                            "app_id": "cli_quanji",
                            "app_secret": "quanji_secret",
                        },
                        "jingli-ai": {
                            "app_id": "cli_jingli",
                            "app_secret": "jingli_secret",
                        },
                    }
                ),
                "FEISHU_CHAT_APP_MAP_JSON": json.dumps(
                    {
                        "oc_33333333333333333333333333333333": "quanji-ai",
                        "oc_44444444444444444444444444444444": "jingli-ai",
                    }
                ),
                "FEISHU_STRICT_TENANT_ROUTING": "true",
            }
        ):
            get_settings.cache_clear()
            from app.domain.notifications.feishu_app_credentials import (
                resolve_feishu_app_credentials,
            )

            credentials = resolve_feishu_app_credentials(
                chat_id="oc_33333333333333333333333333333333"
            )

        self.assertEqual(credentials.profile, "quanji-ai")
        self.assertEqual(credentials.app_id, "cli_quanji")
        self.assertEqual(credentials.app_secret, "quanji_secret")

    def test_unknown_chat_falls_back_to_default_when_strict_routing_is_disabled(self) -> None:
        with self._with_env(
            {
                "FEISHU_APP_ID": "cli_default",
                "FEISHU_APP_SECRET": "default_secret",
                "FEISHU_APP_CREDENTIALS_JSON": "{}",
                "FEISHU_CHAT_APP_MAP_JSON": "{}",
                "FEISHU_STRICT_TENANT_ROUTING": "false",
            }
        ):
            get_settings.cache_clear()
            from app.domain.notifications.feishu_app_credentials import (
                resolve_feishu_app_credentials,
            )

            credentials = resolve_feishu_app_credentials(chat_id="oc_unknown")

        self.assertEqual(credentials.profile, "default")
        self.assertEqual(credentials.app_id, "cli_default")
        self.assertEqual(credentials.app_secret, "default_secret")

    def test_unknown_chat_does_not_fall_back_when_strict_routing_is_enabled(self) -> None:
        with self._with_env(
            {
                "FEISHU_APP_ID": "cli_default",
                "FEISHU_APP_SECRET": "default_secret",
                "FEISHU_APP_CREDENTIALS_JSON": "{}",
                "FEISHU_CHAT_APP_MAP_JSON": "{}",
                "FEISHU_STRICT_TENANT_ROUTING": "true",
            }
        ):
            get_settings.cache_clear()
            from app.domain.notifications.feishu_app_credentials import (
                resolve_feishu_app_credentials,
            )

            credentials = resolve_feishu_app_credentials(chat_id="oc_unknown")

        self.assertEqual(credentials.profile, "unmapped")
        self.assertFalse(credentials.configured())
        self.assertEqual(credentials.app_id, "")
        self.assertEqual(credentials.app_secret, "")

    def test_named_profile_missing_credentials_does_not_fall_back_in_strict_mode(self) -> None:
        with self._with_env(
            {
                "FEISHU_APP_ID": "cli_default",
                "FEISHU_APP_SECRET": "default_secret",
                "FEISHU_APP_CREDENTIALS_JSON": "{}",
                "FEISHU_CHAT_APP_MAP_JSON": json.dumps(
                    {"oc_known": "jingli-ai"}
                ),
                "FEISHU_STRICT_TENANT_ROUTING": "true",
            }
        ):
            get_settings.cache_clear()
            from app.domain.notifications.feishu_app_credentials import (
                resolve_feishu_app_credentials,
            )

            credentials = resolve_feishu_app_credentials(chat_id="oc_known")

        self.assertEqual(credentials.profile, "jingli-ai")
        self.assertFalse(credentials.configured())
        self.assertEqual(credentials.app_id, "")
        self.assertEqual(credentials.app_secret, "")

    def test_invalid_chat_mapping_json_fails_closed_in_strict_mode(self) -> None:
        with self._with_env(
            {
                "FEISHU_APP_ID": "cli_default",
                "FEISHU_APP_SECRET": "default_secret",
                "FEISHU_APP_CREDENTIALS_JSON": "{}",
                "FEISHU_CHAT_APP_MAP_JSON": "{invalid-json",
                "FEISHU_STRICT_TENANT_ROUTING": "true",
            }
        ):
            get_settings.cache_clear()
            from app.domain.notifications.feishu_app_credentials import (
                resolve_feishu_app_credentials,
            )

            credentials = resolve_feishu_app_credentials(chat_id="oc_unknown")

        self.assertEqual(credentials.profile, "unmapped")
        self.assertFalse(credentials.configured())

    def test_invalid_named_credentials_json_fails_closed_in_strict_mode(self) -> None:
        with self._with_env(
            {
                "FEISHU_APP_ID": "cli_default",
                "FEISHU_APP_SECRET": "default_secret",
                "FEISHU_APP_CREDENTIALS_JSON": "{invalid-json",
                "FEISHU_CHAT_APP_MAP_JSON": json.dumps(
                    {"oc_known": "jingli-ai"}
                ),
                "FEISHU_STRICT_TENANT_ROUTING": "true",
            }
        ):
            get_settings.cache_clear()
            from app.domain.notifications.feishu_app_credentials import (
                resolve_feishu_app_credentials,
            )

            credentials = resolve_feishu_app_credentials(chat_id="oc_known")

        self.assertEqual(credentials.profile, "jingli-ai")
        self.assertFalse(credentials.configured())

    def test_public_payload_never_includes_secret(self) -> None:
        with self._with_env(
            {
                "FEISHU_APP_ID": "cli_default",
                "FEISHU_APP_SECRET": "default_secret",
            }
        ):
            get_settings.cache_clear()
            from app.domain.notifications.feishu_app_credentials import (
                resolve_feishu_app_credentials,
            )

            payload = resolve_feishu_app_credentials().public_payload()

        self.assertEqual(payload["profile"], "default")
        self.assertEqual(payload["app_id_configured"], True)
        self.assertNotIn("default_secret", repr(payload))


if __name__ == "__main__":
    unittest.main()
