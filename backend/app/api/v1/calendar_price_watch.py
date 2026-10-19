from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from app.api.security import require_operator_api_key
from app.domain.calendar_price_watch.repository import (
    get_calendar_price_watch_config,
    get_latest_calendar_price_matrix,
    list_calendar_price_alerts,
    list_calendar_price_watch_config_summaries,
    update_calendar_price_watch_config,
)
from app.domain.calendar_price_watch.scheduler import CalendarPriceWatchScheduler
from app.domain.calendar_price_watch.scheduler_status import get_calendar_price_scheduler_status
from app.domain.calendar_price_watch.types import CalendarPriceWatchConfigPatch
from app.domain.competitor_prices.types import WATCH_GROUP_KEY_PATTERN

router = APIRouter(prefix="/calendar-price-watch", tags=["calendar-price-watch"])
WATCH_GROUP_KEY_PATH = Path(pattern=WATCH_GROUP_KEY_PATTERN)


class CalendarRunNowRequest(BaseModel):
    actor_id: str = "operator"


@router.get("/configs")
async def list_calendar_configs_route() -> dict[str, Any]:
    """全部竞品组的日历价监控配置摘要（顶部状态条聚合展示用）。"""

    return {"configs": await list_calendar_price_watch_config_summaries()}


@router.get("/{watch_group_key}/config")
async def get_calendar_config(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    config = await get_calendar_price_watch_config(watch_group_key=watch_group_key)
    if config is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return {"config": config}


@router.patch("/{watch_group_key}/config")
async def patch_calendar_config(
    request: CalendarPriceWatchConfigPatch,
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    config = await update_calendar_price_watch_config(
        watch_group_key=watch_group_key,
        patch=request,
    )
    if config is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return {"config": config}


@router.post(
    "/{watch_group_key}/run-now",
    dependencies=[Depends(require_operator_api_key)],
)
async def run_calendar_watch_now(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    push_to_feishu: bool = Query(default=True),
    request: CalendarRunNowRequest = Body(default_factory=CalendarRunNowRequest),
) -> dict[str, Any]:
    result = await CalendarPriceWatchScheduler().run_watch_group_now(
        watch_group_key,
        push_to_feishu=push_to_feishu,
        actor_id=request.actor_id,
        source="api",
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result.get("summary") or "未找到竞品监控组。")
    return {"result": result}


@router.post("/{watch_group_key}/matrix-brief/push-now")
async def push_calendar_matrix_brief_now(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    request: CalendarRunNowRequest = Body(default_factory=CalendarRunNowRequest),
) -> dict[str, Any]:
    result = await CalendarPriceWatchScheduler().run_matrix_brief_now(
        watch_group_key,
        actor_id=request.actor_id,
        source="api",
        slot_time=None,
        slot_label="手动矩阵简报",
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result.get("summary") or "未找到竞品监控组。")
    return {"result": result}


@router.get("/{watch_group_key}/latest")
async def get_latest_calendar_matrix(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    matrix = await get_latest_calendar_price_matrix(watch_group_key=watch_group_key)
    if matrix is None:
        raise HTTPException(status_code=404, detail="暂无日历价读取结果。")
    return {"matrix": matrix}


@router.get("/{watch_group_key}/alerts")
async def list_calendar_alerts(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    alerts = await list_calendar_price_alerts(watch_group_key=watch_group_key, limit=limit)
    return {"alerts": alerts}


@router.get("/scheduler-status")
async def get_scheduler_status_route() -> dict[str, Any]:
    return {"scheduler": get_calendar_price_scheduler_status()}
