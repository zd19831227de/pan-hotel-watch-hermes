from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.ops.worker_heartbeat import (
    DEFAULT_STALE_AFTER_SECONDS,
    get_worker_heartbeat_status,
    worker_heartbeat_path,
)

SCHEDULER_NAME = "ctrip_ebooking_data_center_scheduler"


def get_data_center_watch_scheduler_status(
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
    summary = status.get("last_tick_summary")
    if not isinstance(summary, dict):
        summary = {}
    return {
        "name": SCHEDULER_NAME,
        "status": status["status"],
        "process_confirmed": status["process_confirmed"],
        "pid": status["pid"],
        "updated_at": status["updated_at"],
        "last_heartbeat_at": status["last_heartbeat_at"],
        "heartbeat_age_seconds": status["heartbeat_age_seconds"],
        "stale_after_seconds": status["stale_after_seconds"],
        "started_at": status["started_at"],
        "last_tick_started_at": status["last_tick_started_at"],
        "last_tick_finished_at": status["last_tick_finished_at"],
        "last_success_at": status["last_success_at"],
        "last_error_at": status["last_error_at"],
        "last_error": status["last_error"],
        "last_due_group_count": summary.get("last_due_group_count"),
        "last_due_daily_brief_count": summary.get("last_due_daily_brief_count"),
        "last_result_count": summary.get("last_result_count"),
        "last_success_count": summary.get("last_success_count"),
        "last_failed_count": summary.get("last_failed_count"),
        "last_run_ids": summary.get("last_run_ids") or [],
        "last_watch_group_keys": summary.get("last_watch_group_keys") or [],
        "status_counts": summary.get("status_counts") or {},
    }


def data_center_watch_scheduler_heartbeat_path() -> Path:
    return worker_heartbeat_path(SCHEDULER_NAME)
