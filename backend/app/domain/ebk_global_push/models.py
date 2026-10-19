from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")


def local_now() -> datetime:
    from app.domain.meituan_ebooking_watch.models import local_now as _local_now

    return _local_now()


class EbkGlobalPushConfig(Base):
    """EBK 全局推送配置（设计文档 §7.1）：以携程监控组为锚点，一锚点一条记录。

    不复制平台账号、浏览器会话或 webhook——这些值每次运行时从当前携程组与
    平台挂载组解析。软停用即 ``enabled=false``，不删除记录。
    """

    __tablename__ = "ebk_global_push_configs"
    __table_args__ = (
        Index(
            "ix_ebk_global_push_configs_enabled",
            "enabled",
        ),
        Index(
            "ix_ebk_global_push_configs_shop_key",
            "shop_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    ctrip_watch_group_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # 店铺归属（重构 P5）：从锚点携程组的 shop_key 派生（写时同步），仅作
    # 筛选维度；权威源始终是锚点组，不在此独立配置。
    shop_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled_platforms_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    schedule_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="fixed_slots")
    push_times_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    window_start_time: Mapped[str] = mapped_column(String(5), nullable=False, default="06:00")
    window_end_time: Mapped[str] = mapped_column(String(5), nullable=False, default="23:00")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    card_title_business: Mapped[str | None] = mapped_column(String(128), nullable=True)
    card_title_promotion: Mapped[str | None] = mapped_column(String(128), nullable=True)
    card_title_advertising: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_successful_push_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=local_now, onupdate=local_now
    )


class EbkGlobalPushRun(Base):
    """EBK 全局推送运行记录（设计文档 §7.2）。

    ``(config_id, schedule_key)`` 唯一约束承担跨进程幂等；同配置互斥
    （同一时刻仅一条 ``running``）由仓储 claim 时的条件检查 + 唯一约束兜底。
    """

    __tablename__ = "ebk_global_push_runs"
    __table_args__ = (
        UniqueConstraint("config_id", "schedule_key", name="uq_ebk_global_push_run_slot"),
        Index(
            "ix_ebk_global_push_runs_config_created",
            "config_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    config_id: Mapped[int] = mapped_column(BigInt, nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduler")
    schedule_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    read_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    read_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    push_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    push_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    platform_results_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    card_results_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
