from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.core.asyncio_runtime import configure_windows_subprocess_event_loop_policy
from app.core.settings import get_settings
from app.db.session import ensure_db_ready
from app.domain.calendar_price_watch.scheduler import CalendarPriceWatchScheduler


def main() -> None:
    configure_windows_subprocess_event_loop_policy()
    settings = get_settings()
    log_dir = Path(settings.browser_state_dir).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "calendar_price_watch_scheduler.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    print("Pan Hotel Watch calendar price scheduler")
    print("Polling enabled read-only calendar price watch configs; Ctrl+C to stop")
    asyncio.run(_run())


async def _run() -> None:
    await ensure_db_ready()
    await CalendarPriceWatchScheduler(tick_seconds=60).run_forever()


if __name__ == "__main__":
    main()
