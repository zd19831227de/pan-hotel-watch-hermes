"""金字塔出价调整预览：读取金字塔推广列表 -> 匹配计划 -> 生成审批候选。

与手动房价调价（pricing_suggestions.preview）同构：
- 账号与分组权限完全复用 EBK 账号的 `pricing_feishu_chat_id` 绑定；
- 只读当前出价后生成候选，人工确认卡片后才允许进入执行链；
- 金字塔调价仅支持携程 EBK 账号。
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
from app.domain.pyramid_bidding.commands import (
    PYRAMID_BID_MAX_CNY,
    PYRAMID_BID_MIN_CNY,
    pyramid_campaign_key,
)
from app.domain.pyramid_bidding.models import PyramidBidPreviewRecord

logger = logging.getLogger(__name__)

PYRAMID_PLATFORM = "ctrip"
PYRAMID_BID_READ_TIMEOUT_SECONDS = 60
_BID_EPSILON = 0.005

PYRAMID_PLATFORM_NOT_SUPPORTED_SUMMARY = (
    "金字塔调价仅支持携程 EBK 账号，当前飞书群绑定的调价账号不是携程账号。"
)


class PyramidBidPreviewResult(BaseModel):
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


def _pyramid_browser_service() -> Any:
    from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service

    return get_ctrip_ebooking_service()


def pyramid_write_allowed_for_account(account: Any | None) -> bool:
    """金字塔写入 = 携程 EBK 账号 + 房价同款闸门（non_direct 且平台白名单放行）。"""
    return (
        str(getattr(account, "platform", "") or "").strip().lower() == PYRAMID_PLATFORM
        and bool(getattr(account, "can_auto_write_price", False))
    )


async def read_pyramid_promotions_for_account(
    pricing_account: Any,
    *,
    actor_id: str = "operator",
) -> dict[str, Any]:
    """只读金字塔推广列表（复用携程 EBK 浏览器服务，页面采集见 browser flow）。"""
    service = _pyramid_browser_service()
    return await asyncio.wait_for(
        service.read_pyramid_promotions(
            platform_account_id=str(getattr(pricing_account, "platform_account_id", "") or ""),
            browser_session_id=str(getattr(pricing_account, "browser_session_id", "") or ""),
            actor_id=actor_id,
        ),
        timeout=PYRAMID_BID_READ_TIMEOUT_SECONDS,
    )


async def create_pyramid_bid_preview(
    *,
    chat_id: str | None,
    campaign_name: str,
    target_bid: float,
    pricing_account: Any | None = None,
    actor_id: str = "operator",
    reason: str | None = None,
) -> PyramidBidPreviewResult:
    await ensure_db_ready()
    campaign_name = _normalize_campaign_name(campaign_name)
    target_bid = _safe_float(target_bid)
    policy = pricing_account_policy_from_browser_session(pricing_account)
    normalized = normalized_account_policy(policy)
    chat_id = str(chat_id or "").strip()

    if not policy.get("pricing_account_configured"):
        result = PyramidBidPreviewResult(
            status="config_required",
            summary="当前飞书群还没有绑定用于调价的 EBK 浏览器账号，无法读取金字塔推广。",
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_pyramid_bid_adjustment",
                "preview_created": False,
                "pricing_account_configured": False,
            },
            next_action="请先在“账号与浏览器”里给携程 EBK 账号绑定当前飞书调价审批群 chat_id。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_pyramid_bid_adjustment",
                "campaign_name": campaign_name,
                "target_bid": target_bid,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    account_platform = str(getattr(pricing_account, "platform", "") or "").strip().lower()
    if account_platform != PYRAMID_PLATFORM:
        result = PyramidBidPreviewResult(
            status="write_not_allowed",
            summary=PYRAMID_PLATFORM_NOT_SUPPORTED_SUMMARY,
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_pyramid_bid_adjustment",
                "preview_created": False,
                "account_platform": account_platform or "unknown",
            },
            next_action="请在“账号与浏览器”里将当前群的调价绑定改到携程 EBK 账号。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_pyramid_bid_adjustment",
                "campaign_name": campaign_name,
                "target_bid": target_bid,
            },
            result,
            actor_id=actor_id,
        )

    if not campaign_name or target_bid is None or not (
        PYRAMID_BID_MIN_CNY <= target_bid <= PYRAMID_BID_MAX_CNY
    ):
        result = PyramidBidPreviewResult(
            status="data_required",
            summary=(
                f"金字塔调价需要明确的推广名称和 {PYRAMID_BID_MIN_CNY:g}~{PYRAMID_BID_MAX_CNY:g} 元之间的目标出价。"
            ),
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_pyramid_bid_adjustment",
                "preview_created": False,
                "pricing_account_configured": True,
            },
            next_action="请按示例重发：金字塔调价 14--16 出价调到6。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_pyramid_bid_adjustment",
                "campaign_name": campaign_name,
                "target_bid": target_bid,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    read_result = await _read_promotions(pricing_account, actor_id=actor_id)
    promotions = read_result.get("promotions") or []
    if read_result.get("status") != "success":
        result = PyramidBidPreviewResult(
            status="read_failed",
            summary=str(
                read_result.get("summary")
                or "读取携程金字塔推广页失败，本次不生成调价预览。"
            ),
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_pyramid_bid_adjustment",
                "preview_created": False,
                "read_status": read_result.get("status"),
            },
            next_action="请稍后重试；若持续失败请人工登录 EBK 查看金字塔页面。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_pyramid_bid_adjustment",
                "campaign_name": campaign_name,
                "target_bid": target_bid,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    matched = _match_promotion(promotions, campaign_name)
    if matched is None:
        available = "、".join(
            str(item.get("campaign_name") or "") for item in promotions[:8]
        )
        result = PyramidBidPreviewResult(
            status="campaign_not_found",
            summary=(
                f"金字塔推广列表里没有找到「{campaign_name}」。"
                f"当前计划：{available or '未读取到任何计划'}。"
            ),
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_pyramid_bid_adjustment",
                "preview_created": False,
                "campaign_count": len(promotions),
            },
            next_action="请发送「金字塔出价」查看最新计划名称后重试。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_pyramid_bid_adjustment",
                "campaign_name": campaign_name,
                "target_bid": target_bid,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    # 命中后以页面规范名回填：用户说「周内0--2」而页面是「周内0-2」时，
    # 审批卡与执行链统一使用页面上的真实计划名，避免变体名混淆。
    campaign_name = str(matched.get("campaign_name") or campaign_name)
    current_bid = _safe_float(matched.get("click_bid_cny"))
    if current_bid is not None and abs(target_bid - current_bid) < _BID_EPSILON:
        result = PyramidBidPreviewResult(
            status="no_change_required",
            summary=f"「{campaign_name}」当前点击出价已是 ¥{current_bid:g}，无需调整。",
            chat_id=chat_id,
            **normalized,
            evidence={
                "preview_type": "manual_pyramid_bid_adjustment",
                "preview_created": False,
                "current_bid_cny": current_bid,
                "target_bid_cny": target_bid,
            },
            next_action="如需改其他出价，请重新发送指令。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_pyramid_bid_adjustment",
                "campaign_name": campaign_name,
                "target_bid": target_bid,
            },
            result,
            actor_id=actor_id,
        )

    approval_id = f"pyramid-bid-approval-{uuid4().hex[:12]}"
    risk_flags = ["manual_pyramid_bid_adjustment", "requires_preflight_revalidation"]
    if current_bid is None:
        risk_flags.append("current_bid_readback_missing")
    if matched.get("smart_campaign"):
        risk_flags.append("smart_campaign_bid_semantics")
    action = {
        "action_id": f"pyramid-bid-{uuid4().hex[:12]}",
        "operation": "set_pyramid_click_bid",
        "source": "manual_feishu_command",
        "platform": PYRAMID_PLATFORM,
        "campaign_name": campaign_name,
        "campaign_status": matched.get("campaign_status"),
        "promotion_date_text": matched.get("promotion_date_text"),
        "daily_budget_cny": _safe_float(matched.get("daily_budget_cny")),
        "today_spend_cny": _safe_float(matched.get("today_spend_cny")),
        "current_bid_cny": current_bid,
        "before_bid_cny": current_bid,
        "proposed_bid_cny": target_bid,
        "target_bid_cny": target_bid,
        "floor_price_cny": None,
        "floor_to_be_verified_on_edit_page": True,
        "trigger_reason": (reason or "飞书手动金字塔调价指令。").strip(),
        "needs_human_attention": True,
        "risk_flags": risk_flags,
        "write_intent": {
            "tool_name": "ota.ctrip_pyramid_bid_write_after_feishu_approval",
            "arguments": {
                "platform": PYRAMID_PLATFORM,
                "campaign_name": campaign_name,
                "target_bid": target_bid,
            },
        },
    }
    candidate = {
        "candidate_id": f"pyramid-bid-preview-{uuid4().hex[:12]}",
        "status": "pending_approval",
        "preview_type": "manual_pyramid_bid_adjustment",
        "source": "manual_feishu_command",
        "approval_id": approval_id,
        "approval_source_required": "feishu_card",
        "pricing_account": _public_pyramid_account(policy),
        "actions": [action],
        "safety": {
            "requires_preview": True,
            "requires_feishu_approval": True,
            "requires_preflight_revalidation": True,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "can_auto_write_price": normalized["can_auto_write_price"],
            "connection_mode": normalized["connection_mode"],
            "platform": PYRAMID_PLATFORM,
        },
    }
    result = PyramidBidPreviewResult(
        status="preview_ready",
        summary=(
            f"金字塔调价预览已生成：「{campaign_name}」点击出价 "
            f"¥{_display_bid(current_bid)} -> ¥{target_bid:g}。"
        ),
        chat_id=chat_id,
        approval_id=approval_id,
        **normalized,
        preview_candidate=candidate,
        evidence={
            "preview_type": "manual_pyramid_bid_adjustment",
            "approval_source_required": "feishu_card",
            "requires_preflight_revalidation": True,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "pricing_account_configured": True,
            "promotion_readback": strip_sensitive(matched),
        },
        next_action=(
            "发送飞书金字塔调价审批卡片。"
            if normalized["can_auto_write_price"]
            else normalized["write_policy_message"]
        ),
    )
    return await _save_preview(
        {
            "preview_type": "manual_pyramid_bid_adjustment",
            "campaign_name": campaign_name,
            "target_bid": target_bid,
            "reason": reason,
        },
        result,
        actor_id=actor_id,
    )


async def get_pyramid_bid_preview(preview_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PyramidBidPreviewRecord, preview_id)
    return _preview_record_to_dict(record) if record is not None else None


async def _read_promotions(pricing_account: Any, *, actor_id: str) -> dict[str, Any]:
    try:
        read_result = await read_pyramid_promotions_for_account(
            pricing_account,
            actor_id=actor_id,
        )
    except asyncio.TimeoutError:
        return {"status": "timeout", "summary": "读取金字塔推广页超时。", "promotions": []}
    except Exception as exc:  # noqa: BLE001
        logger.warning("pyramid promotion read failed: %s", exc)
        return {
            "status": "failed",
            "summary": "读取金字塔推广页失败。",
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


def _match_promotion(promotions: list[dict[str, Any]], campaign_name: str) -> dict[str, Any] | None:
    # 横线/空格变体视为同名（「周内0--2」↔「周内0-2」），与浏览器流同口径。
    target_key = pyramid_campaign_key(campaign_name)
    for item in promotions:
        if not isinstance(item, dict):
            continue
        if pyramid_campaign_key(item.get("campaign_name")) == target_key:
            return item
    return None


def _public_pyramid_account(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": PYRAMID_PLATFORM,
        "platform_account_id": policy.get("pricing_platform_account_id"),
        "browser_session_id": policy.get("pricing_browser_session_id"),
        "connection_mode": policy.get("connection_mode"),
        "can_auto_write_price": bool(policy.get("can_auto_write_price")),
    }


async def _save_preview(
    source_request: dict[str, Any],
    result: PyramidBidPreviewResult,
    *,
    actor_id: str,
) -> PyramidBidPreviewResult:
    async with session_scope() as session:
        record = PyramidBidPreviewRecord(
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
            result.preview_candidate["pyramid_bid_preview_record_id"] = int(record.id)
            record.preview_candidate_json = strip_sensitive(result.preview_candidate)
    return result


def _preview_record_to_dict(record: PyramidBidPreviewRecord) -> dict[str, Any]:
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


def _normalize_campaign_name(raw: Any) -> str:
    return re.sub(r"\s+", "", str(raw or "")).strip()


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _display_bid(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:g}"
