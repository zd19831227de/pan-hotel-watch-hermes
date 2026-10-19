from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.domain.ai_agent.agent import run_default_ai_agent
from app.domain.ai_agent.registry import AgentToolCallContext
from app.domain.competitor_prices.types import DEFAULT_WATCH_GROUP_KEY
from app.domain.browser.sessions import (
    get_browser_session_store,
    get_pricing_browser_session_for_feishu_chat_id,
)
from app.domain.notifications.feishu_client import FeishuBotClient
from app.domain.notifications.feishu_app_credentials import resolve_feishu_app_profile
from app.domain.notifications.manual_price_command import classify_manual_price_adjustment_command
from app.domain.notifications.pricing_query_command import classify_pricing_query_command
from app.domain.pyramid_bidding.cards import (
    build_pyramid_bid_approval_card,
    build_pyramid_bid_approval_fallback_text,
    build_pyramid_bid_confirmation_card,
    build_pyramid_bid_list_card,
    build_pyramid_bid_list_fallback_text,
)
from app.domain.pyramid_bidding.commands import classify_pyramid_bid_command
from app.domain.pyramid_bidding.pipeline import queue_pyramid_bid_execution_pipeline
from app.domain.pyramid_bidding.preview import (
    PYRAMID_PLATFORM,
    create_pyramid_bid_preview,
    read_pyramid_promotions_for_account,
)
from app.domain.promotion_adjust.cards import (
    build_promotion_adjust_approval_card,
    build_promotion_adjust_approval_fallback_text,
    build_promotion_adjust_confirmation_card,
    build_promotion_adjust_list_card,
    build_promotion_adjust_list_fallback_text,
)
from app.domain.promotion_adjust.commands import classify_promotion_adjust_command
from app.domain.promotion_adjust.pipeline import queue_promotion_adjust_execution_pipeline
from app.domain.promotion_adjust.preview import (
    PROMOTION_PLATFORM,
    create_promotion_adjust_preview,
    read_promotion_enrollments_for_account,
)
from app.domain.ops.feishu_chat_context import build_feishu_chat_context
from app.domain.pricing_approvals.cards import (
    build_custom_price_form_card,
    build_operator_target_price_confirmation_card,
    build_manual_price_confirmation_card,
    build_manual_price_confirmation_fallback_text,
    build_pricing_approval_card,
    build_pricing_approval_fallback_text,
)
from app.domain.pricing_approvals.service import record_pricing_approval_decision
from app.domain.pricing_safety import (
    account_policy_allows_write,
    normalized_account_policy,
    pricing_platform_from_preview_payload,
    write_not_allowed_reason,
)
from app.domain.pricing_suggestions.preview import (
    create_manual_pricing_preview,
    get_pricing_preview,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeishuCommandContext:
    event_id: str
    message_id: str
    chat_id: str
    open_id: str | None
    command: str
    store_hint: str | None = None
    mentioned: bool = False


def _feishu_client_factory(chat_id: str | None = None) -> FeishuBotClient:
    return FeishuBotClient(chat_id=chat_id)


def _feishu_agent_mode() -> str:
    """飞书入口模式。默认 hermes：自由文本由 Hermes 接管，后端不调用内置大模型。"""
    from app.core.settings import get_settings

    return (get_settings().feishu_agent_mode or "hermes").strip().lower()


async def handle_event(payload: dict[str, Any]) -> dict[str, Any]:
    verification = _handle_url_verification(payload)
    if verification is not None:
        return verification

    routing_rejection = await _strict_tenant_routing_rejection(payload)
    if routing_rejection is not None:
        return routing_rejection

    reference_defer = parse_pricing_reference_defer_action(payload)
    if reference_defer is not None:
        return await handle_pricing_reference_defer_action(reference_defer)

    custom_cancel = parse_pricing_custom_price_cancel(payload)
    if custom_cancel is not None:
        return await handle_pricing_custom_price_cancel(custom_cancel)

    custom_submit = parse_pricing_custom_price_submit(payload)
    if custom_submit is not None:
        return await handle_pricing_custom_price_submit(custom_submit)

    custom_action = parse_pricing_custom_price_action(payload)
    if custom_action is not None:
        return await handle_pricing_custom_price_action(custom_action)

    action = parse_pricing_approval_card_action(payload)
    if action is not None:
        return await handle_pricing_approval_card_action(action)

    pyramid_action = parse_pyramid_bid_approval_card_action(payload)
    if pyramid_action is not None:
        return await handle_pyramid_bid_approval_card_action(pyramid_action)

    promotion_action = parse_promotion_adjust_approval_card_action(payload)
    if promotion_action is not None:
        return await handle_promotion_adjust_approval_card_action(promotion_action)

    command = parse_command_context(payload)
    if command is not None:
        pyramid_classification = classify_pyramid_bid_command(command.command)
        if pyramid_classification.get("status") != "not_pyramid":
            return await handle_pyramid_bid_command(
                command,
                classification=pyramid_classification,
            )
        promotion_classification = classify_promotion_adjust_command(command.command)
        if promotion_classification.get("status") != "not_promotion":
            return await handle_promotion_adjust_command(
                command,
                classification=promotion_classification,
            )
        pricing_query = classify_pricing_query_command(command.command)
        if pricing_query is not None:
            return await handle_pricing_query_command(command, query=pricing_query)
        classification = classify_manual_price_adjustment_command(command.command)
        if classification.get("status") == "ready":
            return await handle_manual_price_adjustment_command(
                command,
                manual_request=classification,
            )
        if classification.get("status") == "needs_confirmation":
            return await handle_manual_price_adjustment_confirmation(
                command,
                classification=classification,
            )
        watch_group_key = await _resolve_watch_group_key_for_chat(command.chat_id)
        if _is_pricing_command(command.command):
            return await handle_feishu_pricing_command(command, watch_group_key=watch_group_key)
        if command.mentioned:
            if _feishu_agent_mode() == "hermes":
                return _hermes_mode_free_text_ignored()
            return await handle_ai_agent_command(command)

    return {
        "status": "ignored",
        "summary": "Feishu event was not a pricing command or pricing approval action.",
        "evidence": {"execute_triggered": False},
    }


async def _strict_tenant_routing_rejection(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    from app.core.settings import get_settings

    if not get_settings().feishu_strict_tenant_routing:
        return None

    chat_id = _event_chat_id(payload)
    chat_fingerprint = _routing_fingerprint(chat_id) if chat_id else None
    profile_key: str | None = None
    error_type: str | None = None
    if not chat_id:
        error_type = "chat_context_missing"
    else:
        from app.domain.config.service import resolve_active_feishu_chat_agent_profile

        try:
            profile_key = await resolve_active_feishu_chat_agent_profile(chat_id)
        except ValueError:
            error_type = "binding_not_active"
        else:
            configured_profile = resolve_feishu_app_profile(chat_id=chat_id)
            if configured_profile != profile_key:
                error_type = "profile_mismatch"

    if error_type is None:
        return None

    # Hermes does not forward a trusted X-Lark-App-ID. Do not treat a body field
    # as authenticated source identity until that transport boundary is fixed.
    logger.warning(
        "feishu event routing rejected profile_key=%s chat_fingerprint=%s error_type=%s",
        profile_key or "unresolved",
        chat_fingerprint or "missing",
        error_type,
    )
    return {
        "status": "config_required",
        "summary": "当前飞书群未完成有效的 Agent 与凭据绑定，已拒绝处理。",
        "evidence": {
            "execute_triggered": False,
            "ota_write_invoked": False,
            "profile_key": profile_key,
            "chat_fingerprint": chat_fingerprint,
            "error_type": error_type,
        },
        "next_action": "请检查 Agent profile、业务群绑定和受控飞书凭据配置。",
    }


def _event_chat_id(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    candidates = (
        message.get("chat_id"),
        context.get("open_chat_id"),
        event.get("chat_id"),
        event.get("open_chat_id"),
        payload.get("chat_id"),
        payload.get("open_chat_id"),
    )
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return ""


def _routing_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _hermes_mode_free_text_ignored() -> dict[str, Any]:
    """hermes 模式下，飞书自由文本由 Hermes Agent 处理，后端不调用内置大模型。"""
    return {
        "status": "ignored_hermes_mode",
        "summary": "飞书自然语言对话已由 Hermes 接管，后端不再调用内置大模型。",
        "evidence": {
            "execute_triggered": False,
            "ota_write_invoked": False,
            "feishu_agent_mode": "hermes",
        },
    }


async def handle_ai_agent_command(context: FeishuCommandContext) -> dict[str, Any]:
    agent_result = await run_default_ai_agent(
        context.command,
        context=AgentToolCallContext(
            event_id=context.event_id,
            message_id=context.message_id,
            chat_id=context.chat_id,
            open_id=context.open_id,
        ),
    )
    reply_text = _ai_agent_reply_text(agent_result)
    client = _feishu_client_factory(context.chat_id)
    push = await client.reply_text(context.message_id, reply_text)
    return {
        "status": "ai_agent_replied",
        "summary": agent_result.summary,
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "ota_write_invoked": bool(agent_result.evidence.get("ota_write_invoked")),
            "ai_agent_status": agent_result.status,
            "agent_tool_count": len(agent_result.tool_results),
        },
        "next_action": agent_result.next_action,
    }


async def handle_feishu_pricing_command(
    context: FeishuCommandContext,
    *,
    watch_group_key: str | None = None,
) -> dict[str, Any]:
    group_key = watch_group_key or DEFAULT_WATCH_GROUP_KEY
    command_kind = _pricing_command_kind(context.command)
    if command_kind is None:
        return {
            "status": "ignored",
            "summary": "不是调价建议或调价命令。",
            "evidence": {"execute_triggered": False},
        }

    client = _feishu_client_factory(context.chat_id)
    text = (
        "竞品价格轮询成功后，系统会比对相邻两次可信读取；跨阈值时自动发价格变化提醒。\n"
        "竞品降价时：非直连且已绑调价 EBK 的组会发降价参考卡（自定义调价/暂不处理）；"
        "直连或未确认账号只发提醒和建议，不发参考卡。\n"
        "竞品涨价时：只发变化提醒，不会建议本店跟涨。\n\n"
        "若要指定房型目标价，请直接说：把今天高级大床房调到420。"
    )
    push = await client.reply_text(
        context.message_id,
        text,
    )
    return {
        "status": "pricing_help_replied",
        "summary": "已说明自动调价建议的触发方式。",
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "price_change_alert_manual_evaluation": False,
            "auto_trigger": "competitor_polling_after_persistence",
            "watch_group_key": group_key,
            "command": command_kind,
        },
    }


async def handle_manual_price_adjustment_confirmation(
    context: FeishuCommandContext,
    *,
    classification: dict[str, Any],
) -> dict[str, Any]:
    client = _feishu_client_factory(context.chat_id)
    card = build_manual_price_confirmation_card(classification)
    push = await client.reply_interactive_card(
        context.message_id,
        card,
        fallback_text=build_manual_price_confirmation_fallback_text(classification),
    )
    return {
        "status": "needs_confirmation",
        "summary": str(classification.get("summary") or "手动调价信息不完整，请补全后重试。"),
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "manual_price_adjustment": True,
            "approval_card_sent": False,
            "confirmation_card_sent": True,
            "missing_fields": classification.get("missing_fields") or [],
            "partial": classification.get("partial") or {},
        },
        "next_action": "请按卡片示例补全房型、今天/明天和目标价后重新发送。",
    }


