from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.db.session import ensure_db_ready, session_scope
from app.domain.browser.ctrip_price_converter import DEFAULT_COMMISSION_RATE, sell_to_base
from app.domain.browser.sessions import (
    BrowserSession,
    account_write_policy,
    get_pricing_browser_session_for_feishu_chat_id,
    get_browser_session_store,
    get_browser_session_for_platform_account,
)
from app.domain.competitor_prices.models import local_now
from app.domain.pricing_execution.models import PricingPreviewRecord
from app.domain.pricing_safety import (
    TRUSTED_PRICE_SOURCE,
    TRUSTED_SOURCE_STATEMENT,
    normalized_account_policy,
    strip_sensitive,
)


class PricingPreviewResult(BaseModel):
    status: str
    summary: str
    watch_group_key: str
    preview_id: int | None = None
    approval_id: str | None = None
    source_alert_record_id: int | None = None
    connection_mode: str = "unknown"
    can_auto_write_price: bool = False
    write_policy_message: str = ""
    preview_candidate: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


def pricing_account_policy_from_watch_group(group: dict[str, Any] | None) -> dict[str, Any]:
    account: Any | None = None
    if isinstance(group, dict):
        account = _pricing_account_from_watch_group_fields(
            browser_session_id=group.get("pricing_browser_session_id"),
            platform_account_id=group.get("pricing_platform_account_id"),
        )
    return _pricing_account_policy(account)


def pricing_account_policy_from_watch_group_model(group: Any | None) -> dict[str, Any]:
    account = _pricing_account_from_watch_group_fields(
        browser_session_id=getattr(group, "pricing_browser_session_id", None),
        platform_account_id=getattr(group, "pricing_platform_account_id", None),
    )
    return _pricing_account_policy(account)


def pricing_account_policy_from_browser_session(
    account: BrowserSession | str | None,
) -> dict[str, Any]:
    resolved = _pricing_account_from_browser_session(account)
    return _pricing_account_policy(resolved)


def pricing_account_policy_from_feishu_chat_id(chat_id: str | None) -> dict[str, Any]:
    return pricing_account_policy_from_browser_session(
        get_pricing_browser_session_for_feishu_chat_id(chat_id)
    )


async def create_pricing_preview_from_alert(
    *,
    alert_record: dict[str, Any],
    actor_id: str = "operator",
) -> PricingPreviewResult:
    await ensure_db_ready()
    policy = _policy_from_alert(alert_record)
    pricing_account = _public_pricing_account(policy)
    watch_group_key = str(alert_record.get("watch_group_key") or "")
    alert_id = _safe_int(alert_record.get("id") or alert_record.get("alert_record_id"))
    suggestions = [
        item
        for item in (alert_record.get("suggestions") or [])
        if isinstance(item, dict)
    ]
    if alert_record.get("status") != "suggestion_ready" or not suggestions:
        result = PricingPreviewResult(
            status="data_required",
            summary="缺少可执行的可信价格变化建议，未生成调价预览。",
            watch_group_key=watch_group_key,
            source_alert_record_id=alert_id,
            **policy,
            evidence={
                "trusted_price_source": TRUSTED_PRICE_SOURCE,
                "preview_created": False,
            },
            next_action="先完成竞品价格读取和价格变化提醒评估。",
        )
        return await _save_preview(alert_record, result, actor_id=actor_id)

    actions = [_action_from_suggestion(item) for item in suggestions]
    actions = [action for action in actions if action is not None]
    if not actions:
        result = PricingPreviewResult(
            status="data_required",
            summary="价格变化提醒没有完整的当前价、建议调整金额和建议后价格。",
            watch_group_key=watch_group_key,
            source_alert_record_id=alert_id,
            **policy,
            evidence={
                "trusted_price_source": TRUSTED_PRICE_SOURCE,
                "preview_created": False,
            },
            next_action="重新生成只包含可信 DOM 房价的价格变化提醒。",
        )
        return await _save_preview(alert_record, result, actor_id=actor_id)

    approval_id = f"pricing-approval-{uuid4().hex[:12]}"
    candidate = {
        "candidate_id": f"pricing-preview-{uuid4().hex[:12]}",
        "status": "pending_approval",
        "watch_group_key": watch_group_key,
        "source_alert_record_id": alert_id,
        "source_run_id": alert_record.get("current_run_id"),
        "approval_id": approval_id,
        "approval_source_required": "feishu_card",
        "trusted_price_source": TRUSTED_PRICE_SOURCE,
        "trusted_source_statement": TRUSTED_SOURCE_STATEMENT,
        "actions": actions,
        "safety": {
            "requires_preview": True,
            "requires_feishu_approval": True,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "can_auto_write_price": policy["can_auto_write_price"],
            "connection_mode": policy["connection_mode"],
        },
        "pricing_account": pricing_account,
    }
    first = actions[0]
    result = PricingPreviewResult(
        status="preview_ready",
        summary=(
            f"调价预览已生成：{first.get('checkin_date') or first.get('date_label')} "
            f"{first.get('room_type') or '-'}，当前价 {first.get('current_price_yuan')}，"
            f"建议后 {first.get('proposed_price_yuan')}。"
        ),
        watch_group_key=watch_group_key,
        approval_id=approval_id,
        source_alert_record_id=alert_id,
        **policy,
        preview_candidate=candidate,
        evidence={
            "trusted_price_source": TRUSTED_PRICE_SOURCE,
            "trusted_source_statement": TRUSTED_SOURCE_STATEMENT,
            "source_alert_status": alert_record.get("status"),
            "action_count": len(actions),
            "approval_source_required": "feishu_card",
            "api_execute_allowed": False,
        },
        next_action=(
            "发送 Feishu 审批卡片。"
            if policy["can_auto_write_price"]
            else policy["write_policy_message"]
        ),
    )
    return await _save_preview(alert_record, result, actor_id=actor_id)


