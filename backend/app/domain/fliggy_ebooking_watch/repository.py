from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.db.session import ensure_db_ready, session_scope
from app.domain.browser.sessions import BrowserSession, get_browser_session_store
from app.domain.competitor_prices.schedule import watch_group_is_due
from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
from app.domain.fliggy_ebooking_watch.models import (
    FliggyEbookingWatchGroup,
    local_now,
)

DEFAULT_MEITUAN_WATCH_GROUP_KEY = "fliggy_ebooking_data_center"
SUPPORTED_TIMEZONE = "Asia/Shanghai"
WATCH_GROUP_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
TIME_OF_DAY_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
SCHEDULE_MODE_PATTERN = r"^(fixed_slots|last_run_interval)$"
CTRIP_WATCH_GROUP_KEY_PATTERN = WATCH_GROUP_KEY_PATTERN


class FliggyWatchGroupCreate(BaseModel):
    group_key: str = Field(pattern=WATCH_GROUP_KEY_PATTERN)
    name: str
    brief_title: str | None = Field(default=None, max_length=128)
    # v2 §2：店铺直连；legacy 挂载键可选（仅迁移期兼容，二者至少给一个）。
    shop_key: str | None = Field(default=None, pattern=r"^$|^shop_[A-Za-z0-9_-]+$")
    ctrip_watch_group_key: str | None = Field(
        default=None,
        pattern=r"^$" + "|" + CTRIP_WATCH_GROUP_KEY_PATTERN,
    )
    feishu_chat_id: str | None = Field(default=None, max_length=128)
    feishu_webhook_url: str | None = Field(default=None, max_length=2048)
    browser_session_id: str | None = None
    timezone: str | None = None
    schedule_mode: str | None = Field(default=None, pattern=SCHEDULE_MODE_PATTERN)
    schedule_start_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    schedule_end_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    schedule_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    auto_poll_enabled: bool | None = None
    enabled: bool | None = None


class FliggyWatchGroupPatch(BaseModel):
    name: str | None = None
    brief_title: str | None = Field(default=None, max_length=128)
    shop_key: str | None = Field(default=None, pattern=r"^$|^shop_[A-Za-z0-9_-]+$")
    # 允许空串=显式清空 legacy 挂载（v2 §2）。
    ctrip_watch_group_key: str | None = Field(default=None, pattern=r"^$" + "|" + CTRIP_WATCH_GROUP_KEY_PATTERN)
    feishu_chat_id: str | None = Field(default=None, max_length=128)
    feishu_webhook_url: str | None = Field(default=None, max_length=2048)
    browser_session_id: str | None = None
    timezone: str | None = None
    schedule_mode: str | None = Field(default=None, pattern=SCHEDULE_MODE_PATTERN)
    schedule_start_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    schedule_end_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    schedule_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    auto_poll_enabled: bool | None = None
    enabled: bool | None = None


@dataclass(frozen=True)
class FliggyWatchGroupRuntime:
    group_key: str
    name: str
    brief_title: str | None
    platform_account_id: str
    browser_session_id: str | None
    ctrip_watch_group_key: str | None
    timezone: str
    schedule_mode: str
    schedule_start_time: str
    schedule_end_time: str
    schedule_interval_minutes: int
    auto_poll_enabled: bool
    enabled: bool
    last_polled_at: datetime | None
    # v2 §2 直连归属与自有投递（旧调用方可省略）。
    shop_key: str | None = None
    feishu_chat_id: str | None = None
    feishu_webhook_url: str | None = None


async def get_fliggy_watch_group_runtime(
    *, watch_group_key: str
) -> FliggyWatchGroupRuntime | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
    return _group_to_runtime(group)


