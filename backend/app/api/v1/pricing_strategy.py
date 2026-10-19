from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.domain.pricing_strategy.repository import (
    PricingStrategyConfigPatch,
    get_pricing_strategy_config,
    update_pricing_strategy_config,
)

router = APIRouter(prefix="/pricing-strategy", tags=["pricing-strategy"])


class PricingStrategyConfigRequest(PricingStrategyConfigPatch):
    actor_id: str = "operator"


@router.get("/config")
async def get_pricing_strategy_config_route() -> dict[str, Any]:
    return {"strategy": (await get_pricing_strategy_config()).model_dump(mode="json")}


@router.patch("/config")
async def patch_pricing_strategy_config_route(
    request: PricingStrategyConfigRequest,
) -> dict[str, Any]:
    try:
        strategy = await update_pricing_strategy_config(
            patch=PricingStrategyConfigPatch(**request.model_dump(exclude={"actor_id"}, exclude_unset=True)),
            actor_id=_actor_id(request.actor_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"strategy": strategy.model_dump(mode="json")}


def _actor_id(value: str | None) -> str:
    return str(value or "").strip() or "operator"
