from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.security import require_supervisor_api_key
from app.domain.hotel_analytics.service import (
    HotelAnalyticsPermissionError,
    HotelAnalyticsScopeError,
    get_hotel_analytics_overview,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _analytics_http_exception(exc: ValueError) -> HTTPException:
    if isinstance(exc, HotelAnalyticsPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, HotelAnalyticsScopeError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get(
    "/hotel-overview",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def get_hotel_overview(
    chat_id: Annotated[str, Query(min_length=1)],
    scope: Annotated[str | None, Query()] = None,
    profile_keys: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    """Cross-hotel analytics overview, scoped by the caller's Feishu role.

    The route is supervisor-key-gated (the trusted Hermes backend calls it on
    behalf of a Feishu user). The user's ``chat_id`` is then resolved to a role:
    supervisor may query ``own``/``all``/a selected set, every other admitted
    member is confined to ``own``. Permission or scope problems return a clear
    error rather than empty data (fail-closed).
    """

    try:
        return await get_hotel_analytics_overview(
            caller_chat_id=chat_id,
            scope=scope,
            requested_profile_keys=profile_keys,
        )
    except ValueError as exc:
        raise _analytics_http_exception(exc) from exc
