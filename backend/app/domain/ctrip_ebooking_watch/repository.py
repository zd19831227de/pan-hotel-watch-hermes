from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import selectinload

from app.core.settings import get_settings
from app.db.session import ensure_db_ready, session_scope
from app.domain.browser.sessions import BrowserSession, get_browser_session_store
from app.domain.ctrip_ebooking_watch.models import (
    CtripEbookingWatchDataset,
    CtripEbookingWatchDatasetResult,
    CtripEbookingWatchGroup,
    CtripEbookingWatchRun,
)
from app.domain.ctrip_ebooking_watch.operator_text import operator_failure_reason
from app.domain.operator_payload import sanitize_persisted_payload

DEFAULT_DATA_CENTER_WATCH_GROUP_KEY = "ctrip_ebooking_data_center"
DEFAULT_DAILY_BRIEF_TITLE = "EBK 数据简报"
WATCH_GROUP_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

DATA_CENTER_DATASETS: tuple[tuple[str, str], ...] = (
    ("business_outline", "收益概览"),
    ("beneficial_data", "销售数据"),
    ("flow_data", "流量数据"),
    ("competition_profile", "经营对比"),
    ("loss_analysis", "流失订单"),
    ("competition_list", "销售排名"),
    ("promotion_effect", "促销效果"),
    ("promotion_registered", "已参加促销"),
    ("ad_pyramid", "广告金字塔"),
    ("comment_monitor", "点评分析"),
    # 2026-09-10：金字塔出价列表（只读，复用金字塔调价指令的抓取链路）。
    # 追加在末尾：既有分组按 sort_order 排序，不重排原有数据集小节。
    ("pyramid_bid", "金字塔出价"),
)
DATA_CENTER_DATASET_IDS = tuple(dataset_id for dataset_id, _ in DATA_CENTER_DATASETS)
DATASET_LABELS = dict(DATA_CENTER_DATASETS)
# 对标携程官方「数据简报」：本月概览来自销售数据·离店·按月，漏斗来自流量数据+竞争画像。
DAILY_BRIEF_REQUIRED_DATASET_IDS = (
    "business_outline",
    "comment_monitor",
    "beneficial_data",
    "flow_data",
    "competition_profile",
)
TIME_OF_DAY_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class DataCenterWatchGroupPatch(BaseModel):
    name: str | None = None
    brief_title: str | None = Field(default=None, max_length=128)
    daily_brief_title: str | None = Field(default=None, max_length=128)
    daily_brief_push_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_brief_enabled: bool | None = None
    platform_account_id: str | None = None
    browser_session_id: str | None = None
    clear_binding: bool | None = None
    feishu_chat_id: str | None = None
    feishu_webhook_url: str | None = None
    timezone: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    query_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    auto_poll_enabled: bool | None = None
    enabled: bool | None = None
    shop_key: str | None = Field(default=None, pattern=r"^$|^shop_[A-Za-z0-9_-]+$")


class DataCenterWatchGroupCreate(BaseModel):
    group_key: str = Field(pattern=WATCH_GROUP_KEY_PATTERN)
    name: str
    brief_title: str | None = Field(default=None, max_length=128)
    daily_brief_title: str | None = Field(default=None, max_length=128)
    daily_brief_push_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_brief_enabled: bool | None = None
    platform_account_id: str | None = None
    browser_session_id: str | None = None
    feishu_chat_id: str | None = None
    feishu_webhook_url: str | None = None
    timezone: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    query_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    auto_poll_enabled: bool | None = None
    enabled: bool | None = None
    shop_key: str | None = Field(default=None, pattern=r"^$|^shop_[A-Za-z0-9_-]+$")


class DataCenterWatchDatasetPatch(BaseModel):
    enabled: bool | None = None
    push_enabled: bool | None = None


@dataclass(frozen=True)
class DataCenterWatchDatasetRuntime:
    dataset_id: str
    dataset_label: str
    enabled: bool
    push_enabled: bool
    sort_order: int


@dataclass(frozen=True)
class DataCenterWatchGroupRuntime:
    group_key: str
    name: str
    brief_title: str | None
    platform_account_id: str
    browser_session_id: str | None
    feishu_webhook_url: str | None
    feishu_chat_id: str | None
    timezone: str
    retention_days: int
    query_interval_minutes: int
    auto_poll_enabled: bool
    enabled: bool
    last_polled_at: datetime | None
    datasets: list[DataCenterWatchDatasetRuntime]
    daily_brief_title: str | None = None
    daily_brief_push_time: str = "08:30"
    daily_brief_enabled: bool = False
    daily_brief_last_pushed_at: datetime | None = None


async def get_data_center_watch_group_detail(
    *,
    watch_group_key: str = DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
    create_if_missing: bool = True,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        if create_if_missing:
            group = await _get_or_create_group(
                session,
                watch_group_key=watch_group_key,
                platform_account_id=_default_platform_account_id(),
                preserve_existing_platform_account=True,
            )
        else:
            group = await _get_group_with_datasets(session, watch_group_key)
            if group is None:
                return None
        await _ensure_default_datasets(session, group)
        datasets = await _datasets_for_group(session, group.id)
    return _group_to_dict(group, datasets=datasets)


async def list_data_center_watch_groups_by_feishu_chat_id(
    chat_id: str | None,
) -> list[dict[str, Any]]:
    chat_id = _clean_text(chat_id)
    if not chat_id:
        return []
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchGroup)
                    .where(CtripEbookingWatchGroup.feishu_chat_id == chat_id)
                    .order_by(CtripEbookingWatchGroup.group_key.asc())
                )
            ).all()
        )
        if not groups:
            return []
        group_ids = [group.id for group in groups]
        latest_runs = await _latest_runs_by_group(session, group_ids)
        datasets_by_group = await _datasets_by_group(session, group_ids)
    return [
        {
            **_group_to_dict(group, datasets=datasets_by_group.get(group.id, [])),
            "latest_run": _run_summary_to_dict(latest_runs.get(group.id), group.group_key),
        }
        for group in groups
    ]


