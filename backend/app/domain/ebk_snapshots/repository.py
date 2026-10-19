from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, desc, select
from sqlalchemy.exc import IntegrityError

from app.db.session import ensure_db_ready, session_scope
from app.domain.ebk_snapshots.models import EbkReadSnapshot, local_now
from app.domain.ebk_snapshots.types import UnifiedEbkSnapshot, serialize_unified_ebk_snapshot


async def ensure_ebk_snapshot_schema_ready() -> None:
    await ensure_db_ready()


async def list_ebk_snapshots_with_payload_for_export(
    *,
    days: int = 180,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 1000,
    platforms: list[str] | None = None,
    shop_key: str | None = None,
) -> list[dict[str, Any]]:
    """整体业务数据导出：返回窗口内最新的全平台快照候选及脱敏 payload。

    shop_key 给定时只导出该店快照；历史 NULL 行（旧数据无归属）不混入（P7）。
    """

    await ensure_ebk_snapshot_schema_ready()
    cutoff = start_at or (
        local_now() - timedelta(days=max(1, min(int(days), 3650)))
    )
    statement = select(EbkReadSnapshot).where(EbkReadSnapshot.created_at >= cutoff)
    if end_at is not None:
        statement = statement.where(EbkReadSnapshot.created_at < end_at)
    if platforms:
        statement = statement.where(EbkReadSnapshot.platform.in_(platforms))
    if str(shop_key or "").strip():
        statement = statement.where(EbkReadSnapshot.shop_key == str(shop_key).strip())
    statement = (
        statement.order_by(EbkReadSnapshot.created_at.desc(), EbkReadSnapshot.id.desc())
        .limit(max(1, min(int(limit), 5000)))
    )
    async with session_scope() as session:
        rows = list((await session.scalars(statement)).all())
    return [
        {
            "id": row.id,
            "platform": row.platform,
            "platform_account_id": row.platform_account_id,
            "status": row.status,
            "payload_json": row.payload_json if isinstance(row.payload_json, dict) else {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


async def save_ebk_snapshot(
    snapshot: UnifiedEbkSnapshot,
    *,
    read_trigger: dict[str, Any] | None = None,
    source_watch_run_id: int | None = None,
    shop_key: str | None = None,
) -> dict[str, Any]:
    await ensure_ebk_snapshot_schema_ready()
    payload = serialize_unified_ebk_snapshot(snapshot)
    if isinstance(read_trigger, dict):
        trigger = {
            key: str(read_trigger[key]).strip()
            for key in ("actor_id", "via", "global_run_id")
            if str(read_trigger.get(key) or "").strip()
        }
        if trigger:
            payload["read_trigger"] = trigger
    dataset_count = len(snapshot.datasets or {})
    async with session_scope() as session:
        # 同一 watch run 的补存幂等：重试返回既有行，不新增（重构 P4 §5.1）。
        if isinstance(source_watch_run_id, int) and source_watch_run_id > 0:
            existing = (
                await session.scalars(
                    select(EbkReadSnapshot)
                    .where(EbkReadSnapshot.source_watch_run_id == source_watch_run_id)
                    .limit(1)
                )
            ).first()
            if existing is not None:
                return _row_to_dict(existing)
        row = EbkReadSnapshot(
            platform=snapshot.platform,
            platform_account_id=snapshot.platform_account_id,
            browser_session_id=snapshot.browser_session_id,
            status=snapshot.status,
            dataset_count=dataset_count,
            payload_json=payload,
            source_watch_run_id=source_watch_run_id if isinstance(source_watch_run_id, int) else None,
            shop_key=str(shop_key or "").strip() or None,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            # 并发下另一提交方已按同一 run 补存：唯一索引兜底，改读既有行。
            await session.rollback()
            if isinstance(source_watch_run_id, int) and source_watch_run_id > 0:
                winner = (
                    await session.scalars(
                        select(EbkReadSnapshot)
                        .where(EbkReadSnapshot.source_watch_run_id == source_watch_run_id)
                        .limit(1)
                    )
                ).first()
                if winner is not None:
                    return _row_to_dict(winner)
            raise
        return _row_to_dict(row)


async def get_latest_ebk_snapshot(
    *,
    platform: str,
    platform_account_id: str,
) -> dict[str, Any] | None:
    await ensure_ebk_snapshot_schema_ready()
    async with session_scope() as session:
        row = await session.scalar(
            _latest_query(platform=platform, platform_account_id=platform_account_id)
        )
        return _row_to_dict(row) if row is not None else None


async def get_latest_success_ebk_snapshot(
    *,
    platform: str,
    platform_account_id: str,
) -> dict[str, Any] | None:
    await ensure_ebk_snapshot_schema_ready()
    async with session_scope() as session:
        row = await session.scalar(
            _latest_query(
                platform=platform,
                platform_account_id=platform_account_id,
                status="success",
            )
        )
        return _row_to_dict(row) if row is not None else None


async def list_recent_success_ebk_snapshots(
    *,
    platform: str,
    platform_account_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """最近 N 条可用快照（新→旧）。环比基线按字段回溯用：单条快照可能缺模块。

    2026-09-11 审查：纳入 partial 快照——平台读取部分数据集失败时，成功的
    子集仍是有效基线（适配层本身按数据集级 status 过滤，失败数据集不产出
    垃圾行）。human_required/needs_review/error 属读取未成，仍排除。
    """
    await ensure_ebk_snapshot_schema_ready()
    clamped = max(1, min(int(limit), 10))
    async with session_scope() as session:
        statement = (
            select(EbkReadSnapshot)
            .where(
                EbkReadSnapshot.platform == platform,
                EbkReadSnapshot.platform_account_id == platform_account_id,
                EbkReadSnapshot.status.in_(("success", "partial")),
            )
            .order_by(desc(EbkReadSnapshot.created_at), desc(EbkReadSnapshot.id))
            .limit(clamped)
        )
        rows = list((await session.scalars(statement)).all())
    return [_row_to_dict(row) for row in rows]


def _latest_query(
    *,
    platform: str,
    platform_account_id: str,
    status: str | None = None,
) -> Select[tuple[EbkReadSnapshot]]:
    statement = select(EbkReadSnapshot).where(
        EbkReadSnapshot.platform == platform,
        EbkReadSnapshot.platform_account_id == platform_account_id,
    )
    if status is not None:
        statement = statement.where(EbkReadSnapshot.status == status)
    return statement.order_by(desc(EbkReadSnapshot.created_at), desc(EbkReadSnapshot.id)).limit(1)


def _row_to_dict(row: EbkReadSnapshot) -> dict[str, Any]:
    created_at = row.created_at.isoformat() if row.created_at is not None else None
    return {
        "id": row.id,
        "platform": row.platform,
        "platform_account_id": row.platform_account_id,
        "browser_session_id": row.browser_session_id,
        "status": row.status,
        "dataset_count": row.dataset_count,
        "payload_json": row.payload_json if isinstance(row.payload_json, dict) else {},
        "source_watch_run_id": row.source_watch_run_id,
        "shop_key": row.shop_key,
        "created_at": created_at,
    }

async def cleanup_expired_ebk_business_data(
    *,
    retention_days: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """清理超过留存天数的 EBK 业务历史数据（快照/运行记录）。
    覆盖两类业务历史：
      - ``ebk_read_snapshots``：各平台 EBK 读取快照
      - ``ctrip_ebooking_watch_runs`` 及其 ``dataset_results``：数据中心监控运行
    retention_days 为 0 时清空全部；否则删除早于 N 天的数据。
    只清理业务历史，不触碰凭证、配置或账号信息。
    """
    from app.domain.ctrip_ebooking_watch.models import (
        CtripEbookingWatchDatasetResult,
        CtripEbookingWatchRun,
    )

    await ensure_ebk_snapshot_schema_ready()
    days = int(retention_days)
    if days == 0:
        # 0 = 清空全部快照/运行记录
        cutoff = None
    else:
        days = max(1, min(days, 3650))
        cutoff = (now or local_now()) - timedelta(days=days)
    async with session_scope() as session:
        if cutoff is None:
            deleted_snapshots = (
                await session.execute(delete(EbkReadSnapshot))
            ).rowcount or 0
            old_run_ids = list(
                (await session.scalars(select(CtripEbookingWatchRun.id))).all()
            )
        else:
            deleted_snapshots = (
                await session.execute(
                    delete(EbkReadSnapshot).where(EbkReadSnapshot.created_at < cutoff)
                )
            ).rowcount or 0
            old_run_ids = list(
                (
                    await session.scalars(
                        select(CtripEbookingWatchRun.id).where(
                            CtripEbookingWatchRun.captured_at < cutoff
                        )
                    )
                ).all()
            )
        deleted_results = 0
        deleted_runs = 0
        if old_run_ids:
            deleted_results = (
                await session.execute(
                    delete(CtripEbookingWatchDatasetResult).where(
                        CtripEbookingWatchDatasetResult.run_id.in_(old_run_ids)
                    )
                )
            ).rowcount or 0
            deleted_runs = (
                await session.execute(
                    delete(CtripEbookingWatchRun).where(
                        CtripEbookingWatchRun.id.in_(old_run_ids)
                    )
                )
            ).rowcount or 0
    return {
        "deleted_snapshots": int(deleted_snapshots),
        "deleted_runs": int(deleted_runs),
        "deleted_dataset_results": int(deleted_results),
    }

