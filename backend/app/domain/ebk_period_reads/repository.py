from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, or_, select

from app.db.session import ensure_db_ready, session_scope

from .models import EbkPeriodDaySnapshotRun, EbkPeriodReadSnapshot, local_now, local_today_start


async def append_period_read_snapshot(
    *,
    platform: str,
    data_kind: str,
    period: str,
    status: str = "ok",
    caliber: str | None = None,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
    source: str = "manual",
    captured_at: datetime | None = None,
    business_date: date | None = None,
    platform_account_id: str | None = None,
) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        row = EbkPeriodReadSnapshot(
            platform=platform,
            data_kind=data_kind,
            period=period,
            platform_account_id=platform_account_id,
            status=status,
            caliber=caliber,
            summary=summary,
            payload_json=payload,
            source=source,
            business_date=business_date,
            captured_at=captured_at or local_now(),
        )
        session.add(row)
        await session.flush()
        return _to_dict(row)


async def get_latest_period_read(
    *,
    platform: str,
    data_kind: str,
    period: str,
    fresh_only: bool = True,
    platform_account_id: str | None = None,
) -> dict[str, Any] | None:
    """取该 (platform, data_kind, period) 的最新快照。

    fresh_only=True 时只认 captured_at 在当天（本地 Asia/Shanghai）的快照——
    没有当日数据即视为过期，调用方应触发浏览器重读。
    platform_account_id 给定时按店铺过滤（多店铺同维度快照并存）。
    """
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(EbkPeriodReadSnapshot).where(
            EbkPeriodReadSnapshot.platform == platform,
            EbkPeriodReadSnapshot.data_kind == data_kind,
            EbkPeriodReadSnapshot.period == period,
        )
        if platform_account_id:
            stmt = stmt.where(
                EbkPeriodReadSnapshot.platform_account_id == platform_account_id
            )
        if fresh_only:
            stmt = stmt.where(EbkPeriodReadSnapshot.captured_at >= local_today_start())
        stmt = stmt.order_by(EbkPeriodReadSnapshot.id.desc()).limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()
        return _to_dict(row) if row is not None else None


def _to_dict(row: EbkPeriodReadSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform": row.platform,
        "data_kind": row.data_kind,
        "period": row.period,
        "platform_account_id": row.platform_account_id,
        "status": row.status,
        "caliber": row.caliber,
        "summary": row.summary,
        "payload": row.payload_json or {},
        "source": row.source,
        "captured_at": _format_datetime(row.captured_at),
        "business_date": row.business_date.isoformat() if row.business_date else None,
    }


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value is not None else None


