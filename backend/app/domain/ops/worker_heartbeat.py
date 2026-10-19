from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import get_settings

DEFAULT_STALE_AFTER_SECONDS = 300
_SENSITIVE_TERMS = (
    "password",
    "cookie",
    "token",
    "secret",
    "webhook",
    "profile",
    "screenshot",
    "database",
    "dsn",
    "connection",
)
_PATH_KEYS = {"path", "paths", "file", "files", "dir", "directory"}


def write_worker_heartbeat(
    worker_name: str,
    event: str,
    *,
    status: str = "running",
    tick_summary: dict[str, Any] | None = None,
    error: BaseException | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    heartbeat_path = path or worker_heartbeat_path(worker_name)
    current_time = now or _utc_now()
    try:
        previous = _read_heartbeat_payload(heartbeat_path)
    except json.JSONDecodeError:
        previous = {}
    pid = os.getpid()
    same_process = previous.get("pid") == pid
    payload: dict[str, Any] = {
        "name": worker_name,
        "worker_name": worker_name,
        "pid": pid,
        "event": event,
        "status": status,
        "started_at": previous.get("started_at") if same_process else _isoformat(current_time),
        "updated_at": _isoformat(current_time),
    }
    for key in (
        "last_tick_started_at",
        "last_tick_finished_at",
        "last_success_at",
        "last_error_at",
        "last_error",
        "last_tick_summary",
    ):
        if key in previous:
            payload[key] = previous[key]
    if event == "tick_started":
        payload["last_tick_started_at"] = _isoformat(current_time)
    if event == "tick_finished":
        payload["last_tick_finished_at"] = _isoformat(current_time)
        payload["last_success_at"] = _isoformat(current_time)
        payload.pop("last_error", None)
    if tick_summary is not None:
        payload["tick_summary"] = _sanitize_payload(tick_summary)
        payload["last_tick_summary"] = _sanitize_payload(tick_summary)
    if error is not None:
        payload["last_error_at"] = _isoformat(current_time)
        payload["last_error"] = {
            "type": type(error).__name__,
            "message": str(error)[:300],
        }
        payload["error_type"] = type(error).__name__
        payload["error"] = str(error)[:300]
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload


def read_worker_heartbeat(worker_name: str) -> dict[str, Any] | None:
    path = worker_heartbeat_path(worker_name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def get_worker_heartbeat_status(
    worker_name: str,
    *,
    path: Path | None = None,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    heartbeat_path = path or worker_heartbeat_path(worker_name)
    current_time = now or _utc_now()
    if not heartbeat_path.exists():
        return _missing_status(worker_name, stale_after_seconds=stale_after_seconds)
    try:
        payload = _read_heartbeat_payload(heartbeat_path)
    except json.JSONDecodeError:
        return {
            **_missing_status(worker_name, stale_after_seconds=stale_after_seconds),
            "status": "error",
            "last_error": {
                "type": "InvalidHeartbeat",
                "message": "Worker heartbeat file could not be parsed.",
            },
        }
    updated_at = _parse_datetime(payload.get("updated_at"))
    age_seconds = int((current_time - updated_at).total_seconds()) if updated_at else None
    computed_status = str(payload.get("status") or "unknown")
    if computed_status != "error":
        if age_seconds is None:
            computed_status = "missing"
        elif age_seconds > stale_after_seconds:
            computed_status = "stale"
        else:
            computed_status = "healthy"
    summary = payload.get("last_tick_summary") or payload.get("tick_summary") or {}
    return {
        "name": worker_name,
        "status": computed_status,
        "process_confirmed": computed_status == "healthy",
        "pid": _int_or_none(payload.get("pid")),
        "updated_at": payload.get("updated_at"),
        "last_heartbeat_at": payload.get("updated_at"),
        "heartbeat_age_seconds": age_seconds,
        "stale_after_seconds": stale_after_seconds,
        "started_at": payload.get("started_at"),
        "last_tick_started_at": payload.get("last_tick_started_at"),
        "last_tick_finished_at": payload.get("last_tick_finished_at"),
        "last_success_at": payload.get("last_success_at"),
        "last_error_at": payload.get("last_error_at"),
        "last_error": payload.get("last_error"),
        "last_tick_summary": summary if isinstance(summary, dict) else {},
    }


def worker_heartbeat_path(worker_name: str) -> Path:
    return Path(get_settings().browser_state_dir).parent / "heartbeats" / f"{worker_name}.json"


def _missing_status(worker_name: str, *, stale_after_seconds: int) -> dict[str, Any]:
    return {
        "name": worker_name,
        "status": "missing",
        "process_confirmed": False,
        "pid": None,
        "updated_at": None,
        "last_heartbeat_at": None,
        "heartbeat_age_seconds": None,
        "stale_after_seconds": stale_after_seconds,
        "started_at": None,
        "last_tick_started_at": None,
        "last_tick_finished_at": None,
        "last_success_at": None,
        "last_error_at": None,
        "last_error": None,
        "last_tick_summary": {},
    }


def _read_heartbeat_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _sanitize_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)[:300]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower()
            if normalized in _PATH_KEYS or any(term in normalized for term in _SENSITIVE_TERMS):
                continue
            sanitized[key_text] = _sanitize_payload(item, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_payload(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:300]


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
