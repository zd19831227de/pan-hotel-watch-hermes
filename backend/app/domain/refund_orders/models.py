from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


class RefundOrderSnapshot(Base):
    """当日退单快照：append-only，同日多次抓取各留一条，查询取最新。

    不进飞书推送，仅供飞书问答交互（Hermes 工具）调用。
    """

    __tablename__ = "refund_order_snapshots"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    refund_date: Mapped[str] = mapped_column(String(10), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    by_room_type_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    by_hour_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    time_caliber: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="push_read")
    # 店铺归属（重构 P3 §4.9.1）：新抓取行填充；历史行 NULL=旧数据无归属，单店查询排除。
    shop_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform_account_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    browser_session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
