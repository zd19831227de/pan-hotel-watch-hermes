from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = Integer().with_variant(Integer, "sqlite")


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone().replace(tzinfo=None)


class PageStructureSnapshotRecord(Base):
    """每个 (platform, target) 的最新页面结构指纹，用于改版对比。"""

    __tablename__ = "page_structure_snapshots"
    __table_args__ = (UniqueConstraint("platform", "target", name="uq_page_structure_target"),)

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(128))
    structure_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)


class PageHealthAlertRecord(Base):
    """已发送的页面健康告警流水，用于 1 小时去重与审计。"""

    __tablename__ = "page_health_alerts"
    __table_args__ = (
        Index(
            "ix_page_health_alert_dedupe",
            "platform",
            "target",
            "problem_key",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(128))
    problem_key: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
