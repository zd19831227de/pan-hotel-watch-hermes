from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from app.core.settings import get_settings
from app.domain.browser.ctrip_common import _is_ctrip_url, _is_login_url
from app.domain.browser.ctrip_ebooking_parsers import (
    _CTRIP_MONITOR_DATASET_IDS,
    _build_monitor_snapshot,
    _clean_dataset_lines,
    _extract_business_overview,
    _extract_comment_monitor_snapshot,
    _extract_dataset_snapshot,
    _resolve_dataset_definition,
    dataset_expected_terms,
)
from app.domain.browser.ctrip_ebooking_dataset_parsers import (
    _extract_beneficial_monthly_metrics,
    _extract_business_outline_app_funnel_metrics,
)
from app.domain.browser.ctrip_ebooking_results import (
    CtripEbookingCommentMonitorResult,
    CtripEbookingDatasetResult,
    CtripEbookingMonitorResult,
    CtripEbookingSnapshotResult,
)
from app.domain.browser.ctrip_ebooking_session_helpers import (
    _classify_page_state,
    _dismiss_ebk_read_blocking_popups,
    _is_ctrip_home_url,
    _is_stale_playwright_page_error,
    _safe_title,
    _safe_visible_text,
    _select_ad_pyramid_today_filter,
    _select_comment_monitor_today,
    _select_dataset_time_dimension,
    _select_promotion_effect_yesterday_source,
    _settle_page,
    _sync_ebk_browser_session_state,
)
from app.domain.browser.ctrip_ebooking_time_dimensions import (
    assemble_time_dimensioned_snapshot,
    dataset_time_dimension_specs,
    prepare_time_dimension_snapshot,
    resolve_dataset_status,
)
from app.domain.browser.ctrip_messages import (
    _build_comment_monitor_feishu_card,
    _build_comment_monitor_feishu_message,
    _build_dataset_feishu_card,
    _build_dataset_feishu_message,
    _build_feishu_message,
    _build_monitor_feishu_card,
    _build_monitor_feishu_message,
    _build_overview_feishu_card,
)
from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook

if TYPE_CHECKING:
    from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService

async def read_overview_snapshot_flow(
    service: CtripEbookingBrowserService,
    
    *,
    platform_account_id: str,
    browser_session_id: str,
    hotel_id: str | None,
    actor_id: str,
    push_to_feishu: bool,
) -> CtripEbookingSnapshotResult:
    page = await service._get_or_create_page(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        headless=False,
    )
    settings = get_settings()
    if not _is_ctrip_url(page.url):
        await page.goto(
            settings.ctrip_ebooking_url,
            wait_until="domcontentloaded",
            timeout=45_000,
        )
    elif not _is_ctrip_home_url(page.url) and not _is_login_url(page.url):
        await page.goto(
            settings.ctrip_ebooking_home_url,
            wait_until="domcontentloaded",
            timeout=45_000,
        )
    await _settle_page(page)
    popup_dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if popup_dismissal.get("dismissed"):
        await _settle_page(page)

    title = await _safe_title(page)
    body_text = await _safe_visible_text(page)
    current_url = page.url
    screenshot_path = await service._capture_screenshot(page, platform_account_id)
    page_state = _classify_page_state(current_url, body_text)
    overview = _extract_business_overview(body_text)

    status = "success" if page_state == "authenticated" else "human_required"
    summary = (
        "Ctrip EBooking real browser data was read and structured successfully."
        if status == "success"
        else (
            "Ctrip EBooking requires login, verification, or manual takeover "
            "before reading data."
        )
    )
    next_action = (
        (
            "Use analysis_input as the first real OTA read dataset and refine "
            "selectors from evidence."
        )
        if status == "success"
        else "Complete login or verification in the persistent browser, then retry this read."
    )
    data = {
        "hotel_id": hotel_id,
        "actor_id": actor_id,
        "query_type": "ctrip_ebooking_overview_snapshot",
        "current_url": current_url,
        "page_title": title,
        "page_state": page_state,
        "logged_in_detected": status == "success",
        "analysis_input": overview["analysis_input"],
        "dashboard_metrics": overview["dashboard_metrics"],
        "business_sections": overview["business_sections"],
        "numeric_facts": overview["numeric_facts"],
        "possible_orders": overview["possible_orders"],
        "text_snippets": overview["text_snippets"],
        "highlight_lines": overview["highlight_lines"],
        "captured_at": datetime.now(UTC).isoformat(),
    }
    evidence = {
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "screenshot_path": screenshot_path,
        "persistent_profile_configured": True,
        "profile_path_returned": False,
        "password_stored": False,
        "password_returned": False,
        "automation_bypass": False,
        "read_only": True,
        "parser_version": "ctrip_ebooking_visible_text_v2",
        "page_state": page_state,
        "body_text_length": len(body_text),
        "popup_dismissal": popup_dismissal,
    }
    feishu_push = None
    if push_to_feishu:
        title_text = "Pan Hotel Watch - Ctrip EBK 真实读取"
        fallback_text = _build_feishu_message(status=status, data=data, evidence=evidence)
        feishu_push = await push_interactive_card_to_feishu_webhook(
            title=title_text,
            card=_build_overview_feishu_card(status=status, data=data, evidence=evidence),
            webhook_url=get_settings().feishu_webhook_url,
            fallback_text=fallback_text,
        )
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=status,
        page_state=page_state,
    )
    return CtripEbookingSnapshotResult(
        status=status,
        summary=summary,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        data=data,
        evidence=evidence,
        feishu_push=feishu_push,
        next_action=next_action,
    )

