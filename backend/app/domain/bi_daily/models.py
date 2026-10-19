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
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


def iso_weekday_value(value: date) -> int:
    """weekday 口径：ISO 星期，1=周一 … 7=周日。"""
    return value.isoweekday()


class BookingPositionFact(Base):
    """本店预订位置事实：入住日维度、多 as-of 快照、append-only。

    历史入住日存平台实际值（离店/在店口径随 source 注明）；未来入住日
    自 2026-10 起有携程「房价房态日历」源（source=ctrip_inventory_calendar）：
    booked=页面已售、available_inventory=房型组共N间。occupancy_rate 只接受
    平台明示值（携程在店组出租率/市场分析我的出租率），不得用已售反推。
    """

    __tablename__ = "booking_position_facts"
    __table_args__ = (
        Index(
            "ix_booking_position_facts_shop_stay_asof",
            "shop_key",
            "stay_date",
            "as_of_time",
        ),
        UniqueConstraint("source_row_ref", name="uq_booking_position_facts_source_row_ref"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    shop_key: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    stay_date: Mapped[date] = mapped_column(Date, nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    room_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rate_plan: Mapped[str | None] = mapped_column(String(255), nullable=True)
    booked_room_nights: Mapped[int | None] = mapped_column(Integer, nullable=True)
    booking_orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    room_revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    adr: Mapped[float | None] = mapped_column(Float, nullable=True)
    # occupancy 只写平台明示值（携程在店组/市场分析），不用已售反推。
    occupancy_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_inventory: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remaining_inventory: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancellations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 源行幂等引用（如 "ebk_period_read:12"）；唯一约束防重复回灌。
    source_row_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    notes_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)

    __mapper_args__ = {"confirm_deleted_rows": False}


class CompetitorMarketFact(Base):
    """竞品市场事实：仅价格与可售性。

    occupancy_rate / booking_pace / rank 三列是预留位，禁止从价格推断，
    永远不写入值（ingest 测试断言恒 NULL）。
    """

    __tablename__ = "competitor_market_facts"
    __table_args__ = (
        Index("ix_competitor_market_facts_shop_stay", "shop_key", "stay_date"),
        Index(
            "ix_competitor_market_facts_hotel_stay_captured",
            "competitor_hotel_id",
            "stay_date",
            "captured_at",
        ),
        UniqueConstraint("source_row_ref", name="uq_competitor_market_facts_source_row_ref"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    shop_key: Mapped[str] = mapped_column(String(64), nullable=False)
    competitor_hotel_id: Mapped[int] = mapped_column(
        ForeignKey("ctrip_competitor_hotels.id"),
        nullable=False,
    )
    stay_date: Mapped[date] = mapped_column(Date, nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    room_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    breakfast_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # availability 口径沿用竞品盯盘：normal / tight / sold_out / unknown。
    availability: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    # ↓↓↓ 禁止从价格推断的预留列：恒 NULL ↓↓↓
    occupancy_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    booking_pace: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ↑↑↑ 恒 NULL 到出现真实数据源为止 ↑↑↑
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_row_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class BudgetForecastFact(Base):
    """预算 / demand forecast 事实：operator 导入，版本化管理。

    room_type 空串 = 全店汇总口径（NOT NULL 参与唯一约束，空串语义在
    报表层翻译为「全店」）。
    """

    __tablename__ = "budget_forecast_facts"
    __table_args__ = (
        UniqueConstraint(
            "shop_key",
            "stay_date",
            "room_type",
            "forecast_version",
            name="uq_budget_forecast_shop_stay_room_version",
        ),
        Index("ix_budget_forecast_facts_shop_stay", "shop_key", "stay_date"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    shop_key: Mapped[str] = mapped_column(String(64), nullable=False)
    stay_date: Mapped[date] = mapped_column(Date, nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    room_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    budget_room_nights: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_adr: Mapped[float | None] = mapped_column(Float, nullable=True)
    demand_forecast_room_nights: Mapped[int | None] = mapped_column(Integer, nullable=True)
    demand_forecast_revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    forecast_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class MarketDemandFact(Base):
    """市场需求事实：携程「市场分析·市场热度」页（SOA2 24588）只读采集。

    只存页面明示口径：城市/商圈热度分、游客出行热度（含去年同期）、
    我的 vs 竞争圈在手间夜/出租率（页面给定竞争圈口径）、竞争圈调价趋势、
    热点事件。禁止由价格推断任何字段。
    """

    __tablename__ = "market_demand_facts"
    __table_args__ = (
        Index(
            "ix_market_demand_facts_shop_stay_captured",
            "shop_key",
            "stay_date",
            "captured_at",
        ),
        UniqueConstraint("source_row_ref", name="uq_market_demand_facts_source_row_ref"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    shop_key: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False, default="ctrip")
    stay_date: Mapped[date] = mapped_column(Date, nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    city_heat: Mapped[float | None] = mapped_column(Float, nullable=True)
    city_heat_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    zone_heat: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_heat_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    travel_heat: Mapped[float | None] = mapped_column(Float, nullable=True)
    travel_heat_last_year: Mapped[float | None] = mapped_column(Float, nullable=True)
    my_booked_rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    my_occupancy_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    competitor_booked_rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    competitor_occupancy_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    competitor_price_trend: Mapped[float | None] = mapped_column(Float, nullable=True)
    competitor_price_trend_last_year: Mapped[float | None] = mapped_column(Float, nullable=True)
    events_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="bi_demand")
    source_row_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)

    __mapper_args__ = {"confirm_deleted_rows": False}


class WeekdayDateMapping(Base):
    """去年同期同星期映射：D-364（52 周整）优先，跨年取 D-371（53 周）。

    不使用「去年同月同日」。holiday_flag 当前无节假日数据源，恒 NULL 并在
    fallback_reason 注明，不猜测节假日属性。
    """

    __tablename__ = "weekday_date_mappings"
    __table_args__ = (
        UniqueConstraint("current_stay_date", name="uq_weekday_date_mapping_current"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    current_stay_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_year_same_weekday_stay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    matching_rule: Mapped[str] = mapped_column(String(32), nullable=False)
    holiday_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class BiImportAudit(Base):
    """预算/forecast 导入审计：文件名+sha256+行数统计+错误样本。

    只落文件名，不落主机路径；不落任何凭据。
    """

    __tablename__ = "bi_import_audits"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="budget_forecast")
    shop_key: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_valid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class BiCollectionRun(Base):
    """每日 BI 采集轮：覆盖率和数据新鲜度落地留痕。"""

    __tablename__ = "bi_collection_runs"
    __table_args__ = (
        UniqueConstraint("run_date", "source", name="uq_bi_collection_run_date_source"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduler")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    coverage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    shops_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    fact_rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class BiDailyCardPush(Base):
    """飞书 BI 日报推送幂等记录：(shop_key, push_date) 唯一。"""

    __tablename__ = "bi_daily_card_pushes"
    __table_args__ = (
        UniqueConstraint("shop_key", "push_date", name="uq_bi_daily_card_push_shop_date"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    shop_key: Mapped[str] = mapped_column(String(64), nullable=False)
    push_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sent")
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