async def create_pricing_reference_preview_from_alert(
    *,
    alert_record: dict[str, Any],
    actor_id: str = "operator",
    pricing_reference_summary: dict[str, Any] | None = None,
) -> PricingPreviewResult:
    """Persist a reference-only preview for competitor-triggered reference cards."""

    await ensure_db_ready()
    policy = _policy_from_alert(alert_record)
    pricing_account = _public_pricing_account(policy)
    watch_group_key = str(alert_record.get("watch_group_key") or "")
    alert_id = _safe_int(alert_record.get("id") or alert_record.get("alert_record_id"))
    suggestions = [
        item
        for item in (alert_record.get("suggestions") or [])
        if isinstance(item, dict)
    ]
    if alert_record.get("status") != "suggestion_ready" or not suggestions:
        result = PricingPreviewResult(
            status="data_required",
            summary="缺少可执行的竞品降价参考，未生成调价参考预览。",
            watch_group_key=watch_group_key,
            source_alert_record_id=alert_id,
            **policy,
            evidence={
                "trusted_price_source": TRUSTED_PRICE_SOURCE,
                "reference_preview_created": False,
            },
            next_action="先完成竞品价格读取和价格变化提醒评估。",
        )
        return await _save_preview(alert_record, result, actor_id=actor_id)

    actions = [_action_from_suggestion(item) for item in suggestions]
    actions = [action for action in actions if action is not None]
    if not actions:
        result = PricingPreviewResult(
            status="data_required",
            summary="价格变化提醒没有完整的当前价、建议调整金额和建议后价格。",
            watch_group_key=watch_group_key,
            source_alert_record_id=alert_id,
            **policy,
            evidence={
                "trusted_price_source": TRUSTED_PRICE_SOURCE,
                "reference_preview_created": False,
            },
            next_action="重新生成只包含可信 DOM 房价的价格变化提醒。",
        )
        return await _save_preview(alert_record, result, actor_id=actor_id)

    candidate = {
        "candidate_id": f"pricing-reference-{uuid4().hex[:12]}",
        "source": "competitor_price_alert_reference",
        "status": "reference_ready",
        "watch_group_key": watch_group_key,
        "source_alert_record_id": alert_id,
        "source_run_id": alert_record.get("current_run_id"),
        "requires_operator_target_price": True,
        "pricing_reference_summary": pricing_reference_summary or {},
        "trusted_price_source": TRUSTED_PRICE_SOURCE,
        "trusted_source_statement": TRUSTED_SOURCE_STATEMENT,
        "actions": actions,
        "pricing_account": pricing_account,
        "safety": {
            "requires_operator_target_price": True,
            "direct_feishu_approve_allowed": False,
            "requires_feishu_approval": True,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "can_auto_write_price": policy["can_auto_write_price"],
            "connection_mode": policy["connection_mode"],
        },
    }
    result = PricingPreviewResult(
        status="reference_ready",
        summary="竞品降价调价参考已生成，请店长自定义目标价。",
        watch_group_key=watch_group_key,
        source_alert_record_id=alert_id,
        **policy,
        preview_candidate=candidate,
        evidence={
            "trusted_price_source": TRUSTED_PRICE_SOURCE,
            "trusted_source_statement": TRUSTED_SOURCE_STATEMENT,
            "reference_preview_created": True,
            "requires_operator_target_price": True,
            "direct_feishu_approve_allowed": False,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "action_count": len(actions),
        },
        next_action="点击参考卡「自定义调价」输入或确认目标价。",
    )
    return await _save_preview(alert_record, result, actor_id=actor_id)