async def get_fliggy_watch_group_runtime_by_ctrip_anchor(
    *, ctrip_watch_group_key: str
) -> FliggyWatchGroupRuntime | None:
    await ensure_db_ready()
    async with session_scope() as session:
        # limit(1) + scalars().first() 容错：即使历史/手工改库残留重复锚点，
        # 也只取一条而非抛 MultipleResultsFound，保证 agent 授权解析路径不整体失败。
        group = (
            await session.scalars(
                select(FliggyEbookingWatchGroup)
                .where(
                    FliggyEbookingWatchGroup.ctrip_watch_group_key == ctrip_watch_group_key
                )
                .limit(1)
            )
        ).first()
        if group is None:
            return None
    return _group_to_runtime(group)


async def get_fliggy_watch_group_runtime_by_shop_key(
    *, shop_key: str
) -> FliggyWatchGroupRuntime | None:
    """v2 §2 店铺直连：按 shop_key 取该店飞猪组（唯一索引保证至多一个）。"""
    normalized = str(shop_key or "").strip()
    if not normalized:
        return None
    await ensure_db_ready()
    async with session_scope() as session:
        group = (
            await session.scalars(
                select(FliggyEbookingWatchGroup)
                .where(FliggyEbookingWatchGroup.shop_key == normalized)
                .limit(1)
            )
        ).first()
        if group is None:
            return None
    return _group_to_runtime(group)


async def list_fliggy_watch_groups() -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(FliggyEbookingWatchGroup).order_by(
                        FliggyEbookingWatchGroup.group_key.asc()
                    )
                )
            ).all()
        )
    return [_group_to_dict(group) for group in groups]


async def list_due_fliggy_watch_groups(
    *, now: datetime | None = None
) -> list[FliggyWatchGroupRuntime]:
    """enabled + auto_poll 的组里筛出 schedule due 的（共享 competitor due 逻辑）。"""
    await ensure_db_ready()
    current_time = (now or local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(FliggyEbookingWatchGroup)
                    .where(
                        FliggyEbookingWatchGroup.enabled.is_(True),
                        FliggyEbookingWatchGroup.auto_poll_enabled.is_(True),
                    )
                    .order_by(FliggyEbookingWatchGroup.group_key.asc())
                )
            ).all()
        )
    return [
        _group_to_runtime(group)
        for group in groups
        if watch_group_is_due(group, current_time)
    ]


