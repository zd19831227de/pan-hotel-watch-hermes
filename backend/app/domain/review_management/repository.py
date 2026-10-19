from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select

from app.db.session import ensure_db_ready, session_scope

from .models import (
    ReviewAutoReplyConfig,
    ReviewDailyCardPush,
    ReviewItem,
    ReviewStoreBinding,
    ReviewTask,
    ReplyTemplate,
    local_now,
)
from .types import (
    GOOD_SCORE_MIN,
    MEDIUM_SCORE_MIN,
    ReplyTemplateInput,
    ReviewAutoConfigPatch,
    ReviewItemInput,
    classify_review_score,
)

_REVIEW_PK_COLUMNS = (
    ReviewItem.platform,
    ReviewItem.hotel_id,
    ReviewItem.review_id,
)


async def get_review_auto_config(*, platform: str) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        config = await _get_or_create_config(session, platform=platform)
        return _config_to_dict(config)


async def upsert_store_binding(
    *,
    platform: str,
    browser_session_id: str,
    platform_account_id: str,
    display_name: str = "",
    feishu_chat_id: str | None = None,
) -> dict[str, Any]:
    """创建/刷新店铺绑定（账号信息以浏览器会话为准，配置字段保留已存值）。"""
    await ensure_db_ready()
    async with session_scope() as session:
        binding = (
            await session.execute(
                select(ReviewStoreBinding).where(
                    ReviewStoreBinding.browser_session_id == browser_session_id
                )
            )
        ).scalar_one_or_none()
        if binding is None:
            binding = ReviewStoreBinding(
                platform=platform,
                browser_session_id=browser_session_id,
                platform_account_id=platform_account_id,
                display_name=display_name,
                feishu_chat_id=feishu_chat_id,
            )
            session.add(binding)
        else:
            # 账号侧信息变更跟随浏览器会话；店铺配置不被覆盖。
            binding.platform = platform
            binding.platform_account_id = platform_account_id
            if display_name:
                binding.display_name = display_name
            if feishu_chat_id is not None:
                binding.feishu_chat_id = feishu_chat_id
        await session.flush()
        return _binding_to_dict(binding)


async def update_store_config(
    *,
    platform: str,
    browser_session_id: str,
    patch: ReviewAutoConfigPatch,
) -> dict[str, Any]:
    """更新店铺绑定的自动回复配置；绑定不存在时先按平台默认创建。"""
    await ensure_db_ready()
    if not patch.model_dump(exclude_unset=True):
        raise ValueError("没有需要更新的自动回复配置字段。")
    async with session_scope() as session:
        binding = (
            await session.execute(
                select(ReviewStoreBinding).where(
                    ReviewStoreBinding.browser_session_id == browser_session_id
                )
            )
        ).scalar_one_or_none()
        if binding is None:
            binding = ReviewStoreBinding(
                platform=platform, browser_session_id=browser_session_id
            )
            session.add(binding)
        data = patch.model_dump(exclude_unset=True)
        if "approval_chat_id" in data:
            # 店铺绑定的飞书推送目标只有 feishu_chat_id 一个字段；
            # 空字符串 = 清除，未出现在 patch = 保持不变。
            binding.feishu_chat_id = data.pop("approval_chat_id") or None
        for field, value in data.items():
            setattr(binding, field, value)
        await session.flush()
        return _binding_to_dict(binding)


