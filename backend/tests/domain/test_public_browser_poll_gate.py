from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from app.domain.competitor_prices.public_browser_poll_gate import (
    COMPETITOR_DELAY_AFTER_CALENDAR_SECONDS,
    PublicBrowserPollGate,
    calendar_profile_poll,
    competitor_profile_poll,
)


class PublicBrowserPollGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        PublicBrowserPollGate.reset_for_tests()

    def tearDown(self) -> None:
        PublicBrowserPollGate.reset_for_tests()

    def test_competitor_delay_after_calendar_is_thirty_seconds(self) -> None:
        self.assertEqual(COMPETITOR_DELAY_AFTER_CALENDAR_SECONDS, 30)

    async def test_calendar_poll_marks_recent_finish_for_competitor_delay(self) -> None:
        with patch(
            "app.domain.competitor_prices.public_browser_poll_gate.COMPETITOR_DELAY_AFTER_CALENDAR_SECONDS",
            0.05,
        ):
            async with calendar_profile_poll("acct-a", "browser-a", enabled=True):
                pass

            gate = await PublicBrowserPollGate.for_profile("acct-a:browser-a")
            self.assertIsNotNone(gate._last_calendar_finished_at)

            started = time.monotonic()
            async with competitor_profile_poll("acct-a", "browser-a", enabled=True):
                elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.04)

    async def test_competitor_waits_while_calendar_holds_same_profile(self) -> None:
        gate = await PublicBrowserPollGate.for_profile("acct-a:browser-a")
        calendar_started = asyncio.Event()
        calendar_release = asyncio.Event()
        competitor_started = asyncio.Event()

        async def calendar_task() -> None:
            async with gate.calendar_session():
                calendar_started.set()
                await calendar_release.wait()

        async def competitor_task() -> None:
            async with gate.competitor_session():
                competitor_started.set()

        calendar_runner = asyncio.create_task(calendar_task())
        await calendar_started.wait()
        competitor_runner = asyncio.create_task(competitor_task())
        await asyncio.sleep(0.05)
        self.assertFalse(competitor_started.is_set())
        calendar_release.set()
        await asyncio.gather(calendar_runner, competitor_runner)
        self.assertTrue(competitor_started.is_set())

    async def test_manual_polls_skip_gate_when_disabled(self) -> None:
        with patch(
            "app.domain.competitor_prices.public_browser_poll_gate.PublicBrowserPollGate.for_profile",
        ) as for_profile:
            async with calendar_profile_poll("acct-a", "browser-a", enabled=False):
                pass
            async with competitor_profile_poll("acct-a", "browser-a", enabled=False):
                pass
            for_profile.assert_not_called()

    async def test_different_profiles_do_not_block_each_other(self) -> None:
        with patch(
            "app.domain.competitor_prices.public_browser_poll_gate.COMPETITOR_DELAY_AFTER_CALENDAR_SECONDS",
            0.05,
        ):
            async with calendar_profile_poll("acct-a", "browser-a", enabled=True):
                started = time.monotonic()
                async with competitor_profile_poll("acct-b", "browser-b", enabled=True):
                    elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.03)


if __name__ == "__main__":
    unittest.main()
