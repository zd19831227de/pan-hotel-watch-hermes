from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.session import ensure_db_ready, session_scope
from app.domain.rag.models import (
    RagChunkRecord,
    RagDocumentRecord,
    RagFolderRecord,
    RuntimeEmbeddingConfigRecord,
)

DEFAULT_EMBEDDING_CONFIG_KEY = "default"


class PersistedEmbeddingConfig(dict):
    """Lightweight dict-like view of the embedding config row."""


async def get_persisted_embedding_config(
    *, config_key: str = DEFAULT_EMBEDDING_CONFIG_KEY
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.scalar(
            select(RuntimeEmbeddingConfigRecord).where(
                RuntimeEmbeddingConfigRecord.config_key == config_key
            )
        )
    if record is None:
        return None
    return _embedding_config_dict(record)


async def upsert_persisted_embedding_config(
    *,
    provider: str,
    model_name: str,
    base_url: str,
    api_key: str | None,
    dimensions: int | None,
    config_key: str = DEFAULT_EMBEDDING_CONFIG_KEY,
) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.scalar(
            select(RuntimeEmbeddingConfigRecord).where(
                RuntimeEmbeddingConfigRecord.config_key == config_key
            )
        )
        if record is None:
            record = RuntimeEmbeddingConfigRecord(
                config_key=config_key,
                provider=provider,
                model_name=model_name,
                base_url=base_url,
                api_key=api_key or None,
                dimensions=dimensions,
            )
            session.add(record)
        else:
            record.provider = provider
            record.model_name = model_name
            record.base_url = base_url
            if api_key:
                record.api_key = api_key
            record.dimensions = dimensions
        await session.flush()
        return _embedding_config_dict(record)


def _embedding_config_dict(record: RuntimeEmbeddingConfigRecord) -> dict[str, Any]:
    return {
        "provider": record.provider,
        "model_name": record.model_name,
        "base_url": record.base_url,
        "api_key": record.api_key or "",
        "dimensions": record.dimensions,
    }


async def create_rag_document(
    *,
    title: str,
    source_type: str,
    content_hash: str,
    raw_content: str,
    chunks: list[dict[str, Any]],
    folder: str | None = None,
) -> dict[str, Any]:
    await ensure_db_ready()
    try:
        async with session_scope() as session:
            folder_id = await _ensure_folder_id(session, folder)
            document = RagDocumentRecord(
                title=title,
                source_type=source_type,
                folder_id=folder_id,
                content_hash=content_hash,
                raw_content=raw_content,
                chunk_count=len(chunks),
            )
            session.add(document)
            await session.flush()
            for ordinal, chunk in enumerate(chunks):
                session.add(
                    RagChunkRecord(
                        document_id=document.id,
                        ordinal=ordinal,
                        text=chunk["text"],
                        embedding_json=chunk["embedding_json"],
                    )
                )
            await session.flush()
            return _document_dict(document, chunk_count=len(chunks), folder_name=folder)
    except IntegrityError as exc:  # pragma: no cover - defensive
        raise ValueError("RAG 文档写入失败。") from exc


async def list_rag_documents() -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        records = list(
            (
                await session.scalars(
                    select(RagDocumentRecord).order_by(
                        RagDocumentRecord.created_at.desc(), RagDocumentRecord.id.desc()
                    )
                )
            ).all()
        )
        folder_names = await _folder_name_map(session, records)
    return [
        _document_dict(record, folder_name=folder_names.get(record.folder_id))
        for record in records
    ]


async def get_rag_document(document_id: int) -> dict[str, Any] | None:
    """Return the full document detail, including ``raw_content``."""

    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(RagDocumentRecord, document_id)
        if record is None:
            return None
        folder_name = await _folder_name_for(session, record.folder_id)
        return _document_dict(record, include_raw=True, folder_name=folder_name)


async def update_rag_document(
    document_id: int,
    *,
    title: str,
    folder: str | None,
    raw_content: str | None = None,
    content_hash: str | None = None,
    chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Update document metadata and, when ``chunks`` is provided, replace all
    chunks (the caller has already re-chunked and re-embedded the content)."""

    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(RagDocumentRecord, document_id)
        if record is None:
            return None
        record.title = title
        record.folder_id = await _ensure_folder_id(session, folder)
        if raw_content is not None:
            record.raw_content = raw_content
        if content_hash is not None:
            record.content_hash = content_hash
        if chunks is not None:
            await session.execute(
                delete(RagChunkRecord).where(RagChunkRecord.document_id == document_id)
            )
            for ordinal, chunk in enumerate(chunks):
                session.add(
                    RagChunkRecord(
                        document_id=document_id,
                        ordinal=ordinal,
                        text=chunk["text"],
                        embedding_json=chunk["embedding_json"],
                    )
                )
            record.chunk_count = len(chunks)
        await session.flush()
        folder_name = await _folder_name_for(session, record.folder_id)
        return _document_dict(record, include_raw=True, folder_name=folder_name)


# --- 文件夹实体管理 ---


async def list_rag_folders() -> dict[str, Any]:
    """返回所有文件夹（含空文件夹）与未分组/总数统计。"""

    await ensure_db_ready()
    async with session_scope() as session:
        folders = list(
            (
                await session.scalars(
                    select(RagFolderRecord).order_by(
                        RagFolderRecord.sort_order.asc(), RagFolderRecord.id.asc()
                    )
                )
            ).all()
        )
        counts_rows = (
            await session.execute(
                select(
                    RagDocumentRecord.folder_id,
                    func.count(RagDocumentRecord.id),
                ).group_by(RagDocumentRecord.folder_id)
            )
        ).all()
    counts: dict[int | None, int] = {}
    for folder_id, count in counts_rows:
        counts[folder_id if folder_id is not None else None] = int(count or 0)
    return {
        "folders": [
            {
                "id": folder.id,
                "name": folder.name,
                "document_count": counts.get(folder.id, 0),
                "sort_order": folder.sort_order,
            }
            for folder in folders
        ],
        "unfiled_count": counts.get(None, 0),
        "total_count": sum(counts.values()),
    }


async def create_rag_folder(name: str) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        existing = await session.scalar(
            select(RagFolderRecord).where(RagFolderRecord.name == name)
        )
        if existing is not None:
            raise ValueError(f"文件夹「{name}」已存在。")
        max_order = await session.scalar(select(func.max(RagFolderRecord.sort_order))) or 0
        folder = RagFolderRecord(name=name, sort_order=max_order + 1)
        session.add(folder)
        await session.flush()
        return _folder_dict(folder, document_count=0)


async def rename_rag_folder(folder_id: int, name: str) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        folder = await session.get(RagFolderRecord, folder_id)
        if folder is None:
            return None
        collision = await session.scalar(
            select(RagFolderRecord).where(
                RagFolderRecord.name == name, RagFolderRecord.id != folder_id
            )
        )
        if collision is not None:
            raise ValueError(f"文件夹「{name}」已存在。")
        folder.name = name
        await session.flush()
        count = await session.scalar(
            select(func.count(RagDocumentRecord.id)).where(
                RagDocumentRecord.folder_id == folder_id
            )
        )
        return _folder_dict(folder, document_count=int(count or 0))


async def delete_rag_folder(folder_id: int) -> bool:
    """删除文件夹：其下文档归入未分组（folder_id 置空），不连带删除文档。"""

    await ensure_db_ready()
    async with session_scope() as session:
        folder = await session.get(RagFolderRecord, folder_id)
        if folder is None:
            return False
        await session.execute(
            update(RagDocumentRecord)
            .where(RagDocumentRecord.folder_id == folder_id)
            .values(folder_id=None)
        )
        await session.delete(folder)
        await session.flush()
    return True


async def delete_rag_document(document_id: int) -> bool:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(RagDocumentRecord, document_id)
        if record is None:
            return False
        # SQLite needs the cascade enforced explicitly (FKs may be off).
        await session.execute(
            delete(RagChunkRecord).where(RagChunkRecord.document_id == document_id)
        )
        await session.delete(record)
        await session.flush()
    return True


async def list_rag_chunks_with_documents() -> list[dict[str, Any]]:
    """Return every chunk joined to its document for in-memory retrieval."""

    await ensure_db_ready()
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(RagChunkRecord, RagDocumentRecord)
                .join(
                    RagDocumentRecord,
                    RagDocumentRecord.id == RagChunkRecord.document_id,
                )
                .order_by(RagChunkRecord.document_id.asc(), RagChunkRecord.ordinal.asc())
            )
        ).all()
    return [
        {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "title": document.title,
            "ordinal": chunk.ordinal,
            "text": chunk.text,
            "embedding_json": chunk.embedding_json,
        }
        for chunk, document in rows
    ]


def _document_dict(
    record: RagDocumentRecord,
    *,
    chunk_count: int | None = None,
    include_raw: bool = False,
    folder_name: str | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": record.id,
        "title": record.title,
        "source_type": record.source_type,
        "folder": folder_name,
        "content_hash": record.content_hash,
        "chunk_count": chunk_count if chunk_count is not None else record.chunk_count,
        "created_at": record.created_at.isoformat() if isinstance(record.created_at, datetime) else record.created_at,
        "updated_at": record.updated_at.isoformat() if isinstance(record.updated_at, datetime) else record.updated_at,
    }
    if include_raw:
        document["raw_content"] = record.raw_content
    return document


def _folder_dict(folder: RagFolderRecord, *, document_count: int) -> dict[str, Any]:
    return {
        "id": folder.id,
        "name": folder.name,
        "document_count": document_count,
        "sort_order": folder.sort_order,
    }


async def _ensure_folder_id(session: Any, folder_name: str | None) -> int | None:
    """Resolve a folder name to its id, creating the folder row if needed.
    Blank names mean 未分组 (None). Idempotent by unique name."""

    name = str(folder_name or "").strip()
    if not name:
        return None
    existing = await session.scalar(
        select(RagFolderRecord).where(RagFolderRecord.name == name)
    )
    if existing is not None:
        return existing.id
    max_order = await session.scalar(select(func.max(RagFolderRecord.sort_order))) or 0
    folder = RagFolderRecord(name=name, sort_order=max_order + 1)
    session.add(folder)
    await session.flush()
    return folder.id


async def _folder_name_for(session: Any, folder_id: int | None) -> str | None:
    if folder_id is None:
        return None
    folder = await session.get(RagFolderRecord, folder_id)
    return folder.name if folder is not None else None


async def _folder_name_map(
    session: Any, records: list[RagDocumentRecord]
) -> dict[int | None, str]:
    folder_ids = {record.folder_id for record in records if record.folder_id is not None}
    if not folder_ids:
        return {}
    folders = list(
        (
            await session.scalars(
                select(RagFolderRecord).where(RagFolderRecord.id.in_(folder_ids))
            )
        ).all()
    )
    return {folder.id: folder.name for folder in folders}
