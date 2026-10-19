"""促销调整执行链后台任务：预检 -> 页面核验空跑 -> 写入立减。

与 pyramid_bidding.pipeline 同构；每段失败都会向发起审批的飞书群
回推阻断卡片。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from app.domain.notifications.feishu_client import FeishuBotClient

logger = logging.getLogger(__name__)

PROMOTION_PIPELINE_UNEXPECTED_FAILURE_MESSAGE = "促销调整执行链发生未预期异常，已停止自动调整促销立减。"


async def run_promotion_adjust_execution_pipeline(
    *,
    approval_record_id: int,
    chat_id: str | None = None,
) -> None:
    from app.domain.promotion_adjust.cards import (
        build_promotion_execution_blocked_card,
        build_promotion_execution_blocked_fallback_text,
        build_promotion_execution_result_card,
        build_promotion_execution_result_fallback_text,
    )
    from app.domain.promotion_adjust.execution import (
        execute_promotion_adjust_write,
        prepare_promotion_adjust_execution_preflight,
        prepare_promotion_adjust_page_write_dry_run,
    )

    preflight = await prepare_promotion_adjust_execution_preflight(
        approval_record_id=approval_record_id,
        actor_id="feishu-auto-chain",
    )
    if preflight.status != "preflight_ready":
        card = build_promotion_execution_blocked_card(
            blocked_step="促销调整执行预检",
            blocked_status=preflight.status,
            blocked_summary=preflight.summary,
            next_action=preflight.next_action or "",
            audit_record_id=preflight.execution_record_id,
            evidence=preflight.evidence,
        )
        await _send_pipeline_card(
            card,
            chat_id=chat_id,
            fallback_text=build_promotion_execution_blocked_fallback_text(card),
        )
        return

    dry_run = await prepare_promotion_adjust_page_write_dry_run(
        execution_record_id=int(preflight.execution_record_id or 0),
        actor_id="feishu-auto-chain",
    )
    if dry_run.status != "page_write_preview_ready":
        card = build_promotion_execution_blocked_card(
            blocked_step="促销页面核验空跑",
            blocked_status=dry_run.status,
            blocked_summary=dry_run.summary,
            next_action=dry_run.next_action or "",
            audit_record_id=dry_run.execution_record_id,
            evidence=dry_run.evidence,
        )
        await _send_pipeline_card(
            card,
            chat_id=chat_id,
            fallback_text=build_promotion_execution_blocked_fallback_text(card),
        )
        return

    execute = await execute_promotion_adjust_write(
        execution_record_id=int(dry_run.execution_record_id or 0),
        actor_id="feishu-auto-chain",
    )
    payload = execute.model_dump(mode="json")
    if execute.status in {"executed", "success"}:
        card = build_promotion_execution_result_card(payload)
        await _send_pipeline_card(
            card,
            chat_id=chat_id,
            fallback_text=build_promotion_execution_result_fallback_text(payload),
        )
        return
    card = build_promotion_execution_blocked_card(
        blocked_step="促销立减写入",
        blocked_status=execute.status,
        blocked_summary=execute.summary,
        next_action=execute.next_action or "",
        audit_record_id=execute.execution_record_id,
        evidence=execute.evidence,
    )
    await _send_pipeline_card(
        card,
        chat_id=chat_id,
        fallback_text=build_promotion_execution_blocked_fallback_text(card),
    )


async def run_promotion_adjust_execution_pipeline_guarded(
    *,
    approval_record_id: int,
    chat_id: str | None = None,
) -> None:
    from app.domain.promotion_adjust.approvals import update_promotion_approval_pipeline_status
    from app.domain.promotion_adjust.cards import (
        build_promotion_execution_blocked_card,
        build_promotion_execution_blocked_fallback_text,
    )

    await update_promotion_approval_pipeline_status(approval_record_id, status="running")
    try:
        await run_promotion_adjust_execution_pipeline(
            approval_record_id=approval_record_id,
            chat_id=chat_id,
        )
        await update_promotion_approval_pipeline_status(approval_record_id, status="completed")
    except Exception as exc:
        logger.error(
            "promotion adjust execution pipeline failed unexpectedly for approval %s (%s)",
            approval_record_id,
            type(exc).__name__,
        )
        await update_promotion_approval_pipeline_status(
            approval_record_id,
            status="failed",
            error_type=type(exc).__name__,
            error_message=PROMOTION_PIPELINE_UNEXPECTED_FAILURE_MESSAGE,
        )
        card = build_promotion_execution_blocked_card(
            blocked_step="促销调整执行链",
            blocked_status="pipeline_failed",
            blocked_summary=PROMOTION_PIPELINE_UNEXPECTED_FAILURE_MESSAGE,
            next_action="请查看飞书群通知或联系运维；如需继续调整，请重新发起审批。",
            audit_record_id=approval_record_id,
            evidence={"error_type": type(exc).__name__},
        )
        await _send_pipeline_card(
            card,
            chat_id=chat_id,
            fallback_text=build_promotion_execution_blocked_fallback_text(card),
        )


def queue_promotion_adjust_execution_pipeline(
    *,
    approval_record_id: int,
    chat_id: str | None = None,
) -> bool:
    _schedule_background_task(
        run_promotion_adjust_execution_pipeline_guarded(
            approval_record_id=approval_record_id,
            chat_id=chat_id,
        )
    )
    return True


def _schedule_background_task(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    task = asyncio.create_task(coro)
    task.add_done_callback(_log_background_task_outcome)
    return task


def _log_background_task_outcome(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        logger.warning("promotion adjust execution background task was cancelled")
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "promotion adjust execution background task failed (%s)",
            type(exc).__name__,
        )


async def _send_pipeline_card(
    card: dict[str, Any],
    *,
    chat_id: str | None = None,
    fallback_text: str | None = None,
) -> dict[str, Any]:
    if not chat_id:
        return {
            "status": "skipped",
            "summary": "缺少飞书群聊上下文，促销调整通知卡片未发送。",
            "evidence": {"feishu_chat_id_present": False},
        }
    try:
        return await FeishuBotClient(chat_id=chat_id).send_interactive_card(
            chat_id,
            card,
            fallback_text=fallback_text,
        )
    except Exception as exc:
        logger.error(
            "failed to send promotion adjust execution pipeline card (%s)",
            type(exc).__name__,
        )
        return {
            "status": "failed",
            "summary": "促销调整执行结果卡片发送失败。",
            "evidence": {"error_type": type(exc).__name__},
        }