def get_browser_session(browser_session_id: str | None) -> Any | None:
    session_id = _optional_str(browser_session_id)
    if not session_id:
        return None
    try:
        return get_browser_session_store().get(session_id)
    except ValueError:
        return None


def _manual_request_with_store_hint(
    manual_request: dict[str, Any],
    store_hint: str | None,
) -> tuple[dict[str, Any], str | None]:
    hint = _optional_str(store_hint)
    room_type = str(manual_request.get("room_type") or "").strip()
    if hint or not room_type:
        return manual_request, hint

    match = re.match(
        r"^(?P<store>[\w\u4e00-\u9fff·（）() -]{2,40}?(?:酒店|门店|宾馆|民宿|公寓|全套房酒店))的(?P<room>.+)$",
        room_type,
    )
    if match is None:
        return manual_request, hint

    inferred_hint = _optional_str(match.group("store"))
    cleaned_room_type = _optional_str(match.group("room"))
    if inferred_hint and cleaned_room_type:
        inferred_hint = re.sub(r"(?:酒店|门店|宾馆|民宿|公寓)$", "", inferred_hint).strip() or inferred_hint
        cleaned = dict(manual_request)
        cleaned["room_type"] = cleaned_room_type
        return cleaned, inferred_hint
    return manual_request, hint


async def _resolve_manual_pricing_target(
    *,
    chat_id: str | None,
    store_hint: str | None,
) -> tuple[str | None, Any | None]:
    hint = _optional_str(store_hint)
    pricing_account = get_pricing_browser_session_for_feishu_chat_id(chat_id)
    if hint:
        context = await build_feishu_chat_context(chat_id, store_hint=hint)
        resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
        if resolution.get("status") == "resolved":
            group_key = _optional_str(
                resolution.get("competitor_watch_group_key")
                or context.get("competitor_watch_group_key")
            )
            return group_key, pricing_account
        return None, None

    return None, pricing_account


async def handle_manual_price_adjustment_command(
    context: FeishuCommandContext,
    *,
    manual_request: dict[str, Any],
    watch_group_key: str | None = None,
) -> dict[str, Any]:
    manual_request, store_hint = _manual_request_with_store_hint(
        manual_request,
        context.store_hint,
    )
    try:
        resolved_group_key, pricing_account = await _resolve_manual_pricing_target(
            chat_id=context.chat_id,
            store_hint=store_hint,
        )
    except ValueError as exc:
        client = _feishu_client_factory(context.chat_id)
        message = (
            "当前飞书群绑定了多个 EBK 调价账号，系统无法判断要改哪个后台价格。\n"
            "请在“账号与浏览器”里只保留一个 EBK 账号绑定当前群 chat_id 后重试。"
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "config_required",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "manual_price_adjustment": True,
                "pricing_account_configured": False,
                "error_type": type(exc).__name__,
            },
            "next_action": "修正 EBK 账号上的调价审批群绑定。",
        }
    group_key = resolved_group_key or watch_group_key or DEFAULT_WATCH_GROUP_KEY
    preview = await create_manual_pricing_preview(
        watch_group_key=group_key,
        room_type=str(manual_request["room_type"]),
        date_label=str(manual_request["date_label"]),
        target_price=int(manual_request["target_price"]),
        checkin_date=manual_request.get("checkin_date"),
        pricing_account=pricing_account,
        actor_id=_actor_id(context),
        reason=context.command,
        platform=manual_request.get("platform"),
    )
    preview_payload = preview.model_dump(mode="json")
    client = _feishu_client_factory(context.chat_id)
    if preview.status != "preview_ready":
        message = "\n".join(
            item
            for item in [
                preview.summary,
                preview.next_action,
            ]
            if item
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": preview.status,
            "summary": preview.summary,
            "pricing_preview": preview_payload,
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "manual_price_adjustment": True,
                "approval_card_sent": False,
                "can_auto_write_price": preview.can_auto_write_price,
                "connection_mode": preview.connection_mode,
            },
            "next_action": preview.next_action,
        }
    card = build_pricing_approval_card(preview_payload)
    push = await client.reply_interactive_card(
        context.message_id,
        card,
        fallback_text=build_pricing_approval_fallback_text(preview_payload),
    )
    return {
        "status": "approval_card_replied",
        "summary": "已回复手动调价审批卡片。",
        "pricing_preview": preview_payload,
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "approval_source_required": "feishu_card",
            "manual_price_adjustment": True,
            "can_auto_write_price": preview.can_auto_write_price,
            "connection_mode": preview.connection_mode,
            "execute_button_present": preview.can_auto_write_price,
            "push_to_feishu": False,
            "command": "manual_price_adjustment",
        },
    }


