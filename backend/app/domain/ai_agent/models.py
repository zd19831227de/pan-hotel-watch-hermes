from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.competitor_prices.models import local_now


class AiAgentConversation(Base):
    __tablename__ = "ai_agent_conversations"
    __table_args__ = (
        Index("ix_ai_agent_conversations_chat_open", "chat_id", "open_id"),
        Index("ix_ai_agent_conversations_updated_at", "updated_at"),
    )

    session_id: Mapped[str] = mapped_column(String(260), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    open_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )

    messages: Mapped[list["AiAgentMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AiAgentMessage.created_at",
    )


class AiAgentMessage(Base):
    __tablename__ = "ai_agent_messages"
    __table_args__ = (
        Index("ix_ai_agent_messages_session_created", "session_id", "created_at"),
    )

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(260),
        ForeignKey("ai_agent_conversations.session_id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)

    conversation: Mapped[AiAgentConversation] = relationship(back_populates="messages")
