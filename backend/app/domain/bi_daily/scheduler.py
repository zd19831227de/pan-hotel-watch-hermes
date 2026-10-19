"""经营 BI 日报调度循环：每日定时采集 +（可选）飞书日报推送。

挂在 read_only_scheduler.run_forever 的 loops 里；全部数据库操作幂等
（采集轮 (run_date, source) 唯一、推送 (shop_key, push_date) 唯一），
worker 重启不会重复推送或重复落事实。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

from app.core.settings import get_settings
from app.db.session import session_scope
from app.domain.bi_daily.models import local_now
from app.domain.ops.worker_heartbeat import write_worker_heartbeat

logger = logging.getLogger("app.workers.bi_daily")

DEFAULT_COLLECTION_TIME = "09:40"
DEFAULT_PUSH_TIME = "10:00"


def _parse_hhmm(raw: str | None, fallback: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(raw or "").strip())
    if match is None:
        logger.warning("bi_daily 时间配置非法（%r，需 HH:MM），回退默认 %s", raw, fallback)
        hour, minute = (int(part) for part in fallback.split(":"))
    else:
        hour, minute = int(match.group(1)), int(match.group(2))
    return max(0, min(hour, 23)), max(0, min(minute, 59))


def next_run_at(now: datetime, *, raw_time: str | None, fallback: str) -> datetime:
    """下一次目标时刻（已过则顺延到明天）。"""
    hour, minute = _parse_hhmm(raw_time, fallback)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


async def run_bi_daily_collection_tick(source: str = "scheduler") -> dict[str, object]:
    async with session_scope() as session:
        from app.domain.bi_daily import service

        return await service.run_bi_daily_collection_round(
            session=session, source=source
        )


async def run_bi_daily_push_tick() -> list[dict[str, object]]:
    async with session_scope() as session:
        from app.domain.bi_daily.cards import push_bi_daily_reports

        return await push_bi_daily_reports(session=session)


async def run_bi_daily_loop(tick_seconds: int = 60) -> None:
    """主循环：到点采集（默认 09:40）→ 到点推送（开启后默认 10:00）。"""
    write_worker_heartbeat("bi_daily_scheduler", "loop_started")
    settings = get_settings()
    collection_time = getattr(settings, "bi_daily_collection_time", None)
    push_time = getattr(settings, "bi_daily_push_time", None)
    next_collection = next_run_at(
        local_now(), raw_time=collection_time, fallback=DEFAULT_COLLECTION_TIME
    )
    next_push = (
        next_run_at(local_now(), raw_time=push_time, fallback=DEFAULT_PUSH_TIME)
        if getattr(settings, "bi_daily_push_enabled", False)
        else None
    )
    while True:
        try:
            await asyncio.sleep(max(5, tick_seconds))
            now = local_now()
            settings = get_settings()
            # 运行期打开推送开关无需重启 worker：每 tick 重读配置。
            if next_push is None and getattr(settings, "bi_daily_push_enabled", False):
                next_push = next_run_at(
                    now,
                    raw_time=getattr(settings, "bi_daily_push_time", None),
                    fallback=DEFAULT_PUSH_TIME,
                )
            if now >= next_collection:
                summary = await run_bi_daily_collection_tick()
                write_worker_heartbeat(
                    "bi_daily_scheduler", "collection_finished", tick_summary=dict(summary)
                )
                next_collection = next_run_at(
                    local_now(),
                    raw_time=getattr(get_settings(), "bi_daily_collection_time", None),
                    fallback=DEFAULT_COLLECTION_TIME,
                )
            if next_push is not None and now >= next_push:
                push_results = await run_bi_daily_push_tick()
                write_worker_heartbeat(
                    "bi_daily_scheduler",
                    "push_finished",
                    tick_summary={"pushes": len(push_results)},
                )
                if getattr(get_settings(), "bi_daily_push_enabled", False):
                    next_push = next_run_at(
                        local_now(),
                        raw_time=getattr(get_settings(), "bi_daily_push_time", None),
                        fallback=DEFAULT_PUSH_TIME,
                    )
                else:
                    next_push = None
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - loop 必须持续运行
            write_worker_heartbeat(
                "bi_daily_scheduler", "tick_failed", status="error", error=exc
            )
            logger.error("bi_daily scheduler tick failed; continuing", exc_info=exc)