async def handle_pricing_query_command(
    context: FeishuCommandContext,
    *,
    query: dict[str, Any],
) -> dict[str, Any]:
    """「查房价」指令：读当前群绑定门店三平台 EBK 房型房价名单并回卡片。"""

    from datetime import timedelta

    from app.domain.browser.pricing_room_rates import platform_read_error_payload
    from app.domain.competitor_prices.models import local_now
    from app.domain.notifications.pricing_query_card import (
        build_pricing_query_cards,
        build_pricing_query_fallback_text,
    )

    date_label = str(query.get("date_label") or "today")
    requested_platform = str(query.get("platform") or "").strip().lower() or None
    checkin_date = (
        local_now().date() + timedelta(days=1 if date_label == "tomorrow" else 0)
    ).isoformat()

    chat_context = await build_feishu_chat_context(context.chat_id, store_hint=context.store_hint)
    accounts = chat_context.get("ota_ebk_accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    resolution = (
        chat_context.get("resolution")
        if isinstance(chat_context.get("resolution"), dict)
        else {}
    )
    store_name = resolution.get("store_name")
    if not store_name:
        catalog = chat_context.get("store_catalog")
        if isinstance(catalog, list) and catalog and isinstance(catalog[0], dict):
            store_name = catalog[0].get("store_name")

    platform_results: list[dict[str, Any]] = []
    platform_order = (
        (requested_platform,)
        if requested_platform
        else ("ctrip", "meituan", "fliggy")
    )
    for platform in platform_order:
        account = accounts.get(platform) if isinstance(accounts.get(platform), dict) else None
        if account is None:
            # 未绑定也要在卡片上显示（2026-09-23 修复）：此前未点名平台时
            # 静默跳过，卡片只剩已绑定平台，店长分不清「没绑定」和「读失败」。
            platform_results.append(
                platform_read_error_payload(
                    platform=platform,
                    status="account_missing",
                    summary=f"当前群关联的门店没有绑定{_platform_display_name(platform)}EBK 账号。",
                    reason="ota_ebk_account_missing",
                )
            )
            continue
        platform_results.append(
            await _read_platform_room_rates(
                platform=platform,
                platform_account_id=str(account.get("platform_account_id") or ""),
                browser_session_id=str(account.get("browser_session_id") or ""),
                actor_id=_actor_id(context),
                checkin_date=checkin_date,
            )
        )

    cards = build_pricing_query_cards(
        platform_results=platform_results,
        date_label=date_label,
        checkin_date=checkin_date,
        store_name=str(store_name) if store_name else None,
    )
    client = _feishu_client_factory(context.chat_id)
    first_push = await client.reply_interactive_card(
        context.message_id,
        cards[0],
        fallback_text=build_pricing_query_fallback_text(cards),
    )
    extra_pushes: list[dict[str, Any]] = []
    for card in cards[1:]:
        extra_pushes.append(
            await client.send_interactive_card(
                str(context.chat_id or ""),
                card,
                fallback_text=build_pricing_query_fallback_text([card]),
            )
        )
    return {
        "status": "pricing_query_card_replied",
        "summary": f"已回复房价查询卡片（{len(cards)} 张）。",
        "feishu_push": _public_push(first_push),
        "evidence": {
            "pricing_query": True,
            "date_label": date_label,
            "checkin_date": checkin_date,
            "requested_platform": requested_platform,
            "platforms_read": [
                {
                    "platform": item.get("platform"),
                    "status": item.get("status"),
                    "room_rate_count": item.get("room_rate_count"),
                }
                for item in platform_results
            ],
            "card_count": len(cards),
            "extra_cards_sent": len(extra_pushes),
            "execute_triggered": False,
        },
        "next_action": "核对房型名与现价后，用「把今天<房型名>调到<价格>」发起调价审批。",
    }


async def _read_platform_room_rates(
    *,
    platform: str,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    checkin_date: str,
) -> dict[str, Any]:
    """按平台分发只读房价名单读取；异常转成失败 payload，不中断其它平台。"""

    import asyncio

    from app.domain.browser.pricing_room_rates import platform_read_error_payload

    try:
        if platform == "ctrip":
            from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service

            return await asyncio.wait_for(
                get_ctrip_ebooking_service().read_pricing_calendar_room_rates(
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    actor_id=actor_id,
                    checkin_date=checkin_date,
                ),
                timeout=90,
            )
        if platform == "meituan":
            from app.domain.browser.meituan_ebooking import get_meituan_ebooking_service

            return await asyncio.wait_for(
                get_meituan_ebooking_service().read_pricing_product_room_rates(
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    actor_id=actor_id,
                    checkin_date=checkin_date,
                ),
                timeout=90,
            )
        if platform == "fliggy":
            from app.domain.browser.fliggy_ebooking import get_fliggy_ebooking_service

            return await asyncio.wait_for(
                get_fliggy_ebooking_service().read_pricing_product_room_rates(
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    actor_id=actor_id,
                    checkin_date=checkin_date,
                ),
                timeout=90,
            )
        return platform_read_error_payload(
            platform=platform,
            status="skipped",
            summary=f"暂不支持查询{_platform_display_name(platform)}房价。",
            reason="platform_not_supported",
        )
    except TimeoutError:
        return platform_read_error_payload(
            platform=platform,
            status="failed",
            summary=f"{_platform_display_name(platform)}房价读取超时（90 秒）。",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            reason="room_rate_read_timeout",
        )
    except Exception as exc:
        logger.error(
            "pricing query read failed for platform %s (%s)",
            platform,
            type(exc).__name__,
        )
        return platform_read_error_payload(
            platform=platform,
            status="failed",
            summary=f"{_platform_display_name(platform)}房价读取失败（{type(exc).__name__}）。",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            reason="room_rate_read_failed",
        )


def _platform_display_name(platform: str) -> str:
    return {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}.get(
        str(platform or "").strip().lower(), str(platform or "未知")
    )


async def handle_pricing_approval_card_action(payload: dict[str, Any]) -> dict[str, Any]:
    preview_id = _safe_int(
        payload.get("preview_id")
        or payload.get("pricing_preview_record_id")
        or payload.get("pricing_preview_audit_record_id")
    )
    decision = str(payload.get("decision") or "")
    if preview_id is None or decision not in {"approve", "reject", "human_takeover"}:
        return {
            "status": "ignored",
            "summary": "不是有效的调价审批卡片动作。",
            "evidence": {"execute_triggered": False},
        }
    result = await record_pricing_approval_decision(
        preview_id=preview_id,
        decision=decision,  # type: ignore[arg-type]
        actor_id=str(payload.get("open_id") or "feishu-card"),
        source="feishu_card",
        open_id=_optional_str(payload.get("open_id")),
        union_id=_optional_str(payload.get("union_id")),
        message_id=_optional_str(payload.get("message_id")),
        chat_id=_optional_str(payload.get("chat_id")),
    )
    execution_pipeline_queued = False
    if decision == "approve" and result.status == "approved" and result.approval_record_id is not None:
        from app.domain.notifications import pricing_execution_pipeline
        from app.domain.pricing_approvals.service import mark_approval_execute_queued

        chat_id = _optional_str(payload.get("chat_id"))
        if chat_id:
            execution_pipeline_queued = pricing_execution_pipeline.queue_pricing_execution_pipeline(
                approval_record_id=result.approval_record_id,
                chat_id=chat_id,
            )
        else:
            execution_pipeline_queued = pricing_execution_pipeline.queue_pricing_execution_pipeline(
                approval_record_id=result.approval_record_id,
            )
        if execution_pipeline_queued:
            await mark_approval_execute_queued(result.approval_record_id)

    approval_payload = result.model_dump(mode="json")
    approval_payload["evidence"] = {
        **(approval_payload.get("evidence") or {}),
        "execution_pipeline_queued": execution_pipeline_queued,
    }
    decision_push = await _reply_pricing_decision_card(payload, approval_payload)
    return {
        "status": result.status,
        "summary": result.summary,
        "pricing_approval": approval_payload,
        "feishu_push": _public_push(decision_push),
        "evidence": {
            "execute_triggered": execution_pipeline_queued,
            "execution_pipeline_queued": execution_pipeline_queued,
            "approval_source": "feishu_card",
            "preview_id": preview_id,
            "decision": decision,
            "chat_context_present": bool(payload.get("chat_id")),
            "decision_card_sent": decision_push.get("status") == "success",
        },
        "next_action": result.next_action,
    }


