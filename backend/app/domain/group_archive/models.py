"""群资料归档数据模型：群里人发的消息/数据/文档永久积累，永不自动清理。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = Integer().with_variant(Integer, "sqlite")


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone().replace(tzinfo=None)


class FeishuGroupArchiveMessage(Base):
    """一条群消息的归档主记录（message_id 全局唯一，重投直接去重）。"""

    __tablename__ = "feishu_group_archive_messages"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_id: Mapped[str] = mapped_column(String(64), default="")
    chat_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    chat_type: Mapped[str] = mapped_column(String(16), default="")
    message_type: Mapped[str] = mapped_column(String(24), default="", index=True)
    sender_open_id: Mapped[str] = mapped_column(String(64), default="")
    sender_type: Mapped[str] = mapped_column(String(16), default="")
    sent_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)
    # 本地日期（YYYY-MM-DD）：按天检索/积累的主过滤键。
    message_date: Mapped[str] = mapped_column(String(10), default="", index=True)
    text_content: Mapped[str] = mapped_column(Text, default="")
    raw_event: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=local_now)


class FeishuGroupArchiveAttachment(Base):
    """归档附件（file/image/media）：元数据必先落库，下载结果回写状态。

    下载失败只置 download_failed，记录与原始事件永不删除，可事后重试。
    """

    __tablename__ = "feishu_group_archive_attachments"
    __table_args__ = (
        UniqueConstraint("message_id", "file_key", name="uq_group_archive_attachment"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    file_key: Mapped[str] = mapped_column(String(128), default="")
    file_name: Mapped[str] = mapped_column(String(256), default="")
    attachment_type: Mapped[str] = mapped_column(String(16), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    saved_path: Mapped[str] = mapped_column(Text, default="")
    # pending / success / download_failed / skipped_no_downloader
    status: Mapped[str] = mapped_column(String(24), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=local_now)
