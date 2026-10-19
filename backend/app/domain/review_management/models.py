from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "hotel_id",
            "review_id",
            name="uq_review_items_platform_hotel_review",
        ),
        Index("ix_review_items_platform_status", "platform", "reply_status"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hotel_id: Mapped[str] = mapped_column(String(128), nullable=False)
    review_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_nickname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reply_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class ReplyTemplate(Base):
    __tablename__ = "reply_templates"
    __table_args__ = (
        Index(
            "ix_reply_templates_platform_category_priority",
            "platform",
            "category",
            "priority",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    score_range_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class ReviewReplyApproval(Base):
    __tablename__ = "review_reply_approvals"
    __table_args__ = (
        Index("ix_review_reply_approvals_platform_status", "platform", "status"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_item_id: Mapped[int] = mapped_column(
        ForeignKey("review_items.id"),
        nullable=False,
    )
    generated_reply_content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decision_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    operator_open_id_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feishu_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    card_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submit_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class ReviewAutoReplyConfig(Base):
    __tablename__ = "review_auto_reply_configs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    auto_reply_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="off")
    auto_fetch_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fetch_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # 半自动/全自动改为每日 HH:MM 定时触发（2026-09-16），间隔字段仅保留兼容。
    auto_process_time: Mapped[str] = mapped_column(String(5), nullable=False, default="23:00")
    last_auto_processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approval_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class ReviewStoreBinding(Base):
    """评价管理的店铺绑定（同 EBK：一个 EBK 浏览器账号 = 一个店铺）。

    feishu_chat_id 是该店铺的飞书推送机器人目标群（semi_auto 审批卡推送地址），
    与 EBK 监控组的 feishu_chat_id 同义。
    """

    __tablename__ = "review_store_bindings"
    __table_args__ = (
        Index("ix_review_store_bindings_platform_enabled", "platform", "enabled"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    browser_session_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    platform_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    feishu_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    auto_reply_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="off")
    auto_fetch_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fetch_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # 每日定时触发时间（HH:MM）+ 当日游标：半自动/全自动链路到点执行一次。
    auto_process_time: Mapped[str] = mapped_column(String(5), nullable=False, default="23:00")
    last_auto_processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 半自动收敛为数量提醒：抓取后只记未回复数量，不落评价正文（避免堆积）。
    last_pending_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        Index("ix_review_tasks_platform_created", "platform", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    task_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReviewDailyCardPush(Base):
    """评价日报合并卡的当日去重：每群（chat_id）每天最多推一张。"""

    __tablename__ = "review_daily_card_pushes"
    __table_args__ = (
        UniqueConstraint("chat_id", "push_date", name="uq_review_daily_card_chat_date"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    push_date: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