async def list_data_center_watch_group_summaries(
    *,
    include_secrets: bool = False,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchGroup).order_by(
                        CtripEbookingWatchGroup.group_key.asc()
                    )
                )
            ).all()
        )
        if not groups:
            group = await _get_or_create_group(
                session,
                watch_group_key=DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
                platform_account_id=_default_platform_account_id(),
                preserve_existing_platform_account=True,
            )
            groups = [group]
        for group in groups:
            await _ensure_default_datasets(session, group)
        group_ids = [group.id for group in groups]
        latest_runs = await _latest_runs_by_group(session, group_ids)
        datasets_by_group = await _datasets_by_group(session, group_ids)
    return [
        {
            **_group_to_dict(group, datasets=datasets_by_group.get(group.id, [])),
            **(
                {
                    "feishu_chat_id": group.feishu_chat_id,
                    "feishu_webhook_url": group.feishu_webhook_url,
                }
                if include_secrets
                else {}
            ),
            "latest_run": _run_summary_to_dict(latest_runs.get(group.id), group.group_key),
        }
        for group in groups
    ]


async def _validate_group_shop_binding(
    session,
    *,
    shop_key: str | None,
    exclude_group_id: int | None = None,
    platform_account_id: str | None = None,
    browser_session_id: str | None = None,
) -> str | None:
    """归一化并校验监控组店铺归属（重构 P4）：店铺存在、每店一个主锚点、
    组账号与账号店铺归属一致。返回归一化后的 shop_key（None=未归属）。"""
    normalized = str(shop_key or "").strip() or None
    if normalized is None:
        return None
    from app.domain.shops import service as shops_service
    from app.domain.shops.service import ShopNotFound

    try:
        await shops_service.get_shop(normalized)
    except ShopNotFound:
        raise ValueError(f"店铺不存在：{normalized}，请先创建店铺。") from None
    stmt = select(CtripEbookingWatchGroup).where(
        CtripEbookingWatchGroup.shop_key == normalized
    )
    if exclude_group_id is not None:
        stmt = stmt.where(CtripEbookingWatchGroup.id != exclude_group_id)
    conflict = (await session.scalars(stmt.limit(1))).first()
    if conflict is not None:
        raise ValueError(
            f"店铺已绑定主携程监控组：{conflict.group_key}（每店最多一个主锚点）。"
        )
    # 账号一致性按 platform_account_id 查（browser_session_id 不是账号表键）。
    account_hint = str(platform_account_id or "").strip() or str(
        browser_session_id or ""
    ).strip()
    if account_hint:
        from app.domain.browser.sessions import get_browser_session_for_platform_account

        bound = get_browser_session_for_platform_account(account_hint)
        if bound is not None and bound.intended_use == "ebk":
            bound_shop = str(bound.shop_key or "").strip()
            if bound_shop and bound_shop != normalized:
                raise ValueError(
                    f"浏览器账号 {bound.platform_account_id} 已归属其他店铺"
                    f"（{bound_shop}），与监控组店铺归属不一致。"
                )
    return normalized


async def create_data_center_watch_group(request: DataCenterWatchGroupCreate) -> dict[str, Any] | None:
    feishu_chat_id = _clean_text(request.feishu_chat_id)
    if feishu_chat_id:
        from app.domain.config.service import require_active_feishu_chat_agent_binding

        await require_active_feishu_chat_agent_binding(feishu_chat_id)
    await ensure_db_ready()
    async with session_scope() as session:
        existing = await _get_group(session, request.group_key)
        if existing is not None:
            return None
        browser_session_id = _clean_text(request.browser_session_id)
        platform_account_id = _clean_text(request.platform_account_id)
        if browser_session_id:
            browser_session = _validate_ebk_browser_session_id(browser_session_id)
            platform_account_id = browser_session.platform_account_id
        group_shop_key = await _validate_group_shop_binding(
            session,
            shop_key=request.shop_key,
            platform_account_id=platform_account_id or None,
            browser_session_id=browser_session_id or None,
        )
        group = CtripEbookingWatchGroup(
            group_key=request.group_key,
            name=request.name.strip() or request.group_key,
            brief_title=_clean_text(request.brief_title),
            daily_brief_title=_clean_text(request.daily_brief_title) or DEFAULT_DAILY_BRIEF_TITLE,
            daily_brief_push_time=_normalize_daily_brief_push_time(request.daily_brief_push_time),
            daily_brief_enabled=bool(request.daily_brief_enabled)
            if request.daily_brief_enabled is not None
            else False,
            platform_account_id=platform_account_id or _default_platform_account_id(),
            browser_session_id=browser_session_id or None,
            shop_key=group_shop_key,
            feishu_chat_id=feishu_chat_id,
            feishu_webhook_url=_clean_text(request.feishu_webhook_url),
            timezone=_clean_text(request.timezone) or "Asia/Shanghai",
            retention_days=request.retention_days or 14,
            query_interval_minutes=request.query_interval_minutes or 60,
            auto_poll_enabled=bool(request.auto_poll_enabled)
            if request.auto_poll_enabled is not None
            else False,
            enabled=bool(request.enabled) if request.enabled is not None else True,
        )
        session.add(group)
        await session.flush()
        await _ensure_default_datasets(session, group)
        datasets = await _datasets_for_group(session, group.id)
    return _group_to_dict(group, datasets=datasets)


