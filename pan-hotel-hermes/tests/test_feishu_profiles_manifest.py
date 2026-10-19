from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

from scripts import feishu_profiles
from scripts.feishu_profiles import (
    FeishuProfilesManifestError,
    apply_profile_configuration,
    build_backend_environment,
    build_status_payload,
    load_feishu_profiles_manifest,
    migrate_existing_profiles,
    write_backend_environment,
)


class FeishuProfilesManifestTests(unittest.TestCase):
    def _write_manifest(
        self,
        root: Path,
        *,
        duplicate_app_id: bool = False,
        empty_allowlist: bool = False,
        missing_soul: bool = False,
    ) -> Path:
        profiles: dict[str, object] = {}
        for index, profile_key in enumerate(("jingli-ai", "manxin-ai")):
            soul = f"config/profiles/{profile_key}/SOUL.md"
            if not missing_soul or profile_key != "manxin-ai":
                soul_path = root / soul
                soul_path.parent.mkdir(parents=True, exist_ok=True)
                soul_path.write_text(f"# {profile_key}\n", encoding="utf-8")
            profiles[profile_key] = {
                "app_id": "cli_shared" if duplicate_app_id else f"cli_{index}",
                "app_secret": f"secret_{index}",
                "display_name": profile_key,
                "require_mention": profile_key == "jingli-ai",
                "soul": soul,
                "group_rules": [
                    {
                        "chat_id": f"oc_{index}",
                        "policy": "allowlist" if profile_key == "jingli-ai" else "open",
                        "allowed_users": (
                            []
                            if empty_allowlist and profile_key == "jingli-ai"
                            else (["ou_allowed"] if profile_key == "jingli-ai" else [])
                        ),
                    }
                ],
            }
        path = root / "feishu-profiles.yaml"
        path.write_text(
            yaml.safe_dump({"profiles": profiles}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path

    def test_loads_valid_profiles_and_builds_backend_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )

            self.assertEqual(list(manifest.profiles), ["jingli-ai", "manxin-ai"])
            credentials_json, chat_map_json = build_backend_environment(manifest)
            self.assertIn('"jingli-ai"', credentials_json)
            self.assertIn('"app_secret": "secret_0"', credentials_json)
            self.assertEqual(chat_map_json, '{"oc_0": "jingli-ai", "oc_1": "manxin-ai"}')

    def test_status_payload_contains_counts_without_identifiers_or_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )

            payload = build_status_payload(
                manifest,
                context_loader=lambda chat_id: {
                    "status": "resolved",
                    "chat_id": chat_id,
                    "store_catalog": [{"store_label": "测试酒店"}],
                },
            )

            self.assertEqual(payload[0]["profile"], "jingli-ai")
            self.assertEqual(payload[0]["allowed_group_count"], 1)
            self.assertEqual(payload[0]["allowed_user_count"], 1)
            self.assertEqual(payload[0]["bound_stores"], ["测试酒店"])
            self.assertEqual(payload[0]["context_status"], "resolved")
            serialized = yaml.safe_dump(payload)
            self.assertNotIn("app_id", serialized)
            self.assertNotIn("app_secret", serialized)
            self.assertNotIn("cli_", serialized)
            self.assertNotIn("oc_", serialized)
            self.assertNotIn("ou_", serialized)

    def test_rejects_duplicate_app_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(FeishuProfilesManifestError, "App ID"):
                load_feishu_profiles_manifest(
                    self._write_manifest(root, duplicate_app_id=True),
                    hermes_root=root,
                )

    def test_rejects_empty_allowlist_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(FeishuProfilesManifestError, "allowed_users"):
                load_feishu_profiles_manifest(
                    self._write_manifest(root, empty_allowlist=True),
                    hermes_root=root,
                )

    def test_rejects_missing_soul(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(FeishuProfilesManifestError, "SOUL"):
                load_feishu_profiles_manifest(
                    self._write_manifest(root, missing_soul=True),
                    hermes_root=root,
                )

    def test_apply_profile_writes_env_and_merges_group_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            profile_root = root / "runtime" / "profiles" / "jingli-ai"
            profile_root.mkdir(parents=True)
            (profile_root / "config.yaml").write_text(
                yaml.safe_dump({"agent": {"max_turns": 50}}, sort_keys=False),
                encoding="utf-8",
            )

            apply_profile_configuration(
                manifest.profiles["jingli-ai"],
                profile_root,
                service_token="shared-service-token",
            )

            env_text = (profile_root / ".env").read_text(encoding="utf-8")
            self.assertIn("FEISHU_APP_ID=cli_0", env_text)
            self.assertIn("FEISHU_APP_SECRET=secret_0", env_text)
            self.assertIn("FEISHU_ALLOWED_CHATS=oc_0", env_text)
            self.assertIn("FEISHU_ALLOWED_USERS=ou_allowed", env_text)
            self.assertIn("FEISHU_REQUIRE_MENTION=true", env_text)
            self.assertIn("WATCH_API_SHARED_SECRET=shared-service-token", env_text)
            config = yaml.safe_load((profile_root / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["agent"]["max_turns"], 50)
            feishu = config["platforms"]["feishu"]["extra"]
            self.assertEqual(feishu["default_group_policy"], "disabled")
            self.assertEqual(feishu["group_rules"]["oc_0"]["policy"], "allowlist")
            self.assertEqual(feishu["group_rules"]["oc_0"]["allowlist"], ["ou_allowed"])
            self.assertTrue(feishu["group_rules"]["oc_0"]["require_mention"])

    def test_apply_preserves_manual_home_channel_for_multi_group_profile(self) -> None:
        # 多群 profile 无法从清单推断 home channel：apply 不得用空串覆盖运维
        # 手工设定的 FEISHU_HOME_CHANNEL（2026-09-18 生产机A 迁移实测回归：
        # apply 冲空后网关报 "No home channel is set for Feishu"）。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            soul = root / "config" / "profiles" / "jingli-ai" / "SOUL.md"
            soul.parent.mkdir(parents=True)
            soul.write_text("# jingli-ai\n", encoding="utf-8")
            manifest_data = {
                "profiles": {
                    "jingli-ai": {
                        "app_id": "cli_0",
                        "app_secret": "secret_0",
                        "display_name": "jingli-ai",
                        "require_mention": True,
                        "soul": "config/profiles/jingli-ai/SOUL.md",
                        "group_rules": [
                            {"chat_id": "oc_a", "policy": "allowlist", "allowed_users": ["ou_u1"]},
                            {"chat_id": "oc_b", "policy": "allowlist", "allowed_users": ["ou_u1"]},
                        ],
                    }
                }
            }
            manifest_path = root / "feishu-profiles.yaml"
            manifest_path.write_text(
                yaml.safe_dump(manifest_data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            manifest = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            profile_root = root / "runtime" / "profiles" / "jingli-ai"
            profile_root.mkdir(parents=True)
            (profile_root / ".env").write_text(
                "FEISHU_HOME_CHANNEL=oc_manual_home\n",
                encoding="utf-8",
            )

            apply_profile_configuration(
                manifest.profiles["jingli-ai"],
                profile_root,
            )

            env_text = (profile_root / ".env").read_text(encoding="utf-8")
            self.assertIn("FEISHU_HOME_CHANNEL=oc_manual_home", env_text)

    def test_backend_mapping_update_preserves_other_feishu_env_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            dotenv_path = root / ".env"
            dotenv_path.write_text(
                "FEISHU_APP_ID=legacy_app\nFEISHU_VERIFICATION_TOKEN=keep_me\n",
                encoding="utf-8",
            )

            write_backend_environment(manifest, dotenv_path)

            env_text = dotenv_path.read_text(encoding="utf-8")
            self.assertIn("FEISHU_APP_ID=legacy_app", env_text)
            self.assertIn("FEISHU_VERIFICATION_TOKEN=keep_me", env_text)
            self.assertIn("FEISHU_APP_CREDENTIALS_JSON=", env_text)
            self.assertIn("FEISHU_CHAT_APP_MAP_JSON=", env_text)
            self.assertIn("PAN_HOTEL_ENFORCE_API_AUTH=true", env_text)
            self.assertRegex(env_text, r"(?m)^WATCH_API_SHARED_SECRET=.{32,}$")

    def test_backend_environment_preserves_explicit_enforce_api_auth_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            dotenv_path = root / ".env"
            dotenv_path.write_text(
                "PAN_HOTEL_ENFORCE_API_AUTH=false\n",
                encoding="utf-8",
            )

            write_backend_environment(manifest, dotenv_path)

            env_text = dotenv_path.read_text(encoding="utf-8")
            self.assertIn("PAN_HOTEL_ENFORCE_API_AUTH=false", env_text)
            self.assertNotIn("PAN_HOTEL_ENFORCE_API_AUTH=true", env_text)

    def test_apply_profile_writes_supervisor_key_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            profile_root = root / "runtime" / "profiles" / "jingli-ai"
            profile_root.mkdir(parents=True)

            apply_profile_configuration(
                manifest.profiles["jingli-ai"],
                profile_root,
                service_token="shared-service-token",
                supervisor_key="supervisor-key-1",
            )

            env_text = (profile_root / ".env").read_text(encoding="utf-8")
            self.assertIn("WATCH_API_SHARED_SECRET=shared-service-token", env_text)
            self.assertIn("WATCH_API_SUPERVISOR_KEY=supervisor-key-1", env_text)

    def test_backend_environment_preserves_existing_service_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            dotenv_path = root / ".env"
            dotenv_path.write_text(
                "WATCH_API_SHARED_SECRET=existing-service-token\n",
                encoding="utf-8",
            )

            service_token = write_backend_environment(manifest, dotenv_path)

            env_text = dotenv_path.read_text(encoding="utf-8")
            self.assertEqual(service_token, "existing-service-token")
            self.assertEqual(env_text.count("WATCH_API_SHARED_SECRET="), 1)
            self.assertIn("WATCH_API_SHARED_SECRET=existing-service-token", env_text)

    @unittest.skipUnless(feishu_profiles.os.name == "nt", "Windows ACL test")
    def test_atomic_write_applies_restrictive_windows_acl_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "managed-secret.env"
            with (
                mock.patch.dict(
                    feishu_profiles.os.environ,
                    {"USERDOMAIN": "HOTEL", "USERNAME": "runtime-user"},
                    clear=False,
                ),
                mock.patch("subprocess.run") as run,
            ):
                feishu_profiles._atomic_write_text(path, "SECRET=value\n")

            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertEqual(command[0], "icacls")
            self.assertIn("/inheritance:r", command)
            self.assertIn("/grant:r", command)
            self.assertIn("HOTEL\\runtime-user:(F)", command)
            self.assertIn("*S-1-5-18:(F)", command)
            self.assertIn("*S-1-5-32-544:(F)", command)
            self.assertFalse(run.call_args.kwargs.get("shell", False))
            self.assertTrue(run.call_args.kwargs["check"])

    def test_profile_admission_crud_preserves_credentials_and_masks_chat_id(self) -> None:
        read_admission = getattr(feishu_profiles, "read_profile_admission", None)
        update_admission = getattr(feishu_profiles, "update_profile_admission", None)
        add_user = getattr(feishu_profiles, "add_profile_admission_user", None)
        remove_user = getattr(feishu_profiles, "remove_profile_admission_user", None)
        self.assertTrue(callable(read_admission))
        self.assertTrue(callable(update_admission))
        self.assertTrue(callable(add_user))
        self.assertTrue(callable(remove_user))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)

            before = read_admission(
                manifest_path,
                hermes_root=root,
                profile_key="jingli-ai",
            )
            group = before["groups"][0]
            self.assertEqual(group["chat_id_masked"], "***")
            self.assertNotIn("chat_id", group)
            self.assertNotIn("app_id", before)
            self.assertNotIn("app_secret", before)

            update_admission(
                manifest_path,
                hermes_root=root,
                profile_key="jingli-ai",
                group_key=group["group_key"],
                policy="open",
                require_mention=False,
            )
            add_user(
                manifest_path,
                hermes_root=root,
                profile_key="jingli-ai",
                group_key=group["group_key"],
                open_id="ou_second",
            )
            remove_user(
                manifest_path,
                hermes_root=root,
                profile_key="jingli-ai",
                group_key=group["group_key"],
                open_id="ou_allowed",
            )

            after = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            profile = after.profiles["jingli-ai"]
            self.assertEqual(profile.app_id, "cli_0")
            self.assertEqual(profile.app_secret, "secret_0")
            self.assertEqual(profile.group_rules[0].policy, "open")
            self.assertFalse(profile.group_rules[0].require_mention)
            self.assertEqual(profile.group_rules[0].allowed_users, ("ou_second",))

    def test_profile_admission_rejects_empty_allowlist_after_user_removal(self) -> None:
        read_admission = getattr(feishu_profiles, "read_profile_admission", None)
        remove_user = getattr(feishu_profiles, "remove_profile_admission_user", None)
        self.assertTrue(callable(read_admission))
        self.assertTrue(callable(remove_user))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            group_key = read_admission(
                manifest_path,
                hermes_root=root,
                profile_key="jingli-ai",
            )["groups"][0]["group_key"]

            with self.assertRaisesRegex(FeishuProfilesManifestError, "allowed_users"):
                remove_user(
                    manifest_path,
                    hermes_root=root,
                    profile_key="jingli-ai",
                    group_key=group_key,
                    open_id="ou_allowed",
                )

    def test_profile_admission_cli_outputs_json_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            output = io.StringIO()

            try:
                with redirect_stdout(output):
                    result = feishu_profiles.main(
                        [
                            "--manifest",
                            str(manifest_path),
                            "--hermes-root",
                            str(root),
                            "admission-get",
                            "--profile",
                            "jingli-ai",
                        ]
                    )
            except SystemExit as exc:
                self.fail(f"admission-get CLI is unavailable: {exc}")

            self.assertEqual(result, 0)
            payload = yaml.safe_load(output.getvalue())
            self.assertEqual(payload["profile_key"], "jingli-ai")
            self.assertEqual(payload["groups"][0]["policy"], "allowlist")
            self.assertNotIn("cli_0", output.getvalue())
            self.assertNotIn("secret_0", output.getvalue())
            self.assertNotIn("oc_0", output.getvalue())

    def test_describe_cli_is_ascii_safe_for_windows_powershell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            raw["profiles"]["jingli-ai"]["display_name"] = "静里AI管家"
            manifest_path.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            output = io.StringIO()

            with redirect_stdout(output):
                result = feishu_profiles.main(
                    [
                        "--manifest",
                        str(manifest_path),
                        "--hermes-root",
                        str(root),
                        "describe",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertTrue(output.getvalue().isascii())
            payload = json.loads(output.getvalue())
            self.assertEqual(payload[0]["display_name"], "静里AI管家")

    def test_admission_group_add_remove_roundtrip_preserves_credentials(self) -> None:
        add_group = getattr(feishu_profiles, "add_profile_admission_group", None)
        remove_group = getattr(feishu_profiles, "remove_profile_admission_group", None)
        self.assertTrue(callable(add_group))
        self.assertTrue(callable(remove_group))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            new_chat = "oc_newstore1234567890"

            payload = add_group(
                manifest_path,
                hermes_root=root,
                profile_key="manxin-ai",
                chat_id=new_chat,
            )
            self.assertEqual(len(payload["groups"]), 2)
            self.assertNotIn("app_id", payload)
            self.assertNotIn("app_secret", payload)
            reloaded = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            self.assertEqual(reloaded.profiles["manxin-ai"].app_id, "cli_1")
            self.assertEqual(reloaded.profiles["manxin-ai"].app_secret, "secret_1")
            new_rule = reloaded.profiles["manxin-ai"].group_rules[1]
            self.assertEqual(new_rule.chat_id, new_chat)
            self.assertEqual(new_rule.policy, "open")
            self.assertEqual(new_rule.allowed_users, ())
            self.assertTrue(new_rule.require_mention)

            payload_again = add_group(
                manifest_path,
                hermes_root=root,
                profile_key="manxin-ai",
                chat_id=new_chat,
            )
            self.assertEqual(len(payload_again["groups"]), 2)

            payload_removed = remove_group(
                manifest_path,
                hermes_root=root,
                profile_key="manxin-ai",
                chat_id=new_chat,
            )
            self.assertEqual(len(payload_removed["groups"]), 1)
            final = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            self.assertEqual(len(final.profiles["manxin-ai"].group_rules), 1)

            payload_noop = remove_group(
                manifest_path,
                hermes_root=root,
                profile_key="manxin-ai",
                chat_id="oc_missing000000000000",
            )
            self.assertEqual(len(payload_noop["groups"]), 1)

    def test_admission_add_group_rejects_chat_owned_by_other_profile(self) -> None:
        add_group = getattr(feishu_profiles, "add_profile_admission_group", None)
        self.assertTrue(callable(add_group))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)

            with self.assertRaisesRegex(FeishuProfilesManifestError, "already"):
                add_group(
                    manifest_path,
                    hermes_root=root,
                    profile_key="manxin-ai",
                    chat_id="oc_0",
                )

    def test_admission_add_group_rejects_invalid_chat_id(self) -> None:
        add_group = getattr(feishu_profiles, "add_profile_admission_group", None)
        self.assertTrue(callable(add_group))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)

            with self.assertRaisesRegex(FeishuProfilesManifestError, "chat_id"):
                add_group(
                    manifest_path,
                    hermes_root=root,
                    profile_key="manxin-ai",
                    chat_id="not-a-feishu-chat-id",
                )

    def test_admission_remove_group_allows_emptying_profile(self) -> None:
        remove_group = getattr(feishu_profiles, "remove_profile_admission_group", None)
        self.assertTrue(callable(remove_group))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)

            payload = remove_group(
                manifest_path,
                hermes_root=root,
                profile_key="manxin-ai",
                chat_id="oc_1",
            )
            self.assertEqual(payload["groups"], [])
            # 空准入清单的 manifest 仍可加载（set-credentials 新建 profile 即此状态）。
            reloaded = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            self.assertEqual(reloaded.profiles["manxin-ai"].group_rules, ())
            self.assertEqual(reloaded.profiles["manxin-ai"].app_id, "cli_1")

    def test_manifest_allows_profile_without_admission_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            raw["profiles"]["jingli-ai"]["group_rules"] = []
            manifest_path.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

            manifest = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            self.assertEqual(manifest.profiles["jingli-ai"].group_rules, ())
            credentials_json, chat_map_json = build_backend_environment(manifest)
            self.assertNotIn("oc_0", chat_map_json)

    def test_set_credentials_creates_default_soul_for_new_profile(self) -> None:
        set_credentials = getattr(feishu_profiles, "set_profile_credentials", None)
        self.assertTrue(callable(set_credentials))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)

            set_credentials(
                manifest_path,
                profile_key="newstore-ai",
                app_id="cli_newstore",
                app_secret="newstore_secret",
                display_name="新店 AI 管家",
                hermes_root=root,
            )

            soul_path = root / "config" / "profiles" / "newstore-ai" / "SOUL.md"
            self.assertTrue(soul_path.is_file())
            self.assertIn("新店 AI 管家", soul_path.read_text(encoding="utf-8"))
            # 空 group_rules 的新 profile 不再让整个 manifest 失效：
            # 凭据写入后立即可加载、可生成后端映射。
            manifest = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            self.assertEqual(manifest.profiles["newstore-ai"].group_rules, ())
            credentials_json, _ = build_backend_environment(manifest)
            self.assertIn("cli_newstore", credentials_json)
            # 已有 SOUL 内容不被覆盖。
            soul_path.write_text("# 自定义 SOUL\n", encoding="utf-8")
            set_credentials(
                manifest_path,
                profile_key="newstore-ai",
                app_id="cli_newstore",
                app_secret="newstore_secret2",
                hermes_root=root,
            )
            self.assertEqual(
                soul_path.read_text(encoding="utf-8"),
                "# 自定义 SOUL\n",
            )

    def test_admission_group_cli_outputs_json_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            new_chat = "oc_clistore00000000001"
            output = io.StringIO()

            try:
                with redirect_stdout(output):
                    result = feishu_profiles.main(
                        [
                            "--manifest",
                            str(manifest_path),
                            "--hermes-root",
                            str(root),
                            "admission-add-group",
                            "--profile",
                            "manxin-ai",
                            "--chat-id",
                            new_chat,
                        ]
                    )
            except SystemExit as exc:
                self.fail(f"admission-add-group CLI is unavailable: {exc}")

            self.assertEqual(result, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["profile_key"], "manxin-ai")
            self.assertEqual(len(payload["groups"]), 2)
            self.assertNotIn("cli_1", output.getvalue())
            self.assertNotIn("secret_1", output.getvalue())
            self.assertNotIn(new_chat, output.getvalue())

            output_removed = io.StringIO()
            with redirect_stdout(output_removed):
                result_removed = feishu_profiles.main(
                    [
                        "--manifest",
                        str(manifest_path),
                        "--hermes-root",
                        str(root),
                        "admission-remove-group",
                        "--profile",
                        "manxin-ai",
                        "--chat-id",
                        new_chat,
                    ]
                )
            self.assertEqual(result_removed, 0)
            payload_removed = json.loads(output_removed.getvalue())
            self.assertEqual(len(payload_removed["groups"]), 1)

    def _write_repo_runtime_tree(self, root: Path) -> Path:
        """Build a repo-like hermes tree (SOUL base, templates, plugin, skills)."""

        config_dir = root / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "hermes.SOUL.md").write_text(
            "# Pan Hotel Watch 飞书助手\n\n基础 SOUL 规则。\n",
            encoding="utf-8",
        )
        (root / "config" / "hermes.MEMORY.template.md").write_text(
            "# Memory 模板\n",
            encoding="utf-8",
        )
        (root / "config" / "hermes.USER.template.md").write_text(
            "# User 模板\n",
            encoding="utf-8",
        )
        plugin_dir = root / "hermes_plugin" / "pan-hotel-feishu-session"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text("name: pan-hotel-feishu-session\n", encoding="utf-8")
        for skill_name in ("pan-hotel-watch", "feishu-reply-restriction"):
            skill_dir = root / "skills" / skill_name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(f"# {skill_name}\n", encoding="utf-8")
        return root

    def _runtime_profile_root(self, root: Path, profile_key: str = "jingli-ai") -> Path:
        hermes_base = root / "runtime-hermes"
        profile_root = hermes_base / "profiles" / profile_key
        profile_root.mkdir(parents=True, exist_ok=True)
        return profile_root

    def test_apply_bootstraps_full_runtime_for_new_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._write_repo_runtime_tree(Path(temp_dir))
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            profile_root = self._runtime_profile_root(root)
            python_exe = root / "agent-venv" / "Scripts" / "python.exe"
            python_exe.parent.mkdir(parents=True, exist_ok=True)
            python_exe.touch()

            apply_profile_configuration(
                manifest.profiles["jingli-ai"],
                profile_root,
                service_token="shared-service-token",
                hermes_root=root,
                python_exe=python_exe,
                watch_api_base_url="http://127.0.0.1:8100",
            )

            config = yaml.safe_load((profile_root / "config.yaml").read_text(encoding="utf-8"))
            mcp = config["mcp_servers"]["pan-hotel-watch"]
            self.assertEqual(mcp["command"], str(python_exe).replace("\\", "/"))
            self.assertEqual(mcp["args"], ["-m", "pan_hotel_mcp"])
            self.assertEqual(mcp["cwd"], str(root.resolve()).replace("\\", "/"))
            self.assertEqual(mcp["timeout"], 300)
            env = mcp["env"]
            self.assertEqual(env["WATCH_API_BASE_URL"], "http://127.0.0.1:8100")
            self.assertEqual(env["WATCH_API_BROWSER_TIMEOUT"], "300")
            self.assertEqual(env["WATCH_API_SHARED_SECRET"], "${WATCH_API_SHARED_SECRET}")
            self.assertEqual(env["WATCH_API_SUPERVISOR_KEY"], "${WATCH_API_SUPERVISOR_KEY}")
            self.assertEqual(env["PYTHONPATH"], str(root.resolve()).replace("\\", "/"))
            self.assertEqual(env["WATCH_DEFAULT_EBK_GROUP"], "ctrip_ebooking_data_center")
            self.assertIn("pan-hotel-feishu-session", config["plugins"]["enabled"])
            self.assertEqual(config["display"]["language"], "zh")
            self.assertIs(config["group_sessions_per_user"], True)
            self.assertEqual(config["timezone"], "Asia/Shanghai")
            compression = config["compression"]
            self.assertTrue(compression["enabled"])
            self.assertEqual(compression["threshold"], 0.45)
            self.assertEqual(compression["protect_last_n"], 24)
            self.assertEqual(
                config["platforms"]["feishu"]["extra"]["group_rules"]["oc_0"]["policy"],
                "allowlist",
            )

            soul = (profile_root / "SOUL.md").read_text(encoding="utf-8")
            self.assertIn("基础 SOUL 规则", soul)
            self.assertIn("## profile-specific SOUL", soul)
            self.assertIn("# jingli-ai", soul)

            plugin_dst = profile_root / "plugins" / "pan-hotel-feishu-session"
            self.assertTrue((plugin_dst / "plugin.yaml").is_file())
            for skill_name in ("pan-hotel-watch", "feishu-reply-restriction"):
                self.assertTrue(
                    (profile_root / "skills" / skill_name / "SKILL.md").is_file(),
                    skill_name,
                )
            memories = profile_root / "memories"
            self.assertIn("# Memory 模板", (memories / "MEMORY.md").read_text(encoding="utf-8"))
            self.assertIn("# User 模板", (memories / "USER.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(feishu_profiles.os.name == "nt", "Windows junction test")
    def test_apply_links_plugin_and_skills_as_junctions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._write_repo_runtime_tree(Path(temp_dir))
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            profile_root = self._runtime_profile_root(root)
            python_exe = root / "agent-venv" / "Scripts" / "python.exe"
            python_exe.parent.mkdir(parents=True, exist_ok=True)
            python_exe.touch()

            apply_profile_configuration(
                manifest.profiles["jingli-ai"],
                profile_root,
                hermes_root=root,
                python_exe=python_exe,
            )

            plugin_dst = profile_root / "plugins" / "pan-hotel-feishu-session"
            self.assertEqual(
                Path(plugin_dst).resolve(),
                (root / "hermes_plugin" / "pan-hotel-feishu-session").resolve(),
            )
            skill_dst = profile_root / "skills" / "pan-hotel-watch"
            self.assertEqual(
                Path(skill_dst).resolve(),
                (root / "skills" / "pan-hotel-watch").resolve(),
            )

    def test_apply_reconciles_drifted_config_without_touching_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._write_repo_runtime_tree(Path(temp_dir))
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            profile_root = self._runtime_profile_root(root)
            python_exe = root / "agent-venv" / "Scripts" / "python.exe"
            python_exe.parent.mkdir(parents=True, exist_ok=True)
            python_exe.touch()
            stale = root / "Desktop-repo"
            stale.mkdir()
            (profile_root / "config.yaml").write_text(
                yaml.safe_dump(
                    {
                        "agent": {"max_turns": 50},
                        "model": {"default": "old-model", "provider": "custom:pan-hotel-llm-1"},
                        "providers": {"pan-hotel-llm-1": {"name": "keep-me"}},
                        "mcp_servers": {
                            "pan-hotel-watch": {
                                "command": "C:/Users/x/Desktop/venv/Scripts/python.exe",
                                "args": ["-m", "pan_hotel_mcp"],
                                "cwd": str(stale),
                                "env": {
                                    "WATCH_API_BASE_URL": "http://127.0.0.1:8010",
                                    "PYTHONPATH": str(stale),
                                },
                            }
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            apply_profile_configuration(
                manifest.profiles["jingli-ai"],
                profile_root,
                hermes_root=root,
                python_exe=python_exe,
                watch_api_base_url="http://127.0.0.1:8100",
            )

            config = yaml.safe_load((profile_root / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["agent"]["max_turns"], 50)
            self.assertEqual(config["model"]["provider"], "custom:pan-hotel-llm-1")
            self.assertEqual(config["providers"]["pan-hotel-llm-1"]["name"], "keep-me")
            mcp = config["mcp_servers"]["pan-hotel-watch"]
            self.assertEqual(mcp["command"], str(python_exe).replace("\\", "/"))
            self.assertEqual(mcp["cwd"], str(root.resolve()).replace("\\", "/"))
            self.assertEqual(mcp["env"]["WATCH_API_BASE_URL"], "http://127.0.0.1:8100")
            self.assertEqual(
                mcp["env"]["WATCH_API_SHARED_SECRET"],
                "${WATCH_API_SHARED_SECRET}",
            )
            self.assertEqual(config["timezone"], "Asia/Shanghai")

    def test_apply_preserves_local_plugin_copy_and_existing_memories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._write_repo_runtime_tree(Path(temp_dir))
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            profile_root = self._runtime_profile_root(root)
            python_exe = root / "agent-venv" / "Scripts" / "python.exe"
            python_exe.parent.mkdir(parents=True, exist_ok=True)
            python_exe.touch()
            local_plugin = profile_root / "plugins" / "pan-hotel-feishu-session"
            local_plugin.mkdir(parents=True)
            (local_plugin / "local-edit.txt").write_text("keep", encoding="utf-8")
            memories = profile_root / "memories"
            memories.mkdir()
            (memories / "MEMORY.md").write_text("# 已有记忆\n", encoding="utf-8")

            apply_profile_configuration(
                manifest.profiles["jingli-ai"],
                profile_root,
                hermes_root=root,
                python_exe=python_exe,
            )

            self.assertTrue((local_plugin / "local-edit.txt").is_file())
            self.assertEqual(
                (memories / "MEMORY.md").read_text(encoding="utf-8"),
                "# 已有记忆\n",
            )
            # USER.md 缺失仍应补齐。
            self.assertTrue((memories / "USER.md").is_file())

    def test_apply_without_hermes_root_keeps_legacy_stub_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_feishu_profiles_manifest(
                self._write_manifest(root),
                hermes_root=root,
            )
            profile_root = self._runtime_profile_root(root)

            apply_profile_configuration(
                manifest.profiles["jingli-ai"],
                profile_root,
                service_token="token",
            )

            config = yaml.safe_load((profile_root / "config.yaml").read_text(encoding="utf-8"))
            self.assertNotIn("mcp_servers", config)
            self.assertIn("platforms", config)

    def test_remove_profile_entry_is_idempotent_and_keeps_last_profile(self) -> None:
        remove_profile_entry = getattr(feishu_profiles, "remove_profile_entry", None)
        self.assertTrue(callable(remove_profile_entry))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)

            remove_profile_entry(manifest_path, profile_key="manxin-ai")
            manifest = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            self.assertEqual(list(manifest.profiles), ["jingli-ai"])

            # 重复删除幂等：条目已不存在时静默成功。
            remove_profile_entry(manifest_path, profile_key="manxin-ai")
            manifest = load_feishu_profiles_manifest(manifest_path, hermes_root=root)
            self.assertEqual(list(manifest.profiles), ["jingli-ai"])

            # 清空最后一个 profile 会让整个 manifest 失效（后续 validate/apply
            # 全挂），必须拒绝并提示走人工清理。
            with self.assertRaisesRegex(FeishuProfilesManifestError, "最后一个"):
                remove_profile_entry(manifest_path, profile_key="jingli-ai")

    def test_migrates_existing_profile_env_without_changing_admission_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template_path = self._write_manifest(root)
            runtime_root = root / "runtime"
            expected = {
                "jingli-ai": ("open", "false", "oc_current_0", "ou_a,ou_b"),
                "manxin-ai": ("allowlist", "true", "oc_current_1", "ou_c"),
            }
            for index, (profile_key, state) in enumerate(expected.items()):
                policy, require_mention, chat_id, allowed_users = state
                profile_root = runtime_root / "profiles" / profile_key
                profile_root.mkdir(parents=True)
                (profile_root / ".env").write_text(
                    "\n".join(
                        (
                            f"FEISHU_APP_ID=cli_current_{index}",
                            f"FEISHU_APP_SECRET=current_secret_{index}",
                            f"FEISHU_GROUP_POLICY={policy}",
                            f"FEISHU_ALLOWED_CHATS={chat_id}",
                            f"FEISHU_ALLOWED_USERS={allowed_users}",
                            f"FEISHU_REQUIRE_MENTION={require_mention}",
                        )
                    )
                    + "\n",
                    encoding="utf-8",
                )
            destination = root / "generated" / "feishu-profiles.yaml"

            migrate_existing_profiles(
                template_path=template_path,
                destination_path=destination,
                hermes_base=runtime_root,
                hermes_root=root,
            )

            migrated = load_feishu_profiles_manifest(destination, hermes_root=root)
            jingli = migrated.profiles["jingli-ai"]
            manxin = migrated.profiles["manxin-ai"]
            self.assertEqual(jingli.app_secret, "current_secret_0")
            self.assertEqual(jingli.group_rules[0].policy, "open")
            self.assertFalse(jingli.require_mention)
            self.assertEqual(jingli.group_rules[0].allowed_users, ("ou_a", "ou_b"))
            self.assertEqual(manxin.group_rules[0].policy, "allowlist")
            self.assertTrue(manxin.require_mention)
            self.assertEqual(manxin.group_rules[0].allowed_users, ("ou_c",))


if __name__ == "__main__":
    unittest.main()
