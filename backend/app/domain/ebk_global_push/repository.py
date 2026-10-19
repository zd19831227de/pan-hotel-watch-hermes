from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import ensure_db_ready, session_scope
from app.domain.ebk_global_push.models import (
    EbkGlobalPushConfig,
    EbkGlobalPushRun,
    local_now,
)
from app.domain.ebk_global_push.scheduling import (
    MIN_INTERVAL_MINUTES,
    MAX_INTERVAL_MINUTES,
    STALE_RUNNING_MINUTES,
)

SUPPORTED_PLATFORMS = ("ctrip", "meituan", "fliggy")
CTRIP_WATCH_GROUP_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
TIME_OF_DAY_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
SCHEDULE_MODE_PATTERN = r"^(fixed_slots|last_run_interval)$"
TRIGGER_PATTERN = r"^(scheduler|operator|feishu|test)$"


class EbkGlobalPushConfigCreate(BaseModel):
    ctrip_watch_group_key: str = Field(pattern=CTRIP_WATCH_GROUP_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = False
    enabled_platforms: list[str] = Field(min_length=1)
    schedule_mode: str = Field(pattern=SCHEDULE_MODE_PATTERN)
    push_times: list[str] = Field(default_factory=list)
    interval_minutes: int = Field(default=60, ge=MIN_INTERVAL_MINUTES, le=MAX_INTERVAL_MINUTES)
    window_start_time: str = Field(default="06:00", pattern=TIME_OF_DAY_PATTERN)
    window_end_time: str = Field(default="23:00", pattern=TIME_OF_DAY_PATTERN)
    card_title_business: str | None = Field(default=None, max_length=128)
    card_title_promotion: str | None = Field(default=None, max_length=128)
    card_title_advertising: str | None = Field(default=None, max_length=128)


class EbkGlobalPushConfigPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    enabled_platforms: list[str] | None = None
    schedule_mode: str | None = Field(default=None, pattern=SCHEDULE_MODE_PATTERN)
    push_times: list[str] | None = None
    interval_minutes: int | None = Field(
        default=None, ge=MIN_INTERVAL_MINUTES, le=MAX_INTERVAL_MINUTES
    )
    window_start_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    window_end_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    card_title_business: str | None = Field(default=None, max_length=128)
    card_title_promotion: str | None = Field(default=None, max_length=128)
    card_title_advertising: str | None = Field(default=None, max_length=128)


def validate_schedule_fields(
    *,
    schedule_mode: str,
    push_times: list[str],
    interval_minutes: int,
    window_start_time: str,
    window_end_time: str,
) -> None:
    """§7.3 校验：fixed 槽位格式/去重；interval 范围 30-1440；时间格式合法。"""

    if schedule_mode == "fixed_slots":
        if not push_times:
            raise ValueError("固定时间模式至少需要一个推送时间点。")
        normalized = _normalize_push_times(push_times)
        if not normalized:
            raise ValueError("推送时间点格式不合法，应为 HH:MM。")
    if not (MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES):
        raise ValueError(f"间隔分钟需在 {MIN_INTERVAL_MINUTES}-{MAX_INTERVAL_MINUTES} 之间。")
    for label, value in (("开始时间", window_start_time), ("结束时间", window_end_time)):
        if not re.match(TIME_OF_DAY_PATTERN, value or ""):
            raise ValueError(f"间隔模式{label}格式不合法，应为 HH:MM。")


def _normalize_push_times(push_times: list[str]) -> list[str]:
    seen: set[str] = set()
    for raw in push_times or []:
        text = str(raw or "").strip()
        if not re.match(TIME_OF_DAY_PATTERN, text):
            return []
        seen.add(text)
    return sorted(seen)


def _config_to_dict(config: EbkGlobalPushConfig) -> dict[str, Any]:
    return {
        "id": config.id,
        "ctrip_watch_group_key": config.ctrip_watch_group_key,
        "shop_key": config.shop_key,
        "name": config.name,
        "enabled": bool(config.enabled),
        "enabled_platforms": list(config.enabled_platforms_json or []),
        "schedule_mode": config.schedule_mode,
        "push_times": list(config.push_times_json or []),
        "interval_minutes": config.interval_minutes,
        "window_start_time": config.window_start_time,
        "window_end_time": config.window_end_time,
        "timezone": config.timezone,
        "card_title_business": config.card_title_business,
        "card_title_promotion": config.card_title_promotion,
        "card_title_advertising": config.card_title_advertising,
        "last_attempted_at": config.last_attempted_at.isoformat()
        if config.last_attempted_at
        else None,
        "last_successful_push_at": config.last_successful_push_at.isoformat()
        if config.last_successful_push_at
        else None,
        "next_run_at": config.next_run_at.isoformat() if config.next_run_at else None,
        "consecutive_failure_count": config.consecutive_failure_count,
    }


def _run_to_dict(run: EbkGlobalPushRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "config_id": run.config_id,
        "trigger": run.trigger,
        "schedule_key": run.schedule_key,
        "status": run.status,
        "read_started_at": run.read_started_at.isoformat() if run.read_started_at else None,
        "read_finished_at": run.read_finished_at.isoformat() if run.read_finished_at else None,
        "push_started_at": run.push_started_at.isoformat() if run.push_started_at else None,
        "push_finished_at": run.push_finished_at.isoformat() if run.push_finished_at else None,
        "platform_results": dict(run.platform_results_json or {}),
        "card_results": dict(run.card_results_json or {}),
        "error_summary": run.error_summary,
        "error_fingerprint": run.error_fingerprint,
    }


async def _derive_shop_key_for_anchor(session, ctrip_watch_group_key: str) -> str | None:
    """从锚点携程组派生店铺归属（P5 权威源=锚点组；无锚点/未归属为 None）。"""
    from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup

    anchor = (
        await session.scalars(
            select(CtripEbookingWatchGroup).where(
                CtripEbookingWatchGroup.group_key == ctrip_watch_group_key
            ).limit(1)
        )
    ).first()
    if anchor is None:
        return None
    return str(anchor.shop_key or "").strip() or None


async def create_global_push_config(
    request: EbkGlobalPushConfigCreate,
) -> dict[str, Any] | None:
    """创建配置；锚点已存在返回 None（一锚点一条记录，软停用复用原记录）。"""

    unknown = [p for p in request.enabled_platforms if p not in SUPPORTED_PLATFORMS]
    if unknown:
        raise ValueError(f"不支持的平台：{', '.join(unknown)}")
    validate_schedule_fields(
        schedule_mode=request.schedule_mode,
        push_times=request.push_times,
        interval_minutes=request.interval_minutes,
        window_start_time=request.window_start_time,
        window_end_time=request.window_end_time,
    )
    await ensure_db_ready()
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(EbkGlobalPushConfig).where(
                    EbkGlobalPushConfig.ctrip_watch_group_key
                    == request.ctrip_watch_group_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return None
        config = EbkGlobalPushConfig(
            ctrip_watch_group_key=request.ctrip_watch_group_key,
            shop_key=await _derive_shop_key_for_anchor(
                session, request.ctrip_watch_group_key
            ),
            name=request.name,
            enabled=bool(request.enabled),
            enabled_platforms_json=list(request.enabled_platforms),
            schedule_mode=request.schedule_mode,
            push_times_json=_normalize_push_times(request.push_times),
            interval_minutes=request.interval_minutes,
            window_start_time=request.window_start_time,
            window_end_time=request.window_end_time,
            card_title_business=request.card_title_business,
            card_title_promotion=request.card_title_promotion,
            card_title_advertising=request.card_title_advertising,
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return _config_to_dict(config)


async def update_global_push_config(
    config_id: int,
    patch: EbkGlobalPushConfigPatch,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        config = await session.get(EbkGlobalPushConfig, config_id)
        if config is None:
            return None
        data = {
            "schedule_mode": patch.schedule_mode or config.schedule_mode,
            "push_times": patch.push_times
            if patch.push_times is not None
            else list(config.push_times_json or []),
            "interval_minutes": patch.interval_minutes or config.interval_minutes,
            "window_start_time": patch.window_start_time or config.window_start_time,
            "window_end_time": patch.window_end_time or config.window_end_time,
        }
        if patch.enabled_platforms is not None:
            unknown = [p for p in patch.enabled_platforms if p not in SUPPORTED_PLATFORMS]
            if unknown:
                raise ValueError(f"不支持的平台：{', '.join(unknown)}")
            if not patch.enabled_platforms:
                raise ValueError("至少启用一个平台。")
        validate_schedule_fields(**data)
        if patch.name is not None:
            config.name = patch.name
        # 任意更新都重派生店铺归属：权威源是锚点组，配置行不独立维护该值。
        config.shop_key = await _derive_shop_key_for_anchor(
            session, config.ctrip_watch_group_key
        )
        if patch.enabled is not None:
            config.enabled = bool(patch.enabled)
        if patch.enabled_platforms is not None:
            config.enabled_platforms_json = list(patch.enabled_platforms)
        config.schedule_mode = data["schedule_mode"]
        config.push_times_json = _normalize_push_times(data["push_times"])
        config.interval_minutes = data["interval_minutes"]
        config.window_start_time = data["window_start_time"]
        config.window_end_time = data["window_end_time"]
        for field in (
            "card_title_business",
            "card_title_promotion",
            "card_title_advertising",
        ):
            value = getattr(patch, field)
            if value is not None:
                setattr(config, field, value)
        await session.commit()
        await session.refresh(config)
        return _config_to_dict(config)


async def soft_disable_global_push_config(config_id: int) -> None:
    """旧排程冲突反向防护（§4.2）：冲突时软停用，不删除数据。"""

    await ensure_db_ready()
    async with session_scope() as session:
        config = await session.get(EbkGlobalPushConfig, config_id)
        if config is not None and config.enabled:
            config.enabled = False
            await session.commit()


async def get_global_push_config(config_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        config = await session.get(EbkGlobalPushConfig, config_id)
        return _config_to_dict(config) if config else None


async def list_global_push_configs(
    *,
    enabled_only: bool = False,
    shop_key: str | None = None,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(EbkGlobalPushConfig).order_by(EbkGlobalPushConfig.id)
        if enabled_only:
            stmt = stmt.where(EbkGlobalPushConfig.enabled.is_(True))
        if str(shop_key or "").strip():
            stmt = stmt.where(EbkGlobalPushConfig.shop_key == str(shop_key).strip())
        rows = (await session.execute(stmt)).scalars().all()
        return [_config_to_dict(row) for row in rows]


async def claim_global_push_run(
    *,
    config_id: int,
    schedule_key: str,
    trigger: str = "scheduler",
) -> dict[str, Any] | None:
    """抢占运行记录：唯一约束防同槽重复；存在 running 记录时拒绝（互斥）。

    抢占前先回收超时 running（§8.3，45 分钟），使 scheduler 关闭或上一轮崩溃
    留下的孤儿记录不会永久互斥（run-now 与调度共用此入口）。
    """

    if not re.match(TRIGGER_PATTERN, trigger or ""):
        raise ValueError(f"非法触发来源：{trigger}")
    await ensure_db_ready()
    await fail_stale_running_runs()
    async with session_scope() as session:
        running = (
            await session.execute(
                select(EbkGlobalPushRun).where(
                    EbkGlobalPushRun.config_id == config_id,
                    EbkGlobalPushRun.status == "running",
                )
            )
        ).scalar_one_or_none()
        if running is not None:
            return None
        run = EbkGlobalPushRun(
            config_id=config_id,
            trigger=trigger,
            schedule_key=schedule_key,
            status="running",
            read_started_at=local_now(),
        )
        session.add(run)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return None
        await session.refresh(run)
        return _run_to_dict(run)


async def finish_global_push_run(
    run_id: int,
    *,
    status: str,
    platform_results: dict[str, Any] | None = None,
    card_results: dict[str, Any] | None = None,
    error_summary: str | None = None,
    mark_read_finished: bool = True,
    mark_push_finished: bool = True,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        run = await session.get(EbkGlobalPushRun, run_id)
        if run is None:
            return None
        run.status = status
        if platform_results is not None:
            run.platform_results_json = platform_results
        if card_results is not None:
            run.card_results_json = card_results
        if error_summary is not None:
            run.error_summary = error_summary[:500]
            run.error_fingerprint = hashlib.sha256(
                (status + ":" + (error_summary or "")).encode("utf-8")
            ).hexdigest()[:64]
        if mark_read_finished:
            run.read_finished_at = local_now()
        if mark_push_finished:
            run.push_finished_at = local_now()
        await session.commit()
        await session.refresh(run)
        return _run_to_dict(run)


async def mark_config_run_finished(
    config_id: int,
    *,
    succeeded: bool,
    next_run_at: datetime | None,
) -> None:
    """运行结束后推进游标与连续失败计数（C4/C5）。"""

    await ensure_db_ready()
    async with session_scope() as session:
        config = await session.get(EbkGlobalPushConfig, config_id)
        if config is None:
            return
        config.last_attempted_at = local_now()
        if succeeded:
            config.last_successful_push_at = local_now()
            config.consecutive_failure_count = 0
        else:
            config.consecutive_failure_count = int(config.consecutive_failure_count or 0) + 1
        if next_run_at is not None:
            config.next_run_at = next_run_at
        await session.commit()


async def set_config_next_run_at(config_id: int, next_run_at: datetime) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        config = await session.get(EbkGlobalPushConfig, config_id)
        if config is None:
            return
        config.next_run_at = next_run_at
        await session.commit()


async def list_global_push_runs(
    config_id: int,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = (
            select(EbkGlobalPushRun)
            .where(EbkGlobalPushRun.config_id == config_id)
            .order_by(EbkGlobalPushRun.id.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [_run_to_dict(row) for row in rows]


async def list_ctrip_watch_run_links_for_export(
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> dict[str, str]:
    """携程 watch run id → 全局运行 id 映射（设计 §8.2.1）。

    携程 run 表没有 read_trigger/global_run_id 载体，Excel 成组裁剪通过
    本映射把 source=global_push 的携程 run 与美团/飞猪快照归入同一组。
    """

    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(EbkGlobalPushRun).order_by(EbkGlobalPushRun.id.desc()).limit(5000)
        if start_at is not None:
            stmt = stmt.where(
                EbkGlobalPushRun.created_at >= start_at.replace(tzinfo=None)
            )
        if end_at is not None:
            stmt = stmt.where(EbkGlobalPushRun.created_at < end_at.replace(tzinfo=None))
        rows = (await session.execute(stmt)).scalars().all()
        links: dict[str, str] = {}
        for run in rows:
            ctrip_result = (run.platform_results_json or {}).get("ctrip")
            if not isinstance(ctrip_result, dict):
                continue
            watch_run_id = str(
                ctrip_result.get("snapshot_id") or ctrip_result.get("run_id") or ""
            ).strip()
            if watch_run_id:
                links[watch_run_id] = str(run.id)
        return links


async def fail_stale_running_runs(*, now: datetime | None = None) -> int:
    """超时 running（默认 45 分钟）标记 failed，释放同配置互斥（§7.3）。"""

    current = now or local_now()
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(EbkGlobalPushRun).where(EbkGlobalPushRun.status == "running")
        rows = (await session.execute(stmt)).scalars().all()
        stale_ids = [
            row.id
            for row in rows
            if row.created_at and current - row.created_at > timedelta(minutes=STALE_RUNNING_MINUTES)
        ]
        for row_id in stale_ids:
            run = await session.get(EbkGlobalPushRun, row_id)
            if run is not None and run.status == "running":
                run.status = "failed"
                run.error_summary = "运行超时（stale running），由调度器回收。"
                await session.commit()
        return len(stale_ids)
