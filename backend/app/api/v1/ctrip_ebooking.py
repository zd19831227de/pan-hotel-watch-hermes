from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.domain.browser.ctrip_ebooking import (
    CtripBrowserProfileLockedError,
    get_ctrip_ebooking_service,
)
from app.domain.browser.sessions import get_browser_session_id_for_platform_account
from app.domain.operator_payload import sanitize_operator_payload

router = APIRouter(prefix="/ctrip-ebooking", tags=["ctrip-ebooking"])


class CtripEbookingLoginRequest(BaseModel):
    actor_id: str = "operator"
    login_context: Literal["ebk", "competitor_price"] = "ebk"
    account_phone: str | None = None
    account_username: str | None = None
    account_password: SecretStr | None = Field(default=None, repr=False)
    auto_submit_login: bool | None = None
    reset_login_state: bool = False
    browser_session_id: str | None = None
    headless: bool = False


class CtripEbookingReadRequest(BaseModel):
    actor_id: str = "operator"
    hotel_id: str | None = None
    browser_session_id: str | None = None
    push_to_feishu: bool = False


class CtripEbookingDatasetReadRequest(BaseModel):
    actor_id: str = "operator"
    dataset_id: str
    filters: dict[str, Any] = Field(default_factory=dict)
    browser_session_id: str | None = None
    push_to_feishu: bool = False


class CtripEbookingMonitorReadRequest(BaseModel):
    actor_id: str = "operator"
    filters: dict[str, Any] = Field(default_factory=dict)
    browser_session_id: str | None = None
    push_to_feishu: bool = False


class CtripCompetitorPriceReadRequest(BaseModel):
    actor_id: str = "operator"
    own_hotel_url: str
    competitor_urls: list[str] = Field(default_factory=list)
    check_dates: list[str] | None = None
    browser_session_id: str | None = None
    push_to_feishu: bool = False


@router.post("/sessions/{platform_account_id}/open-login")
async def open_ctrip_ebooking_login(
    platform_account_id: str,
    request: CtripEbookingLoginRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        result = await get_ctrip_ebooking_service().open_login(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            account_phone=request.account_phone,
            account_username=request.account_username,
            account_password=(
                request.account_password.get_secret_value() if request.account_password else None
            ),
            auto_submit_login=request.auto_submit_login,
            force_new_login=request.reset_login_state,
            headless=request.headless,
            login_context=request.login_context,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CtripBrowserProfileLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _operator_result(result)


@router.post("/sessions/{platform_account_id}/read-overview")
async def read_ctrip_ebooking_overview(
    platform_account_id: str,
    request: CtripEbookingReadRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        result = await get_ctrip_ebooking_service().read_overview_snapshot(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            hotel_id=request.hotel_id,
            actor_id=request.actor_id,
            push_to_feishu=request.push_to_feishu,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CtripBrowserProfileLockedError as exc:
        _raise_profile_locked(exc)
    return _operator_result(result)


@router.post("/sessions/{platform_account_id}/read-dataset")
async def read_ctrip_ebooking_dataset(
    platform_account_id: str,
    request: CtripEbookingDatasetReadRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        if request.dataset_id == "pyramid_bid":
            # 2026-09-22：金字塔推广计划（计划名=投放时段）并入按需单数据集
            # 现读，复用盯盘 pyramid_bid 的只读抓取链路（不是 dataReport 页，
            # 不能走 _resolve_dataset_definition）。promotions 冗余落 data——
            # Hermes read_ebk_datasets 只透传 data 字段。
            pyramid_result = dict(
                await get_ctrip_ebooking_service().read_pyramid_promotions(
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    actor_id=request.actor_id,
                )
            )
            pyramid_result["data"] = {
                **(pyramid_result.get("data") or {}),
                "promotions": list(pyramid_result.get("promotions") or []),
            }
            return sanitize_operator_payload(pyramid_result)
        result = await get_ctrip_ebooking_service().read_dataset(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            dataset_id=request.dataset_id,
            filters=request.filters,
            actor_id=request.actor_id,
            push_to_feishu=request.push_to_feishu,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CtripBrowserProfileLockedError as exc:
        _raise_profile_locked(exc)
    return _operator_result(result)


@router.post("/sessions/{platform_account_id}/read-monitor")
async def read_ctrip_ebooking_monitor(
    platform_account_id: str,
    request: CtripEbookingMonitorReadRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        result = await get_ctrip_ebooking_service().read_monitor(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            filters=request.filters,
            actor_id=request.actor_id,
            push_to_feishu=request.push_to_feishu,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CtripBrowserProfileLockedError as exc:
        _raise_profile_locked(exc)
    return _operator_result(result)


@router.post("/sessions/{platform_account_id}/read-comment-monitor")
async def read_ctrip_ebooking_comment_monitor(
    platform_account_id: str,
    request: CtripEbookingMonitorReadRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        result = await get_ctrip_ebooking_service().read_comment_monitor(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            filters=request.filters,
            actor_id=request.actor_id,
            push_to_feishu=request.push_to_feishu,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CtripBrowserProfileLockedError as exc:
        _raise_profile_locked(exc)
    return _operator_result(result)


@router.post("/sessions/{platform_account_id}/read-competitor-prices")
async def read_ctrip_competitor_prices(
    platform_account_id: str,
    request: CtripCompetitorPriceReadRequest,
) -> dict[str, Any]:
    browser_session_id = _resolve_browser_session_id(platform_account_id, request.browser_session_id)
    try:
        result = await get_ctrip_ebooking_service().read_competitor_prices(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            own_hotel_url=request.own_hotel_url,
            competitor_urls=request.competitor_urls,
            check_dates=request.check_dates,
            actor_id=request.actor_id,
            push_to_feishu=request.push_to_feishu,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CtripBrowserProfileLockedError as exc:
        _raise_profile_locked(exc)
    return _operator_result(result)


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


def _operator_result(result: Any) -> dict[str, Any]:
    return sanitize_operator_payload(result.model_dump(mode="json"))


def _raise_profile_locked(exc: CtripBrowserProfileLockedError) -> None:
    raise HTTPException(status_code=409, detail=str(exc)) from exc