async def list_store_bindings(
    *, platform: str | None = None
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(ReviewStoreBinding)
        if platform:
            stmt = stmt.where(ReviewStoreBinding.platform == platform)
        stmt = stmt.order_by(ReviewStoreBinding.id.asc())
        rows = (await session.execute(stmt)).scalars().all()
        return [_binding_to_dict(row) for row in rows]


async def get_store_binding(*, browser_session_id: str) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        binding = (
            await session.execute(
                select(ReviewStoreBinding).where(
                    ReviewStoreBinding.browser_session_id == browser_session_id
                )
            )
        ).scalar_one_or_none()
        return _binding_to_dict(binding) if binding is not None else None


async def get_effective_review_config(
    *,
    platform: str,
    browser_session_id: str | None = None,
) -> dict[str, Any]:
    """店铺配置（绑定存在时）优先；否则回落平台级旧配置（browser_session_id=None）。

    返回字典统一暴露 approval_chat_id（semi_auto 推送目标）：店铺绑定时取
    feishu_chat_id，保持 service 层单一字段语义。
    """
    normalized = str(browser_session_id or "").strip() or None
    if normalized is not None:
        binding = await get_store_binding(browser_session_id=normalized)
        if binding is not None and binding["platform"] == platform:
            return {
                "browser_session_id": binding["browser_session_id"],
                "platform": binding["platform"],
                "platform_account_id": binding["platform_account_id"],
                "display_name": binding["display_name"],
                "auto_reply_mode": binding["auto_reply_mode"],
                "auto_fetch_enabled": bool(binding["auto_fetch_enabled"]),
                "fetch_interval_minutes": binding["fetch_interval_minutes"],
                "auto_process_time": binding["auto_process_time"],
                "last_auto_processed_at": binding.get("last_auto_processed_at"),
                "approval_chat_id": binding["feishu_chat_id"],
                "feishu_chat_id": binding["feishu_chat_id"],
                "last_fetched_at": binding["last_fetched_at"],
                "last_pending_count": binding.get("last_pending_count"),
                "updated_at": binding["updated_at"],
            }
    config = await get_review_auto_config(platform=platform)
    config["browser_session_id"] = None
    return config


async def update_review_auto_config(
    *,
    platform: str,
    patch: ReviewAutoConfigPatch,
) -> dict[str, Any]:
    await ensure_db_ready()
    if not patch.model_dump(exclude_unset=True):
        raise ValueError("没有需要更新的自动回复配置字段。")
    async with session_scope() as session:
        config = await _get_or_create_config(session, platform=platform)
        data = patch.model_dump(exclude_unset=True)
        if data.get("approval_chat_id") == "":
            data["approval_chat_id"] = None
        for field, value in data.items():
            setattr(config, field, value)
        await session.flush()
        return _config_to_dict(config)


async def set_last_fetched_at(
    *,
    platform: str,
    browser_session_id: str | None = None,
    pending_count: int | None = None,
) -> dict[str, Any]:
    await ensure_db_ready()
    normalized = str(browser_session_id or "").strip() or None
    async with session_scope() as session:
        if normalized is not None:
            binding = (
                await session.execute(
                    select(ReviewStoreBinding).where(
                        ReviewStoreBinding.browser_session_id == normalized
                    )
                )
            ).scalar_one_or_none()
            if binding is not None:
                binding.last_fetched_at = local_now()
                if pending_count is not None:
                    binding.last_pending_count = pending_count
                await session.flush()
                return _binding_to_dict(binding)
        config = await _get_or_create_config(session, platform=platform)
        config.last_fetched_at = local_now()
        await session.flush()
        return _config_to_dict(config)


async def mark_auto_processed(
    *,
    platform: str,
    browser_session_id: str | None = None,
    processed_at: datetime | None = None,
) -> None:
    """写当日定时触发游标：成功后当日不再触发（与携程简报同口径）。"""
    await ensure_db_ready()
    normalized = str(browser_session_id or "").strip() or None
    marker = (processed_at or local_now()).replace(tzinfo=None)
    async with session_scope() as session:
        if normalized is not None:
            binding = (
                await session.execute(
                    select(ReviewStoreBinding).where(
                        ReviewStoreBinding.browser_session_id == normalized
                    )
                )
            ).scalar_one_or_none()
            if binding is not None:
                binding.last_auto_processed_at = marker
                await session.flush()
                return
        config = await _get_or_create_config(session, platform=platform)
        config.last_auto_processed_at = marker
        await session.flush()


async def upsert_review_items(
    *,
    platform: str,
    hotel_id: str,
    items: list[ReviewItemInput],
    browser_session_id: str | None = None,
) -> dict[str, Any]:
    await ensure_db_ready()
    session_id = str(browser_session_id or "").strip() or None
    inserted = 0
    updated = 0
    async with session_scope() as session:
        for item in items:
            stmt = select(ReviewItem).where(
                ReviewItem.platform == platform,
                ReviewItem.hotel_id == hotel_id,
                ReviewItem.review_id == item.review_id,
            )
            if session_id is not None:
                stmt = stmt.where(ReviewItem.browser_session_id == session_id)
            else:
                stmt = stmt.where(ReviewItem.browser_session_id.is_(None))
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                session.add(
                    ReviewItem(
                        platform=platform,
                        browser_session_id=session_id,
                        hotel_id=hotel_id,
                        review_id=item.review_id,
                        user_nickname=item.user_nickname,
                        score=item.score,
                        content=item.content,
                        review_time=item.review_time,
                        reply_status=item.reply_status or "pending",
                        reply_content=item.reply_content,
                        replied_at=local_now() if item.reply_status == "replied" else None,
                    )
                )
                inserted += 1
                continue
            updated += _apply_review_item_update(existing, item)
        count_stmt = select(func.count()).select_from(ReviewItem).where(
            ReviewItem.platform == platform,
            ReviewItem.hotel_id == hotel_id,
        )
        if session_id is not None:
            count_stmt = count_stmt.where(ReviewItem.browser_session_id == session_id)
        total = (await session.execute(count_stmt)).scalar_one()
        await session.flush()
    return {"inserted": inserted, "updated": updated, "total": total}


def _apply_review_item_update(existing: ReviewItem, item: ReviewItemInput) -> int:
    """Merge adapter data into an existing row; replied rows are never reset."""
    changed = False
    if item.user_nickname is not None and item.user_nickname != existing.user_nickname:
        existing.user_nickname = item.user_nickname
        changed = True
    if item.score is not None and item.score != existing.score:
        existing.score = item.score
        changed = True
    if item.content is not None and item.content != existing.content:
        existing.content = item.content
        changed = True
    if item.review_time is not None and item.review_time != existing.review_time:
        existing.review_time = item.review_time
        changed = True
    if item.reply_status == "replied" and existing.reply_status != "replied":
        existing.reply_status = "replied"
        if item.reply_content:
            existing.reply_content = item.reply_content
        existing.replied_at = local_now()
        changed = True
    elif item.reply_content and item.reply_content != existing.reply_content:
        existing.reply_content = item.reply_content
        changed = True
    return 1 if changed else 0


async def list_reviews(
    *,
    platform: str,
    reply_status: str | None = None,
    category: str | None = None,
    hotel_id: str | None = None,
    browser_session_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(ReviewItem).where(ReviewItem.platform == platform)
        if browser_session_id:
            stmt = stmt.where(ReviewItem.browser_session_id == browser_session_id)
        if hotel_id:
            stmt = stmt.where(ReviewItem.hotel_id == hotel_id)
        if reply_status:
            stmt = stmt.where(ReviewItem.reply_status == reply_status)
        stmt = _apply_category_filter(stmt, category)
        stmt = stmt.order_by(ReviewItem.id.desc()).limit(max(1, min(limit, 500)))
        rows = (await session.execute(stmt)).scalars().all()
        return [_review_to_dict(row) for row in rows]


def _apply_category_filter(stmt, category: str | None):
    if category == "good":
        return stmt.where(ReviewItem.score >= GOOD_SCORE_MIN)
    if category == "medium":
        return stmt.where(
            ReviewItem.score >= MEDIUM_SCORE_MIN,
            ReviewItem.score < GOOD_SCORE_MIN,
        )
    if category == "bad":
        return stmt.where(ReviewItem.score < MEDIUM_SCORE_MIN)
    return stmt


async def save_template(
    *, template: ReplyTemplateInput, browser_session_id: str | None = None
) -> dict[str, Any]:
    await ensure_db_ready()
    # 显式参数优先于模板载荷里的 browser_session_id（服务层调用约定）。
    session_id = (
        str(browser_session_id or "").strip()
        or str(template.browser_session_id or "").strip()
        or None
    )
    async with session_scope() as session:
        row: ReplyTemplate | None = None
        if template.id is not None:
            row = await session.get(ReplyTemplate, template.id)
            if row is not None and row.platform != template.platform:
                raise ValueError("模板平台不一致，不能跨平台修改。")
        if row is None:
            row = ReplyTemplate(
                platform=template.platform,
                browser_session_id=session_id,
                category=template.category,
                name=template.name,
                content=template.content,
                score_range_json=template.score_range,
                priority=template.priority,
                enabled=template.enabled,
                source=template.source,
            )
            session.add(row)
        else:
            row.category = template.category
            row.name = template.name
            row.content = template.content
            row.score_range_json = template.score_range
            row.priority = template.priority
            row.enabled = template.enabled
            if template.source != "manual":
                row.source = template.source
        await session.flush()
        return _template_to_dict(row)


async def list_templates(
    *,
    platform: str,
    category: str | None = None,
    only_enabled: bool = False,
    browser_session_id: str | None = None,
) -> list[dict[str, Any]]:
    """店铺维度查模板：该店铺模板 + 平台级通用模板（browser_session_id 为 NULL）。

    平台级模板对所有店铺生效（店铺未配齐好/中/差时的兜底）；
    「自动更新评价」的覆盖只清店铺自己的模板，不动平台级模板。
    """
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(ReplyTemplate).where(ReplyTemplate.platform == platform)
        if browser_session_id:
            stmt = stmt.where(
                or_(
                    ReplyTemplate.browser_session_id == browser_session_id,
                    ReplyTemplate.browser_session_id.is_(None),
                )
            )
            if category:
                stmt = stmt.where(ReplyTemplate.category == category)
            if only_enabled:
                stmt = stmt.where(ReplyTemplate.enabled.is_(True))
            # 店铺模板排前（同优先级时店铺自有优先于平台兜底）。
            stmt = stmt.order_by(
                ReplyTemplate.browser_session_id.is_(None).asc(),
                ReplyTemplate.priority.asc(),
                ReplyTemplate.id.asc(),
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_template_to_dict(row) for row in rows]
        if category:
            stmt = stmt.where(ReplyTemplate.category == category)
        if only_enabled:
            stmt = stmt.where(ReplyTemplate.enabled.is_(True))
        stmt = stmt.order_by(ReplyTemplate.priority.asc(), ReplyTemplate.id.asc())
        rows = (await session.execute(stmt)).scalars().all()
        return [_template_to_dict(row) for row in rows]


async def delete_templates_for_store(
    *,
    platform: str,
    browser_session_id: str,
    category: str | None = None,
) -> int:
    """删除某店铺的模板（「自动更新评价」覆盖式重建用）。

    category 为 None 删除全部；指定类别时只删该类别——抓取样本缺失的类别
    不能连带清空，否则店铺会丢失该类别全部可用模板。
    """
    await ensure_db_ready()
    async with session_scope() as session:
        conditions = [
            ReplyTemplate.platform == platform,
            ReplyTemplate.browser_session_id == browser_session_id,
        ]
        if category is not None:
            conditions.append(ReplyTemplate.category == category)
        rows = (
            await session.execute(select(ReplyTemplate).where(*conditions))
        ).scalars().all()
        for row in rows:
            await session.delete(row)
        return len(rows)


async def delete_template(*, template_id: int) -> bool:
    await ensure_db_ready()
    async with session_scope() as session:
        row = await session.get(ReplyTemplate, template_id)
        if row is None:
            return False
        await session.delete(row)
        return True


async def create_review_task(
    *,
    platform: str,
    task_type: str,
    browser_session_id: str | None = None,
) -> dict[str, Any]:
    await ensure_db_ready()
    async with session_scope() as session:
        row = ReviewTask(
            platform=platform,
            browser_session_id=str(browser_session_id or "").strip() or None,
            task_type=task_type,
            status="running",
        )
        session.add(row)
        await session.flush()
        return _task_to_dict(row)


async def complete_review_task(
    *,
    task_id: int,
    status: str,
    review_count: int = 0,
    result_json: dict[str, Any] | None = None,
    error_msg: str | None = None,
) -> dict[str, Any]:
    if status not in {"completed", "failed"}:
        raise ValueError(f"不支持的任务状态：{status}")
    await ensure_db_ready()
    async with session_scope() as session:
        row = await session.get(ReviewTask, task_id)
        if row is None:
            raise ValueError("评价任务记录不存在。")
        row.status = status
        row.review_count = review_count
        row.result_json = result_json
        row.error_msg = error_msg
        row.completed_at = local_now()
        await session.flush()
        return _task_to_dict(row)


async def list_review_tasks(
    *,
    platform: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(ReviewTask)
        if platform:
            stmt = stmt.where(ReviewTask.platform == platform)
        stmt = stmt.order_by(ReviewTask.id.desc()).limit(max(1, min(limit, 100)))
        rows = (await session.execute(stmt)).scalars().all()
        return [_task_to_dict(row) for row in rows]


async def record_auto_process_outcome(
    *,
    platform: str,
    browser_session_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """落一条自动处理结果（task_type='auto_process'）：日报合并卡与审计共用。"""
    await ensure_db_ready()
    async with session_scope() as session:
        row = ReviewTask(
            platform=platform,
            browser_session_id=str(browser_session_id or "").strip() or None,
            task_type="auto_process",
            status="completed",
            review_count=int(payload.get("submitted") or 0),
            result_json=payload,
            completed_at=local_now(),
        )
        session.add(row)
        await session.flush()
        return _task_to_dict(row)


async def list_review_tasks_since(
    *,
    created_since: datetime,
    task_types: tuple[str, ...] | list[str],
    platform: str | None = None,
    browser_session_id: str | None = None,
) -> list[dict[str, Any]]:
    """按时间/类型/店铺取任务行（升序），调用方自行挑每店铺最新一条。"""
    await ensure_db_ready()
    async with session_scope() as session:
        stmt = select(ReviewTask).where(
            ReviewTask.task_type.in_(tuple(task_types)),
            ReviewTask.created_at >= created_since,
        )
        if platform:
            stmt = stmt.where(ReviewTask.platform == platform)
        if browser_session_id:
            stmt = stmt.where(
                ReviewTask.browser_session_id == str(browser_session_id).strip()
            )
        stmt = stmt.order_by(ReviewTask.id.asc())
        rows = (await session.execute(stmt)).scalars().all()
        return [_task_to_dict(row) for row in rows]


async def mark_daily_card_pushed(*, chat_id: str, push_date: str) -> bool:
    """日报卡当日去重：新写入返回 True；当日已推过返回 False。"""
    await ensure_db_ready()
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(ReviewDailyCardPush).where(
                    ReviewDailyCardPush.chat_id == chat_id,
                    ReviewDailyCardPush.push_date == push_date,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False
        session.add(ReviewDailyCardPush(chat_id=chat_id, push_date=push_date))
        await session.flush()
        return True


async def _get_or_create_config(session, *, platform: str) -> ReviewAutoReplyConfig:
    config = (
        await session.execute(
            select(ReviewAutoReplyConfig).where(ReviewAutoReplyConfig.platform == platform)
        )
    ).scalar_one_or_none()
    if config is None:
        config = ReviewAutoReplyConfig(platform=platform)
        session.add(config)
        await session.flush()
    return config


def _config_to_dict(config: ReviewAutoReplyConfig) -> dict[str, Any]:
    return {
        "platform": config.platform,
        "auto_reply_mode": config.auto_reply_mode,
        "auto_fetch_enabled": bool(config.auto_fetch_enabled),
        "fetch_interval_minutes": config.fetch_interval_minutes,
        "auto_process_time": config.auto_process_time,
        "last_auto_processed_at": _format_datetime(config.last_auto_processed_at),
        "approval_chat_id": config.approval_chat_id,
        "last_fetched_at": _format_datetime(config.last_fetched_at),
        "updated_at": _format_datetime(config.updated_at),
    }


def _binding_to_dict(binding: ReviewStoreBinding) -> dict[str, Any]:
    return {
        "platform": binding.platform,
        "browser_session_id": binding.browser_session_id,
        "platform_account_id": binding.platform_account_id,
        "display_name": binding.display_name,
        "feishu_chat_id": binding.feishu_chat_id,
        "auto_reply_mode": binding.auto_reply_mode,
        "auto_fetch_enabled": bool(binding.auto_fetch_enabled),
        "fetch_interval_minutes": binding.fetch_interval_minutes,
        "auto_process_time": binding.auto_process_time,
        "last_auto_processed_at": _format_datetime(binding.last_auto_processed_at),
        "last_fetched_at": _format_datetime(binding.last_fetched_at),
        "last_pending_count": binding.last_pending_count,
        "enabled": bool(binding.enabled),
        "updated_at": _format_datetime(binding.updated_at),
    }


def _review_to_dict(row: ReviewItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform": row.platform,
        "browser_session_id": row.browser_session_id,
        "hotel_id": row.hotel_id,
        "review_id": row.review_id,
        "user_nickname": row.user_nickname,
        "score": row.score,
        "content": row.content,
        "review_time": row.review_time,
        "reply_status": row.reply_status,
        "reply_content": row.reply_content,
        "replied_at": _format_datetime(row.replied_at),
        "category": classify_review_score(row.score),
        "created_at": _format_datetime(row.created_at),
        "updated_at": _format_datetime(row.updated_at),
    }


def _template_to_dict(row: ReplyTemplate) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform": row.platform,
        "browser_session_id": row.browser_session_id,
        "category": row.category,
        "name": row.name,
        "content": row.content,
        "score_range": row.score_range_json,
        "priority": row.priority,
        "enabled": bool(row.enabled),
        "source": row.source,
        "created_at": _format_datetime(row.created_at),
        "updated_at": _format_datetime(row.updated_at),
    }


def _task_to_dict(row: ReviewTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform": row.platform,
        "browser_session_id": row.browser_session_id,
        "task_type": row.task_type,
        "status": row.status,
        "review_count": row.review_count,
        "result": row.result_json,
        "error_msg": row.error_msg,
        "created_at": _format_datetime(row.created_at),
        "completed_at": _format_datetime(row.completed_at),
    }


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value is not None else None
