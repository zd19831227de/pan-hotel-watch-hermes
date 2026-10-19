from __future__ import annotations

import asyncio
import sys


def configure_windows_subprocess_event_loop_policy() -> None:
    if sys.platform.startswith("win"):
        policy = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
        if policy is not None:
            asyncio.set_event_loop_policy(policy())