async def delete_data_center_watch_group(*, watch_group_key: str) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
        datasets = await _datasets_for_group(session, group.id)
        payload = _group_to_dict(group, datasets=datasets)
        await session.execute(
            delete(CtripEbookingWatchDatasetResult).where(
                CtripEbookingWatchDatasetResult.group_id == group.id
            )
        )
        await session.execute(
            delete(CtripEbookingWatchRun).where(CtripEbookingWatchRun.group_id == group.id)
        )
        await session.execute(
            delete(CtripEbookingWatchDataset).where(CtripEbookingWatchDataset.group_id == group.id)
        )
        await session.delete(group)
        await session.flush()
    return payload


async def update_data_center_watch_group(
    *,
    watch_group_key: str = DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
    patch: DataCenterWatchGroupPatch,
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
        group = await _get_or_create_group(
            session,
            watch_group_key=watch_group_key,
            platform_account_id=patch.platform_account_id or _default_platform_account_id(),
            preserve_existing_platform_account=patch.platform_account_id is None,
        )
        if patch.name is not None:
            group.name = patch.name.strip() or group.name
        if patch.brief_title is not None:
            group.brief_title = patch.brief_title.strip() or None
        if patch.daily_brief_title is not None:
            group.daily_brief_title = patch.daily_brief_title.strip() or None
        if patch.daily_brief_push_time is not None:
            group.daily_brief_push_time = _normalize_daily_brief_push_time(
                patch.daily_brief_push_time
            )
        if patch.daily_brief_enabled is not None:
            group.daily_brief_enabled = patch.daily_brief_enabled
        if patch.browser_session_id is not None:
            browser_session_id = patch.browser_session_id.strip()
            if browser_session_id:
                browser_session = _validate_ebk_browser_session_id(browser_session_id)
                # 显式 shop 与组账号必须归属一致（§3.2.1）。
                if str(group.shop_key or "").strip():
                    from app.domain.browser.sessions import (
                        get_browser_session_for_platform_account,
                    )

                    bound = get_browser_session_for_platform_account(
                        browser_session.platform_account_id
                    )
                    bound_shop = str(bound.shop_key or "").strip() if bound else ""
                    if bound_shop and bound_shop != str(group.shop_key or "").strip():
                        raise ValueError(
                            f"浏览器账号 {browser_session.platform_account_id} 已归属其他店铺"
                            f"（{bound_shop}），与监控组店铺归属不一致。"
                        )
                group.browser_session_id = browser_session.browser_session_id
                group.platform_account_id = browser_session.platform_account_id
            else:
                group.browser_session_id = None
        if patch.shop_key is not None:
            group.shop_key = await _validate_group_shop_binding(
                session,
                shop_key=patch.shop_key,
                exclude_group_id=group.id,
                platform_account_id=group.platform_account_id,
                browser_session_id=group.browser_session_id,
            )
        if patch.platform_account_id is not None and patch.browser_session_id is None:
            group.platform_account_id = patch.platform_account_id.strip()
        if patch.feishu_chat_id is not None:
            group.feishu_chat_id = feishu_chat_id
        if patch.feishu_webhook_url is not None:
            group.feishu_webhook_url = patch.feishu_webhook_url.strip() or None
        if patch.timezone is not None:
            group.timezone = patch.timezone.strip() or "Asia/Shanghai"
        if patch.retention_days is not None:
            group.retention_days = patch.retention_days
        if patch.query_interval_minutes is not None:
            group.query_interval_minutes = patch.query_interval_minutes
        if patch.auto_poll_enabled is not None:
            group.auto_poll_enabled = patch.auto_poll_enabled
        if patch.enabled is not None:
            group.enabled = patch.enabled
        if patch.clear_binding:
            _clear_group_binding(group)
        await _ensure_default_datasets(session, group)
        await session.flush()
        datasets = await _datasets_for_group(session, group.id)
    return _group_to_dict(group, datasets=datasets)


async def clear_data_center_watch_bindings_for_browser_session(
    *,
    platform_account_id: str,
    browser_session_id: str,
) -> int:
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchGroup).where(
                        or_(
                            CtripEbookingWatchGroup.platform_account_id == platform_account_id,
                            CtripEbookingWatchGroup.browser_session_id == browser_session_id,
                        )
                    )
                )
            ).all()
        )
        for group in groups:
            _clear_group_binding(group)
        await session.flush()
    return len(groups)


async def snapshot_data_center_watch_bindings_for_browser_session(
    *,
    platform_account_id: str,
    browser_session_id: str,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchGroup).where(
                        _browser_session_binding_filter(
                            platform_account_id=platform_account_id,
                            browser_session_id=browser_session_id,
                        )
                    )
                )
            ).all()
        )
        return [_binding_snapshot(group) for group in groups]


async def restore_data_center_watch_bindings(snapshots: list[dict[str, Any]]) -> int:
    if not snapshots:
        return 0
    await ensure_db_ready()
    async with session_scope() as session:
        restored = 0
        for snapshot in snapshots:
            group_key = snapshot.get("group_key")
            if not isinstance(group_key, str) or not group_key:
                continue
            group = await session.scalar(
                select(CtripEbookingWatchGroup).where(
                    CtripEbookingWatchGroup.group_key == group_key
                )
            )
            if group is None:
                continue
            group.platform_account_id = str(snapshot["platform_account_id"])
            browser_session_id = snapshot.get("browser_session_id")
            group.browser_session_id = str(browser_session_id) if browser_session_id else None
            group.enabled = bool(snapshot["enabled"])
            group.auto_poll_enabled = bool(snapshot["auto_poll_enabled"])
            restored += 1
        await session.flush()
    return restored


