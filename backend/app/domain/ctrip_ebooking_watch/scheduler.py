from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.domain.ctrip_ebooking_watch.push_throttle import (
    maybe_log_throttle,
    should_throttle_scheduled_push,
)
from app.domain.ctrip_ebooking_watch.repository import (
    list_due_daily_brief_groups,
    list_due_data_center_watch_groups,
    mark_daily_brief_group_pushed,
)
from app.domain.ctrip_ebooking_watch.service import get_data_center_watch_service
from app.domain.ops.worker_heartbeat import write_worker_heartbeat

logger = logging.getLogger(__name__)
WORKER_NAME = "ctrip_ebooking_data_center_scheduler"


class CtripEbookingDataCenterWatchScheduler:
    """Poll configured Ctrip EBK data-center watch groups."""

    def __init__(self, *, tick_seconds: int = 60) -> None:
        self.tick_seconds = tick_seconds

    async def run_forever(self) -> None:
        _record_worker_heartbeat("started")
        while True:
            _record_worker_heartbeat("tick_started")
            try:
                result = await self.tick()
            except Exception as exc:
                _record_worker_heartbeat("tick_failed", status="error", error=exc)
                raise
            _record_worker_heartbeat("tick_finished", tick_result=result)
            if result["due_group_count"]:
                logger.info("data-center watch scheduler tick: %s", result)
            await asyncio.sleep(self.tick_seconds)

    async def tick(self) -> dict[str, Any]:
        groups = await list_due_data_center_watch_groups()
        daily_brief_groups = await list_due_daily_brief_groups()
        results = []
        for group in groups:
            # V5 Step 6 — skip a scheduled push when a manual push for this
            # group happened inside the cooldown window. Default OFF (no
            # behaviour change) until the push-stability precondition is met.
            throttle, reason = should_throttle_scheduled_push(group.group_key)
            if throttle:
                maybe_log_throttle(group.group_key, reason)
                results.append(
                    {
                        "watch_group_key": group.group_key,
                        "status": "throttled",
                        "summary": reason,
                        "run_kind": "data_center_watch",
                    }
                )
                continue
            try:
                results.append(
                    await get_data_center_watch_service().run_watch_group(
                        group=group,
                        actor_id="scheduler",
                        source="scheduler",
                    )
                )
            except Exception as exc:
                logger.exception("data-center scheduled poll failed")
                results.append(
                    {
                        "watch_group_key": group.group_key,
                        "status": "failed",
                        "summary": str(exc)[:300],
                        "error_type": type(exc).__name__,
                    }
                )
        daily_brief_results = []
        for group in daily_brief_groups:
            try:
                result = await get_data_center_watch_service().run_daily_brief(
                    group=group,
                    actor_id="daily_brief_scheduler",
                    source="scheduler",
                )
                daily_brief_results.append(result)
                if result.get("status") != "skipped":
                    await mark_daily_brief_group_pushed(watch_group_key=group.group_key)
            except Exception as exc:
                logger.exception("daily brief scheduled push failed")
                daily_brief_results.append(
                    {
                        "watch_group_key": group.group_key,
                        "status": "failed",
                        "summary": str(exc)[:300],
                        "error_type": type(exc).__name__,
                        "run_kind": "daily_brief",
                    }
                )
        return {
            "due_group_count": len(groups),
            "due_daily_brief_count": len(daily_brief_groups),
            "results": results,
            "daily_brief_results": daily_brief_results,
        }

    async def run_watch_group_now(
        self,
        watch_group_key: str,
        *,
        push_to_feishu: bool = True,
        actor_id: str = "operator",
    ) -> dict[str, Any]:
        return await get_data_center_watch_service().run_watch_group_now(
            watch_group_key=watch_group_key,
            actor_id=actor_id,
            source="api",
            push_to_feishu=push_to_feishu,
        )

    async def run_daily_brief_now(self, watch_group_key: str) -> dict[str, Any]:
        from app.domain.ctrip_ebooking_watch.repository import (
            get_data_center_watch_group_runtime,
        )

        group = await get_data_center_watch_group_runtime(watch_group_key=watch_group_key)
        if group is None:
            return {
                "watch_group_key": watch_group_key,
                "status": "not_found",
                "summary": "未找到 EBK 数据中心监控组。",
                "run_kind": "daily_brief",
            }
        if not group.enabled:
            return {
                "watch_group_key": watch_group_key,
                "status": "skipped",
                "summary": "EBK 数据中心监控组已停用。",
                "run_kind": "daily_brief",
            }
        return await get_data_center_watch_service().run_daily_brief(
            group=group,
            actor_id="operator",
            source="api",
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
        logger.warning("failed to write data-center scheduler heartbeat", exc_info=True)


def _summarize_tick_result(tick_result: dict[str, Any] | None) -> dict[str, Any]:
    if not tick_result:
        return {
            "last_due_group_count": 0,
            "last_due_daily_brief_count": 0,
            "last_result_count": 0,
            "last_success_count": 0,
            "last_failed_count": 0,
            "last_run_ids": [],
            "last_watch_group_keys": [],
            "status_counts": {},
        }
    results = [item for item in tick_result.get("results", []) if isinstance(item, dict)]
    daily_brief_results = [
        item for item in tick_result.get("daily_brief_results", []) if isinstance(item, dict)
    ]
    all_results = [*results, *daily_brief_results]
    status_counts: dict[str, int] = {}
    run_ids: list[int] = []
    watch_group_keys: list[str] = []
    for item in all_results:
        item_status = str(item.get("status") or "unknown")
        status_counts[item_status] = status_counts.get(item_status, 0) + 1
        watch_group_key = item.get("watch_group_key")
        if isinstance(watch_group_key, str):
            watch_group_keys.append(watch_group_key)
        persistence = item.get("persistence")
        if isinstance(persistence, dict):
            run_id = _int_or_none(persistence.get("run_id"))
            if run_id is not None:
                run_ids.append(run_id)
    return {
        "last_due_group_count": _int_or_none(tick_result.get("due_group_count")) or 0,
        "last_due_daily_brief_count": _int_or_none(tick_result.get("due_daily_brief_count")) or 0,
        "last_result_count": len(all_results),
        "last_success_count": status_counts.get("success", 0),
        "last_failed_count": sum(
            count
            for item_status, count in status_counts.items()
            if item_status not in {"success", "skipped"}
        ),
        "last_run_ids": run_ids[:10],
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
