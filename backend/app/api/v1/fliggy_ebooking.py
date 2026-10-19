from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.domain.browser.fliggy_ebooking import (
    FliggyBrowserProfileLockedError,
    get_fliggy_ebooking_service,
)
from app.domain.browser.sessions import (
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.operator_payload import sanitize_operator_payload
from app.domain.page_change_alerts.service import observe_fliggy_read_payload

router = APIRouter(prefix="/fliggy-ebooking", tags=["fliggy-ebooking"])


class FliggyEbookingLoginRequest(BaseModel):
    actor_id: str = "operator"
    browser_session_id: str | None = None
    headless: bool = False


class FliggyEbookingDatasetReadRequest(FliggyEbookingLoginRequest):
    dataset: str


@router.post("/sessions/{platform_account_id}/open-login")
async def open_fliggy_ebooking_login(
    platform_account_id: str,
    request: FliggyEbookingLoginRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if session.platform != "fliggy" or session.intended_use != "ebk":
        raise HTTPException(
            status_code=422,
            detail="Fliggy EBK open-login requires a Fliggy EBK browser session.",
        )
    if session.platform_account_id != platform_account_id:
        raise HTTPException(
            status_code=422,
            detail="browser_session_id does not match the requested Fliggy platform_account_id.",
        )

    try:
        result = await get_fliggy_ebooking_service().open_login(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=request.actor_id,
            headless=request.headless,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FliggyBrowserProfileLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sanitize_operator_payload(result)


@router.post("/sessions/{platform_account_id}/check-login-state")
async def check_fliggy_ebooking_login_state(
    platform_account_id: str,
    request: FliggyEbookingLoginRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if session.platform != "fliggy" or session.intended_use != "ebk":
        raise HTTPException(
            status_code=422,
            detail="Fliggy EBK check-login-state requires a Fliggy EBK browser session.",
        )
    if session.platform_account_id != platform_account_id:
        raise HTTPException(
            status_code=422,
            detail="browser_session_id does not match the requested Fliggy platform_account_id.",
        )

    try:
        result = await get_fliggy_ebooking_service().check_login_state(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=request.actor_id,
            headless=request.headless,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FliggyBrowserProfileLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sanitize_operator_payload(result)


@router.post("/sessions/{platform_account_id}/read-dataset")
async def read_fliggy_ebooking_dataset(
    platform_account_id: str,
    request: FliggyEbookingDatasetReadRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if session.platform != "fliggy" or session.intended_use != "ebk":
        raise HTTPException(
            status_code=422,
            detail="Fliggy EBK dataset read requires a Fliggy EBK browser session.",
        )
    if session.platform_account_id != platform_account_id:
        raise HTTPException(
            status_code=422,
            detail="browser_session_id does not match the requested Fliggy platform_account_id.",
        )

    try:
        result = await get_fliggy_ebooking_service().read_dataset(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=request.actor_id,
            headless=request.headless,
            dataset=request.dataset,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FliggyBrowserProfileLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # V7 Step 1 — 采集后校验层（selector 健康/结构指纹），失败不影响读取结果
    await observe_fliggy_read_payload(f"dataset:{request.dataset}", result)
    return sanitize_operator_payload(result)


def _resolve_browser_session_id(platform_account_id: str, browser_session_id: str | None) -> str:
    if browser_session_id and browser_session_id.strip():
        return browser_session_id.strip()
    resolved = get_browser_session_id_for_platform_account(platform_account_id)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"No browser session found for platform_account_id={platform_account_id}",
        )
    return resolved