async def _reply_pricing_decision_card(
    payload: dict[str, Any],
    approval_payload: dict[str, Any],
) -> dict[str, Any]:
    """审批卡每个决策（含拒绝/人工接管/重复点击）都向群里回一张结果卡。"""

    chat_id = _optional_str(payload.get("chat_id"))
    if not chat_id:
        return {
            "status": "skipped",
            "summary": "缺少飞书群聊上下文，审批结果卡片未发送。",
            "evidence": {"feishu_chat_id_present": False},
        }
    from app.domain.notifications.pricing_execution_card import (
        build_pricing_approval_decision_card,
        build_pricing_approval_decision_fallback_text,
    )

    card = build_pricing_approval_decision_card(approval_payload)
    client = _feishu_client_factory(chat_id)
    try:
        return await client.send_interactive_card(
            chat_id,
            card,
            fallback_text=build_pricing_approval_decision_fallback_text(card),
        )
    except Exception as exc:
        logger.error(
            "failed to send pricing approval decision card (%s)",
            type(exc).__name__,
        )
        return {
            "status": "failed",
            "summary": "调价审批结果卡片发送失败。",
            "evidence": {"error_type": type(exc).__name__},
        }


async def handle_pricing_reference_defer_action(payload: dict[str, Any]) -> dict[str, Any]:
    preview_id = _safe_int(payload.get("preview_id") or payload.get("pricing_preview_record_id"))
    return {
        "status": "pricing_reference_deferred",
        "summary": "Operator chose not to change price from this reference card.",
        "card_callback_response": _feishu_card_callback_response(
            toast_type="info",
            toast_content="No price change will be made for this reference card.",
        ),
        "evidence": {
            "preview_id": preview_id,
            "open_id": payload.get("open_id"),
            "chat_id": payload.get("chat_id"),
            "execute_triggered": False,
        },
    }


async def handle_pricing_custom_price_action(payload: dict[str, Any]) -> dict[str, Any]:
    preview_id = _safe_int(payload.get("preview_id") or payload.get("pricing_preview_record_id"))
    if preview_id is None or preview_id <= 0:
        return {
            "status": "ignored",
            "summary": "不是有效的自定义调价动作。",
            "evidence": {"execute_triggered": False},
        }
    preview = await get_pricing_preview(preview_id)
    if preview is None:
        return {
            "status": "not_found",
            "summary": "原调价预览不存在，请重新发送调价指令。",
            "card_callback_response": _feishu_card_callback_response(
                toast_type="error",
                toast_content="原审批卡不存在，请重新发送调价指令。",
            ),
            "evidence": {"preview_id": preview_id, "execute_triggered": False},
        }
    if not account_policy_allows_write(_preview_account_policy(preview)):
        return _custom_price_write_not_allowed_response(
            preview_id, platform=pricing_platform_from_preview_payload(preview)
        )
    card = build_custom_price_form_card(preview)
    return {
        "status": "custom_price_form_ready",
        "summary": "已打开自定义调价表单。",
        "card_callback_response": _feishu_card_callback_response(
            card=card,
            toast_type="info",
            toast_content="请输入自定义价格，提交后仍需审批通过才会写入 OTA。",
        ),
        "evidence": {
            "preview_id": preview_id,
            "open_id": payload.get("open_id"),
            "chat_id": payload.get("chat_id"),
            "execute_triggered": False,
            "requires_followup_approval": True,
            "card_updated_in_place": True,
        },
    }


async def handle_pricing_custom_price_submit(payload: dict[str, Any]) -> dict[str, Any]:
    preview_id = _safe_int(payload.get("preview_id") or payload.get("pricing_preview_record_id"))
    target_price = _parse_target_price(payload.get("target_price"))
    if preview_id is None:
        return {
            "status": "ignored",
            "summary": "不是有效的自定义调价提交。",
            "evidence": {"execute_triggered": False},
        }
    if target_price is None:
        return {
            "status": "data_required",
            "summary": "自定义调价需要有效目标售价。",
            "card_callback_response": _feishu_card_callback_response(
                toast_type="error",
                toast_content="未识别到有效目标售价，请输入 2-5 位数字，例如 420。",
            ),
            "evidence": {"preview_id": preview_id, "execute_triggered": False},
            "next_action": "请输入有效的目标售价。",
        }
    original_preview = await get_pricing_preview(preview_id)
    if original_preview is None:
        return {
            "status": "not_found",
            "summary": "原调价预览不存在，请重新发送调价指令。",
            "card_callback_response": _feishu_card_callback_response(
                toast_type="error",
                toast_content="原审批卡不存在，请重新发送调价指令。",
            ),
            "evidence": {"preview_id": preview_id, "execute_triggered": False},
        }
    if not account_policy_allows_write(_preview_account_policy(original_preview)):
        return _custom_price_write_not_allowed_response(
            preview_id, platform=pricing_platform_from_preview_payload(original_preview)
        )
    action = (_preview_actions(original_preview) or [{}])[0]
    room_type = str(action.get("room_type") or "").strip()
    date_label = str(action.get("date_label") or "").strip()
    action_checkin_date = str(action.get("checkin_date") or "").strip() or None
    original_platform = str(action.get("platform") or "").strip().lower() or None
    if not room_type or (date_label not in {"today", "tomorrow", "specified"} and not action_checkin_date):
        return {
            "status": "data_required",
            "summary": "原审批卡缺少房型或日期，请重新发送调价指令。",
            "card_callback_response": _feishu_card_callback_response(
                card=build_custom_price_form_card(original_preview),
                toast_type="error",
                toast_content="原审批卡缺少可执行的房型或日期信息，请重新发送调价指令。",
            ),
            "evidence": {
                "preview_id": preview_id,
                "room_type_present": bool(room_type),
                "date_label": date_label,
                "execute_triggered": False,
            },
        }
    pricing_browser_session_id = _preview_pricing_browser_session_id(original_preview)
    preview = await create_manual_pricing_preview(
        watch_group_key=str(original_preview.get("watch_group_key") or DEFAULT_WATCH_GROUP_KEY),
        room_type=room_type,
        date_label=date_label,
        target_price=target_price,
        checkin_date=action_checkin_date,
        pricing_browser_session_id=pricing_browser_session_id,
        actor_id=str(payload.get("open_id") or "feishu-card"),
        reason=f"Feishu 自定义调价，来源预览 {preview_id}",
        platform=original_platform,
    )
    preview_payload = preview.model_dump(mode="json")
    card = build_operator_target_price_confirmation_card(preview_payload)
    return {
        "status": "custom_price_confirmation_ready",
        "summary": "Target price confirmation card is ready; preflight and dry-run require confirmation.",
        "pricing_preview": preview_payload,
        "card_callback_response": _feishu_card_callback_response(
            card=card,
            toast_type="info",
            toast_content="Target price updated. Confirm to start preflight and dry-run.",
        ),
        "evidence": {
            "source_preview_id": preview_id,
            "new_preview_id": preview.preview_id,
            "custom_target_price": target_price,
            "execute_triggered": False,
            "requires_manual_confirmation": True,
            "card_updated_in_place": True,
        },
    }


async def handle_pricing_custom_price_cancel(payload: dict[str, Any]) -> dict[str, Any]:
    preview_id = _safe_int(payload.get("preview_id") or payload.get("pricing_preview_record_id"))
    if preview_id is None:
        return {
            "status": "ignored",
            "summary": "不是有效的自定义调价取消。",
            "evidence": {"execute_triggered": False},
        }
    preview = await get_pricing_preview(preview_id)
    if preview is None:
        return {
            "status": "not_found",
            "summary": "原调价预览不存在，请重新发送调价指令。",
            "card_callback_response": _feishu_card_callback_response(
                toast_type="error",
                toast_content="原审批卡不存在，请重新发送调价指令。",
            ),
            "evidence": {"preview_id": preview_id, "execute_triggered": False},
        }
    return {
        "status": "custom_price_cancelled",
        "summary": "已取消自定义调价，恢复原审批卡。",
        "card_callback_response": _feishu_card_callback_response(
            card=build_pricing_approval_card(preview),
            toast_type="info",
            toast_content="已取消自定义调价。",
        ),
        "evidence": {
            "preview_id": preview_id,
            "execute_triggered": False,
            "card_updated_in_place": True,
        },
    }


