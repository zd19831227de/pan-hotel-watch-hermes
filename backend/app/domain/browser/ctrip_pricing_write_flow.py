from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.settings import get_settings
from app.domain.browser.ctrip_ebooking_results import CtripPricingPageWritePreviewResult
from app.domain.browser.ctrip_ebooking_session_helpers import (
    _pricing_actions,
    _resolve_ctrip_pricing_target_url,
    _select_pricing_room_type_read_only,
    _settle_page,
    _sync_ebk_browser_session_state,
)
from app.domain.browser.ctrip_pricing_page_reader import read_ctrip_pricing_write_page
from app.domain.browser.ctrip_pricing_page_writer import build_ctrip_pricing_page_write_preview
from app.domain.browser.ctrip_room_type_selector import wait_for_ctrip_pricing_page_state
from app.domain.browser.sessions import get_browser_session_store

if TYPE_CHECKING:
    from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService

async def prepare_pricing_page_write_dry_run_flow(
    service: CtripEbookingBrowserService,
    
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
    target_url: str | None = None,
) -> CtripPricingPageWritePreviewResult:
    requested_url = _resolve_ctrip_pricing_target_url(target_url)
    if requested_url is None:
        return CtripPricingPageWritePreviewResult(
            status="page_structure_changed",
            summary="Pricing page dry-run only accepts Ctrip EBK pricing URLs.",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            data={
                "query_type": "ctrip_ebooking_pricing_page_write_dry_run",
                "actor_id": actor_id,
                "execute_request_id": execute_request.get("request_id"),
                "page_write_preview": {},
                "captured_at": datetime.now(UTC).isoformat(),
            },
            evidence={
                "invalid_target_url": target_url,
                "browser_write_invoked": False,
                "ota_submit_invoked": False,
                "automation_bypass": False,
            },
            next_action="Provide a valid https://ebooking.ctrip.com EBK pricing calendar URL.",
        )

    page = await service._get_or_create_page(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        headless=False,
    )
    await page.goto(requested_url, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    # 2026-09-14：同读现价/查房价链路——日历主内容异步渲染，先有界等待
    # 页面态收敛再读，避免 600ms 内未渲染完被误报页面结构变化。
    await wait_for_ctrip_pricing_page_state(page)

    page_read = await read_ctrip_pricing_write_page(page, execute_request=execute_request)
    page_write_preview = build_ctrip_pricing_page_write_preview(
        execute_request=execute_request,
        page_read=page_read,
    )
    status = str(page_write_preview.get("status") or page_read.get("status") or "failed")
    summary = str(
        page_write_preview.get("summary")
        or page_read.get("summary")
        or "Ctrip EBK pricing page dry-run completed."
    )
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=status,
        page_state=str(page_read.get("page_state") or ""),
    )
    return CtripPricingPageWritePreviewResult(
        status=status,
        summary=summary,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        data={
            "query_type": "ctrip_ebooking_pricing_page_write_dry_run",
            "actor_id": actor_id,
            "requested_url": requested_url,
            "current_url": page.url,
            "execute_request_id": execute_request.get("request_id"),
            "execute_request": execute_request,
            "page_read": page_read,
            "page_write_preview": page_write_preview,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        evidence={
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "requested_url": requested_url,
            "current_url": page.url,
            "read_only": True,
            "dry_run": True,
            "browser_write_invoked": False,
            "input_fill_invoked": False,
            "save_click_invoked": False,
            "ota_submit_invoked": False,
            "automation_bypass": False,
            "screenshot_captured": False,
            "screenshot_path_persisted": False,
            "reader_evidence": page_read.get("evidence"),
            "writer_evidence": page_write_preview.get("evidence"),
        },
        next_action=(
            str(page_write_preview.get("next_action"))
            if page_write_preview.get("next_action")
            else None
        ),
    )

async def read_pricing_calendar_current_rates_flow(
    service: CtripEbookingBrowserService,
    
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
    target_url: str | None = None,
) -> CtripPricingPageWritePreviewResult:
    """Read current EBK room/date pricing facts after quick room selection.

    This is intentionally read-only. It may open the room selector, search,
    check the requested room type, and confirm the filter so the calendar
    table renders the real 房态/房量/卖价/底价 cells, but it never clicks price
    cells, fills inputs, saves, or submits.
    """
    from app.domain.browser.ctrip_room_type_selector import (
        dismiss_lightweight_popups,
        wait_for_ctrip_pricing_page_state,
    )

    requested_url = _resolve_ctrip_pricing_target_url(target_url)
    if requested_url is None:
        return CtripPricingPageWritePreviewResult(
            status="page_structure_changed",
            summary="Current pricing read only accepts Ctrip EBK pricing URLs.",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            data={
                "query_type": "ctrip_ebooking_pricing_calendar_current_rates",
                "actor_id": actor_id,
                "execute_request_id": execute_request.get("request_id"),
                "page_read": {},
                "captured_at": datetime.now(UTC).isoformat(),
            },
            evidence={
                "invalid_target_url": target_url,
                "read_only": True,
                "browser_write_invoked": False,
                "input_fill_invoked": False,
                "save_click_invoked": False,
                "ota_submit_invoked": False,
                "automation_bypass": False,
            },
            next_action="Provide a valid https://ebooking.ctrip.com EBK pricing calendar URL.",
        )

    page = await service._get_or_create_page(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        headless=False,
    )
    await page.goto(requested_url, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    await dismiss_lightweight_popups(page)
    # 2026-09-14：日历主内容异步渲染（叠加消息弹层/节假日横幅），固定 600ms
    # 后正文常无房价信号，曾被误报 not_pricing_page 打断调价链路（同 da614c0
    # 查房价链路）；改为有界等待信号收敛，超时才判结构变化。
    page_state = await wait_for_ctrip_pricing_page_state(page)
    current_url = page.url
    if page_state in ("login_required", "verification_required"):
        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status="human_required",
            page_state=page_state,
        )
        return CtripPricingPageWritePreviewResult(
            status="human_required",
            summary="Ctrip EBK requires login or verification before current price read.",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            data={"page_state": page_state, "current_url": current_url},
            evidence={
                "blocked_reason": page_state,
                "read_only": True,
                "browser_write_invoked": False,
                "ota_submit_invoked": False,
                "automation_bypass": False,
            },
            next_action="Complete login or verification manually.",
        )
    if page_state != "pricing_page":
        return CtripPricingPageWritePreviewResult(
            status="page_structure_changed",
            summary="Ctrip EBK pricing page was not recognized.",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            data={"page_state": page_state, "current_url": current_url},
            evidence={
                "blocked_reason": "not_pricing_page",
                "read_only": True,
                "browser_write_invoked": False,
                "ota_submit_invoked": False,
                "automation_bypass": False,
            },
            next_action="Open the Ctrip EBK pricing page manually and retry.",
        )

    selection_results = []
    overall_status = "success"
    for action in _pricing_actions(execute_request):
        room_type = str(action.get("room_type") or "")
        checkin_date = str(action.get("checkin_date") or "")
        selected = await _select_pricing_room_type_read_only(page, room_type=room_type)
        selection_results.append(
            {
                "room_type": room_type,
                "checkin_date": checkin_date,
                **selected,
            }
        )
        if selected.get("status") != "selected":
            overall_status = "page_structure_changed"
            break

    page_read = (
        await read_ctrip_pricing_write_page(page, execute_request=execute_request)
        if overall_status == "success"
        else {
            "status": overall_status,
            "summary": "Ctrip EBK room type selection did not reach the price table.",
            "targets": [],
            "evidence": {
                "selection_results": selection_results,
                "read_only": True,
                "browser_write_invoked": False,
                "ota_submit_invoked": False,
            },
        }
    )
    status = str(page_read.get("status") or overall_status)
    summary = str(
        page_read.get("summary")
        or "Ctrip EBK current pricing facts were read from the calendar."
    )
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=status,
        page_state=str(page_read.get("page_state") or page_state),
    )
    return CtripPricingPageWritePreviewResult(
        status=status,
        summary=summary,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        data={
            "query_type": "ctrip_ebooking_pricing_calendar_current_rates",
            "actor_id": actor_id,
            "requested_url": requested_url,
            "current_url": page.url,
            "execute_request_id": execute_request.get("request_id"),
            "execute_request": execute_request,
            "selection_results": selection_results,
            "page_read": page_read,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        evidence={
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "requested_url": requested_url,
            "current_url": page.url,
            "read_only": True,
            "browser_write_invoked": False,
            "input_fill_invoked": False,
            "save_click_invoked": False,
            "ota_submit_invoked": False,
            "automation_bypass": False,
            "screenshot_captured": False,
            "screenshot_path_persisted": False,
            "selection_results": selection_results,
            "reader_evidence": page_read.get("evidence"),
        },
        next_action=(
            "Use these current EBK pricing facts in the approval preview; "
            "do not execute OTA writes without approval."
        ),
    )

async def execute_pricing_page_write_flow(
    service: CtripEbookingBrowserService,
    
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
    target_url: str | None = None,
) -> CtripPricingPageWritePreviewResult:
    """Execute a full pricing page write: room select + fill + submit."""
    from app.domain.browser.ctrip_pricing_executor import (
        fill_ctrip_room_price,
        submit_ctrip_price_changes,
    )
    from app.domain.browser.ctrip_room_type_selector import (
        click_first_price_block,
        click_room_selection_confirm,
        detect_room_type_trigger,
        dismiss_lightweight_popups,
        safe_click,
        safe_fill_search,
        select_matching_room_results,
        wait_for_ctrip_pricing_page_state,
        wait_for_room_type_popup,
    )

    settings = get_settings()
    goto_url = _resolve_ctrip_pricing_target_url(target_url)
    if goto_url is None:
        return CtripPricingPageWritePreviewResult(
            status="page_structure_changed",
            summary="Pricing page execute only accepts Ctrip EBK pricing URLs.",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            data={
                "query_type": "ctrip_ebooking_pricing_page_write_execute",
                "execute_request_id": execute_request.get("request_id"),
                "invalid_target_url": target_url,
                "captured_at": datetime.now(UTC).isoformat(),
            },
            evidence={
                "invalid_target_url": target_url,
                "browser_write_invoked": False,
                "ota_submit_invoked": False,
                "automation_bypass": False,
            },
            next_action="Provide a valid https://ebooking.ctrip.com EBK pricing calendar URL.",
        )

    session = get_browser_session_store().get(browser_session_id)
    if session is None:
        return CtripPricingPageWritePreviewResult(
            status="human_required",
            summary="No Ctrip EBK browser session available for execute.",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            evidence={"blocked_reason": "browser_session_missing"},
            next_action="Open the Ctrip EBK browser login session manually.",
        )

    page = await service._get_or_create_page(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        headless=False,
    )

    await page.goto(goto_url, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    await dismiss_lightweight_popups(page)
    # 2026-09-14：同读现价/查房价链路——先有界等待页面态收敛，再进入房型
    # 选择与写价，避免 600ms 内未渲染完被误报页面结构变化。
    page_state = await wait_for_ctrip_pricing_page_state(page)
    current_url = page.url

    if page_state in ("login_required", "verification_required"):
        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status="human_required",
            page_state=page_state,
        )
        return CtripPricingPageWritePreviewResult(
            status="human_required",
            summary="Ctrip EBK requires login or verification before execute.",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            data={"page_state": page_state, "current_url": current_url},
            evidence={"blocked_reason": page_state},
            next_action="Complete login or verification manually.",
        )

    if page_state != "pricing_page":
        return CtripPricingPageWritePreviewResult(
            status="page_structure_changed",
            summary="Ctrip EBK pricing page was not recognized.",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            data={"page_state": page_state, "current_url": current_url},
            evidence={"blocked_reason": "not_pricing_page"},
            next_action="Open the Ctrip EBK pricing page manually and retry.",
        )

    actions = execute_request.get("actions") or []
    action_results: list[dict[str, Any]] = []
    submitted_targets: list[dict[str, Any]] = []
    overall_status = "executed"

    for action in actions:
        room_type = str(action.get("room_type") or "")
        checkin_date = str(action.get("checkin_date") or "")
        proposed_base = str(action.get("proposed_base_price_yuan") or "")

        if not room_type or not checkin_date or not proposed_base:
            action_results.append({
                "room_type": room_type, "checkin_date": checkin_date,
                "status": "data_required",
                "reason": "Missing room_type, checkin_date, or proposed_base_price_yuan.",
            })
            overall_status = "data_required"
            continue

        await dismiss_lightweight_popups(page)

        triggers = await detect_room_type_trigger(page)
        best_trigger = triggers[0] if triggers else None
        if not best_trigger or best_trigger.get("score", 0) <= 0:
            action_results.append({
                "room_type": room_type, "checkin_date": checkin_date,
                "status": "page_structure_changed",
                "reason": "No room-type quick-select trigger found.",
            })
            overall_status = "page_structure_changed"
            continue

        if not await safe_click(
            page, best_trigger["selector_hint"], best_trigger.get("text", "")
        ):
            action_results.append({
                "room_type": room_type, "checkin_date": checkin_date,
                "status": "page_structure_changed",
                "reason": "Could not click room-type trigger.",
            })
            overall_status = "page_structure_changed"
            continue

        # 2026-09-14：弹窗搜索框异步渲染，固定 1500ms 单次 detect 会漏报
        # 「No search input」；改有界等待信号收敛（同读现价链路）。
        popup = await wait_for_room_type_popup(page)
        search_inputs = popup.get("search_inputs") or []
        if not search_inputs:
            action_results.append({
                "room_type": room_type, "checkin_date": checkin_date,
                "status": "page_structure_changed",
                "reason": "No search input in room-type popup.",
            })
            overall_status = "page_structure_changed"
            continue

        search_selector = search_inputs[0]["selector_hint"]
        if await select_matching_room_results(
            page,
            search_selector,
            room_type,
            allow_filter=False,
        ):
            pass
        else:
            if not await safe_fill_search(page, search_selector, room_type):
                action_results.append({
                    "room_type": room_type, "checkin_date": checkin_date,
                    "status": "page_structure_changed",
                    "reason": "Could not fill room-type search.",
                })
                overall_status = "page_structure_changed"
                continue

            if not await select_matching_room_results(page, search_selector, room_type):
                action_results.append({
                    "room_type": room_type, "checkin_date": checkin_date,
                    "status": "page_structure_changed",
                    "reason": f"Could not match room type \"{room_type}\".",
                })
                overall_status = "page_structure_changed"
                continue

        if not await click_room_selection_confirm(page):
            action_results.append({
                "room_type": room_type, "checkin_date": checkin_date,
                "status": "page_structure_changed",
                "reason": "Could not confirm room selection.",
            })
            overall_status = "page_structure_changed"
            continue

        if not await click_first_price_block(
            page, room_type=room_type, checkin_date=checkin_date
        ):
            action_results.append({
                "room_type": room_type, "checkin_date": checkin_date,
                "status": "page_structure_changed",
                "reason": f"Could not click price block for {checkin_date}.",
            })
            overall_status = "page_structure_changed"
            continue

        target = {
            "room_type": room_type,
            "checkin_date": checkin_date,
            "proposed_price_yuan": action.get("proposed_price_yuan"),
            "proposed_base_price_yuan": proposed_base,
            "selector_hint": action.get("selector_hint"),
            "input_candidates": action.get("input_candidates") or [],
        }
        fill_result = await fill_ctrip_room_price(page, target=target, base_price=proposed_base)

        if fill_result.get("status") != "filled":
            action_results.append({
                "room_type": room_type, "checkin_date": checkin_date,
                "status": fill_result.get("status", "input_failed"),
                "fill_result": fill_result,
            })
            if overall_status == "executed":
                overall_status = "failed"
            continue

        action_results.append({
            "room_type": room_type, "checkin_date": checkin_date,
            "status": "filled", "fill_result": fill_result,
        })
        submitted_targets.append(
            {
                "room_type": room_type,
                "checkin_date": checkin_date,
                "proposed_price_yuan": action.get("proposed_price_yuan"),
                "proposed_base_price_yuan": proposed_base,
            }
        )

    submit_result = None
    if overall_status == "executed":
        artifact_dir = Path(settings.playwright_artifact_dir)
        submit_result = await submit_ctrip_price_changes(
            page,
            targets=submitted_targets,
            artifact_dir=artifact_dir,
            platform_account_id=platform_account_id,
        )
        if submit_result.get("status") != "executed":
            overall_status = submit_result.get("status", "failed")

    summary_parts = [f"Executed {len(action_results)} pricing action(s)"]
    if submit_result:
        summary_parts.append(f"Submit: {submit_result.get('status')}")
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=overall_status,
        page_state=page_state,
    )

    return CtripPricingPageWritePreviewResult(
        status=overall_status,
        summary=". ".join(summary_parts),
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        data={
            "query_type": "ctrip_ebooking_pricing_page_write_execute",
            "execute_request_id": execute_request.get("request_id"),
            "current_url": page.url,
            "action_results": action_results,
            "submit_result": submit_result,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        evidence={
            "pricing_executor_version": "ctrip_ebooking_pricing_executor_v1",
            "read_only": False,
            "browser_write_invoked": True,
            "ota_submit_invoked": overall_status == "executed",
            "page_state": page_state,
        },
        next_action=(
            "Verify changed prices on Ctrip EBK."
            if overall_status == "executed"
            else "Inspect the browser page and resubmit after resolving the issue."
        ),
    )