def _dimension_parse_status(*, page_state: str, parsed: dict[str, Any], selected: bool) -> str:
    if page_state != "authenticated":
        return "human_required"
    if not selected:
        return "filter_partial"
    if not parsed.get("structure_ok"):
        return "page_structure_changed"
    if parsed.get("status_hint") in {"partial", "needs_review"}:
        return str(parsed["status_hint"])
    return "success"


async def read_dataset_flow(
    service: CtripEbookingBrowserService,
    
    *,
    platform_account_id: str,
    browser_session_id: str,
    dataset_id: str,
    filters: dict[str, Any] | None,
    actor_id: str,
    push_to_feishu: bool,
) -> CtripEbookingDatasetResult:
    dataset = _resolve_dataset_definition(dataset_id)
    page = await goto_dataset_page_with_recovery(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        url=dataset.url,
    )
    await _settle_page(page)
    popup_dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if popup_dismissal.get("dismissed"):
        await _settle_page(page)
    page_filter_evidence: dict[str, Any] | None = None
    if dataset.dataset_id == "promotion_effect":
        page_filter_evidence = await _select_promotion_effect_yesterday_source(page)
        await _settle_page(page)
        post_filter_popup_dismissal = await _dismiss_ebk_read_blocking_popups(page)
        if post_filter_popup_dismissal.get("dismissed"):
            popup_dismissal = post_filter_popup_dismissal
            await _settle_page(page)
    elif dataset.dataset_id == "ad_pyramid":
        page_filter_evidence = await _select_ad_pyramid_today_filter(page)
        await _settle_page(page)
        post_filter_popup_dismissal = await _dismiss_ebk_read_blocking_popups(page)
        if post_filter_popup_dismissal.get("dismissed"):
            popup_dismissal = post_filter_popup_dismissal
            await _settle_page(page)

    outline_app_funnel = {"status": "not_applicable", "metrics": {}}
    if dataset.dataset_id == "business_outline":
        outline_app_funnel = await _read_business_outline_app_funnel(page)
        await _settle_page(page)
        post_funnel_popup_dismissal = await _dismiss_ebk_read_blocking_popups(page)
        if post_funnel_popup_dismissal.get("dismissed"):
            popup_dismissal = post_funnel_popup_dismissal
            await _settle_page(page)

    time_specs = dataset_time_dimension_specs(dataset.dataset_id)
    if time_specs:
        dimension_payloads: dict[str, Any] = {}
        dimension_matched_terms: dict[str, list[str]] = {}
        filter_entries: list[dict[str, Any]] = []
        page_state = "unknown"
        body_text = ""
        current_url = page.url
        for spec in time_specs:
            click_result = await _select_dataset_time_dimension(
                page, dataset_id=dataset.dataset_id, dimension_key=spec.key
            )
            await _settle_page(page)
            post_filter_popup_dismissal = await _dismiss_ebk_read_blocking_popups(page)
            if post_filter_popup_dismissal.get("dismissed"):
                popup_dismissal = post_filter_popup_dismissal
                await _settle_page(page)
            body_text = await _safe_visible_text(page)
            current_url = page.url
            page_state = _classify_page_state(current_url, body_text)
            dim_parsed = _extract_dataset_snapshot(
                dataset=dataset, body_text=body_text, filters=filters
            )
            dim_parsed = prepare_time_dimension_snapshot(
                dataset_id=dataset.dataset_id,
                dimension_key=spec.key,
                parsed=dim_parsed,
                selected=bool(click_result.get("selected")),
            )
            if dataset.dataset_id == "business_outline" and spec.key == "daily":
                funnel_metrics = outline_app_funnel.get("metrics")
                if isinstance(funnel_metrics, dict) and funnel_metrics:
                    dim_parsed["metrics"] = {
                        **(dim_parsed.get("metrics") or {}),
                        **funnel_metrics,
                    }
                    dim_parsed["structure_ok"] = True
                elif outline_app_funnel.get("status") == "unavailable":
                    notes = list(dim_parsed.get("data_quality_notes") or [])
                    if "business_outline_app_funnel_unavailable" not in notes:
                        notes.append("business_outline_app_funnel_unavailable")
                    dim_parsed["data_quality_notes"] = notes
            dim_status = _dimension_parse_status(
                page_state=page_state,
                parsed=dim_parsed,
                selected=bool(click_result.get("selected")),
            )
            dimension_payloads[spec.key] = {
                "label": spec.label,
                "status": dim_status,
                "structure_ok": dim_parsed.get("structure_ok"),
                "metrics": dim_parsed.get("metrics") or {},
                "tables": dim_parsed.get("tables") or [],
                "rankings": dim_parsed.get("rankings") or [],
                "metric_groups": dim_parsed.get("metric_groups") or {},
                "warnings": dim_parsed.get("warnings") or [],
                "raw_snippets": dim_parsed.get("raw_snippets") or [],
                "data_quality_notes": dim_parsed.get("data_quality_notes") or [],
                "updated_at": dim_parsed.get("updated_at"),
                "inferred_current": bool(dim_parsed.get("inferred_current")),
            }
            dimension_matched_terms[spec.key] = list(
                dim_parsed.get("matched_expected_terms") or []
            )
            filter_entries.append(click_result)
            if dim_status == "human_required":
                break

        title = await _safe_title(page)
        screenshot_path = await service._capture_screenshot(page, platform_account_id)
        assembled = assemble_time_dimensioned_snapshot(
            dataset_id=dataset.dataset_id,
            dimensions=dimension_payloads,
            page_filter_entries=filter_entries,
        )
        status = resolve_dataset_status(dataset.dataset_id, dimension_payloads)
        page_filter_evidence = assembled["page_filter"]
        matched_expected_terms = list(
            dict.fromkeys(
                term
                for terms in dimension_matched_terms.values()
                for term in terms
            )
        )

        if status == "human_required":
            summary = "Ctrip EBooking requires login or verification before reading dataset data."
            next_action = "Complete login or verification in the persistent browser, then retry."
        elif status == "page_structure_changed":
            summary = (
                f"Ctrip dataset {dataset.dataset_id} was opened, but expected business fields "
                "were not found."
            )
            next_action = "Review screenshot/raw_snippets and refine dataset selectors."
        elif status in {"partial", "needs_review"}:
            summary = (
                f"Ctrip dataset {dataset.dataset_id} was read, but some visible fields "
                "need review."
            )
            next_action = "Review data_quality_notes/raw_snippets before using this dataset."
        else:
            summary = f"Ctrip dataset {dataset.dataset_id} was read and structured successfully."
            next_action = "Use this dataset in the monitoring feed and compare changes over time."

        data = {
            "dataset_id": dataset.dataset_id,
            "dataset": dataset.dataset_id,
            "status": status,
            "dataset_label": dataset.label,
            "filters": filters or {},
            "actor_id": actor_id,
            "url": current_url,
            "current_url": current_url,
            "page_title": title,
            "page_state": page_state,
            "summary": {},
            "summary_metrics": {},
            "promotion_metrics": [],
            "promotions": [],
            "ad_pyramid": {},
            "suggestions": [],
            "page_prompts": [],
            "metrics": assembled["metrics"],
            "metric_groups": assembled["metric_groups"],
            "tables": assembled["tables"],
            "rankings": assembled["rankings"],
            "warnings": assembled["warnings"],
            "raw_snippets": assembled["raw_snippets"],
            "data_quality_notes": assembled["data_quality_notes"],
            "updated_at": assembled["updated_at"] or datetime.now(UTC).isoformat(),
            "captured_at": datetime.now(UTC).isoformat(),
            "time_dimensions": assembled["time_dimensions"],
            "primary_time_dimension": assembled["primary_time_dimension"],
            "page_filter": page_filter_evidence,
        }
        evidence = {
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "dataset_id": dataset.dataset_id,
            "dataset_url": dataset.url,
            "screenshot_path": screenshot_path,
            "persistent_profile_configured": True,
            "profile_path_returned": False,
            "password_stored": False,
            "password_returned": False,
            "automation_bypass": False,
            "read_only": True,
            "parser_version": "ctrip_ebooking_dataset_visible_text_v1",
            "page_state": page_state,
            "body_text_length": len(body_text),
            "expected_terms": list(dataset_expected_terms(dataset)),
            "matched_expected_terms": matched_expected_terms,
            "popup_dismissal": popup_dismissal,
            "page_filter": page_filter_evidence,
        }
    else:
        title = await _safe_title(page)
        body_text = await _safe_visible_text(page)
        current_url = page.url
        screenshot_path = await service._capture_screenshot(page, platform_account_id)
        page_state = _classify_page_state(current_url, body_text)
        parsed = _extract_dataset_snapshot(dataset=dataset, body_text=body_text, filters=filters)

        if page_state != "authenticated":
            status = "human_required"
            summary = "Ctrip EBooking requires login or verification before reading dataset data."
            next_action = "Complete login or verification in the persistent browser, then retry."
        elif not parsed["structure_ok"]:
            status = "page_structure_changed"
            summary = (
                f"Ctrip dataset {dataset.dataset_id} was opened, but expected business fields "
                "were not found."
            )
            next_action = "Review screenshot/raw_snippets and refine dataset selectors."
        elif parsed.get("status_hint") in {"partial", "needs_review"}:
            status = str(parsed["status_hint"])
            summary = (
                f"Ctrip dataset {dataset.dataset_id} was read, but some visible fields "
                "need review."
            )
            next_action = "Review data_quality_notes/raw_snippets before using this dataset."
        else:
            status = "success"
            summary = f"Ctrip dataset {dataset.dataset_id} was read and structured successfully."
            next_action = "Use this dataset in the monitoring feed and compare changes over time."

        data = {
            "dataset_id": dataset.dataset_id,
            "dataset": dataset.dataset_id,
            "status": status,
            "dataset_label": dataset.label,
            "filters": filters or {},
            "actor_id": actor_id,
            "url": current_url,
            "current_url": current_url,
            "page_title": title,
            "page_state": page_state,
            "summary": parsed.get("summary", {}),
            "summary_metrics": parsed.get("summary_metrics", {}),
            "promotion_metrics": parsed.get("promotion_metrics", []),
            "promotions": parsed.get("promotions", []),
            "ad_pyramid": parsed.get("ad_pyramid", {}),
            "suggestions": parsed.get("suggestions", []),
            "page_prompts": parsed.get("page_prompts", []),
            "metrics": parsed["metrics"],
            "metric_groups": parsed["metric_groups"],
            "tables": parsed["tables"],
            "rankings": parsed["rankings"],
            "warnings": parsed["warnings"],
            "raw_snippets": parsed["raw_snippets"],
            "data_quality_notes": parsed.get("data_quality_notes", []),
            "updated_at": parsed["updated_at"] or datetime.now(UTC).isoformat(),
            "captured_at": datetime.now(UTC).isoformat(),
        }
        if page_filter_evidence is not None:
            data["page_filter"] = page_filter_evidence
        evidence = {
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "dataset_id": dataset.dataset_id,
            "dataset_url": dataset.url,
            "screenshot_path": screenshot_path,
            "persistent_profile_configured": True,
            "profile_path_returned": False,
            "password_stored": False,
            "password_returned": False,
            "automation_bypass": False,
            "read_only": True,
            "parser_version": "ctrip_ebooking_dataset_visible_text_v1",
            "page_state": page_state,
            "body_text_length": len(body_text),
            "expected_terms": list(dataset_expected_terms(dataset)),
            "matched_expected_terms": parsed["matched_expected_terms"],
            "popup_dismissal": popup_dismissal,
        }
        if page_filter_evidence is not None:
            evidence["page_filter"] = page_filter_evidence
    monthly_supplement: dict[str, Any] | None = None
    if dataset.dataset_id == "beneficial_data" and status == "success":
        monthly_supplement = await _read_beneficial_monthly_supplement(page)
        if monthly_supplement is not None:
            data["monthly"] = monthly_supplement
            evidence["monthly_supplement"] = monthly_supplement.get("status")
    feishu_push = None
    if push_to_feishu:
        title_text = f"Pan Hotel Watch - Ctrip EBK 数据中心 {dataset.label}"
        fallback_text = _build_dataset_feishu_message(
            status=status,
            data=data,
            evidence=evidence,
        )
        feishu_push = await push_interactive_card_to_feishu_webhook(
            title=title_text,
            card=_build_dataset_feishu_card(status=status, data=data, evidence=evidence),
            webhook_url=get_settings().feishu_webhook_url,
            fallback_text=fallback_text,
        )
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=status,
        page_state=page_state,
    )
    return CtripEbookingDatasetResult(
        status=status,
        summary=summary,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        dataset_id=dataset.dataset_id,
        data=data,
        evidence=evidence,
        feishu_push=feishu_push,
        next_action=next_action,
    )


