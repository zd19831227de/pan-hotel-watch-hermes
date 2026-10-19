from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class _IsolatedBrowserState:
    """隔离 browser-state 目录，避免污染真实 browser_sessions.json。"""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {
            "BROWSER_STATE_DIR": os.environ.get("BROWSER_STATE_DIR"),
        }
        os.environ["BROWSER_STATE_DIR"] = str(Path(self._tmp.name) / "browser-state")
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))
        return self

    def __exit__(self, *exc):
        for key, previous in self._saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))
        self._tmp.cleanup()
        return False


class BrowserSessionChatUrlTests(unittest.TestCase):
    """F4-B — llm_chat 会话级 chat_url：可填写、http(s) 校验、仅 llm_chat 有效。"""

    def _client(self) -> TestClient:
        main = importlib.import_module("app.main")
        return TestClient(main.create_app())

    def _llm_chat_payload(self, **overrides) -> dict:
        payload = {
            "platform_account_id": "doubao_main",
            "platform": "doubao",
            "intended_use": "llm_chat",
            "session_port": 9401,
        }
        payload.update(overrides)
        return payload

    def test_llm_chat_session_accepts_and_returns_chat_url(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            created = client.post(
                "/api/v1/browser-sessions",
                json=self._llm_chat_payload(
                    chat_url="https://www.doubao.com/custom/chat/"
                ),
            )
            self.assertEqual(created.status_code, 200)
            self.assertEqual(
                created.json()["chat_url"], "https://www.doubao.com/custom/chat/"
            )
            patched = client.patch(
                f"/api/v1/browser-sessions/{created.json()['browser_session_id']}",
                json={"chat_url": "https://chat.example.com/entry"},
            )
            self.assertEqual(patched.status_code, 200)
            self.assertEqual(patched.json()["chat_url"], "https://chat.example.com/entry")

    def test_llm_chat_session_without_chat_url_keeps_null(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            created = client.post(
                "/api/v1/browser-sessions", json=self._llm_chat_payload()
            )
            self.assertEqual(created.status_code, 200)
            self.assertIsNone(created.json()["chat_url"])

    def test_invalid_chat_url_rejected(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            for bad_url in ("ftp://example.com/chat", "not-a-url", "http://"):
                response = client.post(
                    "/api/v1/browser-sessions",
                    json=self._llm_chat_payload(platform_account_id="bad1", chat_url=bad_url),
                )
                self.assertEqual(response.status_code, 422, bad_url)

    def test_non_llm_chat_session_rejects_chat_url(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            response = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_ebk",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "session_port": 9402,
                    "chat_url": "https://www.doubao.com/chat/",
                },
            )
            self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
