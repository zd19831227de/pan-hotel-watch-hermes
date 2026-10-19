from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from client.feishu_session_bridge import load_session, merge_feishu_session, write_session


class FeishuSessionBridgeTests(unittest.TestCase):
    """Bridge file + env fallback behavior for ``merge_feishu_session``.

    These tests isolate ``HERMES_HOME`` (and the per-call session env vars) in a
    temp directory so they never read or write the operator's real hermes home.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name) / "hermes"
        # ``bridge_dir`` reads HERMES_HOME first; point it at our temp root.
        self._previous_hermes_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(self._root)
        # Snapshot the session env vars the merge function falls back to.
        self._session_env_keys = (
            "HERMES_SESSION_MESSAGE_ID",
            "HERMES_SESSION_CHAT_ID",
            "HERMES_SESSION_ID",
            "HERMES_ACTOR_ID",
            "HERMES_SESSION_USER_ID",
        )
        self._previous_session_env = {key: os.environ.get(key) for key in self._session_env_keys}
        for key in self._session_env_keys:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        if self._previous_hermes_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._previous_hermes_home
        for key, value in self._previous_session_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    @property
    def _bridge_root(self) -> Path:
        return self._root / "feishu-session-bridge"

    def test_write_and_load_session_by_chat_id(self) -> None:
        write_session(
            chat_id="oc_test_group",
            session_id="sess_abc",
            message_id="om_msg_123",
            open_id="ou_user_1",
        )
        payload = load_session(chat_id="oc_test_group")
        self.assertEqual(payload["message_id"], "om_msg_123")
        self.assertEqual(payload["open_id"], "ou_user_1")

    def test_merge_feishu_session_reads_bridge(self) -> None:
        write_session(chat_id="oc_group", message_id="om_999", open_id="ou_1")
        merged = merge_feishu_session({"chat_id": "oc_group"})
        self.assertEqual(merged["message_id"], "om_999")
        self.assertEqual(merged["open_id"], "ou_1")

    def test_merge_feishu_session_keeps_explicit_message_id(self) -> None:
        write_session(chat_id="oc_group", message_id="om_bridge")
        merged = merge_feishu_session({"chat_id": "oc_group", "message_id": "om_explicit"})
        self.assertEqual(merged["message_id"], "om_explicit")

    def test_merge_feishu_session_reads_env(self) -> None:
        os.environ["HERMES_SESSION_MESSAGE_ID"] = "om_env"
        os.environ["HERMES_SESSION_CHAT_ID"] = "oc_env"
        merged = merge_feishu_session({})
        self.assertEqual(merged["message_id"], "om_env")
        self.assertEqual(merged["chat_id"], "oc_env")


if __name__ == "__main__":
    unittest.main()
