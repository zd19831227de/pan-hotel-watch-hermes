"""bi_daily 采集映射：把既有可信数据映射进事实表（只读源表，只写事实表）。

来源与口径：
- ``calendar_price_points``（is_trusted）→ 未来入住日竞品价格矩阵；
- ``ctrip_competitor_room_rates``（is_trusted）→ 近端逐房型价格与可售状态；
- ``ebk_period_read_snapshots``（business/day，status=ok，business_date 有值）
  → 历史入住日实际间夜/销售额/平均卖价（离店或在店口径随平台，notes 留痕）。

硬规则：竞品出租率/预订节奏/排名永不由价格推断（列恒 NULL）；
未绑 shop_key 的竞品监控组不入事实（覆盖率另行提示，不猜归属）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bi_daily.models import (
    BookingPositionFact,
    CompetitorMarketFact,
    iso_weekday_value,
)
from app.domain.bi_daily.repository import add_facts_if_absent

SOURCE_CALENDAR_POINT = "calendar_price_point"
SOURCE_COMPETITOR_ROOM_RATE = "competitor_room_rate"
SOURCE_EBK_PERIOD_DAY = "ebk_period_day"
SOURCE_CTRIP_INVENTORY_CALENDAR = "ctrip_inventory_calendar"
SNAPSHOT_KIND_FORWARD = "bi_forward"
SNAPSHOT_KIND_DEMAND = "bi_demand"

# 平台 → 按日经营 payload.sections 里的指标标签（按序取第一个命中）。
# 携程=离店口径、美团/飞猪=在店口径优先；口径写入 notes_json 留痕。
# occupancy 只认平台明示的百分比（携程在店组出租率），不由间夜反推。
_SECTION_METRIC_LABELS: dict[str, dict[str, tuple[str, ...]]] = {
    "ctrip": {
        "nights": ("离店间夜量",),
        "revenue": ("离店销售额",),
        "adr": ("平均卖价",),
        "occupancy": ("出租率",),
    },
    "meituan": {
        "nights": ("入住间夜", "销售间夜"),
        "revenue": ("入住金额", "销售额"),
        "adr": ("平均房价", "平均卖价"),
    },
    "fliggy": {
        "nights": ("在店间夜", "预订间夜"),
        "revenue": ("房费收入", "预订收入金额"),
        "adr": ("间夜单价", "均价"),
    },
}


def _parse_rate_fraction(value: Any) -> float | None:
    """平台明示百分比（如 "54.29%"）→ 0~1 小数；非百分比口径返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text.endswith("%"):
        return None
    try:
        return float(text[:-1].strip()) / 100
    except ValueError:
        return None