async def migrate_data_center_watch_bindings_for_browser_session(
    *,
    old_platform_account_id: str,
    browser_session_id: str,
    new_platform_account_id: str,
) -> int:
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchGroup).where(
                        _browser_session_binding_filter(
                            platform_account_id=old_platform_account_id,
                            browser_session_id=browser_session_id,
                        )
                    )
                )
            ).all()
        )
        for group in groups:
            group.platform_account_id = new_platform_account_id
            group.browser_session_id = browser_session_id
        await session.flush()
    return len(groups)


async def list_data_center_watch_datasets(
    *,
    watch_group_key: str = DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_or_create_group(
            session,
            watch_group_key=watch_group_key,
            platform_account_id=_default_platform_account_id(),
            preserve_existing_platform_account=True,
        )
        await _ensure_default_datasets(session, group)
        datasets = await _datasets_for_group(session, group.id)
    return [_dataset_to_dict(dataset) for dataset in datasets]


async def update_data_center_watch_dataset(
    *,
    watch_group_key: str = DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
    dataset_id: str,
    patch: DataCenterWatchDatasetPatch,
) -> dict[str, Any] | None:
    normalized_dataset_id = normalize_dataset_id(dataset_id)
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_or_create_group(
            session,
            watch_group_key=watch_group_key,
            platform_account_id=_default_platform_account_id(),
            preserve_existing_platform_account=True,
        )
        await _ensure_default_datasets(session, group)
        dataset = await _get_dataset(session, group.id, normalized_dataset_id)
        if dataset is None:
            return None
        if patch.enabled is not None:
            dataset.enabled = patch.enabled
        if patch.push_enabled is not None:
            dataset.push_enabled = patch.push_enabled
        await session.flush()
    return _dataset_to_dict(dataset)


async def get_data_center_watch_group_runtime(
    *,
    watch_group_key: str = DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
) -> DataCenterWatchGroupRuntime | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group_with_datasets(session, watch_group_key)
        if group is None:
            return None
        await _ensure_default_datasets(session, group)
        datasets = await _datasets_for_group(session, group.id)
    return _group_to_runtime(group, datasets=datasets)


async def list_due_data_center_watch_groups(*, now: datetime | None = None) -> list[DataCenterWatchGroupRuntime]:
    await ensure_db_ready()
    current_time = (now or _local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchGroup)
                    .options(selectinload(CtripEbookingWatchGroup.datasets))
                    .where(
                        CtripEbookingWatchGroup.enabled.is_(True),
                        CtripEbookingWatchGroup.auto_poll_enabled.is_(True),
                    )
                    .order_by(CtripEbookingWatchGroup.group_key.asc())
                )
            ).all()
        )
        for group in groups:
            await _ensure_default_datasets(session, group)
    return [
        _group_to_runtime(group, datasets=group.datasets)
        for group in groups
        if _group_is_due(group, current_time)
    ]


async def list_due_daily_brief_groups(*, now: datetime | None = None) -> list[DataCenterWatchGroupRuntime]:
    await ensure_db_ready()
    current_time = (now or _local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchGroup)
                    .options(selectinload(CtripEbookingWatchGroup.datasets))
                    .where(
                        CtripEbookingWatchGroup.enabled.is_(True),
                        CtripEbookingWatchGroup.daily_brief_enabled.is_(True),
                    )
                    .order_by(CtripEbookingWatchGroup.group_key.asc())
                )
            ).all()
        )
        for group in groups:
            await _ensure_default_datasets(session, group)
    return [
        _group_to_runtime(group, datasets=group.datasets)
        for group in groups
        if _daily_brief_group_is_due(group, current_time)
    ]


