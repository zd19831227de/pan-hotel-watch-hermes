from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.domain.ctrip_ebooking_watch.push_throttle import (
    maybe_log_throttle,
    should_throttle_scheduled_push,
)
from app.domain.meituan_ebooking_watch.repository import list_due_meituan_watch_groups
from app.domain.meituan_ebooking_watch.service import get_meituan_ebooking_watch_service
from app.domain.ops.worker_heartbeat import write_worker_heartbeat
from app.domain.platform_ebk_watch.push_policy import platform_throttle_key

logger = logging.getLogger(__name__)
WORKER_NAME = "meituan_ebooking_scheduler"


class MeituanEbookingWatchScheduler:
    """Poll configured Meituan EBK watch groups (schedule due → read → snapshot → push)."""

    def __init__(self, *, tick_seconds: int = 60) -> None:
        self.tick_seconds = tick_seconds

    async def run_forever(self) -> None:
        _record_worker_heartbeat("started")
        while True:
            _record_worker_heartbeat("tick_started")
            try:
                result = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _record_worker_heartbeat("tick_failed", status="error", error=exc)
                logger.exception("meituan ebooking scheduler tick failed; continuing")
                await asyncio.sleep(self.tick_seconds)
                continue
            _record_worker_heartbeat("tick_finished", tick_result=result)
            if result["due_group_count"]:
                logger.info("meituan ebooking scheduler tick: %s", result)
            await asyncio.sleep(self.tick_seconds)

    async def tick(self) -> dict[str, Any]:
        groups = await list_due_meituan_watch_groups()
        results = []
        for group in groups:
            # 手动推送冷却窗口内跳过定时推送（与携程调度器同一节流实现）；
            # key 必须带平台前缀，避免与携程组或跨平台同名组互串冷却。
            throttle, reason = should_throttle_scheduled_push(
                platform_throttle_key("meituan", group.group_key)
            )
            if throttle:
                maybe_log_throttle(group.group_key, reason)
                results.append(
                    {
                        "watch_group_key": group.group_key,
                        "status": "throttled",
                        "summary": reason,
                    }
                )
                continue
            try:
                results.append(
                    await get_meituan_ebooking_watch_service().run_watch_group(
                        group=group,
                        actor_id="scheduler",
                        source="scheduler",
                    )
                )
            except Exception as exc:
                logger.exception("meituan ebooking scheduled poll failed")
                results.append(
                    {
                        "watch_group_key": group.group_key,
                        "status": "failed",
                        "summary": str(exc)[:300],
                        "error_type": type(exc).__name__,
                    }
                )
        return {
            "due_group_count": len(groups),
            "results": results,
        }

    async def run_watch_group_now(
        self,
        watch_group_key: str,
        *,
        push_to_feishu: bool = True,
        actor_id: str = "operator",
    ) -> dict[str, Any]:
        return await get_meituan_ebooking_watch_service().run_watch_group_now(
            watch_group_key=watch_group_key,
            actor_id=actor_id,
            source="api",
            push_to_feishu=push_to_feishu,
        )


def _record_worker_heartbeat(
    event: str,
    *,
    status: str = "running",
    tick_result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    try:
        write_worker_heartbeat(
            WORKER_NAME,
            event,
            status=status,
            tick_summary=_summarize_tick_result(tick_result),
            error=error,
        )
    except Exception:
        logger.warning("failed to write meituan ebooking scheduler heartbeat", exc_info=True)


def _summarize_tick_result(tick_result: dict[str, Any] | None) -> dict[str, Any]:
    if not tick_result:
        return {
            "last_due_group_count": 0,
            "last_result_count": 0,
            "last_success_count": 0,
            "last_failed_count": 0,
            "last_snapshot_ids": [],
            "last_watch_group_keys": [],
            "status_counts": {},
        }
    results = [item for item in tick_result.get("results", []) if isinstance(item, dict)]
    status_counts: dict[str, int] = {}
    snapshot_ids: list[int] = []
    watch_group_keys: list[str] = []
    for item in results:
        item_status = str(item.get("status") or "unknown")
        status_counts[item_status] = status_counts.get(item_status, 0) + 1
        watch_group_key = item.get("watch_group_key")
        if isinstance(watch_group_key, str):
            watch_group_keys.append(watch_group_key)
        snapshot_id = item.get("snapshot_id")
        if isinstance(snapshot_id, int):
            snapshot_ids.append(snapshot_id)
    return {
        "last_due_group_count": _int_or_none(tick_result.get("due_group_count")) or 0,
        "last_result_count": len(results),
        "last_success_count": status_counts.get("success", 0),
        "last_failed_count": sum(
            count
            for item_status, count in status_counts.items()
            if item_status not in {"success", "skipped", "throttled"}
        ),
        "last_snapshot_ids": snapshot_ids[:10],
        "last_watch_group_keys": watch_group_keys[:10],
        "status_counts": status_counts,
    }


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
