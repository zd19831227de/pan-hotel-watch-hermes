from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


class BrowserSessionConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_browser_state_dir = os.environ.get("BROWSER_STATE_DIR")
        os.environ["BROWSER_STATE_DIR"] = self._tmp.name
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        self.sessions = importlib.reload(importlib.import_module("app.domain.browser.sessions"))

    def tearDown(self) -> None:
        if self._previous_browser_state_dir is None:
            os.environ.pop("BROWSER_STATE_DIR", None)
        else:
            os.environ["BROWSER_STATE_DIR"] = self._previous_browser_state_dir
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))
        self._tmp.cleanup()

    def test_two_store_instances_create_different_accounts_without_lost_update(self) -> None:
        first_store = self.sessions.BrowserSessionStore()
        second_store = self.sessions.BrowserSessionStore()
        barrier = threading.Barrier(2)

        def create(
            store,
            *,
            browser_session_id: str,
            platform_account_id: str,
            session_port: int,
        ) -> None:
            barrier.wait(timeout=5)
            store.create(
                self.sessions.BrowserSessionCreate(
                    browser_session_id=browser_session_id,
                    platform_account_id=platform_account_id,
                    shop_key="shop_concurrency_test",
                    session_port=session_port,
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    create,
                    first_store,
                    browser_session_id="browser-concurrent-a",
                    platform_account_id="concurrent-a",
                    session_port=9631,
                ),
                executor.submit(
                    create,
                    second_store,
                    browser_session_id="browser-concurrent-b",
                    platform_account_id="concurrent-b",
                    session_port=9632,
                ),
            ]
            for future in futures:
                future.result(timeout=10)

        state_path = Path(self._tmp.name) / "browser_sessions.json"
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(payload["sessions"]),
            {"browser-concurrent-a", "browser-concurrent-b"},
        )
        reloaded = self.sessions.BrowserSessionStore()
        self.assertEqual(
            {session.browser_session_id for session in reloaded.list()},
            {"browser-concurrent-a", "browser-concurrent-b"},
        )
        self.assertEqual(
            {session.browser_session_id for session in first_store.list()},
            {"browser-concurrent-a", "browser-concurrent-b"},
        )
        self.assertEqual(
            {session.browser_session_id for session in second_store.list()},
            {"browser-concurrent-a", "browser-concurrent-b"},
        )

    def test_partial_write_failure_keeps_previous_json_intact(self) -> None:
        store = self.sessions.BrowserSessionStore()
        store.create(
            self.sessions.BrowserSessionCreate(
                browser_session_id="browser-stable",
                platform_account_id="stable-account",
                display_name="before",
                shop_key="shop_concurrency_test",
                session_port=9633,
            )
        )
        state_path = Path(self._tmp.name) / "browser_sessions.json"
        original_write_text = Path.write_text

        def partial_write(path: Path, data: str, *args, **kwargs) -> int:
            if "browser_sessions.json" in path.name:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8") as handle:
                    handle.write('{"sessions":')
                raise OSError("simulated partial write")
            return original_write_text(path, data, *args, **kwargs)

        with patch.object(Path, "write_text", partial_write):
            with self.assertRaisesRegex(OSError, "simulated partial write"):
                store.update(
                    "browser-stable",
                    self.sessions.BrowserSessionPatch(display_name="after"),
                )

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["sessions"]["browser-stable"]["display_name"], "before")

    def test_two_processes_create_without_lost_update(self) -> None:
        state_root = Path(self._tmp.name)
        first_ready = state_root / "first-ready"
        release_first = state_root / "release-first"
        second_done = state_root / "second-done"
        backend_root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["BROWSER_STATE_DIR"] = str(state_root)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(backend_root), env.get("PYTHONPATH", "")) if part
        )

        first_code = f"""
import time
from pathlib import Path
from app.domain.browser import sessions

ready = Path({str(first_ready)!r})
release = Path({str(release_first)!r})
original_save = sessions._save_session_state

def delayed_save(payload):
    ready.write_text('ready', encoding='utf-8')
    while not release.exists():
        time.sleep(0.01)
    original_save(payload)

sessions._save_session_state = delayed_save
sessions.BrowserSessionStore().create(sessions.BrowserSessionCreate(
    browser_session_id='browser-process-a',
    platform_account_id='process-a',
    shop_key='shop_concurrency_test',
    session_port=9634,
))
"""
        second_code = f"""
from pathlib import Path
from app.domain.browser import sessions

sessions.BrowserSessionStore().create(sessions.BrowserSessionCreate(
    browser_session_id='browser-process-b',
    platform_account_id='process-b',
    shop_key='shop_concurrency_test',
    session_port=9635,
))
Path({str(second_done)!r}).write_text('done', encoding='utf-8')
"""

        first = subprocess.Popen(
            [sys.executable, "-c", first_code],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        second: subprocess.Popen[str] | None = None
        try:
            self._wait_for_path(first_ready)
            second = subprocess.Popen(
                [sys.executable, "-c", second_code],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline and not second_done.exists():
                time.sleep(0.01)
            release_first.write_text("release", encoding="utf-8")
            first_stdout, first_stderr = first.communicate(timeout=10)
            second_stdout, second_stderr = second.communicate(timeout=10)
        finally:
            release_first.touch(exist_ok=True)
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)

        self.assertEqual(first.returncode, 0, first_stdout + first_stderr)
        self.assertIsNotNone(second)
        self.assertEqual(second.returncode, 0, second_stdout + second_stderr)
        state_path = state_root / "browser_sessions.json"
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(payload["sessions"]),
            {"browser-process-a", "browser-process-b"},
        )

    def _wait_for_path(self, path: Path, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            time.sleep(0.01)
        self.fail(f"Timed out waiting for subprocess marker: {path.name}")


if __name__ == "__main__":
    unittest.main()
