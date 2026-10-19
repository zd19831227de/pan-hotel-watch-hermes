from __future__ import annotations

from typing import Any

from client.watch_groups import WATCH_KIND_COMPETITOR, WATCH_KIND_EBK


def _normalize_operator_id(operator_id: str | None) -> str | None:
    normalized = str(operator_id or "").strip()
    return normalized or None


def _normalize_chat_id(chat_id: str | None) -> str | None:
    normalized = str(chat_id or "").strip()
    return normalized or None


def _pending_is_active(summary: dict[str, Any] | None) -> bool:
    if not isinstance(summary, dict):
        return False
    return bool(summary.get("active"))


async def register_stale_immediate_run_offer(
    client: Any,
    *,
    payload: dict[str, Any],
    chat_id: str | None,
    operator_id: str | None,
    watch_kind: str,
    watch_group_key: str | None,
) -> None:
    normalized_chat_id = _normalize_chat_id(chat_id)
    normalized_operator_id = _normalize_operator_id(operator_id)
    normalized_group_key = str(watch_group_key or "").strip()
    if not normalized_chat_id or not normalized_operator_id or not normalized_group_key:
        return
    operator = payload.get("operator_summary")
    if not isinstance(operator, dict):
        return
    if not operator.get("suggest_immediate_run"):
        return
    await client.put_json(
        "/api/v1/ops/operator-pending-action",
        body={
            "chat_id": normalized_chat_id,
            "operator_id": normalized_operator_id,
            "watch_kind": watch_kind,
            "watch_group_key": normalized_group_key,
            "source": "stale_latest_offer",
        },
    )


async def attach_pending_operator_action(
    client: Any,
    payload: dict[str, Any],
    *,
    chat_id: str | None,
    operator_id: str | None,
) -> dict[str, Any]:
    normalized_chat_id = _normalize_chat_id(chat_id)
    normalized_operator_id = _normalize_operator_id(operator_id)
    if not normalized_chat_id or not normalized_operator_id:
        return payload
    pending_payload = await client.get_json(
        "/api/v1/ops/operator-pending-action",
        params={"chat_id": normalized_chat_id, "operator_id": normalized_operator_id},
    )
    pending_summary = pending_payload.get("operator_summary")
    if not isinstance(pending_summary, dict):
        return payload
    pending_action = pending_summary.get("pending_operator_action")
    if not _pending_is_active(pending_action if isinstance(pending_action, dict) else None):
        return payload
    operator = payload.get("operator_summary")
    if not isinstance(operator, dict):
        operator = {}
        payload["operator_summary"] = operator
    operator["pending_operator_action"] = pending_action
    return payload


async def clear_pending_operator_action(
    client: Any,
    *,
    chat_id: str | None,
    operator_id: str | None,
) -> None:
    normalized_chat_id = _normalize_chat_id(chat_id)
    normalized_operator_id = _normalize_operator_id(operator_id)
    if not normalized_chat_id or not normalized_operator_id:
        return
    await client.delete_json(
        "/api/v1/ops/operator-pending-action",
        params={"chat_id": normalized_chat_id, "operator_id": normalized_operator_id},
    )


def watch_kind_from_payload(payload: dict[str, Any]) -> str | None:
    operator = payload.get("operator_summary")
    if isinstance(operator, dict):
        watch_kind = str(operator.get("watch_kind") or "").strip()
        if watch_kind in {WATCH_KIND_COMPETITOR, WATCH_KIND_EBK}:
            return watch_kind
    watch_kind = str(payload.get("watch_kind") or "").strip()
    if watch_kind in {WATCH_KIND_COMPETITOR, WATCH_KIND_EBK}:
        return watch_kind
    return None
