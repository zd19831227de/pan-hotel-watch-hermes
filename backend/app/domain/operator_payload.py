from __future__ import annotations

import re
from typing import Any, Literal

ARTIFACT_PLACEHOLDER = "artifact_not_returned"
PROFILE_PLACEHOLDER = "profile_path_not_returned"
SECRET_PLACEHOLDER = "***"
PERSISTED_ARTIFACT_PLACEHOLDER = "artifact_not_persisted"
PERSISTED_PROFILE_PLACEHOLDER = "profile_path_not_persisted"

SanitizationScope = Literal["persistence", "operator", "supervisor"]

_ARTIFACT_KEY_TERMS = ("artifact", "screenshot")
_PROFILE_KEY_TERMS = ("profile",)
_SECRET_KEY_TERMS = (
    "cookie",
    "password",
    "secret",
    "token",
    "verification_code",
    "webhook",
)
_SAFE_BOOLEAN_SUFFIXES = (
    "_called",
    "_configured",
    "_returned",
    "_stored",
)
_LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:[\\/]|(?:^|[\\/])(?:output|\.runtime|browser-state|playwright)[\\/])"
)
_FEISHU_WEBHOOK_PATTERN = re.compile(r"(?i)https?://[^\s/]*feishu\.cn/[^\s]*hook[^\s]*")


def sanitize_operator_payload(value: Any) -> Any:
    """Remove local artifacts and secrets before returning payloads to the console."""
    return sanitize_payload(value, scope="operator")


def sanitize_persisted_payload(value: Any) -> Any:
    """Remove fields that must never be written to audit/persistence JSON."""
    return sanitize_payload(value, scope="persistence")


def sanitize_supervisor_payload(value: Any) -> Any:
    """Sanitize supervisor responses before explicit permissioned restoration."""
    return sanitize_payload(value, scope="supervisor")


def sanitize_payload(value: Any, *, scope: SanitizationScope = "operator") -> Any:
    """Apply the shared persistence/operator/supervisor field contract."""
    if scope not in {"persistence", "operator", "supervisor"}:
        raise ValueError(f"Unsupported sanitization scope: {scope}")
    return _sanitize(value, scope=scope)


def _sanitize(
    value: Any,
    *,
    key: str = "",
    scope: SanitizationScope = "operator",
) -> Any:
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item, key=str(item_key), scope=scope)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item, key=key, scope=scope) for item in value]
    if _is_artifact_key(key):
        placeholder = (
            PERSISTED_ARTIFACT_PLACEHOLDER
            if scope == "persistence"
            else ARTIFACT_PLACEHOLDER
        )
        return placeholder if value else value
    if _is_profile_key(key):
        placeholder = (
            PERSISTED_PROFILE_PLACEHOLDER
            if scope == "persistence"
            else PROFILE_PLACEHOLDER
        )
        return (
            value
            if _is_safe_boolean_flag(key, value)
            else placeholder
            if value
            else value
        )
    if _is_secret_key(key):
        return value if _is_safe_boolean_flag(key, value) else SECRET_PLACEHOLDER if value else value
    if isinstance(value, str):
        if _FEISHU_WEBHOOK_PATTERN.search(value):
            return SECRET_PLACEHOLDER
        if _looks_like_local_artifact_path(value):
            return (
                PERSISTED_ARTIFACT_PLACEHOLDER
                if scope == "persistence"
                else ARTIFACT_PLACEHOLDER
            )
    return value


def _is_artifact_key(key: str) -> bool:
    normalized = key.lower()
    return any(term in normalized for term in _ARTIFACT_KEY_TERMS)


def _is_profile_key(key: str) -> bool:
    normalized = key.lower()
    return any(term in normalized for term in _PROFILE_KEY_TERMS)


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return any(term in normalized for term in _SECRET_KEY_TERMS)


def _is_safe_boolean_flag(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        return False
    normalized = key.lower()
    return normalized.endswith(_SAFE_BOOLEAN_SUFFIXES)


def _looks_like_local_artifact_path(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.lower().startswith(("http://", "https://")):
        return False
    return bool(_LOCAL_PATH_PATTERN.search(text))
