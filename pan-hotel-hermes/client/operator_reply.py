from __future__ import annotations

import time
from typing import Any

from client.watch_api import WatchApiClient

_RECENT_CARD_PUSHES: dict[str, list[tuple[float, str]]] = {}
_RECENT_EBK_CAPTURES: dict[str, set[str]] = {}
_CARD_DEDUPE_WINDOW_SECONDS = 120


def _card_push_fingerprint(payload: dict[str, Any]) -> str:
    operator = payload.get("operator_summary")
    operator_dict = operator if isinstance(operator, dict) else {}
    return "|".join(
        [
            str(payload.get("feishu_card_kind") or ""),
            str(operator_dict.get("latest_captured_at") or ""),
            str(payload.get("source") or operator_dict.get("source") or ""),
        ]
    )


def _should_skip_duplicate_card(*, chat_id: str, payload: dict[str, Any]) -> bool:
    fingerprint = _card_push_fingerprint(payload)
    operator = payload.get("operator_summary")
    operator_dict = operator if isinstance(operator, dict) else {}
    card_kind = str(payload.get("feishu_card_kind") or "")
    captured_at = str(operator_dict.get("latest_captured_at") or "").strip()
    now = time.time()
    recent = _RECENT_CARD_PUSHES.setdefault(chat_id, [])
    _RECENT_CARD_PUSHES[chat_id] = [
        (pushed_at, pushed_fp)
        for pushed_at, pushed_fp in recent
        if now - pushed_at <= _CARD_DEDUPE_WINDOW_SECONDS
    ]
    if fingerprint and not fingerprint.startswith("|"):
        if any(pushed_fp == fingerprint for _, pushed_fp in _RECENT_CARD_PUSHES[chat_id]):
            return True
    if card_kind in {"ebk_latest", "beneficial_data"} and captured_at:
        ebk_recent = _RECENT_EBK_CAPTURES.setdefault(chat_id, set())
        if captured_at in ebk_recent:
            return True
        ebk_recent.add(captured_at)
    if fingerprint and not fingerprint.startswith("|"):
        _RECENT_CARD_PUSHES[chat_id].append((now, fingerprint))
    return False


def _forget_duplicate_card_attempt(*, chat_id: str, payload: dict[str, Any]) -> None:
    fingerprint = _card_push_fingerprint(payload)
    if fingerprint and not fingerprint.startswith("|"):
        recent = _RECENT_CARD_PUSHES.get(chat_id)
        if recent:
            _RECENT_CARD_PUSHES[chat_id] = [
                (pushed_at, pushed_fp)
                for pushed_at, pushed_fp in recent
                if pushed_fp != fingerprint
            ]
    operator = payload.get("operator_summary")
    operator_dict = operator if isinstance(operator, dict) else {}
    card_kind = str(payload.get("feishu_card_kind") or "")
    captured_at = str(operator_dict.get("latest_captured_at") or "").strip()
    if card_kind in {"ebk_latest", "beneficial_data"} and captured_at:
        ebk_recent = _RECENT_EBK_CAPTURES.get(chat_id)
        if ebk_recent is not None:
            ebk_recent.discard(captured_at)


def _card_send_failed_reply(exc: Exception) -> dict[str, Any]:
    detail = str(exc).strip() or exc.__class__.__name__
    return {
        "status": "failed",
        "summary": f"Feishu card send failed: {detail}",
        "error_type": exc.__class__.__name__,
    }


def _card_send_failed_payload(
    payload: dict[str, Any],
    *,
    reply: dict[str, Any],
) -> dict[str, Any]:
    return {
        **payload,
        "feishu_card_sent": False,
        "feishu_card_reply": reply,
        "operator_reply_note": (
            "Feishu card send failed; keep the original payload status and answer from the "
            "original payload. Do not treat card delivery failure as a data lookup failure."
        ),
        "feishu_card_delivery": None,
    }


def _card_fallback_text(payload: dict[str, Any]) -> str | None:
    operator = payload.get("operator_summary")
    if isinstance(operator, dict):
        intro = str(operator.get("intro") or "").strip()
        if intro:
            return intro
    summary = str(payload.get("summary") or "").strip()
    return summary or None


def _resolve_response_mode(
    payload: dict[str, Any],
    *,
    response_mode: str | None = None,
) -> str:
    candidate = str(response_mode or payload.get("response_mode") or "").strip().lower()
    if candidate == "detail":
        return "detail"
    if candidate in {"summary", "text"}:
        return "text"
    return "text"


