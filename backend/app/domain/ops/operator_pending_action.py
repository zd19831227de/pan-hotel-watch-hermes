from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.settings import get_settings

DEFAULT_EXPIRE_MINUTES = 30
_RUN_TOOL_BY_KIND = {
    "competitor": "run_competitor_watch_once",
    "ebk": "run_ebk_watch_once",
}


def operator_pending_store_path() -> Path:
    return Path(get_settings().browser_state_dir).parent / "operator_pending_actions.json"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _action_key(chat_id: str, operator_id: str) -> str:
    return f"{chat_id.strip()}::{operator_id.strip()}"


def _read_store(path: Path | None = None) -> dict[str, Any]:
    store_path = path or operator_pending_store_path()
    if not store_path.exists():
        return {}
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_store(store: dict[str, Any], path: Path | None = None) -> None:
    store_path = path or operator_pending_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _normalize_watch_kind(watch_kind: str) -> str | None:
    normalized = str(watch_kind or "").strip().lower()
    if normalized in _RUN_TOOL_BY_KIND:
        return normalized
    return None


def _is_expired(record: dict[str, Any], *, now: datetime | None = None) -> bool:
    expires_at = str(record.get("expires_at") or "").strip()
    if not expires_at:
        return True
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    except ValueError:
        return True
    current = now or _utc_now()
    return current >= parsed.astimezone(UTC)


async def upsert_immediate_run_offer(
    *,
    chat_id: str,
    operator_id: str,
    watch_kind: str,
    watch_group_key: str,
    source: str = "stale_latest_offer",
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    normalized_chat_id = str(chat_id or "").strip()
    normalized_operator_id = str(operator_id or "").strip()
    normalized_watch_kind = _normalize_watch_kind(watch_kind)
    normalized_group_key = str(watch_group_key or "").strip()
    if not normalized_chat_id or not normalized_operator_id:
        return {"status": "invalid_arguments", "summary": "chat_id 与 operator_id 不能为空。"}
    if normalized_watch_kind is None or not normalized_group_key:
        return {"status": "invalid_arguments", "summary": "watch_kind 或 watch_group_key 无效。"}

    current = now or _utc_now()
    expires = current + timedelta(minutes=DEFAULT_EXPIRE_MINUTES)
    record = {
        "status": "offered",
        "action_type": "immediate_run",
        "source": source,
        "chat_id": normalized_chat_id,
        "operator_id": normalized_operator_id,
        "watch_kind": normalized_watch_kind,
        "watch_group_key": normalized_group_key,
        "run_tool_name": _RUN_TOOL_BY_KIND[normalized_watch_kind],
        "offered_at": _isoformat(current),
        "expires_at": _isoformat(expires),
    }
    store = _read_store(path)
    store[_action_key(normalized_chat_id, normalized_operator_id)] = record
    _write_store(store, path)
    return record


async def get_pending_action(
    chat_id: str,
    operator_id: str,
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    normalized_chat_id = str(chat_id or "").strip()
    normalized_operator_id = str(operator_id or "").strip()
    if not normalized_chat_id or not normalized_operator_id:
        return None
    record = _read_store(path).get(_action_key(normalized_chat_id, normalized_operator_id))
    if not isinstance(record, dict):
        return None
    if _is_expired(record, now=now):
        await clear_pending_action(normalized_chat_id, normalized_operator_id, path=path)
        return None
    return record


async def clear_pending_action(
    chat_id: str,
    operator_id: str,
    *,
    path: Path | None = None,
) -> bool:
    normalized_chat_id = str(chat_id or "").strip()
    normalized_operator_id = str(operator_id or "").strip()
    if not normalized_chat_id or not normalized_operator_id:
        return False
    store = _read_store(path)
    key = _action_key(normalized_chat_id, normalized_operator_id)
    if key not in store:
        return False
    del store[key]
    _write_store(store, path)
    return True


def build_pending_operator_summary(pending: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(pending, dict):
        return {"active": False}
    watch_kind = str(pending.get("watch_kind") or "")
    run_tool_name = str(pending.get("run_tool_name") or "")
    watch_group_key = str(pending.get("watch_group_key") or "")
    if not watch_kind or not run_tool_name:
        return {"active": False}
    kind_label = "竞品价格" if watch_kind == "competitor" else "EBK 数据"
    return {
        "active": True,
        "status": pending.get("status"),
        "action_type": pending.get("action_type"),
        "watch_kind": watch_kind,
        "watch_group_key": watch_group_key,
        "run_tool_name": run_tool_name,
        "offered_at": pending.get("offered_at"),
        "expires_at": pending.get("expires_at"),
        "agent_instruction": (
            f"有待确认的现读请求：{kind_label}（组 {watch_group_key}）。"
            f"用户短回复「要/好的/立即读」时，直接调用 {run_tool_name}，无需重复询问。"
        ),
    }
