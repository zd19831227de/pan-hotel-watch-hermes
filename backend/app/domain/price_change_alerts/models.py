from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.competitor_prices.models import local_now

BigInt = BigInteger().with_variant(Integer, "sqlite")


class PriceChangeAlertRecord(Base):
    __tablename__ = "price_change_alert_records"
    __table_args__ = (
        Index("ix_price_change_alert_records_group_created", "watch_group_key", "created_at"),
        Index("ix_price_change_alert_records_status_created", "status", "created_at"),
        Index("ix_price_change_alert_records_current_run", "current_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    watch_group_key: Mapped[str] = mapped_column(String(64), nullable=False)
    current_run_id: Mapped[int | None] = mapped_column(
        BigInt,
        ForeignKey("ctrip_competitor_price_runs.id"),
        nullable=True,
    )
    previous_run_id: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    changed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suggestion_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    compared_key_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    suggestions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    feishu_push_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
