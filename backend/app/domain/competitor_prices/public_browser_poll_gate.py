from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

COMPETITOR_DELAY_AFTER_CALENDAR_SECONDS = 30


def public_browser_profile_key(platform_account_id: str, browser_session_id: str) -> str:
    return f"{platform_account_id}:{browser_session_id}"


class PublicBrowserPollGate:
    """Serialize scheduled calendar and competitor polls on one public browser profile."""

    _gates: dict[str, PublicBrowserPollGate] = {}
    _registry_lock = asyncio.Lock()

    def __init__(self) -> None:
        self._profile_lock = asyncio.Lock()
        self._calendar_active = False
        self._last_calendar_finished_at: float | None = None

    @classmethod
    async def for_profile(cls, profile_key: str) -> PublicBrowserPollGate:
        async with cls._registry_lock:
            gate = cls._gates.get(profile_key)
            if gate is None:
                gate = PublicBrowserPollGate()
                cls._gates[profile_key] = gate
            return gate

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._gates.clear()

    @asynccontextmanager
    async def calendar_session(self) -> AsyncIterator[None]:
        await self._profile_lock.acquire()
        self._calendar_active = True
        try:
            yield
        finally:
            self._calendar_active = False
            self._last_calendar_finished_at = time.monotonic()
            self._profile_lock.release()

    async def wait_for_competitor_turn(self) -> None:
        while True:
            if self._calendar_active:
                await asyncio.sleep(0.25)
                continue
            last_finished = self._last_calendar_finished_at
            if last_finished is None:
                return
            remaining = COMPETITOR_DELAY_AFTER_CALENDAR_SECONDS - (
                time.monotonic() - last_finished
            )
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.5))

    @asynccontextmanager
    async def competitor_session(self) -> AsyncIterator[None]:
        await self.wait_for_competitor_turn()
        await self._profile_lock.acquire()
        try:
            yield
        finally:
            self._profile_lock.release()


@asynccontextmanager
async def calendar_profile_poll(
    platform_account_id: str,
    browser_session_id: str,
    *,
    enabled: bool,
) -> AsyncIterator[None]:
    if not enabled:
        yield
        return
    gate = await PublicBrowserPollGate.for_profile(
        public_browser_profile_key(platform_account_id, browser_session_id)
    )
    async with gate.calendar_session():
        yield


@asynccontextmanager
async def competitor_profile_poll(
    platform_account_id: str,
    browser_session_id: str,
    *,
    enabled: bool,
) -> AsyncIterator[None]:
    if not enabled:
        yield
        return
    gate = await PublicBrowserPollGate.for_profile(
        public_browser_profile_key(platform_account_id, browser_session_id)
    )
    async with gate.competitor_session():
        yield
