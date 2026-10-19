from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.domain.calendar_price_watch import scheduler as calendar_scheduler
from app.domain.competitor_prices import scheduler as competitor_scheduler
from app.domain.fliggy_ebooking_watch import scheduler as fliggy_scheduler
from app.domain.meituan_ebooking_watch import scheduler as meituan_scheduler
from app.workers import read_only_scheduler


class SchedulerLoopResilienceTests(unittest.TestCase):
    def test_competitor_scheduler_continues_after_tick_failure(self) -> None:
        scheduler = competitor_scheduler.CompetitorPriceScheduler(tick_seconds=1)
        scheduler.tick = mock.AsyncMock(
            side_effect=[RuntimeError("first tick failed"), {"due_group_count": 0}]
        )
        heartbeats: list[tuple[str, dict[str, object]]] = []

        with (
            mock.patch.object(
                competitor_scheduler,
                "_record_scheduler_heartbeat",
                side_effect=lambda phase, **kwargs: heartbeats.append((phase, kwargs)),
            ),
            mock.patch.object(
                competitor_scheduler.asyncio,
                "sleep",
                side_effect=_continue_once_then_cancel(),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(scheduler.run_forever())

        self.assertEqual(scheduler.tick.await_count, 2)
        self.assertIn("tick_failed", [phase for phase, _ in heartbeats])
        self.assertIn("tick_finished", [phase for phase, _ in heartbeats])

    def test_calendar_scheduler_continues_after_tick_failure(self) -> None:
        scheduler = calendar_scheduler.CalendarPriceWatchScheduler(tick_seconds=1)
        scheduler.tick = mock.AsyncMock(
            side_effect=[RuntimeError("first tick failed"), {"due_group_count": 0}]
        )
        heartbeats: list[tuple[str, dict[str, object]]] = []

        with (
            mock.patch.object(
                calendar_scheduler,
                "_record_scheduler_heartbeat",
                side_effect=lambda phase, **kwargs: heartbeats.append((phase, kwargs)),
            ),
            mock.patch.object(
                calendar_scheduler.asyncio,
                "sleep",
                side_effect=_continue_once_then_cancel(),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(scheduler.run_forever())

        self.assertEqual(scheduler.tick.await_count, 2)
        self.assertIn("tick_failed", [phase for phase, _ in heartbeats])
        self.assertIn("tick_finished", [phase for phase, _ in heartbeats])

    def test_meituan_scheduler_continues_after_tick_failure(self) -> None:
        scheduler = meituan_scheduler.MeituanEbookingWatchScheduler(tick_seconds=1)
        scheduler.tick = mock.AsyncMock(
            side_effect=[RuntimeError("first tick failed"), {"due_group_count": 0}]
        )
        heartbeats: list[str] = []

        with (
            mock.patch.object(
                meituan_scheduler,
                "_record_worker_heartbeat",
                side_effect=lambda phase, **_: heartbeats.append(phase),
            ),
            mock.patch.object(
                meituan_scheduler.asyncio,
                "sleep",
                side_effect=_continue_once_then_cancel(),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(scheduler.run_forever())

        self.assertEqual(scheduler.tick.await_count, 2)
        self.assertIn("tick_failed", heartbeats)
        self.assertIn("tick_finished", heartbeats)

    def test_fliggy_scheduler_continues_after_tick_failure(self) -> None:
        scheduler = fliggy_scheduler.FliggyEbookingWatchScheduler(tick_seconds=1)
        scheduler.tick = mock.AsyncMock(
            side_effect=[RuntimeError("first tick failed"), {"due_group_count": 0}]
        )
        heartbeats: list[str] = []

        with (
            mock.patch.object(
                fliggy_scheduler,
                "_record_worker_heartbeat",
                side_effect=lambda phase, **_: heartbeats.append(phase),
            ),
            mock.patch.object(
                fliggy_scheduler.asyncio,
                "sleep",
                side_effect=_continue_once_then_cancel(),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(scheduler.run_forever())

        self.assertEqual(scheduler.tick.await_count, 2)
        self.assertIn("tick_failed", heartbeats)
        self.assertIn("tick_finished", heartbeats)

    def test_aggregate_worker_continues_after_child_tick_failure(self) -> None:
        calendar = mock.Mock()
        calendar.tick = mock.AsyncMock(
            side_effect=[
                RuntimeError("calendar failed"),
                {"due_group_count": 0, "due_matrix_brief_count": 0},
            ]
        )
        competitor = mock.Mock()
        competitor.tick = mock.AsyncMock(
            side_effect=[{"due_group_count": 0}, {"due_group_count": 0}]
        )
        calendar_heartbeats: list[str] = []
        competitor_heartbeats: list[str] = []

        with (
            mock.patch.object(
                read_only_scheduler,
                "CalendarPriceWatchScheduler",
                return_value=calendar,
            ),
            mock.patch.object(
                read_only_scheduler,
                "CompetitorPriceScheduler",
                return_value=competitor,
            ),
            mock.patch.object(
                read_only_scheduler,
                "write_calendar_price_scheduler_heartbeat",
                side_effect=lambda phase, **_: calendar_heartbeats.append(phase),
            ),
            mock.patch.object(
                read_only_scheduler,
                "write_competitor_price_scheduler_heartbeat",
                side_effect=lambda phase, **_: competitor_heartbeats.append(phase),
            ),
            mock.patch.object(
                read_only_scheduler.asyncio,
                "sleep",
                side_effect=_continue_once_then_cancel(),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(
                    read_only_scheduler._run_calendar_and_competitor_scheduler_loop()
                )

        self.assertEqual(calendar.tick.await_count, 2)
        self.assertEqual(competitor.tick.await_count, 2)
        self.assertIn("tick_failed", calendar_heartbeats)
        self.assertIn("tick_finished", calendar_heartbeats)
        self.assertIn("tick_finished", competitor_heartbeats)


def _continue_once_then_cancel():
    calls = 0

    async def fake_sleep(_: float) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise asyncio.CancelledError

    return fake_sleep


if __name__ == "__main__":
    unittest.main()
