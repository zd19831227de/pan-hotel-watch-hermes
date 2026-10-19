from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.domain.competitor_prices.types import DEFAULT_WATCH_GROUP_KEY, WATCH_GROUP_KEY_PATTERN
from app.domain.price_change_alerts.service import (
    evaluate_price_change_alert,
    get_latest_price_change_alert,
    list_price_change_alerts,
)

router = APIRouter(prefix="/price-change-alerts", tags=["price-change-alerts"])


class PriceChangeAlertEvaluateRequest(BaseModel):
    watch_group_key: str = Field(default=DEFAULT_WATCH_GROUP_KEY, pattern=WATCH_GROUP_KEY_PATTERN)
    current_run_id: int | None = Field(default=None, ge=1)
    push_to_feishu: bool = False
    actor_id: str = "operator"


@router.post("/evaluate")
async def evaluate_price_change_alert_route(
    request: PriceChangeAlertEvaluateRequest,
) -> dict[str, Any]:
    try:
        result = await evaluate_price_change_alert(
            watch_group_key=request.watch_group_key,
            current_run_id=request.current_run_id,
            push_to_feishu=request.push_to_feishu,
            actor_id=_actor_id(request.actor_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"alert": result.model_dump(mode="json")}


@router.get("/latest")
async def get_latest_price_change_alert_route(
    watch_group_key: str | None = Query(default=None, pattern=WATCH_GROUP_KEY_PATTERN),
) -> dict[str, Any]:
    alert = await get_latest_price_change_alert(watch_group_key=watch_group_key)
    if alert is None:
        raise HTTPException(status_code=404, detail="还没有价格变化提醒记录。")
    return {"alert": alert}


@router.get("")
async def list_price_change_alerts_route(
    watch_group_key: str | None = Query(default=None, pattern=WATCH_GROUP_KEY_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return {
        "alerts": await list_price_change_alerts(
            watch_group_key=watch_group_key,
            limit=limit,
        )
    }


def _actor_id(value: str | None) -> str:
    return str(value or '').strip() or 'operator'
