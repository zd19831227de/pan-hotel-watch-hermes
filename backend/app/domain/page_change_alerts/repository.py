from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.session import ensure_db_ready, session_scope
from app.domain.page_change_alerts.models import (
    PageHealthAlertRecord,
    PageStructureSnapshotRecord,
    local_now,
)


async def upsert_structure_snapshot(
    *,
    platform: str,
    target: str,
    structure_hash: str,
) -> dict[str, Any]:
    """删除一条页面健康告警记录，确认已处理后可移除。"""
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.scalar(
            select(PageStructureSnapshotRecord).where(
                PageStructureSnapshotRecord.platform == platform,
                PageStructureSnapshotRecord.target == target,
            )
        )
        previous_hash = record.structure_hash if record is not None else None
        changed = previous_hash is not None and previous_hash != structure_hash
        if record is None:
            session.add(
                PageStructureSnapshotRecord(
                    platform=platform,
                    target=target,
                    structure_hash=structure_hash,
                    captured_at=local_now(),
                    updated_at=local_now(),
                )
            )
        else:
            record.previous_hash = previous_hash
            record.structure_hash = structure_hash
            record.updated_at = local_now()
        await session.flush()
    return {"previous_hash": previous_hash, "changed": changed}


async def get_last_alert_time(
    *,
    platform: str,
    target: str,
    problem_key: str,
) -> datetime | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.scalar(
            select(PageHealthAlertRecord)
            .where(
                PageHealthAlertRecord.platform == platform,
                PageHealthAlertRecord.target == target,
                PageHealthAlertRecord.problem_key == problem_key,
            )
            .order_by(PageHealthAlertRecord.created_at.desc())
            .limit(1)
        )
    return record.created_at if record is not None else None


async def list_page_health_alerts(
    *,
    platform: str | None = None,
    level: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """查询已记录的页面健康告警（最新在前），供操作台只读展示。"""
    await ensure_db_ready()
    async with session_scope() as session:
        query = select(PageHealthAlertRecord).order_by(
            PageHealthAlertRecord.created_at.desc(),
            PageHealthAlertRecord.id.desc(),
        )
        if platform:
            query = query.where(PageHealthAlertRecord.platform == platform)
        if level:
            query = query.where(PageHealthAlertRecord.level == level)
        query = query.limit(max(1, min(int(limit), 200)))
        records = (await session.scalars(query)).all()
        return [
            {
                "id": record.id,
                "platform": record.platform,
                "target": record.target,
                "problem_key": record.problem_key,
                "level": record.level,
                "summary": record.summary or "",
                "created_at": record.created_at.isoformat() if record.created_at else None,
            }
            for record in records
        ]


async def delete_page_health_alert(alert_id: int) -> bool:
    """删除一条页面健康告警记录，确认已处理后可移除。"""
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PageHealthAlertRecord, int(alert_id))
        if record is None:
            return False
        await session.delete(record)
        await session.flush()
        return True


async def record_alert(
    *,
    platform: str,
    target: str,
    problem_key: str,
    level: str,
    summary: str,
) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        session.add(
            PageHealthAlertRecord(
                platform=platform,
                target=target,
                problem_key=problem_key,
                level=level,
                summary=str(summary or "")[:2000],
                created_at=local_now(),
            )
        )
        await session.flush()


def should_repeat_alert(last_alert_at: datetime | None, *, repeat_seconds: int) -> bool:
    """同一 (platform, target, problem) 在 repeat_seconds 内最多告一次。"""
    if last_alert_at is None:
        return True
    now = local_now()
    if last_alert_at.tzinfo is not None:
        now = datetime.now(last_alert_at.tzinfo).replace(tzinfo=None)
    return now - last_alert_at >= timedelta(seconds=repeat_seconds)
