from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
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


class FliggyEbookingWatchGroup(Base):
    """飞猪 EBK 定时抓取监控组（店铺直连，v2 §2）。

    快照本体落在跨平台 ``ebk_read_snapshots``（platform='fliggy'），本表只
    承载调度配置与浏览器账号绑定。店铺归属走 ``shop_key`` 直连（每店至多
    一个）；``ctrip_watch_group_key`` 为 legacy 只读挂载键，仅迁移期兜底
    投递解析，新写入一律 ``shop_key``。直连组投递可用自身
    ``feishu_webhook_url``，未配置时回落锚点携程组。
    """

    __tablename__ = "fliggy_ebooking_watch_groups"
    __table_args__ = (
        Index(
            "ix_fliggy_ebooking_watch_groups_enabled_auto_poll",
            "enabled",
            "auto_poll_enabled",
        ),
        Index("ux_fliggy_ebk_groups_shop_key", "shop_key", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    group_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    brief_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 店铺直连归属（v2 §2）：每店至多一个飞猪监控组（唯一索引）；NULL=未归属。
    shop_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # legacy 挂载键（只读）：新写入一律 shop_key，此列仅迁移期兜底。
    ctrip_watch_group_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feishu_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feishu_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
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
