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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


class CtripCompetitorWatchGroup(Base):
    __tablename__ = "ctrip_competitor_watch_groups"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    group_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    brief_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    price_change_alert_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 可选店铺归属（重构 P5）：筛选维度，不改变竞品组的全局对象语义。
    shop_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    platform_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    browser_rotation_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="fixed",
    )
    pool_failover_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pool_cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    pricing_platform_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pricing_browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feishu_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    feishu_mobile_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    feishu_desktop_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    feishu_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feishu_card_layout: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="mobile_blocks",
    )
    push_date_scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="today_tomorrow",
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    query_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    schedule_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="fixed_slots")
    schedule_start_time: Mapped[str] = mapped_column(String(5), nullable=False, default="06:00")
    schedule_end_time: Mapped[str] = mapped_column(String(5), nullable=False, default="01:00")
    schedule_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    auto_poll_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )

    hotels: Mapped[list[CtripCompetitorHotel]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    runs: Mapped[list[CtripCompetitorPriceRun]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    browser_pool_members: Mapped[list[CtripCompetitorWatchGroupBrowserPoolMember]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )


class CtripCompetitorWatchGroupBrowserPoolMember(Base):
    __tablename__ = "ctrip_competitor_watch_group_browser_pool_members"
    __table_args__ = (
        UniqueConstraint("group_id", "browser_session_id"),
        Index(
            "ix_ctrip_competitor_browser_pool_group_enabled_sort",
            "group_id",
            "enabled",
            "sort_order",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_watch_groups.id"),
        nullable=False,
    )
    browser_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_selected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )

    group: Mapped[CtripCompetitorWatchGroup] = relationship(back_populates="browser_pool_members")


class CtripCompetitorBrowserLease(Base):
    __tablename__ = "ctrip_competitor_browser_leases"
    __table_args__ = (
        UniqueConstraint("browser_session_id"),
        Index("ix_ctrip_competitor_browser_leases_expires", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    browser_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    watch_group_key: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class CtripCompetitorHotel(Base):
    __tablename__ = "ctrip_competitor_hotels"
    __table_args__ = (
        UniqueConstraint("group_id", "ctrip_hotel_id"),
        Index("ix_ctrip_competitor_hotels_group_enabled_sort", "group_id", "enabled", "sort_order"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_watch_groups.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    short_alias: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hotel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ctrip_hotel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    city_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    city_en_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )

    group: Mapped[CtripCompetitorWatchGroup] = relationship(back_populates="hotels")


class CtripCompetitorPriceRun(Base):
    __tablename__ = "ctrip_competitor_price_runs"
    __table_args__ = (
        Index("ix_ctrip_competitor_price_runs_group_captured", "group_id", "captured_at"),
        Index("ix_ctrip_competitor_price_runs_status_captured", "status", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_watch_groups.id"),
        nullable=False,
    )
    platform_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="api")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    raw_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    push_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)

    group: Mapped[CtripCompetitorWatchGroup] = relationship(back_populates="runs")
    snapshots: Mapped[list[CtripCompetitorHotelSnapshot]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class CtripCompetitorHotelSnapshot(Base):
    __tablename__ = "ctrip_competitor_hotel_snapshots"
    __table_args__ = (
        Index("ix_ctrip_competitor_hotel_snapshots_run", "run_id"),
        Index("ix_ctrip_competitor_hotel_snapshots_group_checkin", "group_id", "checkin_date"),
        Index(
            "ix_ctrip_competitor_hotel_snapshots_hotel_checkin_captured",
            "hotel_config_id",
            "checkin_date",
            "captured_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_price_runs.id"),
        nullable=False,
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_watch_groups.id"),
        nullable=False,
    )
    hotel_config_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_hotels.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    date_label: Mapped[str] = mapped_column(String(16), nullable=False)
    checkin_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    checkout_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    hotel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ctrip_hotel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_status: Mapped[str] = mapped_column(String(32), nullable=False)
    review_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parser_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    page_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    raw_snippets_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[CtripCompetitorPriceRun] = relationship(back_populates="snapshots")
    room_rates: Mapped[list[CtripCompetitorRoomRate]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )


class CtripCompetitorRoomRate(Base):
    __tablename__ = "ctrip_competitor_room_rates"
    __table_args__ = (
        Index("ix_ctrip_competitor_room_rates_run", "run_id"),
        Index("ix_ctrip_competitor_room_rates_snapshot", "snapshot_id"),
        Index(
            "ix_ctrip_competitor_room_rates_hotel_breakfast_created",
            "hotel_config_id",
            "breakfast_kind",
            "created_at",
        ),
        Index("ix_ctrip_competitor_room_rates_price", "price_amount_yuan"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_hotel_snapshots.id"),
        nullable=False,
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_price_runs.id"),
        nullable=False,
    )
    hotel_config_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_hotels.id"),
        nullable=False,
    )
    room_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    breakfast_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    breakfast_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    price_amount_yuan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_amount_yuan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_display: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    availability_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    availability_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_price_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_discount_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_room_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_breakfast_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)

    snapshot: Mapped[CtripCompetitorHotelSnapshot] = relationship(back_populates="room_rates")
