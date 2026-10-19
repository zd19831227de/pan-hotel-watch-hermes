from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.settings import get_settings
from app.domain.config.service import resolve_api_key_role


SERVICE_TOKEN_HEADER = "X-Pan-Hotel-Service-Token"
API_KEY_HEADER = "X-Pan-Hotel-API-Key"


def _api_auth_required() -> bool:
    settings = get_settings()
    environment = str(settings.environment or "").strip().lower()
    return bool(
        settings.pan_hotel_enforce_api_auth
        or environment not in {"", "local", "development", "test"}
    )


async def require_supervisor_api_key(
    api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    if await resolve_api_key_role(api_key) != "supervisor":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要有效的 supervisor API 凭据。",
        )


async def require_operator_api_key(
    api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
    service_token: Annotated[str | None, Header(alias=SERVICE_TOKEN_HEADER)] = None,
) -> None:
    if not _api_auth_required():
        return
    expected_service_token = str(get_settings().watch_api_shared_secret or "").strip()
    if (
        expected_service_token
        and service_token
        and hmac.compare_digest(service_token.strip(), expected_service_token)
    ):
        return
    role = await resolve_api_key_role(api_key)
    if role not in {"operator", "supervisor"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要有效的 operator API 凭据。",
        )


async def require_watch_service_token(
    token: Annotated[str | None, Header(alias=SERVICE_TOKEN_HEADER)] = None,
) -> None:
    expected = str(get_settings().watch_api_shared_secret or "").strip()
    if not expected:
        if _api_auth_required():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="后端未配置 Hermes 服务令牌。",
            )
        return
    if not token or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Hermes 服务令牌无效。",
        )