def _parse_metric_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "—", "–", "暂无"}:
        return None
    if "%" in text:
        return None  # 比率类不是本口径要的量纲
    for suffix in ("元", "间夜", "间", "单"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    text = text.replace(",", "").replace("，", "").strip()
    # 携程预订销售页自定义区间用「万」计数（如 离店销售额 7.00万，2026-10-08
    # 生产实证）；不认万会把整段销售额解析成 None。
    multiplier = 1.0
    if text.endswith("万"):
        multiplier = 10_000.0
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def extract_day_business_metrics(payload: dict[str, Any], platform: str) -> dict[str, Any]:
    """从按日经营 payload.sections 提取 间夜/销售额/平均价/出租率（缺失即 None）。"""
    labels = _SECTION_METRIC_LABELS.get(platform)
    sections = payload.get("sections") if isinstance(payload, dict) else None
    result: dict[str, Any] = {
        "booked_room_nights": None,
        "room_revenue": None,
        "adr": None,
        "occupancy_rate": None,
    }
    if not labels or not isinstance(sections, dict):
        return result
    used: dict[str, str] = {}
    for metric, candidates in labels.items():
        for section_payload in sections.values():
            if not isinstance(section_payload, dict):
                continue
            for label in candidates:
                cell = section_payload.get(label)
                if cell is None:
                    continue
                raw = cell.get("value") if isinstance(cell, dict) else cell
                if metric == "occupancy":
                    number = _parse_rate_fraction(raw)
                else:
                    number = _parse_metric_number(raw)
                if number is not None:
                    field_name = {
                        "nights": "booked_room_nights",
                        "revenue": "room_revenue",
                        "adr": "adr",
                        "occupancy": "occupancy_rate",
                    }[metric]
                    result[field_name] = number
                    used[metric] = label
                    break
            if metric in used:
                break
    if used:
        result["metric_labels"] = used
    return result


async def _shop_bound_competitor_group_ids(session: AsyncSession) -> dict[int, str]:
    from app.domain.competitor_prices.models import CtripCompetitorWatchGroup

    rows = (
        await session.execute(
            select(CtripCompetitorWatchGroup.id, CtripCompetitorWatchGroup.shop_key).where(
                CtripCompetitorWatchGroup.shop_key.is_not(None)
            )
        )
    ).all()
    return {group_id: shop_key for group_id, shop_key in rows}


async def ingest_calendar_price_points(
    session: AsyncSession, *, now: datetime, freshness_days: int = 14
) -> int:
    """每个绑店竞品组的最新一轮日历价 run → trusted 价格点入事实。

    超过 freshness_days 的旧 run 不回灌（避免换绑/停用后复活陈年价格）。
    """
    from app.domain.calendar_price_watch.models import CalendarPricePoint, CalendarPriceRun

    group_shops = await _shop_bound_competitor_group_ids(session)
    if not group_shops:
        return 0
    cutoff = now - timedelta(days=freshness_days)
    latest_runs = (
        (
            await session.execute(
                select(CalendarPriceRun)
                .where(
                    CalendarPriceRun.watch_group_id.in_(list(group_shops)),
                    CalendarPriceRun.captured_at >= cutoff,
                )
                .order_by(
                    CalendarPriceRun.watch_group_id,
                    CalendarPriceRun.captured_at.desc(),
                    CalendarPriceRun.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    seen_groups: set[int] = set()
    latest_run_ids: list[int] = []
    for run in latest_runs:
        if run.watch_group_id in seen_groups:
            continue
        seen_groups.add(run.watch_group_id)
        latest_run_ids.append(run.id)
    if not latest_run_ids:
        return 0
    points = (
        (
            await session.execute(
                select(CalendarPricePoint).where(
                    CalendarPricePoint.run_id.in_(latest_run_ids),
                    CalendarPricePoint.is_trusted.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    rows: list[dict] = []
    for point in points:
        price = point.price_amount_yuan if isinstance(point.price_amount_yuan, int) else None
        rows.append(
            dict(
                shop_key=group_shops[point.watch_group_id],
                competitor_hotel_id=point.hotel_config_id,
                stay_date=point.checkin_date,
                weekday=iso_weekday_value(point.checkin_date),
                room_type=None,
                breakfast_kind="unknown",
                price=price,
                # 日历价页面只给价格；显示价格视为可订，无价格不猜售罄。
                availability="normal" if price is not None else "unknown",
                captured_at=point.captured_at,
                source=SOURCE_CALENDAR_POINT,
                trusted=True,
                source_row_ref=f"{SOURCE_CALENDAR_POINT}:{point.id}",
            )
        )
    return await add_facts_if_absent(session, CompetitorMarketFact, rows)


async def ingest_competitor_room_rates(
    session: AsyncSession, *, now: datetime, lookback_hours: int = 48
) -> int:
    """绑店组近端 trusted 房型价（正常竞品盯盘 run）入事实。"""
    from app.domain.competitor_prices.models import (
        CtripCompetitorHotelSnapshot,
        CtripCompetitorPriceRun,
        CtripCompetitorRoomRate,
    )

    group_shops = await _shop_bound_competitor_group_ids(session)
    if not group_shops:
        return 0
    cutoff = now - timedelta(hours=lookback_hours)
    runs = (
        (
            await session.execute(
                select(CtripCompetitorPriceRun)
                .where(
                    CtripCompetitorPriceRun.group_id.in_(list(group_shops)),
                    CtripCompetitorPriceRun.captured_at >= cutoff,
                    CtripCompetitorPriceRun.status == "success",
                )
                .order_by(
                    CtripCompetitorPriceRun.group_id,
                    CtripCompetitorPriceRun.captured_at.desc(),
                    CtripCompetitorPriceRun.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    seen_groups: set[int] = set()
    latest_run_ids: list[int] = []
    for run in runs:
        if run.group_id in seen_groups:
            continue
        seen_groups.add(run.group_id)
        latest_run_ids.append(run.id)
    if not latest_run_ids:
        return 0
    rates = (
        (
            await session.execute(
                select(CtripCompetitorRoomRate, CtripCompetitorHotelSnapshot)
                .join(
                    CtripCompetitorHotelSnapshot,
                    CtripCompetitorHotelSnapshot.id == CtripCompetitorRoomRate.snapshot_id,
                )
                .where(
                    CtripCompetitorRoomRate.run_id.in_(latest_run_ids),
                    CtripCompetitorRoomRate.is_trusted.is_(True),
                    CtripCompetitorRoomRate.price_amount_yuan.is_not(None),
                    CtripCompetitorHotelSnapshot.checkin_date.is_not(None),
                )
            )
        )
        .all()
    )
    rows: list[dict] = []
    for rate, snapshot in rates:
        rows.append(
            dict(
                shop_key=group_shops[snapshot.group_id],
                competitor_hotel_id=rate.hotel_config_id,
                stay_date=snapshot.checkin_date,
                weekday=iso_weekday_value(snapshot.checkin_date),
                room_type=rate.room_type,
                breakfast_kind=rate.breakfast_kind,
                price=rate.price_amount_yuan,
                availability=rate.availability_status,
                captured_at=snapshot.captured_at,
                source=SOURCE_COMPETITOR_ROOM_RATE,
                trusted=True,
                source_row_ref=f"{SOURCE_COMPETITOR_ROOM_RATE}:{rate.id}",
            )
        )
    return await add_facts_if_absent(session, CompetitorMarketFact, rows)


async def ingest_ebk_period_day_actuals(
    session: AsyncSession,
    *,
    shop_bindings: list[dict],
    now: datetime,
    backfill_days: int = 120,
) -> int:
    """店铺×平台绑定的 EBK 按日经营快照 → 历史入住日实际值入事实。

    backfill 默认 120 天，对齐报表 lookback 上限，避免区间错配。"""
    from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot

    if not shop_bindings:
        return 0
    cutoff = now - timedelta(days=backfill_days)
    total = 0
    for binding in shop_bindings:
        account_id = str(binding.get("platform_account_id") or "").strip()
        shop_key = str(binding.get("shop_key") or "").strip()
        platform = str(binding.get("platform") or "").strip().lower()
        if not account_id or not shop_key or not platform:
            continue
        snapshots = (
            (
                await session.execute(
                    select(EbkPeriodReadSnapshot)
                    .where(
                        EbkPeriodReadSnapshot.platform == platform,
                        EbkPeriodReadSnapshot.data_kind == "business",
                        EbkPeriodReadSnapshot.period == "day",
                        EbkPeriodReadSnapshot.platform_account_id == account_id,
                        EbkPeriodReadSnapshot.business_date.is_not(None),
                        EbkPeriodReadSnapshot.captured_at >= cutoff,
                        EbkPeriodReadSnapshot.status == "ok",
                    )
                    .order_by(EbkPeriodReadSnapshot.captured_at.asc())
                )
            )
            .scalars()
            .all()
        )
        rows: list[dict] = []
        for snapshot in snapshots:
            payload = snapshot.payload_json if isinstance(snapshot.payload_json, dict) else {}
            metrics = extract_day_business_metrics(payload, platform)
            notes: dict[str, Any] = {}
            if snapshot.caliber:
                notes["caliber"] = snapshot.caliber
            if metrics.get("metric_labels"):
                notes["metric_labels"] = metrics["metric_labels"]
            nights = metrics.get("booked_room_nights")
            occupancy = metrics.get("occupancy_rate")
            if occupancy is not None:
                notes["occupancy_caliber"] = "ctrip_staying"
            rows.append(
                dict(
                    shop_key=shop_key,
                    platform=platform,
                    stay_date=snapshot.business_date,
                    weekday=iso_weekday_value(snapshot.business_date),
                    as_of_time=snapshot.captured_at,
                    room_type=None,
                    booked_room_nights=int(nights) if nights is not None else None,
                    booking_orders=None,  # 按日经营快照无订单维度，不伪造
                    room_revenue=metrics.get("room_revenue"),
                    adr=metrics.get("adr"),
                    occupancy_rate=occupancy,
                    source=SOURCE_EBK_PERIOD_DAY,
                    is_trusted=True,
                    source_row_ref=f"{SOURCE_EBK_PERIOD_DAY}:{snapshot.id}",
                    notes_json=notes or None,
                )
            )
        total += await add_facts_if_absent(session, BookingPositionFact, rows)
    return total


async def ingest_ctrip_forward_position(
    session: AsyncSession,
    *,
    shop_bindings: list[dict],
    now: datetime,
    freshness_hours: int = 48,
) -> int:
    """最新 bi_forward 快照 → 未来入住日在手/总房量/预埋价入 BookingPositionFact。

    行粒度 = 房型组 × 入住日：booked=页面已售、available=组共N间；
    预埋价摘要（组内最低卖价 / 无早最低卖价 / 最低底价）进 notes_json。
    快照超期（freshness_hours）不回灌，页面解析失败即无行（不猜）。
    """
    from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot

    if not shop_bindings:
        return 0
    cutoff = now - timedelta(hours=freshness_hours)
    total = 0
    for binding in shop_bindings:
        account_id = str(binding.get("platform_account_id") or "").strip()
        shop_key = str(binding.get("shop_key") or "").strip()
        if binding.get("platform") != "ctrip" or not account_id or not shop_key:
            continue
        snapshot = (
            (
                await session.execute(
                    select(EbkPeriodReadSnapshot)
                    .where(
                        EbkPeriodReadSnapshot.platform == "ctrip",
                        EbkPeriodReadSnapshot.data_kind == SNAPSHOT_KIND_FORWARD,
                        EbkPeriodReadSnapshot.platform_account_id == account_id,
                        EbkPeriodReadSnapshot.captured_at >= cutoff,
                        EbkPeriodReadSnapshot.status == "ok",
                    )
                    .order_by(EbkPeriodReadSnapshot.captured_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if snapshot is None:
            continue
        payload = snapshot.payload_json if isinstance(snapshot.payload_json, dict) else {}
        if payload.get("status") != "ok":
            continue
        if not payload.get("room_groups"):
            # 「共N间」偶发不渲染：回退该账号上一份带房型组总量的快照（标注来源）。
            payload = await _payload_with_fallback_groups(session, snapshot, account_id, payload)
        total += await _write_forward_rows_from_payload(
            session,
            shop_key=shop_key,
            account_id=account_id,
            payload=payload,
            captured_at=snapshot.captured_at,
        )
    return total


async def _payload_with_fallback_groups(
    session: AsyncSession, snapshot: Any, account_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot

    previous = (
        (
            await session.execute(
                select(EbkPeriodReadSnapshot)
                .where(
                    EbkPeriodReadSnapshot.platform == "ctrip",
                    EbkPeriodReadSnapshot.data_kind == SNAPSHOT_KIND_FORWARD,
                    EbkPeriodReadSnapshot.platform_account_id == account_id,
                    EbkPeriodReadSnapshot.status == "ok",
                    EbkPeriodReadSnapshot.id < snapshot.id,
                )
                .order_by(EbkPeriodReadSnapshot.id.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    for candidate in previous:
        body = candidate.payload_json if isinstance(candidate.payload_json, dict) else {}
        groups = body.get("room_groups")
        if groups:
            return {
                **payload,
                "room_groups": groups,
                "room_groups_source": f"previous_snapshot:{candidate.id}",
            }
    return payload


def _forward_group_rows(
    payload: dict[str, Any], captured_at: datetime, *, account_id: str = ""
) -> list[dict]:
    """bi_forward payload → 房型组组长事实行（纯函数，便于测试）。

    source_row_ref 必须嵌入 account_id：不同店铺同一分钟采集时分钟级
    stamp 相同，ref 全局唯一约束会把后采的店整行判重掉（2026-10-10 生产
    实证：立瑞酒店总房量行全被静里的同分钟行挤掉）。
    """
    sold_by_room = payload.get("sold_by_room") or {}
    registry = payload.get("rate_plan_registry") or {}
    groups = payload.get("room_groups") or []
    plans = payload.get("rate_plans") or []
    total_by_name = {str(g.get("room_group_name")): g.get("total_rooms") for g in groups}

    # basicRoomTypeID → 组名（从售卖计划注册表反查）。
    group_name_by_basic_id: dict[str, str] = {}
    for info in registry.values():
        if isinstance(info, dict) and info.get("basic_room_type_id") is not None:
            group_name_by_basic_id.setdefault(
                str(info["basic_room_type_id"]), str(info.get("room_group_name") or "")
            )

    # 组 × 入住日 的预埋价摘要：取组内全部售卖计划当日卖价的最低值。
    plan_group_by_id = {
        str(plan.get("rate_plan_id")): str(
            (registry.get(str(plan.get("rate_plan_id")) or {}) or {}).get("room_group_name")
            or ""
        )
        for plan in plans
        if plan.get("rate_plan_id") is not None
    }
    prices_by_group_date: dict[tuple[str, str], dict[str, Any]] = {}
    for plan in plans:
        plan_name = str(plan.get("rate_plan_name") or "")
        if "钟点" in plan_name:
            # 钟点房按小时计价（118元/3小时等），不得混入过夜预埋价最低值。
            continue
        group_name = plan_group_by_id.get(str(plan.get("rate_plan_id")), "")
        breakfast_hint = (registry.get(str(plan.get("rate_plan_id")) or {}) or {}).get(
            "breakfast_hint", "unknown"
        )
        for cell in plan.get("cells") or []:
            key = (group_name, str(cell.get("stay_date")))
            bucket = prices_by_group_date.setdefault(
                key,
                {"sale_min": None, "sale_min_no_breakfast": None, "floor_min": None},
            )
            sale = cell.get("sale_price")
            floor = cell.get("floor_price")
            if sale is not None:
                bucket["sale_min"] = sale if bucket["sale_min"] is None else min(bucket["sale_min"], sale)
                if breakfast_hint == "none":
                    bucket["sale_min_no_breakfast"] = (
                        sale
                        if bucket["sale_min_no_breakfast"] is None
                        else min(bucket["sale_min_no_breakfast"], sale)
                    )
            if floor is not None:
                bucket["floor_min"] = floor if bucket["floor_min"] is None else min(bucket["floor_min"], floor)

    rows: list[dict] = []
    stamp = captured_at.strftime("%Y%m%d%H%M")
    for basic_room_id, sold_by_date in sold_by_room.items():
        if not isinstance(sold_by_date, dict):
            continue
        group_name = group_name_by_basic_id.get(str(basic_room_id), "")
        for stay_date_raw, sold in sold_by_date.items():
            try:
                stay = date.fromisoformat(str(stay_date_raw))
            except ValueError:
                continue
            prices = prices_by_group_date.get((group_name, str(stay_date_raw))) or {}
            notes: dict[str, Any] = {
                "caliber": "ctrip_calendar_sold",
                "pre_set_price_min": prices.get("sale_min"),
                "pre_set_price_min_no_breakfast": prices.get("sale_min_no_breakfast"),
                "floor_price_min": prices.get("floor_min"),
            }
            rows.append(
                dict(
                    room_type=group_name or None,
                    stay_date=stay,
                    booked_room_nights=int(sold) if sold is not None else None,
                    available_inventory=total_by_name.get(group_name),
                    notes_json=notes,
                    source_row_ref=(
                        f"{SOURCE_CTRIP_INVENTORY_CALENDAR}:{account_id}:room:"
                        f"{basic_room_id}:{stay.isoformat()}:{stamp}"
                    ),
                )
            )
    # 酒店级总房量行（room_type 空串）：各组「共N间」求和。页面 getbasicroomstatushtml
    # 只回有数据的房型组，逐组已售不代表全店——在手/出租率主源是市场分析口径。
    hotel_total = sum(int(group.get("total_rooms") or 0) for group in groups)
    if hotel_total > 0:
        for stay_date_raw in payload.get("dates") or []:
            try:
                stay = date.fromisoformat(str(stay_date_raw))
            except ValueError:
                continue
            rows.append(
                dict(
                    room_type="",
                    stay_date=stay,
                    booked_room_nights=None,
                    available_inventory=hotel_total,
                    notes_json={"caliber": "hotel_total_rooms"},
                    source_row_ref=(
                        f"{SOURCE_CTRIP_INVENTORY_CALENDAR}:{account_id}:hotel:"
                        f"{stay.isoformat()}:{stamp}"
                    ),
                )
            )
    return rows


async def _write_forward_rows_from_payload(
    session: AsyncSession,
    *,
    shop_key: str,
    account_id: str,
    payload: dict[str, Any],
    captured_at: datetime,
) -> int:
    rows = _forward_group_rows(payload, captured_at, account_id=account_id)
    if not rows:
        return 0
    facts = [
        dict(
            shop_key=shop_key,
            platform="ctrip",
            weekday=iso_weekday_value(row["stay_date"]),
            as_of_time=captured_at,
            booking_orders=None,
            room_revenue=None,
            adr=None,
            occupancy_rate=None,
            source=SOURCE_CTRIP_INVENTORY_CALENDAR,
            is_trusted=True,
            **row,
        )
        for row in rows
    ]
    return await add_facts_if_absent(session, BookingPositionFact, facts)


async def ingest_market_demand(
    session: AsyncSession,
    *,
    shop_bindings: list[dict],
    now: datetime,
    freshness_hours: int = 48,
) -> int:
    """最新 bi_demand 快照 → 市场热度/竞争圈在手事实入 MarketDemandFact。"""
    from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot

    if not shop_bindings:
        return 0
    cutoff = now - timedelta(hours=freshness_hours)
    total = 0
    for binding in shop_bindings:
        account_id = str(binding.get("platform_account_id") or "").strip()
        shop_key = str(binding.get("shop_key") or "").strip()
        if binding.get("platform") != "ctrip" or not account_id or not shop_key:
            continue
        snapshot = (
            (
                await session.execute(
                    select(EbkPeriodReadSnapshot)
                    .where(
                        EbkPeriodReadSnapshot.platform == "ctrip",
                        EbkPeriodReadSnapshot.data_kind == SNAPSHOT_KIND_DEMAND,
                        EbkPeriodReadSnapshot.platform_account_id == account_id,
                        EbkPeriodReadSnapshot.captured_at >= cutoff,
                        EbkPeriodReadSnapshot.status == "ok",
                    )
                    .order_by(EbkPeriodReadSnapshot.captured_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if snapshot is None:
            continue
        payload = snapshot.payload_json if isinstance(snapshot.payload_json, dict) else {}
        if payload.get("status") != "ok":
            continue
        total += await _write_demand_rows_from_payload(
            session,
            shop_key=shop_key,
            account_id=account_id,
            payload=payload,
            captured_at=snapshot.captured_at,
        )
    return total


def _demand_fact_rows(
    payload: dict[str, Any], captured_at: datetime, *, account_id: str = ""
) -> list[dict]:
    """bi_demand payload → 逐入住日市场需求行（纯函数，便于测试）。"""

    competitor_rows = {
        str(row.get("stay_date")): row
        for row in (payload.get("competitor_occupied") or {}).get("rows") or []
        if isinstance(row, dict)
    }
    city_by_date = {
        str(row.get("stay_date")): row
        for row in (payload.get("market_heats") or {}).get("city") or []
        if isinstance(row, dict)
    }
    zone_by_date = {
        str(row.get("stay_date")): row
        for row in (payload.get("market_heats") or {}).get("zone") or []
        if isinstance(row, dict)
    }
    travel_by_date = {
        str(row.get("stay_date")): row
        for row in payload.get("travel_heats") or []
        if isinstance(row, dict)
    }
    trend_by_date = {
        str(row.get("stay_date")): row
        for row in payload.get("price_change_trend") or []
        if isinstance(row, dict)
    }
    events = payload.get("events") or []
    events_by_date: dict[str, list[str]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        start = str(event.get("start_date") or "")
        end = str(event.get("end_date") or start)
        if not start:
            continue
        try:
            cursor = date.fromisoformat(start)
            last = date.fromisoformat(end)
        except ValueError:
            continue
        while cursor <= last:
            events_by_date.setdefault(cursor.isoformat(), []).append(str(event.get("name") or ""))
            cursor = cursor + timedelta(days=1)

    rows: list[dict] = []
    stamp = captured_at.strftime("%Y%m%d%H%M")
    for stay_raw, comp in competitor_rows.items():
        try:
            stay = date.fromisoformat(stay_raw)
        except ValueError:
            continue
        city = city_by_date.get(stay_raw) or {}
        zone = zone_by_date.get(stay_raw) or {}
        travel = travel_by_date.get(stay_raw) or {}
        trend = trend_by_date.get(stay_raw) or {}
        rows.append(
            dict(
                shop_key="",  # 由调用方填充
                stay_date=stay,
                weekday=iso_weekday_value(stay),
                city_heat=city.get("heat"),
                city_heat_label=city.get("heat_label"),
                zone_heat=zone.get("heat"),
                zone_heat_label=zone.get("heat_label"),
                travel_heat=travel.get("heat"),
                travel_heat_last_year=travel.get("heat_last_year"),
                my_booked_rooms=(
                    int(comp["my_booked_rooms"])
                    if comp.get("my_booked_rooms") is not None
                    else None
                ),
                my_occupancy_rate=comp.get("my_occupancy_rate"),
                competitor_booked_rooms=(
                    int(comp["competitor_booked_rooms"])
                    if comp.get("competitor_booked_rooms") is not None
                    else None
                ),
                competitor_occupancy_rate=comp.get("competitor_occupancy_rate"),
                competitor_price_trend=trend.get("trend"),
                competitor_price_trend_last_year=trend.get("trend_last_year"),
                events_json=events_by_date.get(stay_raw) or None,
                captured_at=captured_at,
                source_row_ref=f"ctrip_market_demand:{account_id}:{stay_raw}:{stamp}",
            )
        )
    return rows


async def _write_demand_rows_from_payload(
    session: AsyncSession,
    *,
    shop_key: str,
    account_id: str,
    payload: dict[str, Any],
    captured_at: datetime,
) -> int:
    from app.domain.bi_daily.models import MarketDemandFact

    rows = _demand_fact_rows(payload, captured_at, account_id=account_id)
    if not rows:
        return 0
    facts = [dict(row, shop_key=shop_key, platform="ctrip") for row in rows]
    return await add_facts_if_absent(session, MarketDemandFact, facts)


async def refresh_weekday_mappings(
    session: AsyncSession, *, start: date, end: date
) -> int:
    """生成/回填去年同期同星期映射（幂等）。"""
    from app.domain.bi_daily.repository import upsert_weekday_mappings
    from app.domain.bi_daily.weekday_mapping import build_weekday_mappings_for_range

    specs = [
        {
            "current_stay_date": spec.current_date,
            "current_weekday": spec.current_weekday,
            "prior_year_same_weekday_stay_date": spec.prior_year_date,
            "matching_rule": spec.matching_rule,
            "holiday_flag": None,  # 无节假日数据源，不猜测
            "fallback_reason": spec.fallback_reason,
        }
        for spec in build_weekday_mappings_for_range(start, end)
    ]
    return await upsert_weekday_mappings(session, specs)
