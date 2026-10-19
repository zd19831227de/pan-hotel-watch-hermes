from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.security import require_operator_api_key
from app.domain.ebk_snapshots.repository import get_latest_ebk_snapshot
from app.domain.fliggy_ebooking_watch.repository import (
    WATCH_GROUP_KEY_PATTERN,
    FliggyWatchGroupCreate,
    FliggyWatchGroupPatch,
    create_fliggy_watch_group,
    delete_fliggy_watch_group,
    get_fliggy_watch_group_runtime,
    list_fliggy_watch_groups,
    update_fliggy_watch_group,
)
from app.domain.fliggy_ebooking_watch.scheduler import FliggyEbookingWatchScheduler
from app.domain.fliggy_ebooking_watch.scheduler_status import (
    get_fliggy_ebooking_scheduler_status,
)

router = APIRouter(prefix="/fliggy-ebk-watch", tags=["fliggy-ebk-watch"])
WATCH_GROUP_KEY_PATH = Path(pattern=WATCH_GROUP_KEY_PATTERN)


@router.get("/watch-groups")
async def list_fliggy_ebk_watch_groups() -> dict[str, list[dict[str, Any]]]:
    return {
        "watch_groups": [
            _attach_scheduler_status(group)
            for group in await list_fliggy_watch_groups()
        ]
    }


@router.post(
    "/watch-groups",
    dependencies=[Depends(require_operator_api_key)],
)
async def create_fliggy_ebk_watch_group(
    request: FliggyWatchGroupCreate,
) -> dict[str, Any]:
    try:
        watch_group = await create_fliggy_watch_group(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if watch_group is None:
        raise HTTPException(status_code=409, detail="飞猪 EBK 监控组已存在。")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.get("/watch-groups/{watch_group_key}")
async def get_fliggy_ebk_watch_group(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    group = await get_fliggy_watch_group_runtime(watch_group_key=watch_group_key)
    if group is None:
        raise HTTPException(status_code=404, detail="飞猪 EBK 监控组不存在。")
    return {
        "watch_group_key": watch_group_key,
        "ctrip_watch_group_key": group.ctrip_watch_group_key,
        "schedule_mode": group.schedule_mode,
        "schedule_start_time": group.schedule_start_time,
        "schedule_end_time": group.schedule_end_time,
        "schedule_interval_minutes": group.schedule_interval_minutes,
        "auto_poll_enabled": group.auto_poll_enabled,
        "enabled": group.enabled,
        "last_polled_at": group.last_polled_at.isoformat()
        if group.last_polled_at
        else None,
    }


@router.patch(
    "/watch-groups/{watch_group_key}",
    dependencies=[Depends(require_operator_api_key)],
)
async def update_fliggy_ebk_watch_group(
    patch: FliggyWatchGroupPatch,
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    try:
        watch_group = await update_fliggy_watch_group(
            watch_group_key=watch_group_key,
            patch=patch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if watch_group is None:
        raise HTTPException(status_code=404, detail="飞猪 EBK 监控组不存在。")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.delete(
    "/watch-groups/{watch_group_key}",
    dependencies=[Depends(require_operator_api_key)],
)
async def delete_fliggy_ebk_watch_group(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    watch_group = await delete_fliggy_watch_group(watch_group_key=watch_group_key)
    if watch_group is None:
        raise HTTPException(status_code=404, detail="飞猪 EBK 监控组不存在。")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.post(
    "/watch-groups/{watch_group_key}/run-now",
    dependencies=[Depends(require_operator_api_key)],
)
async def run_fliggy_ebk_watch_group_now(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    push_to_feishu: bool = Query(default=True),
    actor_id: str = Query(default="operator", min_length=1, max_length=128),
) -> dict[str, Any]:
    result = await FliggyEbookingWatchScheduler(tick_seconds=1).run_watch_group_now(
        watch_group_key=watch_group_key,
        push_to_feishu=push_to_feishu,
        actor_id=actor_id,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="飞猪 EBK 监控组不存在。")
    return result


@router.get("/watch-groups/{watch_group_key}/latest-snapshot")
async def get_fliggy_ebk_latest_snapshot(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    group = await get_fliggy_watch_group_runtime(watch_group_key=watch_group_key)
    if group is None:
        raise HTTPException(status_code=404, detail="飞猪 EBK 监控组不存在。")
    snapshot = await get_latest_ebk_snapshot(
        platform="fliggy",
        platform_account_id=group.platform_account_id,
    )
    return {"watch_group_key": watch_group_key, "snapshot": snapshot}


@router.get("/scheduler-status")
async def get_fliggy_ebk_scheduler_status() -> dict[str, Any]:
    return get_fliggy_ebooking_scheduler_status()


def _attach_scheduler_status(watch_group: dict[str, Any]) -> dict[str, Any]:
    status = get_fliggy_ebooking_scheduler_status()
    watch_group["scheduler_status"] = status["status"]
    watch_group["scheduler_heartbeat_age_seconds"] = status["heartbeat_age_seconds"]
    return watch_group
