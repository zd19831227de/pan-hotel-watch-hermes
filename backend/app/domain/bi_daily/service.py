"""bi_daily 服务层：采集轮编排、覆盖率、预算导入、日报装配。

只读事实 + operator 导入，绝不接调价链路。缺数口径统一为
``{"value": None, "status": "unavailable", "reason": <code>}``，不补零。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bi_daily import ingest, repository
from app.domain.bi_daily.importers import parse_budget_forecast_bytes
from app.domain.bi_daily.metrics import completion_rate
from app.domain.bi_daily.models import local_now

SHOP_PLATFORMS = ("ctrip", "meituan", "fliggy")

REASON_NO_OTB = "no_on_the_books_source"
REASON_NO_INVENTORY = "no_inventory_source"

_COMPETITOR_FRESH_HOURS = 6
_COMPETITOR_STALE_HOURS = 48
_OCCUPANCY_VERDICT_MARGIN = 0.01

_WEEKDAY_LABELS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _unavailable(reason: str) -> dict[str, Any]:
    return {"value": None, "status": "unavailable", "reason": reason}


def _metric(value: Any, *, source: str, **extra: Any) -> dict[str, Any]:
    return {"value": value, "status": "available", "source": source, **extra}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


async def collect_shop_platform_bindings() -> tuple[list[dict], list[dict]]:
    """全部店铺 × 三平台 → 账号绑定；解析不到的记入 issues（不猜账号）。"""
    from app.domain.shops import repository as shops_repository
    from app.domain.shops.resolver import resolve_platform_binding

    bindings: list[dict] = []
    issues: list[dict] = []
    shops = await shops_repository.list_shops()
    for shop in shops:
        for platform in SHOP_PLATFORMS:
            resolution = await resolve_platform_binding(shop.shop_key, platform)
            if resolution.get("status") == "resolved":
                bindings.append(
                    {
                        "shop_key": shop.shop_key,
                        "platform": platform,
                        "platform_account_id": resolution["platform_account_id"],
                        # 浏览器现读（前瞻/昨日）需要会话 ID 拉浏览器。
                        "browser_session_id": resolution.get("browser_session_id"),
                    }
                )
            else:
                issues.append(
                    {
                        "shop_key": shop.shop_key,
                        "platform": platform,
                        "status": resolution.get("status"),
                        "summary": resolution.get("summary"),
                    }
                )
    return bindings, issues


async def _list_unbound_competitor_groups(session: AsyncSession) -> list[str]:
    from app.domain.competitor_prices.models import CtripCompetitorWatchGroup

    rows = (
        await session.execute(
            select(CtripCompetitorWatchGroup.name).where(
                CtripCompetitorWatchGroup.shop_key.is_(None),
                CtripCompetitorWatchGroup.enabled.is_(True),
            )
        )
    ).all()
    return [name for (name,) in rows if name]


async def build_coverage(
    session: AsyncSession,
    *,
    shops: list[Any],
    bindings: list[dict],
    issues: list[dict],
    now: datetime,
) -> dict[str, Any]:
    """每店每维度的采集覆盖与新鲜度（落 bi_collection_runs.coverage_json）。"""
    from app.domain.competitor_prices.models import CtripCompetitorWatchGroup
    from app.domain.bi_daily.models import BookingPositionFact, CompetitorMarketFact, BudgetForecastFact

    unbound_groups = await _list_unbound_competitor_groups(session)
    coverage: dict[str, Any] = {}
    for shop in shops:
        shop_key = shop.shop_key
        booking_bindings = [b for b in bindings if b["shop_key"] == shop_key]
        shop_issues = [i for i in issues if i["shop_key"] == shop_key]

        latest_actual = (
            await session.execute(
                select(BookingPositionFact.stay_date)
                .where(BookingPositionFact.shop_key == shop_key)
                .order_by(BookingPositionFact.stay_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        comp_latest = (
            await session.execute(
                select(CompetitorMarketFact.captured_at)
                .where(CompetitorMarketFact.shop_key == shop_key)
                .order_by(CompetitorMarketFact.captured_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        bound_group_count = len(
            (
                await session.execute(
                    select(CtripCompetitorWatchGroup.id).where(
                        CtripCompetitorWatchGroup.shop_key == shop_key
                    )
                )
            ).all()
        )

        if comp_latest is None:
            # 本店维度优先：没绑组=no_group；绑了组还没采到价格=empty。
            # unbound_groups 是全局提示（哪些组没绑店），不参与本店状态判定。
            if bound_group_count == 0:
                competitor_status = "no_group"
            else:
                competitor_status = "empty"
        else:
            age_hours = (now - comp_latest).total_seconds() / 3600
            competitor_status = (
                "fresh" if age_hours <= _COMPETITOR_FRESH_HOURS else "stale"
            )

        forward_latest = (
            await session.execute(
                select(BookingPositionFact.as_of_time)
                .where(
                    BookingPositionFact.shop_key == shop_key,
                    BookingPositionFact.source == "ctrip_inventory_calendar",
                )
                .order_by(BookingPositionFact.as_of_time.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        budget_versions = (
            await session.execute(
                select(BudgetForecastFact.forecast_version)
                .where(BudgetForecastFact.shop_key == shop_key)
                .order_by(BudgetForecastFact.imported_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        coverage[shop_key] = {
            "dimensions": {
                # 未来在手预订：携程「房价房态日历」已售/共N间（2026-10 起）。
                "own_on_the_books": {
                    "status": "covered" if forward_latest is not None else "no_source",
                    "last_captured_at": (
                        forward_latest.isoformat() if forward_latest else None
                    ),
                    **(
                        {}
                        if forward_latest is not None
                        else {
                            "reason": "本店未来入住日在手预订暂无数据（携程日历页未读成）。",
                        }
                    ),
                },
                "own_actuals": {
                    "status": "covered" if latest_actual is not None else "empty",
                    "latest_business_date": (
                        latest_actual.isoformat() if latest_actual else None
                    ),
                    "bound_platforms": sorted(
                        {b["platform"] for b in booking_bindings}
                    ),
                    "binding_issues": shop_issues,
                },
                "competitor_prices": {
                    "status": competitor_status,
                    "last_captured_at": (
                        comp_latest.isoformat() if comp_latest else None
                    ),
                    "bound_group_count": bound_group_count,
                    "unbound_groups": unbound_groups,
                },
                "budget_forecast": {
                    "status": "imported" if budget_versions else "missing",
                    "latest_version": budget_versions,
                },
            }
        }
    return coverage


async def run_bi_daily_collection_round(
    *,
    session: AsyncSession,
    now: datetime | None = None,
    shop_bindings: list[dict] | None = None,
    source: str = "manual",
    live_read_fn: Any | None = None,
) -> dict[str, Any]:
    """一轮采集：携程前瞻/昨日现读 + ingest 三源 + 刷新映射 + 覆盖率落地。

    幂等可重跑；浏览器现读逐账号隔离失败（登录态/页面改版不阻塞 DB 采集）。
    live_read_fn 可注入替代真实浏览器读（测试用），签名同
    forward_reads.run_ctrip_forward_reads_for_binding。
    """
    from app.domain.shops import repository as shops_repository

    current = now or local_now()
    bindings = shop_bindings
    issues: list[dict] = []
    if bindings is None:
        bindings, issues = await collect_shop_platform_bindings()

    live_issues: list[dict[str, Any]] = []
    for binding in bindings:
        if binding.get("platform") != "ctrip":
            continue
        account_id = str(binding.get("platform_account_id") or "")
        if not account_id:
            continue
        payload = dict(binding, browser_session_id=binding.get("browser_session_id"))
        try:
            if live_read_fn is not None:
                live_result = await live_read_fn(binding=payload, now=current)
            else:
                from app.domain.bi_daily import forward_reads

                live_result = await forward_reads.run_ctrip_forward_reads_for_binding(
                    platform_account_id=account_id,
                    browser_session_id=binding.get("browser_session_id"),
                    now=current,
                )
                await forward_reads.run_ctrip_yesterday_actuals_read(
                    platform_account_id=account_id,
                    browser_session_id=binding.get("browser_session_id"),
                    now=current,
                )
                await forward_reads.run_ctrip_prior_year_boundary_read(
                    shop_key=str(binding.get("shop_key") or ""),
                    platform_account_id=account_id,
                    browser_session_id=binding.get("browser_session_id"),
                    now=current,
                )
            if str(live_result.get("status")) not in ("ok", "skipped_existing"):
                live_issues.append(
                    {
                        "shop_key": binding.get("shop_key"),
                        "platform": "ctrip",
                        "stage": "forward_read",
                        "status": live_result.get("status"),
                        "summary": live_result.get("reason"),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - 单账号读失败不断轮
            live_issues.append(
                {
                    "shop_key": binding.get("shop_key"),
                    "platform": "ctrip",
                    "stage": "forward_read",
                    "status": "error",
                    "summary": f"{type(exc).__name__}: {exc}"[:200],
                }
            )

    written_booking = await ingest.ingest_ebk_period_day_actuals(
        session, shop_bindings=bindings, now=current
    )
    written_calendar = await ingest.ingest_calendar_price_points(session, now=current)
    written_rates = await ingest.ingest_competitor_room_rates(session, now=current)
    written_forward = await ingest.ingest_ctrip_forward_position(
        session, shop_bindings=bindings, now=current
    )
    written_demand = await ingest.ingest_market_demand(
        session, shop_bindings=bindings, now=current
    )
    await ingest.refresh_weekday_mappings(
        session,
        start=current.date() - timedelta(days=400),
        end=current.date() + timedelta(days=60),
    )

    shops = await shops_repository.list_shops()
    coverage = await build_coverage(
        session, shops=shops, bindings=bindings, issues=issues + live_issues, now=current
    )
    await repository.upsert_collection_run(
        session,
        run_date=current.date(),
        source=source,
        status="success",
        coverage_json=coverage,
        shops_json=[shop.shop_key for shop in shops],
        fact_rows_written=(
            written_booking + written_calendar + written_rates + written_forward + written_demand
        ),
        error_summary=None,
    )
    return {
        "status": "success",
        "booking_facts_written": written_booking,
        "forward_facts_written": written_forward,
        "demand_facts_written": written_demand,
        "competitor_facts_written": written_calendar + written_rates,
        "live_read_issues": live_issues,
        "shops": [shop.shop_key for shop in shops],
    }


async def import_budget_forecast(
    session: AsyncSession,
    *,
    shop_key: str,
    payload: bytes,
    filename: str,
    forecast_version: str,
    valid_from: date | None,
    valid_to: date | None,
) -> dict[str, Any]:
    """解析 → 校验 → upsert 事实 + 审计。致命结构问题抛 ValueError。"""
    parsed = parse_budget_forecast_bytes(payload, filename=filename)
    source = "xlsx_import" if filename.lower().endswith(".xlsx") else "csv_import"
    written = 0
    status = "success" if parsed.rows_valid_count > 0 else "failed"
    if parsed.rows_valid_count > 0:
        written = await repository.upsert_budget_forecast_rows(
            session,
            shop_key=shop_key,
            forecast_version=forecast_version,
            source=source,
            valid_from=valid_from,
            valid_to=valid_to,
            rows=[
                {
                    "stay_date": row.stay_date,
                    "room_type": row.room_type,
                    "budget_room_nights": row.budget_room_nights,
                    "budget_revenue": row.budget_revenue,
                    "budget_adr": row.budget_adr,
                    "demand_forecast_room_nights": row.demand_forecast_room_nights,
                    "demand_forecast_revenue": row.demand_forecast_revenue,
                }
                for row in parsed.rows
            ],
        )
    await repository.record_import_audit(
        session,
        kind="budget_forecast",
        shop_key=shop_key,
        forecast_version=forecast_version,
        source=source,
        file_name=filename,
        file_sha256=hashlib.sha256(payload).hexdigest(),
        rows_total=parsed.rows_total,
        rows_valid=parsed.rows_valid_count,
        rows_rejected=parsed.rows_total - parsed.rows_valid_count,
        errors_json=parsed.errors or None,
        status=status,
    )
    return {
        "status": status,
        "forecast_version": forecast_version,
        "rows_total": parsed.rows_total,
        "rows_valid": parsed.rows_valid_count,
        "rows_written": written,
        "errors": parsed.errors,
    }


async def list_budget_forecast_versions(session: AsyncSession, shop_key: str) -> list[dict]:
    return await repository.list_budget_forecast_versions(session, shop_key)


def _competitor_forward_view(
    facts: list[Any],
    hotel_infos: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    """某入住日的竞品价格/可售性汇总（只汇总价格，不推断出租率）。"""
    if not facts:
        return None
    hotels: dict[int, dict[str, Any]] = {}
    prices: list[int] = []
    availability_counts: dict[str, int] = {}
    for fact in facts:
        info = hotel_infos.get(fact.competitor_hotel_id, {})
        entry = hotels.setdefault(
            fact.competitor_hotel_id,
            {
                "hotel_id": fact.competitor_hotel_id,
                "alias": info.get("alias") or info.get("hotel_name") or f"酒店#{fact.competitor_hotel_id}",
                "role": info.get("role") or "competitor",
                "price": None,
                "availability": "unknown",
                "room_rates": [],
            },
        )
        availability_counts[fact.availability] = availability_counts.get(fact.availability, 0) + 1
        if fact.price is not None:
            prices.append(fact.price)
        if fact.source == "calendar_price_point":
            entry["price"] = fact.price
            entry["availability"] = fact.availability
        else:
            entry["room_rates"].append(
                {
                    "room_type": fact.room_type,
                    "breakfast_kind": fact.breakfast_kind,
                    "price": fact.price,
                    "availability": fact.availability,
                    "captured_at": fact.captured_at.isoformat(),
                }
            )
    for entry in hotels.values():
        if entry["room_rates"]:
            entry["room_rates"].sort(key=lambda item: (item["price"] is None, item["price"] or 0))
            if entry["price"] is None:
                best = entry["room_rates"][0]
                entry["price"] = best["price"]
                entry["availability"] = best["availability"]
    return {
        "status": "available",
        "prices": (
            {"min": min(prices), "max": max(prices), "count": len(prices)}
            if prices
            else None
        ),
        "availability": availability_counts,
        # 明示：竞品出租率/预订量不可由价格推断，本视图不提供。
        "not_available_metrics": ["occupancy_rate", "booking_pace", "rank"],
        "hotels": sorted(hotels.values(), key=lambda item: (item["role"] != "own", item["alias"])),
    }


def _occupancy_verdict(my_occ: float | None, comp_occ: float | None) -> str:
    if my_occ is None or comp_occ is None:
        return "unknown"
    if my_occ - comp_occ > _OCCUPANCY_VERDICT_MARGIN:
        return "win"
    if comp_occ - my_occ > _OCCUPANCY_VERDICT_MARGIN:
        return "lose"
    return "even"


_OCCUPANCY_VERDICT_LABELS = {
    "win": "预订领先竞争圈",
    "lose": "输给竞争圈",
    "even": "与竞争圈持平",
    "unknown": "竞争圈对比数据不足",
}


def _circle_view(
    demand: Any,
    *,
    own_prices: list[int],
    competitor_prices: list[int],
    pre_set_price: float | None,
) -> dict[str, Any]:
    """竞争圈对比视图：预订（页面给定在手/出租率口径）+ 价格位置。

    只消费携程市场分析页明示的竞争圈数据与已采集价格事实，不做推断。
    """
    circle: dict[str, Any] = {
        "status": "empty",
        "reason": "该入住日暂无竞争圈在手数据（携程市场分析未采集）。",
    }
    if demand is not None:
        my_occ = demand.my_occupancy_rate
        comp_occ = demand.competitor_occupancy_rate
        verdict = _occupancy_verdict(my_occ, comp_occ)
        circle = {
            "status": "available",
            "my_occupancy_rate": my_occ,
            "competitor_occupancy_rate": comp_occ,
            "my_booked_rooms": demand.my_booked_rooms,
            "competitor_booked_rooms": demand.competitor_booked_rooms,
            "occupancy_verdict": verdict,
            "verdict_label": _OCCUPANCY_VERDICT_LABELS[verdict],
            "competitor_price_trend": demand.competitor_price_trend,
            "caliber_note": "竞争圈在手/出租率为携程市场分析页口径，非本系统推断。",
        }
    own_min = min(own_prices) if own_prices else pre_set_price
    if own_min is not None and competitor_prices:
        comp_min = min(competitor_prices)
        comp_median = _median([float(value) for value in competitor_prices])
        if own_min <= comp_min:
            position, position_label = "lowest", "低于竞争圈底价"
        elif own_min <= (comp_median or comp_min):
            position, position_label = "below_median", "竞争圈中位以下"
        else:
            position, position_label = "above_median", "高于竞争圈中位"
        circle["price_position"] = {
            "own_min_price": own_min,
            "competitor_min_price": comp_min,
            "competitor_median_price": comp_median,
            "position": position,
            "position_label": position_label,
            "caliber_note": "本店价格取竞品盯盘日历价本店角色/EBK预埋价最低值。",
        }
    return circle


def _market_view(demand: Any) -> dict[str, Any]:
    """市场需求视图：市场热度（城市/商圈/出行，含去年同期）+ 热点事件。"""
    if demand is None:
        return {"status": "empty", "reason": "该入住日暂无市场需求数据。"}
    return {
        "status": "available",
        "city_heat": {"value": demand.city_heat, "label": demand.city_heat_label},
        "zone_heat": {"value": demand.zone_heat, "label": demand.zone_heat_label},
        "travel_heat": {"value": demand.travel_heat},
        "travel_heat_last_year": {"value": demand.travel_heat_last_year},
        "competitor_price_trend": demand.competitor_price_trend,
        "events": list(demand.events_json or []),
    }


async def build_daily_report(
    *,
    session: AsyncSession,
    shop_key: str,
    as_of: datetime | None = None,
    horizon_days: int = 14,
    lookback_days: int = 30,
) -> dict[str, Any]:
    """经营 BI 日报数据：14 天前瞻矩阵 + 历史表现（三类完成率）+ 质量面板。"""
    from app.domain.competitor_prices.models import CtripCompetitorHotel
    from app.domain.shops import repository as shops_repository

    current = as_of or local_now()
    today = current.date()
    forward_dates = [today + timedelta(days=offset) for offset in range(horizon_days)]
    lookback_from = today - timedelta(days=lookback_days)

    booking_by_date = await repository.latest_booking_facts_by_stay_date(
        session, shop_key=shop_key, stay_from=lookback_from, stay_to=today - timedelta(days=1)
    )
    forward_booking = await repository.latest_booking_facts_by_stay_date(
        session, shop_key=shop_key, stay_from=today, stay_to=forward_dates[-1]
    )
    competitor_facts = await repository.latest_competitor_facts(
        session,
        shop_key=shop_key,
        stay_from=today,
        stay_to=forward_dates[-1],
        captured_after=current - timedelta(days=7),
    )
    # 预算只用于历史表现完成率；未来日期不查（前瞻区不展示预算）。
    budget_rows = await repository.budget_rows_for_stay_dates(
        session,
        shop_key=shop_key,
        stay_dates=sorted(booking_by_date.keys()),
    )

    hotel_ids = sorted({fact.competitor_hotel_id for fact in competitor_facts})
    hotel_infos: dict[int, dict[str, Any]] = {}
    if hotel_ids:
        for hotel in (
            (
                await session.execute(
                    select(CtripCompetitorHotel).where(
                        CtripCompetitorHotel.id.in_(hotel_ids)
                    )
                )
            )
            .scalars()
            .all()
        ):
            hotel_infos[hotel.id] = {
                "alias": hotel.alias,
                "hotel_name": hotel.hotel_name,
                "role": hotel.role,
            }

    # 竞品去重：同一 (入住日, 酒店, 来源, 房型, 早餐) 只保留最新 captured_at
    # 的事实——多轮采集的历史快照不混入价格带/可售性统计，但不同入住日
    # （日历价矩阵逐日一行）互不挤占。
    competitor_by_date: dict[date, list[Any]] = {}
    latest_fact_by_key: dict[tuple, Any] = {}
    for fact in competitor_facts:  # 仓储已按 captured_at 倒序返回
        key = (
            fact.stay_date,
            fact.competitor_hotel_id,
            fact.source,
            fact.room_type or "",
            fact.breakfast_kind or "",
        )
        if key not in latest_fact_by_key:
            latest_fact_by_key[key] = fact
    for fact in sorted(
        latest_fact_by_key.values(), key=lambda item: item.captured_at, reverse=True
    ):
        competitor_by_date.setdefault(fact.stay_date, []).append(fact)

    # 日基线只认全店口径（room_type 空串）的最新版本；房型行不作日基线。
    budget_by_date: dict[date, Any] = {}
    for row in budget_rows:
        if row.room_type:
            continue
        existing = budget_by_date.get(row.stay_date)
        if existing is None or row.imported_at > existing.imported_at:
            budget_by_date[row.stay_date] = row

    # 去年同期映射 + 去年实际：前瞻参照与历史同比共用一份。
    performance_dates = sorted(booking_by_date.keys())
    reference_dates = sorted(set(forward_dates) | set(performance_dates))
    mappings: dict[date, Any] = {}
    if reference_dates:
        await ingest.refresh_weekday_mappings(
            session, start=reference_dates[0], end=reference_dates[-1]
        )
        mappings = await repository.weekday_mappings_for_stay_dates(
            session, stay_dates=reference_dates
        )
    prior_actual_by_date: dict[date, list[Any]] = {}
    prior_needed = [
        mappings[d].prior_year_same_weekday_stay_date
        for d in reference_dates
        if mappings.get(d) and mappings[d].prior_year_same_weekday_stay_date
    ]
    if prior_needed:
        prior_actual_by_date = await repository.latest_booking_facts_by_stay_date(
            session,
            shop_key=shop_key,
            stay_from=min(prior_needed),
            stay_to=max(prior_needed),
        )

    forward: list[dict[str, Any]] = []
    demand_by_date = await repository.latest_market_demand_by_stay_date(
        session, shop_key=shop_key, stay_from=today, stay_to=forward_dates[-1]
    )
    forward_facts = await repository.forward_position_facts(
        session, shop_key=shop_key, stay_from=today, stay_to=forward_dates[-1]
    )
    # (入住日, 房型) 只保留最新 as_of 的一轮；酒店行(room_type='')给总房量，
    # 各组行给预埋价。全店在手/出租率主源=市场分析（页面只回有数据的房型组）。
    forward_latest_by_key: dict[tuple[date, str], Any] = {}
    for fact in forward_facts:  # 仓储已按 as_of 倒序返回
        key = (fact.stay_date, fact.room_type or "")
        if key not in forward_latest_by_key:
            forward_latest_by_key[key] = fact
    own_forward_by_date: dict[date, dict[str, Any]] = {}
    for (stay, room), fact in forward_latest_by_key.items():
        bucket = own_forward_by_date.setdefault(
            stay, {"total": None, "pre_set": None, "pre_set_nb": None}
        )
        if not room and fact.available_inventory is not None:
            bucket["total"] = fact.available_inventory
        notes = fact.notes_json if isinstance(fact.notes_json, dict) else {}
        for note_key, bucket_key in (
            ("pre_set_price_min_no_breakfast", "pre_set_nb"),
            ("pre_set_price_min", "pre_set"),
        ):
            value = notes.get(note_key)
            if value is not None:
                bucket[bucket_key] = (
                    value if bucket[bucket_key] is None else min(bucket[bucket_key], value)
                )

    # 价格位置（本店 vs 竞争圈）：来自日历价事实里本店/竞店角色的最低价。
    own_price_by_date: dict[date, list[int]] = {}
    competitor_price_by_date: dict[date, list[int]] = {}
    for fact in latest_fact_by_key.values():
        role = hotel_infos.get(fact.competitor_hotel_id, {}).get("role")
        target = own_price_by_date if role == "own" else competitor_price_by_date
        if fact.price is not None:
            target.setdefault(fact.stay_date, []).append(fact.price)

    for stay_date in forward_dates:
        own_actual = forward_booking.get(stay_date) or []
        own: dict[str, Any] = {
            # 无源路径保持显式 unavailable，绝不补零。
            "booked_room_nights": _unavailable(REASON_NO_OTB),
            "booking_orders": _unavailable(REASON_NO_OTB),
            "available_inventory": _unavailable(REASON_NO_INVENTORY),
            "remaining_inventory": _unavailable(REASON_NO_INVENTORY),
            "occupancy_rate": _unavailable(REASON_NO_INVENTORY),
            "adr": _unavailable(REASON_NO_OTB),
            "pickup": _unavailable(REASON_NO_OTB),
        }
        own_bucket = own_forward_by_date.get(stay_date)
        demand = demand_by_date.get(stay_date)
        booked_rooms = demand.my_booked_rooms if demand is not None else None
        if booked_rooms is not None:
            own["booked_room_nights"] = _metric(
                booked_rooms, source="ctrip_market_analysis"
            )
        if own_bucket is not None and own_bucket["total"] is not None:
            own["available_inventory"] = _metric(
                own_bucket["total"], source="ctrip_inventory_calendar"
            )
            if booked_rooms is not None:
                own["remaining_inventory"] = _metric(
                    own_bucket["total"] - booked_rooms,
                    source="computed_total_minus_market_rooms",
                )
            pre_set = (
                own_bucket["pre_set_nb"]
                if own_bucket["pre_set_nb"] is not None
                else own_bucket["pre_set"]
            )
            if pre_set is not None:
                own["pre_set_price"] = _metric(pre_set, source="ctrip_inventory_calendar")
        if demand is not None and demand.my_occupancy_rate is not None:
            own["occupancy_rate"] = _metric(
                demand.my_occupancy_rate, source="ctrip_market_analysis"
            )
        elif (
            booked_rooms is not None
            and own_bucket is not None
            and own_bucket["total"]
            and own_bucket["total"] > 0
        ):
            own["occupancy_rate"] = _metric(
                round(booked_rooms / own_bucket["total"], 4),
                source="computed_market_rooms_over_total",
            )
        if own_actual:
            # 万一未来日期已有实际值（数据修正场景），如实展示并标注来源。
            own["has_actual_facts"] = True

        competitor_view = _competitor_forward_view(
            competitor_by_date.get(stay_date) or [], hotel_infos
        )
        competitor: dict[str, Any]
        if competitor_view is not None:
            competitor = competitor_view
        else:
            competitor = {
                "status": "empty",
                "reason": "该入住日暂无已采集的竞品可信价格。",
                "not_available_metrics": ["occupancy_rate", "booking_pace", "rank"],
            }

        circle = _circle_view(
            demand,
            own_prices=own_price_by_date.get(stay_date) or [],
            competitor_prices=competitor_price_by_date.get(stay_date) or [],
            pre_set_price=(
                own.get("pre_set_price", {}).get("value")
                if isinstance(own.get("pre_set_price"), dict)
                else None
            ),
        )
        market = _market_view(demand)
        # 去年同期参照（板块2）：未来日期的同星期去年实际，指导预埋定价。
        forward_mapping = mappings.get(stay_date)
        prior_ref: dict[str, Any] = {
            "mapped_date": None,
            "matching_rule": None,
            "actual": None,
        }
        if forward_mapping is not None:
            prior_ref["mapped_date"] = (
                forward_mapping.prior_year_same_weekday_stay_date.isoformat()
                if forward_mapping.prior_year_same_weekday_stay_date
                else None
            )
            prior_ref["matching_rule"] = forward_mapping.matching_rule
            mapped_stay = forward_mapping.prior_year_same_weekday_stay_date
            mapped_facts = prior_actual_by_date.get(mapped_stay) if mapped_stay else None
            if mapped_facts:
                seen_platforms: set[str] = set()
                prior_nights_total = 0
                has_prior_nights = False
                prior_ctrip_ref = None
                for fact in mapped_facts:
                    if fact.platform in seen_platforms:
                        continue
                    seen_platforms.add(fact.platform)
                    if fact.booked_room_nights is not None:
                        prior_nights_total += fact.booked_room_nights
                        has_prior_nights = True
                    if fact.platform == "ctrip" and prior_ctrip_ref is None:
                        prior_ctrip_ref = fact
                if has_prior_nights or (
                    prior_ctrip_ref is not None
                    and (
                        prior_ctrip_ref.occupancy_rate is not None
                        or prior_ctrip_ref.adr is not None
                    )
                ):
                    prior_ref["actual"] = {
                        "booked_room_nights": prior_nights_total if has_prior_nights else None
                    }
                    if prior_ctrip_ref is not None:
                        if prior_ctrip_ref.occupancy_rate is not None:
                            prior_ref["actual"]["occupancy_rate"] = prior_ctrip_ref.occupancy_rate
                        if prior_ctrip_ref.adr is not None:
                            prior_ref["actual"]["adr"] = prior_ctrip_ref.adr
        forward.append(
            {
                "stay_date": stay_date.isoformat(),
                "weekday": stay_date.isoweekday(),
                "own": own,
                "circle": circle,
                "market": market,
                "prior_year": prior_ref,
                "competitor": competitor,
            }
        )

    performance: list[dict[str, Any]] = []
    weekday_buckets: dict[int, dict[str, Any]] = {}
    for stay_date in performance_dates:
        facts = booking_by_date[stay_date]
        platforms: dict[str, Any] = {}
        for fact in facts:  # 已按 as_of 倒序，首个即最新
            if fact.platform in platforms:
                continue
            platforms[fact.platform] = fact
        total_nights = (
            sum(
                fact.booked_room_nights or 0
                for fact in platforms.values()
                if fact.booked_room_nights is not None
            )
            if any(fact.booked_room_nights is not None for fact in platforms.values())
            else None
        )
        total_revenue = (
            sum(fact.room_revenue or 0 for fact in platforms.values() if fact.room_revenue is not None)
            if any(fact.room_revenue is not None for fact in platforms.values())
            else None
        )
        # 部分平台缺数时总量会偏低：如实标注哪些平台缺间夜，不静默当 0。
        platforms_missing_nights = sorted(
            platform for platform, fact in platforms.items() if fact.booked_room_nights is None
        )
        mapping = mappings.get(stay_date)
        prior_view: dict[str, Any] = {
            "mapped_date": None,
            "matching_rule": None,
            "actual": None,
            "completion": _unavailable("prior_year_baseline_missing"),
        }
        if mapping is not None:
            prior_view["mapped_date"] = (
                mapping.prior_year_same_weekday_stay_date.isoformat()
                if mapping.prior_year_same_weekday_stay_date
                else None
            )
            prior_view["matching_rule"] = mapping.matching_rule
            prior_facts = (
                prior_actual_by_date.get(mapping.prior_year_same_weekday_stay_date)
                if mapping.prior_year_same_weekday_stay_date
                else None
            )
            if prior_facts:
                seen_platforms: set[str] = set()
                prior_nights = 0
                prior_revenue: float | None = None
                has_any_prior_nights = False
                prior_ctrip = None
                for fact in prior_facts:
                    if fact.platform in seen_platforms:
                        continue
                    seen_platforms.add(fact.platform)
                    if fact.booked_room_nights is not None:
                        prior_nights += fact.booked_room_nights
                        has_any_prior_nights = True
                    if fact.room_revenue is not None:
                        prior_revenue = (prior_revenue or 0) + fact.room_revenue
                    if fact.platform == "ctrip" and prior_ctrip is None:
                        prior_ctrip = fact
                prior_has_content = has_any_prior_nights or (
                    prior_ctrip is not None
                    and (
                        prior_ctrip.occupancy_rate is not None or prior_ctrip.adr is not None
                    )
                )
                if prior_has_content:
                    # 真实的 0 基线照实传给完成率（baseline_zero 口径）；
                    # 全部提取失败时 actual 保持 None，绝不伪造成 0。
                    prior_view["actual"] = {
                        "booked_room_nights": prior_nights if has_any_prior_nights else None
                    }
                    if prior_ctrip is not None:
                        if prior_ctrip.occupancy_rate is not None:
                            prior_view["actual"]["occupancy_rate"] = prior_ctrip.occupancy_rate
                        if prior_ctrip.adr is not None:
                            prior_view["actual"]["adr"] = prior_ctrip.adr
                    if has_any_prior_nights:
                        prior_view["completion"] = completion_rate(
                            actual=float(total_nights) if total_nights is not None else None,
                            baseline=float(prior_nights),
                            baseline_kind="prior_year",
                        )
                    # 同比完成系数：销售额（多平台合计）+ 携程离店口径 出租率/均价。
                    if prior_revenue is not None and total_revenue is not None:
                        prior_view["revenue_completion"] = completion_rate(
                            actual=float(total_revenue),
                            baseline=float(prior_revenue),
                            baseline_kind="prior_year",
                        )
                    current_ctrip = platforms.get("ctrip")
                    if current_ctrip is not None and prior_ctrip is not None:
                        if current_ctrip.adr is not None and prior_ctrip.adr is not None:
                            prior_view["adr_ratio"] = completion_rate(
                                actual=float(current_ctrip.adr),
                                baseline=float(prior_ctrip.adr),
                                baseline_kind="prior_year",
                            )
                        if (
                            current_ctrip.occupancy_rate is not None
                            and prior_ctrip.occupancy_rate is not None
                        ):
                            prior_view["occupancy_delta_pp"] = round(
                                (current_ctrip.occupancy_rate - prior_ctrip.occupancy_rate)
                                * 100,
                                2,
                            )
                else:
                    prior_view["completion"] = _unavailable("prior_year_baseline_missing")
            else:
                prior_view["completion"] = _unavailable("prior_year_baseline_missing")

        budget = budget_by_date.get(stay_date)
        budget_view: dict[str, Any] = {
            "version": None,
            "budget_room_nights": None,
            "completion": _unavailable("budget_baseline_missing"),
        }
        if budget is not None:
            budget_view.update(
                {
                    "version": budget.forecast_version,
                    "budget_room_nights": budget.budget_room_nights,
                    "completion": completion_rate(
                        actual=float(total_nights) if total_nights is not None else None,
                        baseline=(
                            float(budget.budget_room_nights)
                            if budget.budget_room_nights is not None
                            else None
                        ),
                        baseline_kind="budget",
                    ),
                }
            )
        forecast_view: dict[str, Any] = {
            "demand_forecast_room_nights": None,
            "completion": _unavailable("forecast_baseline_missing"),
        }
        if budget is not None:
            forecast_view.update(
                {
                    "demand_forecast_room_nights": budget.demand_forecast_room_nights,
                    "completion": completion_rate(
                        actual=float(total_nights) if total_nights is not None else None,
                        baseline=(
                            float(budget.demand_forecast_room_nights)
                            if budget.demand_forecast_room_nights is not None
                            else None
                        ),
                        baseline_kind="forecast",
                    ),
                }
            )

        performance.append(
            {
                "stay_date": stay_date.isoformat(),
                "weekday": stay_date.isoweekday(),
                "platforms": [
                    {
                        "platform": fact.platform,
                        "booked_room_nights": fact.booked_room_nights,
                        "room_revenue": fact.room_revenue,
                        "adr": fact.adr,
                        "occupancy_rate": fact.occupancy_rate,
                        "as_of_time": fact.as_of_time.isoformat(),
                    }
                    for fact in sorted(platforms.values(), key=lambda f: f.platform)
                ],
                "totals": {
                    "booked_room_nights": total_nights,
                    "room_revenue": total_revenue,
                    **(
                        {"platforms_missing_nights": platforms_missing_nights}
                        if platforms_missing_nights
                        else {}
                    ),
                },
                "prior_year": prior_view,
                "budget": budget_view,
                "forecast": forecast_view,
            }
        )

        # 星期维度桶（板块2「看星期，不以日期为参数」）：当日与去年同期同星期。
        weekday_bucket = weekday_buckets.setdefault(
            stay_date.isoweekday(),
            {
                "current": {"days": 0, "nights": 0, "revenue": 0.0, "occupancies": [], "adrs": []},
                "prior": {"days": 0, "nights": 0, "revenue": 0.0, "occupancies": [], "adrs": []},
            },
        )
        weekday_bucket["current"]["days"] += 1
        if total_nights is not None:
            weekday_bucket["current"]["nights"] += total_nights
        if total_revenue is not None:
            weekday_bucket["current"]["revenue"] += total_revenue
        current_ctrip_fact = platforms.get("ctrip")
        if current_ctrip_fact is not None:
            if current_ctrip_fact.occupancy_rate is not None:
                weekday_bucket["current"]["occupancies"].append(current_ctrip_fact.occupancy_rate)
            if current_ctrip_fact.adr is not None:
                weekday_bucket["current"]["adrs"].append(current_ctrip_fact.adr)
        prior_mapping = mappings.get(stay_date)
        prior_stay = (
            prior_mapping.prior_year_same_weekday_stay_date
            if prior_mapping is not None
            else None
        )
        if prior_stay is not None:
            prior_facts_for_weekday = prior_actual_by_date.get(prior_stay) or []
            seen: set[str] = set()
            prior_bucket_nights = 0
            prior_bucket_revenue = 0.0
            has_prior = False
            for fact in prior_facts_for_weekday:
                if fact.platform in seen:
                    continue
                seen.add(fact.platform)
                if fact.booked_room_nights is not None:
                    prior_bucket_nights += fact.booked_room_nights
                    has_prior = True
                if fact.room_revenue is not None:
                    prior_bucket_revenue += fact.room_revenue
                if fact.platform == "ctrip":
                    if fact.occupancy_rate is not None:
                        weekday_bucket["prior"]["occupancies"].append(fact.occupancy_rate)
                    if fact.adr is not None:
                        weekday_bucket["prior"]["adrs"].append(fact.adr)
                    has_prior = True
            if has_prior:
                weekday_bucket["prior"]["days"] += 1
                weekday_bucket["prior"]["nights"] += prior_bucket_nights
                weekday_bucket["prior"]["revenue"] += prior_bucket_revenue

    weekday_profile: list[dict[str, Any]] = []
    for weekday in sorted(weekday_buckets):
        bucket = weekday_buckets[weekday]
        cur_stats = bucket["current"]
        prior_stats = bucket["prior"]
        row: dict[str, Any] = {
            "weekday": weekday,
            "weekday_label": _WEEKDAY_LABELS.get(weekday, str(weekday)),
            "current": {
                "days": cur_stats["days"],
                "nights_sum": cur_stats["nights"],
                "revenue_sum": cur_stats["revenue"],
                "occupancy_avg": (
                    round(sum(cur_stats["occupancies"]) / len(cur_stats["occupancies"]), 4)
                    if cur_stats["occupancies"]
                    else None
                ),
                "adr_avg": (
                    round(sum(cur_stats["adrs"]) / len(cur_stats["adrs"]), 2)
                    if cur_stats["adrs"]
                    else None
                ),
            },
            "prior_year": {
                "days": prior_stats["days"],
                "nights_sum": prior_stats["nights"],
                "revenue_sum": prior_stats["revenue"],
                "occupancy_avg": (
                    round(sum(prior_stats["occupancies"]) / len(prior_stats["occupancies"]), 4)
                    if prior_stats["occupancies"]
                    else None
                ),
                "adr_avg": (
                    round(sum(prior_stats["adrs"]) / len(prior_stats["adrs"]), 2)
                    if prior_stats["adrs"]
                    else None
                ),
            },
        }
        # 同比完成系数（板块3）：间夜/销售额合计比 + 出租率/均价差。
        if prior_stats["nights"]:
            row["completion"] = completion_rate(
                actual=float(cur_stats["nights"]),
                baseline=float(prior_stats["nights"]),
                baseline_kind="prior_year",
            )
        elif cur_stats["days"]:
            row["completion"] = _unavailable("prior_year_baseline_missing")
        if prior_stats["revenue"] and cur_stats["revenue"]:
            row["revenue_completion"] = completion_rate(
                actual=float(cur_stats["revenue"]),
                baseline=float(prior_stats["revenue"]),
                baseline_kind="prior_year",
            )
        if cur_stats["occupancies"] and prior_stats["occupancies"]:
            row["occupancy_delta_pp"] = round(
                (
                    sum(cur_stats["occupancies"]) / len(cur_stats["occupancies"])
                    - sum(prior_stats["occupancies"]) / len(prior_stats["occupancies"])
                )
                * 100,
                2,
            )
        if cur_stats["adrs"] and prior_stats["adrs"]:
            row["adr_ratio"] = completion_rate(
                actual=sum(cur_stats["adrs"]) / len(cur_stats["adrs"]),
                baseline=sum(prior_stats["adrs"]) / len(prior_stats["adrs"]),
                baseline_kind="prior_year",
            )
        weekday_profile.append(row)

    shops = await shops_repository.list_shops()
    bindings, issues = await collect_shop_platform_bindings()
    coverage = await build_coverage(
        session, shops=shops, bindings=bindings, issues=issues, now=current
    )
    return {
        "shop_key": shop_key,
        "as_of": current.isoformat(),
        "horizon_days": horizon_days,
        "forward": forward,
        "performance": performance,
        "weekday_profile": weekday_profile,
        "quality": {
            "coverage": coverage.get(shop_key, {}),
            "generated_at": local_now().isoformat(),
            "metric_notes": [
                "竞争圈在手/出租率、市场热度均为携程市场分析页口径，非本系统推断。",
                "本店未来在手预订/总房量来自携程房价房态日历页（携程渠道已售/房型组共N间）。",
                "去年同期按同星期映射（D-364 优先，跨年 D-371），星期视图聚合窗口内同星期。",
                "平台自定义区间仅支持近约12个月：更早的去年同期无法回读，如实标注缺失；采集轮每日补读边界日，同比基线逐日累积。",
                "出租率/均价同比取携程离店口径；美团/飞猪无页面出租率，如实缺省。",
            ],
        },
    }
