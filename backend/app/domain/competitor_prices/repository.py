from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select

from app.core.settings import get_settings
from app.db.session import ensure_db_ready, session_scope
from app.domain.browser.ctrip_public_hotel_urls import (
    is_valid_public_ctrip_hotel_url,
    public_ctrip_hotel_id_from_url,
)
from app.domain.browser.ctrip_room_rate_rules import classify_breakfast_kind
from app.domain.competitor_prices.models import (
    CtripCompetitorHotel,
    CtripCompetitorHotelSnapshot,
    CtripCompetitorWatchGroupBrowserPoolMember,
    CtripCompetitorPriceRun,
    CtripCompetitorRoomRate,
    CtripCompetitorWatchGroup,
    local_now,
)
from app.domain.competitor_prices.schedule import (
    competitor_watch_schedule_state,
    watch_group_is_due,
)
from app.domain.competitor_prices.types import (
    DEFAULT_BROWSER_ROTATION_MODE,
    DEFAULT_FEISHU_CARD_LAYOUT,
    DEFAULT_PUSH_DATE_SCOPE,
    DEFAULT_POOL_COOLDOWN_MINUTES,
    DEFAULT_SCHEDULE_END_TIME,
    DEFAULT_SCHEDULE_INTERVAL_MINUTES,
    DEFAULT_SCHEDULE_MODE,
    DEFAULT_SCHEDULE_START_TIME,
    DEFAULT_WATCH_GROUP_KEY,
    DEFAULT_WATCH_GROUP_NAME,
    CompetitorHotelConfigIn,
    CompetitorHotelConfigPatch,
    CompetitorBrowserPoolUpdate,
    CompetitorPersistenceResult,
    CompetitorWatchGroupCreate,
    CompetitorWatchGroupPatch,
    ConfiguredCompetitorTargets,
)
from app.domain.operator_payload import sanitize_persisted_payload

_POLL_STATUSES_ADVANCE_SCHEDULE = frozenset({"success", "partial"})
_RETRY_COOLDOWN_STATUSES = frozenset({"needs_review", "failed", "human_required"})
_OPERATOR_RUN_STATUS_LABELS = {
    "success": "读取成功",
    "partial": "部分成功",
    "needs_review": "需要复核",
    "human_required": "需要人工处理",
    "failed": "读取失败",
}


def _poll_status_advances_schedule(result_status: str) -> bool:
    return str(result_status or "").strip() in _POLL_STATUSES_ADVANCE_SCHEDULE


def _operator_run_status_label(status: str) -> str:
    return _OPERATOR_RUN_STATUS_LABELS.get(str(status or "").strip(), str(status or "未知"))


def competitor_poll_retry_cooldown_summary(
    group: dict[str, Any],
    latest_run: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> str | None:
    if not isinstance(latest_run, dict):
        return None
    status = str(latest_run.get("status") or "").strip()
    if status not in _RETRY_COOLDOWN_STATUSES:
        return None
    captured_at = _parse_datetime(latest_run.get("captured_at"))
    if captured_at is None:
        return None
    current_time = (now or local_now()).replace(tzinfo=None)
    if captured_at.tzinfo is not None:
        captured_at = captured_at.replace(tzinfo=None)
    interval_minutes = _bounded_int(
        group.get("schedule_interval_minutes") or group.get("query_interval_minutes"),
        default=DEFAULT_SCHEDULE_INTERVAL_MINUTES,
        minimum=1,
        maximum=1440,
    )
    retry_at = captured_at + timedelta(minutes=interval_minutes)
    if current_time < retry_at:
        label = _operator_run_status_label(status)
        return (
            f"上一轮仍为{label}（{captured_at.strftime('%H:%M')}），"
            f"约 {retry_at.strftime('%H:%M')} 后再自动重试。"
        )
    return None


class CompetitorRetentionCleanupResult(BaseModel):
    status: str
    summary: str
    watch_group_key: str | None = None
    cutoff_at: str | None = None
    deleted_runs: int = 0
    deleted_snapshots: int = 0
    deleted_room_rates: int = 0


async def list_competitor_watch_group_summaries(
    *,
    include_secrets: bool = False,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripCompetitorWatchGroup).order_by(
                        CtripCompetitorWatchGroup.group_key.asc()
                    )
                )
            ).all()
        )
        if not groups:
            group = await _get_or_create_watch_group(
                session,
                watch_group_key=DEFAULT_WATCH_GROUP_KEY,
                platform_account_id=_default_platform_account_id(),
                preserve_existing_platform_account=True,
            )
            groups = [group]
        group_ids = [group.id for group in groups]
        counts = await _enabled_hotel_counts_by_group(session, group_ids)
        latest_runs = await _latest_runs_by_group(session, group_ids)
        browser_pools = await _browser_pool_summaries_by_group(session, groups)
    return [
        {
            **_watch_group_to_dict(
                group,
                browser_pool=browser_pools.get(group.id),
                include_secrets=include_secrets,
            ),
            **counts.get(
                group.id,
                {"enabled_own_hotel_count": 0, "enabled_competitor_hotel_count": 0},
            ),
            "runtime_config_status": _runtime_config_status(
                counts.get(group.id, {}).get("enabled_own_hotel_count", 0),
                counts.get(group.id, {}).get("enabled_competitor_hotel_count", 0),
            ),
            "latest_run": _run_summary_to_dict(latest_runs.get(group.id)),
        }
        for group in groups
    ]


async def _validate_competitor_shop_key(shop_key: str | None) -> str | None:
    """竞品组店铺归属校验（P5）：店铺须存在；空值归一为 None（未归属）。"""
    normalized = str(shop_key or "").strip() or None
    if normalized is None:
        return None
    from app.domain.shops import service as shops_service
    from app.domain.shops.service import ShopNotFound

    try:
        await shops_service.get_shop(normalized)
    except ShopNotFound:
        raise ValueError(f"店铺不存在：{normalized}，请先创建店铺。") from None
    return normalized


async def create_competitor_watch_group(request: CompetitorWatchGroupCreate) -> dict[str, Any] | None:
    feishu_chat_id = _clean_text(request.feishu_chat_id)
    if feishu_chat_id:
        from app.domain.config.service import require_active_feishu_chat_agent_binding

        await require_active_feishu_chat_agent_binding(feishu_chat_id)
    await ensure_db_ready()
    async with session_scope() as session:
        existing = await _get_watch_group(session, request.group_key)
        if existing is not None:
            return None
        browser_session_id = _clean_text(request.browser_session_id)
        platform_account_id = _clean_text(request.platform_account_id)
        if browser_session_id:
            browser_session = _validate_browser_session(browser_session_id)
            platform_account_id = browser_session.platform_account_id
        pricing_platform_account_id, pricing_browser_session_id = _resolve_pricing_binding(
            browser_session_id=request.pricing_browser_session_id,
            platform_account_id=request.pricing_platform_account_id,
        )
        group_shop_key = await _validate_competitor_shop_key(request.shop_key)
        group = CtripCompetitorWatchGroup(
            group_key=request.group_key,
            name=request.name.strip() or request.group_key,
            brief_title=_clean_text(request.brief_title),
            price_change_alert_title=_clean_text(request.price_change_alert_title),
            shop_key=group_shop_key,
            platform_account_id=platform_account_id or _default_platform_account_id(),
            browser_session_id=browser_session_id,
            browser_rotation_mode=_normalize_browser_rotation_mode(
                request.browser_rotation_mode
            ),
            pool_failover_enabled=(
                bool(request.pool_failover_enabled)
                if request.pool_failover_enabled is not None
                else True
            ),
            pool_cooldown_minutes=_bounded_int(
                request.pool_cooldown_minutes,
                default=DEFAULT_POOL_COOLDOWN_MINUTES,
                minimum=1,
                maximum=1440,
            ),
            pricing_platform_account_id=pricing_platform_account_id,
            pricing_browser_session_id=pricing_browser_session_id,
            feishu_webhook_url=_clean_text(request.feishu_webhook_url),
            feishu_mobile_webhook_url=_clean_text(request.feishu_mobile_webhook_url),
            feishu_desktop_webhook_url=_clean_text(request.feishu_desktop_webhook_url),
            feishu_chat_id=feishu_chat_id,
            feishu_card_layout=_normalize_feishu_card_layout(request.feishu_card_layout),
            push_date_scope=_normalize_push_date_scope(request.push_date_scope),
            retention_days=request.retention_days or 14,
            query_interval_minutes=request.query_interval_minutes
            or request.schedule_interval_minutes
            or DEFAULT_SCHEDULE_INTERVAL_MINUTES,
            schedule_mode=_normalize_schedule_mode(request.schedule_mode),
            schedule_start_time=_normalize_time_of_day(
                request.schedule_start_time,
                default=DEFAULT_SCHEDULE_START_TIME,
            ),
            schedule_end_time=_normalize_time_of_day(
                request.schedule_end_time,
                default=DEFAULT_SCHEDULE_END_TIME,
            ),
            schedule_interval_minutes=request.schedule_interval_minutes
            or request.query_interval_minutes
            or DEFAULT_SCHEDULE_INTERVAL_MINUTES,
            auto_poll_enabled=bool(request.auto_poll_enabled)
            if request.auto_poll_enabled is not None
            else False,
            enabled=bool(request.enabled) if request.enabled is not None else True,
        )
        await _ensure_unique_enabled_browser_session_binding(session, group)
        await _ensure_pool_ready_for_auto_poll(session, group)
        session.add(group)
        await session.flush()
    return _watch_group_to_dict(group)


async def get_competitor_watch_group_detail(
    *,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
    include_secrets: bool = False,
    create_if_missing: bool = True,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        if create_if_missing:
            group = await _get_or_create_watch_group(
                session,
                watch_group_key=watch_group_key,
                platform_account_id=_default_platform_account_id(),
                preserve_existing_platform_account=True,
            )
        else:
            group = await _get_watch_group(session, watch_group_key)
            if group is None:
                return None
        counts = await _enabled_hotel_counts_by_group(session, [group.id])
        latest_runs = await _latest_runs_by_group(session, [group.id])
        browser_pool = await _browser_pool_summary_for_group(session, group)
    return {
        **_watch_group_to_dict(
            group,
            include_secrets=include_secrets,
            browser_pool=browser_pool,
        ),
        **counts.get(group.id, {"enabled_own_hotel_count": 0, "enabled_competitor_hotel_count": 0}),
        "runtime_config_status": _runtime_config_status(
            counts.get(group.id, {}).get("enabled_own_hotel_count", 0),
            counts.get(group.id, {}).get("enabled_competitor_hotel_count", 0),
        ),
        "latest_run": _run_summary_to_dict(latest_runs.get(group.id)),
    }


async def get_competitor_watch_group_by_feishu_chat_id(
    chat_id: str | None,
    *,
    include_secrets: bool = False,
) -> dict[str, Any] | None:
    groups = await list_competitor_watch_groups_by_feishu_chat_id(
        chat_id,
        include_secrets=include_secrets,
    )
    if not groups:
        return None
    if len(groups) > 1:
        return None
    return groups[0]


async def list_competitor_watch_groups_by_feishu_chat_id(
    chat_id: str | None,
    *,
    include_secrets: bool = False,
) -> list[dict[str, Any]]:
    chat_id = _clean_text(chat_id)
    if not chat_id:
        return []
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripCompetitorWatchGroup)
                    .where(CtripCompetitorWatchGroup.feishu_chat_id == chat_id)
                    .order_by(CtripCompetitorWatchGroup.group_key.asc())
                )
            ).all()
        )
        if not groups:
            return []
        counts = await _enabled_hotel_counts_by_group(session, [group.id for group in groups])
        latest_runs = await _latest_runs_by_group(session, [group.id for group in groups])
        browser_pools = await _browser_pool_summaries_by_group(session, groups)
    results: list[dict[str, Any]] = []
    for group in groups:
        results.append(
            {
                **_watch_group_to_dict(
                    group,
                    include_secrets=include_secrets,
                    browser_pool=browser_pools.get(group.id),
                ),
                **counts.get(group.id, {"enabled_own_hotel_count": 0, "enabled_competitor_hotel_count": 0}),
                "runtime_config_status": _runtime_config_status(
                    counts.get(group.id, {}).get("enabled_own_hotel_count", 0),
                    counts.get(group.id, {}).get("enabled_competitor_hotel_count", 0),
                ),
                "latest_run": _run_summary_to_dict(latest_runs.get(group.id)),
            }
        )
    return results


