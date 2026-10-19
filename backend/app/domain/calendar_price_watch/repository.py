from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select

from app.db.session import ensure_db_ready, session_scope
from app.domain.calendar_price_watch.models import (
    CalendarPriceAlert,
    CalendarPricePoint,
    CalendarPriceRun,
    CalendarPriceWatchConfig,
    local_now,
)
from app.domain.calendar_price_watch.types import (
    CalendarPriceWatchConfigPatch,
    DEFAULT_CALENDAR_FUTURE_DAYS,
    DEFAULT_HIGH_GAP_THRESHOLD_YUAN,
    DEFAULT_LOW_GAP_THRESHOLD_YUAN,
    DEFAULT_MATRIX_BRIEF_EVENING_PUSH_TIME,
    DEFAULT_MATRIX_BRIEF_MORNING_PUSH_TIME,
    DEFAULT_MATRIX_BRIEF_PUSH_TIMES,
    MAX_CALENDAR_FUTURE_DAYS,
    TIME_OF_DAY_PATTERN,
)
from app.domain.competitor_prices.models import CtripCompetitorWatchGroup
from app.domain.competitor_prices.repository import list_competitor_hotels
from app.domain.competitor_prices.schedule import competitor_watch_schedule_state
from app.domain.competitor_prices.browser_binding import validate_competitor_browser_session_config
from app.domain.competitor_prices.types import (
    DEFAULT_SCHEDULE_INTERVAL_MINUTES,
)

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
TRUSTED_CALENDAR_SOURCE = "calendar_dom_cell_price"
_SECRET_KEYS = frozenset(
    {
        "webhook",
        "webhook_url",
        "feishu_webhook_url",
        "feishu_mobile_webhook_url",
        "feishu_desktop_webhook_url",
        "screenshot_path",
        "profile_path",
        "cookie",
        "password",
        "verification_code",
    }
)


