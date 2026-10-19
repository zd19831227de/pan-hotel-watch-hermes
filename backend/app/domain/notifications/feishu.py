from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from app.core.settings import get_settings


class FeishuPushResult(BaseModel):
    status: str
    summary: str
    channel: str = "feishu"
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


async def push_text_to_feishu(title: str, text: str) -> FeishuPushResult:
    return await push_text_to_feishu_webhook(
        title=title,
        text=text,
        webhook_url=get_settings().feishu_webhook_url,
    )


async def push_text_to_feishu_webhook(
    *,
    title: str,
    text: str,
    webhook_url: str | None,
) -> FeishuPushResult:
    if not webhook_url:
        return FeishuPushResult(
            status="config_required",
            summary="Feishu webhook is not configured.",
            evidence={"feishu_webhook_configured": False},
            next_action="Configure a Feishu webhook URL in the relevant business module.",
        )

    payload = {
        "msg_type": "text",
        "content": {
            "text": f"{title}\n\n{text}",
        },
    }
    return await _post_feishu_webhook(
        webhook_url=webhook_url,
        payload=payload,
        title=title,
        msg_type="text",
    )


async def push_interactive_card_to_feishu_webhook(
    *,
    title: str,
    card: dict[str, Any],
    webhook_url: str | None,
    fallback_text: str | None = None,
) -> FeishuPushResult:
    """Send a Feishu custom-bot interactive card with the required msg_type field."""

    if not webhook_url:
        return FeishuPushResult(
            status="config_required",
            summary="Feishu webhook is not configured.",
            evidence={"feishu_webhook_configured": False},
            next_action="Configure a Feishu webhook URL in the relevant business module.",
        )
    result = await _post_feishu_webhook(
        webhook_url=webhook_url,
        payload={"msg_type": "interactive", "card": card},
        title=title,
        msg_type="interactive",
    )
    if result.status == "success" or not fallback_text:
        return result
    fallback_title = _feishu_webhook_timed_text_title(title)
    fallback_result = await push_text_to_feishu_webhook(
        title=fallback_title,
        text=fallback_text,
        webhook_url=webhook_url,
    )
    fallback_result.evidence["card_push"] = result.model_dump(mode="json")
    fallback_result.data["fallback_from"] = "interactive"
    return fallback_result


async def _post_feishu_webhook(
    *,
    webhook_url: str,
    payload: dict[str, Any],
    title: str,
    msg_type: str,
) -> FeishuPushResult:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(webhook_url, json=payload)
    except httpx.HTTPError as exc:
        return FeishuPushResult(
            status="failed",
            summary="Feishu webhook push failed.",
            data={"title": title, "msg_type": msg_type},
            evidence={
                "feishu_webhook_configured": True,
                "error": str(exc),
            },
            next_action="Check Feishu webhook URL, bot settings, and network access.",
        )

    response_payload = _safe_response_json(response)
    feishu_code = response_payload.get("code") if isinstance(response_payload, dict) else None
    feishu_msg = response_payload.get("msg") if isinstance(response_payload, dict) else None
    is_success = response.is_success and (feishu_code in {None, 0})
    return FeishuPushResult(
        status="success" if is_success else "failed",
        summary=(
            "Feishu webhook push completed."
            if is_success
            else "Feishu webhook push failed."
        ),
        data={
            "http_status": response.status_code,
            "title": title,
            "msg_type": msg_type,
            "feishu_code": feishu_code,
            "feishu_msg": feishu_msg,
        },
        evidence={
            "feishu_webhook_configured": True,
            "response_preview": response.text[:300],
        },
        next_action=None if is_success else "Check Feishu webhook URL and bot settings.",
    )


def _safe_response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _feishu_webhook_timed_text_title(title: str) -> str:
    normalized = str(title or "").strip() or "消息通知"
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return f"{normalized} · {now:%H:%M}"
