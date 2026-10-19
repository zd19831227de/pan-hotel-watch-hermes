from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.competitor_prices.models import local_now

BigInt = BigInteger().with_variant(Integer, "sqlite")


class PricingStrategyConfig(Base):
    __tablename__ = "pricing_strategy_configs"
    __table_args__ = (
        Index("ix_pricing_strategy_configs_watch_group", "watch_group_key"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    strategy_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    watch_group_key: Mapped[str] = mapped_column(String(64), nullable=False)
    change_trigger_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="percent")
    change_trigger_percent: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    min_delta_yuan: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_delta_yuan: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    recommendation_step_yuan: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_suggested_change_yuan: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    allow_downward_suggestion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )
