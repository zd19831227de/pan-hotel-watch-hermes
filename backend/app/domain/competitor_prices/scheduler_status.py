from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.ops.worker_heartbeat import (
    DEFAULT_STALE_AFTER_SECONDS,
    get_worker_heartbeat_status,
    worker_heartbeat_path,
    write_worker_heartbeat,
)

SCHEDULER_NAME = "competitor_price_scheduler"


def write_competitor_price_scheduler_heartbeat(
    event: str,
    *,
    status: str = "running",
    tick_result: dict[str, Any] | None = None,
    error: BaseException | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return write_worker_heartbeat(
        SCHEDULER_NAME,
        event,
        status=status,
        tick_summary=_summarize_tick_result(tick_result),
        error=error,
        path=path,
        now=now,
    )


def get_competitor_price_scheduler_status(
    *,
    path: Path | None = None,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    status = get_worker_heartbeat_status(
        SCHEDULER_NAME,
        path=path,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    return _flatten_scheduler_status(status)


def competitor_price_scheduler_heartbeat_path() -> Path:
    return worker_heartbeat_path(SCHEDULER_NAME)


def _flatten_scheduler_status(status: dict[str, Any]) -> dict[str, Any]:
    summary = status.get("last_tick_summary")
    if not isinstance(summary, dict):
        summary = {}
    return {
        "name": status.get("name") or SCHEDULER_NAME,
        "status": status.get("status"),
        "process_confirmed": bool(status.get("process_confirmed")),
        "pid": status.get("pid"),
        "updated_at": status.get("updated_at"),
        "last_heartbeat_at": status.get("last_heartbeat_at"),
        "heartbeat_age_seconds": status.get("heartbeat_age_seconds"),
        "stale_after_seconds": status.get("stale_after_seconds"),
        "started_at": status.get("started_at"),
        "last_tick_started_at": status.get("last_tick_started_at"),
        "last_tick_finished_at": status.get("last_tick_finished_at"),
        "last_success_at": status.get("last_success_at"),
        "last_error_at": status.get("last_error_at"),
        "last_error": status.get("last_error"),
        "last_due_group_count": summary.get("last_due_group_count"),
        "last_result_count": summary.get("last_result_count"),
        "last_success_count": summary.get("last_success_count"),
        "last_failed_count": summary.get("last_failed_count"),
        "last_run_ids": summary.get("last_run_ids") or [],
        "last_watch_group_keys": summary.get("last_watch_group_keys") or [],
        "status_counts": summary.get("status_counts") or {},
    }


def _summarize_tick_result(tick_result: dict[str, Any] | None) -> dict[str, Any]:
    if not tick_result:
        return {
            "last_due_group_count": 0,
            "last_result_count": 0,
            "last_success_count": 0,
            "last_failed_count": 0,
            "last_run_ids": [],
            "last_watch_group_keys": [],
            "status_counts": {},
        }
    results = [item for item in tick_result.get("results", []) if isinstance(item, dict)]
    status_counts: dict[str, int] = {}
    run_ids: list[int] = []
    watch_group_keys: list[str] = []
    for item in results:
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
        "last_result_count": len(results),
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