async def _read_business_outline_app_funnel(page: Page) -> dict[str, Any]:
    """Capture the outline page's authenticated, read-only yesterday funnel response."""

    try:
        async with page.expect_response(
            lambda response: (
                "queryFlowTransforNewV1" in response.url
                and response.request.method.upper() == "POST"
            ),
            timeout=20_000,
        ) as response_info:
            await page.reload(wait_until="domcontentloaded", timeout=45_000)
        response = await response_info.value
        if response.status != 200:
            return {
                "status": "unavailable",
                "reason": "request_failed",
                "metrics": {},
            }
        payload = await response.json()
    except (AttributeError, PlaywrightError, TypeError, ValueError):
        return {"status": "unavailable", "reason": "request_failed", "metrics": {}}
    metrics = _extract_business_outline_app_funnel_metrics(payload)
    if not metrics:
        return {"status": "unavailable", "reason": "metrics_missing", "metrics": {}}
    return {"status": "success", "metrics": metrics}

async def _read_beneficial_monthly_supplement(page: Page) -> dict[str, Any] | None:
    """销售数据页「离店·按月」补充读取：本月经营概览。

    页面在按月视图下直接展示「去年 同期 / 去年 同比」原文，因此同比不做任何
    推算。补充读取失败只返回 unavailable，不影响主快照状态。"""
    try:
        await page.get_by_text("总平台", exact=True).first.click(timeout=3_000)
        await page.wait_for_timeout(1_500)
    except Exception:
        # 平台维度保持页面当前选中，口径以页面为准。
        pass
    try:
        monthly_tabs = page.get_by_text("按月", exact=True)
        count = await monthly_tabs.count()
        if count < 1:
            return {"status": "unavailable", "reason": "monthly_tab_not_found", "metrics": {}}
        # 页面按 预订/在店/离店 依次各有一组时间 tab，最后一个「按月」属于离店。
        await monthly_tabs.nth(count - 1).click(timeout=5_000)
    except Exception:
        return {"status": "unavailable", "reason": "monthly_tab_click_failed", "metrics": {}}
    await _settle_page(page)
    body_text = await _safe_visible_text(page)
    metrics = _extract_beneficial_monthly_metrics(_clean_dataset_lines(body_text))
    if not any(key != "monthly_period_label" for key in metrics):
        return {"status": "unavailable", "reason": "monthly_metrics_not_found", "metrics": {}}
    return {
        "status": "success",
        "captured_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
    }


