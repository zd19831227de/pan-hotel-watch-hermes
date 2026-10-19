from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from tests._feishu_deployment_isolation import isolated_feishu_deployment as _isolated_feishu_deployment

BOOTSTRAP_SUPERVISOR_API_KEY = "task-7a-bootstrap-supervisor-key"


class ConfigApiTests(unittest.TestCase):
    def test_feishu_admission_api_is_supervisor_only_and_reloads_one_profile(self) -> None:
        admission = {
            "profile_key": "jingli-ai",
            "display_name": "静里 AI 管家",
            "groups": [
                {
                    "group_key": "0123456789abcdef",
                    "chat_id_masked": "oc_1...room",
                    "policy": "allowlist",
                    "allowed_users": ["ou_first"],
                    "require_mention": True,
                }
            ],
        }
        process_calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command, **kwargs):
            command = [str(item) for item in command]
            process_calls.append((command, kwargs))
            group = admission["groups"][0]
            if "admission-patch" in command:
                if "--policy" in command:
                    group["policy"] = command[command.index("--policy") + 1]
                if "--require-mention" in command:
                    group["require_mention"] = (
                        command[command.index("--require-mention") + 1] == "true"
                    )
                stdout = json.dumps(admission, ensure_ascii=False)
            elif "admission-add-user" in command:
                group["allowed_users"].append(command[command.index("--open-id") + 1])
                stdout = json.dumps(admission, ensure_ascii=False)
            elif "admission-remove-user" in command:
                group["allowed_users"].remove(command[command.index("--open-id") + 1])
                stdout = json.dumps(admission, ensure_ascii=False)
            elif "admission-get" in command:
                stdout = json.dumps(admission, ensure_ascii=False)
            else:
                stdout = "ok"
            return mock.Mock(returncode=0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "feishu-profiles.yaml"
            manifest_path.write_text("profiles: {}\n", encoding="utf-8")
            hermes_base = temp_root / "hermes"
            (hermes_base / "profiles" / "jingli-ai").mkdir(parents=True)
            hermes_exe = temp_root / "hermes.exe"
            hermes_exe.touch()
            deployment_env = {
                "PAN_HOTEL_FEISHU_PROFILES_MANIFEST": str(manifest_path),
                "PAN_HOTEL_HERMES_BASE_PATH": str(hermes_base),
                "PAN_HOTEL_HERMES_PYTHON_EXE": os.sys.executable,
                "PAN_HOTEL_HERMES_EXE": str(hermes_exe),
            }
            with (
                _isolated_sqlite_database() as db_url,
                mock.patch.dict(os.environ, deployment_env, clear=False),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                _reload_db_session(db_url)
                app = _reload_app()
                anonymous = TestClient(app)
                client = _supervisor_client(app)

                denied = anonymous.get(
                    "/api/v1/config/feishu/profiles/jingli-ai/admission"
                )
                loaded = client.get(
                    "/api/v1/config/feishu/profiles/jingli-ai/admission"
                )
                patched = client.patch(
                    "/api/v1/config/feishu/profiles/jingli-ai/admission",
                    json={
                        "group_key": "0123456789abcdef",
                        "policy": "open",
                        "require_mention": False,
                    },
                )
                added = client.post(
                    "/api/v1/config/feishu/profiles/jingli-ai/admission/users",
                    json={
                        "group_key": "0123456789abcdef",
                        "open_id": "ou_second",
                    },
                )
                removed = client.request(
                    "DELETE",
                    "/api/v1/config/feishu/profiles/jingli-ai/admission/users/ou_first",
                    params={"group_key": "0123456789abcdef"},
                )

            self.assertEqual(denied.status_code, 401)
            self.assertEqual(loaded.status_code, 200)
            self.assertEqual(patched.status_code, 200)
            self.assertEqual(patched.json()["groups"][0]["policy"], "open")
            self.assertFalse(patched.json()["groups"][0]["require_mention"])
            self.assertIn("ou_second", added.json()["groups"][0]["allowed_users"])
            self.assertNotIn("ou_first", removed.json()["groups"][0]["allowed_users"])
            for response in (loaded, patched, added, removed):
                self.assertNotIn("app_secret", response.text)
                self.assertNotIn("secret", response.text)
                self.assertNotIn("oc_1234567890_room", response.text)

            mutation_commands = [
                command
                for command, _ in process_calls
                if any(
                    action in command
                    for action in (
                        "admission-patch",
                        "admission-add-user",
                        "admission-remove-user",
                    )
                )
            ]
            apply_commands = [
                command for command, _ in process_calls if "apply" in command
            ]
            restart_commands = [
                command
                for command, _ in process_calls
                if any(item.endswith("start-hermes-gateways.ps1") for item in command)
            ]
            self.assertEqual(len(mutation_commands), 3)
            self.assertEqual(len(apply_commands), 3)
            self.assertEqual(len(restart_commands), 3)
            for command in restart_commands:
                self.assertIn("-Replace", command)
                self.assertEqual(command[command.index("-Profiles") + 1], "jingli-ai")
            managed_process_calls = [
                (command, kwargs)
                for command, kwargs in process_calls
                if any(
                    item.endswith("feishu_profiles.py")
                    or item.endswith("start-hermes-gateways.ps1")
                    for item in command
                )
            ]
            self.assertTrue(managed_process_calls)
            for _, kwargs in managed_process_calls:
                self.assertFalse(kwargs.get("shell", False))

    def test_feishu_agent_profile_persists_only_non_secret_metadata(self) -> None:
        credentials = {
            "jingli-ai": {
                "app_id": "cli_jingli_sensitive_app_id",
                "app_secret": "jingli-sensitive-app-secret",
            }
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_sqlite_database() as db_url:
                _reload_db_session(db_url)
                client = _supervisor_client(_reload_app())

                created = client.post(
                    "/api/v1/config/feishu-agent-profiles",
                    json={
                        "profile_key": "jingli-ai",
                        "display_name": "静里 AI 管家",
                        "enabled": True,
                    },
                )

                self.assertEqual(created.status_code, 201)
                profile = created.json()["profile"]
                self.assertEqual(profile["profile_key"], "jingli-ai")
                self.assertEqual(profile["display_name"], "静里 AI 管家")
                self.assertTrue(profile["enabled"])
                self.assertTrue(profile["credential_configured"])
                self.assertRegex(profile["app_id_fingerprint"], r"^[0-9a-f]{12}$")
                self.assertNotIn("cli_jingli_sensitive_app_id", created.text)
                self.assertNotIn("jingli-sensitive-app-secret", created.text)

                listed = client.get("/api/v1/config/feishu-agent-profiles")
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(len(listed.json()["profiles"]), 1)
                self.assertNotIn("cli_jingli_sensitive_app_id", listed.text)
                self.assertNotIn("jingli-sensitive-app-secret", listed.text)

                database_path = Path(db_url.removeprefix("sqlite+aiosqlite:///"))
                with closing(sqlite3.connect(database_path)) as connection:
                    columns = {
                        row[1]
                        for row in connection.execute(
                            "PRAGMA table_info(feishu_agent_profiles)"
                        )
                    }
                    stored = connection.execute(
                        "SELECT * FROM feishu_agent_profiles WHERE profile_key = ?",
                        ("jingli-ai",),
                    ).fetchone()
                self.assertNotIn("app_secret", columns)
                self.assertNotIn("app_id", columns)
                self.assertNotIn("jingli-sensitive-app-secret", repr(stored))
                self.assertNotIn("cli_jingli_sensitive_app_id", repr(stored))

    def test_feishu_agent_profile_requires_controlled_credentials_to_enable(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": "{}"},
            clear=False,
        ):
            with _isolated_sqlite_database() as db_url:
                _reload_db_session(db_url)
                client = _supervisor_client(_reload_app())

                rejected = client.post(
                    "/api/v1/config/feishu-agent-profiles",
                    json={
                        "profile_key": "unconfigured-ai",
                        "display_name": "未配置 Agent",
                        "enabled": True,
                    },
                )
                self.assertEqual(rejected.status_code, 422)
                self.assertIn("凭据", rejected.text)

                disabled = client.post(
                    "/api/v1/config/feishu-agent-profiles",
                    json={
                        "profile_key": "unconfigured-ai",
                        "display_name": "未配置 Agent",
                        "enabled": False,
                    },
                )
                self.assertEqual(disabled.status_code, 201)
                self.assertFalse(disabled.json()["profile"]["credential_configured"])

    def test_feishu_chat_binding_is_unique_masked_and_protects_profile_delete(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"},
            "manxin-ai": {"app_id": "cli_manxin", "app_secret": "manxin_secret"},
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment():
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    for profile_key in credentials:
                        response = client.post(
                            "/api/v1/config/feishu-agent-profiles",
                            json={
                                "profile_key": profile_key,
                                "display_name": profile_key,
                                "enabled": True,
                            },
                        )
                        self.assertEqual(response.status_code, 201)

                    raw_chat_id = "oc_1234567890abcdefghijklmnop"
                    created = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": raw_chat_id,
                            "profile_key": "jingli-ai",
                            "display_name": "静里运营群",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(created.status_code, 201)
                    binding = created.json()["binding"]
                    self.assertEqual(binding["profile_key"], "jingli-ai")
                    self.assertNotIn("chat_id", binding)
                    self.assertNotIn(raw_chat_id, created.text)
                    self.assertIn("chat_id_masked", binding)
                    self.assertRegex(binding["chat_id_fingerprint"], r"^[0-9a-f]{12}$")

                    duplicate = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": raw_chat_id,
                            "profile_key": "manxin-ai",
                            "display_name": "错误重复绑定",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(duplicate.status_code, 409)

                    blocked_delete = client.delete(
                        "/api/v1/config/feishu-agent-profiles/jingli-ai"
                    )
                    self.assertEqual(blocked_delete.status_code, 409)
                    self.assertIn("业务群", blocked_delete.text)

    def test_feishu_chat_binding_delete_succeeds_then_returns_not_found(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"}
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment():
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    profile = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(profile.status_code, 201)
                    created = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": "oc_delete_binding_group",
                            "profile_key": "jingli-ai",
                            "display_name": "待删除业务群",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(created.status_code, 201)
                    binding_id = created.json()["binding"]["id"]

                    deleted = client.delete(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}"
                    )
                    self.assertEqual(deleted.status_code, 200)
                    self.assertEqual(deleted.json()["status"], "deleted")
                    self.assertNotIn("oc_delete_binding_group", deleted.text)

                    missing = client.delete(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}"
                    )
                    self.assertEqual(missing.status_code, 404)

    def test_chat_binding_lifecycle_syncs_admission_manifest(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"},
            "manxin-ai": {"app_id": "cli_manxin", "app_secret": "manxin_secret"},
        }
        raw_chat_id = "oc_lifecycle_sync_group"
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment() as deployment:
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    for profile_key in credentials:
                        response = client.post(
                            "/api/v1/config/feishu-agent-profiles",
                            json={
                                "profile_key": profile_key,
                                "display_name": profile_key,
                                "enabled": True,
                            },
                        )
                        self.assertEqual(response.status_code, 201)

                    created = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": raw_chat_id,
                            "profile_key": "jingli-ai",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(created.status_code, 201)
                    self.assertTrue(created.json()["binding"]["admission_synced"])
                    self.assertIn(raw_chat_id, deployment.admitted["jingli-ai"])
                    add_commands = [
                        command
                        for command in deployment.process_calls
                        if "admission-add-group" in command
                    ]
                    self.assertEqual(len(add_commands), 1)
                    self.assertIn(raw_chat_id, add_commands[0])
                    restart_commands = [
                        command
                        for command in deployment.process_calls
                        if any(item.endswith("start-hermes-gateways.ps1") for item in command)
                    ]
                    self.assertEqual(len(restart_commands), 1)

                    binding_id = created.json()["binding"]["id"]
                    moved = client.patch(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}",
                        json={"profile_key": "manxin-ai"},
                    )
                    self.assertEqual(moved.status_code, 200)
                    self.assertTrue(moved.json()["binding"]["admission_synced"])
                    self.assertNotIn(raw_chat_id, deployment.admitted["jingli-ai"])
                    self.assertIn(raw_chat_id, deployment.admitted["manxin-ai"])

                    disabled = client.patch(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}",
                        json={"enabled": False},
                    )
                    self.assertEqual(disabled.status_code, 200)
                    self.assertNotIn(raw_chat_id, deployment.admitted["manxin-ai"])

                    calls_before_rename = len(deployment.process_calls)
                    renamed = client.patch(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}",
                        json={"display_name": "改名不应触发部署"},
                    )
                    self.assertEqual(renamed.status_code, 200)
                    self.assertEqual(
                        len(deployment.process_calls),
                        calls_before_rename,
                        "display_name 变更不应触发准入部署",
                    )

                    reenabled = client.patch(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}",
                        json={"enabled": True},
                    )
                    self.assertEqual(reenabled.status_code, 200)
                    self.assertIn(raw_chat_id, deployment.admitted["manxin-ai"])

                    deleted = client.delete(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}"
                    )
                    self.assertEqual(deleted.status_code, 200)
                    self.assertNotIn(raw_chat_id, deployment.admitted["manxin-ai"])

                    nocred_profile = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "nocred-ai",
                            "display_name": "未部署凭据 Agent",
                            "enabled": False,
                        },
                    )
                    self.assertEqual(nocred_profile.status_code, 201)
                    calls_before_nocred = len(deployment.process_calls)
                    offline_binding = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": "oc_nocred_group_000001",
                            "profile_key": "nocred-ai",
                            "enabled": False,
                        },
                    )
                    self.assertEqual(offline_binding.status_code, 201)
                    self.assertFalse(
                        offline_binding.json()["binding"]["admission_synced"]
                    )
                    self.assertEqual(
                        len(deployment.process_calls),
                        calls_before_nocred,
                        "无凭据 profile 不应触发任何准入同步",
                    )

    def test_chat_binding_create_validates_chat_id_and_default_display_name(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"}
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment():
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    profile = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(profile.status_code, 201)

                    rejected = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": "not-a-feishu-chat-id",
                            "profile_key": "jingli-ai",
                            "enabled": False,
                        },
                    )
                    self.assertEqual(rejected.status_code, 422)

                    unnamed = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": "oc_unnamed_group_00001",
                            "profile_key": "jingli-ai",
                            "enabled": False,
                        },
                    )
                    self.assertEqual(unnamed.status_code, 201)
                    binding = unnamed.json()["binding"]
                    self.assertTrue(binding["display_name"].startswith("运营群 "))
                    self.assertNotIn("oc_unnamed_group_00001", unnamed.text)

    def test_feishu_agent_profile_and_binding_patch_enforce_active_state(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"}
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment():
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    profile = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": False,
                        },
                    )
                    self.assertEqual(profile.status_code, 201)

                    raw_chat_id = "oc_patch_active_state_room"
                    rejected = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": raw_chat_id,
                            "profile_key": "jingli-ai",
                            "display_name": "静里运营群",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(rejected.status_code, 422)
                    self.assertIn("停用", rejected.text)

                    disabled_binding = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": raw_chat_id,
                            "profile_key": "jingli-ai",
                            "display_name": "静里运营群",
                            "enabled": False,
                        },
                    )
                    self.assertEqual(disabled_binding.status_code, 201)
                    binding_id = disabled_binding.json()["binding"]["id"]

                    rejected_patch = client.patch(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}",
                        json={"enabled": True},
                    )
                    self.assertEqual(rejected_patch.status_code, 422)

                    enabled_profile = client.patch(
                        "/api/v1/config/feishu-agent-profiles/jingli-ai",
                        json={"display_name": "静里 Agent", "enabled": True},
                    )
                    self.assertEqual(enabled_profile.status_code, 200)
                    self.assertEqual(
                        enabled_profile.json()["profile"]["display_name"],
                        "静里 Agent",
                    )

                    enabled_binding = client.patch(
                        f"/api/v1/config/feishu-chat-agent-bindings/{binding_id}",
                        json={"display_name": "静里业务群", "enabled": True},
                    )
                    self.assertEqual(enabled_binding.status_code, 200)
                    binding = enabled_binding.json()["binding"]
                    self.assertTrue(binding["enabled"])
                    self.assertEqual(binding["display_name"], "静里业务群")
                    self.assertNotIn(raw_chat_id, enabled_binding.text)

    def test_watch_group_chat_requires_active_agent_binding(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"}
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment():
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    raw_chat_id = "oc_agent_bound_ops_room"

                    rejected_ebk = client.post(
                        "/api/v1/ctrip-ebooking/data-center-watch-groups",
                        json={
                            "group_key": "agent_bound_ebk",
                            "name": "Agent bound EBK",
                            "platform_account_id": "ctrip_ebk_account",
                            "feishu_chat_id": raw_chat_id,
                        },
                    )
                    rejected_competitor = client.post(
                        "/api/v1/ctrip-ebooking/competitor-watch-groups",
                        json={
                            "group_key": "agent_bound_competitor",
                            "name": "Agent bound competitor",
                            "platform_account_id": "ctrip_public_account",
                            "feishu_chat_id": raw_chat_id,
                        },
                    )
                    self.assertEqual(rejected_ebk.status_code, 422)
                    self.assertEqual(rejected_competitor.status_code, 422)
                    self.assertIn("先绑定", rejected_ebk.text)
                    self.assertIn("先绑定", rejected_competitor.text)

                    profile = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(profile.status_code, 201)
                    binding = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": raw_chat_id,
                            "profile_key": "jingli-ai",
                            "display_name": "静里运营群",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(binding.status_code, 201)

                    accepted_ebk = client.post(
                        "/api/v1/ctrip-ebooking/data-center-watch-groups",
                        json={
                            "group_key": "agent_bound_ebk",
                            "name": "Agent bound EBK",
                            "platform_account_id": "ctrip_ebk_account",
                            "feishu_chat_id": raw_chat_id,
                        },
                    )
                    accepted_competitor = client.post(
                        "/api/v1/ctrip-ebooking/competitor-watch-groups",
                        json={
                            "group_key": "agent_bound_competitor",
                            "name": "Agent bound competitor",
                            "platform_account_id": "ctrip_public_account",
                            "feishu_chat_id": raw_chat_id,
                        },
                    )
                    self.assertEqual(accepted_ebk.status_code, 200)
                    self.assertEqual(accepted_competitor.status_code, 200)

                    unbound_chat_id = "oc_unbound_patch_room"
                    rejected_ebk_patch = client.patch(
                        "/api/v1/ctrip-ebooking/data-center-watch-groups/agent_bound_ebk",
                        json={"feishu_chat_id": unbound_chat_id},
                    )
                    rejected_competitor_patch = client.patch(
                        "/api/v1/ctrip-ebooking/competitor-watch-groups/agent_bound_competitor",
                        json={"feishu_chat_id": unbound_chat_id},
                    )
                    self.assertEqual(rejected_ebk_patch.status_code, 422)
                    self.assertEqual(rejected_competitor_patch.status_code, 422)

    def test_feishu_admin_endpoints_require_bootstrap_supervisor_key(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = TestClient(_reload_app())

            missing = client.get("/api/v1/config/feishu-agent-profiles")
            invalid = client.get(
                "/api/v1/config/feishu-agent-profiles",
                headers={"X-Pan-Hotel-API-Key": "invalid-supervisor-key"},
            )
            authorized = client.get(
                "/api/v1/config/feishu-agent-profiles",
                headers={
                    "X-Pan-Hotel-API-Key": BOOTSTRAP_SUPERVISOR_API_KEY,
                },
            )

            self.assertEqual(missing.status_code, 401)
            self.assertEqual(invalid.status_code, 401)
            self.assertEqual(authorized.status_code, 200)

    def test_profile_role_assignment_crud_and_default_operator_resolution(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"}
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment():
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    raw_chat_id = "oc_role_assignment_group"
                    profile = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(profile.status_code, 201)
                    binding = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": raw_chat_id,
                            "profile_key": "jingli-ai",
                            "display_name": "静里运营群",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(binding.status_code, 201)

                    config_service = importlib.import_module("app.domain.config.service")
                    self.assertEqual(
                        asyncio.run(config_service.resolve_feishu_role_for_chat(raw_chat_id)),
                        "operator",
                    )

                    created = client.post(
                        "/api/v1/config/feishu-role-assignments",
                        json={
                            "subject_type": "profile",
                            "subject_ref": "jingli-ai",
                            "role": "viewer",
                            "granted_by": "bootstrap-supervisor",
                        },
                    )
                    self.assertEqual(created.status_code, 201)
                    assignment = created.json()["assignment"]
                    self.assertEqual(assignment["subject_type"], "profile")
                    self.assertEqual(assignment["subject_ref"], "jingli-ai")
                    self.assertEqual(assignment["role"], "viewer")
                    self.assertEqual(
                        asyncio.run(config_service.resolve_feishu_role_for_chat(raw_chat_id)),
                        "viewer",
                    )

                    assignment_id = assignment["id"]
                    updated = client.patch(
                        f"/api/v1/config/feishu-role-assignments/{assignment_id}",
                        json={"role": "operator", "granted_by": "security-admin"},
                    )
                    self.assertEqual(updated.status_code, 200)
                    self.assertEqual(updated.json()["assignment"]["role"], "operator")
                    listed = client.get("/api/v1/config/feishu-role-assignments")
                    self.assertEqual(listed.status_code, 200)
                    self.assertEqual(len(listed.json()["assignments"]), 1)

                    deleted = client.delete(
                        f"/api/v1/config/feishu-role-assignments/{assignment_id}"
                    )
                    missing = client.delete(
                        f"/api/v1/config/feishu-role-assignments/{assignment_id}"
                    )
                    self.assertEqual(deleted.status_code, 200)
                    self.assertEqual(missing.status_code, 404)

    def test_api_key_role_assignment_stores_only_sha256_fingerprint(self) -> None:
        delegated_api_key = "task-7a-delegated-supervisor-key"
        expected_fingerprint = hashlib.sha256(
            delegated_api_key.encode("utf-8")
        ).hexdigest()
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())

            created = client.post(
                "/api/v1/config/feishu-role-assignments",
                json={
                    "subject_type": "api_key",
                    "api_key": delegated_api_key,
                    "role": "supervisor",
                    "granted_by": "bootstrap-supervisor",
                },
            )

            self.assertEqual(created.status_code, 201)
            assignment = created.json()["assignment"]
            self.assertEqual(assignment["subject_ref"], expected_fingerprint)
            self.assertNotIn("api_key", assignment)
            self.assertNotIn(delegated_api_key, created.text)

            database_path = Path(db_url.removeprefix("sqlite+aiosqlite:///"))
            with closing(sqlite3.connect(database_path)) as connection:
                stored = connection.execute(
                    "SELECT subject_ref, role FROM feishu_role_assignments"
                ).fetchone()
            self.assertEqual(stored, (expected_fingerprint, "supervisor"))
            self.assertNotIn(delegated_api_key, repr(stored))

            delegated = client.get(
                "/api/v1/config/supervisor-overview",
                headers={"X-Pan-Hotel-API-Key": delegated_api_key},
            )
            self.assertEqual(delegated.status_code, 200)

    def test_role_assignment_rejects_invalid_subjects(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            client = _supervisor_client(_reload_app())

            missing_profile = client.post(
                "/api/v1/config/feishu-role-assignments",
                json={
                    "subject_type": "profile",
                    "subject_ref": "missing-profile",
                    "role": "viewer",
                    "granted_by": "bootstrap-supervisor",
                },
            )
            missing_api_key = client.post(
                "/api/v1/config/feishu-role-assignments",
                json={
                    "subject_type": "api_key",
                    "role": "supervisor",
                    "granted_by": "bootstrap-supervisor",
                },
            )
            mismatched_subject = client.post(
                "/api/v1/config/feishu-role-assignments",
                json={
                    "subject_type": "profile",
                    "subject_ref": "missing-profile",
                    "api_key": "must-not-be-accepted-for-profile",
                    "role": "operator",
                    "granted_by": "bootstrap-supervisor",
                },
            )

            self.assertEqual(missing_profile.status_code, 404)
            self.assertEqual(missing_api_key.status_code, 422)
            self.assertEqual(mismatched_subject.status_code, 422)
            self.assertNotIn("must-not-be-accepted-for-profile", mismatched_subject.text)

    def test_set_credentials_deploys_full_runtime_and_syncs_llm_before_restart(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"}
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment() as deployment:
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    profile = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": False,
                        },
                    )
                    self.assertEqual(profile.status_code, 201)

                    sync_calls: list[str] = []

                    async def _record_llm_sync(**_: object) -> object:
                        sync_calls.append("llm-sync")
                        deployment.process_calls.append(["<llm-sync>"])
                        return mock.Mock(status="synced")

                    runtime_config = mock.Mock()
                    runtime_config.sync_hermes_model_configs = _record_llm_sync
                    with mock.patch(
                        "app.domain.config.service.get_runtime_config",
                        return_value=runtime_config,
                    ):
                        response = client.post(
                            "/api/v1/config/feishu-agent-profiles/jingli-ai/credentials",
                            json={
                                "app_id": "cli_jingli_new",
                                "app_secret": "rotated_secret_value",
                                "display_name": "静里 AI 管家",
                            },
                        )
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn("rotated_secret_value", response.text)
                    self.assertEqual(len(sync_calls), 1)

                    commands = deployment.process_calls
                    apply_commands = [
                        command for command in commands if "apply" in command
                    ]
                    self.assertEqual(len(apply_commands), 1)
                    apply_command = apply_commands[0]
                    python_index = apply_command.index("--python-exe")
                    self.assertIn("python.exe", apply_command[python_index + 1])
                    restart_indexes = [
                        index
                        for index, command in enumerate(commands)
                        if any(item.endswith("start-hermes-gateways.ps1") for item in command)
                    ]
                    self.assertEqual(len(restart_indexes), 1)
                    apply_index = commands.index(apply_command)
                    llm_sync_index = commands.index(["<llm-sync>"])
                    restart_index = restart_indexes[0]
                    self.assertLess(apply_index, llm_sync_index)
                    self.assertLess(llm_sync_index, restart_index)

    def test_delete_profile_cleans_manifest_env_and_stops_gateway(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"},
            "manxin-ai": {"app_id": "cli_manxin", "app_secret": "manxin_secret"},
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment() as deployment:
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    created = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(created.status_code, 201)
                    calls_before_delete = len(deployment.process_calls)

                    deleted = client.delete(
                        "/api/v1/config/feishu-agent-profiles/jingli-ai"
                    )
                    self.assertEqual(deleted.status_code, 200)
                    payload = deleted.json()["profile"]
                    self.assertTrue(payload["manifest_cleaned"])
                    self.assertIsNone(payload["cleanup_warning"])
                    self.assertNotIn("jingli_secret", deleted.text)

                    cleanup_commands = deployment.process_calls[calls_before_delete:]
                    remove_commands = [
                        command
                        for command in cleanup_commands
                        if "remove-profile" in command
                    ]
                    self.assertEqual(len(remove_commands), 1)
                    self.assertIn("jingli-ai", remove_commands[0])
                    env_commands = [
                        command
                        for command in cleanup_commands
                        if "write-backend-env" in command
                    ]
                    self.assertEqual(len(env_commands), 1)
                    stop_commands = [
                        command
                        for command in cleanup_commands
                        if "gateway" in command and "stop" in command
                    ]
                    self.assertEqual(len(stop_commands), 1)
                    self.assertIn("jingli-ai", stop_commands[0])

                    listed = client.get("/api/v1/config/feishu-agent-profiles")
                    profile_keys = [
                        profile["profile_key"] for profile in listed.json()["profiles"]
                    ]
                    self.assertNotIn("jingli-ai", profile_keys)

    def test_delete_profile_without_credentials_skips_manifest_cleanup(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": "{}"},
            clear=False,
        ):
            with _isolated_feishu_deployment() as deployment:
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    created = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": False,
                        },
                    )
                    self.assertEqual(created.status_code, 201)
                    calls_before_delete = len(deployment.process_calls)

                    deleted = client.delete(
                        "/api/v1/config/feishu-agent-profiles/jingli-ai"
                    )
                    self.assertEqual(deleted.status_code, 200)
                    self.assertFalse(deleted.json()["profile"]["manifest_cleaned"])
                    self.assertEqual(
                        len(deployment.process_calls),
                        calls_before_delete,
                        "无凭据 profile 删除不应触发部署命令",
                    )

    def test_supervisor_overview_returns_all_profiles_bindings_and_watch_groups(self) -> None:
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"}
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_feishu_deployment():
                with _isolated_sqlite_database() as db_url:
                    _reload_db_session(db_url)
                    client = _supervisor_client(_reload_app())
                    raw_chat_id = "oc_supervisor_overview_group"
                    profile = client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": "jingli-ai",
                            "display_name": "静里 AI 管家",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(profile.status_code, 201)
                    binding = client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": raw_chat_id,
                            "profile_key": "jingli-ai",
                            "display_name": "静里运营群",
                            "enabled": True,
                        },
                    )
                    self.assertEqual(binding.status_code, 201)
                    ebk_group = client.post(
                        "/api/v1/ctrip-ebooking/data-center-watch-groups",
                        json={
                            "group_key": "overview_ebk",
                            "name": "Overview EBK",
                            "feishu_chat_id": raw_chat_id,
                        },
                    )
                    competitor_group = client.post(
                        "/api/v1/ctrip-ebooking/competitor-watch-groups",
                        json={
                            "group_key": "overview_competitor",
                            "name": "Overview competitor",
                            "feishu_chat_id": raw_chat_id,
                        },
                    )
                    self.assertEqual(ebk_group.status_code, 200)
                    self.assertEqual(competitor_group.status_code, 200)

                    regular_ebk = client.get("/api/v1/ctrip-ebooking/data-center-watch-groups")
                    regular_competitor = client.get(
                        "/api/v1/ctrip-ebooking/competitor-watch-groups"
                    )
                    self.assertEqual(regular_ebk.status_code, 200)
                    self.assertEqual(regular_competitor.status_code, 200)
                    self.assertNotIn(raw_chat_id, regular_ebk.text)
                    self.assertNotIn(raw_chat_id, regular_competitor.text)

                    response = client.get("/api/v1/config/supervisor-overview")

                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertEqual(
                        [item["profile_key"] for item in payload["profiles"]],
                        ["jingli-ai"],
                    )
                    self.assertEqual(len(payload["bindings"]), 1)
                    self.assertEqual(payload["bindings"][0]["chat_id"], raw_chat_id)
                    self.assertIn(
                        "overview_ebk",
                        [item["group_key"] for item in payload["watch_groups"]["ebk"]],
                    )
                    self.assertIn(
                        "overview_competitor",
                        [
                            item["group_key"]
                            for item in payload["watch_groups"]["competitor"]
                        ],
                    )
                    ebk_overview = next(
                        item
                        for item in payload["watch_groups"]["ebk"]
                        if item["group_key"] == "overview_ebk"
                    )
                    competitor_overview = next(
                        item
                        for item in payload["watch_groups"]["competitor"]
                        if item["group_key"] == "overview_competitor"
                    )
                    self.assertEqual(ebk_overview["feishu_chat_id"], raw_chat_id)
                    self.assertEqual(competitor_overview["feishu_chat_id"], raw_chat_id)

    def test_supervisor_overview_exposes_raw_webhook_urls_for_authenticated_admins(self) -> None:
        """Supervisor overview is API-key-gated; authenticated admins must see
        the raw feishu_webhook_url, while regular endpoints keep masking it."""
        credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"}
        }
        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(credentials)},
            clear=False,
        ):
            with _isolated_sqlite_database() as db_url:
                _reload_db_session(db_url)
                client = _supervisor_client(_reload_app())
                raw_chat_id = "oc_webhook_visibility_group"
                client.post(
                    "/api/v1/config/feishu-agent-profiles",
                    json={
                        "profile_key": "jingli-ai",
                        "display_name": "静里 AI 管家",
                        "enabled": True,
                    },
                )
                client.post(
                    "/api/v1/config/feishu-chat-agent-bindings",
                    json={
                        "chat_id": raw_chat_id,
                        "profile_key": "jingli-ai",
                        "display_name": "运营群",
                        "enabled": True,
                    },
                )
                ebk_webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/ebk-secret-token"
                competitor_webhook = (
                    "https://open.feishu.cn/open-apis/bot/v2/hook/competitor-secret-token"
                )
                client.post(
                    "/api/v1/ctrip-ebooking/data-center-watch-groups",
                    json={
                        "group_key": "webhook_ebk",
                        "name": "Webhook EBK",
                        "feishu_chat_id": raw_chat_id,
                        "feishu_webhook_url": ebk_webhook,
                    },
                )
                client.post(
                    "/api/v1/ctrip-ebooking/competitor-watch-groups",
                    json={
                        "group_key": "webhook_competitor",
                        "name": "Webhook competitor",
                        "feishu_chat_id": raw_chat_id,
                        "feishu_webhook_url": competitor_webhook,
                    },
                )

                # Regular (non-supervisor) endpoints must still mask the URL.
                regular_ebk = client.get("/api/v1/ctrip-ebooking/data-center-watch-groups")
                regular_competitor = client.get(
                    "/api/v1/ctrip-ebooking/competitor-watch-groups"
                )
                self.assertNotIn(ebk_webhook, regular_ebk.text)
                self.assertNotIn(competitor_webhook, regular_competitor.text)

                # Supervisor overview must expose the raw URL to admins.
                response = client.get("/api/v1/config/supervisor-overview")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                ebk_overview = next(
                    item
                    for item in payload["watch_groups"]["ebk"]
                    if item["group_key"] == "webhook_ebk"
                )
                competitor_overview = next(
                    item
                    for item in payload["watch_groups"]["competitor"]
                    if item["group_key"] == "webhook_competitor"
                )
                self.assertEqual(ebk_overview["feishu_webhook_url"], ebk_webhook)
                self.assertEqual(
                    competitor_overview["feishu_webhook_url"], competitor_webhook
                )

    def test_integration_config_masks_llm_key(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)

            initial = client.get("/api/v1/config/integrations")
            self.assertEqual(initial.status_code, 200)
            self.assertEqual(initial.json()["llm_provider"], "deepseek")
            self.assertEqual(initial.json()["llm_model"], "deepseek-v4-flash")
            self.assertFalse(initial.json()["llm_api_key_configured"])

            response = client.post(
                "/api/v1/config/integrations",
                json={
                    "llm_provider": "deepseek",
                    "llm_base_url": "https://api.deepseek.com",
                    "llm_model": "deepseek-v4-flash",
                    "llm_api_key": "test-deepseek-api-key-placeholder",
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["llm_api_key_configured"])
            self.assertEqual(payload["llm_api_key_masked"], "test...lder")
            self.assertNotIn("test-deepseek-api-key-placeholder", str(payload))

    def test_llm_url_rejects_unlisted_relay_host(self) -> None:
        config_service = importlib.import_module("app.domain.config.service")
        settings_module = importlib.import_module("app.core.settings")
        with mock.patch.dict(os.environ, {"LLM_ALLOWED_HOSTS": ""}, clear=False):
            settings_module.get_settings.cache_clear()
            try:
                with self.assertRaisesRegex(ValueError, "允许列表"):
                    config_service._validate_llm_base_url(
                        "https://relay.example.net/v1"
                    )
            finally:
                settings_module.get_settings.cache_clear()

    def test_default_allowlist_includes_builtin_provider_hosts(self) -> None:
        config_service = importlib.import_module("app.domain.config.service")
        settings_module = importlib.import_module("app.core.settings")
        with mock.patch.dict(os.environ, {"LLM_ALLOWED_HOSTS": ""}, clear=False):
            settings_module.get_settings.cache_clear()
            try:
                self.assertEqual(
                    config_service._validate_llm_base_url(
                        "https://api.siliconflow.cn/v1"
                    ),
                    "https://api.siliconflow.cn/v1",
                )
                self.assertEqual(
                    config_service._validate_llm_base_url(
                        "https://api.openai.com/v1"
                    ),
                    "https://api.openai.com/v1",
                )
            finally:
                settings_module.get_settings.cache_clear()

    def test_llm_url_rejects_allowlisted_hostname_resolving_to_private_address(self) -> None:
        config_service = importlib.import_module("app.domain.config.service")
        settings_module = importlib.import_module("app.core.settings")
        with (
            mock.patch.dict(
                os.environ,
                {"LLM_ALLOWED_HOSTS": "api.deepseek.com"},
                clear=False,
            ),
            mock.patch(
                "socket.getaddrinfo",
                return_value=[
                    (2, 1, 6, "", ("10.23.4.5", 443)),
                    (2, 1, 6, "", ("203.0.113.10", 443)),
                ],
            ),
        ):
            settings_module.get_settings.cache_clear()
            try:
                with self.assertRaisesRegex(ValueError, "内网|保留地址"):
                    config_service._validate_llm_base_url(
                        "https://api.deepseek.com",
                        resolve_hostname=True,
                    )
            finally:
                settings_module.get_settings.cache_clear()

    def test_llm_url_accepts_allowlisted_hostname_resolving_only_to_public_addresses(self) -> None:
        config_service = importlib.import_module("app.domain.config.service")
        settings_module = importlib.import_module("app.core.settings")
        with (
            mock.patch.dict(
                os.environ,
                {"LLM_ALLOWED_HOSTS": "llm.example.test"},
                clear=False,
            ),
            mock.patch(
                "socket.getaddrinfo",
                return_value=[
                    (2, 1, 6, "", ("8.8.8.8", 443)),
                    (10, 1, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
                ],
            ),
        ):
            settings_module.get_settings.cache_clear()
            try:
                self.assertEqual(
                    config_service._validate_llm_base_url(
                        "https://llm.example.test/v1",
                        resolve_hostname=True,
                    ),
                    "https://llm.example.test/v1",
                )
            finally:
                settings_module.get_settings.cache_clear()

    def test_llm_static_config_does_not_require_dns_but_runtime_does(self) -> None:
        config_service = importlib.import_module("app.domain.config.service")
        with mock.patch(
            "socket.getaddrinfo",
            side_effect=OSError("temporary DNS failure"),
        ) as resolve:
            self.assertEqual(
                config_service._validate_llm_base_url(
                    "https://api.deepseek.com"
                ),
                "https://api.deepseek.com",
            )
            resolve.assert_not_called()

        runtime = config_service.RuntimeIntegrationConfig()
        with mock.patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "内网|保留地址"):
                asyncio.run(runtime.llm_runtime())

    def test_llm_test_and_model_list_reject_private_dns_before_http(self) -> None:
        config_service = importlib.import_module("app.domain.config.service")
        runtime = config_service.RuntimeIntegrationConfig()
        runtime.llm_api_key = "configured-test-key"
        with (
            mock.patch(
                "socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("169.254.169.254", 443))],
            ),
            mock.patch.object(
                config_service.httpx,
                "AsyncClient",
                side_effect=AssertionError("HTTP client must not be constructed"),
            ) as async_client,
        ):
            connection = asyncio.run(runtime.test_llm_connection())
            models = asyncio.run(runtime.list_llm_models())

        self.assertEqual(connection.status, "failed")
        self.assertFalse(connection.evidence["request_sent"])
        self.assertEqual(models.status, "failed")
        self.assertFalse(models.evidence["request_sent"])
        async_client.assert_not_called()

    def test_integration_config_persists_saved_key_after_runtime_reset(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)
            response = client.post(
                "/api/v1/config/integrations",
                json={"llm_api_key": "persisted-deepseek-api-key-placeholder"},
            )
            self.assertEqual(response.status_code, 200)

            config_service = importlib.import_module("app.domain.config.service")
            config_service.reset_runtime_config()

            response = client.get("/api/v1/config/integrations")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["llm_api_key_configured"])
            self.assertEqual(payload["llm_api_key_masked"], "pers...lder")
            self.assertNotIn("persisted-deepseek-api-key-placeholder", str(payload))

    def test_integration_update_and_snapshot_do_not_expose_half_updated_state(self) -> None:
        config_service = importlib.import_module("app.domain.config.service")

        async def scenario() -> None:
            persisted = {
                "llm_provider": "deepseek",
                "llm_base_url": "https://api.deepseek.com",
                "llm_model": "deepseek-v3",
                "llm_api_key": "old-runtime-key",
            }
            persist_started = asyncio.Event()
            allow_persist = asyncio.Event()

            async def read_persisted():
                return mock.Mock(**persisted)

            async def write_persisted(**values):
                persist_started.set()
                await allow_persist.wait()
                persisted.update(values)

            with (
                mock.patch.object(
                    config_service,
                    "get_persisted_integration_config",
                    side_effect=read_persisted,
                ),
                mock.patch.object(
                    config_service,
                    "upsert_persisted_integration_config",
                    side_effect=write_persisted,
                ),
            ):
                runtime = config_service.RuntimeIntegrationConfig()
                update_task = asyncio.create_task(
                    runtime.update(
                        config_service.IntegrationConfigUpdate(
                            llm_model="deepseek-v4-flash",
                            llm_api_key="new-runtime-key",
                        )
                    )
                )
                await asyncio.wait_for(persist_started.wait(), timeout=1)
                snapshot_task = asyncio.create_task(runtime.snapshot())
                await asyncio.sleep(0)

                self.assertFalse(
                    snapshot_task.done(),
                    "snapshot must wait until the in-flight update is persisted",
                )
                allow_persist.set()
                updated, snapshot = await asyncio.gather(update_task, snapshot_task)

            self.assertEqual(updated.llm_model, "deepseek-v4-flash")
            self.assertEqual(snapshot.llm_model, "deepseek-v4-flash")
            self.assertEqual(updated.llm_api_key_masked, "new-...-key")
            self.assertEqual(snapshot.llm_api_key_masked, "new-...-key")

        asyncio.run(scenario())

    def test_llm_connection_test_requires_saved_api_key(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            response = _supervisor_client(app).post("/api/v1/config/integrations/llm/test")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "config_required")
            self.assertFalse(payload["api_key_configured"])
            self.assertFalse(payload["evidence"]["api_key_returned"])
            self.assertFalse(payload["evidence"]["request_sent"])

    def test_llm_connection_test_calls_openai_compatible_endpoint(self) -> None:
        import httpx

        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)
            save_response = client.post(
                "/api/v1/config/integrations",
                json={"llm_api_key": "test-deepseek-api-key-placeholder"},
            )
            self.assertEqual(save_response.status_code, 200)

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
                self.assertEqual(
                    request.headers["Authorization"],
                    "Bearer test-deepseek-api-key-placeholder",
                )
                body = request.read().decode("utf-8")
                self.assertIn("deepseek-v4-flash", body)
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "{\"status\":\"ok\"}"}}],
                        "usage": {"total_tokens": 12},
                    },
                )

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            config_service = importlib.import_module("app.domain.config.service")
            original_async_client = config_service.httpx.AsyncClient
            config_service.httpx.AsyncClient = (
                lambda *args, **kwargs: real_async_client(transport=transport)
            )
            try:
                response = client.post("/api/v1/config/integrations/llm/test")
            finally:
                config_service.httpx.AsyncClient = original_async_client

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertTrue(payload["api_key_configured"])
            self.assertEqual(payload["data"]["http_status"], 200)
            self.assertEqual(payload["data"]["reply_preview"], '{"status":"ok"}')
            self.assertFalse(payload["evidence"]["api_key_returned"])
            self.assertNotIn("test-deepseek-api-key-placeholder", str(payload))

    def test_llm_model_list_calls_models_endpoint_without_exposing_key(self) -> None:
        import httpx

        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(str(request.url), "https://api.deepseek.com/models")
                self.assertEqual(
                    request.headers["Authorization"],
                    "Bearer transient-deepseek-api-key-placeholder",
                )
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": "deepseek-v4-flash"},
                            {"id": "deepseek-chat"},
                            {"id": "deepseek-v4-flash"},
                        ]
                    },
                )

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            config_service = importlib.import_module("app.domain.config.service")
            original_async_client = config_service.httpx.AsyncClient
            async_client_kwargs: dict[str, object] = {}

            def async_client_factory(*args, **kwargs):
                async_client_kwargs.update(kwargs)
                return real_async_client(transport=transport)

            config_service.httpx.AsyncClient = async_client_factory
            try:
                response = client.post(
                    "/api/v1/config/integrations/llm/models",
                    json={
                        "llm_base_url": "https://api.deepseek.com",
                        "llm_api_key": "transient-deepseek-api-key-placeholder",
                    },
                )
            finally:
                config_service.httpx.AsyncClient = original_async_client

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["models"], ["deepseek-chat", "deepseek-v4-flash"])
            self.assertEqual(async_client_kwargs["timeout"], 30)
            self.assertNotIn("transient-deepseek-api-key-placeholder", str(payload))

    def test_llm_connection_test_accepts_unsaved_form_values(self) -> None:
        import httpx

        with (
            _isolated_sqlite_database() as db_url,
            mock.patch.dict(
                os.environ,
                {
                    "LLM_ALLOWED_HOSTS": "api.deepseek.com,llm.example.test",
                },
                clear=False,
            ),
            mock.patch(
                "socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
            ),
        ):
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(str(request.url), "https://llm.example.test/v1/chat/completions")
                self.assertEqual(
                    request.headers["Authorization"],
                    "Bearer transient-llm-api-key-placeholder",
                )
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "ok"}}]},
                )

            transport = httpx.MockTransport(handler)
            real_async_client = httpx.AsyncClient
            config_service = importlib.import_module("app.domain.config.service")
            original_async_client = config_service.httpx.AsyncClient
            config_service.httpx.AsyncClient = (
                lambda *args, **kwargs: real_async_client(transport=transport)
            )
            try:
                response = client.post(
                    "/api/v1/config/integrations/llm/test",
                    json={
                        "llm_provider": "custom",
                        "llm_base_url": "https://llm.example.test/v1",
                        "llm_model": "custom-model",
                        "llm_api_key": "transient-llm-api-key-placeholder",
                    },
                )
            finally:
                config_service.httpx.AsyncClient = original_async_client

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["base_url"], "https://llm.example.test/v1")
            self.assertNotIn("transient-llm-api-key-placeholder", str(payload))

    def test_active_open_ids_returns_deduped_open_ids_from_session_bridge(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            hermes_base = temp_root / "hermes"
            bridge_dir = hermes_base / "feishu-session-bridge"
            bridge_dir.mkdir(parents=True)

            def _write_bridge(name: str, chat_id: str, open_id: str) -> None:
                payload = {"chat_id": chat_id, "session_id": name, "message_id": "", "open_id": open_id}
                (bridge_dir / f"{name}.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )

            _write_bridge("sess_a", "oc_group_one", "ou_repeated_open_id")
            time.sleep(0.05)
            _write_bridge("sess_b", "oc_group_two", "ou_repeated_open_id")
            time.sleep(0.05)
            _write_bridge("sess_c", "oc_group_three", "ou_other_open_id")

            deployment_env = {"HERMES_HOME": str(hermes_base)}
            with (
                _isolated_sqlite_database() as db_url,
                mock.patch.dict(os.environ, deployment_env, clear=False),
            ):
                _reload_db_session(db_url)
                app = _reload_app()
                client = _supervisor_client(app)

                response = client.get(
                    "/api/v1/config/feishu/profiles/jingli-ai/admission/active-open-ids"
                )

            self.assertEqual(response.status_code, 200)
            open_ids = response.json()["open_ids"]
            by_open = {row["open_id"]: row for row in open_ids}
            self.assertEqual(set(by_open), {"ou_repeated_open_id", "ou_other_open_id"})
            repeated = by_open["ou_repeated_open_id"]
            self.assertIn("last_seen", repeated)
            self.assertIn("chat_id_masked", repeated)
            self.assertNotIn("oc_group_one", str(repeated))
            self.assertNotIn("oc_group_two", str(repeated))

    def test_active_open_ids_returns_empty_when_bridge_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            hermes_base = temp_root / "hermes"
            deployment_env = {"HERMES_HOME": str(hermes_base)}
            with (
                _isolated_sqlite_database() as db_url,
                mock.patch.dict(os.environ, deployment_env, clear=False),
            ):
                _reload_db_session(db_url)
                app = _reload_app()
                client = _supervisor_client(app)

                response = client.get(
                    "/api/v1/config/feishu/profiles/jingli-ai/admission/active-open-ids"
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["open_ids"], [])


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watch-config-test.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        self._previous_llm_key = os.environ.get("LLM_API_KEY")
        self._previous_bootstrap_key = os.environ.get(
            "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"
        )
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        os.environ[
            "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"
        ] = BOOTSTRAP_SUPERVISOR_API_KEY
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
        # Restore the global app.db.session engine so later tests in this
        # process do not inherit a stale engine pointing at the temp file we
        # are about to delete. Must run before the temp dir cleanup.
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


