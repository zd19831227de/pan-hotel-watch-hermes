from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.api.security import require_supervisor_api_key
from app.core.settings import get_settings
from app.domain.browser.cache_cleanup import (
    audit_browser_state,
    audit_playwright_screenshots,
    cleanup_browser_state,
    cleanup_playwright_screenshots,
)
from app.domain.browser.sessions import (
    BrowserSessionCreate,
    BrowserSessionPatch,
    get_browser_session_store,
    public_browser_session_payload,
)
from app.domain.ctrip_ebooking_watch.repository import (
    clear_data_center_watch_bindings_for_browser_session,
    migrate_data_center_watch_bindings_for_browser_session,
    restore_data_center_watch_bindings,
    snapshot_data_center_watch_bindings_for_browser_session,
)
from app.domain.competitor_prices.repository import (
    clear_competitor_watch_bindings_for_browser_session,
    get_competitor_browser_pool_usage_by_browser_session,
    migrate_competitor_watch_bindings_for_browser_session,
    restore_competitor_watch_bindings,
    snapshot_competitor_watch_bindings_for_browser_session,
    validate_browser_session_intended_use_change,
)
from app.domain.fliggy_ebooking_watch.repository import (
    clear_fliggy_watch_bindings_for_browser_session,
    migrate_fliggy_watch_bindings_for_browser_session,
    restore_fliggy_watch_bindings,
    snapshot_fliggy_watch_bindings_for_browser_session,
)
from app.domain.meituan_ebooking_watch.repository import (
    clear_meituan_watch_bindings_for_browser_session,
    migrate_meituan_watch_bindings_for_browser_session,
    restore_meituan_watch_bindings,
    snapshot_meituan_watch_bindings_for_browser_session,
)

router = APIRouter(prefix="/browser-sessions", tags=["browser-sessions"])


class BrowserSessionActionRequest(BaseModel):
    actor_id: str = "operator"


class BrowserSessionAbnormalRequest(BrowserSessionActionRequest):
    abnormal_type: str = "captcha"
    detail: str | None = None


@router.get("")
async def list_browser_sessions() -> dict[str, list[dict[str, Any]]]:
    store = get_browser_session_store()
    try:
        pool_usage_by_session = await get_competitor_browser_pool_usage_by_browser_session()
    except Exception:
        pool_usage_by_session = {}
    sessions: list[dict[str, Any]] = []
    for session in store.list():
        payload = public_browser_session_payload(session)
        pool_usage = pool_usage_by_session.get(session.browser_session_id, [])
        if pool_usage:
            payload["competitor_pool_usage"] = pool_usage
        sessions.append(payload)
    return {"sessions": sessions}


async def _validate_shop_binding(
    *,
    intended_use: str,
    shop_key: str | None,
    shop_key_provided: bool,
    intended_use_provided: bool = True,
) -> None:
    """店铺归属校验（重构 P2）：语义规则由 store 兜底，这里补店铺存在性检查。"""
    cleaned = str(shop_key or "").strip() or None
    if intended_use == "ebk":
        if cleaned is None and (shop_key_provided or intended_use_provided):
            raise HTTPException(
                status_code=422,
                detail="EBK 浏览器账号必须归属一个店铺（shop_key），请先选择或创建店铺。",
            )
    elif cleaned is not None and shop_key_provided:
        raise HTTPException(
            status_code=422,
            detail="shop_key 仅允许配置在 EBK 浏览器会话上。",
        )
    if cleaned:
        from app.domain.shops.service import ShopNotFound
        from app.domain.shops import service as shops_service

        try:
            await shops_service.get_shop(cleaned)
        except ShopNotFound:
            raise HTTPException(
                status_code=422,
                detail=f"店铺不存在：{cleaned}，请先在店铺树创建店铺。",
            ) from None