async def create_manual_pricing_preview(
    *,
    watch_group_key: str,
    room_type: str,
    date_label: str,
    target_price: int,
    checkin_date: str | None = None,
    pricing_browser_session_id: str | None = None,
    pricing_account: BrowserSession | None = None,
    actor_id: str = "operator",
    reason: str | None = None,
    platform: str | None = None,
) -> PricingPreviewResult:
    await ensure_db_ready()
    room_type = re.sub(r"\s+", " ", str(room_type or "")).strip()
    date_label = str(date_label or "").strip()
    checkin_date = str(checkin_date or "").strip() or None
    target_price = _safe_int(target_price) or 0
    resolved_account = _pricing_account_from_browser_session(
        pricing_account or pricing_browser_session_id
    )
    account_platform = _normalize_manual_platform(
        getattr(resolved_account, "platform", None)
    )
    requested_platform = _normalize_manual_platform(platform)
    policy = pricing_account_policy_from_browser_session(
        pricing_account or pricing_browser_session_id
    )
    normalized_policy = normalized_account_policy(policy)
    pricing_account = _public_pricing_account(policy)
    if not policy["pricing_account_configured"]:
        result = PricingPreviewResult(
            status="config_required",
            summary="监控组还没有绑定调价 EBK 账号。",
            watch_group_key=watch_group_key,
            **normalized_policy,
            evidence={
                "preview_type": "manual_price_adjustment",
                "preview_created": False,
                "pricing_account_configured": False,
                "api_execute_allowed": False,
                "frontend_execute_allowed": False,
            },
            next_action="请先在竞品价格监控组中选择“调价 EBK 账号”，再在群里重新发送调价指令。",
        )
        result.summary = "当前飞书群还没有绑定用于调价的 EBK 浏览器账号。"
        result.next_action = (
            "请先在“账号与浏览器”里给对应 EBK 账号绑定当前飞书调价审批群 chat_id，再重新发送调价指令。"
        )
        return await _save_preview(
            {
                "preview_type": "manual_price_adjustment",
                "room_type": room_type,
                "date_label": date_label,
                "target_price": target_price,
                "reason": reason,
                "pricing_account_configured": False,
            },
            result,
            actor_id=actor_id,
        )
    if requested_platform and account_platform and requested_platform != account_platform:
        result = PricingPreviewResult(
            status="config_required",
            summary=(
                f"当前飞书群绑定的调价账号是{_manual_platform_label(account_platform)}，"
                f"不能调整{_manual_platform_label(requested_platform)}的价格。"
            ),
            watch_group_key=watch_group_key,
            **normalized_policy,
            evidence={
                "preview_type": "manual_price_adjustment",
                "preview_created": False,
                "pricing_account_configured": True,
                "requested_platform": requested_platform,
                "account_platform": account_platform,
                "api_execute_allowed": False,
                "frontend_execute_allowed": False,
            },
            next_action=(
                "请核对指令中的平台词（携程/美团/飞猪），或在“账号与浏览器”里更换"
                "当前群绑定的调价 EBK 账号后重试。"
            ),
        )
        return await _save_preview(
            {
                "preview_type": "manual_price_adjustment",
                "room_type": room_type,
                "date_label": date_label,
                "target_price": target_price,
                "reason": reason,
                "requested_platform": requested_platform,
                "account_platform": account_platform,
            },
            result,
            actor_id=actor_id,
        )
    if (
        not room_type
        or (date_label not in {"today", "tomorrow"} and not checkin_date)
        or target_price <= 0
    ):
        result = PricingPreviewResult(
            status="data_required",
            summary="手动调价预览需要明确房型、入住日期（今天/明天或具体日期）和目标售价。",
            watch_group_key=watch_group_key,
            **normalized_policy,
            evidence={
                "preview_type": "manual_price_adjustment",
                "preview_created": False,
                "pricing_account_configured": True,
                "api_execute_allowed": False,
            },
            next_action="请在飞书里发送类似：把今天高级大床房调到420，或把9月15日高级大床房调到420。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_price_adjustment",
                "room_type": room_type,
                "date_label": date_label,
                "target_price": target_price,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )

    effective_platform = requested_platform or account_platform or "ctrip"
    # 非法 ISO（如「9月20日」原文混进 checkin_date）不得让预览 500：
    # 解析失败按缺日期处理，回退 date_label 口径或走 data_required。
    resolved_checkin_date: date | None = None
    if checkin_date:
        try:
            resolved_checkin_date = date.fromisoformat(checkin_date)
        except ValueError:
            resolved_checkin_date = None
    invalid_checkin_date = checkin_date if (checkin_date and resolved_checkin_date is None) else None
    # 窗口兜底：与 manual_price_command 的 15 天窗口一致，拦截 MCP 工具
    # 直传的超窗口日期（过去/太远），不进审批。
    from app.domain.notifications.manual_price_command import (
        MANUAL_CHECKIN_MAX_DAYS_AHEAD,
    )

    if resolved_checkin_date is not None:
        today = local_now().date()
        if resolved_checkin_date < today or (
            (resolved_checkin_date - today).days > MANUAL_CHECKIN_MAX_DAYS_AHEAD
        ):
            invalid_checkin_date = resolved_checkin_date.isoformat()
            resolved_checkin_date = None
    if invalid_checkin_date is not None and date_label not in {"today", "tomorrow"}:
        result = PricingPreviewResult(
            status="data_required",
            summary=(
                f"入住日期 {invalid_checkin_date} 无效或超出今天起 "
                f"{MANUAL_CHECKIN_MAX_DAYS_AHEAD} 天的可调范围，请改用今天/明天或窗口内具体日期。"
            ),
            watch_group_key=watch_group_key,
            **normalized_policy,
            evidence={
                "preview_type": "manual_price_adjustment",
                "preview_created": False,
                "invalid_checkin_date": invalid_checkin_date,
            },
            next_action="请把日期改为今天/明天，或用窗口内 YYYY-MM-DD（如 2026-09-20）。",
        )
        return await _save_preview(
            {
                "preview_type": "manual_price_adjustment",
                "room_type": room_type,
                "date_label": date_label,
                "checkin_date": checkin_date,
                "target_price": target_price,
                "reason": reason,
            },
            result,
            actor_id=actor_id,
        )
    if resolved_checkin_date is None:
        resolved_checkin_date = _manual_checkin_date(date_label)
    checkout_date = resolved_checkin_date + timedelta(days=1)
    commission_rate = DEFAULT_COMMISSION_RATE
    current_rate = await _read_manual_current_rate(
        actor_id=actor_id,
        pricing_account=pricing_account,
        pricing_browser_session_id=policy.get("pricing_browser_session_id"),
        platform_account_id=policy.get("pricing_platform_account_id"),
        watch_group_key=watch_group_key,
        platform=effective_platform,
        room_type=room_type,
        checkin_date=resolved_checkin_date.isoformat(),
        checkout_date=checkout_date.isoformat(),
        target_price=target_price,
        commission_rate=commission_rate,
    )
    raw_readback = current_rate.get("readback")
    readback = raw_readback if isinstance(raw_readback, dict) else {}
    read_status = str(current_rate.get("status") or "skipped")
    read_reason = _manual_current_rate_read_reason(current_rate)
    current_price = _safe_int(readback.get("current_price_yuan"))
    current_base = (
        sell_to_base(current_price, commission_rate)
        if current_price is not None
        else _safe_float(readback.get("current_base_price_yuan"))
    )
    risk_flags = ["manual_price_adjustment", "requires_preflight_revalidation"]
    if read_status != "success" or current_price is None:
        risk_flags.append("current_price_readback_missing")

    approval_id = f"pricing-approval-{uuid4().hex[:12]}"
    action = {
        "action_id": f"manual-price-{uuid4().hex[:12]}",
        "operation": "set_room_price",
        "source": "manual_feishu_command",
        "platform": effective_platform,
        "date_label": date_label,
        "checkin_date": resolved_checkin_date.isoformat(),
        "checkout_date": checkout_date.isoformat(),
        "room_type": room_type,
        "normalized_room_type": room_type,
        "breakfast_kind": "unknown",
        "breakfast_text": "早餐未确认",
        "current_price_yuan": current_price,
        "before_price_yuan": current_price,
        "current_base_price_yuan": current_base,
        "room_status_text": readback.get("room_status_text"),
        "room_inventory_text": readback.get("room_inventory_text"),
        "recommended_adjustment_yuan": None,
        "proposed_price_yuan": target_price,
        "target_price_yuan": target_price,
        "proposed_base_price_yuan": sell_to_base(target_price, commission_rate),
        "commission_rate": commission_rate,
        "trigger_reason": (reason or "Feishu 手动调价指令。").strip(),
        "needs_human_attention": True,
        "risk_flags": risk_flags,
        "current_rate_readback": readback or None,
        "current_rate_readback_status": read_status,
        "current_rate_readback_reason": read_reason,
        "current_rate_readback_summary": current_rate.get("summary"),
        "write_intent": {
            "tool_name": (
                f"ota.{effective_platform}_ebooking_price_write_after_feishu_approval"
            ),
            "arguments": {
                "platform": effective_platform,
                "room_type": room_type,
                "checkin_date": resolved_checkin_date.isoformat(),
                "checkout_date": checkout_date.isoformat(),
                "target_price": target_price,
            },
        },
    }
    candidate = {
        "candidate_id": f"pricing-preview-{uuid4().hex[:12]}",
        "status": "pending_approval",
        "watch_group_key": watch_group_key,
        "preview_type": "manual_price_adjustment",
        "source": "manual_feishu_command",
        "approval_id": approval_id,
        "approval_source_required": "feishu_card",
        "trusted_price_source": TRUSTED_PRICE_SOURCE,
        "trusted_source_statement": TRUSTED_SOURCE_STATEMENT,
        "pricing_account": pricing_account,
        "actions": [action],
        "safety": {
            "requires_preview": True,
            "requires_feishu_approval": True,
            "requires_preflight_revalidation": True,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "can_auto_write_price": normalized_policy["can_auto_write_price"],
            "connection_mode": normalized_policy["connection_mode"],
        },
    }
    result = PricingPreviewResult(
        status="preview_ready",
        summary=(
            f"手动调价预览已生成：{room_type} "
            f"{resolved_checkin_date.isoformat() if checkin_date else _display_date_label(date_label)} "
            f"-> ¥{target_price}。"
        ),
        watch_group_key=watch_group_key,
        approval_id=approval_id,
        **normalized_policy,
        preview_candidate=candidate,
        evidence={
            "preview_type": "manual_price_adjustment",
            "manual_price_adjustment": True,
            "approval_source_required": "feishu_card",
            "requires_preflight_revalidation": True,
            "api_execute_allowed": False,
            "frontend_execute_allowed": False,
            "pricing_account_configured": True,
            "pricing_account": pricing_account,
            "current_price_readback": strip_sensitive(current_rate),
        },
        next_action=(
            "发送 Feishu 调价审批卡片。"
            if normalized_policy["can_auto_write_price"]
            else normalized_policy["write_policy_message"]
        ),
    )
    return await _save_preview(
        {
            "preview_type": "manual_price_adjustment",
            "source": "manual_feishu_command",
            "room_type": room_type,
            "date_label": date_label,
            "target_price": target_price,
            "reason": reason,
            "pricing_account_configured": True,
            "pricing_account": pricing_account,
        },
        result,
        actor_id=actor_id,
    )


