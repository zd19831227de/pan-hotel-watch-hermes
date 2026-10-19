from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.competitor_prices.scheduler import CompetitorPriceScheduler


class CompetitorSchedulerConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tick_polls_independent_groups_concurrently(self) -> None:
        scheduler = CompetitorPriceScheduler(tick_seconds=1)
        groups = [
            {"group_key": "group-a"},
            {"group_key": "group-b"},
        ]
        started = 0
        all_started = asyncio.Event()
        release = asyncio.Event()

        async def fake_poll(group, **kwargs):
            nonlocal started
            started += 1
            if started == len(groups):
                all_started.set()
            await release.wait()
            return {"watch_group_key": group["group_key"], "status": "success"}

        with (
            patch(
                "app.domain.competitor_prices.scheduler.list_due_competitor_watch_groups",
                new=AsyncMock(return_value=groups),
            ),
            patch(
                "app.domain.competitor_prices.scheduler.cleanup_expired_competitor_price_data",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        model_dump=lambda mode: {"status": "success"},
                    )
                ),
            ),
        ):
            scheduler._poll_group = fake_poll
            tick_task = asyncio.create_task(scheduler.tick())
            await asyncio.wait_for(all_started.wait(), timeout=0.5)
            release.set()
            result = await tick_task

        self.assertEqual(started, 2)
        self.assertEqual(
            {item["watch_group_key"] for item in result["results"]},
            {"group-a", "group-b"},
        )


if __name__ == "__main__":
    unittest.main()