async def goto_dataset_page_with_recovery(
    service: CtripEbookingBrowserService,
    
    *,
    platform_account_id: str,
    browser_session_id: str,
    url: str,
) -> Page:
    page = await service._get_or_create_page(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        headless=False,
    )
    try:
        await _goto_dataset_url(page, url)
        return page
    except Exception as exc:
        if not _is_stale_playwright_page_error(exc):
            raise
        await service._discard_cached_browser_state(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )

    page = await service._get_or_create_page(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        headless=False,
    )
    await _goto_dataset_url(page, url)
    return page


async def _goto_dataset_url(page: Page, url: str) -> None:
    max_retry = 3
    for attempt in range(max_retry):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            break
        except Exception as exc:
            if not _is_navigation_abort(exc) or attempt >= max_retry - 1:
                raise
            await page.wait_for_timeout(random.randint(500, 1_200))
    await page.wait_for_timeout(_ebk_dataset_page_switch_delay_ms())


def _is_navigation_abort(exc: Exception) -> bool:
    return "err_aborted" in str(exc).lower()


def _ebk_dataset_page_switch_delay_ms() -> int:
    return random.randint(2_000, 3_000)

async def read_monitor_flow(
    service: CtripEbookingBrowserService,
    
    *,
    platform_account_id: str,
    browser_session_id: str,
    filters: dict[str, Any] | None,
    actor_id: str,
    push_to_feishu: bool,
) -> CtripEbookingMonitorResult:
    dataset_results: list[CtripEbookingDatasetResult] = []
    for dataset_id in _CTRIP_MONITOR_DATASET_IDS:
        dataset_results.append(
            await read_dataset_flow(
                    service,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                dataset_id=dataset_id,
                filters=filters or {},
                actor_id=actor_id,
                push_to_feishu=False,
            )
        )

    dataset_payloads = [result.model_dump(mode="json") for result in dataset_results]
    monitor = _build_monitor_snapshot(dataset_payloads)
    statuses = [result.status for result in dataset_results]
    if any(status == "human_required" for status in statuses):
        status = "human_required"
        summary = "Ctrip EBooking requires login or verification before reading monitor data."
        next_action = "Complete login or verification in the persistent browser, then retry."
    elif any(status == "page_structure_changed" for status in statuses):
        status = "partial"
        summary = "Ctrip monitor was read, but some dataset structures need selector review."
        next_action = "Review datasets with page_structure_changed and refine selectors."
    else:
        status = "success"
        summary = "Ctrip data-center monitor was read and summarized successfully."
        next_action = "Push this monitor snapshot to Feishu or persist it for trend alerts."

    screenshot_paths = [
        payload.get("evidence", {}).get("screenshot_path")
        for payload in dataset_payloads
        if payload.get("evidence", {}).get("screenshot_path")
    ]
    data = {
        "query_type": "ctrip_ebooking_data_center_monitor",
        "actor_id": actor_id,
        "filters": filters or {},
        "monitor": monitor,
        "datasets": {payload["dataset_id"]: payload["data"] for payload in dataset_payloads},
        "captured_at": datetime.now(UTC).isoformat(),
    }
    evidence = {
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "dataset_ids": list(_CTRIP_MONITOR_DATASET_IDS),
        "screenshot_paths": screenshot_paths,
        "password_stored": False,
        "password_returned": False,
        "automation_bypass": False,
        "read_only": True,
        "parser_version": "ctrip_ebooking_monitor_v1",
        "dataset_statuses": {
            payload["dataset_id"]: payload["status"] for payload in dataset_payloads
        },
    }
    feishu_push = None
    if push_to_feishu:
        title_text = "Pan Hotel Watch - Ctrip EBK 真实读取"
        fallback_text = _build_monitor_feishu_message(
            status=status,
            data=data,
            evidence=evidence,
        )
        feishu_push = await push_interactive_card_to_feishu_webhook(
            title=title_text,
            card=_build_monitor_feishu_card(status=status, data=data, evidence=evidence),
            webhook_url=get_settings().feishu_webhook_url,
            fallback_text=fallback_text,
        )
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=status,
        page_state=None,
    )
    return CtripEbookingMonitorResult(
        status=status,
        summary=summary,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        data=data,
        evidence=evidence,
        feishu_push=feishu_push,
        next_action=next_action,
    )