@router.post("")
async def create_browser_session(request: BrowserSessionCreate) -> dict[str, Any]:
    await _validate_shop_binding(
        intended_use=request.intended_use,
        shop_key=request.shop_key,
        shop_key_provided=True,
    )
    store = get_browser_session_store()
    try:
        session = store.create(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return public_browser_session_payload(session)


# --- Browser cache audit & cleanup (V4-Step4) -------------------------------
# Declared before any /{browser_session_id} route so the static paths are not
# swallowed by the path-param route. Both endpoints require the supervisor API
# key: cache cleanup deletes files (only audit-confirmed cleanable cache) and
# must not be triggerable by operator/watch-service callers. The audit drives
# the cleanup — the endpoint never deletes by a hard-coded path.


async def _require_supervisor_api_key(
    api_key: Annotated[
        str | None,
        Header(alias="X-Pan-Hotel-API-Key"),
    ] = None,
) -> None:
    await require_supervisor_api_key(api_key)


def _browser_state_root() -> Path:
    return Path(get_settings().browser_state_dir)


class BrowserCacheCleanupRequest(BaseModel):
    # Minimum age (days) of cache files to remove. null/0 => remove all
    # audit-cleanable cache. The scheduled task passes 60; the manual "clean
    # now" button passes 0/null for an aggressive one-shot clear.
    min_age_days: int | None = 0
    dry_run: bool = False


@router.get(
    "/cache-audit",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def get_browser_cache_audit() -> dict[str, Any]:
    """Return the per-profile cache audit (sizes + cleanable fraction).

    Only audit-confirmed cleanable_cache is reported as cleanable; login state,
    non-cache model/learning data and unknown entries are preserved.
    """
    audit = audit_browser_state(_browser_state_root())
    platforms: list[dict[str, Any]] = []
    for plat_name, plat in audit.platforms.items():
        profiles = []
        for prof in plat.profiles:
            profiles.append(
                {
                    "profile_id": prof.profile_id,
                    "total_bytes": prof.total_bytes,
                    "cleanable_bytes": prof.cleanable_bytes,
                    "cleanable_fraction": round(prof.cleanable_fraction, 4),
                    "login_state_bytes": prof.login_state_bytes,
                    "non_cache_bytes": prof.non_cache_bytes,
                    "unknown_bytes": prof.unknown_bytes,
                }
            )
        platforms.append(
            {
                "platform": plat_name,
                "profile_count": len(plat.profiles),
                "total_bytes": plat.total_bytes,
                "cleanable_bytes": plat.cleanable_bytes,
                "profiles": profiles,
            }
        )
    return {
        "root": str(audit.root),
        "total_bytes": audit.total_bytes,
        "cleanable_bytes": audit.cleanable_bytes,
        "cleanable_fraction": (
            round(audit.cleanable_bytes / audit.total_bytes, 4) if audit.total_bytes else 0.0
        ),
        "platforms": platforms,
    }


@router.post(
    "/cleanup",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def cleanup_browser_cache(
    request: BrowserCacheCleanupRequest,
) -> dict[str, Any]:
    """Remove audit-confirmed browser cache. Supervisor only.

    Re-audits at execution time and deletes only entries classified
    ``cleanable_cache`` (HTTP/GPU/Service-Worker caches, metrics, crash dumps).
    Login state, non-cache model/learning data and unknown entries are never
    touched; root-level registry files (browser_sessions.json + lock) are out of
    scope. Holds the browser-state lock for the whole pass.
    """
    min_age = request.min_age_days if request.min_age_days and request.min_age_days > 0 else None
    result = cleanup_browser_state(
        _browser_state_root(),
        dry_run=request.dry_run,
        min_age_days=min_age,
    )
    return result


# --- Playwright screenshot audit & cleanup (V4-Step4 follow-up) -------------
# OTA reads snap a browser screenshot for audit/debugging; they pile up to
# multiple GB over weeks. retention_days is configurable (no code edit):
# manual callers pass it in the body; the scheduled worker reads
# PLAYWRIGHT_SCREENSHOT_RETENTION_DAYS from .env. Non-PNG files are never touched.


class ScreenshotCleanupRequest(BaseModel):
    # Remove PNG screenshots older than this many days. null => use the
    # configured default (PLAYWRIGHT_SCREENSHOT_RETENTION_DAYS). <=0 => all.
    retention_days: int | None = None
    dry_run: bool = False


def _playwright_artifact_root() -> Path:
    return Path(get_settings().playwright_artifact_dir)


@router.get(
    "/screenshot-audit",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def get_screenshot_audit(
    retention_days: int | None = None,
) -> dict[str, Any]:
    """Return screenshot inventory + how many are older than retention."""
    retention = (
        retention_days
        if retention_days is not None and retention_days >= 0
        else int(get_settings().playwright_screenshot_retention_days)
    )
    return audit_playwright_screenshots(_playwright_artifact_root(), retention)


@router.post(
    "/cleanup-screenshots",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def cleanup_screenshots(
    request: ScreenshotCleanupRequest,
) -> dict[str, Any]:
    """Remove OTA-read PNG screenshots older than retention_days. Supervisor only."""
    retention = (
        request.retention_days
        if request.retention_days is not None and request.retention_days >= 0
        else int(get_settings().playwright_screenshot_retention_days)
    )
    return cleanup_playwright_screenshots(
        _playwright_artifact_root(),
        retention,
        dry_run=request.dry_run,
    )


@router.get("/{browser_session_id}")
async def get_browser_session(browser_session_id: str) -> dict[str, Any]:
    store = get_browser_session_store()
    try:
        session = store.get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return public_browser_session_payload(session)


@router.patch("/{browser_session_id}")
async def update_browser_session(
    browser_session_id: str,
    request: BrowserSessionPatch,
) -> dict[str, Any]:
    store = get_browser_session_store()
    try:
        previous = store.get(browser_session_id).model_copy(deep=True)
        await _validate_shop_binding(
            intended_use=request.intended_use or previous.intended_use,
            shop_key=(
                request.shop_key
                if "shop_key" in request.model_fields_set
                else previous.shop_key
            ),
            shop_key_provided="shop_key" in request.model_fields_set,
            intended_use_provided=request.intended_use is not None,
        )
        await validate_browser_session_intended_use_change(
            browser_session_id=previous.browser_session_id,
            platform_account_id=previous.platform_account_id,
            current_platform=previous.platform,
            next_platform=request.platform,
            current_intended_use=previous.intended_use,
            next_intended_use=request.intended_use,
            pricing_feishu_chat_id_configured=bool(previous.pricing_feishu_chat_id),
        )
        session = store.update(browser_session_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if previous.platform_account_id != session.platform_account_id:
        snapshots: list[dict[str, Any]] = []
        competitor_snapshots: list[dict[str, Any]] = []
        meituan_snapshots: list[dict[str, Any]] = []
        fliggy_snapshots: list[dict[str, Any]] = []
        try:
            snapshots = await snapshot_data_center_watch_bindings_for_browser_session(
                platform_account_id=previous.platform_account_id,
                browser_session_id=previous.browser_session_id,
            )
            competitor_snapshots = await snapshot_competitor_watch_bindings_for_browser_session(
                platform_account_id=previous.platform_account_id,
                browser_session_id=previous.browser_session_id,
            )
            meituan_snapshots = await snapshot_meituan_watch_bindings_for_browser_session(
                platform_account_id=previous.platform_account_id,
                browser_session_id=previous.browser_session_id,
            )
            fliggy_snapshots = await snapshot_fliggy_watch_bindings_for_browser_session(
                platform_account_id=previous.platform_account_id,
                browser_session_id=previous.browser_session_id,
            )
            await migrate_data_center_watch_bindings_for_browser_session(
                old_platform_account_id=previous.platform_account_id,
                browser_session_id=previous.browser_session_id,
                new_platform_account_id=session.platform_account_id,
            )
            await migrate_competitor_watch_bindings_for_browser_session(
                old_platform_account_id=previous.platform_account_id,
                browser_session_id=previous.browser_session_id,
                new_platform_account_id=session.platform_account_id,
            )
            await migrate_meituan_watch_bindings_for_browser_session(
                old_platform_account_id=previous.platform_account_id,
                browser_session_id=previous.browser_session_id,
                new_platform_account_id=session.platform_account_id,
            )
            await migrate_fliggy_watch_bindings_for_browser_session(
                old_platform_account_id=previous.platform_account_id,
                browser_session_id=previous.browser_session_id,
                new_platform_account_id=session.platform_account_id,
            )
        except Exception as exc:
            with suppress(Exception):
                await restore_data_center_watch_bindings(snapshots)
            with suppress(Exception):
                await restore_competitor_watch_bindings(competitor_snapshots)
            with suppress(Exception):
                await restore_meituan_watch_bindings(meituan_snapshots)
            with suppress(Exception):
                await restore_fliggy_watch_bindings(fliggy_snapshots)
            with suppress(Exception):
                store.restore(previous)
            raise HTTPException(
                status_code=500,
                detail="Failed to migrate watch bindings for browser account.",
            ) from exc
    return public_browser_session_payload(session)


@router.delete("/{browser_session_id}")
async def delete_browser_session(browser_session_id: str) -> dict[str, Any]:
    store = get_browser_session_store()
    try:
        session = store.get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    snapshots = await snapshot_data_center_watch_bindings_for_browser_session(
        platform_account_id=session.platform_account_id,
        browser_session_id=session.browser_session_id,
    )
    competitor_snapshots = await snapshot_competitor_watch_bindings_for_browser_session(
        platform_account_id=session.platform_account_id,
        browser_session_id=session.browser_session_id,
    )
    meituan_snapshots = await snapshot_meituan_watch_bindings_for_browser_session(
        platform_account_id=session.platform_account_id,
        browser_session_id=session.browser_session_id,
    )
    fliggy_snapshots = await snapshot_fliggy_watch_bindings_for_browser_session(
        platform_account_id=session.platform_account_id,
        browser_session_id=session.browser_session_id,
    )
    try:
        await clear_data_center_watch_bindings_for_browser_session(
            platform_account_id=session.platform_account_id,
            browser_session_id=session.browser_session_id,
        )
        await clear_competitor_watch_bindings_for_browser_session(
            platform_account_id=session.platform_account_id,
            browser_session_id=session.browser_session_id,
        )
        await clear_meituan_watch_bindings_for_browser_session(
            platform_account_id=session.platform_account_id,
            browser_session_id=session.browser_session_id,
        )
        await clear_fliggy_watch_bindings_for_browser_session(
            platform_account_id=session.platform_account_id,
            browser_session_id=session.browser_session_id,
        )
        removed = store.delete(browser_session_id)
    except Exception as exc:
        with suppress(Exception):
            await restore_data_center_watch_bindings(snapshots)
        with suppress(Exception):
            await restore_competitor_watch_bindings(competitor_snapshots)
        with suppress(Exception):
            await restore_meituan_watch_bindings(meituan_snapshots)
        with suppress(Exception):
            await restore_fliggy_watch_bindings(fliggy_snapshots)
        raise HTTPException(
            status_code=500,
            detail="Failed to clear watch bindings for deleted browser account.",
        ) from exc
    return public_browser_session_payload(removed)


@router.post("/{browser_session_id}/request-verification")
async def request_human_verification(
    browser_session_id: str,
    request: BrowserSessionActionRequest,
) -> dict[str, Any]:
    store = get_browser_session_store()
    try:
        session = store.request_verification(browser_session_id, request.actor_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return public_browser_session_payload(session)


@router.post("/{browser_session_id}/report-abnormal")
async def report_browser_session_abnormal(
    browser_session_id: str,
    request: BrowserSessionAbnormalRequest,
) -> dict[str, Any]:
    store = get_browser_session_store()
    try:
        session = store.report_abnormal(
            browser_session_id,
            request.actor_id,
            request.abnormal_type,
            request.detail,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return public_browser_session_payload(session)


@router.post("/{browser_session_id}/takeover")
async def takeover_browser_session(
    browser_session_id: str,
    request: BrowserSessionActionRequest,
) -> dict[str, Any]:
    store = get_browser_session_store()
    try:
        session = store.takeover(browser_session_id, request.actor_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return public_browser_session_payload(session)


@router.post("/{browser_session_id}/logout")
async def logout_browser_session(
    browser_session_id: str,
    request: BrowserSessionActionRequest,
) -> dict[str, Any]:
    store = get_browser_session_store()
    try:
        session = store.logout(browser_session_id, request.actor_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return public_browser_session_payload(session)
