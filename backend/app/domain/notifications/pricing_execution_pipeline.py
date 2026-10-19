from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from app.domain.notifications.feishu_client import FeishuBotClient

logger = logging.getLogger(__name__)

PIPELINE_UNEXPECTED_FAILURE_MESSAGE = "调价执行链发生未预期异常，已停止自动写价。"


async def run_pricing_execution_pipeline(
    *,
    approval_record_id: int,
    chat_id: str | None = None,
) -> None:
    from app.domain.notifications.pricing_execution_card import (
        build_pricing_execution_blocked_card,
        build_pricing_execution_blocked_fallback_text,
        build_pricing_execution_result_card,
        build_pricing_execution_result_fallback_text,
    )
    from app.domain.pricing_execution.service import (
        execute_pricing_page_write,
        prepare_pricing_execution_preflight,
        prepare_pricing_page_write_dry_run,
    )

    p6 = await prepare_pricing_execution_preflight(
        approval_record_id=approval_record_id,
        actor_id="feishu-auto-chain",
    )
    if p6.status != "preflight_ready":
        card = build_pricing_execution_blocked_card(
            blocked_step="P6 执行预检",
            blocked_status=p6.status,
            blocked_summary=p6.summary,
            next_action=p6.next_action or "",
            audit_record_id=p6.execution_record_id,
            watch_group_key=p6.watch_group_key,
            evidence=p6.evidence,
        )
        await _send_pipeline_card(
            card,
            chat_id=chat_id,
            fallback_text=build_pricing_execution_blocked_fallback_text(card),
        )
        return

    p7 = await prepare_pricing_page_write_dry_run(
        execution_record_id=int(p6.execution_record_id or 0),
        actor_id="feishu-auto-chain",
    )
    if p7.status != "page_write_preview_ready":
        card = build_pricing_execution_blocked_card(
            blocked_step="P7 页面写入空跑",
            blocked_status=p7.status,
            blocked_summary=p7.summary,
            next_action=p7.next_action or "",
            audit_record_id=p7.execution_record_id,
            watch_group_key=p7.watch_group_key,
            evidence=p7.evidence,
        )
        await _send_pipeline_card(
            card,
            chat_id=chat_id,
            fallback_text=build_pricing_execution_blocked_fallback_text(card),
        )
        return

    p8 = await execute_pricing_page_write(
        execution_record_id=int(p7.execution_record_id or 0),
        actor_id="feishu-auto-chain",
    )
    payload = p8.model_dump(mode="json")
    if p8.status in {"executed", "success"}:
        card = build_pricing_execution_result_card(payload)
        await _send_pipeline_card(
            card,
            chat_id=chat_id,
            fallback_text=build_pricing_execution_result_fallback_text(payload),
        )
        return
    card = build_pricing_execution_blocked_card(
        blocked_step="P8 真实写价",
        blocked_status=p8.status,
        blocked_summary=p8.summary,
        next_action=p8.next_action or "",
        audit_record_id=p8.execution_record_id,
        watch_group_key=p8.watch_group_key,
        evidence=p8.evidence,
    )
    await _send_pipeline_card(
        card,
        chat_id=chat_id,
        fallback_text=build_pricing_execution_blocked_fallback_text(card),
    )


async def run_pricing_execution_pipeline_guarded(
    *,
    approval_record_id: int,
    chat_id: str | None = None,
) -> None:
    from app.domain.notifications.pricing_execution_card import (
        build_pricing_execution_blocked_card,
        build_pricing_execution_blocked_fallback_text,
    )
    from app.domain.pricing_approvals.service import update_approval_pipeline_status

    await update_approval_pipeline_status(approval_record_id, status="running")
    try:
        await run_pricing_execution_pipeline(
            approval_record_id=approval_record_id,
            chat_id=chat_id,
        )
        await update_approval_pipeline_status(approval_record_id, status="completed")
    except Exception as exc:
        logger.error(
            "pricing execution pipeline failed unexpectedly for approval %s (%s)",
            approval_record_id,
            type(exc).__name__,
        )
        await update_approval_pipeline_status(
            approval_record_id,
            status="failed",
            error_type=type(exc).__name__,
            error_message=PIPELINE_UNEXPECTED_FAILURE_MESSAGE,
        )
        card = build_pricing_execution_blocked_card(
            blocked_step="调价执行链",
            blocked_status="pipeline_failed",
            blocked_summary=PIPELINE_UNEXPECTED_FAILURE_MESSAGE,
            next_action="请查看飞书群通知或联系运维；如需继续改价，请重新发起审批。",
            audit_record_id=approval_record_id,
            watch_group_key="",
            evidence={"error_type": type(exc).__name__},
        )
        await _send_pipeline_card(
            card,
            chat_id=chat_id,
            fallback_text=build_pricing_execution_blocked_fallback_text(card),
        )


def queue_pricing_execution_pipeline(
    *,
    approval_record_id: int,
    chat_id: str | None = None,
) -> bool:
    _schedule_background_task(
        run_pricing_execution_pipeline_guarded(
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
        logger.warning("pricing execution background task was cancelled")
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "pricing execution background task failed (%s)",
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
            "summary": "缺少飞书群聊上下文，执行通知卡片未发送。",
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
            "failed to send pricing execution pipeline card (%s)",
            type(exc).__name__,
        )
        return {
            "status": "failed",
            "summary": "调价执行结果卡片发送失败。",
            "evidence": {"error_type": type(exc).__name__},
        }
