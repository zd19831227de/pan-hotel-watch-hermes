from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.core.settings import get_settings
from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service
from app.domain.browser.sessions import (
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.ctrip_ebooking_watch.repository import (
    DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
    get_data_center_watch_group_runtime,
)


class RealEnvSmokeCheckOptions(BaseModel):
    actor_id: str = "smoke-check"
    platform_account_id: str | None = None
    browser_session_id: str | None = None
    data_center_watch_group_key: str = DEFAULT_DATA_CENTER_WATCH_GROUP_KEY
    run_browser_login_state_check: bool = False
    headless: bool = False


async def run_real_env_smoke_check(options: RealEnvSmokeCheckOptions) -> dict[str, Any]:
    settings = get_settings()
    platform_account_id = _resolve_platform_account_id(options.platform_account_id)
    browser = _browser_session_check(
        platform_account_id=platform_account_id,
        requested_browser_session_id=options.browser_session_id,
    )
    data_center = await _data_center_check(
        watch_group_key=options.data_center_watch_group_key,
    )
    login_state: dict[str, Any] | None = None
    if options.run_browser_login_state_check:
        login_state = await _browser_login_state_check(
            platform_account_id=platform_account_id,
            browser_session_id=browser.get("browser_session_id"),
            headless=options.headless,
        )

    modules = {
        "browser_session": browser,
        "ctrip_ebooking_data_center": data_center,
    }
    if login_state is not None:
        modules["ctrip_ebooking_login_state"] = login_state

    return {
        "status": _combined_status(modules),
        "summary": _summary(modules),
        "options": {
            "run_browser_login_state_check": options.run_browser_login_state_check,
            "headless": options.headless,
        },
        "modules": modules,
        "checks": {
            "database_url_configured": bool(settings.database_url),
            "browser_state_dir_configured": bool(settings.browser_state_dir),
            "ctrip_ebooking_urls_configured": bool(
                settings.ctrip_ebooking_login_url and settings.ctrip_ebooking_home_url
            ),
        },
        "evidence": {
            "read_only": True,
            "browser_read_invoked": options.run_browser_login_state_check,
            "webhook_called": False,
            "ota_write_invoked": False,
            "secrets_returned": False,
            "profile_path_returned": False,
        },
    }


def _resolve_platform_account_id(requested: str | None) -> str:
    settings = get_settings()
    return (
        requested
        or settings.ctrip_ebooking_default_platform_account_id
        or settings.feishu_default_platform_account_id
        or "__unconfigured__"
    )


def _browser_session_check(
    *,
    platform_account_id: str,
    requested_browser_session_id: str | None,
) -> dict[str, Any]:
    browser_session_id = requested_browser_session_id or get_browser_session_id_for_platform_account(
        platform_account_id
    )
    if not browser_session_id:
        return {
            "status": "config_required",
            "summary": "No browser session is bound to the platform account.",
            "platform_account_id": platform_account_id,
            "browser_session_id": None,
            "evidence": {"session_bound": False, "human_required": True},
        }
    try:
        session = get_browser_session_store().get(browser_session_id)
    except KeyError:
        return {
            "status": "human_required",
            "summary": "Bound browser session is not registered in the session store.",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "evidence": {"session_bound": True, "session_registered": False},
        }
    human_required = session.login_state in {"human_required", "logged_out"}
    needs_check = session.login_state == "needs_check" or (
        session.human_verification_status in {"required", "requested"} and not human_required
    )
    status = "human_required" if human_required else "needs_review" if needs_check else "ready"
    return {
        "status": status,
        "summary": (
            "Browser session requires human takeover."
            if human_required
            else "Browser session login state needs a real read confirmation."
            if needs_check
            else "Browser session is ready for read-only checks."
        ),
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "login_state": session.login_state,
        "human_verification_status": session.human_verification_status,
        "risk_flags": session.risk_flags,
        "manual_takeover_url": session.manual_takeover_url,
        "evidence": {
            "session_bound": True,
            "session_registered": True,
            "human_required": human_required,
            "needs_check": needs_check,
            "automation_bypass": False,
            "profile_path_returned": False,
        },
    }


async def _data_center_check(*, watch_group_key: str) -> dict[str, Any]:
    group = await get_data_center_watch_group_runtime(watch_group_key=watch_group_key)
    if group is None:
        return {
            "status": "config_required",
            "summary": "Ctrip EBK data-center watch group is not configured.",
            "watch_group_key": watch_group_key,
            "evidence": {"configured": False},
        }
    enabled_dataset_ids = [item.dataset_id for item in group.datasets if item.enabled]
    configured = group.enabled and group.platform_account_id != "__unconfigured__" and bool(
        enabled_dataset_ids
    )
    return {
        "status": "ready" if configured else "config_required",
        "summary": (
            "Ctrip EBK data-center watch is configured."
            if configured
            else "Ctrip EBK data-center watch requires platform account and enabled datasets."
        ),
        "watch_group_key": watch_group_key,
        "platform_account_id": group.platform_account_id,
        "browser_session_id": group.browser_session_id,
        "enabled_dataset_count": len(enabled_dataset_ids),
        "webhook_configured": bool(group.feishu_webhook_url),
        "evidence": {"configured": configured, "read_only": True},
    }


async def _browser_login_state_check(
    *,
    platform_account_id: str,
    browser_session_id: Any,
    headless: bool,
) -> dict[str, Any]:
    if not isinstance(browser_session_id, str) or not browser_session_id:
        return {
            "status": "config_required",
            "summary": "No browser session is available for real browser smoke check.",
            "evidence": {"browser_opened": False},
        }
    service = get_ctrip_ebooking_service()
    try:
        result = await service.open_login(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            account_phone=None,
            account_username=None,
            account_password=None,
            auto_submit_login=False,
            force_new_login=False,
            headless=headless,
            login_context="ebk",
        )
    except Exception as exc:
        return {
            "status": "failed",
            "summary": f"Browser login-state smoke check failed: {type(exc).__name__}: {str(exc)[:240]}",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "evidence": {
                "browser_opened": False,
                "read_only": True,
                "error_type": type(exc).__name__,
                "profile_path_returned": False,
            },
        }
    finally:
        await service.close()
    payload = result.model_dump(mode="json")
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    page_state = evidence.get("page_state")
    status = "ready" if payload.get("status") == "success" else "human_required"
    return {
        "status": status,
        "summary": (
            "Browser opened and Ctrip EBK login state was reused."
            if status == "ready"
            else "Browser opened, but Ctrip EBK requires login, CAPTCHA, SMS, QR, or risk-control handling."
        ),
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "page_state": page_state,
        "next_action": payload.get("next_action"),
        "evidence": {
            "browser_opened": True,
            "login_state_reused": status == "ready",
            "human_required": status != "ready",
            "current_url": evidence.get("current_url"),
            "page_state": page_state,
            "automation_bypass": False,
            "read_only": True,
            "profile_path_returned": False,
        },
    }


def _combined_status(modules: dict[str, dict[str, Any]]) -> str:
    statuses = {str(module.get("status")) for module in modules.values()}
    if "failed" in statuses:
        return "failed"
    if "human_required" in statuses:
        return "human_required"
    if "config_required" in statuses:
        return "config_required"
    if "needs_review" in statuses:
        return "needs_review"
    return "success"


def _summary(modules: dict[str, dict[str, Any]]) -> str:
    status = _combined_status(modules)
    if status == "success":
        return "Smoke check passed. Read-only runtime surfaces are ready."
    if status == "human_required":
        return "Smoke check found a browser state that requires human login or verification."
    if status == "config_required":
        return "Smoke check found missing runtime configuration."
    if status == "needs_review":
        return "Smoke check needs a real browser read confirmation."
    return "Smoke check failed; inspect module details."