async def get_pricing_preview(preview_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PricingPreviewRecord, preview_id)
    return _preview_record_to_dict(record) if record is not None else None


def _policy_from_alert(alert_record: dict[str, Any]) -> dict[str, Any]:
    evidence = alert_record.get("evidence") if isinstance(alert_record.get("evidence"), dict) else {}
    policy = evidence.get("account_write_policy") if isinstance(evidence.get("account_write_policy"), dict) else {}
    normalized = normalized_account_policy(
        {
            **policy,
            "connection_mode": alert_record.get("connection_mode") or policy.get("connection_mode"),
            "can_auto_write_price": alert_record.get("can_auto_write_price", policy.get("can_auto_write_price")),
            "write_policy_message": alert_record.get("write_policy_message") or policy.get("write_policy_message"),
        }
    )
    return {
        **normalized,
        "pricing_account_configured": bool(policy.get("pricing_account_configured")),
        "pricing_platform_account_id": policy.get("pricing_platform_account_id"),
        "pricing_browser_session_id": policy.get("pricing_browser_session_id"),
    }


def _pricing_account_from_browser_session(account: BrowserSession | str | None):
    if account is None:
        return None
    if isinstance(account, str):
        try:
            session = get_browser_session_store().get(account)
        except KeyError:
            return None
        return session if session.intended_use == "ebk" else None
    if getattr(account, "intended_use", None) == "ebk":
        return account
    return None