async def create_fliggy_watch_group(
    request: FliggyWatchGroupCreate,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    anchor_key = _clean_text(request.ctrip_watch_group_key)
    async with session_scope() as session:
        existing = await _get_group(session, request.group_key)
        if existing is not None:
            return None
        anchor: CtripEbookingWatchGroup | None = None
        if anchor_key:
            anchor = await _require_ctrip_anchor(session, anchor_key)
            await _require_anchor_not_taken(session, anchor_key)
        browser_session_id = _clean_text(request.browser_session_id)
        platform_account_id = ""
        if browser_session_id:
            browser_session = _validate_fliggy_browser_session_id(browser_session_id)
            platform_account_id = browser_session.platform_account_id
        group_shop_key = await _resolve_group_shop_key(
            session,
            shop_key=request.shop_key,
            anchor=anchor,
            platform_account_id=platform_account_id or None,
            browser_session_id=browser_session_id or None,
        )
        group = FliggyEbookingWatchGroup(
            group_key=request.group_key,
            name=request.name.strip() or request.group_key,
            brief_title=_clean_text(request.brief_title),
            shop_key=group_shop_key,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id or None,
            ctrip_watch_group_key=anchor_key or None,
            feishu_chat_id=_clean_text(request.feishu_chat_id) or None,
            feishu_webhook_url=_clean_text(request.feishu_webhook_url) or None,
            timezone=_normalize_timezone(request.timezone),
            schedule_mode=_clean_text(request.schedule_mode) or "fixed_slots",
            schedule_start_time=_clean_text(request.schedule_start_time) or "06:00",
            schedule_end_time=_clean_text(request.schedule_end_time) or "01:00",
            schedule_interval_minutes=request.schedule_interval_minutes or 120,
            auto_poll_enabled=bool(request.auto_poll_enabled)
            if request.auto_poll_enabled is not None
            else False,
            enabled=bool(request.enabled) if request.enabled is not None else True,
        )
        session.add(group)
        await session.flush()
    return _group_to_dict(group)


async def _resolve_group_shop_key(
    session,
    *,
    shop_key: str | None,
    anchor: "CtripEbookingWatchGroup | None",
    exclude_group_key: str | None = None,
    platform_account_id: str | None = None,
    browser_session_id: str | None = None,
) -> str | None:
    """v2 §2 归属解析：显式 shop_key 优先；legacy 锚点挂载自动派生锚点店铺
    （迁移期兼容写）；两者都没有 → 422 必须归属店铺。校验店铺存在、每店
    每平台唯一、组账号与账号店铺归属一致。"""

    explicit = str(shop_key or "").strip() or None
    anchor_shop = str(getattr(anchor, "shop_key", "") or "").strip() or None
    if explicit is None and anchor_shop is None:
        raise ValueError(
            "飞猪监控组必须归属一个店铺（shop_key）；如需迁移旧挂载，请同时提供"
            " ctrip_watch_group_key（挂载的携程组已归属店铺时可自动派生）。"
        )
    resolved = explicit or anchor_shop
    if explicit and anchor_shop and explicit != anchor_shop:
        raise ValueError(
            f"shop_key（{explicit}）与挂载携程组的店铺（{anchor_shop}）不一致，"
            "请先统一归属。"
        )
    from app.domain.shops import service as shops_service
    from app.domain.shops.service import ShopNotFound

    try:
        await shops_service.get_shop(resolved)
    except ShopNotFound:
        raise ValueError(f"店铺不存在：{resolved}，请先创建店铺。") from None
    query = select(FliggyEbookingWatchGroup).where(
        FliggyEbookingWatchGroup.shop_key == resolved
    )
    if exclude_group_key:
        query = query.where(FliggyEbookingWatchGroup.group_key != exclude_group_key)
    conflict = (await session.scalars(query.limit(1))).first()
    if conflict is not None:
        raise ValueError(
            f"店铺已绑定飞猪监控组：{conflict.group_key}（每店最多一个）。"
        )
    account_hint = str(platform_account_id or "").strip() or str(
        browser_session_id or ""
    ).strip()
    if account_hint:
        from app.domain.browser.sessions import get_browser_session_for_platform_account

        bound = get_browser_session_for_platform_account(account_hint)
        if bound is not None and bound.intended_use == "ebk":
            bound_shop = str(bound.shop_key or "").strip()
            if bound_shop and bound_shop != resolved:
                raise ValueError(
                    f"浏览器账号 {bound.platform_account_id} 已归属其他店铺"
                    f"（{bound_shop}），与监控组店铺归属不一致。"
                )
    return resolved


async def update_fliggy_watch_group(
    *, watch_group_key: str, patch: FliggyWatchGroupPatch
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
        anchor: CtripEbookingWatchGroup | None = None
        if patch.ctrip_watch_group_key is not None:
            anchor_key = patch.ctrip_watch_group_key.strip()
            if anchor_key and anchor_key != (group.ctrip_watch_group_key or ""):
                anchor = await _require_ctrip_anchor(session, anchor_key)
                await _require_anchor_not_taken(
                    session, anchor_key, exclude_group_key=group.group_key
                )
                group.ctrip_watch_group_key = anchor_key
            elif not anchor_key:
                # 允许显式清空 legacy 挂载（v2 §2：新写入一律 shop_key）。
                group.ctrip_watch_group_key = None
        if patch.feishu_chat_id is not None:
            group.feishu_chat_id = patch.feishu_chat_id.strip() or None
        if patch.feishu_webhook_url is not None:
            group.feishu_webhook_url = patch.feishu_webhook_url.strip() or None
        if patch.shop_key is not None:
            explicit = patch.shop_key.strip() or None
            if explicit is None:
                # 显式置空店铺归属：只有纯挂载旧组允许（回到未分组待补绑）。
                if group.ctrip_watch_group_key:
                    raise ValueError(
                        "该组仍挂载携程组，不能清空店铺归属；请先归属店铺或清空挂载。"
                    )
                group.shop_key = None
            else:
                if anchor is not None:
                    anchor_shop = str(getattr(anchor, "shop_key", "") or "").strip()
                    if anchor_shop and anchor_shop != explicit:
                        raise ValueError(
                            f"shop_key（{explicit}）与挂载携程组的店铺（{anchor_shop}）"
                            "不一致，请先统一归属。"
                        )
                group.shop_key = await _resolve_group_shop_key(
                    session,
                    shop_key=explicit,
                    anchor=None,
                    exclude_group_key=group.group_key,
                    platform_account_id=group.platform_account_id or None,
                    browser_session_id=group.browser_session_id,
                )
        elif anchor is not None:
            # 仅改挂载锚点：legacy 键变更不动店铺归属；锚点有店且组未归属时
            # 顺手派生（迁移期兼容），已归属的直接保留（店铺为权威）。
            anchor_shop = str(getattr(anchor, "shop_key", "") or "").strip() or None
            if anchor_shop and not str(group.shop_key or "").strip():
                group.shop_key = await _resolve_group_shop_key(
                    session,
                    shop_key=anchor_shop,
                    anchor=None,
                    exclude_group_key=group.group_key,
                    platform_account_id=group.platform_account_id or None,
                    browser_session_id=group.browser_session_id,
                )
        if patch.name is not None:
            group.name = patch.name.strip() or group.name
        if patch.brief_title is not None:
            group.brief_title = patch.brief_title.strip() or None
        if patch.browser_session_id is not None:
            browser_session_id = patch.browser_session_id.strip()
            if browser_session_id:
                browser_session = _validate_fliggy_browser_session_id(browser_session_id)
                group.browser_session_id = browser_session.browser_session_id
                group.platform_account_id = browser_session.platform_account_id
            else:
                group.browser_session_id = None
                group.platform_account_id = ""
        if patch.timezone is not None:
            group.timezone = _normalize_timezone(patch.timezone)
        if patch.schedule_mode is not None:
            group.schedule_mode = patch.schedule_mode.strip()
        if patch.schedule_start_time is not None:
            group.schedule_start_time = patch.schedule_start_time.strip()
        if patch.schedule_end_time is not None:
            group.schedule_end_time = patch.schedule_end_time.strip()
        if patch.schedule_interval_minutes is not None:
            group.schedule_interval_minutes = patch.schedule_interval_minutes
        if patch.auto_poll_enabled is not None:
            group.auto_poll_enabled = patch.auto_poll_enabled
        if patch.enabled is not None:
            group.enabled = patch.enabled
        await session.flush()
    return _group_to_dict(group)


async def delete_fliggy_watch_group(
    *, watch_group_key: str
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
        payload = _group_to_dict(group)
        await session.delete(group)
        await session.flush()
    return payload


async def delete_fliggy_watch_groups_by_ctrip_anchor(
    *, ctrip_watch_group_key: str
) -> list[dict[str, Any]]:
    """携程组删除时的级联清理：只删纯挂载（shop_key 为空）的旧组；
    v2 §2 直连组与携程组解耦，锚点删除后保留（legacy 键置空）。"""

    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(FliggyEbookingWatchGroup).where(
                        FliggyEbookingWatchGroup.ctrip_watch_group_key
                        == ctrip_watch_group_key,
                        FliggyEbookingWatchGroup.shop_key.is_(None),
                    )
                )
            ).all()
        )
        payloads = [_group_to_dict(group) for group in groups]
        for group in groups:
            await session.delete(group)
        await session.flush()
        # 直连组仅解除 legacy 挂载键，店铺归属与调度不动。
        detached = (
            await session.scalars(
                select(FliggyEbookingWatchGroup).where(
                    FliggyEbookingWatchGroup.ctrip_watch_group_key
                    == ctrip_watch_group_key
                )
            )
        ).all()
        for group in detached:
            group.ctrip_watch_group_key = None
        await session.flush()
    return payloads