async def finalize_operator_payload(
    client: WatchApiClient,
    payload: dict[str, Any],
    *,
    chat_id: str | None = None,
    message_id: str | None = None,
    store_hint: str | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    """Push briefing-style Feishu cards to the group chat when possible."""
    resolved_mode = _resolve_response_mode(payload, response_mode=response_mode)
    if resolved_mode == "text":
        result = {
            **payload,
            "response_mode": "text",
            "feishu_card_sent": False,
            "reply_mode": "text",
            "operator_reply_note": (
                "text 模式：未发送飞书卡片。请用简体中文短回复用户所问指标/结论与数据时间；"
                "禁止 MOBILE_SUMMARY_JSON；禁止重复粘贴整表；禁止再调配置类工具发卡。"
            ),
        }
        # text-first：勿留下 card_first / feishu_card 误导模型「已发卡」。
        result.pop("feishu_card", None)
        result.pop("feishu_card_kind", None)
        operator = result.get("operator_summary")
        if isinstance(operator, dict):
            result["operator_summary"] = {
                **operator,
                "reply_mode": "text_only",
                "response_mode": "text",
            }
        return result
    card = payload.get("feishu_card")
    if not isinstance(card, dict):
        return {**payload, "response_mode": resolved_mode}

    normalized_chat_id = str(chat_id or "").strip() or None
    normalized_message_id = str(message_id or "").strip() or None
    fallback_text = _card_fallback_text(payload)

    if normalized_chat_id:
        if _should_skip_duplicate_card(chat_id=normalized_chat_id, payload=payload):
            return {
                **payload,
                "response_mode": "detail",
                "feishu_card_sent": True,
                "feishu_card_skipped_duplicate": True,
                "operator_reply_note": (
                    "本群刚推送过相同内容的飞书卡片，已跳过重复发送。"
                    "请直接引用上一轮卡片或 operator_summary 文字回复，勿再次调 get_ebk_latest_result。"
                ),
            }
        try:
            reply = await client.send_operator_feishu_card(
                chat_id=normalized_chat_id,
                card=card,
                store_hint=store_hint,
                fallback_text=fallback_text,
            )
        except Exception as exc:
            _forget_duplicate_card_attempt(chat_id=normalized_chat_id, payload=payload)
            return _card_send_failed_payload(
                payload,
                reply=_card_send_failed_reply(exc),
            )
        sent = reply.get("status") == "success"
        if not sent:
            _forget_duplicate_card_attempt(chat_id=normalized_chat_id, payload=payload)
        delivery = str(reply.get("delivery") or "")
        return {
            **payload,
            "response_mode": "detail",
            "feishu_card_sent": sent,
            "feishu_card_reply": reply,
            "operator_reply_note": (
                "已通过飞书机器人卡片回复到本群。文字只需说明已回复卡片，必要时补充口径或建议，"
                "禁止 Markdown 文档式排版，勿重复卡片表格。"
                if sent
                else f"飞书简报卡片推送失败：{reply.get('summary') or '未知错误'}"
            ),
            "feishu_card_delivery": delivery or ("app_bot" if sent else None),
        }

    if normalized_message_id:
        try:
            reply = await client.reply_operator_feishu_card(
                message_id=normalized_message_id,
                card=card,
                fallback_text=fallback_text,
            )
        except Exception as exc:
            return _card_send_failed_payload(
                payload,
                reply=_card_send_failed_reply(exc),
            )
        sent = reply.get("status") == "success"
        return {
            **payload,
            "response_mode": "detail",
            "feishu_card_sent": sent,
            "feishu_card_reply": reply,
            "operator_reply_note": (
                "已通过飞书卡片回复。文字可说明已通过卡片回复，必要时补充口径或建议，"
                "禁止 Markdown 文档式排版，勿重复卡片表格。"
                if sent
                else f"飞书卡片发送失败：{reply.get('summary') or '未知错误'}"
            ),
            "feishu_card_delivery": "reply" if sent else None,
        }

    return {
        **payload,
        "response_mode": "detail",
        "feishu_card_sent": False,
        "operator_reply_note": (
            "未自动发送飞书卡片；缺少 chat_id。"
            "请直接用文字给店长说明结果，不要要求用户补发消息或解释 message_id。"
        ),
    }
