"""bi_daily 仓储层：事实追加（source_row_ref 幂等）、导入 upsert、查询。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bi_daily.models import (
    BiCollectionRun,
    BiDailyCardPush,
    BiImportAudit,
    BookingPositionFact,
    BudgetForecastFact,
    CompetitorMarketFact,
    MarketDemandFact,
    WeekdayDateMapping,
)
from app.domain.bi_daily.models import local_now


async def existing_refs(session: AsyncSession, model, refs: Sequence[str | None]) -> set[str]:
    """已存在的 source_row_ref 集合（幂等回灌依据）。"""
    wanted = [ref for ref in refs if ref]
    if not wanted:
        return set()
    rows = await session.execute(
        select(model.source_row_ref).where(model.source_row_ref.in_(wanted))
    )
    return {row[0] for row in rows.all()}


async def add_facts_if_absent(session: AsyncSession, model, rows: list[dict]) -> int:
    """按 source_row_ref 去重追加事实行；返回实际新增数。"""
    refs = [row.get("source_row_ref") for row in rows]
    seen = await existing_refs(session, model, refs)
    pending: list[dict] = []
    local_seen: set[str] = set()
    for row in rows:
        ref = row.get("source_row_ref")
        if ref:
            if ref in seen or ref in local_seen:
                continue
            local_seen.add(ref)
        pending.append(row)
    if pending:
        session.add_all(model(**row) for row in pending)
    return len(pending)


async def upsert_budget_forecast_rows(
    session: AsyncSession,
    *,
    shop_key: str,
    forecast_version: str,
    source: str,
    valid_from: date | None,
    valid_to: date | None,
    rows: list[dict],
    imported_at: datetime | None = None,
) -> int:
    """同版本同 (stay_date, room_type) 更新，否则插入；返回落库行数。

    rows 元素须含 stay_date / room_type / 预算与预测字段。
    """
    stamp = imported_at or local_now()
    existing_rows = (
        (
            await session.execute(
                select(BudgetForecastFact).where(
                    BudgetForecastFact.shop_key == shop_key,
                    BudgetForecastFact.forecast_version == forecast_version,
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_key = {
        (row.stay_date, row.room_type): row for row in existing_rows
    }
    written = 0
    for row in rows:
        stay = row["stay_date"]
        room_type = row.get("room_type") or ""
        current = existing_by_key.get((stay, room_type))
        payload = dict(
            budget_room_nights=row.get("budget_room_nights"),
            budget_revenue=row.get("budget_revenue"),
            budget_adr=row.get("budget_adr"),
            demand_forecast_room_nights=row.get("demand_forecast_room_nights"),
            demand_forecast_revenue=row.get("demand_forecast_revenue"),
            imported_at=stamp,
            valid_from=valid_from,
            valid_to=valid_to,
            source=source,
        )
        if current is not None:
            for field, value in payload.items():
                setattr(current, field, value)
        else:
            inserted = BudgetForecastFact(
                shop_key=shop_key,
                stay_date=stay,
                weekday=stay.isoweekday(),
                room_type=room_type,
                forecast_version=forecast_version,
                **payload,
            )
            session.add(inserted)
            # 同一批内的重复键走更新分支，避免 flush 撞唯一约束（500）。
            existing_by_key[(stay, room_type)] = inserted
        written += 1
    return written


async def list_budget_forecast_versions(session: AsyncSession, shop_key: str) -> list[dict]:
    rows = (
        (
            await session.execute(
                select(
                    BudgetForecastFact.forecast_version,
                    BudgetForecastFact.source,
                    BudgetForecastFact.imported_at,
                    BudgetForecastFact.valid_from,
                    BudgetForecastFact.valid_to,
                    func.count(BudgetForecastFact.id),
                    func.min(BudgetForecastFact.stay_date),
                    func.max(BudgetForecastFact.stay_date),
                )
                .where(BudgetForecastFact.shop_key == shop_key)
                .group_by(
                    BudgetForecastFact.forecast_version,
                    BudgetForecastFact.source,
                    BudgetForecastFact.imported_at,
                    BudgetForecastFact.valid_from,
                    BudgetForecastFact.valid_to,
                )
                .order_by(BudgetForecastFact.imported_at.desc())
            )
        )
        .all()
    )
    return [
        {
            "forecast_version": row[0],
            "source": row[1],
            "imported_at": row[2],
            "valid_from": row[3],
            "valid_to": row[4],
            "row_count": row[5],
            "stay_date_from": row[6],
            "stay_date_to": row[7],
        }
        for row in rows
    ]


async def record_import_audit(session: AsyncSession, **fields) -> BiImportAudit:
    audit = BiImportAudit(**fields)
    session.add(audit)
    await session.flush()
    return audit


async def list_import_audits(
    session: AsyncSession, shop_key: str, *, limit: int = 20
) -> list[BiImportAudit]:
    rows = (
        (
            await session.execute(
                select(BiImportAudit)
                .where(BiImportAudit.shop_key == shop_key)
                .order_by(BiImportAudit.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def upsert_weekday_mappings(
    session: AsyncSession, specs: Iterable[dict]
) -> int:
    """按 current_stay_date 幂等写入映射行；返回新增数。"""
    spec_list = list(specs)
    if not spec_list:
        return 0
    dates = [spec["current_stay_date"] for spec in spec_list]
    existing = {
        row.current_stay_date: row
        for row in (
            await session.execute(
                select(WeekdayDateMapping).where(
                    WeekdayDateMapping.current_stay_date.in_(dates)
                )
            )
        ).scalars()
    }
    added = 0
    for spec in spec_list:
        if spec["current_stay_date"] in existing:
            current = existing[spec["current_stay_date"]]
            current.prior_year_same_weekday_stay_date = spec[
                "prior_year_same_weekday_stay_date"
            ]
            current.matching_rule = spec["matching_rule"]
            current.fallback_reason = spec.get("fallback_reason")
        else:
            session.add(WeekdayDateMapping(**spec))
            added += 1
    return added


async def upsert_collection_run(session: AsyncSession, **fields) -> BiCollectionRun:
    """(run_date, source) 已存在则原地更新（同日重跑不新增行）。"""
    existing = (
        await session.execute(
            select(BiCollectionRun).where(
                BiCollectionRun.run_date == fields.get("run_date"),
                BiCollectionRun.source == fields.get("source"),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        return existing
    run = BiCollectionRun(**fields)
    session.add(run)
    await session.flush()
    return run


async def get_latest_collection_run(session: AsyncSession) -> BiCollectionRun | None:
    rows = (
        (
            await session.execute(
                select(BiCollectionRun).order_by(BiCollectionRun.id.desc()).limit(1)
            )
        )
        .scalars()
        .first()
    )
    return rows


async def record_card_push_once(
    session: AsyncSession, *, shop_key: str, push_date: date, status: str, detail: str | None
) -> BiDailyCardPush | None:
    """(shop_key, push_date) 已存在则返回 None（跨进程幂等由唯一约束兜底）。"""
    exists = (
        await session.execute(
            select(BiDailyCardPush.id).where(
                BiDailyCardPush.shop_key == shop_key,
                BiDailyCardPush.push_date == push_date,
            )
        )
    ).first()
    if exists:
        return None
    push = BiDailyCardPush(shop_key=shop_key, push_date=push_date, status=status, detail=detail)
    session.add(push)
    await session.flush()
    return push


async def latest_booking_facts_by_stay_date(
    session: AsyncSession,
    *,
    shop_key: str,
    stay_from: date,
    stay_to: date,
) -> dict[date, list[BookingPositionFact]]:
    """按入住日取每个 stay_date 的全部事实（多 as-of 都返回，最新在前）。"""
    rows = (
        (
            await session.execute(
                select(BookingPositionFact)
                .where(
                    BookingPositionFact.shop_key == shop_key,
                    BookingPositionFact.stay_date >= stay_from,
                    BookingPositionFact.stay_date <= stay_to,
                )
                .order_by(BookingPositionFact.stay_date, BookingPositionFact.as_of_time.desc())
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[date, list[BookingPositionFact]] = {}
    for row in rows:
        grouped.setdefault(row.stay_date, []).append(row)
    return grouped


async def latest_market_demand_by_stay_date(
    session: AsyncSession,
    *,
    shop_key: str,
    stay_from: date,
    stay_to: date,
) -> dict[date, "MarketDemandFact"]:
    """按入住日取最新 captured_at 的市场需求事实（每日期一行）。"""
    from app.domain.bi_daily.models import MarketDemandFact

    rows = (
        (
            await session.execute(
                select(MarketDemandFact)
                .where(
                    MarketDemandFact.shop_key == shop_key,
                    MarketDemandFact.stay_date >= stay_from,
                    MarketDemandFact.stay_date <= stay_to,
                )
                .order_by(MarketDemandFact.stay_date, MarketDemandFact.captured_at.desc())
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[date, MarketDemandFact] = {}
    for row in rows:
        if row.stay_date not in grouped:
            grouped[row.stay_date] = row
    return grouped


async def forward_position_facts(
    session: AsyncSession,
    *,
    shop_key: str,
    stay_from: date,
    stay_to: date,
) -> list[BookingPositionFact]:
    """携程日历前瞻事实（source=ctrip_inventory_calendar），最新 as_of 优先。"""
    rows = (
        (
            await session.execute(
                select(BookingPositionFact)
                .where(
                    BookingPositionFact.shop_key == shop_key,
                    BookingPositionFact.source == "ctrip_inventory_calendar",
                    BookingPositionFact.stay_date >= stay_from,
                    BookingPositionFact.stay_date <= stay_to,
                )
                .order_by(
                    BookingPositionFact.stay_date, BookingPositionFact.as_of_time.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def latest_competitor_facts(
    session: AsyncSession,
    *,
    shop_key: str,
    stay_from: date,
    stay_to: date,
    captured_after: datetime | None = None,
) -> list[CompetitorMarketFact]:
    statement = select(CompetitorMarketFact).where(
        CompetitorMarketFact.shop_key == shop_key,
        CompetitorMarketFact.stay_date >= stay_from,
        CompetitorMarketFact.stay_date <= stay_to,
    )
    if captured_after is not None:
        statement = statement.where(CompetitorMarketFact.captured_at >= captured_after)
    rows = (
        (await session.execute(statement.order_by(CompetitorMarketFact.captured_at.desc())))
        .scalars()
        .all()
    )
    return list(rows)


async def budget_rows_for_stay_dates(
    session: AsyncSession,
    *,
    shop_key: str,
    stay_dates: Sequence[date],
) -> list[BudgetForecastFact]:
    if not stay_dates:
        return []
    rows = (
        (
            await session.execute(
                select(BudgetForecastFact)
                .where(
                    BudgetForecastFact.shop_key == shop_key,
                    BudgetForecastFact.stay_date.in_(list(stay_dates)),
                )
                .order_by(BudgetForecastFact.imported_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def weekday_mappings_for_stay_dates(
    session: AsyncSession,
    *,
    stay_dates: Sequence[date],
) -> dict[date, WeekdayDateMapping]:
    if not stay_dates:
        return {}
    rows = (
        (
            await session.execute(
                select(WeekdayDateMapping).where(
                    WeekdayDateMapping.current_stay_date.in_(list(stay_dates))
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.current_stay_date: row for row in rows}