async def read_comment_monitor_flow(
    service: CtripEbookingBrowserService,
    
    *,
    platform_account_id: str,
    browser_session_id: str,
    filters: dict[str, Any] | None,
    actor_id: str,
    push_to_feishu: bool,
) -> CtripEbookingCommentMonitorResult:
    comment_url = "https://ebooking.ctrip.com/ebkgrowth/datacenter/userbehavior/user?microJump=true"
    page = await goto_dataset_page_with_recovery(
            service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        url=comment_url,
    )
    await _settle_page(page)
    popup_dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if popup_dismissal.get("dismissed"):
        await _settle_page(page)
    title = await _safe_title(page)
    body_text = await _safe_visible_text(page)
    current_url = page.url
    screenshot_path = await service._capture_screenshot(page, platform_account_id)
    page_state = _classify_page_state(current_url, body_text)
    read_filters = {
        "scope": "review_analysis",
        "selected_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        **(filters or {}),
    }
    parsed = _extract_comment_monitor_snapshot(body_text=body_text, filters=read_filters)

    if page_state != "authenticated":
        status = "human_required"
        summary = "Ctrip EBooking requires login or verification before reading review analysis."
        next_action = "Complete login or verification in the persistent browser, then retry."
    elif not parsed["structure_ok"]:
        status = "page_structure_changed"
        summary = "Ctrip review analysis opened, but overview or reply metrics were not found."
        next_action = "Review screenshot/raw_snippets and refine review-analysis selectors."
    elif parsed.get("status_hint") == "partial":
        status = "partial"
        summary = "Ctrip review analysis was read, but some overview fields were missing."
        next_action = "Review raw_snippets and improve review-analysis parsing coverage."
    else:
        status = "success"
        summary = "Ctrip review analysis was read and summarized successfully."
        next_action = "Push this review analysis snapshot to Feishu or persist it for trends."

    data = {
        "query_type": "ctrip_ebooking_review_analysis",
        "actor_id": actor_id,
        "filters": read_filters,
        "current_url": current_url,
        "page_title": title,
        "page_state": page_state,
        "comment_monitor": {
            "review_overview": parsed["review_overview"],
            "reply_overview": parsed["reply_overview"],
            "overall_impression": parsed["overall_impression"],
            "keyword_statistics": parsed["keyword_statistics"],
            "user_profile": parsed["user_profile"],
            "competition_benchmark": parsed["competition_benchmark"],
            "sections": parsed["sections"],
            "metrics": parsed["metrics"],
            "raw_snippets": parsed["raw_snippets"],
            "data_quality_notes": parsed["data_quality_notes"],
        },
        "captured_at": datetime.now(UTC).isoformat(),
    }
    evidence = {
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "comment_url": comment_url,
        "screenshot_path": screenshot_path,
        "password_stored": False,
        "password_returned": False,
        "automation_bypass": False,
        "read_only": True,
        "parser_version": "ctrip_ebooking_review_analysis_visible_text_v1",
        "page_state": page_state,
        "body_text_length": len(body_text),
        "popup_dismissal": popup_dismissal,
    }
    feishu_push = None
    if push_to_feishu:
        title_text = "Pan Hotel Watch - Ctrip EBK 真实读取"
        fallback_text = _build_comment_monitor_feishu_message(
            status=status,
            data=data,
            evidence=evidence,
        )
        feishu_push = await push_interactive_card_to_feishu_webhook(
            title=title_text,
            card=_build_comment_monitor_feishu_card(
                status=status,
                data=data,
                evidence=evidence,
            ),
            webhook_url=get_settings().feishu_webhook_url,
            fallback_text=fallback_text,
        )
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=status,
        page_state=page_state,
    )
    return CtripEbookingCommentMonitorResult(
        status=status,
        summary=summary,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        data=data,
        evidence=evidence,
        feishu_push=feishu_push,
        next_action=next_action,
    )
