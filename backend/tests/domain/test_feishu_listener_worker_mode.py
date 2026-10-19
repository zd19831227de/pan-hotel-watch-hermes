from __future__ import annotations

import importlib
import unittest
from types import SimpleNamespace


def _reload():
    return importlib.reload(importlib.import_module("app.workers.feishu_listener"))


def _settings(mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        app_name="Pan Hotel Watch",
        environment="local",
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
        feishu_agent_mode=mode,
    )


class _FakeListener:
    started = False

    def start(self) -> None:
        type(self).started = True


class FeishuListenerWorkerModeTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeListener.started = False

    def test_hermes_mode_does_not_start_listener(self) -> None:
        mod = _reload()
        mod.configure_windows_subprocess_event_loop_policy = lambda: None
        mod.get_settings = lambda: _settings("hermes")
        mod.FeishuLongConnectionListener = _FakeListener

        mod.main()

        self.assertFalse(_FakeListener.started)

    def test_builtin_mode_starts_listener(self) -> None:
        mod = _reload()
        mod.configure_windows_subprocess_event_loop_policy = lambda: None
        mod.get_settings = lambda: _settings("builtin")
        mod.FeishuLongConnectionListener = _FakeListener

        mod.main()

        self.assertTrue(_FakeListener.started)


if __name__ == "__main__":
    unittest.main()
