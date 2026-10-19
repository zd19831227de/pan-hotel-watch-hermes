from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


class EbkReadSnapshot(Base):
    __tablename__ = "ebk_read_snapshots"
    __table_args__ = (
        Index(
            "ix_ebk_read_snapshots_platform_account_created",
            "platform",
            "platform_account_id",
            "created_at",
        ),
        Index(
            "ix_ebk_read_snapshots_platform_account_status_created",
            "platform",
            "platform_account_id",
            "status",
            "created_at",
        ),
        # 同一 watch run 只允许一条补存快照（幂等去重，NULL 不受限）。
        Index(
            "ux_ebk_read_snapshots_source_run",
            "source_watch_run_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    browser_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 快照来源与店铺归属（重构 P4 §5.1）：watch run 补存填 source_watch_run_id
    # （唯一，重试幂等）；统一读取直存时为 NULL。历史行 NULL=旧数据无归属。
    source_watch_run_id: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    shop_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