def _pricing_account_from_watch_group_fields(
    *,
    browser_session_id: Any,
    platform_account_id: Any,
):
    browser_session_id = str(browser_session_id or "").strip()
    platform_account_id = str(platform_account_id or "").strip()
    if browser_session_id:
        return _pricing_account_from_browser_session(browser_session_id)
    if platform_account_id:
        account = get_browser_session_for_platform_account(platform_account_id)
        return account if getattr(account, "intended_use", None) == "ebk" else None
    return None


def _pricing_account_policy(account: Any | None) -> dict[str, Any]:
    policy = account_write_policy(account)
    return {
        **policy,
        "pricing_account_configured": bool(account),
        "pricing_platform_account_id": getattr(account, "platform_account_id", None),
        "pricing_browser_session_id": getattr(account, "browser_session_id", None),
    }


def _public_pricing_account(policy: dict[str, Any]) -> dict[str, Any] | None:
    if not policy.get("pricing_account_configured"):
        return None
    return {
        "platform_account_id": policy.get("pricing_platform_account_id"),
        "browser_session_id": policy.get("pricing_browser_session_id"),
        "connection_mode": policy.get("connection_mode") or "unknown",
        "can_auto_write_price": bool(policy.get("can_auto_write_price")),
    }


def _action_from_suggestion(item: dict[str, Any]) -> dict[str, Any] | None:
    current_price = _safe_int(item.get("current_own_price_yuan"))
    recommended_price = _safe_int(item.get("recommended_price_yuan"))
    adjustment = _safe_int(item.get("recommended_adjustment_yuan"))
    if current_price is None or recommended_price is None or adjustment is None:
        return None
    if recommended_price < 0:
        return None
    commission_rate = DEFAULT_COMMISSION_RATE
    return {
        "action_id": f"pricing-action-{uuid4().hex[:12]}",
        "operation": "set_room_price",
        "platform": "ctrip",
        "hotel_config_id": item.get("hotel_config_id"),
        "hotel_name": item.get("hotel_name"),
        "date_label": item.get("date_label"),
        "checkin_date": item.get("checkin_date"),
        "checkout_date": item.get("checkout_date"),
        "room_type": item.get("room_type"),
        "normalized_room_type": item.get("normalized_room_type"),
        "breakfast_kind": item.get("breakfast_kind"),
        "breakfast_text": item.get("breakfast_text"),
        "current_price_yuan": current_price,
        "before_price_yuan": current_price,
        "current_base_price_yuan": item.get("current_base_price_yuan"),
        "recommended_adjustment_yuan": adjustment,
        "proposed_price_yuan": recommended_price,
        "target_price_yuan": recommended_price,
        "proposed_base_price_yuan": sell_to_base(recommended_price, commission_rate),
        "commission_rate": item.get("commission_rate") or commission_rate,
        "competitor_previous_price_yuan": item.get("competitor_previous_price_yuan"),
        "competitor_current_price_yuan": item.get("competitor_current_price_yuan"),
        "change_amount_yuan": item.get("change_amount_yuan"),
        "change_percent": item.get("change_percent"),
        "trigger_reason": item.get("trigger_reason"),
        "needs_human_attention": bool(item.get("needs_human_attention")),
        "source": TRUSTED_PRICE_SOURCE,
        "trusted_source_statement": TRUSTED_SOURCE_STATEMENT,
        "write_intent": {
            "tool_name": "ota.ctrip_ebooking_price_write_after_feishu_approval",
            "arguments": {
                "platform": "ctrip",
                "room_type": item.get("room_type"),
                "checkin_date": item.get("checkin_date"),
                "checkout_date": item.get("checkout_date"),
                "target_price": recommended_price,
            },
        },
    }


