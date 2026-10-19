from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


class CtripEbookingWatchGroup(Base):
    __tablename__ = "ctrip_ebooking_watch_groups"
    __table_args__ = (
        Index("ux_ctrip_ebk_groups_shop_key", "shop_key", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    group_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    brief_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 店铺归属（重构 P4）：每店最多一个主携程锚点（唯一索引）；NULL=未归属。
    shop_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feishu_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feishu_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    query_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    auto_poll_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_brief_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_brief_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    daily_brief_push_time: Mapped[str] = mapped_column(String(5), nullable=False, default="08:30")
    daily_brief_last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )

    datasets: Mapped[list[CtripEbookingWatchDataset]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    runs: Mapped[list[CtripEbookingWatchRun]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )


class CtripEbookingWatchDataset(Base):
    __tablename__ = "ctrip_ebooking_watch_datasets"
    __table_args__ = (
        UniqueConstraint("group_id", "dataset_id"),
        Index(
            "ix_ctrip_ebooking_watch_datasets_group_enabled",
            "group_id",
            "enabled",
            "push_enabled",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_ebooking_watch_groups.id"),
        nullable=False,
    )
    dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_label: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )

    group: Mapped[CtripEbookingWatchGroup] = relationship(back_populates="datasets")


class CtripEbookingWatchRun(Base):
    __tablename__ = "ctrip_ebooking_watch_runs"
    __table_args__ = (
        Index("ix_ctrip_ebooking_watch_runs_group_captured", "group_id", "captured_at"),
        Index("ix_ctrip_ebooking_watch_runs_status_captured", "status", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_ebooking_watch_groups.id"),
        nullable=False,
    )
    platform_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="api")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    human_required_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    push_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)

    group: Mapped[CtripEbookingWatchGroup] = relationship(back_populates="runs")
    results: Mapped[list[CtripEbookingWatchDatasetResult]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class CtripEbookingWatchDatasetResult(Base):
    __tablename__ = "ctrip_ebooking_watch_dataset_results"
    __table_args__ = (
        Index("ix_ctrip_ebooking_watch_dataset_results_run", "run_id"),
        Index(
            "ix_ctrip_ebooking_watch_dataset_results_group_dataset_captured",
            "group_id",
            "dataset_id",
            "captured_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_ebooking_watch_runs.id"),
        nullable=False,
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_ebooking_watch_groups.id"),
        nullable=False,
    )
    dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_label: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    data_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pushed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    push_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)

    run: Mapped[CtripEbookingWatchRun] = relationship(back_populates="results")
