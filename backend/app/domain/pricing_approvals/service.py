from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.db.session import ensure_db_ready, session_scope
from app.domain.pricing_execution.models import PricingApprovalRecord, PricingPreviewRecord
from app.domain.pricing_safety import (
    account_policy_allows_write,
    hashed_identifier,
    normalized_account_policy,
    pricing_platform_from_preview_payload,
    strip_sensitive,
    write_not_allowed_reason,
)

ApprovalDecision = Literal["approve", "reject", "human_takeover"]


class PricingApprovalResult(BaseModel):
    status: str
    summary: str
    preview_id: int | None = None
    approval_record_id: int | None = None
    approval_id: str | None = None
    decision: str | None = None
    source: str = "api"
    execute_triggered: bool = False
    approval_candidate: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


async def record_pricing_approval_decision(
    *,
    preview_id: int,
    decision: ApprovalDecision,
    actor_id: str = "operator",
    source: Literal["api", "feishu_card"] = "api",
    open_id: str | None = None,
    union_id: str | None = None,
    message_id: str | None = None,
    chat_id: str | None = None,
) -> PricingApprovalResult:
    await ensure_db_ready()
    async with session_scope() as session:
        preview = await session.get(PricingPreviewRecord, preview_id)
        if preview is None:
            return PricingApprovalResult(
                status="not_found",
                summary="调价预览不存在。",
                preview_id=preview_id,
                decision=decision,
                source=source,
                evidence={"execute_triggered": False},
                next_action="重新生成调价预览。",
            )
        existing = await session.scalar(
            select(PricingApprovalRecord)
            .where(
                PricingApprovalRecord.preview_id == preview_id,
                PricingApprovalRecord.status.in_(["approved", "rejected", "human_takeover"]),
            )
            .order_by(desc(PricingApprovalRecord.created_at), desc(PricingApprovalRecord.id))
            .limit(1)
        )
        if existing is not None:
            return _record_to_result(existing, preview, already_decided=True)

        if decision == "approve" and str(preview.status or "") == "reference_ready":
            return PricingApprovalResult(
                status="operator_target_price_required",
                summary="调价参考卡须先自定义并确认目标价，不能直接通过。",
                preview_id=preview_id,
                decision=decision,
                source=source,
                evidence={
                    "execute_triggered": False,
                    "requires_operator_target_price": True,
                    "direct_feishu_approve_allowed": False,
                },
                next_action="点击「自定义调价」输入目标价并确认。",
            )

        preview_payload = _preview_to_payload(preview)
        policy = normalized_account_policy(
            {
                "connection_mode": preview.connection_mode,
                "can_auto_write_price": preview.can_auto_write_price,
                "write_policy_message": preview.write_policy_message,
            }
        )
        if decision == "approve" and source == "feishu_card" and not account_policy_allows_write(policy):
            status = "write_not_allowed"
            summary = write_not_allowed_reason(
                pricing_platform_from_preview_payload(preview_payload)
            )
        elif decision == "approve":
            status = "approved"
            summary = (
                "Feishu 审批已通过，等待执行队列。"
                if source == "feishu_card"
                else "审批已记录；API 直接审批不会触发自动执行。"
            )
        elif decision == "human_takeover":
            status = "human_takeover"
            summary = "已记录人工接管，本次不会自动写价。"
        else:
            status = "rejected"
            summary = "已拒绝本次调价预览，不会自动写价。"

        result_payload = {
            "preview": preview_payload,
            "decision": decision,
            "source": source,
            "execute_triggered": False,
            "account_policy": policy,
            # 执行链重启恢复时用于补推「请重新发起」卡（chat_id 非敏感，
            # 监控组表内本就明文存储）。
            "feishu_chat_id": str(chat_id or "").strip() or None,
        }
        record = PricingApprovalRecord(
            preview_id=preview_id,
            approval_id=str(preview_payload.get("approval_id") or f"pricing-approval-{preview_id}"),
            source=source,
            decision=decision,
            status=status,
            summary=summary,
            actor_id=actor_id.strip() or "operator",
            open_id_hash=hashed_identifier(open_id),
            union_id_hash=hashed_identifier(union_id),
            message_id_hash=hashed_identifier(message_id),
            execute_triggered=False,
            result_json=strip_sensitive(result_payload),
        )
        session.add(record)
        await session.flush()
        result = _record_to_result(record, preview)
    return result


async def mark_approval_execute_queued(approval_record_id: int) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PricingApprovalRecord, approval_record_id)
        if record is not None:
            record.execute_triggered = True
            record.result_json = strip_sensitive(
                {**(record.result_json or {}), "execute_triggered": True}
            )
    await update_approval_pipeline_status(approval_record_id, status="queued")


PIPELINE_LOST_ON_RESTART_MESSAGE = (
    "backend 重启导致调价执行链丢失，本次已不会自动写价，请重新发起审批。"
)