async def mark_data_center_watch_group_polled(
    *,
    watch_group_key: str,
    captured_at: datetime | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
        group.last_polled_at = (captured_at or _local_now()).replace(tzinfo=None)
        datasets = await _datasets_for_group(session, group.id)
    return _group_to_dict(group, datasets=datasets)


async def mark_daily_brief_group_pushed(
    *,
    watch_group_key: str,
    captured_at: datetime | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
        group.daily_brief_last_pushed_at = (captured_at or _local_now()).replace(tzinfo=None)
        datasets = await _datasets_for_group(session, group.id)
    return _group_to_dict(group, datasets=datasets)


async def save_data_center_watch_run(
    *,
    watch_group_key: str,
    platform_account_id: str,
    browser_session_id: str | None,
    actor_id: str,
    source: Literal["api", "scheduler", "feishu", "smoke_check", "global_push"],
    status: str,
    summary: str,
    dataset_results: list[dict[str, Any]],
    push_result: dict[str, Any] | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    await ensure_db_ready()
    run_captured_at = (captured_at or _local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        group = await _get_or_create_group(
            session,
            watch_group_key=watch_group_key,
            platform_account_id=platform_account_id,
            preserve_existing_platform_account=True,
        )
        await _ensure_default_datasets(session, group)
        datasets = await _datasets_for_group(session, group.id)
        run = CtripEbookingWatchRun(
            group_id=group.id,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            source=source,
            status=status,
            summary=summary,
            dataset_count=len(dataset_results),
            success_count=sum(1 for item in dataset_results if item.get("status") == "success"),
            failed_count=sum(1 for item in dataset_results if item.get("status") == "failed"),
            human_required_count=sum(
                1 for item in dataset_results if item.get("status") == "human_required"
            ),
            captured_at=run_captured_at,
            push_result_json=_strip_sensitive(push_result),
        )
        session.add(run)
        await session.flush()

        datasets_by_id = {dataset.dataset_id: dataset for dataset in datasets}
        for result in dataset_results:
            dataset_id = normalize_dataset_id(str(result.get("dataset_id") or ""))
            dataset = datasets_by_id.get(dataset_id)
            status_value = str(result.get("status") or "failed")
            failure_reason = _failure_reason(result)
            dataset_captured_at = _parse_datetime(result.get("captured_at")) or run_captured_at
            row = CtripEbookingWatchDatasetResult(
                run_id=run.id,
                group_id=group.id,
                dataset_id=dataset_id,
                dataset_label=DATASET_LABELS[dataset_id],
                status=status_value,
                summary=_optional_str(result.get("summary")),
                failure_reason=failure_reason,
                parser_version=_optional_str(result.get("parser_version")),
                captured_at=dataset_captured_at,
                data_json=_strip_sensitive(result.get("data")),
                evidence_json=_strip_sensitive(result.get("evidence")),
                push_enabled=bool(result.get("push_enabled", False)),
                pushed=bool(result.get("pushed", False)),
                push_result_json=_strip_sensitive(result.get("push_result")),
            )
            session.add(row)
            if dataset is not None:
                dataset.last_status = status_value
                dataset.last_failure_reason = failure_reason
                dataset.last_captured_at = dataset_captured_at
        group.last_polled_at = run_captured_at
        await session.flush()
        run_id = run.id
    return {
        "status": "success",
        "summary": f"Saved data-center watch run {run_id}.",
        "watch_group_key": watch_group_key,
        "run_id": run_id,
        "dataset_count": len(dataset_results),
    }


async def update_data_center_watch_run_push_result(
    *,
    run_id: int,
    dataset_results: list[dict[str, Any]],
    push_result: dict[str, Any],
) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        run = await session.get(CtripEbookingWatchRun, run_id)
        if run is not None:
            run.push_result_json = _strip_sensitive(push_result)
        rows = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchDatasetResult).where(
                        CtripEbookingWatchDatasetResult.run_id == run_id
                    )
                )
            ).all()
        )
        results_by_dataset = {str(result.get("dataset_id") or ""): result for result in dataset_results}
        for row in rows:
            result = results_by_dataset.get(row.dataset_id)
            if result is None:
                continue
            row.pushed = bool(result.get("pushed", False))
            row.push_result_json = _strip_sensitive(result.get("push_result"))


async def get_previous_business_outline_runs(
    *,
    watch_group_key: str,
    before_captured_at: datetime,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """最近含成功 business_outline 结果的运行（供日环比等真实读取对比）。

    只返回真实成功读取过的 data_json；调用方自行决定口径（如仅取前一日）。"""
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return []
        runs = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchRun)
                    .where(
                        CtripEbookingWatchRun.group_id == group.id,
                        CtripEbookingWatchRun.captured_at
                        < before_captured_at.replace(tzinfo=None),
                    )
                    .order_by(
                        CtripEbookingWatchRun.captured_at.desc(),
                        CtripEbookingWatchRun.id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        )
        payloads: list[dict[str, Any]] = []
        for run in runs:
            row = await session.scalar(
                select(CtripEbookingWatchDatasetResult).where(
                    CtripEbookingWatchDatasetResult.run_id == run.id,
                    CtripEbookingWatchDatasetResult.dataset_id == "business_outline",
                    CtripEbookingWatchDatasetResult.status == "success",
                )
            )
            if row is None or not row.data_json:
                continue
            try:
                data = json.loads(row.data_json)
            except (TypeError, ValueError):
                continue
            payloads.append(
                {"run_id": run.id, "captured_at": run.captured_at, "data": data}
            )
        return payloads


async def get_latest_data_center_watch_run(
    *,
    watch_group_key: str = DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
        run = await session.scalar(
            select(CtripEbookingWatchRun)
            .where(CtripEbookingWatchRun.group_id == group.id)
            .order_by(CtripEbookingWatchRun.captured_at.desc(), CtripEbookingWatchRun.id.desc())
            .limit(1)
        )
        if run is None:
            return None
        results = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchDatasetResult)
                    .where(CtripEbookingWatchDatasetResult.run_id == run.id)
                    .order_by(CtripEbookingWatchDatasetResult.id.asc())
                )
            ).all()
        )
    return _run_to_dict(run, results, watch_group_key)


async def list_data_center_watch_runs(
    *,
    watch_group_key: str = DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
    limit: int = 20,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
        runs = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchRun)
                    .where(CtripEbookingWatchRun.group_id == group.id)
                    .order_by(CtripEbookingWatchRun.captured_at.desc(), CtripEbookingWatchRun.id.desc())
                    .limit(limit)
                )
            ).all()
        )
    return {
        "watch_group_key": watch_group_key,
        "runs": [_run_summary_to_dict(run, watch_group_key) for run in runs],
    }


