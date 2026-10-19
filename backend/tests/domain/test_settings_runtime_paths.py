from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path


class SettingsRuntimePathTests(unittest.TestCase):
    def test_relative_browser_state_dir_resolves_under_project_root(self) -> None:
        previous = os.environ.get("BROWSER_STATE_DIR")
        os.environ["BROWSER_STATE_DIR"] = ".runtime/browser-state-path-test"
        try:
            settings_mod = importlib.reload(importlib.import_module("app.core.settings"))
            settings_mod.get_settings.cache_clear()
            settings = settings_mod.get_settings()
            root = settings_mod._project_root()
            resolved = Path(settings.browser_state_dir)
            self.assertTrue(resolved.is_absolute())
            self.assertEqual(resolved, (root / ".runtime/browser-state-path-test").resolve())
        finally:
            if previous is None:
                os.environ.pop("BROWSER_STATE_DIR", None)
            else:
                os.environ["BROWSER_STATE_DIR"] = previous
            settings_mod = importlib.reload(importlib.import_module("app.core.settings"))
            settings_mod.get_settings.cache_clear()

    def test_absolute_browser_state_dir_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("BROWSER_STATE_DIR")
            os.environ["BROWSER_STATE_DIR"] = tmp
            try:
                settings_mod = importlib.reload(importlib.import_module("app.core.settings"))
                settings_mod.get_settings.cache_clear()
                settings = settings_mod.get_settings()
                self.assertEqual(
                    Path(settings.browser_state_dir).resolve(),
                    Path(tmp).resolve(),
                )
            finally:
                if previous is None:
                    os.environ.pop("BROWSER_STATE_DIR", None)
                else:
                    os.environ["BROWSER_STATE_DIR"] = previous
                settings_mod = importlib.reload(importlib.import_module("app.core.settings"))
                settings_mod.get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