async def recover_stuck_pricing_approval_pipelines() -> list[int]:
    """启动时扫描 queued/running 的执行链记录：进程内任务已随重启丢失，
    标记 failed 并按记录里的 chat_id 补推「请重新发起」卡片。"""

    from sqlalchemy import select

    from app.domain.notifications.feishu_client import FeishuBotClient
    from app.domain.notifications.pricing_execution_card import (
        build_pricing_execution_blocked_card,
        build_pricing_execution_blocked_fallback_text,
    )

    await ensure_db_ready()
    stuck_ids: list[int] = []
    async with session_scope() as session:
        records = (
            (
                await session.execute(
                    select(PricingApprovalRecord)
                    .where(PricingApprovalRecord.execute_triggered.is_(True))
                    .order_by(PricingApprovalRecord.id)
                )
            )
            .scalars()
            .all()
        )
        for record in records:
            result_json = record.result_json or {}
            pipeline = result_json.get("pipeline") or {}
            if str(pipeline.get("status") or "") not in {"queued", "running"}:
                continue
            stuck_ids.append(int(record.id))
            record.result_json = strip_sensitive(
                {
                    **result_json,
                    "pipeline": {
                        **pipeline,
                        "status": "failed",
                        "error_type": "pipeline_lost_on_restart",
                        "error_message": PIPELINE_LOST_ON_RESTART_MESSAGE,
                    },
                }
            )
    for approval_record_id in stuck_ids:
        card = build_pricing_execution_blocked_card(
            blocked_step="调价执行链（重启恢复）",
            blocked_status="pipeline_lost_on_restart",
            blocked_summary=PIPELINE_LOST_ON_RESTART_MESSAGE,
            next_action="请重新发送调价指令并发起审批；已批准的旧卡不会再执行。",
            audit_record_id=approval_record_id,
            watch_group_key="",
            evidence={"recovered_at_startup": True},
        )
        chat_id = None
        async with session_scope() as session:
            record = await session.get(PricingApprovalRecord, approval_record_id)
            chat_id = str(((record.result_json or {}) if record else {}).get("feishu_chat_id") or "").strip() or None
        if not chat_id:
            continue
        try:
            await FeishuBotClient(chat_id=chat_id).send_interactive_card(
                chat_id,
                card,
                fallback_text=build_pricing_execution_blocked_fallback_text(card),
            )
        except Exception:  # noqa: BLE001 - 恢复推送失败不阻断启动
            pass
    return stuck_ids


async def update_approval_pipeline_status(
    approval_record_id: int,
    *,
    status: Literal["queued", "running", "completed", "failed"],
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    from datetime import UTC, datetime

    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PricingApprovalRecord, approval_record_id)
        if record is None:
            return
        pipeline = {
            **((record.result_json or {}).get("pipeline") or {}),
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if error_type:
            pipeline["error_type"] = error_type
        if error_message:
            pipeline["error_message"] = error_message[:300]
        record.result_json = strip_sensitive(
            {
                **(record.result_json or {}),
                "pipeline": pipeline,
            }
        )


async def get_pricing_approval(approval_record_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PricingApprovalRecord, approval_record_id)
    return _approval_record_to_dict(record) if record is not None else None


def _record_to_result(
    record: PricingApprovalRecord,
    preview: PricingPreviewRecord,
    *,
    already_decided: bool = False,
) -> PricingApprovalResult:
    preview_payload = _preview_to_payload(preview)
    status = "already_decided" if already_decided else record.status
    return PricingApprovalResult(
        status=status,
        summary=record.summary if not already_decided else "这张调价审批卡已处理过。",
        preview_id=int(preview.id),
        approval_record_id=int(record.id),
        approval_id=record.approval_id,
        decision=record.decision,
        source=record.source,
        execute_triggered=bool(record.execute_triggered),
        approval_candidate={
            "approval_id": record.approval_id,
            "preview_id": int(preview.id),
            "status": record.status,
            "decision": record.decision,
            "source": record.source,
            "actions": preview_payload.get("preview_candidate", {}).get("actions", []),
            "can_execute": preview.can_auto_write_price and record.status == "approved",
        },
        evidence={
            "execute_triggered": bool(record.execute_triggered),
            "approval_source": record.source,
            "feishu_card_required_for_execute": True,
            "api_direct_execute_allowed": False,
        },
        next_action=(
            "执行队列将继续预检、页面空跑和写价。"
            if record.source == "feishu_card" and record.status == "approved"
            else "本次不会触发自动写价。"
        ),
    )


def _approval_record_to_dict(record: PricingApprovalRecord) -> dict[str, Any]:
    return {
        "approval_record_id": record.id,
        "id": record.id,
        "preview_id": record.preview_id,
        "approval_id": record.approval_id,
        "source": record.source,
        "decision": record.decision,
        "status": record.status,
        "summary": record.summary,
        "actor_id": record.actor_id,
        "execute_triggered": record.execute_triggered,
        "result": record.result_json,
        "created_at": record.created_at.isoformat(),
        "decided_at": record.decided_at.isoformat(),
    }


def _preview_to_payload(preview: PricingPreviewRecord) -> dict[str, Any]:
    candidate = preview.preview_candidate_json or {}
    return {
        "preview_id": int(preview.id),
        "id": int(preview.id),
        "status": preview.status,
        "summary": preview.summary,
        "watch_group_key": preview.watch_group_key,
        "source_alert_record_id": preview.source_alert_record_id,
        "connection_mode": preview.connection_mode,
        "can_auto_write_price": preview.can_auto_write_price,
        "write_policy_message": preview.write_policy_message,
        "approval_id": candidate.get("approval_id") if isinstance(candidate, dict) else None,
        "preview_candidate": candidate,
    }