async def list_data_center_watch_runs_for_export(
    *,
    start_at: datetime,
    end_at: datetime | None,
    limit: int,
    shop_key: str | None = None,
) -> list[dict[str, Any]]:
    """Return newest persisted Ctrip watch-run candidates for business export.

    The returned dictionaries are an explicit allow-list: business ``data_json``
    is included while browser sessions, evidence and push results are omitted.
    shop_key 给定时只导出锚点组归属该店的 run（P7，按组归属过滤）。
    """

    await ensure_db_ready()
    safe_limit = max(1, min(int(limit), 5000))
    statement = select(CtripEbookingWatchRun).where(
        CtripEbookingWatchRun.captured_at >= start_at.replace(tzinfo=None)
    )
    if end_at is not None:
        statement = statement.where(
            CtripEbookingWatchRun.captured_at < end_at.replace(tzinfo=None)
        )
    if str(shop_key or "").strip():
        statement = statement.join(
            CtripEbookingWatchGroup,
            CtripEbookingWatchRun.group_id == CtripEbookingWatchGroup.id,
        ).where(CtripEbookingWatchGroup.shop_key == str(shop_key).strip())
    statement = (
        statement.options(selectinload(CtripEbookingWatchRun.results))
        .order_by(
            CtripEbookingWatchRun.captured_at.desc(),
            CtripEbookingWatchRun.id.desc(),
        )
        .limit(safe_limit)
    )
    async with session_scope() as session:
        runs = list((await session.scalars(statement)).all())
    return [
        {
            "run_id": run.id,
            "platform_account_id": run.platform_account_id,
            "actor_id": run.actor_id,
            "source": run.source,
            "captured_at": run.captured_at.isoformat(),
            "results": [
                {
                    "dataset_id": result.dataset_id,
                    "dataset_label": result.dataset_label,
                    "status": result.status,
                    "data_json": result.data_json
                    if isinstance(result.data_json, dict)
                    else {},
                }
                for result in sorted(run.results, key=lambda item: item.id)
            ],
        }
        for run in runs
    ]


def normalize_dataset_id(dataset_id: str) -> str:
    normalized = str(dataset_id or "").strip().lower()
    if normalized not in DATASET_LABELS:
        supported = ", ".join(DATA_CENTER_DATASET_IDS)
        raise ValueError(f"Unsupported Ctrip data-center dataset_id: {dataset_id}. {supported}")
    return normalized


async def _get_group(session, watch_group_key: str) -> CtripEbookingWatchGroup | None:
    return await session.scalar(
        select(CtripEbookingWatchGroup).where(CtripEbookingWatchGroup.group_key == watch_group_key)
    )


async def _get_group_with_datasets(session, watch_group_key: str) -> CtripEbookingWatchGroup | None:
    return await session.scalar(
        select(CtripEbookingWatchGroup)
        .options(selectinload(CtripEbookingWatchGroup.datasets))
        .where(CtripEbookingWatchGroup.group_key == watch_group_key)
    )


async def _get_or_create_group(
    session,
    *,
    watch_group_key: str,
    platform_account_id: str,
    preserve_existing_platform_account: bool = False,
) -> CtripEbookingWatchGroup:
    _validate_watch_group_key(watch_group_key)
    group = await _get_group_with_datasets(session, watch_group_key)
    if group is not None:
        if not preserve_existing_platform_account:
            group.platform_account_id = platform_account_id
        return group
    group = CtripEbookingWatchGroup(
        group_key=watch_group_key,
        name="携程 EBK 数据中心盯盘",
        brief_title="携程 EBK 数据中心盯盘",
        daily_brief_title=DEFAULT_DAILY_BRIEF_TITLE,
        daily_brief_push_time="08:30",
        daily_brief_enabled=False,
        platform_account_id=platform_account_id,
        timezone="Asia/Shanghai",
        retention_days=14,
        query_interval_minutes=60,
        auto_poll_enabled=False,
        enabled=True,
    )
    session.add(group)
    await session.flush()
    await _ensure_default_datasets(session, group)
    return group


async def _ensure_default_datasets(session, group: CtripEbookingWatchGroup) -> None:
    existing_rows = await _datasets_for_group(session, group.id)
    existing = {dataset.dataset_id for dataset in existing_rows}
    for dataset in existing_rows:
        expected_label = DATASET_LABELS.get(dataset.dataset_id)
        if expected_label and dataset.dataset_label != expected_label:
            dataset.dataset_label = expected_label
    for sort_order, (dataset_id, dataset_label) in enumerate(DATA_CENTER_DATASETS):
        if dataset_id in existing:
            continue
        session.add(
            CtripEbookingWatchDataset(
                group_id=group.id,
                dataset_id=dataset_id,
                dataset_label=dataset_label,
                enabled=True,
                push_enabled=True,
                sort_order=sort_order,
            )
        )
    await session.flush()


async def _datasets_for_group(session, group_id: int) -> list[CtripEbookingWatchDataset]:
    return list(
        (
            await session.scalars(
                select(CtripEbookingWatchDataset)
                .where(CtripEbookingWatchDataset.group_id == group_id)
                .order_by(CtripEbookingWatchDataset.sort_order.asc(), CtripEbookingWatchDataset.dataset_id.asc())
            )
        ).all()
    )


async def _datasets_by_group(
    session,
    group_ids: list[int],
) -> dict[int, list[CtripEbookingWatchDataset]]:
    if not group_ids:
        return {}
    rows = list(
        (
            await session.scalars(
                select(CtripEbookingWatchDataset)
                .where(CtripEbookingWatchDataset.group_id.in_(group_ids))
                .order_by(
                    CtripEbookingWatchDataset.group_id.asc(),
                    CtripEbookingWatchDataset.sort_order.asc(),
                    CtripEbookingWatchDataset.dataset_id.asc(),
                )
            )
        ).all()
    )
    grouped: dict[int, list[CtripEbookingWatchDataset]] = {}
    for dataset in rows:
        grouped.setdefault(dataset.group_id, []).append(dataset)
    return grouped


