from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.domain.browser.meituan_ebooking import (
    MeituanBrowserProfileLockedError,
    get_meituan_ebooking_service,
)
from app.domain.browser.sessions import (
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.operator_payload import sanitize_operator_payload
from app.domain.page_change_alerts.service import observe_meituan_read_payload

router = APIRouter(prefix="/meituan-ebooking", tags=["meituan-ebooking"])


class MeituanEbookingLoginRequest(BaseModel):
    actor_id: str = "operator"
    browser_session_id: str | None = None
    headless: bool = False


class MeituanEbookingDatasetReadRequest(MeituanEbookingLoginRequest):
    dataset: str


@router.post("/sessions/{platform_account_id}/open-login")
async def open_meituan_ebooking_login(
    platform_account_id: str,
    request: MeituanEbookingLoginRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if session.platform != "meituan" or session.intended_use != "ebk":
        raise HTTPException(
            status_code=422,
            detail="Meituan EBK open-login requires a Meituan EBK browser session.",
        )
    if session.platform_account_id != platform_account_id:
        raise HTTPException(
            status_code=422,
            detail="browser_session_id does not match the requested Meituan platform_account_id.",
        )

    try:
        result = await get_meituan_ebooking_service().open_login(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=request.actor_id,
            headless=request.headless,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeituanBrowserProfileLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sanitize_operator_payload(result)


@router.post("/sessions/{platform_account_id}/check-login-state")
async def check_meituan_ebooking_login_state(
    platform_account_id: str,
    request: MeituanEbookingLoginRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if session.platform != "meituan" or session.intended_use != "ebk":
        raise HTTPException(
            status_code=422,
            detail="Meituan EBK check-login-state requires a Meituan EBK browser session.",
        )
    if session.platform_account_id != platform_account_id:
        raise HTTPException(
            status_code=422,
            detail="browser_session_id does not match the requested Meituan platform_account_id.",
        )

    try:
        result = await get_meituan_ebooking_service().check_login_state(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=request.actor_id,
            headless=request.headless,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeituanBrowserProfileLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sanitize_operator_payload(result)


@router.post("/sessions/{platform_account_id}/read-data-center")
async def read_meituan_ebooking_data_center(
    platform_account_id: str,
    request: MeituanEbookingLoginRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if session.platform != "meituan" or session.intended_use != "ebk":
        raise HTTPException(
            status_code=422,
            detail="Meituan EBK data-center read requires a Meituan EBK browser session.",
        )
    if session.platform_account_id != platform_account_id:
        raise HTTPException(
            status_code=422,
            detail="browser_session_id does not match the requested Meituan platform_account_id.",
        )

    try:
        result = await get_meituan_ebooking_service().read_data_center(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=request.actor_id,
            headless=request.headless,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeituanBrowserProfileLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # V7 Step 1 — 采集后校验层（selector 健康/结构指纹），失败不影响读取结果
    await observe_meituan_read_payload("data_center_home", result)
    return sanitize_operator_payload(result)


@router.post("/sessions/{platform_account_id}/read-dataset")
async def read_meituan_ebooking_dataset(
    platform_account_id: str,
    request: MeituanEbookingDatasetReadRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if session.platform != "meituan" or session.intended_use != "ebk":
        raise HTTPException(
            status_code=422,
            detail="Meituan EBK dataset read requires a Meituan EBK browser session.",
        )
    if session.platform_account_id != platform_account_id:
        raise HTTPException(
            status_code=422,
            detail="browser_session_id does not match the requested Meituan platform_account_id.",
        )

    try:
        result = await get_meituan_ebooking_service().read_dataset(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=request.actor_id,
            headless=request.headless,
            dataset=request.dataset,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeituanBrowserProfileLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # V7 Step 1 — 采集后校验层（selector 健康/结构指纹），失败不影响读取结果
    await observe_meituan_read_payload(f"dataset:{request.dataset}", result)
    return sanitize_operator_payload(result)


def _resolve_browser_session_id(
    platform_account_id: str,
    requested_browser_session_id: str | None,
) -> str:
    if requested_browser_session_id:
        return requested_browser_session_id
    browser_session_id = get_browser_session_id_for_platform_account(platform_account_id)
    if not browser_session_id:
        raise HTTPException(
            status_code=422,
            detail="No browser session is configured for this platform_account_id.",
        )
    return browser_session_id
