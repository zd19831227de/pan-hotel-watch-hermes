from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.competitor_prices.models import local_now

BigInt = BigInteger().with_variant(Integer, "sqlite")


class RuntimeEmbeddingConfigRecord(Base):
    """V5 Step 4 — single-row config for the external embedding model.

    Independent from the LLM multi-model chain; the vector model only needs one
    endpoint. ``base_url`` is SSRF-validated on write via the service layer.
    """

    __tablename__ = "runtime_embedding_configs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openai")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="text-embedding-3-small")
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class RagFolderRecord(Base):
    """知识库文件夹：可独立新建/重命名/删除，删除后文档归入未分组。"""

    __tablename__ = "rag_folders"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class RagDocumentRecord(Base):
    __tablename__ = "rag_documents"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    folder: Mapped[str | None] = mapped_column(String(128), nullable=True)
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("rag_folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=local_now,
        onupdate=local_now,
    )


class RagChunkRecord(Base):
    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