async def handle_pyramid_bid_command(
    context: FeishuCommandContext,
    *,
    classification: dict[str, Any],
) -> dict[str, Any]:
    """金字塔出价指令入口：查询回列表卡，调价生成审批卡。"""
    status = str(classification.get("status") or "")
    if status == "list_query":
        return await handle_pyramid_bid_list_query(context)
    if status == "needs_confirmation":
        return await handle_pyramid_bid_confirmation(context, classification=classification)
    if status != "ready":
        client = _feishu_client_factory(context.chat_id)
        message = str(classification.get("summary") or "金字塔调价指令无法识别，请补全后重试。")
        push = await client.reply_text(context.message_id, message)
        return {
            "status": status or "not_pyramid",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "pyramid_bid_adjustment": True,
                "approval_card_sent": False,
            },
            "next_action": "请按示例重发指令。",
        }

    try:
        pricing_account = get_pricing_browser_session_for_feishu_chat_id(context.chat_id)
    except ValueError:
        client = _feishu_client_factory(context.chat_id)
        message = (
            "当前飞书群绑定了多个 EBK 调价账号，系统无法判断要调整哪个账号的金字塔出价。\n"
            "请在“账号与浏览器”里只保留一个 EBK 账号绑定当前群 chat_id 后重试。"
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "config_required",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "pyramid_bid_adjustment": True,
                "pricing_account_configured": False,
            },
            "next_action": "修正 EBK 账号上的调价审批群绑定。",
        }

    preview = await create_pyramid_bid_preview(
        chat_id=context.chat_id,
        campaign_name=str(classification.get("campaign_name") or ""),
        target_bid=float(classification.get("target_bid") or 0),
        pricing_account=pricing_account,
        actor_id=_actor_id(context),
        reason=context.command,
    )
    preview_payload = preview.model_dump(mode="json")
    client = _feishu_client_factory(context.chat_id)
    if preview.status != "preview_ready":
        message = "\n".join(
            item for item in [preview.summary, preview.next_action] if item
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": preview.status,
            "summary": preview.summary,
            "pyramid_bid_preview": preview_payload,
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "pyramid_bid_adjustment": True,
                "approval_card_sent": False,
                "can_auto_write_price": preview.can_auto_write_price,
                "connection_mode": preview.connection_mode,
            },
            "next_action": preview.next_action,
        }
    card = build_pyramid_bid_approval_card(preview_payload)
    push = await client.reply_interactive_card(
        context.message_id,
        card,
        fallback_text=build_pyramid_bid_approval_fallback_text(preview_payload),
    )
    return {
        "status": "approval_card_replied",
        "summary": "已回复金字塔调价审批卡片。",
        "pyramid_bid_preview": preview_payload,
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "approval_source_required": "feishu_card",
            "pyramid_bid_adjustment": True,
            "can_auto_write_price": preview.can_auto_write_price,
            "connection_mode": preview.connection_mode,
            "execute_button_present": preview.can_auto_write_price,
            "push_to_feishu": False,
            "command": "manual_pyramid_bid_adjustment",
        },
    }


async def handle_pyramid_bid_confirmation(
    context: FeishuCommandContext,
    *,
    classification: dict[str, Any],
) -> dict[str, Any]:
    client = _feishu_client_factory(context.chat_id)
    card = build_pyramid_bid_confirmation_card(classification)
    push = await client.reply_interactive_card(context.message_id, card, fallback_text=None)
    return {
        "status": "needs_confirmation",
        "summary": str(classification.get("summary") or "金字塔调价信息不完整，请补全后重试。"),
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "pyramid_bid_adjustment": True,
            "approval_card_sent": False,
            "confirmation_card_sent": True,
            "missing_fields": classification.get("missing_fields") or [],
            "partial": classification.get("partial") or {},
        },
        "next_action": "请按卡片示例补全推广名称和目标出价后重新发送。",
    }


async def handle_pyramid_bid_list_query(context: FeishuCommandContext) -> dict[str, Any]:
    try:
        pricing_account = get_pricing_browser_session_for_feishu_chat_id(context.chat_id)
    except ValueError:
        client = _feishu_client_factory(context.chat_id)
        message = (
            "当前飞书群绑定了多个 EBK 调价账号，系统无法判断要读取哪个账号的金字塔出价。\n"
            "请在“账号与浏览器”里只保留一个 EBK 账号绑定当前群 chat_id 后重试。"
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "config_required",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {"execute_triggered": False, "pyramid_bid_adjustment": True},
        }
    client = _feishu_client_factory(context.chat_id)
    if pricing_account is None:
        message = (
            "当前飞书群还没有绑定用于调价的携程 EBK 浏览器账号。\n"
            "请先在“账号与浏览器”里给携程 EBK 账号绑定当前群 chat_id。"
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "config_required",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {"execute_triggered": False, "pricing_account_configured": False},
        }
    if str(getattr(pricing_account, "platform", "") or "").strip().lower() != PYRAMID_PLATFORM:
        message = "金字塔调价仅支持携程 EBK 账号，当前群绑定的调价账号不是携程账号。"
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "write_not_allowed",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {"execute_triggered": False},
        }
    read_result = await read_pyramid_promotions_for_account(
        pricing_account,
        actor_id=_actor_id(context),
    )
    card = build_pyramid_bid_list_card(
        read_result,
        read_status=str(read_result.get("status") or "failed"),
    )
    push = await client.reply_interactive_card(
        context.message_id,
        card,
        fallback_text=build_pyramid_bid_list_fallback_text(read_result),
    )
    return {
        "status": "pyramid_bid_list_replied",
        "summary": str(read_result.get("summary") or "金字塔出价列表已回复。"),
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "read_only": True,
            "promotion_count": len(read_result.get("promotions") or []),
        },
    }


async def handle_promotion_adjust_command(
    context: FeishuCommandContext,
    *,
    classification: dict[str, Any],
) -> dict[str, Any]:
    """促销调整指令入口：查询回列表卡，调整生成审批卡。"""
    status = str(classification.get("status") or "")
    if status == "list_query":
        return await handle_promotion_adjust_list_query(context)
    if status == "needs_confirmation":
        client = _feishu_client_factory(context.chat_id)
        card = build_promotion_adjust_confirmation_card(classification)
        push = await client.reply_interactive_card(context.message_id, card, fallback_text=None)
        return {
            "status": "needs_confirmation",
            "summary": str(classification.get("summary") or "促销调整信息不完整，请补全后重试。"),
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "promotion_adjustment": True,
                "approval_card_sent": False,
                "confirmation_card_sent": True,
                "missing_fields": classification.get("missing_fields") or [],
                "partial": classification.get("partial") or {},
            },
            "next_action": "请按卡片示例补全促销名称和目标立减后重新发送。",
        }
    if status != "ready":
        client = _feishu_client_factory(context.chat_id)
        message = str(classification.get("summary") or "促销调整指令无法识别，请补全后重试。")
        push = await client.reply_text(context.message_id, message)
        return {
            "status": status or "not_promotion",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "promotion_adjustment": True,
                "approval_card_sent": False,
            },
            "next_action": "请按示例重发指令。",
        }

    try:
        pricing_account = get_pricing_browser_session_for_feishu_chat_id(context.chat_id)
    except ValueError:
        client = _feishu_client_factory(context.chat_id)
        message = (
            "当前飞书群绑定了多个 EBK 调价账号，系统无法判断要调整哪个账号的促销。\n"
            "请在“账号与浏览器”里只保留一个 EBK 账号绑定当前群 chat_id 后重试。"
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "config_required",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "promotion_adjustment": True,
                "pricing_account_configured": False,
            },
            "next_action": "修正 EBK 账号上的调价审批群绑定。",
        }

    preview = await create_promotion_adjust_preview(
        chat_id=context.chat_id,
        promotion_name=str(classification.get("promotion_name") or ""),
        target_discount_percent=float(classification.get("target_discount_percent") or 0),
        pricing_account=pricing_account,
        actor_id=_actor_id(context),
        reason=context.command,
    )
    preview_payload = preview.model_dump(mode="json")
    client = _feishu_client_factory(context.chat_id)
    if preview.status != "preview_ready":
        message = "\n".join(
            item for item in [preview.summary, preview.next_action] if item
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": preview.status,
            "summary": preview.summary,
            "promotion_adjust_preview": preview_payload,
            "feishu_push": _public_push(push),
            "evidence": {
                "execute_triggered": False,
                "promotion_adjustment": True,
                "approval_card_sent": False,
                "can_auto_write_price": preview.can_auto_write_price,
                "connection_mode": preview.connection_mode,
            },
            "next_action": preview.next_action,
        }
    card = build_promotion_adjust_approval_card(preview_payload)
    push = await client.reply_interactive_card(
        context.message_id,
        card,
        fallback_text=build_promotion_adjust_approval_fallback_text(preview_payload),
    )
    return {
        "status": "approval_card_replied",
        "summary": "已回复促销调整审批卡片。",
        "promotion_adjust_preview": preview_payload,
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "approval_source_required": "feishu_card",
            "promotion_adjustment": True,
            "can_auto_write_price": preview.can_auto_write_price,
            "connection_mode": preview.connection_mode,
            "execute_button_present": preview.can_auto_write_price,
            "push_to_feishu": False,
            "command": "manual_promotion_adjustment",
        },
    }


