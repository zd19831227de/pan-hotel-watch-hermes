from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


class CalendarPriceWatchConfig(Base):
    __tablename__ = "calendar_price_watch_configs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    watch_group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_watch_groups.id"),
        nullable=False,
        unique=True,
    )
    future_days: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    high_gap_threshold_yuan: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    low_gap_threshold_yuan: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    auto_poll_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schedule_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    alert_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    matrix_brief_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    matrix_brief_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    matrix_brief_push_times: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    matrix_brief_morning_push_time: Mapped[str] = mapped_column(
        String(5), nullable=False, default="10:00"
    )
    matrix_brief_evening_push_time: Mapped[str] = mapped_column(
        String(5), nullable=False, default="21:00"
    )
    matrix_brief_last_pushed_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class CalendarPriceRun(Base):
    __tablename__ = "calendar_price_runs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    watch_group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_watch_groups.id"),
        nullable=False,
    )
    config_id: Mapped[int] = mapped_column(
        ForeignKey("calendar_price_watch_configs.id"),
        nullable=False,
    )
    platform_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    future_days: Mapped[int] = mapped_column(Integer, nullable=False)
    high_gap_threshold_yuan: Mapped[int] = mapped_column(Integer, nullable=False)
    low_gap_threshold_yuan: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    push_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class CalendarPricePoint(Base):
    __tablename__ = "calendar_price_points"
    __table_args__ = (
        Index("ix_calendar_price_points_run_date", "run_id", "checkin_date"),
        Index(
            "ix_calendar_price_points_hotel_date_captured",
            "hotel_config_id",
            "checkin_date",
            "captured_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("calendar_price_runs.id"), nullable=False)
    watch_group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_watch_groups.id"),
        nullable=False,
    )
    hotel_config_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_hotels.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    hotel_alias: Mapped[str] = mapped_column(String(128), nullable=False)
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False)
    price_amount_yuan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    review_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class CalendarPriceAlert(Base):
    __tablename__ = "calendar_price_alerts"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("calendar_price_runs.id"), nullable=False)
    watch_group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_watch_groups.id"),
        nullable=False,
    )
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False)
    own_price_yuan: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_price_yuan: Mapped[int] = mapped_column(Integer, nullable=False)
    gap_yuan: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_hotel_alias: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
