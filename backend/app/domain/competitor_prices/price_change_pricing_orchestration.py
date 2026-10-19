from __future__ import annotations

from typing import Any

from app.domain.competitor_prices.repository import get_competitor_watch_group_detail
from app.domain.notifications.feishu_client import FeishuBotClient
from app.domain.competitor_prices.pricing_reference_summary import build_pricing_reference_summary
from app.domain.pricing_approvals.cards import (
    build_pricing_reference_card,
    build_pricing_reference_fallback_text,
)
from app.domain.pricing_suggestions.preview import create_pricing_reference_preview_from_alert
from app.domain.pricing_safety import account_policy_allows_write, normalized_account_policy, strip_sensitive


class PricingReferenceSummaryClient:
    async def summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("pricing reference AI summary is not configured")


def _alert_account_policy(alert_record: dict[str, Any]) -> dict[str, Any]:
    evidence = alert_record.get("evidence") if isinstance(alert_record.get("evidence"), dict) else {}
    account_policy = (
        evidence.get("account_write_policy")
        if isinstance(evidence.get("account_write_policy"), dict)
        else {}
    )
    return normalized_account_policy(
        {
            "connection_mode": alert_record.get("connection_mode") or account_policy.get("connection_mode"),
            "can_auto_write_price": alert_record.get(
                "can_auto_write_price",
                account_policy.get("can_auto_write_price"),
            ),
            "write_policy_message": alert_record.get("write_policy_message")
            or account_policy.get("write_policy_message"),
        }
    )


async def try_auto_preview_and_approval_for_alert(
    *,
    alert_record: dict[str, Any],
    watch_group_key: str,
    actor_id: str,
) -> dict[str, Any]:
    """Send a competitor-triggered pricing reference card without approve actions."""

    if str(alert_record.get("status") or "") != "suggestion_ready":
        return {
            "outcome": "skipped",
            "reason": "alert_not_suggestion_ready",
            "alert_status": alert_record.get("status"),
        }
    if not isinstance(alert_record.get("suggestions"), list) or not alert_record.get("suggestions"):
        return {
            "outcome": "skipped",
            "reason": "missing_pricing_suggestions",
            "alert_status": alert_record.get("status"),
        }

    account_policy = _alert_account_policy(alert_record)
    if not account_policy_allows_write(account_policy):
        return {
            "outcome": "skipped",
            "reason": "suggestion_only_account",
            "alert_status": alert_record.get("status"),
            "account_policy": account_policy,
            "summary": "直连或未确认账号仅生成调价建议，不发送调价审批卡。",
        }

    pricing_reference_summary = await build_pricing_reference_summary(
        alert_record,
        ai_client=PricingReferenceSummaryClient(),
    )
    reference_preview = await create_pricing_reference_preview_from_alert(
        alert_record=alert_record,
        actor_id=actor_id,
        pricing_reference_summary=pricing_reference_summary,
    )
    preview_payload = reference_preview.model_dump(mode="json")
    preview_payload["pricing_reference_summary"] = pricing_reference_summary
    result: dict[str, Any] = {
        "outcome": "reference_card_ready",
        "preview_status": preview_payload["status"],
        "preview_id": reference_preview.preview_id,
        "approval_status": None,
        "approval_delivery": None,
        "pricing_reference_summary": pricing_reference_summary,
    }
    if reference_preview.status != "reference_ready" or not reference_preview.preview_id:
        result.update(
            {
                "outcome": "reference_preview_failed",
                "summary": reference_preview.summary,
                "next_action": reference_preview.next_action,
            }
        )
        return result

    group = await get_competitor_watch_group_detail(
        watch_group_key=watch_group_key,
        include_secrets=True,
        create_if_missing=False,
    )
    chat_id = str((group or {}).get("feishu_chat_id") or "").strip()
    if not chat_id:
        result.update(
            {
                "outcome": "approval_config_required",
                "approval_status": "config_required",
                "summary": "未配置竞品监控组 Feishu chat_id，调价审批卡未发送。",
                "evidence": {"feishu_chat_id_configured": False},
                "next_action": "在竞品监控组配置 Feishu chat_id，或在群里发送一次手动调价命令完成绑定。",
            }
        )
        return result

    title_source = (
        (group or {}).get("price_change_alert_title")
        or (group or {}).get("brief_title")
        or (group or {}).get("name")
        or ""
    )
    card = build_pricing_reference_card(preview_payload, title=str(title_source))
    fallback_text = build_pricing_reference_fallback_text(preview_payload)
    try:
        push = await FeishuBotClient(chat_id=chat_id).send_interactive_card(
            chat_id,
            card,
            fallback_text=fallback_text,
        )
    except Exception as exc:
        result.update(
            {
                "outcome": "approval_send_error",
                "approval_status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
            }
        )
        return result

    sanitized_push = strip_sensitive(push)
    if isinstance(sanitized_push, dict):
        sanitized_push.pop("evidence", None)
    result.update(
        {
            "outcome": "reference_card_sent" if push.get("status") == "success" else "reference_card_send_failed",
            "approval_status": push.get("status"),
            "approval_delivery": "feishu_chat",
            "feishu_push": sanitized_push,
            "evidence": {
                "feishu_chat_id_configured": True,
                "operator_target_price_required": True,
                "api_direct_execute_allowed": False,
                "frontend_execute_allowed": False,
            },
        }
    )
    return result
