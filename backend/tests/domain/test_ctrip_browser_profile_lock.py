from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.domain.browser.ctrip_ebooking import (
    _BrowserProfileLaunchLock,
    _is_profile_singleton_error,
)


class CtripBrowserProfileLockTests(unittest.TestCase):
    def test_launch_lock_prevents_parallel_profile_launches_and_releases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "ctrip-profile"
            first = _BrowserProfileLaunchLock(profile_dir)
            second = _BrowserProfileLaunchLock(profile_dir)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())

            first.release()

            self.assertTrue(second.acquire())
            second.release()
            self.assertFalse((profile_dir / ".pan_hotel_watch_launch.lock").exists())

    def test_launch_lock_recovers_from_stale_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "ctrip-profile"
            lock_path = profile_dir / ".pan_hotel_watch_launch.lock"
            profile_dir.mkdir(parents=True)
            lock_path.write_text("stale", encoding="ascii")
            old_time = time.time() - 300
            lock_path.touch()
            import os

            os.utime(lock_path, (old_time, old_time))

            lock = _BrowserProfileLaunchLock(profile_dir)

            self.assertTrue(lock.acquire())
            lock.release()

    def test_profile_singleton_error_is_detected_from_chrome_logs(self) -> None:
        error = RuntimeError(
            "BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
            "Lock file can not be created! Error code: 5\n"
            "Failed to create a ProcessSingleton for your profile directory."
        )

        self.assertTrue(_is_profile_singleton_error(error))


if __name__ == "__main__":
    unittest.main()
