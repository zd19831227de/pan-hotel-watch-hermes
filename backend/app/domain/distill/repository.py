from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.session import ensure_db_ready, session_scope
from app.domain.distill.models import DistillRunRecord, DistillSourceRecord, local_now


async def create_distill_source(
    *,
    source_type: str,
    content: str,
    platform: str,
    title: str = "",
    distill_prompt: str = "",
    schedule_interval_minutes: int = 120,
    enabled: bool = True,
) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        record = DistillSourceRecord(
            source_type=source_type,
            content=content,
            platform=platform,
            title=title[:256],
            distill_prompt=distill_prompt,
            enabled=enabled,
            schedule_interval_minutes=schedule_interval_minutes,
        )
        session.add(record)
        await session.flush()
        return _source_dict(record)


async def get_distill_source(source_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(DistillSourceRecord, source_id)
        return _source_dict(record) if record is not None else None


async def list_distill_sources(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        query = select(DistillSourceRecord).order_by(DistillSourceRecord.id)
        if enabled_only:
            query = query.where(DistillSourceRecord.enabled.is_(True))
        records = (await session.scalars(query)).all()
        return [_source_dict(record) for record in records]


async def list_due_distill_sources(
    *, now_override: datetime | None = None
) -> list[dict[str, Any]]:
    """启用中且到期（从未跑过或距上次超过 schedule_interval_minutes）的数据源。"""
    await ensure_db_ready()
    now = now_override or local_now()
    async with session_scope() as session:
        query = select(DistillSourceRecord).where(
            DistillSourceRecord.enabled.is_(True),
            DistillSourceRecord.schedule_interval_minutes > 0,
        )
        records = (await session.scalars(query)).all()
        due = []
        for record in records:
            if record.last_run_at is None:
                due.append(record)
                continue
            last = record.last_run_at
            if last.tzinfo is not None:
                last = last.replace(tzinfo=None)
            if now - last >= timedelta(minutes=record.schedule_interval_minutes):
                due.append(record)
        return [_source_dict(record) for record in due]


async def set_distill_source_enabled(source_id: int, enabled: bool) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(DistillSourceRecord, source_id)
        if record is None:
            return None
        record.enabled = bool(enabled)
        await session.flush()
        return _source_dict(record)


async def update_distill_source_fields(
    source_id: int,
    *,
    enabled: bool | None = None,
    schedule_interval_minutes: int | None = None,
) -> dict[str, Any] | None:
    """更新数据源的可编辑字段（启停 / 调度间隔）；至少传一项，否则返回原记录。"""
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(DistillSourceRecord, source_id)
        if record is None:
            return None
        if enabled is not None:
            record.enabled = bool(enabled)
        if schedule_interval_minutes is not None:
            interval = int(schedule_interval_minutes)
            if interval < 0:
                raise ValueError("schedule_interval_minutes must be >= 0")
            record.schedule_interval_minutes = interval
        await session.flush()
        return _source_dict(record)


async def delete_distill_source(source_id: int) -> bool:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(DistillSourceRecord, source_id)
        if record is None:
            return False
        await session.delete(record)
        await session.flush()
        return True


async def mark_source_run(source_id: int, *, finished_at: datetime | None = None) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(DistillSourceRecord, source_id)
        if record is not None:
            record.last_run_at = finished_at or local_now()
            await session.flush()


async def create_distill_run(
    *,
    source_id: int | None,
    platform: str,
    source_type: str,
    status: str,
    summary: str = "",
    extracted_text: str = "",
    knowledge_title: str = "",
    rag_document_id: int | None = None,
    reject_reason: str | None = None,
) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        record = DistillRunRecord(
            source_id=source_id,
            platform=platform,
            source_type=source_type,
            status=status,
            summary=str(summary or "")[:2000],
            extracted_text=str(extracted_text or ""),
            knowledge_title=str(knowledge_title or "")[:256],
            rag_document_id=rag_document_id,
            reject_reason=reject_reason,
            created_at=local_now(),
        )
        session.add(record)
        await session.flush()
        return _run_dict(record)


async def get_distill_run(run_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(DistillRunRecord, run_id)
        return _run_dict(record) if record is not None else None


async def list_distill_runs(*, limit: int = 20) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        query = (
            select(DistillRunRecord)
            .order_by(DistillRunRecord.id.desc())
            .limit(max(1, min(int(limit), 100)))
        )
        records = (await session.scalars(query)).all()
        return [_run_dict(record) for record in records]


def _source_dict(record: DistillSourceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "source_type": record.source_type,
        "content": record.content,
        "platform": record.platform,
        "title": record.title or "",
        "distill_prompt": record.distill_prompt or "",
        "enabled": bool(record.enabled),
        "schedule_interval_minutes": record.schedule_interval_minutes,
        "last_run_at": record.last_run_at.isoformat() if record.last_run_at else None,
    }


def _run_dict(record: DistillRunRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "source_id": record.source_id,
        "platform": record.platform,
        "source_type": record.source_type,
        "status": record.status,
        "summary": record.summary or "",
        "extracted_text": record.extracted_text or "",
        "knowledge_title": record.knowledge_title or "",
        "rag_document_id": record.rag_document_id,
        "reject_reason": record.reject_reason,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