async def list_calendar_price_watch_config_summaries() -> list[dict[str, Any]]:
    """全部竞品组的日历价监控配置摘要（无配置的组按默认关闭补齐）。"""

    await ensure_db_ready()
    async with session_scope() as session:
        groups = (
            (
                await session.execute(
                    select(CtripCompetitorWatchGroup).order_by(
                        CtripCompetitorWatchGroup.id
                    )
                )
            )
            .scalars()
            .all()
        )
        configs = (
            (
                await session.execute(
                    select(CalendarPriceWatchConfig).where(
                        CalendarPriceWatchConfig.watch_group_id.in_(
                            [group.id for group in groups]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    config_by_group = {config.watch_group_id: config for config in configs}
    summaries: list[dict[str, Any]] = []
    for group in groups:
        config = config_by_group.get(group.id)
        summaries.append(
            {
                "watch_group_key": group.group_key,
                "group_name": group.name,
                "group_enabled": bool(group.enabled),
                "auto_poll_enabled": bool(config.auto_poll_enabled) if config else False,
                "matrix_brief_enabled": (
                    bool(config.matrix_brief_enabled) if config else False
                ),
                "last_polled_at": (
                    config.last_polled_at.isoformat() if config and config.last_polled_at else None
                ),
                "config_present": config is not None,
            }
        )
    return summaries


async def get_calendar_price_watch_config(*, watch_group_key: str) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        config = await _get_config_for_group(session, group.id)
        if config is None:
            config = CalendarPriceWatchConfig(watch_group_id=group.id)
            session.add(config)
            await session.flush()
        return _config_to_dict(group, config)


async def update_calendar_price_watch_config(
    *,
    watch_group_key: str,
    patch: CalendarPriceWatchConfigPatch,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    current_time = (now or local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        config = await _get_config_for_group(session, group.id)
        if config is None:
            config = CalendarPriceWatchConfig(watch_group_id=group.id)
            session.add(config)
            await session.flush()
        if patch.future_days is not None:
            config.future_days = max(1, min(int(patch.future_days), MAX_CALENDAR_FUTURE_DAYS))
        if patch.high_gap_threshold_yuan is not None:
            config.high_gap_threshold_yuan = int(patch.high_gap_threshold_yuan)
        if patch.low_gap_threshold_yuan is not None:
            config.low_gap_threshold_yuan = int(patch.low_gap_threshold_yuan)
        if patch.auto_poll_enabled is not None:
            config.auto_poll_enabled = bool(patch.auto_poll_enabled)
        if patch.schedule_interval_minutes is not None:
            config.schedule_interval_minutes = max(1, min(int(patch.schedule_interval_minutes), 1440))
        if patch.alert_title is not None:
            config.alert_title = patch.alert_title.strip() or None
        was_enabled = bool(config.matrix_brief_enabled)
        previous_push_times = set(_resolve_matrix_brief_push_times(config))
        if (
            patch.matrix_brief_push_times is not None
            or patch.matrix_brief_morning_push_time is not None
            or patch.matrix_brief_evening_push_time is not None
        ):
            _apply_matrix_brief_push_times_patch(config, patch)
        if patch.matrix_brief_title is not None:
            config.matrix_brief_title = patch.matrix_brief_title.strip() or None
        if patch.matrix_brief_enabled is not None:
            config.matrix_brief_enabled = bool(patch.matrix_brief_enabled)
        if config.matrix_brief_enabled:
            enabling = patch.matrix_brief_enabled is not None and not was_enabled
            added_times = set(_resolve_matrix_brief_push_times(config)) - previous_push_times
            if enabling:
                _seed_past_matrix_brief_slots_on_enable(config, now=current_time)
            elif added_times:
                _seed_past_matrix_brief_slots_on_enable(
                    config,
                    now=current_time,
                    only_slots=added_times,
                )
        if patch.browser_session_id is not None:
            browser_session_id = patch.browser_session_id.strip()
            if browser_session_id:
                browser_session = validate_competitor_browser_session_config(browser_session_id)
                config.browser_session_id = browser_session.browser_session_id
                config.platform_account_id = browser_session.platform_account_id
            else:
                config.browser_session_id = None
                config.platform_account_id = None
        config.updated_at = local_now()
        await session.flush()
        return _config_to_dict(group, config, now=current_time)


async def save_calendar_price_run(
    *,
    watch_group_key: str,
    config: dict[str, Any],
    result: dict[str, Any],
    actor_id: str,
    source: str,
) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            raise ValueError(f"watch group not found: {watch_group_key}")
        db_config = await _get_config_for_group(session, group.id)
        if db_config is None:
            db_config = CalendarPriceWatchConfig(watch_group_id=group.id)
            session.add(db_config)
            await session.flush()

        evidence = _sanitize_payload(result.get("evidence") or {})
        if result.get("start_date"):
            evidence["start_date"] = str(result.get("start_date"))
        run = CalendarPriceRun(
            watch_group_id=group.id,
            config_id=db_config.id,
            platform_account_id=str(result.get("platform_account_id") or ""),
            browser_session_id=_clean_text(result.get("browser_session_id")),
            actor_id=actor_id,
            source=source,
            status=str(result.get("status") or "needs_review"),
            summary=_clean_text(result.get("summary")),
            future_days=int(config.get("future_days") or DEFAULT_CALENDAR_FUTURE_DAYS),
            high_gap_threshold_yuan=int(
                config.get("high_gap_threshold_yuan") or DEFAULT_HIGH_GAP_THRESHOLD_YUAN
            ),
            low_gap_threshold_yuan=int(
                config.get("low_gap_threshold_yuan") or DEFAULT_LOW_GAP_THRESHOLD_YUAN
            ),
            evidence_json=evidence,
            push_result_json=_sanitize_payload(result.get("push_result")),
        )
        session.add(run)
        await session.flush()

        for point in result.get("points") or []:
            if not isinstance(point, dict):
                continue
            checkin = _parse_date(point.get("checkin_date"))
            if checkin is None:
                continue
            session.add(
                CalendarPricePoint(
                    run_id=run.id,
                    watch_group_id=group.id,
                    hotel_config_id=int(point["hotel_config_id"]),
                    role=str(point.get("role") or "competitor"),
                    hotel_alias=str(point.get("hotel_alias") or ""),
                    checkin_date=checkin,
                    price_amount_yuan=_int_or_none(point.get("price_amount_yuan")),
                    currency=str(point.get("currency") or "CNY"),
                    source=str(point.get("source") or TRUSTED_CALENDAR_SOURCE),
                    is_trusted=bool(point.get("is_trusted")),
                    status=str(point.get("status") or result.get("status") or "needs_review"),
                    review_reason=_clean_text(point.get("review_reason")),
                    source_text=_clean_text(point.get("source_text")),
                )
            )

        for alert in result.get("alerts") or []:
            if not isinstance(alert, dict):
                continue
            checkin = _parse_date(alert.get("checkin_date"))
            if checkin is None:
                continue
            session.add(
                CalendarPriceAlert(
                    run_id=run.id,
                    watch_group_id=group.id,
                    alert_type=str(alert.get("alert_type") or "unknown"),
                    checkin_date=checkin,
                    own_price_yuan=int(alert.get("own_price_yuan") or 0),
                    reference_price_yuan=int(alert.get("reference_price_yuan") or 0),
                    gap_yuan=int(alert.get("gap_yuan") or 0),
                    reference_hotel_alias=_clean_text(alert.get("reference_hotel_alias")),
                    summary=str(alert.get("summary") or ""),
                )
            )

        db_config.last_polled_at = run.captured_at
        await session.flush()
        return _sanitize_payload(
            {
                "run_id": run.id,
                "watch_group_key": watch_group_key,
                "status": run.status,
                "summary": run.summary,
                "captured_at": run.captured_at.isoformat(),
                "point_count": len(result.get("points") or []),
                "alert_count": len(result.get("alerts") or []),
            }
        )


async def get_latest_calendar_price_matrix(*, watch_group_key: str) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        config = await _get_config_for_group(session, group.id)
        if config is None:
            return None
        run = await session.scalar(
            select(CalendarPriceRun)
            .where(CalendarPriceRun.watch_group_id == group.id)
            .order_by(desc(CalendarPriceRun.captured_at), desc(CalendarPriceRun.id))
            .limit(1)
        )
        if run is None:
            return None
        points = list(
            (
                await session.scalars(
                    select(CalendarPricePoint)
                    .where(CalendarPricePoint.run_id == run.id)
                    .order_by(CalendarPricePoint.checkin_date.asc(), CalendarPricePoint.id.asc())
                )
            ).all()
        )
        alerts = list(
            (
                await session.scalars(
                    select(CalendarPriceAlert)
                    .where(CalendarPriceAlert.run_id == run.id)
                    .order_by(CalendarPriceAlert.checkin_date.asc(), CalendarPriceAlert.id.asc())
                )
            ).all()
        )
    hotels = await list_competitor_hotels(watch_group_key=watch_group_key, include_disabled=False)
    own_hotel = next((item for item in hotels if item["role"] == "own"), None)
    own_alias = str((own_hotel or {}).get("alias") or "本店").strip() or "本店"
    own_short_alias = str((own_hotel or {}).get("short_alias") or "").strip() or None
    competitor_hotels = [
        {
            "alias": str(item.get("alias") or "").strip(),
            "short_alias": str(item.get("short_alias") or "").strip() or None,
        }
        for item in hotels
        if item.get("role") != "own" and str(item.get("alias") or "").strip()
    ]
    start_date = _parse_date_from_result(run) or _shanghai_today()
    future_days = int(run.future_days or config.future_days or DEFAULT_CALENDAR_FUTURE_DAYS)
    return _sanitize_payload(
        {
            "status": run.status,
            "watch_group_key": watch_group_key,
            "future_days": future_days,
            "trusted_source": TRUSTED_CALENDAR_SOURCE,
            "own_short_alias": own_short_alias,
            "competitor_hotels": competitor_hotels,
            "run": {
                "id": run.id,
                "status": run.status,
                "captured_at": run.captured_at.isoformat(),
            },
            "dates": _build_matrix_dates(
                start_date=start_date,
                future_days=future_days,
                points=points,
                alerts=alerts,
                own_alias=own_alias,
            ),
        }
    )


async def list_calendar_price_alerts(
    *,
    watch_group_key: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    bounded_limit = max(1, min(int(limit or 20), 100))
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return []
        alerts = list(
            (
                await session.scalars(
                    select(CalendarPriceAlert)
                    .where(CalendarPriceAlert.watch_group_id == group.id)
                    .order_by(desc(CalendarPriceAlert.created_at), desc(CalendarPriceAlert.id))
                    .limit(bounded_limit)
                )
            ).all()
        )
    return [_alert_to_dict(alert) for alert in alerts]


async def update_calendar_price_run_push_result(
    *,
    run_id: int,
    push_result: dict[str, Any],
) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        run = await session.get(CalendarPriceRun, run_id)
        if run is None:
            return
        run.push_result_json = _sanitize_payload(push_result)


async def list_due_calendar_price_watch_configs(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    current_time = (now or local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(CalendarPriceWatchConfig, CtripCompetitorWatchGroup)
                    .join(
                        CtripCompetitorWatchGroup,
                        CalendarPriceWatchConfig.watch_group_id == CtripCompetitorWatchGroup.id,
                    )
                    .where(
                        CalendarPriceWatchConfig.auto_poll_enabled.is_(True),
                        CtripCompetitorWatchGroup.enabled.is_(True),
                    )
                    .order_by(CtripCompetitorWatchGroup.group_key.asc())
                )
            ).all()
        )
    due: list[dict[str, Any]] = []
    for config, group in rows:
        if _calendar_config_is_due(config, group, current_time):
            due.append(_config_to_dict(group, config, now=current_time))
    return due


async def list_due_matrix_brief_configs(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    current_time = (now or local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(CalendarPriceWatchConfig, CtripCompetitorWatchGroup)
                    .join(
                        CtripCompetitorWatchGroup,
                        CalendarPriceWatchConfig.watch_group_id == CtripCompetitorWatchGroup.id,
                    )
                    .where(
                        CalendarPriceWatchConfig.matrix_brief_enabled.is_(True),
                        CtripCompetitorWatchGroup.enabled.is_(True),
                    )
                    .order_by(CtripCompetitorWatchGroup.group_key.asc())
                )
            ).all()
        )
    due: list[dict[str, Any]] = []
    for config, group in rows:
        payload = _config_to_dict(group, config, now=current_time)
        for slot_time in payload.get("matrix_brief_push_times") or []:
            normalized_slot = _normalize_matrix_brief_push_time(slot_time)
            if _matrix_brief_slot_is_due(
                normalized_slot,
                config.matrix_brief_last_pushed_json,
                current_time,
            ):
                due.append(
                    {
                        **payload,
                        "due_slot": normalized_slot,
                        "due_slot_time": normalized_slot,
                    }
                )
    return due


async def mark_matrix_brief_slot_pushed(
    *,
    watch_group_key: str,
    slot_time: str,
    pushed_at: datetime | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    normalized_slot = _normalize_matrix_brief_push_time(slot_time)
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        config = await _get_config_for_group(session, group.id)
        if config is None:
            return None
        pushed_json = (
            dict(config.matrix_brief_last_pushed_json)
            if isinstance(config.matrix_brief_last_pushed_json, dict)
            else {}
        )
        pushed_json[normalized_slot] = (pushed_at or local_now()).replace(tzinfo=None).isoformat()
        config.matrix_brief_last_pushed_json = pushed_json
        config.updated_at = local_now()
        await session.flush()
        return _config_to_dict(group, config)


async def mark_calendar_price_watch_polled(
    *,
    watch_group_key: str,
    captured_at: datetime | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_watch_group(session, watch_group_key)
        if group is None:
            return None
        config = await _get_config_for_group(session, group.id)
        if config is None:
            return None
        config.last_polled_at = (captured_at or local_now()).replace(tzinfo=None)
        await session.flush()
        return _config_to_dict(group, config)


def _build_matrix_dates(
    *,
    start_date: date,
    future_days: int,
    points: list[CalendarPricePoint],
    alerts: list[CalendarPriceAlert],
    own_alias: str,
) -> list[dict[str, Any]]:
    points_by_date: dict[str, list[CalendarPricePoint]] = {}
    for point in points:
        points_by_date.setdefault(point.checkin_date.isoformat(), []).append(point)
    alerts_by_date: dict[str, list[dict[str, Any]]] = {}
    for alert in alerts:
        alerts_by_date.setdefault(alert.checkin_date.isoformat(), []).append(_alert_to_dict(alert))

    dates: list[dict[str, Any]] = []
    for offset in range(max(1, min(future_days, MAX_CALENDAR_FUTURE_DAYS))):
        checkin_date = (start_date + timedelta(days=offset)).isoformat()
        date_points = points_by_date.get(checkin_date, [])
        own_point = next((item for item in date_points if item.role == "own"), None)
        competitor_points = [item for item in date_points if item.role != "own"]
        if own_point is None:
            own_payload = {
                "hotel_alias": own_alias,
                "price_amount_yuan": None,
                "status": "needs_review",
            }
        else:
            own_payload = {
                "hotel_alias": own_point.hotel_alias,
                "price_amount_yuan": own_point.price_amount_yuan,
                "status": own_point.status,
            }
        dates.append(
            {
                "checkin_date": checkin_date,
                "own": own_payload,
                "competitors": [
                    {
                        "hotel_alias": item.hotel_alias,
                        "price_amount_yuan": item.price_amount_yuan,
                        "status": item.status,
                    }
                    for item in competitor_points
                ],
                "alerts": alerts_by_date.get(checkin_date, []),
            }
        )
    return dates


def _calendar_config_is_due(
    config: CalendarPriceWatchConfig,
    group: CtripCompetitorWatchGroup,
    now: datetime,
) -> bool:
    schedule_state = _calendar_schedule_state(group, config, now=now)
    return bool(schedule_state.get("due"))


def _config_to_dict(
    group: CtripCompetitorWatchGroup,
    config: CalendarPriceWatchConfig,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    schedule_state = _calendar_schedule_state(group, config, now=now)
    return {
        "watch_group_key": group.group_key,
        "future_days": int(config.future_days or DEFAULT_CALENDAR_FUTURE_DAYS),
        "high_gap_threshold_yuan": int(
            config.high_gap_threshold_yuan or DEFAULT_HIGH_GAP_THRESHOLD_YUAN
        ),
        "low_gap_threshold_yuan": int(
            config.low_gap_threshold_yuan or DEFAULT_LOW_GAP_THRESHOLD_YUAN
        ),
        "auto_poll_enabled": bool(config.auto_poll_enabled),
        "schedule_interval_minutes": int(
            schedule_state.get("interval_minutes") or DEFAULT_SCHEDULE_INTERVAL_MINUTES
        ),
        "schedule_mode": str(schedule_state.get("mode") or group.schedule_mode),
        "schedule_start_time": str(schedule_state.get("start_time") or group.schedule_start_time),
        "schedule_end_time": str(schedule_state.get("end_time") or group.schedule_end_time),
        "schedule_source": "competitor_watch_group",
        "schedule_state": schedule_state,
        "browser_binding_source": "calendar_price_watch_config",
        "browser_session_id": config.browser_session_id,
        "browser_session_configured": bool(config.browser_session_id),
        "platform_account_id": config.platform_account_id,
        "feishu_webhook_configured": bool(group.feishu_webhook_url),
        "feishu_webhook_masked": _mask_secret(group.feishu_webhook_url),
        "alert_title": config.alert_title,
        "last_polled_at": config.last_polled_at.isoformat() if config.last_polled_at else None,
        "matrix_brief_enabled": bool(config.matrix_brief_enabled),
        "matrix_brief_title": config.matrix_brief_title,
        **_matrix_brief_config_fields(config, now=now),
    }


def _matrix_brief_config_fields(
    config: CalendarPriceWatchConfig,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    push_times = _resolve_matrix_brief_push_times(config)
    return {
        "matrix_brief_push_times": push_times,
        "matrix_brief_morning_push_time": push_times[0],
        "matrix_brief_evening_push_time": push_times[-1],
        "matrix_brief_schedule_state": _matrix_brief_schedule_state(config, now=now),
    }


def _calendar_schedule_state(
    group: CtripCompetitorWatchGroup,
    config: CalendarPriceWatchConfig,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    interval_minutes = max(
        1,
        min(
            int(
                config.schedule_interval_minutes
                or group.schedule_interval_minutes
                or group.query_interval_minutes
                or DEFAULT_SCHEDULE_INTERVAL_MINUTES
            ),
            1440,
        ),
    )
    return competitor_watch_schedule_state(
        {
            "timezone": group.timezone,
            "schedule_mode": group.schedule_mode,
            "schedule_start_time": group.schedule_start_time,
            "schedule_end_time": group.schedule_end_time,
            "schedule_interval_minutes": interval_minutes,
            "query_interval_minutes": interval_minutes,
            "last_polled_at": config.last_polled_at,
        },
        now=now,
    )


def _alert_to_dict(alert: CalendarPriceAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "run_id": alert.run_id,
        "alert_type": alert.alert_type,
        "checkin_date": alert.checkin_date.isoformat(),
        "own_price_yuan": alert.own_price_yuan,
        "reference_price_yuan": alert.reference_price_yuan,
        "gap_yuan": alert.gap_yuan,
        "reference_hotel_alias": alert.reference_hotel_alias,
        "summary": alert.summary,
        "created_at": alert.created_at.isoformat(),
    }


async def _get_watch_group(session, watch_group_key: str) -> CtripCompetitorWatchGroup | None:
    return await session.scalar(
        select(CtripCompetitorWatchGroup).where(
            CtripCompetitorWatchGroup.group_key == watch_group_key
        )
    )


async def _get_config_for_group(session, watch_group_id: int) -> CalendarPriceWatchConfig | None:
    return await session.scalar(
        select(CalendarPriceWatchConfig).where(
            CalendarPriceWatchConfig.watch_group_id == watch_group_id
        )
    )


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_date_from_result(run: CalendarPriceRun) -> date | None:
    evidence = run.evidence_json if isinstance(run.evidence_json, dict) else {}
    start_date = _parse_date(evidence.get("start_date"))
    if start_date is not None:
        return start_date
    if run.captured_at is not None:
        captured = run.captured_at
        if captured.tzinfo is not None:
            captured = captured.replace(tzinfo=None)
        return captured.date()
    return None


def _shanghai_today() -> date:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).date()


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _SECRET_KEYS or "webhook" in lowered or "screenshot" in lowered:
                continue
            sanitized[key] = _sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    return value


def _mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}***{secret[-4:]}"


def _normalize_matrix_brief_push_time(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_MATRIX_BRIEF_MORNING_PUSH_TIME
    if not TIME_OF_DAY_PATTERN.fullmatch(text):
        raise ValueError("矩阵简报推送时间必须是 HH:MM，例如 10:00。")
    return text


def _normalize_matrix_brief_push_times(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    if values is None:
        return list(DEFAULT_MATRIX_BRIEF_PUSH_TIMES)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        slot = _normalize_matrix_brief_push_time(str(value))
        if slot in seen:
            continue
        seen.add(slot)
        normalized.append(slot)
    if not normalized:
        raise ValueError("矩阵简报至少需要配置一个推送时刻。")
    return sorted(normalized)


def _resolve_matrix_brief_push_times(config: CalendarPriceWatchConfig) -> list[str]:
    raw = config.matrix_brief_push_times
    if isinstance(raw, list) and raw:
        return _normalize_matrix_brief_push_times(raw)
    return list(DEFAULT_MATRIX_BRIEF_PUSH_TIMES)


def _apply_matrix_brief_push_times_patch(
    config: CalendarPriceWatchConfig,
    patch: CalendarPriceWatchConfigPatch,
) -> None:
    if patch.matrix_brief_push_times is not None:
        times = _normalize_matrix_brief_push_times(patch.matrix_brief_push_times)
    else:
        current = _resolve_matrix_brief_push_times(config)
        morning = (
            _normalize_matrix_brief_push_time(patch.matrix_brief_morning_push_time)
            if patch.matrix_brief_morning_push_time is not None
            else current[0]
        )
        evening = (
            _normalize_matrix_brief_push_time(patch.matrix_brief_evening_push_time)
            if patch.matrix_brief_evening_push_time is not None
            else current[-1]
        )
        middle = current[1:-1] if len(current) > 2 else []
        times = _normalize_matrix_brief_push_times([morning, *middle, evening])
    config.matrix_brief_push_times = times
    config.matrix_brief_morning_push_time = times[0]
    config.matrix_brief_evening_push_time = times[-1]


def _seed_past_matrix_brief_slots_on_enable(
    config: CalendarPriceWatchConfig,
    *,
    now: datetime,
    only_slots: set[str] | None = None,
) -> None:
    """Skip catch-up pushes for slots that already passed when enabling or adding times."""
    pushed_json = (
        dict(config.matrix_brief_last_pushed_json)
        if isinstance(config.matrix_brief_last_pushed_json, dict)
        else {}
    )
    changed = False
    for slot_time in _resolve_matrix_brief_push_times(config):
        normalized_slot = _normalize_matrix_brief_push_time(slot_time)
        if only_slots is not None and normalized_slot not in only_slots:
            continue
        hour, minute = (int(part) for part in normalized_slot.split(":", 1))
        due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < due_at:
            continue
        last_pushed = _parse_datetime(pushed_json.get(normalized_slot))
        if last_pushed is not None and last_pushed.date() >= now.date():
            continue
        pushed_json[normalized_slot] = now.isoformat()
        changed = True
    if changed:
        config.matrix_brief_last_pushed_json = pushed_json


def _matrix_brief_slot_is_due(
    slot_time: str,
    last_pushed_json: dict[str, Any] | None,
    now: datetime,
) -> bool:
    normalized_slot = _normalize_matrix_brief_push_time(slot_time)
    hour, minute = (int(part) for part in normalized_slot.split(":", 1))
    due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < due_at:
        return False
    pushed_map = last_pushed_json if isinstance(last_pushed_json, dict) else {}
    last_pushed = _parse_datetime(pushed_map.get(normalized_slot))
    if last_pushed is None:
        return True
    return last_pushed.date() < now.date()


def _matrix_brief_schedule_state(
    config: CalendarPriceWatchConfig,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = (now or local_now()).replace(tzinfo=None)
    push_times = _resolve_matrix_brief_push_times(config)
    pushed_map = (
        dict(config.matrix_brief_last_pushed_json)
        if isinstance(config.matrix_brief_last_pushed_json, dict)
        else {}
    )
    slots = []
    for slot_time in push_times:
        slots.append(
            {
                "push_time": slot_time,
                "last_pushed_at": pushed_map.get(slot_time),
                "next_at": _matrix_brief_next_run_at(slot_time, current_time).isoformat(),
                "due": _matrix_brief_slot_is_due(
                    slot_time, config.matrix_brief_last_pushed_json, current_time
                ),
            }
        )
    morning_slot = push_times[0]
    evening_slot = push_times[-1]
    return {
        "push_times": push_times,
        "slots": slots,
        # Compat fields for older UI/clients.
        "morning_push_time": morning_slot,
        "evening_push_time": evening_slot,
        "morning_last_pushed_at": pushed_map.get(morning_slot),
        "evening_last_pushed_at": pushed_map.get(evening_slot),
        "morning_next_at": _matrix_brief_next_run_at(morning_slot, current_time).isoformat(),
        "evening_next_at": _matrix_brief_next_run_at(evening_slot, current_time).isoformat(),
        "morning_due": _matrix_brief_slot_is_due(
            morning_slot, config.matrix_brief_last_pushed_json, current_time
        ),
        "evening_due": _matrix_brief_slot_is_due(
            evening_slot, config.matrix_brief_last_pushed_json, current_time
        ),
    }


def _matrix_brief_next_run_at(slot_time: str, now: datetime) -> datetime:
    normalized_slot = _normalize_matrix_brief_push_time(slot_time)
    hour, minute = (int(part) for part in normalized_slot.split(":", 1))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < candidate:
        return candidate
    return candidate + timedelta(days=1)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
    return parsed
