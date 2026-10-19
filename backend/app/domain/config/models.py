from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.competitor_prices.models import local_now

BigInt = BigInteger().with_variant(Integer, "sqlite")


class RuntimeIntegrationConfigRecord(Base):
    __tablename__ = "runtime_integration_configs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    llm_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    llm_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    llm_model: Mapped[str] = mapped_column(String(128), nullable=False)
    llm_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class RuntimeLlmModelRecord(Base):
    """V5 Step 1 — per-model rows that form the LLM fallback chain.

    Each row is one model endpoint with its own provider/model_name/base_url/
    api_key. ``priority`` orders the fallback chain (lower = tried first).
    Health-check probes write ``last_health_status`` / ``last_health_summary``;
    these never contain the api_key.
    """

    __tablename__ = "runtime_llm_models"
    __table_args__ = (
        UniqueConstraint(
            "config_key",
            "provider",
            "model_name",
            "base_url",
            name="uq_runtime_llm_model_config_endpoint",
        ),
        Index(
            "ix_runtime_llm_models_config_priority",
            "config_key",
            "priority",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_health_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_health_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_health_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class FeishuAgentProfileRecord(Base):
    __tablename__ = "feishu_agent_profiles"

    profile_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credential_configured: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    app_id_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class FeishuChatAgentBindingRecord(Base):
    __tablename__ = "feishu_chat_agent_bindings"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    profile_key: Mapped[str] = mapped_column(
        ForeignKey("feishu_agent_profiles.profile_key", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class FeishuRoleAssignmentRecord(Base):
    __tablename__ = "feishu_role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "subject_type",
            "subject_ref",
            name="uq_feishu_role_assignment_subject",
        ),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
