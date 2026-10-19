from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.db.session import ensure_db_ready, session_scope
from app.domain.competitor_prices.types import DEFAULT_WATCH_GROUP_KEY
from app.domain.pricing_strategy.models import PricingStrategyConfig

DEFAULT_STRATEGY_KEY = "default"
ChangeTriggerMode = Literal["percent", "amount"]


class PricingStrategyConfigPatch(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    watch_group_key: str | None = Field(default=None, min_length=1, max_length=64)
    change_trigger_mode: ChangeTriggerMode | None = None
    change_trigger_percent: float | None = Field(default=None, ge=0, le=100)
    min_delta_yuan: int | None = Field(default=None, ge=0, le=10000)
    max_delta_yuan: int | None = Field(default=None, ge=0, le=10000)
    recommendation_step_yuan: int | None = Field(default=None, ge=1, le=1000)
    max_suggested_change_yuan: int | None = Field(default=None, ge=0, le=10000)
    allow_downward_suggestion: bool | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> PricingStrategyConfigPatch:
        if (
            self.min_delta_yuan is not None
            and self.max_delta_yuan is not None
            and self.min_delta_yuan > self.max_delta_yuan
        ):
            raise ValueError("min_delta_yuan must be less than or equal to max_delta_yuan.")
        return self


class PricingStrategyDetail(BaseModel):
    id: int
    strategy_key: str
    name: str
    watch_group_key: str
    change_trigger_mode: ChangeTriggerMode
    change_trigger_percent: float
    min_delta_yuan: int
    max_delta_yuan: int
    recommendation_step_yuan: int
    max_suggested_change_yuan: int
    allow_downward_suggestion: bool
    enabled: bool
    summary: str
    trusted_source_statement: str = "仅使用可信 DOM 房价 room_rate_dom_display_price 生成建议"
    created_at: str
    updated_at: str


async def get_pricing_strategy_config(
    *,
    strategy_key: str = DEFAULT_STRATEGY_KEY,
) -> PricingStrategyDetail:
    await ensure_db_ready()
    async with session_scope() as session:
        strategy = await _get_or_create_strategy(session, strategy_key=strategy_key)
    return _strategy_to_detail(strategy)


async def update_pricing_strategy_config(
    *,
    patch: PricingStrategyConfigPatch,
    actor_id: str,
    strategy_key: str = DEFAULT_STRATEGY_KEY,
) -> PricingStrategyDetail:
    await ensure_db_ready()
    async with session_scope() as session:
        strategy = await _get_or_create_strategy(session, strategy_key=strategy_key)
        _apply_patch(strategy, patch)
        _validate_strategy(strategy)
        strategy.updated_by = actor_id.strip() or "operator"
        strategy.last_config_json = _strategy_to_dict(strategy, include_dates=False)
        await session.flush()
    return _strategy_to_detail(strategy)


async def get_pricing_strategy_runtime_config(
    *,
    strategy_key: str = DEFAULT_STRATEGY_KEY,
) -> PricingStrategyDetail:
    return await get_pricing_strategy_config(strategy_key=strategy_key)


async def _get_or_create_strategy(session, *, strategy_key: str) -> PricingStrategyConfig:
    strategy = await session.scalar(
        select(PricingStrategyConfig).where(PricingStrategyConfig.strategy_key == strategy_key)
    )
    if strategy is not None:
        return strategy
    strategy = PricingStrategyConfig(
        strategy_key=strategy_key,
        name="价格变化提醒策略",
        watch_group_key=DEFAULT_WATCH_GROUP_KEY,
        change_trigger_mode="percent",
        change_trigger_percent=5.0,
        min_delta_yuan=20,
        max_delta_yuan=10000,
        recommendation_step_yuan=10,
        max_suggested_change_yuan=30,
        allow_downward_suggestion=False,
        enabled=True,
    )
    strategy.last_config_json = _strategy_to_dict(strategy, include_dates=False)
    session.add(strategy)
    await session.flush()
    return strategy


def _apply_patch(strategy: PricingStrategyConfig, patch: PricingStrategyConfigPatch) -> None:
    update = patch.model_dump(exclude_unset=True)
    for field_name, value in update.items():
        if value is None:
            continue
        if field_name == "name":
            strategy.name = str(value).strip() or strategy.name
        elif field_name == "watch_group_key":
            strategy.watch_group_key = str(value).strip() or strategy.watch_group_key
        else:
            setattr(strategy, field_name, value)


def _validate_strategy(strategy: PricingStrategyConfig) -> None:
    if strategy.change_trigger_mode not in {"percent", "amount"}:
        raise ValueError("change_trigger_mode must be percent or amount.")
    if int(strategy.min_delta_yuan) > int(strategy.max_delta_yuan):
        raise ValueError("min_delta_yuan must be less than or equal to max_delta_yuan.")
    strategy.recommendation_step_yuan = max(1, int(strategy.recommendation_step_yuan or 1))
    strategy.max_suggested_change_yuan = max(0, int(strategy.max_suggested_change_yuan or 0))
    strategy.min_delta_yuan = max(0, int(strategy.min_delta_yuan or 0))
    strategy.max_delta_yuan = max(0, int(strategy.max_delta_yuan or 0))
    strategy.change_trigger_percent = float(strategy.change_trigger_percent or 0)


def _strategy_to_detail(strategy: PricingStrategyConfig) -> PricingStrategyDetail:
    payload = _strategy_to_dict(strategy, include_dates=True)
    payload["summary"] = _strategy_summary(payload)
    return PricingStrategyDetail(**payload)


def _strategy_to_dict(strategy: PricingStrategyConfig, *, include_dates: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": strategy.id or 0,
        "strategy_key": strategy.strategy_key,
        "name": strategy.name,
        "watch_group_key": strategy.watch_group_key,
        "change_trigger_mode": strategy.change_trigger_mode,
        "change_trigger_percent": float(strategy.change_trigger_percent or 0),
        "min_delta_yuan": int(strategy.min_delta_yuan or 0),
        "max_delta_yuan": int(strategy.max_delta_yuan or 0),
        "recommendation_step_yuan": int(strategy.recommendation_step_yuan or 1),
        "max_suggested_change_yuan": int(strategy.max_suggested_change_yuan or 0),
        "allow_downward_suggestion": bool(strategy.allow_downward_suggestion),
        "enabled": bool(strategy.enabled),
    }
    if include_dates:
        payload["created_at"] = strategy.created_at.isoformat()
        payload["updated_at"] = strategy.updated_at.isoformat()
    return payload


def _strategy_summary(payload: dict[str, Any]) -> str:
    if payload["change_trigger_mode"] == "amount":
        trigger = f"价格变化 {payload['min_delta_yuan']}-{payload['max_delta_yuan']} 元触发"
    else:
        trigger = f"价格变化达到 {payload['change_trigger_percent']:.2f}% 触发"
    direction = (
        "竞对降价时给出本店降价金额"
        if payload["allow_downward_suggestion"]
        else "竞对降价时只提醒关注，不给出降价金额"
    )
    return (
        f"{trigger}；建议降价按 {payload['recommendation_step_yuan']} 元取整；"
        f"单次建议最多 {payload['max_suggested_change_yuan']} 元；{direction}。"
    )