async def handle_promotion_adjust_list_query(context: FeishuCommandContext) -> dict[str, Any]:
    try:
        pricing_account = get_pricing_browser_session_for_feishu_chat_id(context.chat_id)
    except ValueError:
        client = _feishu_client_factory(context.chat_id)
        message = (
            "当前飞书群绑定了多个 EBK 调价账号，系统无法判断要读取哪个账号的促销。\n"
            "请在“账号与浏览器”里只保留一个 EBK 账号绑定当前群 chat_id 后重试。"
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "config_required",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {"execute_triggered": False, "promotion_adjustment": True},
        }
    client = _feishu_client_factory(context.chat_id)
    if pricing_account is None:
        message = (
            "当前飞书群还没有绑定用于调价的携程 EBK 浏览器账号。\n"
            "请先在“账号与浏览器”里给携程 EBK 账号绑定当前群 chat_id。"
        )
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "config_required",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {"execute_triggered": False, "pricing_account_configured": False},
        }
    if str(getattr(pricing_account, "platform", "") or "").strip().lower() != PROMOTION_PLATFORM:
        message = "促销调整仅支持携程 EBK 账号，当前群绑定的调价账号不是携程账号。"
        push = await client.reply_text(context.message_id, message)
        return {
            "status": "write_not_allowed",
            "summary": message,
            "feishu_push": _public_push(push),
            "evidence": {"execute_triggered": False},
        }
    read_result = await read_promotion_enrollments_for_account(
        pricing_account,
        actor_id=_actor_id(context),
    )
    card = build_promotion_adjust_list_card(
        read_result,
        read_status=str(read_result.get("status") or "failed"),
    )
    push = await client.reply_interactive_card(
        context.message_id,
        card,
        fallback_text=build_promotion_adjust_list_fallback_text(read_result),
    )
    return {
        "status": "promotion_list_replied",
        "summary": str(read_result.get("summary") or "已参加促销列表已回复。"),
        "feishu_push": _public_push(push),
        "evidence": {
            "execute_triggered": False,
            "read_only": True,
            "promotion_count": len(read_result.get("promotions") or []),
        },
    }


async def handle_promotion_adjust_approval_card_action(payload: dict[str, Any]) -> dict[str, Any]:
    from app.domain.promotion_adjust.approvals import (
        mark_promotion_approval_execute_queued,
        record_promotion_adjust_approval_decision,
    )

    preview_id = _safe_int(
        payload.get("preview_id") or payload.get("promotion_adjust_preview_record_id")
    )
    decision = str(payload.get("decision") or "")
    if preview_id is None or decision not in {"approve", "reject", "human_takeover"}:
        return {
            "status": "ignored",
            "summary": "不是有效的促销调整审批卡片动作。",
            "evidence": {"execute_triggered": False},
        }
    result = await record_promotion_adjust_approval_decision(
        preview_id=preview_id,
        decision=decision,  # type: ignore[arg-type]
        actor_id=str(payload.get("open_id") or "feishu-card"),
        source="feishu_card",
        open_id=_optional_str(payload.get("open_id")),
        union_id=_optional_str(payload.get("union_id")),
        message_id=_optional_str(payload.get("message_id")),
    )
    execution_pipeline_queued = False
    if decision == "approve" and result.status == "approved" and result.approval_record_id is not None:
        execution_pipeline_queued = queue_promotion_adjust_execution_pipeline(
            approval_record_id=result.approval_record_id,
            chat_id=_optional_str(payload.get("chat_id")),
        )
        if execution_pipeline_queued:
            await mark_promotion_approval_execute_queued(result.approval_record_id)
    return {
        "status": result.status,
        "summary": result.summary,
        "promotion_adjust_approval": result.model_dump(mode="json"),
        "card_callback_response": _feishu_card_callback_response(
            toast_type=_promotion_approval_toast_type(result.status),
            toast_content=_promotion_approval_toast_content(result),
        ),
        "evidence": {
            "execute_triggered": execution_pipeline_queued,
            "execution_pipeline_queued": execution_pipeline_queued,
            "approval_source": "feishu_card",
            "preview_id": preview_id,
            "decision": decision,
            "chat_context_present": bool(payload.get("chat_id")),
        },
        "next_action": result.next_action,
    }


def _promotion_approval_toast_type(status: str) -> str:
    if status in {"approved", "rejected", "already_decided", "human_takeover"}:
        return "info"
    if status == "write_not_allowed":
        return "warning"
    return "error"


def _promotion_approval_toast_content(result: Any) -> str:
    status = str(getattr(result, "status", "") or "")
    if status == "rejected":
        return "已记录：本次促销暂不调整，立减保持不变。"
    if status == "approved":
        return "已确认调整促销，正在进入预检与页面核验。"
    if status == "already_decided":
        return "这张促销调整审批卡已处理过，请勿重复操作。"
    if status == "write_not_allowed":
        return "当前账号为直连或未确认账号，仅允许生成促销调整建议，不允许自动调整促销。"
    if status == "not_found":
        return "促销调整预览不存在，请重新发送促销调整指令。"
    if status == "not_ready":
        return "该促销预览不在待审批状态，请重新发送促销调整指令。"
    return str(getattr(result, "summary", None) or "促销调整审批已记录。")


def parse_promotion_adjust_approval_card_action(payload: dict[str, Any]) -> dict[str, Any] | None:
    if _event_type(payload) not in {"card.action.trigger", "card.action.trigger_v1"}:
        return None
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value = action.get("value") if isinstance(action.get("value"), dict) else {}
    if value.get("workflow") != "promotion_adjust_approval":
        return None
    if value.get("action"):
        return None
    decision = value.get("decision")
    if decision not in {"approve", "reject", "human_takeover"}:
        return None
    preview_id = (
        value.get("preview_id") or value.get("promotion_adjust_preview_record_id")
    )
    if _safe_int(preview_id) is None:
        return None
    return {
        "event_id": _header_event_id(payload),
        "preview_id": preview_id,
        "decision": value.get("decision"),
        "open_id": _operator_open_id(event),
        "union_id": _operator_union_id(event),
        "chat_id": _card_chat_id(event),
        "message_id": event.get("message_id"),
    }


