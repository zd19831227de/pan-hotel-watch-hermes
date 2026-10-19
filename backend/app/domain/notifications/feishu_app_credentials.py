from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.settings import get_settings


@dataclass(frozen=True)
class FeishuAppCredentials:
    profile: str
    app_id: str
    app_secret: str

    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def public_payload(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "app_id_configured": bool(self.app_id),
            "app_secret_configured": bool(self.app_secret),
        }


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_profile(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text or "default"


def _credentials_from_mapping(profile: str) -> FeishuAppCredentials | None:
    settings = get_settings()
    mapping = _parse_json_object(settings.feishu_app_credentials_json)
    raw = mapping.get(profile)
    if not isinstance(raw, dict):
        return None
    app_id = str(raw.get("app_id") or raw.get("FEISHU_APP_ID") or "").strip()
    app_secret = str(raw.get("app_secret") or raw.get("FEISHU_APP_SECRET") or "").strip()
    if not app_id and not app_secret:
        return None
    return FeishuAppCredentials(profile=profile, app_id=app_id, app_secret=app_secret)


def resolve_feishu_app_profile(
    *,
    chat_id: str | None = None,
    app_profile: str | None = None,
) -> str:
    explicit = _normalize_profile(app_profile)
    if explicit != "default":
        return explicit
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return "default"
    settings = get_settings()
    chat_map = _parse_json_object(settings.feishu_chat_app_map_json)
    return _normalize_profile(chat_map.get(normalized_chat_id))


def resolve_feishu_app_credentials(
    *,
    chat_id: str | None = None,
    app_profile: str | None = None,
) -> FeishuAppCredentials:
    settings = get_settings()
    normalized_chat_id = str(chat_id or "").strip()
    explicit_profile = _normalize_profile(app_profile)
    chat_map = _parse_json_object(settings.feishu_chat_app_map_json)
    mapped_profile_raw = chat_map.get(normalized_chat_id) if normalized_chat_id else None
    chat_has_explicit_profile = bool(str(mapped_profile_raw or "").strip())
    profile = resolve_feishu_app_profile(chat_id=chat_id, app_profile=app_profile)
    if (
        settings.feishu_strict_tenant_routing
        and normalized_chat_id
        and explicit_profile == "default"
        and not chat_has_explicit_profile
    ):
        return FeishuAppCredentials(profile="unmapped", app_id="", app_secret="")
    if profile != "default":
        mapped = _credentials_from_mapping(profile)
        if mapped is not None:
            return mapped
        if settings.feishu_strict_tenant_routing:
            return FeishuAppCredentials(profile=profile, app_id="", app_secret="")
    default_mapped = _credentials_from_mapping("default")
    if default_mapped is not None:
        return default_mapped
    return FeishuAppCredentials(
        profile="default",
        app_id=str(settings.feishu_app_id or "").strip(),
        app_secret=str(settings.feishu_app_secret or "").strip(),
    )
