from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = Integer().with_variant(Integer, "sqlite")


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone().replace(tzinfo=None)


class DistillSourceRecord(Base):
    """持久化蒸馏数据源：定时调度按 schedule_interval_minutes 周期执行。"""

    __tablename__ = "distill_sources"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(16))  # text / image_url / video_url
    content: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(16))  # doubao / qwen
    title: Mapped[str] = mapped_column(String(256), default="")
    distill_prompt: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_interval_minutes: Mapped[int] = mapped_column(Integer, default=120)  # 0=仅手动；默认 2 小时（F4-F）
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)


class DistillRunRecord(Base):
    """蒸馏运行记录：成功/拒绝/需人工/解析失败/失败，含提取文本与入库指针。"""

    __tablename__ = "distill_runs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    source_id: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    platform: Mapped[str] = mapped_column(String(16))
    source_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text, default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    knowledge_title: Mapped[str] = mapped_column(String(256), default="")
    rag_document_id: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
