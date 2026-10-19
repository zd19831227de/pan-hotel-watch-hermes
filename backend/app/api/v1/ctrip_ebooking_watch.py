from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.security import require_operator_api_key
from app.domain.ctrip_ebooking_watch.repository import (
    DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
    WATCH_GROUP_KEY_PATTERN,
    DataCenterWatchGroupCreate,
    DataCenterWatchDatasetPatch,
    DataCenterWatchGroupPatch,
    create_data_center_watch_group,
    delete_data_center_watch_group,
    get_data_center_watch_group_detail,
    get_latest_data_center_watch_run,
    list_data_center_watch_group_summaries,
    list_data_center_watch_datasets,
    list_data_center_watch_runs,
    update_data_center_watch_dataset,
    update_data_center_watch_group,
)
from app.domain.ctrip_ebooking_watch.scheduler import CtripEbookingDataCenterWatchScheduler
from app.domain.ctrip_ebooking_watch.scheduler_status import (
    get_data_center_watch_scheduler_status,
)

router = APIRouter(prefix="/ctrip-ebooking", tags=["ctrip-ebk-watch"])
WATCH_GROUP_KEY_PATH = Path(pattern=WATCH_GROUP_KEY_PATTERN)


@router.get("/data-center-watch-groups")
async def list_data_center_watch_groups() -> dict[str, list[dict[str, Any]]]:
    return {
        "watch_groups": [
            _attach_scheduler_status(group)
            for group in await list_data_center_watch_group_summaries()
        ]
    }


@router.post("/data-center-watch-groups")
async def create_ctrip_data_center_watch_group(
    request: DataCenterWatchGroupCreate,
) -> dict[str, Any]:
    try:
        watch_group = await create_data_center_watch_group(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if watch_group is None:
        raise HTTPException(status_code=409, detail="EBK 数据中心监控组已存在。")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.get("/data-center-watch-groups/{watch_group_key}")
async def get_ctrip_data_center_watch_group(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    try:
        watch_group = await get_data_center_watch_group_detail(watch_group_key=watch_group_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if watch_group is None:
        raise HTTPException(status_code=404, detail="Data-center watch group not found.")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.patch("/data-center-watch-groups/{watch_group_key}")
async def patch_ctrip_data_center_watch_group(
    request: DataCenterWatchGroupPatch,
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    try:
        watch_group = await update_data_center_watch_group(
            watch_group_key=watch_group_key,
            patch=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if watch_group is None:
        raise HTTPException(status_code=404, detail="Data-center watch group not found.")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.delete("/data-center-watch-groups/{watch_group_key}")
async def delete_ctrip_data_center_watch_group(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    watch_group = await delete_data_center_watch_group(watch_group_key=watch_group_key)
    if watch_group is None:
        raise HTTPException(status_code=404, detail="Data-center watch group not found.")
    # 设计 §5 P2：删除携程组需连带移除挂载的美团/飞猪定时监控（级联清理）。
    from app.domain.fliggy_ebooking_watch.repository import (
        delete_fliggy_watch_groups_by_ctrip_anchor,
    )
    from app.domain.meituan_ebooking_watch.repository import (
        delete_meituan_watch_groups_by_ctrip_anchor,
    )

    removed_meituan = await delete_meituan_watch_groups_by_ctrip_anchor(
        ctrip_watch_group_key=watch_group_key
    )
    removed_fliggy = await delete_fliggy_watch_groups_by_ctrip_anchor(
        ctrip_watch_group_key=watch_group_key
    )
    return {
        "watch_group": _attach_scheduler_status(watch_group),
        "removed_platform_schedules": {
            "meituan": [item["group_key"] for item in removed_meituan],
            "fliggy": [item["group_key"] for item in removed_fliggy],
        },
    }


@router.get("/data-center-watch-groups/{watch_group_key}/datasets")
async def get_ctrip_data_center_watch_datasets(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    try:
        datasets = await list_data_center_watch_datasets(watch_group_key=watch_group_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"watch_group_key": watch_group_key, "datasets": datasets}


@router.patch("/data-center-watch-groups/{watch_group_key}/datasets/{dataset_id}")
async def patch_ctrip_data_center_watch_dataset(
    request: DataCenterWatchDatasetPatch,
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    dataset_id: str = Path(min_length=1, max_length=64),
) -> dict[str, Any]:
    try:
        dataset = await update_data_center_watch_dataset(
            watch_group_key=watch_group_key,
            dataset_id=dataset_id,
            patch=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if dataset is None:
        raise HTTPException(status_code=404, detail="Data-center watch dataset not found.")
    return {"watch_group_key": watch_group_key, "dataset": dataset}


@router.post(
    "/data-center-watch-groups/{watch_group_key}/run-now",
    dependencies=[Depends(require_operator_api_key)],
)
async def run_ctrip_data_center_watch_group_now(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    push_to_feishu: bool = Query(default=True),
    actor_id: str = Query(default="operator", min_length=1, max_length=128),
) -> dict[str, Any]:
    result = await CtripEbookingDataCenterWatchScheduler(tick_seconds=1).run_watch_group_now(
        watch_group_key=watch_group_key,
        push_to_feishu=push_to_feishu,
        actor_id=actor_id,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Data-center watch group not found.")
    return result


@router.post(
    "/data-center-watch-groups/{watch_group_key}/daily-brief/run-now",
    dependencies=[Depends(require_operator_api_key)],
)
async def run_ctrip_data_center_daily_brief_now(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    result = await CtripEbookingDataCenterWatchScheduler(tick_seconds=1).run_daily_brief_now(
        watch_group_key=watch_group_key,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Data-center watch group not found.")
    return result


@router.get("/data-center-watch-groups/{watch_group_key}/latest")
async def get_latest_ctrip_data_center_watch_run(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    snapshot = await get_latest_data_center_watch_run(watch_group_key=watch_group_key)
    if snapshot is None:
        watch_group = await get_data_center_watch_group_detail(
            watch_group_key=watch_group_key,
            create_if_missing=False,
        )
        if watch_group is None:
            raise HTTPException(status_code=404, detail="Data-center watch group not found.")
        return {"watch_group_key": watch_group_key, "run": None, "dataset_results": []}
    return snapshot


@router.get("/data-center-watch-groups/{watch_group_key}/runs")
async def get_ctrip_data_center_watch_runs(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    runs = await list_data_center_watch_runs(watch_group_key=watch_group_key, limit=limit)
    if runs is None:
        raise HTTPException(status_code=404, detail="Data-center watch group not found.")
    return runs


def _attach_scheduler_status(watch_group: dict[str, Any]) -> dict[str, Any]:
    return {
        **watch_group,
        "scheduler_status": get_data_center_watch_scheduler_status(),
    }
