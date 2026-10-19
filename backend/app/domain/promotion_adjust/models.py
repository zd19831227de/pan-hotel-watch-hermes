from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.competitor_prices.models import local_now

BigInt = BigInteger().with_variant(Integer, "sqlite")


class PromotionAdjustPreviewRecord(Base):
    """促销折扣调整预览（来源：飞书 @机器人 指令）。"""

    __tablename__ = "promotion_adjust_preview_records"
    __table_args__ = (
        Index("ix_promotion_adjust_preview_records_chat_created", "chat_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    connection_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    can_auto_write_price: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    write_policy_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preview_candidate_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class PromotionAdjustApprovalRecord(Base):
    __tablename__ = "promotion_adjust_approval_records"
    __table_args__ = (
        Index(
            "ix_promotion_adjust_approval_records_preview_created",
            "preview_id",
            "created_at",
        ),
        Index(
            "ix_promotion_adjust_approval_records_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    preview_id: Mapped[int] = mapped_column(
        BigInt,
        ForeignKey("promotion_adjust_preview_records.id"),
        nullable=False,
    )
    approval_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="api")
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="operator")
    open_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    union_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execute_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class PromotionAdjustExecutionRecord(Base):
    __tablename__ = "promotion_adjust_execution_records"
    __table_args__ = (
        Index(
            "ix_promotion_adjust_execution_records_approval_created",
            "approval_record_id",
            "created_at",
        ),
        Index(
            "ix_promotion_adjust_execution_records_event_status",
            "event_type",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    approval_record_id: Mapped[int | None] = mapped_column(
        BigInt,
        ForeignKey("promotion_adjust_approval_records.id"),
        nullable=True,
    )
    preview_id: Mapped[int | None] = mapped_column(
        BigInt,
        ForeignKey("promotion_adjust_preview_records.id"),
        nullable=True,
    )
    source_execution_record_id: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="operator")
    platform_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execute_request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
