from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app.core.settings import get_settings
from app.domain.browser.cache_cleanup import (
    PROFILE_CLEANABLE_DIRS,
    PROFILE_CLEANABLE_FILES,
    PROFILE_LOGIN_STATE_ENTRIES,
    PROFILE_NON_CACHE_ENTRIES,
    audit_browser_state,
    audit_playwright_screenshots,
    audit_profile,
    cleanup_browser_state,
    cleanup_playwright_screenshots,
    cleanup_profile,
    classify_browser_profile_entry,
    render_audit_report,
)


def _write(path: Path, size: int = 4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(b"x" * size)


class ClassifyEntryTests(unittest.TestCase):
    def test_cache_dirs_are_cleanable(self) -> None:
        for name in ["Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "Service Worker"]:
            self.assertEqual(classify_browser_profile_entry(name), "cleanable_cache", name)

    def test_login_state_preserved(self) -> None:
        for name in ["Network", "Local State", "Login Data", "Web Data", "Local Storage"]:
            self.assertEqual(classify_browser_profile_entry(name), "login_state", name)

    def test_model_and_learning_non_cache(self) -> None:
        for name in ["screen_ai", "segmentation_platform", "OnDeviceHeadSuggestModel"]:
            self.assertEqual(classify_browser_profile_entry(name), "non_cache_model", name)

    def test_unknown_preserved(self) -> None:
        self.assertEqual(classify_browser_profile_entry("some_unknown_dir"), "unknown_preserve")
        self.assertEqual(classify_browser_profile_entry("component_crx_cache"), "cleanable_cache")


class AuditProfileTests(unittest.TestCase):
    def _make_profile(self, root: Path) -> Path:
        profile = root / "ctrip-ebooking" / "acct-1"
        # cleanable cache
        _write(profile / "Cache" / "data.bin", 100)
        _write(profile / "Code Cache" / "js.bin", 50)
        _write(profile / "component_crx_cache" / "crx", 200)
        _write(profile / "GrShaderCache" / "shader", 10)
        # login state (must be preserved)
        _write(profile / "Default" / "Network" / "Cookies", 30)
        _write(profile / "Default" / "Login Data", 5)
        _write(profile / "Local State", 2)
        # non-cache model (must be preserved)
        _write(profile / "screen_ai" / "model.bin", 500)
        # unknown (preserved by default)
        _write(profile / "Default" / "MysteryDir" / "x", 7)
        # a cleanable cache inside Default
        _write(profile / "Default" / "Cache" / "d", 40)
        return profile

    def test_audit_categorizes_and_sizes(self) -> None:
        with TemporaryDirectory() as tmp:
            profile = self._make_profile(Path(tmp))
            audit = audit_profile(profile)
            names_cleanable = {e.name for e in audit.cleanable}
            self.assertIn("Cache", names_cleanable)
            self.assertIn("component_crx_cache", names_cleanable)
            self.assertGreater(audit.cleanable_bytes, 0)
            self.assertGreater(audit.login_state_bytes, 0)
            self.assertGreater(audit.non_cache_bytes, 0)
            self.assertGreater(audit.total_bytes, audit.cleanable_bytes)
            # cleanable fraction reported
            self.assertGreater(audit.cleanable_fraction, 0.0)


class CleanupProfileTests(unittest.TestCase):
    def _make_profile(self, root: Path) -> Path:
        profile = root / "ctrip-ebooking" / "acct-1"
        _write(profile / "Cache" / "data.bin", 100)
        _write(profile / "component_crx_cache" / "crx", 200)
        _write(profile / "Default" / "Network" / "Cookies", 30)
        _write(profile / "Default" / "Login Data", 5)
        _write(profile / "Local State", 2)
        _write(profile / "screen_ai" / "model.bin", 500)
        _write(profile / "Default" / "Cache" / "d", 40)
        return profile

    def test_dry_run_deletes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            profile = self._make_profile(Path(tmp))
            before = sum(f.stat().st_size for f in profile.rglob("*") if f.is_file())
            result = cleanup_profile(profile, dry_run=True)
            after = sum(f.stat().st_size for f in profile.rglob("*") if f.is_file())
            self.assertEqual(before, after)
            self.assertGreater(result.bytes_freed, 0)

    def test_cleanup_removes_cache_preserves_login_and_model(self) -> None:
        with TemporaryDirectory() as tmp:
            profile = self._make_profile(Path(tmp))
            result = cleanup_profile(profile, dry_run=False)
            # cache contents gone
            self.assertFalse(any((profile / "Cache").glob("*")))
            self.assertFalse(any((profile / "component_crx_cache").glob("*")))
            # login state intact
            self.assertTrue((profile / "Default" / "Network" / "Cookies").exists())
            self.assertTrue((profile / "Default" / "Login Data").exists())
            self.assertTrue((profile / "Local State").exists())
            # non-cache model intact
            self.assertTrue((profile / "screen_ai" / "model.bin").exists())
            self.assertGreater(result.bytes_freed, 0)

    def test_cleanup_never_touches_login_even_if_classified(self) -> None:
        with TemporaryDirectory() as tmp:
            profile = self._make_profile(Path(tmp))
            cleanup_profile(profile, dry_run=False)
            # The login-state guard: Network dir + Cookies still present
            self.assertTrue((profile / "Default" / "Network").is_dir())
            self.assertTrue((profile / "Default" / "Network" / "Cookies").exists())


class AuditBrowserStateAndReportTests(unittest.TestCase):
    def test_audit_and_report_cover_platforms_and_profiles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "ctrip-ebooking" / "a1" / "Cache" / "x", 100)
            _write(root / "ctrip-ebooking" / "a1" / "Local State", 2)
            _write(root / "meituan-ebooking" / "m1" / "Code Cache" / "y", 80)
            _write(root / "browser_sessions.json", 10)
            audit = audit_browser_state(root)
            self.assertEqual(sorted(audit.platforms.keys()), ["ctrip-ebooking", "meituan-ebooking"])
            md = render_audit_report(audit)
            self.assertIn("browser-cache-audit", md.lower())
            self.assertIn("ctrip-ebooking", md)
            self.assertIn("meituan-ebooking", md)

    def test_cleanup_browser_state_root_files_preserved(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "ctrip-ebooking" / "a1" / "Cache" / "x", 100)
            _write(root / "browser_sessions.json", 10)
            _write(root / "browser_sessions.json.lock", 1)
            result = cleanup_browser_state(root, dry_run=False)
            # root-level registry files untouched
            self.assertTrue((root / "browser_sessions.json").exists())
            self.assertTrue((root / "browser_sessions.json.lock").exists())
            self.assertGreater(result["bytes_freed"], 0)


class ConfigListsTests(unittest.TestCase):
    def test_login_list_contains_critical_login_entries(self) -> None:
        joined = " ".join(PROFILE_LOGIN_STATE_ENTRIES)
        for must in ["Network", "Local State", "Login Data", "Web Data", "Cookies"]:
            self.assertIn(must, joined)

    def test_non_cache_list_contains_models(self) -> None:
        joined = " ".join(PROFILE_NON_CACHE_ENTRIES)
        for must in ["screen_ai", "segmentation_platform"]:
            self.assertIn(must, joined)

    def test_cleanable_lists_nonempty(self) -> None:
        self.assertTrue(PROFILE_CLEANABLE_DIRS)
        self.assertTrue(PROFILE_CLEANABLE_FILES)


_SUPERVISOR_KEY = "cache-cleanup-supervisor-key-999"


class BrowserCacheEndpointTests(unittest.TestCase):
    """GET /browser-sessions/cache-audit and POST /browser-sessions/cleanup."""

    def setUp(self) -> None:
        self._env = {
            key: os.environ.get(key)
            for key in (
                "ENVIRONMENT",
                "PAN_HOTEL_ENFORCE_API_AUTH",
                "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY",
            )
        }
        get_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    def _enforce_auth(self) -> None:
        os.environ["ENVIRONMENT"] = "production"
        os.environ["PAN_HOTEL_ENFORCE_API_AUTH"] = "true"
        os.environ["PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"] = _SUPERVISOR_KEY
        get_settings.cache_clear()

    def test_cleanup_rejects_without_supervisor_key(self) -> None:
        self._enforce_auth()
        with mock.patch("app.api.v1.browser_sessions.cleanup_browser_state") as patched:
            response = TestClient(app_for_tests()).post("/api/v1/browser-sessions/cleanup", json={})
        self.assertEqual(response.status_code, 401, response.text)
        patched.assert_not_called()

    def test_cleanup_runs_with_supervisor_key(self) -> None:
        self._enforce_auth()
        with mock.patch("app.api.v1.browser_sessions.cleanup_browser_state") as patched:
            patched.return_value = {"bytes_freed": 4096, "profiles_cleaned": 1, "dry_run": False}
            response = TestClient(app_for_tests()).post(
                "/api/v1/browser-sessions/cleanup",
                json={"min_age_days": 0, "dry_run": False},
                headers={"X-Pan-Hotel-API-Key": _SUPERVISOR_KEY},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["bytes_freed"], 4096)
        patched.assert_called_once()
        # min_age_days 0/null => None passed (clean all cleanable)
        self.assertIsNone(patched.call_args.kwargs.get("min_age_days"))
        self.assertFalse(patched.call_args.kwargs.get("dry_run"))

    def test_audit_returns_summary_with_supervisor_key(self) -> None:
        self._enforce_auth()
        with TemporaryDirectory() as tmp:
            _write(Path(tmp) / "ctrip-ebooking" / "a1" / "Cache" / "x", 100)
            _write(Path(tmp) / "ctrip-ebooking" / "a1" / "Local State", 2)
            with mock.patch(
                "app.api.v1.browser_sessions.audit_browser_state"
            ) as patched:
                patched.return_value = audit_browser_state(Path(tmp))
                response = TestClient(app_for_tests()).get(
                    "/api/v1/browser-sessions/cache-audit",
                    headers={"X-Pan-Hotel-API-Key": _SUPERVISOR_KEY},
                )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("platforms", body)
        self.assertGreater(body["total_bytes"], 0)

    def test_screenshot_cleanup_rejects_without_supervisor_key(self) -> None:
        self._enforce_auth()
        with mock.patch("app.api.v1.browser_sessions.cleanup_playwright_screenshots") as patched:
            response = TestClient(app_for_tests()).post(
                "/api/v1/browser-sessions/cleanup-screenshots", json={}
            )
        self.assertEqual(response.status_code, 401, response.text)
        patched.assert_not_called()

    def test_screenshot_cleanup_runs_with_supervisor_key_and_retention(self) -> None:
        self._enforce_auth()
        with mock.patch("app.api.v1.browser_sessions.cleanup_playwright_screenshots") as patched:
            patched.return_value = {"files_removed": 12, "bytes_freed": 48000, "dry_run": False}
            response = TestClient(app_for_tests()).post(
                "/api/v1/browser-sessions/cleanup-screenshots",
                json={"retention_days": 45, "dry_run": False},
                headers={"X-Pan-Hotel-API-Key": _SUPERVISOR_KEY},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["files_removed"], 12)
        # retention_days from body is forwarded
        self.assertEqual(patched.call_args.args[1], 45)


class PlaywrightScreenshotCleanupTests(unittest.TestCase):
    """audit/cleanup of OTA-read screenshots under output/playwright (*.png)."""

    def _make_screenshots(self, root: Path, old_count: int = 3, new_count: int = 2) -> None:
        import time

        now = time.time()
        old_t = now - 100 * 86400  # 100 days ago
        new_t = now - 1 * 86400  # yesterday
        for i in range(old_count):
            p = root / f"old-{i}.png"
            p.write_bytes(b"x" * 1000)
            os.utime(p, (old_t, old_t))
        for i in range(new_count):
            p = root / f"new-{i}.png"
            p.write_bytes(b"y" * 500)
            os.utime(p, (new_t, new_t))
        # non-png file must be preserved
        (root / "note.md").write_text("keep me", encoding="utf-8")

    def test_audit_counts_png_only_and_cleanable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_screenshots(root)
            audit = audit_playwright_screenshots(root, retention_days=30)
            self.assertEqual(audit["total_files"], 5)
            self.assertEqual(audit["cleanable_files"], 3)  # the 3 old png
            self.assertEqual(audit["retention_days"], 30)
            self.assertGreater(audit["cleanable_bytes"], audit["total_bytes"] - audit["cleanable_bytes"])

    def test_dry_run_deletes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_screenshots(root)
            before = len(list(root.glob("*.png")))
            result = cleanup_playwright_screenshots(root, retention_days=30, dry_run=True)
            after = len(list(root.glob("*.png")))
            self.assertEqual(before, after)
            self.assertEqual(result["files_removed"], 3)
            self.assertTrue(result["dry_run"])

    def test_cleanup_removes_old_keeps_new_and_nonpng(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_screenshots(root)
            result = cleanup_playwright_screenshots(root, retention_days=30, dry_run=False)
            self.assertEqual(result["files_removed"], 3)
            self.assertFalse(any(root.glob("old-*.png")))
            self.assertTrue(all((root / f"new-{i}.png").exists() for i in range(2)))
            self.assertTrue((root / "note.md").exists())

    def test_retention_zero_removes_all_png(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_screenshots(root)
            result = cleanup_playwright_screenshots(root, retention_days=0, dry_run=False)
            self.assertEqual(result["files_removed"], 5)
            self.assertTrue((root / "note.md").exists())  # non-png kept

    def test_cleanup_descends_subdirs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "2026-07"
            sub.mkdir()
            import time

            old_t = time.time() - 100 * 86400
            p = sub / "shot.png"
            p.write_bytes(b"x" * 100)
            os.utime(p, (old_t, old_t))
            result = cleanup_playwright_screenshots(root, retention_days=30, dry_run=False)
            self.assertEqual(result["files_removed"], 1)
            self.assertFalse(p.exists())


def app_for_tests():
    from app.main import app

    return app


if __name__ == "__main__":
    unittest.main()