async def _save_preview(
    alert_record: dict[str, Any],
    result: PricingPreviewResult,
    *,
    actor_id: str,
) -> PricingPreviewResult:
    async with session_scope() as session:
        record = PricingPreviewRecord(
            watch_group_key=result.watch_group_key or "unknown",
            source_alert_record_id=result.source_alert_record_id,
            status=result.status,
            summary=result.summary,
            connection_mode=result.connection_mode,
            can_auto_write_price=result.can_auto_write_price,
            write_policy_message=result.write_policy_message,
            preview_candidate_json=strip_sensitive(result.preview_candidate),
            source_alert_json=strip_sensitive(alert_record),
            actor_id=actor_id.strip() or "operator",
        )
        session.add(record)
        await session.flush()
        result.preview_id = int(record.id)
        if result.preview_candidate:
            result.preview_candidate["pricing_preview_record_id"] = int(record.id)
            record.preview_candidate_json = strip_sensitive(result.preview_candidate)
    return result


def _preview_record_to_dict(record: PricingPreviewRecord) -> dict[str, Any]:
    candidate = record.preview_candidate_json or {}
    return {
        "preview_id": record.id,
        "id": record.id,
        "status": record.status,
        "summary": record.summary,
        "watch_group_key": record.watch_group_key,
        "source_alert_record_id": record.source_alert_record_id,
        "connection_mode": record.connection_mode,
        "can_auto_write_price": record.can_auto_write_price,
        "write_policy_message": record.write_policy_message,
        "approval_id": candidate.get("approval_id") if isinstance(candidate, dict) else None,
        "preview_candidate": candidate,
        "created_at": record.created_at.isoformat(),
    }