async def handle_pyramid_bid_approval_card_action(payload: dict[str, Any]) -> dict[str, Any]:
    from app.domain.pyramid_bidding.approvals import (
        mark_pyramid_approval_execute_queued,
        record_pyramid_bid_approval_decision,
    )

    preview_id = _safe_int(
        payload.get("preview_id") or payload.get("pyramid_bid_preview_record_id")
    )
    decision = str(payload.get("decision") or "")
    if preview_id is None or decision not in {"approve", "reject", "human_takeover"}:
        return {
            "status": "ignored",
            "summary": "不是有效的金字塔调价审批卡片动作。",
            "evidence": {"execute_triggered": False},
        }
    result = await record_pyramid_bid_approval_decision(
        preview_id=preview_id,
        decision=decision,  # type: ignore[arg-type]
        actor_id=str(payload.get("open_id") or "feishu-card"),
        source="feishu_card",
        open_id=_optional_str(payload.get("open_id")),
        union_id=_optional_str(payload.get("union_id")),
        message_id=_optional_str(payload.get("message_id")),
    )
    execution_pipeline_queued = False
    if decision == "approve" and result.status == "approved" and result.approval_record_id is not None:
        execution_pipeline_queued = queue_pyramid_bid_execution_pipeline(
            approval_record_id=result.approval_record_id,
            chat_id=_optional_str(payload.get("chat_id")),
        )
        if execution_pipeline_queued:
            await mark_pyramid_approval_execute_queued(result.approval_record_id)
    return {
        "status": result.status,
        "summary": result.summary,
        "pyramid_bid_approval": result.model_dump(mode="json"),
        "card_callback_response": _feishu_card_callback_response(
            toast_type=_pyramid_approval_toast_type(result.status),
            toast_content=_pyramid_approval_toast_content(result),
        ),
        "evidence": {
            "execute_triggered": execution_pipeline_queued,
            "execution_pipeline_queued": execution_pipeline_queued,
            "approval_source": "feishu_card",
            "preview_id": preview_id,
            "decision": decision,
            "chat_context_present": bool(payload.get("chat_id")),
        },
        "next_action": result.next_action,
    }


def _pyramid_approval_toast_type(status: str) -> str:
    if status in {"approved", "rejected", "already_decided", "human_takeover"}:
        return "info"
    if status == "write_not_allowed":
        return "warning"
    return "error"


def _pyramid_approval_toast_content(result: Any) -> str:
    status = str(getattr(result, "status", "") or "")
    if status == "rejected":
        return "已记录：本次金字塔调价暂不调整，出价保持不变。"
    if status == "approved":
        return "已确认调整出价，正在进入预检与页面核验。"
    if status == "already_decided":
        return "这张金字塔调价审批卡已处理过，请勿重复操作。"
    if status == "write_not_allowed":
        return "当前账号为直连或未确认账号，仅允许生成调价建议，不允许自动调整出价。"
    if status == "not_found":
        return "金字塔调价预览不存在，请重新发送调价指令。"
    if status == "not_ready":
        return "该金字塔预览不在待审批状态，请重新发送调价指令。"
    return str(getattr(result, "summary", None) or "金字塔调价审批已记录。")


def parse_pyramid_bid_approval_card_action(payload: dict[str, Any]) -> dict[str, Any] | None:
    if _event_type(payload) not in {"card.action.trigger", "card.action.trigger_v1"}:
        return None
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value = action.get("value") if isinstance(action.get("value"), dict) else {}
    if value.get("workflow") != "pyramid_bid_approval":
        return None
    if value.get("action"):
        return None
    decision = value.get("decision")
    if decision not in {"approve", "reject", "human_takeover"}:
        return None
    preview_id = (
        value.get("preview_id") or value.get("pyramid_bid_preview_record_id")
    )
    if _safe_int(preview_id) is None:
        return None
    return {
        "event_id": _header_event_id(payload),
        "preview_id": preview_id,
        "decision": value.get("decision"),
        "open_id": _operator_open_id(event),
        "union_id": _operator_union_id(event),
        "chat_id": _card_chat_id(event),
        "message_id": event.get("message_id"),
    }


def parse_pricing_approval_card_action(payload: dict[str, Any]) -> dict[str, Any] | None:
    if _event_type(payload) not in {"card.action.trigger", "card.action.trigger_v1"}:
        return None
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value = action.get("value") if isinstance(action.get("value"), dict) else {}
    if value.get("workflow") != "pricing_approval":
        return None
    if value.get("action"):
        return None
    decision = value.get("decision")
    if decision not in {"approve", "reject", "human_takeover"}:
        return None
    preview_id = (
        value.get("preview_id")
        or value.get("pricing_preview_record_id")
        or value.get("pricing_preview_audit_record_id")
    )
    return {
        "event_id": _header_event_id(payload),
        "preview_id": preview_id,
        "pricing_preview_record_id": preview_id,
        "decision": value.get("decision"),
        "open_id": _operator_open_id(event),
        "union_id": _operator_union_id(event),
        "chat_id": _card_chat_id(event),
        "message_id": event.get("message_id"),
    }


def parse_pricing_reference_defer_action(payload: dict[str, Any]) -> dict[str, Any] | None:
    event, _action, value = _pricing_card_action_parts(payload)
    if not event or value.get("workflow") != "pricing_reference":
        return None
    if str(value.get("action") or "") != "defer":
        return None
    return {
        "event_id": _header_event_id(payload),
        "preview_id": _safe_int(value.get("preview_id") or value.get("pricing_preview_record_id")),
        "pricing_preview_record_id": _safe_int(value.get("pricing_preview_record_id") or value.get("preview_id")),
        "open_id": _operator_open_id(event),
        "chat_id": _card_chat_id(event),
    }


def parse_pricing_custom_price_action(payload: dict[str, Any]) -> dict[str, Any] | None:
    event, _action, value = _pricing_card_action_parts(payload)
    if not event or value.get("workflow") != "pricing_approval":
        return None
    if str(value.get("action") or "") != "custom_price":
        return None
    preview_id = (
        value.get("preview_id")
        or value.get("pricing_preview_record_id")
        or value.get("pricing_preview_audit_record_id")
    )
    if _safe_int(preview_id) is None:
        return None
    return {
        "event_id": _header_event_id(payload),
        "preview_id": preview_id,
        "pricing_preview_record_id": preview_id,
        "open_id": _operator_open_id(event),
        "union_id": _operator_union_id(event),
        "chat_id": _card_chat_id(event),
        "message_id": event.get("message_id"),
    }


def parse_pricing_custom_price_submit(payload: dict[str, Any]) -> dict[str, Any] | None:
    event, action, value = _pricing_card_action_parts(payload)
    if not event or value.get("workflow") != "pricing_custom_price":
        return None
    if str(value.get("action") or "") != "submit":
        return None
    preview_id = (
        value.get("preview_id")
        or value.get("pricing_preview_record_id")
        or value.get("pricing_preview_audit_record_id")
    )
    if _safe_int(preview_id) is None:
        return None
    form_value = action.get("form_value") if isinstance(action.get("form_value"), dict) else {}
    raw_target_price = _first_present_value(
        form_value.get("target_price"),
        form_value.get("TargetPrice"),
        form_value.get("price"),
        action.get("input_value"),
        value.get("target_price"),
    )
    return {
        "event_id": _header_event_id(payload),
        "preview_id": preview_id,
        "pricing_preview_record_id": preview_id,
        "target_price": raw_target_price,
        "raw_target_price": str(raw_target_price).strip() if raw_target_price is not None else None,
        "open_id": _operator_open_id(event),
        "union_id": _operator_union_id(event),
        "chat_id": _card_chat_id(event),
        "message_id": event.get("message_id"),
        "update_token": _optional_str(event.get("token")),
    }


def parse_pricing_custom_price_cancel(payload: dict[str, Any]) -> dict[str, Any] | None:
    event, _action, value = _pricing_card_action_parts(payload)
    if not event or value.get("workflow") != "pricing_custom_price":
        return None
    if str(value.get("action") or "") != "cancel":
        return None
    preview_id = (
        value.get("preview_id")
        or value.get("pricing_preview_record_id")
        or value.get("pricing_preview_audit_record_id")
    )
    if _safe_int(preview_id) is None:
        return None
    return {
        "event_id": _header_event_id(payload),
        "preview_id": preview_id,
        "pricing_preview_record_id": preview_id,
        "open_id": _operator_open_id(event),
        "union_id": _operator_union_id(event),
        "chat_id": _card_chat_id(event),
        "message_id": event.get("message_id"),
    }