async def _latest_runs_by_group(
    session,
    group_ids: list[int],
) -> dict[int, CtripEbookingWatchRun]:
    if not group_ids:
        return {}
    rows = list(
        (
            await session.scalars(
                select(CtripEbookingWatchRun)
                .where(CtripEbookingWatchRun.group_id.in_(group_ids))
                .order_by(
                    CtripEbookingWatchRun.group_id.asc(),
                    CtripEbookingWatchRun.captured_at.desc(),
                    CtripEbookingWatchRun.id.desc(),
                )
            )
        ).all()
    )
    latest: dict[int, CtripEbookingWatchRun] = {}
    for run in rows:
        latest.setdefault(run.group_id, run)
    return latest


async def _get_dataset(session, group_id: int, dataset_id: str) -> CtripEbookingWatchDataset | None:
    return await session.scalar(
        select(CtripEbookingWatchDataset).where(
            CtripEbookingWatchDataset.group_id == group_id,
            CtripEbookingWatchDataset.dataset_id == dataset_id,
        )
    )


def _group_to_runtime(
    group: CtripEbookingWatchGroup,
    *,
    datasets: list[CtripEbookingWatchDataset],
) -> DataCenterWatchGroupRuntime:
    return DataCenterWatchGroupRuntime(
        group_key=group.group_key,
        name=group.name,
        brief_title=group.brief_title,
        daily_brief_title=group.daily_brief_title,
        daily_brief_push_time=_normalize_daily_brief_push_time(group.daily_brief_push_time),
        daily_brief_enabled=group.daily_brief_enabled,
        daily_brief_last_pushed_at=group.daily_brief_last_pushed_at,
        platform_account_id=group.platform_account_id,
        browser_session_id=group.browser_session_id,
        feishu_webhook_url=group.feishu_webhook_url,
        feishu_chat_id=group.feishu_chat_id,
        timezone=group.timezone,
        retention_days=group.retention_days,
        query_interval_minutes=group.query_interval_minutes,
        auto_poll_enabled=group.auto_poll_enabled,
        enabled=group.enabled,
        last_polled_at=group.last_polled_at,
        datasets=[
            DataCenterWatchDatasetRuntime(
                dataset_id=dataset.dataset_id,
                dataset_label=dataset.dataset_label,
                enabled=dataset.enabled,
                push_enabled=dataset.push_enabled,
                sort_order=dataset.sort_order,
            )
            for dataset in _sorted_datasets(datasets)
        ],
    )


def _clear_group_binding(group: CtripEbookingWatchGroup) -> None:
    group.platform_account_id = _cleared_platform_account_id()
    group.browser_session_id = None
    group.enabled = False
    group.auto_poll_enabled = False


def _binding_snapshot(group: CtripEbookingWatchGroup) -> dict[str, Any]:
    return {
        "group_key": group.group_key,
        "platform_account_id": group.platform_account_id,
        "browser_session_id": group.browser_session_id,
        "enabled": group.enabled,
        "auto_poll_enabled": group.auto_poll_enabled,
    }


def _browser_session_binding_filter(*, platform_account_id: str, browser_session_id: str) -> Any:
    return or_(
        CtripEbookingWatchGroup.platform_account_id == platform_account_id,
        CtripEbookingWatchGroup.browser_session_id == browser_session_id,
    )


def _session_status_label(browser_session_id: str | None) -> str:
    """Tag a browser session id as registered/unregistered/none for the display layer.

    Returns ``"none"`` when no session is bound, ``"registered"`` when the id
    exists in BrowserSessionStore, and ``"unregistered"`` when it is stale/unknown.
    The raw id is never rewritten; the frontend uses this field to decide whether
    to show a "会话未注册" hint instead of surfacing Unknown browser session errors.
    """
    if not browser_session_id:
        return "none"
    try:
        get_browser_session_store().get(browser_session_id)
        return "registered"
    except KeyError:
        return "unregistered"



def _group_to_dict(
    group: CtripEbookingWatchGroup,
    *,
    datasets: list[CtripEbookingWatchDataset] | None = None,
) -> dict[str, Any]:
    return {
        "id": group.id,
        "group_key": group.group_key,
        "name": group.name,
        "shop_key": group.shop_key,
        "brief_title": group.brief_title,
        "daily_brief_title": group.daily_brief_title,
        "daily_brief_push_time": _normalize_daily_brief_push_time(group.daily_brief_push_time),
        "daily_brief_enabled": group.daily_brief_enabled,
        "daily_brief_last_pushed_at": (
            group.daily_brief_last_pushed_at.isoformat()
            if group.daily_brief_last_pushed_at
            else None
        ),
        "platform_account_id": group.platform_account_id,
        "browser_session_id": group.browser_session_id,
        "session_status": _session_status_label(group.browser_session_id),
        "feishu_chat_id_configured": bool(group.feishu_chat_id),
        "feishu_chat_id_masked": _mask_secret(group.feishu_chat_id),
        "feishu_webhook_configured": bool(group.feishu_webhook_url),
        "feishu_webhook_masked": _mask_secret(group.feishu_webhook_url),
        "timezone": group.timezone,
        "retention_days": group.retention_days,
        "query_interval_minutes": group.query_interval_minutes,
        "auto_poll_enabled": group.auto_poll_enabled,
        "enabled": group.enabled,
        "created_at": group.created_at.isoformat(),
        "updated_at": group.updated_at.isoformat(),
        "last_polled_at": group.last_polled_at.isoformat() if group.last_polled_at else None,
        "datasets": [_dataset_to_dict(dataset) for dataset in _sorted_datasets(datasets or [])],
    }


