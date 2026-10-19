from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path


class BrowserProfileMigrationTests(unittest.TestCase):
    def test_dry_run_reports_pending_copy_without_writing(self) -> None:
        migration = importlib.import_module("app.domain.browser.profile_migration")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical"
            fallout = root / "fallout-chrome"
            source = fallout / "fliggy-ebooking" / "fliggy"
            dest = canonical / "fliggy-ebooking" / "fliggy"
            _seed_real_profile(source)
            dest.mkdir(parents=True, exist_ok=True)

            plan = migration.plan_browser_profile_migration(
                canonical_root=canonical,
                fallout_roots=[("chrome_application", fallout)],
                dry_run=True,
            )

            self.assertEqual(plan["pending_copies"], 1)
            self.assertEqual(plan["copied"], 0)
            self.assertEqual(plan["skipped"], 0)
            self.assertFalse((dest / "Local State").exists())
            self.assertTrue(all(":" not in item.get("source_label", "") for item in plan["items"]))
            self.assertEqual(plan["items"][0]["source_label"], "chrome_application")
            self.assertEqual(plan["items"][0]["platform"], "fliggy-ebooking")
            self.assertEqual(plan["items"][0]["account_id"], "fliggy")
            self.assertEqual(plan["items"][0]["action"], "copy")

    def test_apply_copies_then_second_apply_is_noop(self) -> None:
        migration = importlib.import_module("app.domain.browser.profile_migration")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical"
            fallout = root / "fallout-chrome"
            source = fallout / "fliggy-ebooking" / "fliggy"
            dest = canonical / "fliggy-ebooking" / "fliggy"
            _seed_real_profile(source)
            dest.mkdir(parents=True, exist_ok=True)

            first = migration.apply_browser_profile_migration(
                canonical_root=canonical,
                fallout_roots=[("chrome_application", fallout)],
            )
            self.assertEqual(first["copied"], 1)
            self.assertTrue((dest / "Local State").exists())
            self.assertTrue((dest / "Default" / "Cookies").exists())
            self.assertTrue((source / "Local State").exists())

            second = migration.apply_browser_profile_migration(
                canonical_root=canonical,
                fallout_roots=[("chrome_application", fallout)],
            )
            self.assertEqual(second["copied"], 0)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(second["items"][0]["action"], "skip_destination_populated")

    def test_skip_when_destination_already_populated(self) -> None:
        migration = importlib.import_module("app.domain.browser.profile_migration")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical"
            fallout = root / "fallout"
            source = fallout / "meituan-ebooking" / "jingli-meituan"
            dest = canonical / "meituan-ebooking" / "jingli-meituan"
            _seed_real_profile(source)
            _seed_real_profile(dest)

            result = migration.apply_browser_profile_migration(
                canonical_root=canonical,
                fallout_roots=[("playwright_chromium", fallout)],
            )
            self.assertEqual(result["copied"], 0)
            self.assertEqual(result["skipped"], 1)
            self.assertEqual(result["items"][0]["action"], "skip_destination_populated")

    def test_copy_skips_bulky_cache_directories(self) -> None:
        migration = importlib.import_module("app.domain.browser.profile_migration")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical"
            fallout = root / "fallout"
            source = fallout / "fliggy-ebooking" / "fliggy"
            dest = canonical / "fliggy-ebooking" / "fliggy"
            _seed_real_profile(source)
            cache = source / "Default" / "Cache"
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "big.bin").write_bytes(b"x" * 1024)
            dest.mkdir(parents=True, exist_ok=True)

            result = migration.apply_browser_profile_migration(
                canonical_root=canonical,
                fallout_roots=[("chrome_application", fallout)],
            )
            self.assertEqual(result["copied"], 1)
            self.assertTrue((dest / "Default" / "Cookies").exists())
            self.assertFalse((dest / "Default" / "Cache").exists())

    def test_abort_when_singleton_lock_present(self) -> None:
        migration = importlib.import_module("app.domain.browser.profile_migration")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical"
            fallout = root / "fallout"
            source = fallout / "ctrip-ebooking" / "ebk-2"
            dest = canonical / "ctrip-ebooking" / "ebk-2"
            _seed_real_profile(source)
            (source / "SingletonLock").write_text("locked", encoding="utf-8")
            dest.mkdir(parents=True, exist_ok=True)

            result = migration.apply_browser_profile_migration(
                canonical_root=canonical,
                fallout_roots=[("playwright_chromium", fallout)],
            )
            self.assertEqual(result["copied"], 0)
            self.assertEqual(result["aborted"], 1)
            self.assertEqual(result["items"][0]["action"], "abort_locked")
            self.assertFalse((dest / "Local State").exists())


def _seed_real_profile(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "Local State").write_text("{}", encoding="utf-8")
    default = profile_dir / "Default"
    default.mkdir(parents=True, exist_ok=True)
    (default / "Cookies").write_bytes(b"cookie")


if __name__ == "__main__":
    unittest.main()