async def delete_competitor_watch_group(
    *,
    watch_group_key: str,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        payload = _watch_group_to_dict(group)
        group_id = group.id
        snapshot_ids = list(
            (
                await session.scalars(
                    select(CtripCompetitorHotelSnapshot.id).where(
                        CtripCompetitorHotelSnapshot.group_id == group_id
                    )
                )
            ).all()
        )
        if snapshot_ids:
            await session.execute(
                delete(CtripCompetitorRoomRate).where(
                    CtripCompetitorRoomRate.snapshot_id.in_(snapshot_ids)
                )
            )
        await session.execute(
            delete(CtripCompetitorHotelSnapshot).where(
                CtripCompetitorHotelSnapshot.group_id == group_id
            )
        )
        await session.execute(
            delete(CtripCompetitorPriceRun).where(
                CtripCompetitorPriceRun.group_id == group_id
            )
        )
        await session.execute(
            delete(CtripCompetitorHotel).where(CtripCompetitorHotel.group_id == group_id)
        )
        await session.delete(group)
        await session.flush()
    return payload


async def update_competitor_watch_group(
    *,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
    patch: CompetitorWatchGroupPatch,
) -> dict[str, Any] | None:
    feishu_chat_id = (
        patch.feishu_chat_id.strip() or None
        if patch.feishu_chat_id is not None
        else None
    )
    if feishu_chat_id and not patch.clear_binding:
        from app.domain.config.service import require_active_feishu_chat_agent_binding

        await require_active_feishu_chat_agent_binding(feishu_chat_id)
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_or_create_watch_group(
            session,
            watch_group_key=watch_group_key,
            platform_account_id=patch.platform_account_id or _default_platform_account_id(),
            preserve_existing_platform_account=patch.platform_account_id is None,
        )
        if patch.name is not None:
            group.name = patch.name.strip() or group.name
        if patch.brief_title is not None:
            group.brief_title = patch.brief_title.strip() or None
        if patch.price_change_alert_title is not None:
            group.price_change_alert_title = patch.price_change_alert_title.strip() or None
        if patch.shop_key is not None:
            group.shop_key = await _validate_competitor_shop_key(patch.shop_key)
        if patch.browser_session_id is not None:
            browser_session_id = patch.browser_session_id.strip()
            if browser_session_id:
                browser_session = _validate_browser_session(browser_session_id)
                group.browser_session_id = browser_session.browser_session_id
                group.platform_account_id = browser_session.platform_account_id
            else:
                group.browser_session_id = None
        if patch.browser_rotation_mode is not None:
            group.browser_rotation_mode = _normalize_browser_rotation_mode(
                patch.browser_rotation_mode
            )
        if patch.pool_failover_enabled is not None:
            group.pool_failover_enabled = bool(patch.pool_failover_enabled)
        if patch.pool_cooldown_minutes is not None:
            group.pool_cooldown_minutes = _bounded_int(
                patch.pool_cooldown_minutes,
                default=DEFAULT_POOL_COOLDOWN_MINUTES,
                minimum=1,
                maximum=1440,
            )
        if patch.platform_account_id is not None and patch.browser_session_id is None:
            group.platform_account_id = patch.platform_account_id.strip()
        if patch.pricing_browser_session_id is not None:
            pricing_platform_account_id, pricing_browser_session_id = _resolve_pricing_binding(
                browser_session_id=patch.pricing_browser_session_id,
                platform_account_id=None,
            )
            group.pricing_platform_account_id = pricing_platform_account_id
            group.pricing_browser_session_id = pricing_browser_session_id
        if patch.pricing_platform_account_id is not None and patch.pricing_browser_session_id is None:
            pricing_platform_account_id, pricing_browser_session_id = _resolve_pricing_binding(
                browser_session_id=None,
                platform_account_id=patch.pricing_platform_account_id,
            )
            group.pricing_platform_account_id = pricing_platform_account_id
            group.pricing_browser_session_id = pricing_browser_session_id
        if patch.feishu_webhook_url is not None:
            group.feishu_webhook_url = patch.feishu_webhook_url.strip() or None
        if patch.feishu_mobile_webhook_url is not None:
            group.feishu_mobile_webhook_url = patch.feishu_mobile_webhook_url.strip() or None
        if patch.feishu_desktop_webhook_url is not None:
            group.feishu_desktop_webhook_url = patch.feishu_desktop_webhook_url.strip() or None
        if patch.feishu_chat_id is not None:
            group.feishu_chat_id = feishu_chat_id
        if patch.feishu_card_layout is not None:
            group.feishu_card_layout = _normalize_feishu_card_layout(patch.feishu_card_layout)
        if patch.push_date_scope is not None:
            group.push_date_scope = _normalize_push_date_scope(patch.push_date_scope)
        if patch.retention_days is not None:
            group.retention_days = patch.retention_days
        if patch.query_interval_minutes is not None:
            group.query_interval_minutes = patch.query_interval_minutes
            if patch.schedule_interval_minutes is None:
                group.schedule_interval_minutes = patch.query_interval_minutes
        if patch.schedule_mode is not None:
            group.schedule_mode = _normalize_schedule_mode(patch.schedule_mode)
        if patch.schedule_start_time is not None:
            group.schedule_start_time = _normalize_time_of_day(
                patch.schedule_start_time,
                default=DEFAULT_SCHEDULE_START_TIME,
            )
        if patch.schedule_end_time is not None:
            group.schedule_end_time = _normalize_time_of_day(
                patch.schedule_end_time,
                default=DEFAULT_SCHEDULE_END_TIME,
            )
        if patch.schedule_interval_minutes is not None:
            group.schedule_interval_minutes = patch.schedule_interval_minutes
            group.query_interval_minutes = patch.schedule_interval_minutes
        if patch.auto_poll_enabled is not None:
            group.auto_poll_enabled = patch.auto_poll_enabled
        if patch.enabled is not None:
            group.enabled = patch.enabled
        if patch.clear_binding:
            group.platform_account_id = "__cleared__"
            group.browser_session_id = None
            group.enabled = False
            group.auto_poll_enabled = False
        if patch.clear_pricing_binding:
            group.pricing_platform_account_id = None
            group.pricing_browser_session_id = None
        await _ensure_unique_enabled_browser_session_binding(session, group)
        await _ensure_pool_ready_for_auto_poll(session, group)
        await session.flush()
    return await get_competitor_watch_group_detail(watch_group_key=watch_group_key)


async def get_competitor_watch_group_browser_pool(
    *,
    watch_group_key: str,
    include_disabled: bool = True,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        browser_pool = await _browser_pool_summary_for_group(
            session,
            group,
            include_disabled=include_disabled,
        )
    return {
        "watch_group_key": watch_group_key,
        "browser_pool": browser_pool,
    }


async def update_competitor_watch_group_browser_pool(
    *,
    watch_group_key: str,
    request: CompetitorBrowserPoolUpdate,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        await session.execute(
            delete(CtripCompetitorWatchGroupBrowserPoolMember).where(
                CtripCompetitorWatchGroupBrowserPoolMember.group_id == group.id
            )
        )
        seen: set[str] = set()
        for index, member in enumerate(request.members):
            browser_session = _validate_browser_session(member.browser_session_id)
            if browser_session.browser_session_id in seen:
                continue
            seen.add(browser_session.browser_session_id)
            session.add(
                CtripCompetitorWatchGroupBrowserPoolMember(
                    group_id=group.id,
                    browser_session_id=browser_session.browser_session_id,
                    enabled=member.enabled,
                    sort_order=member.sort_order if member.sort_order is not None else index,
                    weight=member.weight,
                )
            )
        await session.flush()
        await _ensure_pool_ready_for_auto_poll(session, group)
        browser_pool = await _browser_pool_summary_for_group(session, group)
    return {
        "watch_group_key": watch_group_key,
        "browser_pool": browser_pool,
    }


async def list_competitor_hotels(
    *,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return []
        statement = (
            select(CtripCompetitorHotel)
            .where(CtripCompetitorHotel.group_id == group.id)
            .order_by(
                CtripCompetitorHotel.role.desc(),
                CtripCompetitorHotel.sort_order.asc(),
                CtripCompetitorHotel.id.asc(),
            )
        )
        if not include_disabled:
            statement = statement.where(CtripCompetitorHotel.enabled.is_(True))
        hotels = list((await session.scalars(statement)).all())
    return [_hotel_to_dict(hotel) for hotel in hotels]


async def upsert_competitor_hotel_config(
    *,
    platform_account_id: str,
    hotel: CompetitorHotelConfigIn,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
) -> dict[str, Any]:
    await ensure_db_ready()
    config = _target_config_from_hotel_input(hotel)
    async with session_scope() as session:
        group = await _get_or_create_watch_group(
            session,
            watch_group_key=watch_group_key,
            platform_account_id=platform_account_id or _default_platform_account_id(),
            preserve_existing_platform_account=True,
        )
        existing = await _find_hotel_by_ctrip_id(session, group.id, config["ctrip_hotel_id"])
        if existing is None:
            existing = CtripCompetitorHotel(
                group_id=group.id,
                role=config["role"],
                sort_order=config["sort_order"],
                alias=config["alias"],
                short_alias=config["short_alias"],
                ctrip_hotel_id=config["ctrip_hotel_id"],
                city_id=config["city_id"],
                city_en_name=config["city_en_name"],
                detail_url=config["url"],
                enabled=hotel.enabled,
            )
            session.add(existing)
            await session.flush()
        else:
            existing.role = config["role"]
            existing.sort_order = config["sort_order"]
            existing.alias = config["alias"]
            existing.short_alias = config["short_alias"]
            existing.city_id = config["city_id"]
            existing.city_en_name = config["city_en_name"]
            existing.detail_url = config["url"]
            existing.enabled = hotel.enabled
            await session.flush()
    return _hotel_to_dict(existing)


async def update_competitor_hotel_config(
    *,
    hotel_config_id: int,
    patch: CompetitorHotelConfigPatch,
    watch_group_key: str | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        hotel = await _get_hotel_for_update(
            session,
            hotel_config_id=hotel_config_id,
            watch_group_key=watch_group_key,
        )
        if hotel is None:
            return None
        if patch.role is not None:
            hotel.role = patch.role
        if patch.alias is not None:
            hotel.alias = patch.alias.strip()
        if "short_alias" in patch.model_fields_set:
            hotel.short_alias = _normalize_short_alias(patch.short_alias)
        if patch.hotel_name is not None:
            hotel.hotel_name = patch.hotel_name.strip() or None
        if patch.detail_url is not None:
            detail_url = _validated_public_ctrip_hotel_url(patch.detail_url)
            hotel.detail_url = detail_url
            hotel.ctrip_hotel_id = _extract_ctrip_hotel_id(detail_url)
            hotel.city_id = _extract_query_value(detail_url, "cityId")
            hotel.city_en_name = _extract_query_value(detail_url, "cityEnName")
        if patch.sort_order is not None:
            hotel.sort_order = patch.sort_order
        if patch.enabled is not None:
            hotel.enabled = patch.enabled
        await session.flush()
    return _hotel_to_dict(hotel)


async def delete_competitor_hotel_config(
    *,
    hotel_config_id: int,
    watch_group_key: str | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        hotel = await _get_hotel_for_update(
            session,
            hotel_config_id=hotel_config_id,
            watch_group_key=watch_group_key,
        )
        if hotel is None:
            return None
        if hotel.role == "own":
            raise ValueError("本店配置不能从列表移除，请直接编辑本店 URL 或别名。")
        hotel.enabled = False
        await session.flush()
    return _hotel_to_dict(hotel)


async def resolve_configured_competitor_targets(
    *,
    platform_account_id: str,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
) -> ConfiguredCompetitorTargets | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None or not group.enabled:
            return None
        if (
            _normalize_browser_rotation_mode(group.browser_rotation_mode) != "pool_round_robin"
            and group.platform_account_id != platform_account_id
        ):
            return None
        hotels = list(
            (
                await session.scalars(
                    select(CtripCompetitorHotel)
                    .where(
                        CtripCompetitorHotel.group_id == group.id,
                        CtripCompetitorHotel.enabled.is_(True),
                    )
                    .order_by(
                        CtripCompetitorHotel.role.desc(),
                        CtripCompetitorHotel.sort_order.asc(),
                    )
                )
            ).all()
        )
    own_hotel_configs = [hotel for hotel in hotels if hotel.role == "own"]
    competitor_configs = [hotel for hotel in hotels if hotel.role == "competitor"]
    own_hotels = [hotel.detail_url for hotel in own_hotel_configs]
    competitors = [hotel.detail_url for hotel in competitor_configs]
    if len(own_hotels) != 1 or not competitors:
        return None
    return ConfiguredCompetitorTargets(
        own_hotel_url=own_hotels[0],
        competitor_urls=competitors,
        own_hotel_alias=own_hotel_configs[0].alias,
        competitor_aliases=[hotel.alias for hotel in competitor_configs],
    )


async def save_ctrip_competitor_price_result(
    *,
    result_status: str,
    summary: str,
    platform_account_id: str,
    browser_session_id: str | None,
    actor_id: str,
    data: dict[str, Any],
    evidence: dict[str, Any],
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
    source: str = "api",
    push_result: dict[str, Any] | None = None,
) -> CompetitorPersistenceResult:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_or_create_watch_group(
            session,
            watch_group_key=watch_group_key,
            platform_account_id=platform_account_id,
            preserve_existing_platform_account=True,
        )
        targets = _target_configs_from_result(data)
        hotels_by_role = await _configured_hotels_by_result_role(session, group.id, targets)
        captured_at = _parse_datetime(data.get("captured_at")) or local_now()
        run = CtripCompetitorPriceRun(
            group_id=group.id,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            source=source,
            status=result_status,
            summary=summary,
            parser_version=str(evidence.get("parser_version") or ""),
            target_count=len(targets),
            captured_at=captured_at,
            raw_result_json=_strip_sensitive({"data": data, "evidence": evidence}),
            push_result_json=_strip_sensitive(push_result),
        )
        session.add(run)
        await session.flush()

        snapshot_count = 0
        skipped_snapshot_count = 0
        rate_count = 0
        trusted_rate_count = 0
        hotel_statuses_this_run: dict[int, str] = {}
        for snapshot in _iter_result_snapshots(data):
            hotel = await _resolve_snapshot_hotel(session, group, hotels_by_role, snapshot)
            if hotel is None:
                skipped_snapshot_count += 1
                continue
            hotel_snapshot = _build_hotel_snapshot(
                run=run,
                group_id=group.id,
                hotel=hotel,
                snapshot=snapshot,
            )
            session.add(hotel_snapshot)
            await session.flush()
            snapshot_count += 1
            for index, rate in enumerate(_room_rates_from_snapshot(snapshot)):
                room_rate = _build_room_rate(
                    snapshot=hotel_snapshot,
                    rate=rate,
                    is_primary=index == 0,
                )
                session.add(room_rate)
                rate_count += 1
                trusted_rate_count += int(room_rate.is_trusted)
            if _snapshot_can_update_hotel_name(hotel_snapshot):
                hotel.hotel_name = hotel_snapshot.hotel_name or hotel.hotel_name
            hotel.last_captured_at = captured_at
            hotel.last_status = hotel_statuses_this_run[hotel.id] = _combined_snapshot_status(
                hotel_statuses_this_run.get(hotel.id),
                hotel_snapshot.snapshot_status,
            )
        if _poll_status_advances_schedule(result_status):
            group.last_polled_at = captured_at
        await session.flush()
        run_id = run.id
    return CompetitorPersistenceResult(
        status="success" if skipped_snapshot_count == 0 else "partial",
        summary=(
            f"Saved competitor price run {run_id}: {snapshot_count} snapshots, "
            f"{trusted_rate_count} trusted room rates."
        ),
        watch_group_key=watch_group_key,
        run_id=run_id,
        snapshot_count=snapshot_count,
        skipped_snapshot_count=skipped_snapshot_count,
        rate_count=rate_count,
        trusted_rate_count=trusted_rate_count,
    )


async def update_competitor_price_run_push_result(
    *,
    run_id: int,
    push_result: dict[str, Any],
) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        run = await session.get(CtripCompetitorPriceRun, run_id)
        if run is not None:
            run.push_result_json = _strip_sensitive(push_result)


async def get_latest_competitor_price_run_meta(
    *,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        latest_runs = await _latest_runs_by_group(session, [group.id])
        run = latest_runs.get(group.id)
        if run is None:
            return None
        return _run_summary_to_dict(run)


async def get_latest_competitor_price_snapshot(
    *,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
    include_disabled: bool = False,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        latest_run = await session.scalar(
            select(CtripCompetitorPriceRun)
            .where(CtripCompetitorPriceRun.group_id == group.id)
            .order_by(CtripCompetitorPriceRun.captured_at.desc(), CtripCompetitorPriceRun.id.desc())
            .limit(1)
        )
        if latest_run is None:
            return None
        display_run = latest_run
        review_pending_run = None
        if not _poll_status_advances_schedule(latest_run.status):
            review_pending_run = latest_run
            display_run = await session.scalar(
                select(CtripCompetitorPriceRun)
                .where(
                    CtripCompetitorPriceRun.group_id == group.id,
                    CtripCompetitorPriceRun.status.in_(tuple(_POLL_STATUSES_ADVANCE_SCHEDULE)),
                )
                .order_by(
                    CtripCompetitorPriceRun.captured_at.desc(),
                    CtripCompetitorPriceRun.id.desc(),
                )
                .limit(1)
            )
            if display_run is None:
                display_run = latest_run
        rows = (
            await session.execute(
                select(
                    CtripCompetitorHotel,
                    CtripCompetitorHotelSnapshot,
                    CtripCompetitorRoomRate,
                )
                .join(
                    CtripCompetitorHotelSnapshot,
                    CtripCompetitorHotelSnapshot.hotel_config_id == CtripCompetitorHotel.id,
                )
                .outerjoin(
                    CtripCompetitorRoomRate,
                    CtripCompetitorRoomRate.snapshot_id == CtripCompetitorHotelSnapshot.id,
                )
                .where(CtripCompetitorHotelSnapshot.run_id == display_run.id)
                .order_by(
                    CtripCompetitorHotel.role.desc(),
                    CtripCompetitorHotel.sort_order.asc(),
                    CtripCompetitorHotelSnapshot.date_label.asc(),
                    CtripCompetitorRoomRate.is_primary.desc(),
                    CtripCompetitorRoomRate.price_amount_yuan.asc(),
                )
            )
        ).all()
        if not include_disabled:
            rows = [row for row in rows if row[0].enabled]
    payload = _latest_snapshot_to_dict(display_run, rows, watch_group_key)
    if review_pending_run is not None and review_pending_run.id != display_run.id:
        payload["latest_poll_run"] = _run_summary_to_dict(review_pending_run)
        label = _operator_run_status_label(str(review_pending_run.status or ""))
        captured_text = review_pending_run.captured_at.isoformat()
        payload["operator_note"] = (
            f"最新轮询状态为「{label}」（{captured_text}），下列为上次可信价格。"
        )
    return payload


async def get_competitor_price_trend(
    *,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
    days: int = 7,
    granularity: Literal["run", "hour", "day"] = "run",
    date_label: str | None = None,
    breakfast_kind: str | None = None,
    include_disabled: bool = False,
) -> dict[str, Any] | None:
    if granularity not in {"run", "hour", "day"}:
        raise ValueError("granularity must be one of: run, hour, day")
    days = max(1, min(days, 3650))
    cutoff = local_now() - timedelta(days=days)
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        statement = (
            select(
                CtripCompetitorPriceRun.id.label("run_id"),
                CtripCompetitorPriceRun.captured_at.label("captured_at"),
                CtripCompetitorHotel.id.label("hotel_config_id"),
                CtripCompetitorHotel.role.label("hotel_role"),
                CtripCompetitorHotel.sort_order.label("sort_order"),
                CtripCompetitorHotel.alias.label("hotel_alias"),
                CtripCompetitorHotel.ctrip_hotel_id.label("ctrip_hotel_id"),
                CtripCompetitorHotelSnapshot.hotel_name.label("snapshot_hotel_name"),
                CtripCompetitorHotelSnapshot.date_label.label("date_label"),
                CtripCompetitorHotelSnapshot.checkin_date.label("checkin_date"),
                CtripCompetitorHotelSnapshot.checkout_date.label("checkout_date"),
                CtripCompetitorRoomRate.id.label("room_rate_id"),
                CtripCompetitorRoomRate.is_primary.label("is_primary"),
                CtripCompetitorRoomRate.room_type.label("room_type"),
                CtripCompetitorRoomRate.breakfast_text.label("breakfast_text"),
                CtripCompetitorRoomRate.breakfast_kind.label("breakfast_kind"),
                CtripCompetitorRoomRate.price_amount_yuan.label("price_amount_yuan"),
                CtripCompetitorRoomRate.availability_status.label("availability_status"),
                CtripCompetitorRoomRate.availability_text.label("availability_text"),
            )
            .select_from(CtripCompetitorRoomRate)
            .join(
                CtripCompetitorPriceRun,
                CtripCompetitorPriceRun.id == CtripCompetitorRoomRate.run_id,
            )
            .join(
                CtripCompetitorHotelSnapshot,
                CtripCompetitorHotelSnapshot.id == CtripCompetitorRoomRate.snapshot_id,
            )
            .join(
                CtripCompetitorHotel,
                CtripCompetitorHotel.id == CtripCompetitorRoomRate.hotel_config_id,
            )
            .where(
                CtripCompetitorPriceRun.group_id == group.id,
                CtripCompetitorPriceRun.captured_at >= cutoff,
                CtripCompetitorRoomRate.is_trusted.is_(True),
                CtripCompetitorRoomRate.source == "room_rate_dom_display_price",
                CtripCompetitorRoomRate.price_amount_yuan.is_not(None),
            )
            .order_by(
                CtripCompetitorHotel.role.desc(),
                CtripCompetitorHotel.sort_order.asc(),
                CtripCompetitorPriceRun.captured_at.asc(),
                CtripCompetitorPriceRun.id.asc(),
                CtripCompetitorRoomRate.is_primary.desc(),
                CtripCompetitorRoomRate.id.asc(),
            )
        )
        if not include_disabled:
            statement = statement.where(CtripCompetitorHotel.enabled.is_(True))
        if date_label and date_label != "all":
            statement = statement.where(CtripCompetitorHotelSnapshot.date_label == date_label)
        if breakfast_kind and breakfast_kind != "all":
            statement = statement.where(CtripCompetitorRoomRate.breakfast_kind == breakfast_kind)
        rows = list((await session.execute(statement)).all())
    rows = _first_trusted_room_type_rows(rows)
    return _trend_rows_to_dict(
        rows=rows,
        watch_group_key=watch_group_key,
        days=days,
        granularity=granularity,
    )


def _first_trusted_room_type_rows(rows: list[Any]) -> list[Any]:
    first_room_by_snapshot: dict[tuple[Any, ...], str | None] = {}
    for row in rows:
        key = (row.run_id, row.hotel_config_id, row.date_label, row.checkin_date)
        if key not in first_room_by_snapshot:
            first_room_by_snapshot[key] = row.room_type
    return [
        row
        for row in rows
        if row.room_type
        == first_room_by_snapshot.get(
            (row.run_id, row.hotel_config_id, row.date_label, row.checkin_date)
        )
    ]


async def list_due_competitor_watch_groups(*, now: datetime | None = None) -> list[dict[str, Any]]:
    await ensure_db_ready()
    current_time = (now or local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripCompetitorWatchGroup)
                    .where(
                        CtripCompetitorWatchGroup.enabled.is_(True),
                        CtripCompetitorWatchGroup.auto_poll_enabled.is_(True),
                    )
                    .order_by(CtripCompetitorWatchGroup.group_key.asc())
                )
            ).all()
        )
    return [_watch_group_to_dict(group, include_secrets=True) for group in groups if _group_is_due(group, current_time)]


async def mark_competitor_watch_group_polled(
    *,
    watch_group_key: str,
    captured_at: datetime | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        group.last_polled_at = (captured_at or local_now()).replace(tzinfo=None)
        await session.flush()
    return _watch_group_to_dict(group)


async def cleanup_expired_competitor_price_data(
    *,
    watch_group_key: str | None = None,
    now: datetime | None = None,
) -> CompetitorRetentionCleanupResult:
    await ensure_db_ready()
    current_time = (now or local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        statement = select(CtripCompetitorWatchGroup)
        if watch_group_key:
            statement = statement.where(CtripCompetitorWatchGroup.group_key == watch_group_key)
        groups = list((await session.scalars(statement)).all())
        if watch_group_key and not groups:
            return CompetitorRetentionCleanupResult(
                status="not_found",
                summary="未找到竞品监控组。",
                watch_group_key=watch_group_key,
            )
        total_runs = 0
        total_snapshots = 0
        total_rates = 0
        earliest_cutoff: datetime | None = None
        for group in groups:
            cutoff = current_time - timedelta(days=group.retention_days)
            earliest_cutoff = cutoff if earliest_cutoff is None else min(earliest_cutoff, cutoff)
            run_ids = list(
                (
                    await session.scalars(
                        select(CtripCompetitorPriceRun.id).where(
                            CtripCompetitorPriceRun.group_id == group.id,
                            CtripCompetitorPriceRun.captured_at < cutoff,
                        )
                    )
                ).all()
            )
            if not run_ids:
                continue
            snapshot_ids = list(
                (
                    await session.scalars(
                        select(CtripCompetitorHotelSnapshot.id).where(
                            CtripCompetitorHotelSnapshot.run_id.in_(run_ids)
                        )
                    )
                ).all()
            )
            if snapshot_ids:
                total_rates += _rowcount(
                    await session.execute(
                        delete(CtripCompetitorRoomRate).where(
                            CtripCompetitorRoomRate.snapshot_id.in_(snapshot_ids)
                        )
                    )
                )
                total_snapshots += _rowcount(
                    await session.execute(
                        delete(CtripCompetitorHotelSnapshot).where(
                            CtripCompetitorHotelSnapshot.id.in_(snapshot_ids)
                        )
                    )
                )
            total_runs += _rowcount(
                await session.execute(
                    delete(CtripCompetitorPriceRun).where(CtripCompetitorPriceRun.id.in_(run_ids))
                )
            )
    return CompetitorRetentionCleanupResult(
        status="success",
        summary=(
            f"Deleted {total_runs} expired competitor price run(s), "
            f"{total_snapshots} snapshot(s), and {total_rates} room rate(s)."
        ),
        watch_group_key=watch_group_key,
        cutoff_at=earliest_cutoff.isoformat() if earliest_cutoff else None,
        deleted_runs=total_runs,
        deleted_snapshots=total_snapshots,
        deleted_room_rates=total_rates,
    )


async def clear_competitor_watch_bindings_for_browser_session(
    *,
    platform_account_id: str,
    browser_session_id: str | None = None,
) -> int:
    await ensure_db_ready()
    async with session_scope() as session:
        filters = []
        if platform_account_id:
            filters.append(CtripCompetitorWatchGroup.platform_account_id == platform_account_id)
            filters.append(
                CtripCompetitorWatchGroup.pricing_platform_account_id == platform_account_id
            )
        if browser_session_id:
            filters.append(CtripCompetitorWatchGroup.browser_session_id == browser_session_id)
            filters.append(
                CtripCompetitorWatchGroup.pricing_browser_session_id == browser_session_id
            )
            pool_group_ids = list(
                (
                    await session.scalars(
                        select(CtripCompetitorWatchGroupBrowserPoolMember.group_id).where(
                            CtripCompetitorWatchGroupBrowserPoolMember.browser_session_id
                            == browser_session_id
                        )
                    )
                ).all()
            )
            if pool_group_ids:
                filters.append(CtripCompetitorWatchGroup.id.in_(pool_group_ids))
        if not filters:
            return 0
        groups = list(
            (await session.scalars(select(CtripCompetitorWatchGroup).where(or_(*filters)))).all()
        )
        for group in groups:
            clears_read_binding = (
                bool(platform_account_id and group.platform_account_id == platform_account_id)
                or bool(browser_session_id and group.browser_session_id == browser_session_id)
            )
            clears_pricing_binding = (
                bool(
                    platform_account_id
                    and group.pricing_platform_account_id == platform_account_id
                )
                or bool(
                    browser_session_id
                    and group.pricing_browser_session_id == browser_session_id
                )
            )
            if clears_read_binding:
                group.platform_account_id = "__cleared__"
                group.browser_session_id = None
                group.enabled = False
                group.auto_poll_enabled = False
            if clears_pricing_binding:
                group.pricing_platform_account_id = None
                group.pricing_browser_session_id = None
        if browser_session_id:
            await session.execute(
                delete(CtripCompetitorWatchGroupBrowserPoolMember).where(
                    CtripCompetitorWatchGroupBrowserPoolMember.browser_session_id
                    == browser_session_id
                )
            )
        await session.flush()
    return len(groups)


async def get_competitor_browser_pool_usage_by_browser_session() -> dict[str, list[dict[str, Any]]]:
    await ensure_db_ready()
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(
                        CtripCompetitorWatchGroupBrowserPoolMember,
                        CtripCompetitorWatchGroup,
                    )
                    .join(
                        CtripCompetitorWatchGroup,
                        CtripCompetitorWatchGroup.id
                        == CtripCompetitorWatchGroupBrowserPoolMember.group_id,
                    )
                    .order_by(
                        CtripCompetitorWatchGroup.name,
                        CtripCompetitorWatchGroupBrowserPoolMember.sort_order,
                    )
                )
            ).all()
        )
    usage_by_session: dict[str, list[dict[str, Any]]] = {}
    for member, group in rows:
        usage_by_session.setdefault(member.browser_session_id, []).append(
            _competitor_pool_usage_entry(group, member)
        )
    return usage_by_session


async def get_competitor_browser_pool_usage_for_browser_session(
    browser_session_id: str,
) -> list[dict[str, Any]]:
    usage = await get_competitor_browser_pool_usage_by_browser_session()
    return usage.get(str(browser_session_id or "").strip(), [])


async def validate_browser_session_intended_use_change(
    *,
    browser_session_id: str,
    platform_account_id: str,
    current_platform: str = "ctrip",
    next_platform: str | None = None,
    current_intended_use: str,
    next_intended_use: str | None,
    pricing_feishu_chat_id_configured: bool = False,
) -> None:
    next_use = str(next_intended_use or "").strip()
    current_use = str(current_intended_use or "").strip()
    target_use = next_use or current_use
    current_platform = str(current_platform or "").strip()
    target_platform = str(next_platform or "").strip() or current_platform
    use_changed = target_use != current_use
    platform_changed = target_platform != current_platform
    if not use_changed and not platform_changed:
        return
    if current_use == "competitor_price" and target_use == "ebk":
        usage = await get_competitor_browser_pool_usage_for_browser_session(browser_session_id)
        if usage:
            raise ValueError(
                "This browser account is still used in a competitor browser pool; "
                "remove it from the pool before changing its purpose."
            )
    pricing_capability_removed = current_use == "ebk" and (
        target_use != "ebk" or target_platform != "ctrip"
    )
    if pricing_capability_removed:
        if pricing_feishu_chat_id_configured:
            raise ValueError(
                "This Ctrip EBK browser account is still bound to a pricing Feishu group; "
                "clear that binding before changing its platform or purpose."
            )
        pricing_usage = await _competitor_pricing_binding_usage_for_browser_session(
            browser_session_id=browser_session_id,
            platform_account_id=platform_account_id,
        )
        if pricing_usage:
            raise ValueError(
                "This EBK browser account is still used as a competitor watch pricing account; "
                "remove that pricing binding before changing its platform or purpose."
            )


async def snapshot_competitor_watch_bindings_for_browser_session(
    *,
    platform_account_id: str,
    browser_session_id: str | None = None,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        filters = []
        if platform_account_id:
            filters.append(CtripCompetitorWatchGroup.platform_account_id == platform_account_id)
            filters.append(
                CtripCompetitorWatchGroup.pricing_platform_account_id == platform_account_id
            )
        if browser_session_id:
            filters.append(CtripCompetitorWatchGroup.browser_session_id == browser_session_id)
            filters.append(
                CtripCompetitorWatchGroup.pricing_browser_session_id == browser_session_id
            )
        if not filters:
            return []
        groups = list(
            (await session.scalars(select(CtripCompetitorWatchGroup).where(or_(*filters)))).all()
        )
    return [
        {
            "group_key": group.group_key,
            "platform_account_id": group.platform_account_id,
            "browser_session_id": group.browser_session_id,
            "pricing_platform_account_id": group.pricing_platform_account_id,
            "pricing_browser_session_id": group.pricing_browser_session_id,
            "enabled": group.enabled,
            "auto_poll_enabled": group.auto_poll_enabled,
        }
        for group in groups
    ]


async def restore_competitor_watch_bindings(snapshots: list[dict[str, Any]]) -> int:
    if not snapshots:
        return 0
    await ensure_db_ready()
    async with session_scope() as session:
        restored = 0
        for snapshot in snapshots:
            group_key = snapshot.get("group_key")
            if not isinstance(group_key, str) or not group_key:
                continue
            group = await _get_watch_group(session, group_key)
            if group is None:
                continue
            group.platform_account_id = str(snapshot["platform_account_id"])
            group.browser_session_id = _clean_text(snapshot.get("browser_session_id"))
            group.pricing_platform_account_id = _clean_text(
                snapshot.get("pricing_platform_account_id")
            )
            group.pricing_browser_session_id = _clean_text(
                snapshot.get("pricing_browser_session_id")
            )
            group.enabled = bool(snapshot["enabled"])
            group.auto_poll_enabled = bool(snapshot["auto_poll_enabled"])
            restored += 1
        await session.flush()
    return restored


async def migrate_competitor_watch_bindings_for_browser_session(
    *,
    old_platform_account_id: str,
    new_platform_account_id: str,
    browser_session_id: str | None = None,
) -> int:
    await ensure_db_ready()
    async with session_scope() as session:
        filters = [
            CtripCompetitorWatchGroup.platform_account_id == old_platform_account_id,
            CtripCompetitorWatchGroup.pricing_platform_account_id == old_platform_account_id,
        ]
        if browser_session_id:
            filters.append(CtripCompetitorWatchGroup.browser_session_id == browser_session_id)
            filters.append(
                CtripCompetitorWatchGroup.pricing_browser_session_id == browser_session_id
            )
        groups = list(
            (await session.scalars(select(CtripCompetitorWatchGroup).where(or_(*filters)))).all()
        )
        for group in groups:
            if group.platform_account_id == old_platform_account_id or (
                browser_session_id and group.browser_session_id == browser_session_id
            ):
                group.platform_account_id = new_platform_account_id
            if group.pricing_platform_account_id == old_platform_account_id or (
                browser_session_id and group.pricing_browser_session_id == browser_session_id
            ):
                group.pricing_platform_account_id = new_platform_account_id
        await session.flush()
    return len(groups)


def _competitor_pool_usage_entry(
    group: CtripCompetitorWatchGroup,
    member: CtripCompetitorWatchGroupBrowserPoolMember,
) -> dict[str, Any]:
    return {
        "watch_group_key": group.group_key,
        "watch_group_name": group.name,
        "browser_rotation_mode": _normalize_browser_rotation_mode(group.browser_rotation_mode),
        "pool_enabled": _normalize_browser_rotation_mode(group.browser_rotation_mode)
        == "pool_round_robin",
        "member_enabled": bool(member.enabled),
        "sort_order": member.sort_order,
        "last_status": member.last_status,
    }


async def _competitor_pricing_binding_usage_for_browser_session(
    *,
    browser_session_id: str,
    platform_account_id: str,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    filters = []
    if browser_session_id:
        filters.append(CtripCompetitorWatchGroup.pricing_browser_session_id == browser_session_id)
    if platform_account_id:
        filters.append(CtripCompetitorWatchGroup.pricing_platform_account_id == platform_account_id)
    if not filters:
        return []
    async with session_scope() as session:
        groups = list(
            (await session.scalars(select(CtripCompetitorWatchGroup).where(or_(*filters)))).all()
        )
    return [
        {
            "watch_group_key": group.group_key,
            "watch_group_name": group.name,
            "browser_rotation_mode": _normalize_browser_rotation_mode(
                group.browser_rotation_mode
            ),
        }
        for group in groups
    ]


def persistence_failure_result(*, watch_group_key: str, exc: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "summary": "竞品价格读取成功，但入库失败。",
        "watch_group_key": watch_group_key,
        "error_type": type(exc).__name__,
        "error": str(exc)[:500],
    }


async def _get_watch_group(session, watch_group_key: str) -> CtripCompetitorWatchGroup | None:
    return await session.scalar(
        select(CtripCompetitorWatchGroup).where(
            CtripCompetitorWatchGroup.group_key == watch_group_key
        )
    )


async def _get_or_create_watch_group(
    session,
    *,
    watch_group_key: str,
    platform_account_id: str,
    preserve_existing_platform_account: bool = False,
) -> CtripCompetitorWatchGroup:
    group = await _get_watch_group(session, watch_group_key)
    if group is not None:
        if not preserve_existing_platform_account:
            group.platform_account_id = platform_account_id
        return group
    group = CtripCompetitorWatchGroup(
        group_key=watch_group_key,
        name=DEFAULT_WATCH_GROUP_NAME,
        platform_account_id=platform_account_id,
    )
    session.add(group)
    await session.flush()
    return group


async def _enabled_hotel_counts_by_group(
    session,
    group_ids: list[int],
) -> dict[int, dict[str, int]]:
    if not group_ids:
        return {}
    rows = (
        await session.execute(
            select(
                CtripCompetitorHotel.group_id,
                CtripCompetitorHotel.role,
                func.count(CtripCompetitorHotel.id),
            )
            .where(
                CtripCompetitorHotel.group_id.in_(group_ids),
                CtripCompetitorHotel.enabled.is_(True),
            )
            .group_by(CtripCompetitorHotel.group_id, CtripCompetitorHotel.role)
        )
    ).all()
    counts: dict[int, dict[str, int]] = {}
    for group_id, role, count in rows:
        bucket = counts.setdefault(
            int(group_id),
            {"enabled_own_hotel_count": 0, "enabled_competitor_hotel_count": 0},
        )
        if role == "own":
            bucket["enabled_own_hotel_count"] = int(count or 0)
        if role == "competitor":
            bucket["enabled_competitor_hotel_count"] = int(count or 0)
    return counts


async def _latest_runs_by_group(
    session,
    group_ids: list[int],
) -> dict[int, CtripCompetitorPriceRun]:
    if not group_ids:
        return {}
    latest_captured = (
        select(
            CtripCompetitorPriceRun.group_id.label("group_id"),
            func.max(CtripCompetitorPriceRun.captured_at).label("captured_at"),
        )
        .where(CtripCompetitorPriceRun.group_id.in_(group_ids))
        .group_by(CtripCompetitorPriceRun.group_id)
        .subquery()
    )
    rows = (
        await session.scalars(
            select(CtripCompetitorPriceRun)
            .join(
                latest_captured,
                (CtripCompetitorPriceRun.group_id == latest_captured.c.group_id)
                & (CtripCompetitorPriceRun.captured_at == latest_captured.c.captured_at),
            )
            .order_by(
                CtripCompetitorPriceRun.group_id.asc(),
                CtripCompetitorPriceRun.id.desc(),
            )
        )
    ).all()
    latest: dict[int, CtripCompetitorPriceRun] = {}
    for run in rows:
        latest.setdefault(run.group_id, run)
    return latest


async def _find_hotel_by_ctrip_id(
    session,
    group_id: int,
    ctrip_hotel_id: str,
) -> CtripCompetitorHotel | None:
    return await session.scalar(
        select(CtripCompetitorHotel).where(
            CtripCompetitorHotel.group_id == group_id,
            CtripCompetitorHotel.ctrip_hotel_id == ctrip_hotel_id,
        )
    )


async def _get_hotel_for_update(
    session,
    *,
    hotel_config_id: int,
    watch_group_key: str | None,
) -> CtripCompetitorHotel | None:
    if not watch_group_key:
        return await session.get(CtripCompetitorHotel, hotel_config_id)
    return await session.scalar(
        select(CtripCompetitorHotel)
        .join(
            CtripCompetitorWatchGroup,
            CtripCompetitorWatchGroup.id == CtripCompetitorHotel.group_id,
        )
        .where(
            CtripCompetitorHotel.id == hotel_config_id,
            CtripCompetitorWatchGroup.group_key == watch_group_key,
        )
    )


async def _configured_hotels_by_result_role(
    session,
    group_id: int,
    targets: list[dict[str, Any]],
) -> dict[str, CtripCompetitorHotel]:
    hotels_by_role: dict[str, CtripCompetitorHotel] = {}
    for target in targets:
        hotel = await _find_hotel_by_ctrip_id(session, group_id, target["ctrip_hotel_id"])
        if hotel is not None:
            hotels_by_role[target["target_role"]] = hotel
    return hotels_by_role


async def _resolve_snapshot_hotel(
    session,
    group: CtripCompetitorWatchGroup,
    hotels_by_role: dict[str, CtripCompetitorHotel],
    snapshot: dict[str, Any],
) -> CtripCompetitorHotel | None:
    role = str(snapshot.get("role") or "")
    hotel = hotels_by_role.get(role)
    if hotel is not None:
        return hotel
    ctrip_hotel_id = _extract_ctrip_hotel_id(str(snapshot.get("requested_url") or role))
    return await _find_hotel_by_ctrip_id(session, group.id, ctrip_hotel_id)


def _target_configs_from_result(data: dict[str, Any]) -> list[dict[str, Any]]:
    targets = data.get("targets") if isinstance(data.get("targets"), list) else []
    aliases = data.get("target_aliases") if isinstance(data.get("target_aliases"), dict) else {}
    configs: list[dict[str, Any]] = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            continue
        url = str(target.get("url") or "").strip()
        if not url:
            continue
        role_value = str(target.get("role") or "")
        role = "own" if role_value == "own" else "competitor"
        configured_alias = _configured_alias_for_target(
            aliases=aliases,
            role_value=role_value,
            role=role,
            index=index,
        )
        configs.append(
            {
                "target_role": role_value or role,
                "role": role,
                "sort_order": 0 if role == "own" else _competitor_sort_order(role_value, index),
                "alias": str(target.get("label") or ("本店" if role == "own" else "竞品")),
                "url": url,
                "ctrip_hotel_id": _extract_ctrip_hotel_id(url),
                "city_id": _extract_query_value(url, "cityId"),
                "city_en_name": _extract_query_value(url, "cityEnName"),
            }
        )
        if configured_alias:
            configs[-1]["alias"] = configured_alias
    return configs


def _target_config_from_hotel_input(hotel: CompetitorHotelConfigIn) -> dict[str, Any]:
    detail_url = _validated_public_ctrip_hotel_url(hotel.detail_url)
    return {
        "target_role": "own" if hotel.role == "own" else f"competitor_{hotel.sort_order}",
        "role": hotel.role,
        "sort_order": hotel.sort_order,
        "alias": hotel.alias.strip(),
        "short_alias": _normalize_short_alias(hotel.short_alias),
        "url": detail_url,
        "ctrip_hotel_id": _extract_ctrip_hotel_id(detail_url),
        "city_id": _extract_query_value(detail_url, "cityId"),
        "city_en_name": _extract_query_value(detail_url, "cityEnName"),
    }


def _iter_result_snapshots(data: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = data.get("snapshots")
    if not isinstance(snapshots, list):
        return []
    items: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        items.append({**snapshot, "_date_label": "today"})
        tomorrow = snapshot.get("tomorrow")
        if isinstance(tomorrow, dict):
            items.append(
                {
                    **tomorrow,
                    "_date_label": "tomorrow",
                    "_parent": snapshot,
                    "role": snapshot.get("role"),
                    "label": snapshot.get("label"),
                    "configured_alias": snapshot.get("configured_alias"),
                    "requested_url": tomorrow.get("requested_url") or snapshot.get("requested_url"),
                    "current_url": tomorrow.get("current_url") or snapshot.get("current_url"),
                    "page_state": tomorrow.get("page_state") or snapshot.get("page_state"),
                }
            )
    return items


def _build_hotel_snapshot(
    *,
    run: CtripCompetitorPriceRun,
    group_id: int,
    hotel: CtripCompetitorHotel,
    snapshot: dict[str, Any],
) -> CtripCompetitorHotelSnapshot:
    checkin_date, checkout_date = _snapshot_dates(snapshot)
    captured_at = _parse_datetime(snapshot.get("captured_at")) or run.captured_at
    return CtripCompetitorHotelSnapshot(
        run_id=run.id,
        group_id=group_id,
        hotel_config_id=hotel.id,
        role=hotel.role,
        date_label=str(snapshot.get("_date_label") or "today"),
        checkin_date=checkin_date,
        checkout_date=checkout_date,
        hotel_name=str(snapshot.get("hotel_name") or hotel.hotel_name or hotel.alias),
        ctrip_hotel_id=hotel.ctrip_hotel_id,
        snapshot_status=str(snapshot.get("status") or "needs_review"),
        review_reason=_optional_str(snapshot.get("review_reason")),
        parser_confidence=_optional_str(snapshot.get("parser_confidence")),
        page_state=_optional_str(snapshot.get("page_state")),
        requested_url=_optional_str(snapshot.get("requested_url")),
        current_url=_optional_str(snapshot.get("current_url")),
        captured_at=captured_at,
        raw_snippets_json=_strip_sensitive(
            {
                "raw_snippets": snapshot.get("raw_snippets") or [],
                "clue_lines": snapshot.get("clue_lines") or {},
            }
        ),
    )


def _configured_alias_for_target(
    *,
    aliases: dict[str, Any],
    role_value: str,
    role: str,
    index: int,
) -> str | None:
    candidates = [role_value, role]
    if role == "competitor":
        candidates.extend([f"competitor_{index}", str(index)])
    for key in candidates:
        value = aliases.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _snapshot_can_update_hotel_name(snapshot: CtripCompetitorHotelSnapshot) -> bool:
    if snapshot.snapshot_status != "success":
        return False
    if snapshot.page_state and snapshot.page_state != "authenticated":
        return False
    name = str(snapshot.hotel_name or "").strip()
    if not name:
        return False
    if name in {"登录首页", "本店", "我们店"}:
        return False
    if re.fullmatch(r"竞品\d*", name):
        return False
    return True


def _combined_snapshot_status(current: str | None, incoming: str) -> str:
    priority = {
        "failed": 5,
        "human_required": 4,
        "needs_review": 3,
        "partial": 2,
        "success": 1,
    }
    if priority.get(incoming, 0) >= priority.get(current or "", 0):
        return incoming
    return current or incoming


def _room_rates_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    room_rates = snapshot.get("room_rates")
    if isinstance(room_rates, list) and room_rates:
        return [rate for rate in room_rates if isinstance(rate, dict)]
    price = snapshot.get("price")
    if not isinstance(price, dict):
        return []
    return [
        {
            "room_type": snapshot.get("room_type"),
            "breakfast": snapshot.get("breakfast"),
            "price": price,
            "availability": snapshot.get("availability"),
            "source": "snapshot_price",
        }
    ]


def _build_room_rate(
    *,
    snapshot: CtripCompetitorHotelSnapshot,
    rate: dict[str, Any],
    is_primary: bool,
) -> CtripCompetitorRoomRate:
    price = rate.get("price")
    amount = price.get("amount") if isinstance(price, dict) else None
    currency = str(price.get("currency") or "CNY") if isinstance(price, dict) else "CNY"
    source = _optional_str(rate.get("source"))
    breakfast_text = _optional_str(rate.get("breakfast"))
    availability_text = _optional_str(rate.get("availability"))
    discount_amount = _discount_amount_yuan(rate)
    discount_display = _discount_display(rate, discount_amount)
    is_trusted = (
        snapshot.snapshot_status == "success"
        and source == "room_rate_dom_display_price"
        and isinstance(amount, int)
    )
    return CtripCompetitorRoomRate(
        snapshot_id=snapshot.id,
        run_id=snapshot.run_id,
        hotel_config_id=snapshot.hotel_config_id,
        room_type=_optional_str(rate.get("room_type")),
        breakfast_text=breakfast_text,
        breakfast_kind=classify_breakfast_kind(breakfast_text),
        price_amount_yuan=amount if isinstance(amount, int) else None,
        discount_amount_yuan=discount_amount,
        discount_display=discount_display,
        currency=currency,
        availability_text=availability_text,
        availability_status=_availability_status(availability_text),
        source=source,
        source_price_line=_optional_str(rate.get("source_price_line")),
        source_discount_line=_optional_str(rate.get("source_discount_line")),
        source_room_line=_optional_str(rate.get("source_room_line")),
        source_breakfast_line=_optional_str(rate.get("source_breakfast_line")),
        is_primary=is_primary,
        is_trusted=is_trusted,
    )


def _discount_amount_yuan(rate: dict[str, Any]) -> int | None:
    value = rate.get("discount_amount_yuan")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= 20000 else None
    for key in ("discount_display", "source_discount_line"):
        text = rate.get(key)
        if text is None:
            continue
        match = re.search(r"优惠\s*(\d{1,5})", str(text))
        if not match:
            continue
        amount = int(match.group(1))
        if 0 < amount <= 20000:
            return amount
    return None


def _discount_display(rate: dict[str, Any], amount: int | None) -> str | None:
    value = _optional_str(rate.get("discount_display"))
    if value:
        return value[:64]
    if amount is not None:
        return f"优惠{amount}"
    return None


def _snapshot_dates(snapshot: dict[str, Any]) -> tuple[date | None, date | None]:
    context = snapshot.get("date_context") if isinstance(snapshot.get("date_context"), dict) else {}
    checkin = _parse_date(context.get("check_in") or context.get("checkin"))
    checkout = _parse_date(context.get("check_out") or context.get("checkout"))
    if checkin is None:
        checkin = _parse_date(_extract_query_value(str(snapshot.get("requested_url") or ""), "checkIn"))
    if checkout is None:
        checkout = _parse_date(
            _extract_query_value(str(snapshot.get("requested_url") or ""), "checkOut")
        )
    if checkout is None and checkin is not None:
        checkout = checkin + timedelta(days=1)
    return checkin, checkout


def _availability_status(value: str | None) -> str:
    if not value:
        return "normal"
    normalized = value.strip().lower()
    if normalized in {"tight", "low_inventory", "limited_inventory"}:
        return "tight"
    if normalized in {"sold_out", "soldout", "full"}:
        return "sold_out"
    if any(term in value for term in ("房量紧张", "仅剩", "剩余")):
        return "tight"
    if any(term in value for term in ("订完", "售罄", "满房")):
        return "sold_out"
    return "normal"


def _extract_ctrip_hotel_id(value: str) -> str:
    public_hotel_id = public_ctrip_hotel_id_from_url(value)
    if public_hotel_id:
        return public_hotel_id
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    for key in ("hotelId", "hotelid", "hotelID"):
        if query.get(key):
            return str(query[key][0])
    path_match = re.search(r"hotel(?:s)?[/_-]*(\d{5,})", parsed.path, re.IGNORECASE)
    if path_match:
        return path_match.group(1)
    digit_match = re.search(r"\d{5,}", value)
    if digit_match:
        return digit_match.group(0)
    return f"unknown-{abs(hash(value))}"


def _validated_public_ctrip_hotel_url(value: str) -> str:
    normalized = str(value or "").strip()
    if not is_valid_public_ctrip_hotel_url(normalized):
        raise ValueError("Only public Ctrip hotel detail URLs from hotels.ctrip.com are supported.")
    return normalized


def _extract_query_value(url: str, key: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    values = query.get(key)
    return str(values[0]) if values else None


def _competitor_sort_order(role: str, fallback: int) -> int:
    match = re.search(r"(\d+)$", role)
    return int(match.group(1)) if match else fallback


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strip_sensitive(value: Any) -> Any:
    return sanitize_persisted_payload(value)


def _normalize_short_alias(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _hotel_to_dict(hotel: CtripCompetitorHotel) -> dict[str, Any]:
    return {
        "id": hotel.id,
        "group_id": hotel.group_id,
        "role": hotel.role,
        "sort_order": hotel.sort_order,
        "alias": hotel.alias,
        "short_alias": hotel.short_alias,
        "hotel_name": hotel.hotel_name,
        "ctrip_hotel_id": hotel.ctrip_hotel_id,
        "city_id": hotel.city_id,
        "city_en_name": hotel.city_en_name,
        "detail_url": hotel.detail_url,
        "enabled": hotel.enabled,
        "last_captured_at": hotel.last_captured_at.isoformat() if hotel.last_captured_at else None,
        "last_status": hotel.last_status,
    }


async def _browser_pool_summaries_by_group(
    session,
    groups: list[CtripCompetitorWatchGroup],
) -> dict[int, dict[str, Any]]:
    group_ids = [group.id for group in groups if group.id is not None]
    if not group_ids:
        return {}
    members = list(
        (
            await session.scalars(
                select(CtripCompetitorWatchGroupBrowserPoolMember)
                .where(CtripCompetitorWatchGroupBrowserPoolMember.group_id.in_(group_ids))
                .order_by(
                    CtripCompetitorWatchGroupBrowserPoolMember.group_id.asc(),
                    CtripCompetitorWatchGroupBrowserPoolMember.sort_order.asc(),
                    CtripCompetitorWatchGroupBrowserPoolMember.id.asc(),
                )
            )
        ).all()
    )
    members_by_group: dict[int, list[CtripCompetitorWatchGroupBrowserPoolMember]] = {}
    for member in members:
        members_by_group.setdefault(member.group_id, []).append(member)
    return {
        group.id: _browser_pool_summary_from_members(
            group,
            members_by_group.get(group.id, []),
        )
        for group in groups
        if group.id is not None
    }


async def _browser_pool_summary_for_group(
    session,
    group: CtripCompetitorWatchGroup,
    *,
    include_disabled: bool = True,
) -> dict[str, Any]:
    if group.id is None:
        return _browser_pool_summary_from_members(group, [])
    statement = (
        select(CtripCompetitorWatchGroupBrowserPoolMember)
        .where(CtripCompetitorWatchGroupBrowserPoolMember.group_id == group.id)
        .order_by(
            CtripCompetitorWatchGroupBrowserPoolMember.sort_order.asc(),
            CtripCompetitorWatchGroupBrowserPoolMember.id.asc(),
        )
    )
    if not include_disabled:
        statement = statement.where(
            CtripCompetitorWatchGroupBrowserPoolMember.enabled.is_(True)
        )
    members = list((await session.scalars(statement)).all())
    return _browser_pool_summary_from_members(group, members)


def _browser_pool_summary_from_members(
    group: CtripCompetitorWatchGroup,
    members: list[CtripCompetitorWatchGroupBrowserPoolMember],
) -> dict[str, Any]:
    now = local_now()
    rows = [_browser_pool_member_to_dict(member, now=now) for member in members]
    return {
        "pool_enabled": _normalize_browser_rotation_mode(group.browser_rotation_mode)
        == "pool_round_robin",
        "rotation_mode": _normalize_browser_rotation_mode(group.browser_rotation_mode),
        "pool_size": len(rows),
        "enabled_pool_size": sum(1 for row in rows if row["enabled"]),
        "usable_pool_size": sum(1 for row in rows if row["usable"]),
        "failover_enabled": bool(group.pool_failover_enabled),
        "cooldown_minutes": _bounded_int(
            group.pool_cooldown_minutes,
            default=DEFAULT_POOL_COOLDOWN_MINUTES,
            minimum=1,
            maximum=1440,
        ),
        "members": rows,
    }


def _browser_pool_member_to_dict(
    member: CtripCompetitorWatchGroupBrowserPoolMember,
    *,
    now: datetime,
) -> dict[str, Any]:
    browser_session = _browser_session_by_id_or_account(
        browser_session_id=member.browser_session_id,
        platform_account_id=None,
    )
    intended_use = getattr(browser_session, "intended_use", None)
    login_state = getattr(browser_session, "login_state", None)
    human_verification_status = getattr(browser_session, "human_verification_status", None)
    cooldown_active = bool(member.cooldown_until and member.cooldown_until > now)
    usable = bool(
        member.enabled
        and intended_use == "competitor_price"
        and login_state == "logged_in"
        and human_verification_status in {None, "", "none"}
        and not cooldown_active
    )
    return {
        "id": member.id,
        "browser_session_id": member.browser_session_id,
        "platform_account_id": getattr(browser_session, "platform_account_id", None),
        "display_name": getattr(browser_session, "display_name", None),
        "intended_use": intended_use,
        "login_state": login_state,
        "human_verification_status": human_verification_status,
        "enabled": bool(member.enabled),
        "sort_order": member.sort_order,
        "weight": member.weight,
        "last_selected_at": (
            member.last_selected_at.isoformat() if member.last_selected_at else None
        ),
        "last_success_at": (
            member.last_success_at.isoformat() if member.last_success_at else None
        ),
        "last_failure_at": (
            member.last_failure_at.isoformat() if member.last_failure_at else None
        ),
        "last_status": member.last_status,
        "cooldown_until": (
            member.cooldown_until.isoformat() if member.cooldown_until else None
        ),
        "cooldown_active": cooldown_active,
        "consecutive_failures": member.consecutive_failures,
        "usable": usable,
    }


def _watch_group_to_dict(
    group: CtripCompetitorWatchGroup,
    *,
    include_secrets: bool = False,
    browser_pool: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pricing_account = _browser_session_by_id_or_account(
        browser_session_id=group.pricing_browser_session_id,
        platform_account_id=group.pricing_platform_account_id,
    )
    if getattr(pricing_account, "intended_use", None) != "ebk":
        pricing_account = None
    pricing_policy = _pricing_account_policy(pricing_account)
    payload = {
        "id": group.id,
        "group_key": group.group_key,
        "name": group.name,
        "brief_title": group.brief_title,
        "price_change_alert_title": group.price_change_alert_title,
        "shop_key": group.shop_key,
        "platform_account_id": group.platform_account_id,
        "browser_session_id": group.browser_session_id,
        "browser_rotation_mode": _normalize_browser_rotation_mode(
            group.browser_rotation_mode
        ),
        "pool_failover_enabled": bool(group.pool_failover_enabled),
        "pool_cooldown_minutes": _bounded_int(
            group.pool_cooldown_minutes,
            default=DEFAULT_POOL_COOLDOWN_MINUTES,
            minimum=1,
            maximum=1440,
        ),
        "browser_pool": browser_pool or _browser_pool_summary_from_members(group, []),
        "pricing_platform_account_id": group.pricing_platform_account_id,
        "pricing_browser_session_id": group.pricing_browser_session_id,
        "pricing_account_configured": bool(pricing_account),
        "pricing_connection_mode": pricing_policy["connection_mode"],
        "pricing_can_auto_write_price": pricing_policy["can_auto_write_price"],
        "pricing_write_policy_message": pricing_policy["write_policy_message"],
        "feishu_webhook_configured": bool(group.feishu_webhook_url),
        "feishu_webhook_masked": _mask_secret(group.feishu_webhook_url),
        "feishu_mobile_webhook_configured": bool(group.feishu_mobile_webhook_url),
        "feishu_mobile_webhook_masked": _mask_secret(group.feishu_mobile_webhook_url),
        "feishu_desktop_webhook_configured": bool(group.feishu_desktop_webhook_url),
        "feishu_desktop_webhook_masked": _mask_secret(group.feishu_desktop_webhook_url),
        "feishu_chat_id_configured": bool(group.feishu_chat_id),
        "feishu_chat_id_masked": _mask_secret(group.feishu_chat_id),
        "feishu_card_layout": _normalize_feishu_card_layout(group.feishu_card_layout),
        "push_date_scope": _normalize_push_date_scope(group.push_date_scope),
        "timezone": group.timezone,
        "retention_days": group.retention_days,
        "query_interval_minutes": group.query_interval_minutes,
        "schedule_mode": _normalize_schedule_mode(group.schedule_mode),
        "schedule_start_time": _normalize_time_of_day(
            group.schedule_start_time,
            default=DEFAULT_SCHEDULE_START_TIME,
        ),
        "schedule_end_time": _normalize_time_of_day(
            group.schedule_end_time,
            default=DEFAULT_SCHEDULE_END_TIME,
        ),
        "schedule_interval_minutes": _bounded_int(
            group.schedule_interval_minutes,
            default=group.query_interval_minutes or DEFAULT_SCHEDULE_INTERVAL_MINUTES,
            minimum=1,
            maximum=1440,
        ),
        "schedule_state": competitor_watch_schedule_state(group),
        "auto_poll_enabled": group.auto_poll_enabled,
        "enabled": group.enabled,
        "created_at": group.created_at.isoformat(),
        "updated_at": group.updated_at.isoformat(),
        "last_polled_at": group.last_polled_at.isoformat() if group.last_polled_at else None,
    }
    if include_secrets:
        payload["feishu_webhook_url"] = group.feishu_webhook_url
        payload["feishu_mobile_webhook_url"] = group.feishu_mobile_webhook_url
        payload["feishu_desktop_webhook_url"] = group.feishu_desktop_webhook_url
        payload["feishu_chat_id"] = group.feishu_chat_id
    return payload


async def _ensure_unique_enabled_browser_session_binding(
    session,
    group: CtripCompetitorWatchGroup,
) -> None:
    # Multiple enabled watch groups may share one competitor_price browser session.
    # PublicBrowserPollGate serializes reads on the same profile key.
    return


async def _ensure_pool_ready_for_auto_poll(
    session,
    group: CtripCompetitorWatchGroup,
) -> None:
    if not group.auto_poll_enabled:
        return
    if _normalize_browser_rotation_mode(group.browser_rotation_mode) != "pool_round_robin":
        return
    enabled_count = 0
    if group.id is not None:
        enabled_count = int(
            await session.scalar(
                select(func.count(CtripCompetitorWatchGroupBrowserPoolMember.id)).where(
                    CtripCompetitorWatchGroupBrowserPoolMember.group_id == group.id,
                    CtripCompetitorWatchGroupBrowserPoolMember.enabled.is_(True),
                )
            )
            or 0
        )
    if enabled_count <= 0:
        raise ValueError(
            "pool_round_robin auto polling requires at least one enabled "
            "competitor_price browser account."
        )


def _latest_snapshot_to_dict(
    run: CtripCompetitorPriceRun,
    rows: list[tuple[CtripCompetitorHotel, CtripCompetitorHotelSnapshot, CtripCompetitorRoomRate]],
    watch_group_key: str,
) -> dict[str, Any]:
    hotels: dict[int, dict[str, Any]] = {}
    for hotel, snapshot, rate in rows:
        hotel_payload = hotels.setdefault(
            hotel.id,
            {"hotel_config": _hotel_to_dict(hotel), "snapshots": {}},
        )
        snapshot_payload = hotel_payload["snapshots"].setdefault(
            snapshot.date_label,
            {
                "snapshot_id": snapshot.id,
                "checkin_date": snapshot.checkin_date.isoformat() if snapshot.checkin_date else None,
                "checkout_date": snapshot.checkout_date.isoformat() if snapshot.checkout_date else None,
                "status": snapshot.snapshot_status,
                "review_reason": snapshot.review_reason,
                "page_state": snapshot.page_state,
                "rates": [],
            },
        )
        if rate is not None:
            snapshot_payload["rates"].append(
                {
                    "room_type": rate.room_type,
                    "breakfast_text": rate.breakfast_text,
                    "breakfast_kind": rate.breakfast_kind,
                    "price_amount_yuan": rate.price_amount_yuan,
                    "discount_amount_yuan": rate.discount_amount_yuan,
                    "discount_display": rate.discount_display,
                    "availability_text": rate.availability_text,
                    "availability_status": rate.availability_status,
                    "is_trusted": rate.is_trusted,
                    "source": rate.source,
                }
            )
    return {
        "watch_group_key": watch_group_key,
        "run": {
            "id": run.id,
            "status": run.status,
            "summary": run.summary,
            "captured_at": run.captured_at.isoformat(),
            "parser_version": run.parser_version,
            "push_result": run.push_result_json,
        },
        "hotels": list(hotels.values()),
        "trusted_source": "room_rate_dom_display_price",
    }


def _trend_rows_to_dict(
    *,
    rows: list[Any],
    watch_group_key: str,
    days: int,
    granularity: Literal["run", "hour", "day"],
) -> dict[str, Any]:
    buckets: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        bucket_at = _bucket_captured_at(row.captured_at, granularity)
        series_id = (
            f"{row.hotel_config_id}:{row.date_label}:{row.breakfast_kind}"
        )
        bucket_key = (
            bucket_at,
            row.run_id if granularity == "run" else None,
            row.hotel_config_id,
            row.date_label,
            row.breakfast_kind,
        )
        current = buckets.get(bucket_key)
        if current is None or row.captured_at > current["captured_at"]:
            current = {
                "series_id": series_id,
                "bucket_at": bucket_at,
                "captured_at": row.captured_at,
                "run_id": row.run_id,
                "hotel_config_id": row.hotel_config_id,
                "hotel_role": row.hotel_role,
                "sort_order": row.sort_order,
                "hotel_alias": row.hotel_alias,
                "hotel_name": row.hotel_alias or row.snapshot_hotel_name,
                "ctrip_hotel_id": row.ctrip_hotel_id,
                "date_label": row.date_label,
                "checkin_date": row.checkin_date,
                "checkout_date": row.checkout_date,
                "breakfast_kind": row.breakfast_kind,
                "breakfast_text": row.breakfast_text,
                "room_type": row.room_type,
                "min_price_amount_yuan": row.price_amount_yuan,
                "max_price_amount_yuan": row.price_amount_yuan,
                "trusted_rate_count": 0,
                "_room_types": set(),
                "_availability_statuses": set(),
                "_availability_texts": set(),
            }
            buckets[bucket_key] = current
        if row.captured_at == current["captured_at"]:
            current["min_price_amount_yuan"] = min(
                current["min_price_amount_yuan"],
                row.price_amount_yuan,
            )
            current["max_price_amount_yuan"] = max(
                current["max_price_amount_yuan"],
                row.price_amount_yuan,
            )
            current["trusted_rate_count"] += 1
            if row.room_type:
                current["_room_types"].add(row.room_type)
            if row.availability_status:
                current["_availability_statuses"].add(row.availability_status)
            if row.availability_text:
                current["_availability_texts"].add(row.availability_text)
    points = [_finalize_trend_point(point) for point in buckets.values()]
    points.sort(
        key=lambda point: (
            point["hotel_role"] != "own",
            point["sort_order"],
            point["date_label"],
            point["breakfast_kind"],
            point["bucket_at"],
        )
    )
    return {
        "status": "success",
        "summary": f"Built {len(points)} trusted competitor price trend point(s).",
        "watch_group_key": watch_group_key,
        "granularity": granularity,
        "days": days,
        "generated_at": local_now().isoformat(),
        "point_count": len(points),
        "trusted_source": "room_rate_dom_display_price",
        "series": _trend_series_from_points(points),
        "points": points,
    }


def _finalize_trend_point(point: dict[str, Any]) -> dict[str, Any]:
    finalized = {
        key: value
        for key, value in point.items()
        if not key.startswith("_") and key not in {"bucket_at", "captured_at"}
    }
    finalized["bucket_at"] = point["bucket_at"].isoformat()
    finalized["captured_at"] = point["captured_at"].isoformat()
    finalized["captured_date"] = point["bucket_at"].date().isoformat()
    finalized["checkin_date"] = (
        point["checkin_date"].isoformat() if point["checkin_date"] else None
    )
    finalized["checkout_date"] = (
        point["checkout_date"].isoformat() if point["checkout_date"] else None
    )
    finalized["room_type_count"] = len(point["_room_types"])
    finalized["availability_statuses"] = sorted(point["_availability_statuses"])
    finalized["availability_texts"] = sorted(point["_availability_texts"])[:5]
    return finalized


def _trend_series_from_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    series_by_id: dict[str, dict[str, Any]] = {}
    for point in points:
        room_type = point.get("room_type")
        series = series_by_id.setdefault(
            point["series_id"],
            {
                "series_id": point["series_id"],
                "hotel_config_id": point["hotel_config_id"],
                "hotel_role": point["hotel_role"],
                "sort_order": point["sort_order"],
                "hotel_alias": point["hotel_alias"],
                "hotel_name": point["hotel_name"],
                "ctrip_hotel_id": point["ctrip_hotel_id"],
                "date_label": point["date_label"],
                "checkin_date": point["checkin_date"],
                "checkout_date": point["checkout_date"],
                "breakfast_kind": point["breakfast_kind"],
                "room_type": room_type,
                "room_types": [],
                "room_type_changed": False,
                "points": [],
            },
        )
        if room_type and room_type not in series["room_types"]:
            series["room_types"].append(room_type)
        if len(series["room_types"]) > 1:
            series["room_type"] = "房型有变化"
            series["room_type_changed"] = True
        series["points"].append(
            {
                "bucket_at": point["bucket_at"],
                "captured_at": point["captured_at"],
                "run_id": point["run_id"],
                "price_amount_yuan": point["min_price_amount_yuan"],
                "room_type": point.get("room_type"),
                "availability_statuses": point["availability_statuses"],
            }
        )
    return sorted(
        series_by_id.values(),
        key=lambda item: (
            item["hotel_role"] != "own",
            item["sort_order"],
            item["date_label"],
            item["breakfast_kind"],
        ),
    )


def _bucket_captured_at(
    captured_at: datetime,
    granularity: Literal["run", "hour", "day"],
) -> datetime:
    captured_at = captured_at.replace(microsecond=0)
    if granularity == "day":
        return captured_at.replace(hour=0, minute=0, second=0)
    if granularity == "hour":
        return captured_at.replace(minute=0, second=0)
    return captured_at


def _runtime_config_status(enabled_own_count: int, enabled_competitor_count: int) -> str:
    if enabled_own_count == 1 and enabled_competitor_count >= 1:
        return "ready"
    return "config_required"


def _run_summary_to_dict(run: CtripCompetitorPriceRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "summary": run.summary,
        "captured_at": run.captured_at.isoformat(),
        "source": run.source,
    }


def _group_is_due(group: CtripCompetitorWatchGroup, now: datetime) -> bool:
    return watch_group_is_due(group, now)


def _rowcount(result: Any) -> int:
    return int(result.rowcount or 0)


def _default_platform_account_id() -> str:
    settings = get_settings()
    return (
        settings.ctrip_ebooking_default_platform_account_id
        or settings.feishu_default_platform_account_id
        or "__unconfigured__"
    )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _normalize_feishu_card_layout(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"mobile_blocks", "desktop_table"}:
        return text
    return DEFAULT_FEISHU_CARD_LAYOUT


def _normalize_push_date_scope(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"today_only", "today_tomorrow"}:
        return text
    return DEFAULT_PUSH_DATE_SCOPE


def _normalize_schedule_mode(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"fixed_slots", "last_run_interval"}:
        return text
    return DEFAULT_SCHEDULE_MODE


def _normalize_browser_rotation_mode(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"fixed", "pool_round_robin"}:
        return text
    return DEFAULT_BROWSER_ROTATION_MODE


def _normalize_time_of_day(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"^([01]\d|2[0-3]):[0-5]\d$", text):
        return text
    return default


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _resolve_pricing_binding(
    *,
    browser_session_id: str | None,
    platform_account_id: str | None,
) -> tuple[str | None, str | None]:
    browser_session_id = _clean_text(browser_session_id)
    platform_account_id = _clean_text(platform_account_id)
    if browser_session_id:
        browser_session = _validate_pricing_browser_session(browser_session_id)
        return browser_session.platform_account_id, browser_session.browser_session_id
    if platform_account_id:
        browser_session = _browser_session_by_id_or_account(
            browser_session_id=None,
            platform_account_id=platform_account_id,
        )
        if browser_session is None:
            raise ValueError(f"调价 EBK 账号不存在：{platform_account_id}")
        browser_session = _validate_pricing_browser_session(browser_session)
        return browser_session.platform_account_id, browser_session.browser_session_id
    return None, None


def _pricing_account_policy(account: Any | None) -> dict[str, Any]:
    from app.domain.browser.sessions import account_write_policy

    return account_write_policy(account)




def _mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}***{secret[-4:]}"


def _validate_browser_session(browser_session_id):
    from app.domain.browser.sessions import BrowserSession, get_browser_session_store

    if isinstance(browser_session_id, BrowserSession) or (
        hasattr(browser_session_id, "browser_session_id")
        and hasattr(browser_session_id, "platform")
        and hasattr(browser_session_id, "intended_use")
    ):
        session = browser_session_id
    else:
        try:
            session = get_browser_session_store().get(str(browser_session_id))
        except KeyError as exc:
            raise ValueError(f"浏览器账号不存在：{browser_session_id}") from exc
    if session.platform != "ctrip":
        raise ValueError("竞品监控组只能绑定携程浏览器账号。")
    if session.intended_use != "competitor_price":
        raise ValueError("竞品监控组只能绑定用途为竞品价格的浏览器账号。")
    return session


def _validate_pricing_browser_session(browser_session_id):
    from app.domain.browser.sessions import BrowserSession, get_browser_session_store

    if isinstance(browser_session_id, BrowserSession) or (
        hasattr(browser_session_id, "browser_session_id")
        and hasattr(browser_session_id, "platform")
        and hasattr(browser_session_id, "intended_use")
    ):
        session = browser_session_id
    else:
        try:
            session = get_browser_session_store().get(str(browser_session_id))
        except KeyError as exc:
            raise ValueError(f"调价 EBK 账号不存在：{browser_session_id}") from exc
    if session.platform != "ctrip":
        raise ValueError("调价 EBK 账号只能绑定携程浏览器账号。")
    if session.intended_use != "ebk":
        raise ValueError("调价 EBK 账号只能绑定用途为 EBK 后台的浏览器账号。")
    return session


def _browser_session_by_id_or_account(
    *,
    browser_session_id: str | None,
    platform_account_id: str | None,
):
    from app.domain.browser.sessions import get_browser_session_for_platform_account, get_browser_session_store

    if browser_session_id:
        try:
            return get_browser_session_store().get(browser_session_id)
        except KeyError:
            return None
    if platform_account_id:
        return get_browser_session_for_platform_account(platform_account_id)
    return None
