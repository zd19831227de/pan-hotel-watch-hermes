"""金字塔出价审批：记录飞书卡 / API 审批决策。

与 pricing_approvals.service 同构：API 审批只记录状态、不触发执行；
只有 `source=feishu_card` 且账号策略放行的 approve 才可能进入执行链。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.db.session import ensure_db_ready, session_scope
from app.domain.pricing_safety import (
    account_policy_allows_write,
    hashed_identifier,
    normalized_account_policy,
    strip_sensitive,
)
from app.domain.pyramid_bidding.models import (
    PyramidBidApprovalRecord,
    PyramidBidPreviewRecord,
)

PyramidApprovalDecision = Literal["approve", "reject", "human_takeover"]

PYRAMID_WRITE_NOT_ALLOWED_SUMMARY = (
    "当前携程 EBK 账号为直连或未确认账号，仅允许生成金字塔调价建议，不允许自动化调整出价。"
)


class PyramidBidApprovalResult(BaseModel):
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


async def record_pyramid_bid_approval_decision(
    *,
    preview_id: int,
    decision: PyramidApprovalDecision,
    actor_id: str = "operator",
    source: Literal["api", "feishu_card"] = "api",
    open_id: str | None = None,
    union_id: str | None = None,
    message_id: str | None = None,
) -> PyramidBidApprovalResult:
    await ensure_db_ready()
    async with session_scope() as session:
        preview = await session.get(PyramidBidPreviewRecord, preview_id)
        if preview is None:
            return PyramidBidApprovalResult(
                status="not_found",
                summary="金字塔调价预览不存在。",
                preview_id=preview_id,
                decision=decision,
                source=source,
                evidence={"execute_triggered": False},
                next_action="重新发送金字塔调价指令生成预览。",
            )
        existing = await session.scalar(
            select(PyramidBidApprovalRecord)
            .where(
                PyramidBidApprovalRecord.preview_id == preview_id,
                PyramidBidApprovalRecord.status.in_(
                    ["approved", "rejected", "human_takeover"]
                ),
            )
            .order_by(desc(PyramidBidApprovalRecord.created_at), desc(PyramidBidApprovalRecord.id))
            .limit(1)
        )
        if existing is not None:
            return _record_to_result(existing, preview, already_decided=True)

        if decision == "approve" and str(preview.status or "") != "preview_ready":
            return PyramidBidApprovalResult(
                status="not_ready",
                summary="该金字塔预览不是待审批状态，不能直接确认调整。",
                preview_id=preview_id,
                decision=decision,
                source=source,
                evidence={"execute_triggered": False},
                next_action="请重新发送金字塔调价指令。",
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
            summary = PYRAMID_WRITE_NOT_ALLOWED_SUMMARY
        elif decision == "approve":
            status = "approved"
            summary = (
                "金字塔调价审批已通过，等待执行链预检。"
                if source == "feishu_card"
                else "审批已记录；API 直接审批不会触发自动执行。"
            )
        elif decision == "human_takeover":
            status = "human_takeover"
            summary = "已记录人工接管，本次不会自动调整金字塔出价。"
        else:
            status = "rejected"
            summary = "已拒绝本次金字塔调价，不会自动调整出价。"

        record = PyramidBidApprovalRecord(
            preview_id=preview_id,
            approval_id=str(
                preview_payload.get("approval_id") or f"pyramid-approval-{preview_id}"
            ),
            source=source,
            decision=decision,
            status=status,
            summary=summary,
            actor_id=actor_id.strip() or "operator",
            open_id_hash=hashed_identifier(open_id),
            union_id_hash=hashed_identifier(union_id),
            message_id_hash=hashed_identifier(message_id),
            execute_triggered=False,
            result_json=strip_sensitive(
                {
                    "preview": preview_payload,
                    "decision": decision,
                    "source": source,
                    "execute_triggered": False,
                    "account_policy": policy,
                }
            ),
        )
        session.add(record)
        await session.flush()
        result = _record_to_result(record, preview)
    return result


async def mark_pyramid_approval_execute_queued(approval_record_id: int) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PyramidBidApprovalRecord, approval_record_id)
        if record is not None:
            record.execute_triggered = True
            record.result_json = strip_sensitive(
                {**(record.result_json or {}), "execute_triggered": True}
            )
    await update_pyramid_approval_pipeline_status(approval_record_id, status="queued")


async def update_pyramid_approval_pipeline_status(
    approval_record_id: int,
    *,
    status: Literal["queued", "running", "completed", "failed"],
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PyramidBidApprovalRecord, approval_record_id)
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


async def get_pyramid_bid_approval(approval_record_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PyramidBidApprovalRecord, approval_record_id)
    return _approval_record_to_dict(record) if record is not None else None


def _record_to_result(
    record: PyramidBidApprovalRecord,
    preview: PyramidBidPreviewRecord,
    *,
    already_decided: bool = False,
) -> PyramidBidApprovalResult:
    preview_payload = _preview_to_payload(preview)
    status = "already_decided" if already_decided else record.status
    return PyramidBidApprovalResult(
        status=status,
        summary=record.summary if not already_decided else "这张金字塔调价审批卡已处理过。",
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
            "执行链将继续预检、页面核验和出价调整。"
            if record.source == "feishu_card" and record.status == "approved"
            else "本次不会触发自动调整出价。"
        ),
    )


def _approval_record_to_dict(record: PyramidBidApprovalRecord) -> dict[str, Any]:
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


def _preview_to_payload(preview: PyramidBidPreviewRecord) -> dict[str, Any]:
    candidate = preview.preview_candidate_json or {}
    return {
        "preview_id": int(preview.id),
        "id": int(preview.id),
        "status": preview.status,
        "summary": preview.summary,
        "chat_id": preview.chat_id,
        "connection_mode": preview.connection_mode,
        "can_auto_write_price": preview.can_auto_write_price,
        "write_policy_message": preview.write_policy_message,
        "approval_id": candidate.get("approval_id") if isinstance(candidate, dict) else None,
        "preview_candidate": candidate,
    }
