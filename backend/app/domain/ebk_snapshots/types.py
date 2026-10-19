from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.operator_payload import sanitize_operator_payload

EbkPlatform = Literal["ctrip", "meituan", "fliggy"]
EbkSnapshotStatus = Literal[
    "success",
    "partial",
    "human_required",
    "needs_review",
    "error",
]

_SUPPORTED_PLATFORMS = frozenset({"ctrip", "meituan", "fliggy"})
_SUPPORTED_STATUSES = frozenset(
    {"success", "partial", "human_required", "needs_review", "error"}
)


class UnifiedEbkSnapshot(BaseModel):
    """Cross-platform EBK read snapshot for later Hermes analysis."""

    platform: EbkPlatform
    platform_account_id: str
    browser_session_id: str
    read_at: datetime | str
    status: EbkSnapshotStatus
    summary: str
    datasets: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    platform_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("platform")
    @classmethod
    def _validate_platform(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Unsupported EBK platform: {value!r}. "
                f"Supported: {', '.join(sorted(_SUPPORTED_PLATFORMS))}"
            )
        return normalized

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _SUPPORTED_STATUSES:
            raise ValueError(
                f"Unsupported EBK snapshot status: {value!r}. "
                f"Supported: {', '.join(sorted(_SUPPORTED_STATUSES))}"
            )
        return normalized


def serialize_unified_ebk_snapshot(snapshot: UnifiedEbkSnapshot) -> dict[str, Any]:
    """Serialize a unified snapshot and strip secrets from operator-facing fields."""
    payload = snapshot.model_dump(mode="json")
    read_at = payload.get("read_at")
    if isinstance(snapshot.read_at, datetime) and isinstance(read_at, str):
        payload["read_at"] = snapshot.read_at.isoformat()
    sanitized = sanitize_operator_payload(payload)
    if not isinstance(sanitized, dict):
        raise TypeError("Unified EBK snapshot serialization must return a dict.")
    return sanitized