def parse_manual_price_adjustment_command(command: str) -> dict[str, Any] | None:
    from app.domain.notifications.manual_price_command import (
        parse_manual_price_adjustment_command as _parse_manual_price_adjustment_command,
    )

    return _parse_manual_price_adjustment_command(command)


def parse_command_context(payload: dict[str, Any]) -> FeishuCommandContext | None:
    if _event_type(payload) != "im.message.receive_v1":
        return None
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    message_id = _optional_str(message.get("message_id"))
    chat_id = _optional_str(message.get("chat_id"))
    if not message_id or not chat_id:
        return None
    raw_text = _extract_message_text(message)
    command = _strip_mentions(raw_text)
    if not command:
        return None
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    return FeishuCommandContext(
        event_id=_header_event_id(payload) or message_id,
        message_id=message_id,
        chat_id=chat_id,
        open_id=_optional_str(sender_id.get("open_id") or sender.get("open_id")),
        command=command,
        store_hint=_optional_str(message.get("store_hint")),
        mentioned=_message_mentions_bot(raw_text),
    )


def _handle_url_verification(payload: dict[str, Any]) -> dict[str, str] | None:
    if payload.get("type") == "url_verification" and payload.get("challenge"):
        return {"challenge": str(payload["challenge"])}
    if (
        payload.get("schema") == "2.0"
        and payload.get("header", {}).get("type") == "url_verification"
        and payload.get("challenge")
    ):
        return {"challenge": str(payload["challenge"])}
    return None


_PRICING_SUGGESTION_COMMANDS = frozenset({"调价建议", "价格建议"})
_PRICING_APPROVAL_COMMANDS = frozenset({"调价", "我要调价"})


def _pricing_command_kind(command: str) -> str | None:
    normalized = re.sub(r"\s+", "", command or "")
    if not normalized:
        return None
    if normalized in _PRICING_SUGGESTION_COMMANDS:
        return "suggestion"
    if normalized in _PRICING_APPROVAL_COMMANDS:
        return "approval"
    return None


def _is_pricing_command(command: str) -> bool:
    return _pricing_command_kind(command) is not None


def _pricing_card_action_parts(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if _event_type(payload) not in {"card.action.trigger", "card.action.trigger_v1"}:
        return {}, {}, {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value = action.get("value") if isinstance(action.get("value"), dict) else {}
    return event, action, value


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except ValueError:
            return content
        text = decoded.get("text") if isinstance(decoded, dict) else None
        return text if isinstance(text, str) else content
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _strip_mentions(text: str) -> str:
    text = re.sub(r"<at[^>]*>.*?</at>", " ", text)
    text = re.sub(r"@\S+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _message_mentions_bot(text: str) -> bool:
    return bool(re.search(r"<at\b[^>]*>.*?</at>", text) or re.search(r"@\S+", text))


def _ai_agent_reply_text(agent_result: Any) -> str:
    lines = [
        str(agent_result.final_output or agent_result.summary or "暂时没有可用回复。").strip(),
    ]
    next_action = _optional_str(getattr(agent_result, "next_action", None))
    if next_action:
        lines.append(next_action)
    return "\n".join(line for line in lines if line)


def _public_push(push: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": push.get("status"),
        "summary": push.get("summary"),
        "data": push.get("data") if isinstance(push.get("data"), dict) else {},
        "next_action": push.get("next_action"),
    }


def _feishu_card_callback_response(
    *,
    card: dict[str, Any] | None = None,
    toast_type: str = "info",
    toast_content: str,
) -> dict[str, Any]:
    response: dict[str, Any] = {"toast": {"type": toast_type, "content": toast_content}}
    if card is not None:
        response["card"] = {"type": "raw", "data": card}
    return response


def _preview_actions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    candidate = preview.get("preview_candidate") if isinstance(preview.get("preview_candidate"), dict) else {}
    actions = candidate.get("actions") if isinstance(candidate, dict) else []
    if not isinstance(actions, list):
        return []
    return [item for item in actions if isinstance(item, dict)]


def _preview_pricing_browser_session_id(preview: dict[str, Any]) -> str | None:
    candidate = preview.get("preview_candidate") if isinstance(preview.get("preview_candidate"), dict) else {}
    pricing_account = (
        candidate.get("pricing_account")
        if isinstance(candidate.get("pricing_account"), dict)
        else {}
    )
    return _optional_str(pricing_account.get("browser_session_id"))


def _first_present_value(*values: Any) -> Any:
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


def _parse_target_price(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    match = re.search(r"\d{2,5}", str(value))
    if match is None:
        return None
    price = int(match.group(0))
    return price if price > 0 else None


def _event_type(payload: dict[str, Any]) -> str | None:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    return _optional_str(header.get("event_type") or header.get("type") or event.get("type"))


def _header_event_id(payload: dict[str, Any]) -> str:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    return str(header.get("event_id") or "")


def _operator_open_id(event: dict[str, Any]) -> str | None:
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    operator_id = operator.get("operator_id") if isinstance(operator.get("operator_id"), dict) else {}
    return _optional_str(operator_id.get("open_id") or operator.get("open_id") or event.get("open_id"))


def _operator_union_id(event: dict[str, Any]) -> str | None:
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    operator_id = operator.get("operator_id") if isinstance(operator.get("operator_id"), dict) else {}
    return _optional_str(
        operator_id.get("union_id") or operator.get("union_id") or event.get("union_id")
    )


def _card_chat_id(event: dict[str, Any]) -> str | None:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    return _optional_str(
        context.get("open_chat_id")
        or context.get("chat_id")
        or event.get("open_chat_id")
        or event.get("chat_id")
    )


def _actor_id(context: FeishuCommandContext) -> str:
    return context.open_id or "feishu-command"


async def _bind_competitor_watch_chat(*, watch_group_key: str, chat_id: str | None) -> dict[str, Any]:
    chat_id = _optional_str(chat_id)
    if not chat_id:
        return {
            "status": "skipped",
            "summary": "缺少 Feishu chat_id，未绑定审批卡投递群。",
            "evidence": {"feishu_chat_id_present": False},
        }
    try:
        from app.domain.competitor_prices.repository import update_competitor_watch_group
        from app.domain.competitor_prices.types import CompetitorWatchGroupPatch

        watch_group = await update_competitor_watch_group(
            watch_group_key=watch_group_key,
            patch=CompetitorWatchGroupPatch(feishu_chat_id=chat_id),
        )
    except Exception as exc:
        return {
            "status": "failed",
            "summary": "Feishu chat_id 绑定失败。",
            "evidence": {"error_type": type(exc).__name__},
        }
    return {
        "status": "success" if watch_group else "not_found",
        "summary": "已绑定竞品监控组审批卡投递群。" if watch_group else "未找到竞品监控组。",
        "evidence": {"feishu_chat_id_configured": bool(watch_group)},
    }


async def _resolve_watch_group_key_for_chat(chat_id: str | None) -> str | None:
    chat_id = _optional_str(chat_id)
    if not chat_id:
        return None
    try:
        from app.domain.competitor_prices.repository import (
            get_competitor_watch_group_by_feishu_chat_id,
        )

        watch_group = await get_competitor_watch_group_by_feishu_chat_id(chat_id)
    except Exception:
        return None
    if not watch_group:
        return None
    group_key = str(watch_group.get("group_key") or "").strip()
    return group_key or None


def _preview_account_policy(preview: dict[str, Any]) -> dict[str, Any]:
    return normalized_account_policy(
        {
            "connection_mode": preview.get("connection_mode"),
            "can_auto_write_price": preview.get("can_auto_write_price"),
            "write_policy_message": preview.get("write_policy_message"),
        }
    )


def _custom_price_write_not_allowed_response(
    preview_id: int,
    *,
    platform: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "write_not_allowed",
        "summary": write_not_allowed_reason(platform),
        "card_callback_response": _feishu_card_callback_response(
            toast_type="warning",
            toast_content="当前账号仅可查看调价建议，无法通过卡片改价，请手动登录 EBK 处理。",
        ),
        "evidence": {"preview_id": preview_id, "execute_triggered": False},
    }


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