_MANUAL_PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}


def _normalize_manual_platform(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _MANUAL_PLATFORM_LABELS else None


def _manual_platform_label(platform: str | None) -> str:
    return _MANUAL_PLATFORM_LABELS.get(
        str(platform or "").strip().lower(), str(platform or "未知")
    )


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _manual_checkin_date(date_label: str):
    today = local_now().date()
    return today + timedelta(days=1 if date_label == "tomorrow" else 0)


def _display_date_label(date_label: str) -> str:
    if date_label == "today":
        return "今日入住"
    if date_label == "tomorrow":
        return "明日入住"
    return date_label


async def _read_manual_current_rate(
    *,
    actor_id: str,
    pricing_account: BrowserSession | None,
    pricing_browser_session_id: str | None,
    platform_account_id: str | None,
    watch_group_key: str,
    platform: str,
    room_type: str,
    checkin_date: str,
    checkout_date: str,
    target_price: int,
    commission_rate: float,
) -> dict[str, Any]:
    browser_session_id = str(
        pricing_browser_session_id
        or getattr(pricing_account, "browser_session_id", "")
        or ""
    ).strip()
    resolved_platform_account_id = str(
        platform_account_id or getattr(pricing_account, "platform_account_id", "") or ""
    ).strip()
    if not browser_session_id or not resolved_platform_account_id:
        return {
            "status": "human_required",
            "reason": "browser_session_missing",
            "watch_group_key": watch_group_key,
        }

    effective_platform = _normalize_manual_platform(platform) or "ctrip"
    if effective_platform != "ctrip":
        # 美团/飞猪的批量改价页现价读取尚未接入；宁可明确说明读取不到，
        # 也不能拿美团/飞猪账号去打开携程日历。
        return {
            "status": "skipped",
            "reason": f"{effective_platform}_current_rate_read_not_supported",
            "platform": effective_platform,
            "platform_account_id": resolved_platform_account_id,
            "browser_session_id": browser_session_id,
            "watch_group_key": watch_group_key,
            "summary": (
                f"{_manual_platform_label(effective_platform)} EBK 现价读取暂未接入，"
                "审批卡当前价显示为未读取到；空跑与执行仍会按页面实际值校验。"
            ),
        }

    execute_request = {
        "request_id": f"manual-current-rate-{uuid4().hex[:12]}",
        "actions": [
            {
                "operation": "read_current_room_price",
                "platform": "ctrip",
                "room_type": room_type,
                "checkin_date": checkin_date,
                "checkout_date": checkout_date,
                "proposed_price_yuan": target_price,
                "proposed_base_price_yuan": sell_to_base(target_price, commission_rate),
                "commission_rate": commission_rate,
            }
        ],
    }
    from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service

    try:
        result = await asyncio.wait_for(
            get_ctrip_ebooking_service().read_pricing_calendar_current_rates(
                platform_account_id=resolved_platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                execute_request=execute_request,
            ),
            # 2026-09-14：读价链路改有界等待信号收敛（最长 15s）后，20s 外层
            # 超时会截断慢渲染页的正常读取，放宽到 60s 与 goto 预算对齐。
            timeout=60,
        )
    except TimeoutError:
        return {
            "status": "failed",
            "reason": "current_rate_browser_read_timeout",
            "platform_account_id": resolved_platform_account_id,
            "browser_session_id": browser_session_id,
            "watch_group_key": watch_group_key,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "reason": "current_rate_browser_read_failed",
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:240],
            "platform_account_id": resolved_platform_account_id,
            "browser_session_id": browser_session_id,
            "watch_group_key": watch_group_key,
        }

    payload = result.model_dump(mode="json")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    page_read = data.get("page_read") if isinstance(data.get("page_read"), dict) else {}
    targets = page_read.get("targets") if isinstance(page_read.get("targets"), list) else []
    readback = next((item for item in targets if isinstance(item, dict)), {})
    status = result.status
    if result.status == "success":
        status = "success" if readback and readback.get("status") == "located" else "not_found"
    return {
        "status": status,
        "reason": "current_rate_read",
        "platform_account_id": resolved_platform_account_id,
        "browser_session_id": browser_session_id,
        "watch_group_key": watch_group_key,
        "readback": readback,
        "summary": result.summary,
        "evidence": payload.get("evidence"),
        "next_action": result.next_action,
    }


def _manual_current_rate_read_reason(current_rate: dict[str, Any]) -> str | None:
    evidence = current_rate.get("evidence")
    if isinstance(evidence, dict):
        reason = (
            evidence.get("human_required_reason")
            or evidence.get("blocked_reason")
            or evidence.get("reason")
        )
        if reason:
            return str(reason)
    reason = current_rate.get("reason")
    return str(reason).strip() if reason else None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