def _dataset_to_dict(dataset: CtripEbookingWatchDataset) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "group_id": dataset.group_id,
        "dataset_id": dataset.dataset_id,
        "dataset_label": dataset.dataset_label,
        "enabled": dataset.enabled,
        "push_enabled": dataset.push_enabled,
        "sort_order": dataset.sort_order,
        "last_status": dataset.last_status,
        "last_failure_reason": operator_failure_reason(
            dataset.last_failure_reason,
            status=dataset.last_status,
        ),
        "last_captured_at": dataset.last_captured_at.isoformat() if dataset.last_captured_at else None,
    }


def _run_to_dict(
    run: CtripEbookingWatchRun,
    results: list[CtripEbookingWatchDatasetResult],
    watch_group_key: str,
) -> dict[str, Any]:
    return {
        "watch_group_key": watch_group_key,
        "run": _run_summary_to_dict(run, watch_group_key),
        "dataset_results": [_result_to_dict(result) for result in results],
    }


def _run_summary_to_dict(run: CtripEbookingWatchRun | None, watch_group_key: str) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "watch_group_key": watch_group_key,
        "platform_account_id": run.platform_account_id,
        "browser_session_id": run.browser_session_id,
        "session_status": _session_status_label(run.browser_session_id),
        "actor_id": run.actor_id,
        "source": run.source,
        "status": run.status,
        "summary": run.summary,
        "dataset_count": run.dataset_count,
        "success_count": run.success_count,
        "failed_count": run.failed_count,
        "human_required_count": run.human_required_count,
        "captured_at": run.captured_at.isoformat(),
        "push_result": _operator_push_result(run.push_result_json),
    }


def _result_to_dict(result: CtripEbookingWatchDatasetResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "run_id": result.run_id,
        "dataset_id": result.dataset_id,
        "dataset_label": result.dataset_label,
        "status": result.status,
        "summary": result.summary,
        "failure_reason": operator_failure_reason(
            result.failure_reason,
            status=result.status,
        ),
        "parser_version": result.parser_version,
        "captured_at": result.captured_at.isoformat(),
        "data": result.data_json,
        "evidence": result.evidence_json,
        "push_enabled": result.push_enabled,
        "pushed": result.pushed,
        "push_result": _operator_push_result(result.push_result_json),
    }


def _operator_push_result(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    status = str(payload.get("status") or "")
    summary = payload.get("summary")
    if status == "config_required" and isinstance(summary, str) and (
        "No Feishu webhook URL" in summary or "Configure a Feishu webhook URL" in summary
    ):
        payload["summary"] = "未配置飞书机器人地址，已保存本次读取结果。"
        payload["next_action"] = "在 EBK 数据中心监控组中配置飞书机器人地址。"
    return payload


def _sorted_datasets(datasets: list[CtripEbookingWatchDataset]) -> list[CtripEbookingWatchDataset]:
    return sorted(datasets, key=lambda item: (item.sort_order, item.dataset_id))


def _group_is_due(group: CtripEbookingWatchGroup, now: datetime) -> bool:
    if not group.last_polled_at:
        return True
    return group.last_polled_at + timedelta(minutes=group.query_interval_minutes) <= now


def _daily_brief_group_is_due(group: CtripEbookingWatchGroup, now: datetime) -> bool:
    push_time = _normalize_daily_brief_push_time(group.daily_brief_push_time)
    hour, minute = (int(part) for part in push_time.split(":", 1))
    due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < due_at:
        return False
    last_pushed_at = group.daily_brief_last_pushed_at
    if last_pushed_at is None:
        return True
    return last_pushed_at.date() < now.date()


def _normalize_daily_brief_push_time(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "08:30"
    if not TIME_OF_DAY_PATTERN.fullmatch(text):
        raise ValueError("EBK 数据简报推送时间必须是 HH:MM，例如 08:30。")
    return text


def _default_platform_account_id() -> str:
    settings = get_settings()
    return (
        settings.ctrip_ebooking_default_platform_account_id
        or settings.feishu_default_platform_account_id
        or "__unconfigured__"
    )


def _cleared_platform_account_id() -> str:
    return "__cleared__"


def _local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None) if parsed.tzinfo else parsed


def _failure_reason(result: dict[str, Any]) -> str | None:
    if result.get("status") == "success":
        return None
    for key in ("failure_reason", "summary"):
        value = result.get(key)
        if value:
            return str(value)[:255]
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    for key in ("review_reason", "error", "error_type", "page_state"):
        value = evidence.get(key)
        if value:
            return str(value)[:255]
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strip_sensitive(value: Any) -> Any:
    return sanitize_persisted_payload(value)


def _mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}***{secret[-4:]}"


def _validate_watch_group_key(value: str) -> None:
    if not re.fullmatch(WATCH_GROUP_KEY_PATTERN, value or ""):
        raise ValueError("Invalid watch_group_key.")


def _validate_ebk_browser_session_id(browser_session_id: str) -> BrowserSession:
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise ValueError(f"浏览器账号不存在：{browser_session_id}") from exc
    return _validate_ebk_browser_session(session)


def _validate_ebk_browser_session(session: BrowserSession) -> BrowserSession:
    if session.platform != "ctrip":
        raise ValueError("EBK 数据中心监控组只能绑定携程浏览器账号。")
    if session.intended_use != "ebk":
        raise ValueError("EBK 数据中心监控组只能绑定用途为 EBK 后台的浏览器账号。")
    return session
