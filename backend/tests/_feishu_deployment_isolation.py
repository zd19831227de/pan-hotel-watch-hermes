"""Shared test isolation for Hermes admission deployment paths.

群绑定生命周期会同步 Hermes 准入清单并热重载 Gateway；测试绝不能写真实
manifest 或重启真实网关（本机装有完整 Hermes 部署，默认回退路径全部有效）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock


class isolated_feishu_deployment:  # noqa: N801 - context manager, used like a function
    def __init__(self, *, profiles: tuple[str, ...] = ("jingli-ai", "manxin-ai")):
        self._profile_keys = profiles
        self.process_calls: list[list[str]] = []
        self.admitted: dict[str, list[str]] = {
            profile_key: [] for profile_key in profiles
        }

    def _fake_run(self, command, **kwargs):
        command = [str(item) for item in command]
        self.process_calls.append(command)
        stdout = "ok"
        if any(
            action in command
            for action in (
                "admission-add-group",
                "admission-remove-group",
                "admission-get",
            )
        ):
            profile_key = command[command.index("--profile") + 1]
            chats = self.admitted.setdefault(profile_key, [])
            if "admission-add-group" in command and "--chat-id" in command:
                chat_id = command[command.index("--chat-id") + 1]
                if chat_id not in chats:
                    chats.append(chat_id)
            elif "admission-remove-group" in command and "--chat-id" in command:
                chat_id = command[command.index("--chat-id") + 1]
                if chat_id in chats:
                    chats.remove(chat_id)
            stdout = json.dumps(
                {
                    "profile_key": profile_key,
                    "display_name": profile_key,
                    "groups": [
                        {
                            "group_key": f"{index:016x}",
                            "chat_id_masked": "oc_1...room",
                            "policy": "open",
                            "allowed_users": [],
                            "require_mention": True,
                        }
                        for index, _ in enumerate(chats)
                    ],
                },
                ensure_ascii=False,
            )
        return mock.Mock(returncode=0, stdout=stdout, stderr="")

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        temp_root = Path(self._tmp.name)
        manifest_path = temp_root / "feishu-profiles.yaml"
        manifest_path.write_text("profiles: {}\n", encoding="utf-8")
        hermes_base = temp_root / "hermes"
        for profile_key in self._profile_keys:
            (hermes_base / "profiles" / profile_key).mkdir(parents=True)
        python_exe = temp_root / "python.exe"
        hermes_exe = temp_root / "hermes.exe"
        python_exe.touch()
        hermes_exe.touch()
        deployment_env_keys = (
            "PAN_HOTEL_FEISHU_PROFILES_MANIFEST",
            "PAN_HOTEL_HERMES_BASE_PATH",
            "PAN_HOTEL_HERMES_PYTHON_EXE",
            "PAN_HOTEL_HERMES_EXE",
        )
        self._previous_env = {
            key: os.environ.get(key) for key in deployment_env_keys
        }
        os.environ.update(
            {
                "PAN_HOTEL_FEISHU_PROFILES_MANIFEST": str(manifest_path),
                "PAN_HOTEL_HERMES_BASE_PATH": str(hermes_base),
                "PAN_HOTEL_HERMES_PYTHON_EXE": str(python_exe),
                "PAN_HOTEL_HERMES_EXE": str(hermes_exe),
            }
        )
        self._run_patch = mock.patch("subprocess.run", side_effect=self._fake_run)
        self._run_patch.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._run_patch.stop()
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()
        return False
