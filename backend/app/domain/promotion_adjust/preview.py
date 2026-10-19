"""促销折扣调整预览：读取已参加促销列表 -> 匹配促销 -> 生成审批候选。

与金字塔出价预览（pyramid_bidding.preview）同构：
- 账号与分组权限完全复用 EBK 账号的 `pricing_feishu_chat_id` 绑定；
- 只读当前立减后生成候选，人工确认卡片后才允许进入执行链；
- 促销调整仅支持携程 EBK 账号，且仅支持单条「立减 X%」规则的促销。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.db.session import ensure_db_ready, session_scope
from app.domain.pricing_safety import normalized_account_policy, strip_sensitive
from app.domain.pricing_suggestions.preview import (
    pricing_account_policy_from_browser_session,
)
from app.domain.promotion_adjust.commands import (
    PROMOTION_DISCOUNT_MAX_PERCENT,
    PROMOTION_DISCOUNT_MIN_PERCENT,
)
from app.domain.promotion_adjust.models import PromotionAdjustPreviewRecord

logger = logging.getLogger(__name__)

PROMOTION_PLATFORM = "ctrip"
PROMOTION_READ_TIMEOUT_SECONDS = 60
_PERCENT_EPSILON = 0.005

PROMOTION_PLATFORM_NOT_SUPPORTED_SUMMARY = (
    "促销调整仅支持携程 EBK 账号，当前飞书群绑定的调价账号不是携程账号。"
)


class PromotionAdjustPreviewResult(BaseModel):
    status: str
    summary: str
    chat_id: str = ""
    preview_id: int | None = None
    approval_id: str | None = None
    connection_mode: str = "unknown"
    can_auto_write_price: bool = False
    write_policy_message: str = ""
    preview_candidate: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


def _promotion_browser_service() -> Any:
    from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service

    return get_ctrip_ebooking_service()


async def read_promotion_enrollments_for_account(
    pricing_account: Any,
    *,
    actor_id: str = "operator",
) -> dict[str, Any]:
    """只读已参加促销列表（复用携程 EBK 浏览器服务，页面采集见 browser flow）。"""
    service = _promotion_browser_service()
    return await asyncio.wait_for(
        service.read_promotion_enrollments(
            platform_account_id=str(getattr(pricing_account, "platform_account_id", "") or ""),
            browser_session_id=str(getattr(pricing_account, "browser_session_id", "") or ""),
            actor_id=actor_id,
        ),
        timeout=PROMOTION_READ_TIMEOUT_SECONDS,
    )


async def create_promotion_adjust_preview(
    *,
    chat_id: str | None,
    promotion_name: str,
    target_discount_percent: float,
    pricing_account: Any | None = None,
    actor_id: str = "operator",
    reason: str | None = None,
) -> PromotionAdjustPreviewResult:
    await ensure_db_ready()
    promotion_name = _normalize_promotion_name(promotion_name)
    target_discount_percent = _safe_float(target_discount_percent)
    policy = pricing_account_policy_from_browser_session(pricing_account)
    normalized = normalized_account_policy(policy)
    chat_id = str(chat_id or "").strip()

    if not policy.get("pricing_account_configured"):
        result = PromotionAdjustPreviewResult(
            status="config_required",
            summary="当前飞书群还没有绑定用于调价的 EBK 浏览器账号，无法读取已参加促销。",
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_promotion_adjustment",
                "preview_created": False,
                "pricing_account_configured": False,
            },
            next_action="请先在“账号与浏览器”里给携程 EBK 账号绑定当前飞书调价审批群 chat_id。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_promotion_adjustment",
                "promotion_name": promotion_name,
                "target_discount_percent": target_discount_percent,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    account_platform = str(getattr(pricing_account, "platform", "") or "").strip().lower()
    if account_platform != PROMOTION_PLATFORM:
        result = PromotionAdjustPreviewResult(
            status="write_not_allowed",
            summary=PROMOTION_PLATFORM_NOT_SUPPORTED_SUMMARY,
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_promotion_adjustment",
                "preview_created": False,
                "account_platform": account_platform or "unknown",
            },
            next_action="请在“账号与浏览器”里将当前群的调价绑定改到携程 EBK 账号。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_promotion_adjustment",
                "promotion_name": promotion_name,
                "target_discount_percent": target_discount_percent,
            },
            result,
            actor_id=actor_id,
        )

    if not promotion_name or target_discount_percent is None or not (
        PROMOTION_DISCOUNT_MIN_PERCENT <= target_discount_percent <= PROMOTION_DISCOUNT_MAX_PERCENT
    ):
        result = PromotionAdjustPreviewResult(
            status="data_required",
            summary=(
                "促销调整需要明确的促销名称和 "
                f"{PROMOTION_DISCOUNT_MIN_PERCENT:g}~{PROMOTION_DISCOUNT_MAX_PERCENT:g} "
                "之间的目标立减百分比。"
            ),
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_promotion_adjustment",
                "preview_created": False,
                "pricing_account_configured": True,
            },
            next_action="请按示例重发：促销调整 天天特价 立减调到5。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_promotion_adjustment",
                "promotion_name": promotion_name,
                "target_discount_percent": target_discount_percent,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    read_result = await _read_promotions(pricing_account, actor_id=actor_id)
    promotions = read_result.get("promotions") or []
    if read_result.get("status") != "success":
        result = PromotionAdjustPreviewResult(
            status="read_failed",
            summary=str(
                read_result.get("summary")
                or "读取携程促销中心已参加促销失败，本次不生成调整预览。"
            ),
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_promotion_adjustment",
                "preview_created": False,
                "read_status": read_result.get("status"),
            },
            next_action="请稍后重试；若持续失败请人工登录 EBK 查看促销中心。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_promotion_adjustment",
                "promotion_name": promotion_name,
                "target_discount_percent": target_discount_percent,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    matched = _match_promotion(promotions, promotion_name)
    if matched is None:
        available = "、".join(
            str(item.get("promotion_name") or "") for item in promotions[:8]
        )
        result = PromotionAdjustPreviewResult(
            status="promotion_not_found",
            summary=(
                f"已参加促销列表里没有找到「{promotion_name}」。"
                f"当前促销：{available or '未读取到任何促销'}。"
            ),
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_promotion_adjustment",
                "preview_created": False,
                "promotion_count": len(promotions),
            },
            next_action="请发送「促销列表」查看最新促销名称后重试。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_promotion_adjustment",
                "promotion_name": promotion_name,
                "target_discount_percent": target_discount_percent,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    # 命中后以页面规范名回填，审批卡与执行链统一使用页面上的真实促销名。
    promotion_name = str(matched.get("promotion_name") or promotion_name)
    if matched.get("multi_rule"):
        result = PromotionAdjustPreviewResult(
            status="multi_rule_promotion",
            summary=(
                f"「{promotion_name}」包含多条促销规则"
                f"（{matched.get('discount_text') or '-'}），自动调整可能改错规则，"
                "请在 EBK 促销中心人工调整。"
            ),
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_promotion_adjustment",
                "preview_created": False,
                "discount_kind": matched.get("discount_kind"),
            },
            next_action="多规则促销暂不支持自动调整。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_promotion_adjustment",
                "promotion_name": promotion_name,
                "target_discount_percent": target_discount_percent,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    current_percent = _safe_float(matched.get("discount_percent"))
    if current_percent is not None and abs(target_discount_percent - current_percent) < _PERCENT_EPSILON:
        result = PromotionAdjustPreviewResult(
            status="no_change_required",
            summary=f"「{promotion_name}」当前立减已是 {current_percent:g}%，无需调整。",
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_promotion_adjustment",
                "preview_created": False,
                "current_discount_percent": current_percent,
                "target_discount_percent": target_discount_percent,
            },
            next_action="如需改其他立减，请重新发送指令。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_promotion_adjustment",
                "promotion_name": promotion_name,
                "target_discount_percent": target_discount_percent,
            },
            result,
            actor_id=actor_id,
        )

    approval_id = f"promotion-adjust-approval-{uuid4().hex[:12]}"
    risk_flags = ["manual_promotion_adjustment", "requires_preflight_revalidation"]
    if current_percent is None:
        risk_flags.append("current_discount_readback_missing")
    action = {
        "action_id": f"promotion-adjust-{uuid4().hex[:12]}",
        "operation": "set_promotion_discount_percent",
        "source": "manual_feishu_command",
        "platform": PROMOTION_PLATFORM,
        "promotion_name": promotion_name,
        "room_scope": matched.get("room_scope"),
        "date_text": matched.get("date_text"),
        "current_discount_percent": current_percent,
        "before_discount_percent": current_percent,
        "proposed_discount_percent": target_discount_percent,
        "target_discount_percent": target_discount_percent,
        "trigger_reason": (reason or "飞书手动促销调整指令。").strip(),
        "needs_human_attention": True,
        "risk_flags": risk_flags,
        "write_intent": {
            "tool_name": "ota.ctrip_promotion_adjust_write_after_feishu_approval",
            "arguments": {
                "platform": PROMOTION_PLATFORM,
                "promotion_name": promotion_name,
                "target_discount_percent": target_discount_percent,
            },
        },
    }
    candidate = {
        "candidate_id": f"promotion-adjust-preview-{uuid4().hex[:12]}",
        "status": "pending_approval",
        "preview_type": "manual_promotion_adjustment",
        "source": "manual_feishu_command",
        "approval_id": approval_id,
        "approval_source_required": "feishu_card",
        "pricing_account": _public_promotion_account(policy),
        "actions": [action],
        "safety": {
            "requires_preview": True,
            "requires_feishu_approval": True,
            "requires_preflight_revalidation": True,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "can_auto_write_price": normalized["can_auto_write_price"],
            "connection_mode": normalized["connection_mode"],
            "platform": PROMOTION_PLATFORM,
        },
    }
    result = PromotionAdjustPreviewResult(
        status="preview_ready",
        summary=(
            f"促销调整预览已生成：「{promotion_name}」立减 "
            f"{_display_percent(current_percent)} -> {target_discount_percent:g}%。"
        ),
        chat_id=chat_id,
        approval_id=approval_id,
        **normalized,
        preview_candidate=candidate,
        evidence={
            "preview_type": "manual_promotion_adjustment",
            "approval_source_required": "feishu_card",
            "requires_preflight_revalidation": True,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "pricing_account_configured": True,
            "promotion_readback": strip_sensitive(matched),
        },
        next_action=(
            "发送飞书促销调整审批卡片。"
            if normalized["can_auto_write_price"]
            else normalized["write_policy_message"]
        ),
    )
    return await _save_preview(
        {
            "preview_type": "manual_promotion_adjustment",
            "promotion_name": promotion_name,
            "target_discount_percent": target_discount_percent,
            "reason": reason,
        },
        result,
        actor_id=actor_id,
    )


async def get_promotion_adjust_preview(preview_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PromotionAdjustPreviewRecord, preview_id)
    return _preview_record_to_dict(record) if record is not None else None


async def _read_promotions(pricing_account: Any, *, actor_id: str) -> dict[str, Any]:
    try:
        read_result = await read_promotion_enrollments_for_account(
            pricing_account,
            actor_id=actor_id,
        )
    except asyncio.TimeoutError:
        return {"status": "timeout", "summary": "读取已参加促销列表超时。", "promotions": []}
    except Exception as exc:  # noqa: BLE001
        logger.warning("promotion enrollment read failed (%s)", type(exc).__name__)
        return {
            "status": "failed",
            "summary": "读取已参加促销列表失败。",
            "promotions": [],
        }
    promotions = read_result.get("promotions") if isinstance(read_result, dict) else None
    if read_result.get("status") != "success" or not isinstance(promotions, list):
        return {
            "status": str(read_result.get("status") or "failed") if isinstance(read_result, dict) else "failed",
            "summary": str(read_result.get("summary") or "") if isinstance(read_result, dict) else "",
            "promotions": [],
        }
    return {"status": "success", "promotions": promotions, "read": read_result}


def _match_promotion(promotions: list[dict[str, Any]], promotion_name: str) -> dict[str, Any] | None:
    from app.domain.promotion_adjust.commands import promotion_name_key

    target = promotion_name_key(promotion_name)
    if not target:
        return None
    exact: list[dict[str, Any]] = []
    contains: list[dict[str, Any]] = []
    for item in promotions:
        if not isinstance(item, dict):
            continue
        name_key = promotion_name_key(item.get("promotion_name"))
        if not name_key:
            continue
        if target == name_key:
            exact.append(item)
        elif target in name_key or name_key in target:
            contains.append(item)
    if len(exact) == 1:
        return exact[0]
    if not exact and len(contains) == 1:
        return contains[0]
    return None


def _public_promotion_account(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": PROMOTION_PLATFORM,
        "platform_account_id": policy.get("pricing_platform_account_id"),
        "browser_session_id": policy.get("pricing_browser_session_id"),
        "connection_mode": policy.get("connection_mode"),
        "can_auto_write_price": bool(policy.get("can_auto_write_price")),
    }


async def _save_preview(
    source_request: dict[str, Any],
    result: PromotionAdjustPreviewResult,
    *,
    actor_id: str,
) -> PromotionAdjustPreviewResult:
    async with session_scope() as session:
        record = PromotionAdjustPreviewRecord(
            chat_id=result.chat_id or "unknown",
            status=result.status,
            summary=result.summary,
            connection_mode=result.connection_mode,
            can_auto_write_price=result.can_auto_write_price,
            write_policy_message=result.write_policy_message,
            preview_candidate_json=strip_sensitive(result.preview_candidate),
            source_request_json=strip_sensitive(source_request),
            actor_id=actor_id.strip() or "operator",
        )
        session.add(record)
        await session.flush()
        result.preview_id = int(record.id)
        if result.preview_candidate:
            result.preview_candidate["promotion_adjust_preview_record_id"] = int(record.id)
            record.preview_candidate_json = strip_sensitive(result.preview_candidate)
    return result


def _preview_record_to_dict(record: PromotionAdjustPreviewRecord) -> dict[str, Any]:
    candidate = record.preview_candidate_json or {}
    return {
        "preview_id": record.id,
        "id": record.id,
        "status": record.status,
        "summary": record.summary,
        "chat_id": record.chat_id,
        "connection_mode": record.connection_mode,
        "can_auto_write_price": record.can_auto_write_price,
        "write_policy_message": record.write_policy_message,
        "approval_id": candidate.get("approval_id") if isinstance(candidate, dict) else None,
        "preview_candidate": candidate,
        "created_at": record.created_at.isoformat(),
    }


def _normalize_promotion_name(raw: Any) -> str:
    return re.sub(r"\s+", "", str(raw or "")).strip()


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _display_percent(value: float | None) -> str:
    return "-" if value is None else f"{value:g}%"
