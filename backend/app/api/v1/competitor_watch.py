from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from app.api.security import require_operator_api_key
from app.domain.competitor_prices.repository import (
    create_competitor_watch_group,
    delete_competitor_hotel_config,
    delete_competitor_watch_group,
    get_competitor_watch_group_browser_pool,
    get_competitor_price_trend,
    get_competitor_watch_group_detail,
    get_latest_competitor_price_snapshot,
    list_competitor_hotels,
    list_competitor_watch_group_summaries,
    update_competitor_hotel_config,
    update_competitor_watch_group_browser_pool,
    update_competitor_watch_group,
    upsert_competitor_hotel_config,
)
from app.domain.competitor_prices.scheduler import CompetitorPriceScheduler
from app.domain.competitor_prices.scheduler_status import get_competitor_price_scheduler_status
from app.domain.competitor_prices.types import (
    WATCH_GROUP_KEY_PATTERN,
    CompetitorBrowserPoolUpdate,
    CompetitorHotelConfigIn,
    CompetitorHotelConfigPatch,
    CompetitorWatchGroupCreate,
    CompetitorWatchGroupPatch,
)

router = APIRouter(prefix="/ctrip-ebooking/competitor-watch-groups", tags=["competitor-watch"])
WATCH_GROUP_KEY_PATH = Path(pattern=WATCH_GROUP_KEY_PATTERN)


class CompetitorRunNowRequest(BaseModel):
    actor_id: str = "operator"


@router.get("")
async def list_competitor_watch_groups() -> dict[str, list[dict[str, Any]]]:
    return {
        "watch_groups": [
            _attach_scheduler_status(group)
            for group in await list_competitor_watch_group_summaries()
        ]
    }


@router.post("")
async def create_competitor_watch_group_route(
    request: CompetitorWatchGroupCreate,
) -> dict[str, Any]:
    try:
        watch_group = await create_competitor_watch_group(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if watch_group is None:
        raise HTTPException(status_code=409, detail="竞品监控组已存在。")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.get("/{watch_group_key}")
async def get_competitor_watch_group(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    watch_group = await get_competitor_watch_group_detail(
        watch_group_key=watch_group_key,
        create_if_missing=False,
    )
    if watch_group is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.patch("/{watch_group_key}")
async def patch_competitor_watch_group(
    request: CompetitorWatchGroupPatch,
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    try:
        watch_group = await update_competitor_watch_group(
            watch_group_key=watch_group_key,
            patch=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if watch_group is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.get("/{watch_group_key}/browser-pool")
async def get_competitor_browser_pool_route(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    payload = await get_competitor_watch_group_browser_pool(
        watch_group_key=watch_group_key,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return payload


@router.put("/{watch_group_key}/browser-pool")
async def put_competitor_browser_pool_route(
    request: CompetitorBrowserPoolUpdate,
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    try:
        payload = await update_competitor_watch_group_browser_pool(
            watch_group_key=watch_group_key,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return payload


@router.delete("/{watch_group_key}")
async def delete_competitor_watch_group_route(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    watch_group = await delete_competitor_watch_group(watch_group_key=watch_group_key)
    if watch_group is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return {"watch_group": _attach_scheduler_status(watch_group)}


@router.post(
    "/{watch_group_key}/run-now",
    dependencies=[Depends(require_operator_api_key)],
)
async def run_competitor_watch_now(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    push_to_feishu: bool = Query(default=True),
    request: CompetitorRunNowRequest | None = Body(default=None),
) -> dict[str, Any]:
    result = await CompetitorPriceScheduler(tick_seconds=1).run_watch_group_now(
        watch_group_key=watch_group_key,
        push_to_feishu=push_to_feishu,
        actor_id=_actor_id(request.actor_id if request is not None else None),
        source="api",
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return result


def _actor_id(value: str | None) -> str:
    return str(value or "").strip() or "operator"


@router.get("/{watch_group_key}/hotels")
async def get_competitor_hotels(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    include_disabled: bool = Query(default=False),
) -> dict[str, Any]:
    return {
        "watch_group_key": watch_group_key,
        "hotels": await list_competitor_hotels(
            watch_group_key=watch_group_key,
            include_disabled=include_disabled,
        ),
    }


@router.post("/{watch_group_key}/hotels")
async def post_competitor_hotel(
    request: CompetitorHotelConfigIn,
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
) -> dict[str, Any]:
    watch_group = await get_competitor_watch_group_detail(watch_group_key=watch_group_key)
    if watch_group is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    try:
        hotel = await upsert_competitor_hotel_config(
            platform_account_id=str(watch_group["platform_account_id"]),
            hotel=request,
            watch_group_key=watch_group_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"watch_group_key": watch_group_key, "hotel": hotel}


@router.patch("/{watch_group_key}/hotels/{hotel_config_id}")
async def patch_competitor_hotel(
    request: CompetitorHotelConfigPatch,
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    hotel_config_id: int = Path(ge=1),
) -> dict[str, Any]:
    try:
        hotel = await update_competitor_hotel_config(
            hotel_config_id=hotel_config_id,
            patch=request,
            watch_group_key=watch_group_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if hotel is None:
        raise HTTPException(status_code=404, detail="未找到竞品酒店配置。")
    return {"watch_group_key": watch_group_key, "hotel": hotel}


@router.delete("/{watch_group_key}/hotels/{hotel_config_id}")
async def delete_competitor_hotel(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    hotel_config_id: int = Path(ge=1),
) -> dict[str, Any]:
    try:
        hotel = await delete_competitor_hotel_config(
            hotel_config_id=hotel_config_id,
            watch_group_key=watch_group_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if hotel is None:
        raise HTTPException(status_code=404, detail="未找到竞品酒店配置。")
    return {"watch_group_key": watch_group_key, "hotel": hotel}


@router.get("/{watch_group_key}/latest")
async def get_latest_competitor_watch_snapshot(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    include_disabled: bool = Query(default=False),
) -> dict[str, Any]:
    snapshot = await get_latest_competitor_price_snapshot(
        watch_group_key=watch_group_key,
        include_disabled=include_disabled,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="还没有竞品价格读取记录。")
    return snapshot


@router.get("/{watch_group_key}/price-trend")
async def get_competitor_price_trend_route(
    watch_group_key: str = WATCH_GROUP_KEY_PATH,
    days: int = Query(default=7, ge=1, le=3650),
    granularity: Literal["run", "hour", "day"] = Query(default="run"),
    date_label: str | None = Query(default=None),
    breakfast_kind: str | None = Query(default=None),
    include_disabled: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        trend = await get_competitor_price_trend(
            watch_group_key=watch_group_key,
            days=days,
            granularity=granularity,
            date_label=date_label,
            breakfast_kind=breakfast_kind,
            include_disabled=include_disabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if trend is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    return trend


def _attach_scheduler_status(watch_group: dict[str, Any]) -> dict[str, Any]:
    return {
        **watch_group,
        "scheduler_status": get_competitor_price_scheduler_status(),
    }
