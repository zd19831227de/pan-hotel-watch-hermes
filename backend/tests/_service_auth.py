from __future__ import annotations

from app.core.settings import get_settings


def service_token_headers() -> dict[str, str]:
    token = str(get_settings().watch_api_shared_secret or "").strip()
    if not token:
        return {}
    return {"X-Pan-Hotel-Service-Token": token}