async def mark_fliggy_watch_group_polled(
    *,
    watch_group_key: str,
    captured_at: datetime | None = None,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await _get_group(session, watch_group_key)
        if group is None:
            return None
        group.last_polled_at = (captured_at or local_now()).replace(tzinfo=None)
    return _group_to_dict(group)


async def clear_fliggy_watch_bindings_for_browser_session(
    *,
    platform_account_id: str,
    browser_session_id: str,
) -> int:
    """浏览器账号删除时解绑：保留锚点与调度配置，停用自动轮询（对照携程钩子）。"""

    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(FliggyEbookingWatchGroup).where(
                        or_(
                            FliggyEbookingWatchGroup.platform_account_id == platform_account_id,
                            FliggyEbookingWatchGroup.browser_session_id == browser_session_id,
                        )
                    )
                )
            ).all()
        )
        for group in groups:
            # 仅解绑账号，保留 enabled/auto_poll：账号删除后下一轮定时 tick 读取会
            # 因缺账号失败，走 60 分钟失败告警冷却向锚定携程组机器人提醒（设计 ③：
            # 失败态走携程组机器人、消除静默），而非停用后悄无声息。
            group.platform_account_id = ""
            group.browser_session_id = None
        await session.flush()
    return len(groups)


async def snapshot_fliggy_watch_bindings_for_browser_session(
    *,
    platform_account_id: str,
    browser_session_id: str,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        groups = list(
            (
                await session.scalars(
                    select(FliggyEbookingWatchGroup).where(
                        or_(
                            FliggyEbookingWatchGroup.platform_account_id == platform_account_id,
                            FliggyEbookingWatchGroup.browser_session_id == browser_session_id,
                        )
                    )
                )
            ).all()
        )
        return [
            {
                "group_key": group.group_key,
                "platform_account_id": group.platform_account_id,
                "browser_session_id": group.browser_session_id,
                "enabled": group.enabled,
                "auto_poll_enabled": group.auto_poll_enabled,
            }
            for group in groups
        ]


async def restore_fliggy_watch_bindings(snapshots: list[dict[str, Any]]) -> int:
    if not snapshots:
        return 0
    await ensure_db_ready()
    async with session_scope() as session:
        restored = 0
        for snapshot in snapshots:
            group_key = snapshot.get("group_key")
            if not isinstance(group_key, str) or not group_key:
                continue
            group = await _get_group(session, group_key)
            if group is None:
                continue
            group.platform_account_id = str(snapshot.get("platform_account_id") or "")
            browser_session_id = snapshot.get("browser_session_id")
            group.browser_session_id = str(browser_session_id) if browser_session_id else None
            group.enabled = bool(snapshot.get("enabled"))
            group.auto_poll_enabled = bool(snapshot.get("auto_poll_enabled"))
            restored += 1
        await session.flush()
    return restored


async def migrate_fliggy_watch_bindings_for_browser_session(
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
                    select(FliggyEbookingWatchGroup).where(
                        or_(
                            FliggyEbookingWatchGroup.platform_account_id == old_platform_account_id,
                            FliggyEbookingWatchGroup.browser_session_id == browser_session_id,
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


async def _get_group(session, watch_group_key: str) -> FliggyEbookingWatchGroup | None:
    return await session.scalar(
        select(FliggyEbookingWatchGroup).where(
            FliggyEbookingWatchGroup.group_key == watch_group_key
        )
    )


async def _require_ctrip_anchor(session, ctrip_watch_group_key: str):
    anchor = await session.scalar(
        select(CtripEbookingWatchGroup).where(
            CtripEbookingWatchGroup.group_key == ctrip_watch_group_key
        )
    )
    if anchor is None:
        raise ValueError(
            f"未找到携程 EBK 监控组：{ctrip_watch_group_key}，请先在携程 EBK 控制台创建。"
        )
    return anchor


async def _require_anchor_not_taken(
    session, ctrip_watch_group_key: str, *, exclude_group_key: str | None = None
) -> None:
    query = (
        select(FliggyEbookingWatchGroup)
        .where(FliggyEbookingWatchGroup.ctrip_watch_group_key == ctrip_watch_group_key)
        .limit(1)
    )
    taken = (await session.scalars(query)).first()
    if taken is not None and taken.group_key != exclude_group_key:
        raise ValueError(
            "该携程监控组已绑定飞猪定时监控（一个携程组只能挂一个飞猪排程）。"
        )


def _group_to_runtime(group: FliggyEbookingWatchGroup) -> FliggyWatchGroupRuntime:
    return FliggyWatchGroupRuntime(
        group_key=group.group_key,
        name=group.name,
        brief_title=group.brief_title,
        platform_account_id=group.platform_account_id,
        browser_session_id=group.browser_session_id,
        shop_key=group.shop_key,
        ctrip_watch_group_key=group.ctrip_watch_group_key,
        feishu_chat_id=group.feishu_chat_id,
        feishu_webhook_url=group.feishu_webhook_url,
        timezone=group.timezone,
        schedule_mode=group.schedule_mode,
        schedule_start_time=group.schedule_start_time,
        schedule_end_time=group.schedule_end_time,
        schedule_interval_minutes=group.schedule_interval_minutes,
        auto_poll_enabled=group.auto_poll_enabled,
        enabled=group.enabled,
        last_polled_at=group.last_polled_at,
    )


def _group_to_dict(group: FliggyEbookingWatchGroup) -> dict[str, Any]:
    return {
        "id": group.id,
        "group_key": group.group_key,
        "name": group.name,
        "brief_title": group.brief_title,
        "shop_key": group.shop_key,
        "platform_account_id": group.platform_account_id,
        "browser_session_id": group.browser_session_id,
        "session_status": _session_status_label(group.browser_session_id),
        "ctrip_watch_group_key": group.ctrip_watch_group_key,
        "anchor_status": "anchored" if group.ctrip_watch_group_key else "legacy_unanchored",
        "feishu_chat_id": group.feishu_chat_id,
        "feishu_webhook_url_configured": bool(group.feishu_webhook_url),
        "timezone": group.timezone,
        "schedule_mode": group.schedule_mode,
        "schedule_start_time": group.schedule_start_time,
        "schedule_end_time": group.schedule_end_time,
        "schedule_interval_minutes": group.schedule_interval_minutes,
        "auto_poll_enabled": group.auto_poll_enabled,
        "enabled": group.enabled,
        "last_polled_at": group.last_polled_at.isoformat()
        if group.last_polled_at
        else None,
        "created_at": group.created_at.isoformat(),
        "updated_at": group.updated_at.isoformat(),
    }


def _session_status_label(browser_session_id: str | None) -> str:
    if not browser_session_id:
        return "none"
    try:
        get_browser_session_store().get(browser_session_id)
        return "registered"
    except KeyError:
        return "unregistered"


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_timezone(value: Any) -> str:
    timezone = _clean_text(value) or SUPPORTED_TIMEZONE
    if timezone != SUPPORTED_TIMEZONE:
        raise ValueError(f"当前 EBK 定时监控仅支持 {SUPPORTED_TIMEZONE} 时区。")
    return timezone


def _validate_fliggy_browser_session_id(browser_session_id: str) -> BrowserSession:
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise ValueError(f"浏览器账号不存在：{browser_session_id}") from exc
    if session.platform != "fliggy":
        raise ValueError("飞猪 EBK 监控组只能绑定飞猪浏览器账号。")
    if session.intended_use != "ebk":
        raise ValueError("飞猪 EBK 监控组只能绑定用途为 EBK 后台的浏览器账号。")
    return session
