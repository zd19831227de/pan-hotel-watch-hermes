from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def bridge_dir() -> Path:
    root = os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "hermes",
    )
    return Path(root) / "feishu-session-bridge"


def _safe_key(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", str(value or "").strip())[:200]


def write_session(
    *,
    chat_id: str = "",
    session_id: str = "",
    message_id: str = "",
    open_id: str = "",
) -> None:
    payload = {
        "chat_id": str(chat_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "message_id": str(message_id or "").strip(),
        "open_id": str(open_id or "").strip(),
    }
    if not payload["message_id"] and not payload["chat_id"]:
        return
    directory = bridge_dir()
    directory.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False)
    for key in (payload["chat_id"], payload["session_id"]):
        if not key:
            continue
        path = directory / f"{_safe_key(key)}.json"
        path.write_text(encoded, encoding="utf-8")


def load_session(*, chat_id: str = "", session_id: str = "") -> dict[str, Any]:
    directory = bridge_dir()
    for key in (str(chat_id or "").strip(), str(session_id or "").strip()):
        if not key:
            continue
        path = directory / f"{_safe_key(key)}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def merge_feishu_session(arguments: dict[str, Any]) -> dict[str, Any]:
    merged = dict(arguments or {})
    chat_id = str(merged.get("chat_id") or "").strip()
    session_id = str(merged.get("hermes_session_id") or os.environ.get("HERMES_SESSION_ID") or "").strip()

    if not str(merged.get("message_id") or "").strip():
        bridge = load_session(chat_id=chat_id, session_id=session_id)
        if bridge.get("message_id"):
            merged["message_id"] = bridge["message_id"]
        if bridge.get("chat_id") and not chat_id:
            merged["chat_id"] = bridge["chat_id"]
        if bridge.get("open_id") and not str(merged.get("open_id") or "").strip():
            merged["open_id"] = bridge["open_id"]

    if not str(merged.get("message_id") or "").strip():
        env_message_id = str(os.environ.get("HERMES_SESSION_MESSAGE_ID") or "").strip()
        if env_message_id:
            merged["message_id"] = env_message_id
    if not str(merged.get("chat_id") or "").strip():
        env_chat_id = str(os.environ.get("HERMES_SESSION_CHAT_ID") or "").strip()
        if env_chat_id:
            merged["chat_id"] = env_chat_id
    if not str(merged.get("open_id") or "").strip():
        env_open_id = str(os.environ.get("HERMES_ACTOR_ID") or os.environ.get("HERMES_SESSION_USER_ID") or "").strip()
        if env_open_id:
            merged["open_id"] = env_open_id
    return merged