async def list_period_reads_by_captured_range(
    *,
    platform: str | None = None,
    data_kind: str | None = None,
    date_from: str,
    date_to: str,
    platform_accounts: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """按业务日期范围取历史快照（升序）。

    date_from/date_to 指业务日期窗口，不做 fresh 校验、不区分 period：
    - period=day 快照带 business_date 时按页面真实数据日期过滤；无
      business_date（历史行/未提取到日期）回落抓取日 - 1（[from+1, to+2)）。
      此前 9 点前美团按日现读把前前日数据按 captured-1 标成昨日口径污染
      历史——现按页面数据日期落库，前前日数据不再错挂昨日业务日。
    - week/month 快照无独立业务日期列，按抓取时间窗 [from, to+1) 过滤，
      实际覆盖区间由各快照 caliber/data_period_label 标注，调用方据此解读。
    platform_accounts（chat 店铺过滤）给定时只返回 (platform, account) 命中
    的行，不在其中的平台整段排除（多店铺历史互不串读）。
    """
    from datetime import datetime, timedelta

    start = datetime.strptime(date_from, "%Y-%m-%d")
    end = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    day_shift = timedelta(days=1)
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(EbkPeriodReadSnapshot).where(
            or_(
                # day 快照有页面数据日期：按真实业务日期过滤。
                and_(
                    EbkPeriodReadSnapshot.period == "day",
                    EbkPeriodReadSnapshot.business_date.is_not(None),
                    EbkPeriodReadSnapshot.business_date >= start.date(),
                    EbkPeriodReadSnapshot.business_date < end.date(),
                ),
                # day 快照无 business_date（历史行/未提取到日期）：回落 captured-1。
                and_(
                    EbkPeriodReadSnapshot.period == "day",
                    EbkPeriodReadSnapshot.business_date.is_(None),
                    EbkPeriodReadSnapshot.captured_at >= start + day_shift,
                    EbkPeriodReadSnapshot.captured_at < end + day_shift,
                ),
                and_(
                    EbkPeriodReadSnapshot.period != "day",
                    EbkPeriodReadSnapshot.captured_at >= start,
                    EbkPeriodReadSnapshot.captured_at < end,
                ),
            )
        )
        if platform:
            stmt = stmt.where(EbkPeriodReadSnapshot.platform == platform)
        if data_kind:
            stmt = stmt.where(EbkPeriodReadSnapshot.data_kind == data_kind)
        if platform_accounts:
            stmt = stmt.where(
                or_(
                    *[
                        and_(
                            EbkPeriodReadSnapshot.platform == bound_platform,
                            EbkPeriodReadSnapshot.platform_account_id == account_id,
                        )
                        for bound_platform, account_id in platform_accounts.items()
                    ]
                )
            )
        stmt = stmt.order_by(
            EbkPeriodReadSnapshot.captured_at.asc(), EbkPeriodReadSnapshot.id.asc()
        )
        rows = list((await session.scalars(stmt)).all())
    # day 快照同业务日可能有多条（09:30 日终轮 + 手动现读），每业务日只
    # 保留最新一条；业务日优先用 business_date（页面真实数据日期），
    # 无则回落抓取日，避免前前日数据与昨日口径混在同一业务日。
    latest_day_by_date: dict[tuple[Any, ...], EbkPeriodReadSnapshot] = {}
    others: list[EbkPeriodReadSnapshot] = []
    for row in rows:
        if row.period == "day":
            business_key = row.business_date or row.captured_at.date()
            key = (row.platform, row.data_kind, business_key)
            previous = latest_day_by_date.get(key)
            if previous is None or row.id > previous.id:
                latest_day_by_date[key] = row
        else:
            others.append(row)
    ordered = sorted(
        (*latest_day_by_date.values(), *others),
        key=lambda row: (row.captured_at, row.id),
    )
    return [_to_dict(row) for row in ordered]


async def append_day_snapshot_run(
    *,
    status: str,
    platform_results: dict[str, Any] | None = None,
    ok_count: int = 0,
    not_available_count: int = 0,
    error_count: int = 0,
    error_summary: str | None = None,
    triggered_at: datetime | None = None,
) -> dict[str, Any]:
    """日终快照轮次结束落一条运行记录（append-only，运行日志展示用）。"""
    await ensure_db_ready()
    async with session_scope() as session:
        row = EbkPeriodDaySnapshotRun(
            status=status,
            platform_results_json=platform_results or {},
            ok_count=ok_count,
            not_available_count=not_available_count,
            error_count=error_count,
            error_summary=error_summary,
            triggered_at=triggered_at or local_now(),
        )
        session.add(row)
        await session.flush()
        return _run_to_dict(row)


async def list_day_snapshot_runs(limit: int = 20) -> list[dict[str, Any]]:
    """最近 N 轮昨日快照运行记录（时间倒序）。"""
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = (
            select(EbkPeriodDaySnapshotRun)
            .order_by(EbkPeriodDaySnapshotRun.id.desc())
            .limit(max(1, min(int(limit), 100)))
        )
        rows = list((await session.scalars(stmt)).all())
    return [_run_to_dict(row) for row in rows]


def _run_to_dict(row: EbkPeriodDaySnapshotRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "platform_results": row.platform_results_json or {},
        "ok_count": row.ok_count,
        "not_available_count": row.not_available_count,
        "error_count": row.error_count,
        "error_summary": row.error_summary,
        "triggered_at": _format_datetime(row.triggered_at),
        "created_at": _format_datetime(row.created_at),
    }
