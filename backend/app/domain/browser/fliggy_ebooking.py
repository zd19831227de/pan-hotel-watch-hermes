from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.core.asyncio_runtime import configure_windows_subprocess_event_loop_policy
from app.core.settings import get_settings
from app.domain.browser.sessions import get_browser_session_store

FLIGGY_EBK_LOGIN_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/homeV1"
)
FLIGGY_EBK_BASE_URL = "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm"
FLIGGY_EBK_HOME_URL = f"{FLIGGY_EBK_BASE_URL}#/ebk/homeV1"
FLIGGY_EBK_DIAGNOSE_URL = f"{FLIGGY_EBK_BASE_URL}#/ebk-sycm/dataCenter/diagnoseReport"
FLIGGY_EBK_OPERATING_URL = f"{FLIGGY_EBK_BASE_URL}#/ebk-sycm/dataCenter/operatingData"
FLIGGY_EBK_PROMOTION_URL = f"{FLIGGY_EBK_BASE_URL}#/ebk-promotion/promotionAgent"
# 付费推广-直通车首页：数据总览/广告效果转化概况默认「今日」页签，
# 智能投放托管卡为当前状态；未开通商业化权限的账号会被重定向到
# self-service-application 申请页（2026-09-11 实测）。
FLIGGY_EBK_AD_P4P_URL = f"{FLIGGY_EBK_BASE_URL}#/bp/ebk-home"

_FLIGGY_HOME_PARSER = "fliggy_home_dom_v1"
_FLIGGY_DIAGNOSE_PARSER = "fliggy_diagnose_dom_v1"
_FLIGGY_OPERATING_PARSER = "fliggy_operating_dom_v1"
_FLIGGY_PROMOTION_PARSER = "fliggy_promotion_dom_v1"
_FLIGGY_AD_P4P_PARSER = "fliggy_ad_p4p_dom_v1"

_FLIGGY_DATASET_CONFIGS: dict[str, dict[str, str]] = {
    "home_v1": {
        "url": FLIGGY_EBK_HOME_URL,
        "url_marker": "homeV1",
    },
    "diagnose_report": {
        "url": FLIGGY_EBK_DIAGNOSE_URL,
        "url_marker": "diagnoseReport",
    },
    "operating_data": {
        "url": FLIGGY_EBK_OPERATING_URL,
        "url_marker": "operatingData",
    },
    "promotion_agent": {
        "url": FLIGGY_EBK_PROMOTION_URL,
        "url_marker": "promotionAgent",
    },
    "ad_p4p": {
        "url": FLIGGY_EBK_AD_P4P_URL,
        "url_marker": "ebk-home",
    },
}


class FliggyBrowserProfileLockedError(RuntimeError):
    """Raised when another process owns the persistent Fliggy browser profile."""


class FliggyEbookingBrowserService:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._cdp_browsers: dict[str, Browser] = {}
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._page_locks: dict[str, asyncio.Lock] = {}
        self._os_chrome_processes: dict[str, subprocess.Popen[Any]] = {}

    async def open_login(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )
        # 2026-09-23 立瑞案例：标签页可能停在早前会话过期时被弹回的
        # login.htm 上，之后运营已重新登录、Cookie 仍有效。直接亮出陈旧
        # 登录页会被误判为「又掉登录」——先导航到 EBK 首页用真实状态说话。
        if not _is_fliggy_url(page.url) or _is_fliggy_login_page_url(page.url):
            await page.goto(FLIGGY_EBK_LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
            await _settle_page(page)

        return await self._sync_login_state_from_page(
            page=page,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            opened_login_window=True,
        )

    async def check_login_state(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        cache_key = _fliggy_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        self._pages.pop(cache_key, None)
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )
        return await self._sync_login_state_from_page(
            page=page,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            opened_login_window=False,
        )

    async def read_dataset(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        dataset: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        normalized_dataset = _normalize_fliggy_dataset(dataset)
        config = _FLIGGY_DATASET_CONFIGS.get(normalized_dataset)
        if config is None:
            return _fliggy_unknown_dataset_payload(
                dataset=dataset,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            )

        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )
        url_marker = config["url_marker"]
        navigated = await _ensure_fliggy_dataset_page(
            page,
            target_url=str(config["url"]),
            url_marker=url_marker,
        )
        if navigated.get("status") == "human_required":
            return _fliggy_human_required_dataset_payload(
                dataset=normalized_dataset,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                current_url=str(navigated.get("current_url") or page.url),
                page_state=str(navigated.get("page_state") or "login"),
            )

        if normalized_dataset == "home_v1":
            text = await _capture_fliggy_home_text(page)
        elif normalized_dataset == "operating_data":
            text = await _capture_fliggy_operating_text(page)
        elif normalized_dataset == "promotion_agent":
            text = await _capture_fliggy_promotion_text(page)
        else:
            text = await _safe_fliggy_dataset_text(page, dataset=normalized_dataset)
        page_state = _classify_fliggy_page_state(page.url, text)
        if page_state != "authenticated":
            # Cookie/profile may still be settling after cold CDP attach.
            if page_state == "login":
                retry = await _ensure_fliggy_dataset_page(
                    page,
                    target_url=str(config["url"]),
                    url_marker=url_marker,
                    attempts=1,
                )
                if retry.get("status") != "human_required":
                    if normalized_dataset == "home_v1":
                        text = await _capture_fliggy_home_text(page)
                    elif normalized_dataset == "operating_data":
                        text = await _capture_fliggy_operating_text(page)
                    elif normalized_dataset == "promotion_agent":
                        text = await _capture_fliggy_promotion_text(page)
                    else:
                        text = await _safe_fliggy_dataset_text(page, dataset=normalized_dataset)
                    page_state = _classify_fliggy_page_state(page.url, text)
            if page_state != "authenticated":
                return _fliggy_human_required_dataset_payload(
                    dataset=normalized_dataset,
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    current_url=page.url,
                    page_state=page_state,
                )

        parsed = parse_fliggy_dataset_text(
            text,
            current_url=page.url,
            dataset=normalized_dataset,
        )
        if normalized_dataset == "ad_p4p":
            # 直通车两组数据页签（数据总览/广告效果转化概况）默认「今日」；
            # DOM 选中态校验通过才让适配层按 realtime 口径放行（与美团
            # 今日实时页签同款证据链）。
            checked_tabs = await _fliggy_ad_checked_tabs(page)
            parsed.setdefault("data", {})["today_evidence"] = {
                "today_confirmed": checked_tabs.count("今日") >= 2,
                "checked_tabs": checked_tabs,
                "default_page_tab": "今日",
            }
        parsed["platform_account_id"] = platform_account_id
        parsed["browser_session_id"] = browser_session_id
        parsed["manual_login_required"] = False
        parsed["evidence"]["session_port"] = get_browser_session_store().get(browser_session_id).session_port
        parsed["evidence"]["page_state"] = page_state
        parsed["next_action"] = (
            "可将本次飞猪 EBK 读取结果与携程/美团数据一起交给 Hermes 汇总。"
            if parsed["status"] == "success"
            else "请人工复核飞猪 EBK 页面结构后重试。"
        )
        get_browser_session_store().mark_authenticated(
            browser_session_id,
            actor_id,
            action=f"Fliggy EBK dataset read completed: {normalized_dataset}.",
        )
        return parsed

    async def _sync_login_state_from_page(
        self,
        *,
        page: Page,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        opened_login_window: bool,
    ) -> dict[str, Any]:
        body_text = await _safe_visible_text(page)
        current_url = page.url
        page_state = _classify_fliggy_page_state(current_url, body_text)
        login_complete = page_state == "authenticated"
        session = get_browser_session_store().get(browser_session_id)

        if login_complete:
            get_browser_session_store().mark_authenticated(
                browser_session_id,
                actor_id,
                action="Fliggy EBK login confirmed from opened browser session.",
            )
            summary = "Fliggy EBK login state is authenticated."
            next_action = "Fliggy login is ready for later read-only capture."
        else:
            if opened_login_window:
                with suppress(KeyError):
                    get_browser_session_store().mark_login_window_opened(
                        browser_session_id,
                        actor_id,
                        action="Fliggy EBK login window opened; finish login manually if required.",
                    )
                summary = "Fliggy EBK login page is open in system Chrome. Complete login manually."
                next_action = "Complete Fliggy login and any verification, then click 确认登录态."
            else:
                summary = "Fliggy EBK is not authenticated yet. Keep the Chrome window open and finish login."
                next_action = "Finish Fliggy login in Chrome, then click 确认登录态 again."

        return {
            "status": "success" if login_complete else "human_required",
            "summary": summary,
            "platform": "fliggy",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "login_url": FLIGGY_EBK_LOGIN_URL,
            "manual_login_required": not login_complete,
            "evidence": {
                "session_port": session.session_port,
                "profile_path_returned": False,
                "password_returned": False,
                "password_stored": False,
                "automation_bypass": False,
                "browser_mode": "os_chrome_cdp",
                "current_url": current_url,
                "page_state": page_state,
            },
            "next_action": next_action,
        }

    async def prepare_pricing_page_write_dry_run(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
        target_url: str | None = None,
    ) -> dict[str, Any]:
        """V9 Step 3 — 飞猪价格页写入空跑（dry-run，不填价不提交）。"""
        from app.domain.browser.fliggy_pricing_page_reader import (
            read_fliggy_pricing_write_page,
        )
        from app.domain.browser.fliggy_pricing_page_writer import (
            build_fliggy_pricing_page_write_preview,
        )
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _sync_ebk_browser_session_state,
        )

        requested_url = _resolve_fliggy_pricing_target_url(target_url)
        if requested_url is None:
            return _fliggy_pricing_invalid_url_payload(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                execute_request=execute_request,
                target_url=target_url,
            )
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=False,
        )
        await page.goto(requested_url, wait_until="domcontentloaded", timeout=45_000)
        await _settle_page(page)

        # B5.3 — 写价前先把页面收敛到「只选审批目标产品 + 指定日期」。
        from app.domain.browser.pricing_page_product_selection import (
            build_selection_failure_payload,
            ensure_product_selection,
        )

        selection = await ensure_product_selection(
            page=page,
            platform="fliggy",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
            query_type="fliggy_ebooking_pricing_page_write_dry_run",
        )
        if selection.get("status") != "success":
            return build_selection_failure_payload(
                selection,
                platform="fliggy",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                query_type="fliggy_ebooking_pricing_page_write_dry_run",
            )

        page_read = await read_fliggy_pricing_write_page(
            page, execute_request=execute_request
        )
        preview = build_fliggy_pricing_page_write_preview(
            execute_request=execute_request,
            page_read=page_read,
        )
        status = str(preview.get("status") or page_read.get("status") or "failed")
        summary = str(
            preview.get("summary") or page_read.get("summary") or "飞猪 EBK 价格页空跑完成。"
        )
        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status=status,
            page_state=str(page_read.get("page_state") or ""),
        )
        return {
            "status": status,
            "summary": summary,
            "platform": "fliggy",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {
                "query_type": "fliggy_ebooking_pricing_page_write_dry_run",
                "actor_id": actor_id,
                "requested_url": requested_url,
                "current_url": page.url,
                "execute_request_id": execute_request.get("request_id"),
                "page_read": page_read,
                "page_write_preview": preview,
                "captured_at": datetime.now(UTC).isoformat(),
            },
            "evidence": {
                "platform": "fliggy",
                "read_only": True,
                "dry_run": True,
                "product_selection": {
                    "matched_rows": selection.get("matched_rows"),
                    "counter": selection.get("counter"),
                    "steps": selection.get("steps"),
                },
                "browser_write_invoked": False,
                "input_fill_invoked": False,
                "save_click_invoked": False,
                "ota_submit_invoked": False,
                "automation_bypass": False,
                "reader_evidence": page_read.get("evidence"),
                "writer_evidence": preview.get("evidence"),
            },
            "next_action": preview.get("next_action"),
        }

    async def read_pricing_product_room_rates(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        target_url: str | None = None,
        checkin_date: str | None = None,
    ) -> dict[str, Any]:
        """只读：房价房量日历页读取房型现价/房态（查房价交互）。

        2026-09-24 实测：批量修改页产品树与编辑面板之外，「房价房量管理」
        日历页（roomsVsManage）是纯展示页，展开后产品子行带 房态/房量/
        卖价。查询改走此页；写价链路（批量修改页）不受影响。
        """

        from app.domain.browser.pricing_room_rates import (
            login_redirected_after_read,
            platform_read_error_payload,
            platform_read_payload,
            read_fliggy_calendar_room_rates,
            wait_for_definitive_page_state,
        )
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _settle_page,
            _sync_ebk_browser_session_state,
        )
        from app.domain.browser.fliggy_pricing_page_reader import (
            classify_fliggy_pricing_page_state,
        )

        requested_url = _resolve_fliggy_room_calendar_url(target_url)
        if requested_url is None:
            return platform_read_error_payload(
                platform="fliggy",
                status="page_structure_changed",
                summary="飞猪房价查询只接受 fliggy.com 页面链接。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason="invalid_target_url",
            )
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=False,
        )
        await page.goto(requested_url, wait_until="domcontentloaded", timeout=45_000)
        await _settle_page(page)

        # 2026-09-23 修复：固定 600ms 等待对慢渲染 SPA 不够；飞猪会话在
        # 读取过程中过期还会把页面重定向到 login（实测 09-23）。有界等待
        # 到明确页面态，采集后再做一次登录重定向后置检查。
        page_state = await wait_for_definitive_page_state(
            page, classify_fliggy_pricing_page_state
        )
        if page_state in ("login_required", "verification_required"):
            _sync_ebk_browser_session_state(
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                status="human_required",
                page_state=page_state,
            )
            return platform_read_error_payload(
                platform="fliggy",
                status="human_required",
                summary="飞猪 EBK 需要先人工完成登录/验证，再查询房价。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason=page_state,
            )
        if page_state != "pricing_page":
            return platform_read_error_payload(
                platform="fliggy",
                status="page_structure_changed",
                summary="飞猪房价房量日历页结构无法识别。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason=f"page_state={page_state}",
            )

        read = await read_fliggy_calendar_room_rates(
            page, checkin_date=str(checkin_date or "")
        )
        # 2026-09-23 实测：采集过程中会话过期会把页面重定向到 login，此时
        # 结果不可信，明确 human_required 而不是伪成功（房型名单无价格）。
        if login_redirected_after_read(page.url):
            _sync_ebk_browser_session_state(
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                status="human_required",
                page_state="login_redirect_after_read",
            )
            return platform_read_error_payload(
                platform="fliggy",
                status="human_required",
                summary="飞猪 EBK 登录态在读取过程中失效，需要人工重新登录后再查询房价。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason="login_redirect_after_read",
            )
        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status=str(read.get("status") or "failed"),
            page_state=page_state,
        )
        return platform_read_payload(
            platform="fliggy",
            status=str(read.get("status") or "failed"),
            summary=str(read.get("summary") or ""),
            room_rates=list(read.get("room_rates") or []),
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )

    async def execute_pricing_page_write(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
        target_url: str | None = None,
    ) -> dict[str, Any]:
        """V9 Step 3 — 飞猪价格页正式写入（填价 + 保存 + 逐目标回读）。"""
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _sync_ebk_browser_session_state,
        )
        from app.domain.browser.fliggy_pricing_executor import (
            fill_fliggy_room_price,
            submit_fliggy_price_changes,
        )
        from app.domain.browser.fliggy_pricing_page_reader import (
            classify_fliggy_pricing_page_state,
        )

        settings = get_settings()
        goto_url = _resolve_fliggy_pricing_target_url(target_url)
        if goto_url is None:
            return _fliggy_pricing_invalid_url_payload(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                execute_request=execute_request,
                target_url=target_url,
            )
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=False,
        )
        await page.goto(goto_url, wait_until="domcontentloaded", timeout=45_000)
        await _settle_page(page)

        # B5.3 — execute 同样先收敛页面到审批目标，杜绝波及默认勾选产品。
        from app.domain.browser.pricing_page_product_selection import (
            build_selection_failure_payload,
            ensure_product_selection,
        )

        selection = await ensure_product_selection(
            page=page,
            platform="fliggy",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
            query_type="fliggy_ebooking_pricing_page_write",
        )
        if selection.get("status") != "success":
            return build_selection_failure_payload(
                selection,
                platform="fliggy",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                query_type="fliggy_ebooking_pricing_page_write",
            )

        body_text = ""
        try:
            body_text = await page.locator("body").inner_text(timeout=5_000)
        except Exception:
            body_text = ""
        page_state = classify_fliggy_pricing_page_state(page.url, body_text)
        if page_state in ("login_required", "verification_required"):
            _sync_ebk_browser_session_state(
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                status="human_required",
                page_state=page_state,
            )
            return {
                "status": "human_required",
                "summary": "飞猪 EBK 需要先完成登录或验证，才能执行写价。",
                "platform": "fliggy",
                "platform_account_id": platform_account_id,
                "browser_session_id": browser_session_id,
                "data": {"page_state": page_state, "current_url": page.url},
                "evidence": {"blocked_reason": page_state, "ota_submit_invoked": False},
                "next_action": "请人工完成登录/验证后重试。",
            }
        if page_state != "pricing_page":
            return {
                "status": "page_structure_changed",
                "summary": "飞猪 EBK 价格页结构未能识别，写入已中止。",
                "platform": "fliggy",
                "platform_account_id": platform_account_id,
                "browser_session_id": browser_session_id,
                "data": {"page_state": page_state, "current_url": page.url},
                "evidence": {"blocked_reason": "not_pricing_page", "ota_submit_invoked": False},
                "next_action": "请人工打开飞猪 EBK 价格页确认结构后重试。",
            }

        actions = execute_request.get("actions") or []
        action_results: list[dict[str, Any]] = []
        submitted_targets: list[dict[str, Any]] = []
        overall_status = "executed"
        for action in actions:
            if not isinstance(action, dict):
                continue
            room_type = str(action.get("room_type") or "")
            checkin_date = str(action.get("checkin_date") or "")
            proposed_base = str(action.get("proposed_base_price_yuan") or "")
            if not room_type or not checkin_date or not proposed_base:
                action_results.append(
                    {
                        "room_type": room_type,
                        "checkin_date": checkin_date,
                        "status": "data_required",
                        "reason": "Missing room_type, checkin_date, or proposed_base_price_yuan.",
                    }
                )
                overall_status = "data_required"
                continue
            fill_result = await fill_fliggy_room_price(
                page,
                target={
                    "room_type": room_type,
                    "checkin_date": checkin_date,
                    "selector_hint": action.get("selector_hint"),
                    "input_candidates": action.get("input_candidates") or [],
                },
                base_price=proposed_base,
            )
            if fill_result.get("status") != "filled":
                action_results.append(
                    {
                        "room_type": room_type,
                        "checkin_date": checkin_date,
                        "status": fill_result.get("status", "input_failed"),
                        "fill_result": fill_result,
                    }
                )
                if overall_status == "executed":
                    overall_status = "failed"
                continue
            action_results.append(
                {"room_type": room_type, "checkin_date": checkin_date, "status": "filled"}
            )
            submitted_targets.append(
                {
                    "room_type": room_type,
                    "checkin_date": checkin_date,
                    "proposed_base_price_yuan": proposed_base,
                    "selector_hint": action.get("selector_hint"),
                }
            )

        submit_result = None
        if overall_status == "executed":
            submit_result = await submit_fliggy_price_changes(
                page,
                targets=submitted_targets,
                artifact_dir=Path(settings.playwright_artifact_dir),
                platform_account_id=platform_account_id,
            )
            if submit_result.get("status") != "executed":
                overall_status = submit_result.get("status", "failed")

        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status=overall_status,
            page_state=page_state,
        )
        summary_parts = [f"Executed {len(action_results)} pricing action(s)"]
        if submit_result:
            summary_parts.append(f"Submit: {submit_result.get('status')}")
        return {
            "status": overall_status,
            "summary": ". ".join(summary_parts),
            "platform": "fliggy",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {
                "query_type": "fliggy_ebooking_pricing_page_write_execute",
                "execute_request_id": execute_request.get("request_id"),
                "current_url": page.url,
                "action_results": action_results,
                "submit_result": submit_result,
                "captured_at": datetime.now(UTC).isoformat(),
            },
            "evidence": {
                "pricing_executor_version": "fliggy_ebooking_pricing_executor_v1",
                "platform": "fliggy",
                "read_only": False,
                "browser_write_invoked": True,
                "ota_submit_invoked": overall_status == "executed",
                "page_state": page_state,
            },
            "next_action": (
                "Verify changed prices on Fliggy EBK."
                if overall_status == "executed"
                else "Inspect the browser page and resubmit after resolving the issue."
            ),
        }

    async def _get_or_create_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        headless: bool,
    ) -> Page:
        cache_key = _fliggy_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        async with self._lock_for(cache_key):
            return await self._get_or_create_page_unlocked(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                headless=headless,
            )

    async def _get_or_create_page_unlocked(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        headless: bool,
    ) -> Page:
        del headless  # Fliggy login always uses a visible OS Chrome window.
        cache_key = _fliggy_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        existing = self._pages.get(cache_key)
        if existing:
            try:
                if not existing.is_closed():
                    return existing
            except Exception as exc:
                if _is_stale_playwright_page_error(exc):
                    await self._reset_playwright_driver()
                else:
                    raise

        session = get_browser_session_store().get(browser_session_id)
        profile_dir = self._profile_dir(platform_account_id)
        profile_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            playwright = await self._get_playwright()
            launch_lock: _BrowserProfileLaunchLock | None = None
            try:
                cdp_page = await self._connect_existing_debug_page(
                    playwright=playwright,
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    port=session.session_port,
                )
                if cdp_page:
                    return cdp_page

                launch_lock = _BrowserProfileLaunchLock(profile_dir)
                if not launch_lock.acquire():
                    cdp_page = await self._connect_existing_debug_page(
                        playwright=playwright,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                        port=session.session_port,
                        timeout_ms=5_000,
                    )
                    if cdp_page:
                        return cdp_page
                    raise FliggyBrowserProfileLockedError(
                        "飞猪浏览器账号正在被另一个后台任务或登录窗口占用，请稍后重试，或关闭占用该账号的 Chrome 后再打开。"
                    )

                chrome_path = _resolve_installed_chrome_path()
                if chrome_path is None:
                    launch_lock.release()
                    raise RuntimeError(
                        "未找到本机 Google Chrome。请安装 Chrome 后重试，或先用带调试端口的 Chrome 打开飞猪登录页。"
                    )

                # Cold-start on about:blank so the persistent profile cookies load
                # before any Fliggy navigation. Opening home/login on the CLI races
                # SSO and often lands on login.htm even when the profile is logged in.
                args = _fliggy_os_chrome_launch_args(
                    chrome_path=chrome_path,
                    profile_dir=profile_dir,
                    session_port=session.session_port,
                    login_url="about:blank",
                )
                process = subprocess.Popen(  # noqa: S603 - operator-owned local Chrome launch
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._os_chrome_processes[cache_key] = process
                ready = await _wait_for_cdp_ready(session.session_port, timeout_seconds=20)
                launch_lock.release()
                launch_lock = None
                if not ready:
                    raise RuntimeError(
                        "本机 Chrome 已启动，但调试端口尚未就绪。请确认账号调试端口未被占用后重试。"
                    )

                cdp_page = await self._connect_existing_debug_page(
                    playwright=playwright,
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    port=session.session_port,
                    timeout_ms=5_000,
                )
                if cdp_page:
                    return cdp_page
                raise RuntimeError("本机 Chrome 已启动，但无法通过 CDP 连接。请关闭占用窗口后重试。")
            except Exception as exc:
                if launch_lock is not None:
                    launch_lock.release()
                if _is_profile_singleton_error(exc):
                    cdp_page = await self._connect_existing_debug_page(
                        playwright=playwright,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                        port=session.session_port,
                        timeout_ms=5_000,
                    )
                    if cdp_page:
                        return cdp_page
                    raise FliggyBrowserProfileLockedError(
                        "飞猪浏览器账号正在被其他 Chrome 进程占用，无法同时启动第二个持久化浏览器。请稍后重试或关闭占用窗口。"
                    ) from exc
                if attempt == 0 and _is_stale_playwright_page_error(exc):
                    await self._reset_playwright_driver()
                    continue
                raise
        raise RuntimeError("Fliggy EBK browser page could not be created.")

    def _lock_for(self, cache_key: str) -> asyncio.Lock:
        lock = self._page_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            self._page_locks[cache_key] = lock
        return lock

    async def _reset_playwright_driver(self) -> None:
        close_candidates: list[Any] = [
            *self._pages.values(),
            *self._contexts.values(),
            *self._cdp_browsers.values(),
        ]
        self._pages.clear()
        self._contexts.clear()
        self._cdp_browsers.clear()
        for cached in close_candidates:
            with suppress(Exception):
                await cached.close()
        if self._playwright is not None:
            with suppress(Exception):
                await self._playwright.stop()
        self._playwright = None

    async def close(self) -> None:
        await self._reset_playwright_driver()

    async def _connect_existing_debug_page(
        self,
        *,
        playwright: Playwright,
        platform_account_id: str,
        browser_session_id: str,
        port: int,
        timeout_ms: int = 2_000,
    ) -> Page | None:
        try:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}",
                timeout=timeout_ms,
            )
        except PlaywrightError as exc:
            if _is_stale_playwright_page_error(exc):
                raise
            return None
        contexts = browser.contexts
        if not contexts:
            await browser.close()
            return None
        context = contexts[0]
        page = _select_fliggy_page(context.pages)
        if page is None:
            page = await context.new_page()
        else:
            await _close_blank_sibling_pages(context, keep=page)
        cache_key = _fliggy_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        self._cdp_browsers[browser_session_id] = browser
        self._contexts[cache_key] = context
        self._pages[cache_key] = page
        return page

    async def _get_playwright(self) -> Playwright:
        if self._playwright is None:
            configure_windows_subprocess_event_loop_policy()
            self._playwright = await async_playwright().start()
        return self._playwright

    def _profile_dir(self, platform_account_id: str) -> Path:
        settings = get_settings()
        return (Path(settings.browser_state_dir) / "fliggy-ebooking" / platform_account_id).resolve()


class _BrowserProfileLaunchLock:
    _STALE_AFTER_SECONDS = 120

    def __init__(self, profile_dir: Path) -> None:
        self.path = profile_dir / ".pan_hotel_watch_launch.lock"
        self._handle: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self._handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._handle, str(os.getpid()).encode("ascii", errors="ignore"))
                return True
            except FileExistsError:
                if attempt == 0 and self._is_stale():
                    with suppress(FileNotFoundError):
                        self.path.unlink()
                    continue
                return False
        return False

    def release(self) -> None:
        if self._handle is not None:
            with suppress(OSError):
                os.close(self._handle)
            self._handle = None
        with suppress(FileNotFoundError):
            self.path.unlink()

    def _is_stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > self._STALE_AFTER_SECONDS
        except OSError:
            return False


def _fliggy_cache_key(*, platform_account_id: str, browser_session_id: str) -> str:
    return f"{platform_account_id}:{browser_session_id}:fliggy-ebooking"


def _fliggy_os_chrome_launch_args(
    *,
    chrome_path: Path,
    profile_dir: Path,
    session_port: int,
    login_url: str,
) -> list[str]:
    return [
        str(chrome_path),
        f"--remote-debugging-port={session_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session",
        "--new-window",
        login_url,
    ]


def _resolve_installed_chrome_path() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


async def _wait_for_cdp_ready(session_port: int, *, timeout_seconds: float = 20) -> bool:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{session_port}/json/version"
    while time.monotonic() < deadline:
        ready = await asyncio.to_thread(_probe_cdp_version, url)
        if ready:
            return True
        await asyncio.sleep(0.25)
    return False


def _probe_cdp_version(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:  # noqa: S310 - local CDP only
            return int(getattr(response, "status", 200) or 200) == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _is_blank_page_url(url: str) -> bool:
    normalized = str(url or "").strip().lower().rstrip("/")
    return normalized in {
        "about:blank",
        "about:newtab",
        "chrome://new-tab-page",
        "chrome://newtab",
        "",
    }


async def _close_blank_sibling_pages(context: Any, *, keep: Any) -> None:
    """关闭复用连接时遗留的空白兄弟标签。

    2026-09-14：冷启动参数是 --restore-last-session + about:blank，恢复会话
    标签之外会多开一个空白标签；复用飞猪页时它永不被选中，每次打开浏览器
    都多一个「空白网页」。只关空白类标签——飞猪页面与当前选中页绝不动。
    """
    for candidate in list(context.pages):
        if candidate is keep:
            continue
        try:
            if candidate.is_closed():
                continue
            if not _is_blank_page_url(getattr(candidate, "url", "") or ""):
                continue
            with suppress(Exception):
                await candidate.close()
        except Exception:
            continue


def _select_fliggy_page(pages: list[Page]) -> Page | None:
    open_pages = [page for page in pages if not page.is_closed()]
    if not open_pages:
        return None

    authenticated = [
        page
        for page in open_pages
        if _classify_fliggy_page_state(page.url, "") == "authenticated"
    ]
    if authenticated:
        return authenticated[0]

    fliggy_pages = [page for page in open_pages if _is_fliggy_url(page.url)]
    if fliggy_pages:
        return fliggy_pages[0]
    return open_pages[0]


def _is_fliggy_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return (
        "fliggy.com" in host
        or "taobao.com" in host
        or "tmall.com" in host
        or "alibaba.com" in host
    )


def _is_fliggy_login_page_url(url: str) -> bool:
    """飞猪登录页 URL（会话过期被弹回时标签停在这里，不代表当前登录态）。"""

    normalized = str(url or "").lower()
    return "login.htm" in normalized or "login.html" in normalized


def _classify_fliggy_page_state(url: str, body_text: str) -> str:
    normalized_url = (url or "").lower()
    text = (body_text or "").lower()
    login_url = (
        "/login" in normalized_url
        or "login." in normalized_url
        or "passport" in normalized_url
        or normalized_url.endswith("login.jhtml")
        or normalized_url.endswith("login.html")
    )
    human_markers = (
        "验证码",
        "短信",
        "扫码",
        "二维码",
        "人机验证",
        "风控",
        "安全验证",
        "滑块",
        "请向右拖动",
    )
    if login_url and any(marker.lower() in text for marker in human_markers):
        return "human_required"
    if login_url:
        return "login"
    if "fliggy.com" in normalized_url and (
        "ebooking" in normalized_url
        or "ebk" in normalized_url
        or "hotelbaseinfouv" in normalized_url
    ):
        return "authenticated"
    if any(marker.lower() in text for marker in human_markers):
        return "human_required"
    return "human_required"


def _resolve_fliggy_pricing_target_url(target_url: str | None) -> str | None:
    """飞猪写价只接受 fliggy.com 域名下的 http(s) 价格页链接。"""
    cleaned = str(target_url or "").strip()
    if not cleaned:
        # B5.1 实测：默认执行面为「房价房量管理 → 批量修改-价格」页。
        from app.domain.browser.pricing_page_product_selection import (
            FLIGGY_PRICING_PAGE_URL,
        )

        return FLIGGY_PRICING_PAGE_URL
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host != "fliggy.com" and not host.endswith(".fliggy.com"):
        return None
    return cleaned


def _resolve_fliggy_room_calendar_url(target_url: str | None) -> str | None:
    """飞猪查房价只读面：默认「房价房量管理」日历页；自定义链接须 fliggy.com。"""

    cleaned = str(target_url or "").strip()
    if not cleaned:
        # 2026-09-24 实测：批量修改页不展示价格，现价/房态在价库日历页。
        from app.domain.browser.pricing_page_product_selection import (
            FLIGGY_ROOM_CALENDAR_URL,
        )

        return FLIGGY_ROOM_CALENDAR_URL
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host != "fliggy.com" and not host.endswith(".fliggy.com"):
        return None
    return cleaned


def _fliggy_pricing_invalid_url_payload(
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
    target_url: str | None,
) -> dict[str, Any]:
    return {
        "status": "page_structure_changed",
        "summary": "飞猪价格页写入只接受 fliggy.com 域名下的 EBK 价格页链接。",
        "platform": "fliggy",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {
            "query_type": "fliggy_ebooking_pricing_page_write",
            "actor_id": actor_id,
            "execute_request_id": execute_request.get("request_id"),
            "invalid_target_url": target_url,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        "evidence": {
            "invalid_target_url": target_url,
            "browser_write_invoked": False,
            "ota_submit_invoked": False,
            "automation_bypass": False,
        },
        "next_action": "请提供有效的 fliggy.com EBK 价格页链接。",
    }


async def _settle_page(page: Page) -> None:
    with suppress(Exception):
        await page.wait_for_timeout(1200)


async def _ensure_fliggy_dataset_page(
    page: Page,
    *,
    target_url: str,
    url_marker: str,
    attempts: int = 2,
) -> dict[str, str]:
    """Navigate to a Fliggy dataset URL, retrying once if SSO briefly shows login.htm."""

    last_state = "unknown"
    current_url = page.url or ""
    if url_marker in current_url and _classify_fliggy_page_state(current_url, "") == "authenticated":
        return {"status": "ok", "current_url": current_url, "page_state": "authenticated"}

    for attempt in range(max(1, attempts)):
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            if not _is_fliggy_navigation_error(exc):
                raise
            return {
                "status": "human_required",
                "current_url": page.url or current_url,
                "page_state": "navigation_failed",
            }
        await _settle_page(page)
        if attempt == 0:
            with suppress(Exception):
                # Give persistent-profile cookies a moment after cold CDP attach.
                await page.wait_for_timeout(2_000)
        current_url = page.url or ""
        preview = await _safe_visible_text(page)
        last_state = _classify_fliggy_page_state(current_url, preview)
        if last_state == "authenticated" or (
            url_marker in current_url and last_state != "login"
        ):
            return {"status": "ok", "current_url": current_url, "page_state": last_state}
        if last_state != "login":
            break
    return {
        "status": "human_required",
        "current_url": current_url,
        "page_state": last_state,
    }


async def _safe_visible_text(page: Page) -> str:
    with suppress(Exception):
        return await page.locator("body").inner_text(timeout=3_000)
    return ""


async def _safe_fliggy_dataset_text(page: Page, *, dataset: str) -> str:
    """Poll SPA body text briefly until dataset signals appear or attempts end."""

    best = ""
    for _ in range(6):
        text = await _safe_visible_text(page)
        if len(text) > len(best):
            best = text
        if _has_fliggy_dataset_signal(text, dataset=dataset):
            return text
        await _settle_page(page)
    return best


async def _capture_fliggy_home_text(page: Page) -> str:
    """Click 今日/昨日 tabs and stitch metric blocks so parsers see separate sections."""

    chunks: list[str] = []
    hotel_name: str | None = None
    for tab in ("今日数据", "昨日数据"):
        await _click_fliggy_visible_text(page, tab)
        await _settle_page(page)
        text = await _safe_fliggy_dataset_text(page, dataset="home_v1")
        lines = _clean_fliggy_text_lines(text)
        if hotel_name is None:
            hotel_name = _infer_fliggy_hotel_name(lines)
        metric_lines = _slice_fliggy_home_metric_lines(lines)
        chunks.append(tab)
        chunks.extend(metric_lines)
    if len(chunks) <= 2:
        return await _safe_fliggy_dataset_text(page, dataset="home_v1")
    if hotel_name:
        chunks.insert(0, hotel_name)
    return "\n".join(chunks)


async def _capture_fliggy_operating_text(page: Page) -> str:
    """Capture 交易数据 (在店/预订) then click 流量数据 and harvest funnel metrics."""

    await _safe_fliggy_dataset_text(page, dataset="operating_data")
    await _click_fliggy_booking_period(page, period="昨日")
    await _settle_page(page)
    trade_text = await _safe_fliggy_dataset_text(page, dataset="operating_data")

    await _click_fliggy_flow_data_tab(page)
    await _settle_page(page)
    flow_text = await _safe_fliggy_dataset_text(page, dataset="operating_data")
    funnel = await _harvest_fliggy_flow_funnel_metrics(page)
    flow_block = _format_fliggy_flow_block(flow_text, funnel)
    return f"{trade_text}\n流量数据概览\n{flow_block}".strip()


async def _click_fliggy_flow_data_tab(page: Page) -> bool:
    selectors = [
        "[id$='-tab-flowData']",
        "[role='tab'][aria-controls*='flowData']",
        "div.ant-tabs-tab-btn:has-text('流量数据')",
    ]
    for selector in selectors:
        with suppress(Exception):
            locator = page.locator(selector).first
            if await locator.count() > 0:
                await locator.click(timeout=3_000)
                return True
    with suppress(Exception):
        await page.get_by_role("tab", name="流量数据").click(timeout=3_000)
        return True
    return await _click_fliggy_visible_text(page, "流量数据")


async def _harvest_fliggy_flow_funnel_metrics(page: Page) -> dict[str, str]:
    """Funnel absolute counts live on canvas; hover tooltips expose「指标:数值」."""

    metrics: dict[str, str] = {}
    labels = ("列表页曝光人数", "详情页曝光人数", "线上预订人数")

    # Anchor around conversion-rate labels; funnel bars sit to the right.
    anchor = None
    with suppress(Exception):
        anchor = await page.locator("text=浏览转化率").first.bounding_box()
    if anchor:
        points = [
            (dx, dy)
            for dx in (180, 220, 250, 280, 320)
            for dy in (-30, 0, 30, 60, 90, 120, 150, 180)
        ]
        for dx, dy in points:
            await page.mouse.move(anchor["x"] + dx, anchor["y"] + dy)
            await page.wait_for_timeout(220)
            tips = await page.evaluate(
                """() => Array.from(document.querySelectorAll('.ant-tooltip-inner, [class*="tooltip"]'))
                  .map(el => (el.innerText || '').trim())
                  .filter(Boolean)"""
            )
            for tip in tips or []:
                normalized = str(tip).replace("：", ":").replace("\n", " ")
                for label in labels:
                    if f"{label}:" not in normalized:
                        continue
                    match = re.search(rf"{re.escape(label)}:(\d+(?:\.\d+)?%?)", normalized)
                    if match:
                        metrics[label] = match.group(1)

    with suppress(Exception):
        snapshot = await page.accessibility.snapshot()

        def _walk(node: dict[str, Any] | None) -> None:
            if not node:
                return
            name = str(node.get("name") or "").strip().replace("：", ":")
            for label in labels:
                prefix = f"{label}:"
                if name.startswith(prefix):
                    value = name.split(":", 1)[1].strip()
                    if value and any(ch.isdigit() for ch in value):
                        metrics.setdefault(label, value)
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    _walk(child)

        _walk(snapshot if isinstance(snapshot, dict) else None)

    # Bottom funnel tier often has no tooltip; derive from 详情页 × 预订转化率 when both exist.
    if "线上预订人数" not in metrics:
        detail = metrics.get("详情页曝光人数")
        # rate may still be only in page text; caller merges later.
        pass
    return metrics


def _format_fliggy_flow_block(flow_text: str, funnel: dict[str, str]) -> str:
    lines = _clean_fliggy_text_lines(flow_text)
    chunks: list[str] = []
    merged = dict(funnel)
    rate_labels = ("浏览转化率", "预订转化率")
    for label in rate_labels:
        if label in merged:
            continue
        for index, line in enumerate(lines):
            if line == label and index + 1 < len(lines) and _looks_like_metric_value(lines[index + 1]):
                merged[label] = lines[index + 1]
                break
    if "线上预订人数" not in merged:
        derived = _derive_fliggy_online_bookings(merged)
        if derived is not None:
            merged["线上预订人数"] = derived
    for label in (
        "列表页曝光人数",
        "详情页曝光人数",
        "线上预订人数",
        "浏览转化率",
        "预订转化率",
    ):
        value = merged.get(label)
        if value:
            chunks.extend([label, value])
    if chunks:
        return "\n".join(chunks)
    return "\n".join(lines[:80])


def _derive_fliggy_online_bookings(metrics: dict[str, str]) -> str | None:
    detail_raw = str(metrics.get("详情页曝光人数") or "").strip()
    rate_raw = str(metrics.get("预订转化率") or "").strip()
    if not detail_raw or not rate_raw.endswith("%"):
        return None
    try:
        detail = float(re.sub(r"[^\d.]", "", detail_raw))
        rate = float(rate_raw.rstrip("%")) / 100.0
    except ValueError:
        return None
    if detail <= 0 or rate < 0:
        return None
    return str(int(round(detail * rate)))


async def _capture_fliggy_promotion_text(page: Page) -> str:
    """Click the ant-tabs「已参加活动」tab (registeredList) before reading activity rows."""

    await _safe_fliggy_dataset_text(page, dataset="promotion_agent")
    await _click_fliggy_registered_activities_tab(page)
    await _settle_page(page)
    best = ""
    for _ in range(6):
        text = await _safe_visible_text(page)
        if len(text) > len(best):
            best = text
        lines = _clean_fliggy_text_lines(text)
        if _extract_joined_activities(lines) or (
            "报名时间" in lines and "活动状态" in lines
        ) or ("暂无数据" in lines and "已参加活动" in lines):
            return text
        await _settle_page(page)
    return best or await _safe_fliggy_dataset_text(page, dataset="promotion_agent")


async def _click_fliggy_registered_activities_tab(page: Page) -> bool:
    selectors = [
        "#rc-tabs-0-tab-registeredList",
        "[id$='-tab-registeredList']",
        "[role='tab'][aria-controls*='registeredList']",
        "div.ant-tabs-tab-btn:has-text('已参加活动')",
    ]
    for selector in selectors:
        with suppress(Exception):
            locator = page.locator(selector).first
            if await locator.count() > 0:
                await locator.click(timeout=3_000)
                return True
    with suppress(Exception):
        await page.get_by_role("tab", name="已参加活动").click(timeout=3_000)
        return True
    return await _click_fliggy_visible_text(page, "已参加活动")


async def _click_fliggy_visible_text(page: Page, label: str) -> bool:
    with suppress(Exception):
        locator = page.get_by_text(label, exact=True)
        count = await locator.count()
        for index in range(min(count, 4)):
            candidate = locator.nth(index)
            with suppress(Exception):
                if await candidate.is_visible():
                    await candidate.click(timeout=3_000)
                    return True
    with suppress(Exception):
        locator = page.locator(f"text={label}").first
        await locator.click(timeout=3_000)
        return True
    return False


async def _click_fliggy_booking_period(page: Page, *, period: str) -> bool:
    """Prefer the 昨日/今日 control under 预订数据 (often the second period tab on page)."""

    with suppress(Exception):
        await page.get_by_text("预订数据", exact=True).first.scroll_into_view_if_needed(timeout=3_000)
    with suppress(Exception):
        locator = page.get_by_text(period, exact=True)
        count = await locator.count()
        if count >= 2:
            await locator.nth(1).click(timeout=3_000)
            return True
        if count == 1:
            await locator.first.click(timeout=3_000)
            return True
    return await _click_fliggy_visible_text(page, period)


def _slice_fliggy_home_metric_lines(lines: list[str]) -> list[str]:
    for index in range(len(lines) - 1):
        pair = {lines[index], lines[index + 1]}
        if pair != {"今日数据", "昨日数据"}:
            continue
        start = index + 2
        end = len(lines)
        for cursor in range(start, len(lines)):
            if lines[cursor] in {"流失数据", "待办事项", "诊断建议", "热点日期"}:
                end = cursor
                break
        return lines[start:end]
    return []


_FLIGGY_HOME_METRIC_VALUE_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?元?$")


def _has_fliggy_home_metric_value(lines: list[str]) -> bool:
    """首页指标区出现数值行才算数据渲染完成。

    飞猪首页骨架标签（销售额/浏览人数…）先渲染、值后填充，且早上「今日数据」
    全「暂无」是正常形态、成功页面靠昨日区数值撑起解析（生产快照 477/525 实证）。
    「竞 4/6」是竞争排名展示形态，不算指标值。
    """
    metric_lines = _slice_fliggy_home_metric_lines(lines)
    return any(_FLIGGY_HOME_METRIC_VALUE_RE.match(line) for line in metric_lines)


def _has_fliggy_dataset_signal(text: str, *, dataset: str) -> bool:
    if dataset == "home_v1":
        # 骨架标签（今日数据/销售额…）在值未填充时即出现在文本里，不能作为
        # 放行信号；要求指标切片区内存在真实数值行（2026-09-24 快照 525 根因）。
        return _has_fliggy_home_metric_value(_clean_fliggy_text_lines(text))
    if dataset == "promotion_agent":
        # 「已参加活动」是左侧菜单 tab 名，页面初始 DOM 即存在、与列表区渲染
        # 无关；只认列表区内容信号（表头/状态值/空态确认）。
        markers = ("进行中", "已结束", "暂无数据", "报名时间", "registeredList")
        normalized = "\n".join(_clean_fliggy_text_lines(text))
        return any(marker in normalized for marker in markers)
    normalized = "\n".join(_clean_fliggy_text_lines(text))
    markers_by_dataset = {
        "diagnose_report": ("交易数据概览", "客人点评得分", "MCI酒店竞争力指数", "酒店竞争力指数", "点评得分"),
        "operating_data": (
            "在店数据",
            "预定数据",
            "预订数据",
            "在店间夜",
            "预订间夜",
            "房费收入",
            "流量数据",
            "列表页曝光人数",
            "浏览转化率",
            # 「数据更新时间」随区块渲染出现（T1.6 实测）；作为信号之一可让
            # 等待循环覆盖 SPA 渲染时序，避免首拍文本缺更新时间行而丢今日证据。
            "数据更新时间",
        ),
        "ad_p4p": (
            # 只用内容区标记：子导航本身就有「智能投放」，不能当渲染完成信号。
            "数据总览",
            "广告引导浏览量",
            "广告效果转化概况",
            "还未开通商业化权限",
            "未入驻商业化",
        ),
    }
    markers = markers_by_dataset.get(dataset, ())
    return any(marker in normalized for marker in markers)

def _is_stale_playwright_page_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "target closed" in message or "browser has been closed" in message or "connection closed" in message


def _is_profile_singleton_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "processsingleton" in message
        or "lock file can not be created" in message
        or "profile appears to be in use" in message
        or "user data directory is already in use" in message
    )


def _is_fliggy_navigation_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "navigation" in message or "net::" in message or "timeout" in message


def parse_fliggy_dataset_text(text: str, *, current_url: str, dataset: str) -> dict[str, Any]:
    normalized = _normalize_fliggy_dataset(dataset)
    if normalized == "home_v1":
        return parse_fliggy_home_text(text, current_url=current_url)
    if normalized == "diagnose_report":
        return parse_fliggy_diagnose_text(text, current_url=current_url)
    if normalized == "operating_data":
        return parse_fliggy_operating_text(text, current_url=current_url)
    if normalized == "promotion_agent":
        return parse_fliggy_promotion_text(text, current_url=current_url)
    if normalized == "ad_p4p":
        return parse_fliggy_ad_text(text, current_url=current_url)
    return _fliggy_unknown_dataset_payload(
        dataset=dataset,
        platform_account_id="",
        browser_session_id="",
        current_url=current_url,
    )


def parse_fliggy_home_text(text: str, *, current_url: str) -> dict[str, Any]:
    lines = _clean_fliggy_text_lines(text)
    home_labels = [
        "销售额",
        "浏览人数",
        "预订订单量",
        "预订间夜",
        "订单数",
        "间夜",
        "浏览量",
        "曝光量",
        "转化率",
        "均价",
    ]
    today = _extract_section_label_values(
        lines,
        section_markers=["今日数据"],
        end_markers=["昨日数据", "流失数据", "诊断", "经营"],
        labels=home_labels,
        require_metric_value=True,
    )
    yesterday = _extract_section_label_values(
        lines,
        section_markers=["昨日数据"],
        end_markers=["流失数据", "诊断", "经营", "交易", "今日数据"],
        labels=home_labels,
        require_metric_value=True,
    )
    hotel_name = _infer_fliggy_hotel_name(lines)
    if not today and not yesterday:
        return {
            "status": "needs_review",
            "summary": "飞猪 EBK 首页结构未识别，请人工复核是否已登录或页面已改版。",
            "platform": "fliggy",
            "dataset": "home_v1",
            "data": {
                "hotel_name": hotel_name,
                "today_data": today,
                "yesterday_data": yesterday,
                "visible_lines": _bounded_visible_lines(lines),
            },
            "evidence": _fliggy_dataset_evidence(
                parser=_FLIGGY_HOME_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    return {
        "status": "success",
        "summary": "已读取飞猪 EBK 首页今日/昨日数据。",
        "platform": "fliggy",
        "dataset": "home_v1",
        "data": {
            "hotel_name": hotel_name,
            "today_data": today,
            "yesterday_data": yesterday,
            "visible_lines": _bounded_visible_lines(lines),
        },
        "evidence": _fliggy_dataset_evidence(
            parser=_FLIGGY_HOME_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def parse_fliggy_diagnose_text(text: str, *, current_url: str) -> dict[str, Any]:
    lines = _clean_fliggy_text_lines(text)
    realtime = _extract_section_label_values(
        lines,
        section_markers=["实时数据", "交易数据概览", "交易概览"],
        end_markers=["昨日数据", "客人点评得分", "MCI酒店竞争力指数", "点评得分"],
        labels=["今日在店", "间夜单价", "今日预订", "销售额", "支付订单数", "订单数", "间夜", "转化率", "均价"],
        require_metric_value=True,
    )
    yesterday_trade = _extract_section_label_values(
        lines,
        section_markers=["昨日数据"],
        end_markers=["客人点评得分", "MCI酒店竞争力指数", "点评得分", "实时数据"],
        labels=["在店间夜", "间夜单价", "预订订单", "销售额", "支付订单数", "订单数", "间夜"],
        require_metric_value=True,
    )
    trade = _merge_fliggy_diagnose_trade(realtime, yesterday_trade)
    review_score = _extract_fliggy_review_score(lines)
    review_extras = _extract_fliggy_review_extras(lines)
    mci_index = _numeric_value_after_markers(
        lines,
        ["MCI酒店竞争力指数", "酒店竞争力指数"],
        secondary_labels=["总分值"],
        exact_markers=True,
    )
    if not trade and not review_score and not mci_index:
        return {
            "status": "needs_review",
            "summary": "飞猪诊断报告结构未识别，请人工复核是否已登录或页面已改版。",
            "platform": "fliggy",
            "dataset": "diagnose_report",
            "data": {
                "trade_overview": trade,
                "guest_review_score": review_score,
                "review_extras": review_extras,
                "mci_index": mci_index,
                "visible_lines": _bounded_visible_lines(lines),
            },
            "evidence": _fliggy_dataset_evidence(
                parser=_FLIGGY_DIAGNOSE_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    return {
        "status": "success",
        "summary": "已读取飞猪诊断报告交易/点评/MCI。",
        "platform": "fliggy",
        "dataset": "diagnose_report",
        "data": {
            "trade_overview": trade,
            "guest_review_score": review_score,
            "review_extras": review_extras,
            "mci_index": mci_index,
            "visible_lines": _bounded_visible_lines(lines),
        },
        "evidence": _fliggy_dataset_evidence(
            parser=_FLIGGY_DIAGNOSE_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def _fliggy_operating_today_evidence(lines: list[str]) -> dict[str, Any] | None:
    """T1.6（2026-08-28 实测）：经营数据页每区带「数据更新时间 YYYY/MM/DD HH:MM」。

    「在店/预定」区默认选中「今日」页签（DOM ant-radio-button-wrapper-checked），
    文本层证据为更新时间日期等于当日；存在更新时间且全部为当日时确认
    today_confirmed，否则返回 None（不确认，读取层不放行进实时卡）。
    """

    # 分区绑定：每个「数据更新时间」归属其最近的区头（在店/预定/流量），
    # 流量区可能滞后一日（昨日口径），不应否决在店/预定区的当日判定。
    section_at: list[tuple[str, str]] = []
    current_section = ""
    for line in lines:
        if "在店数据" in line or line == "在店":
            current_section = "在店"
        elif "预订数据" in line or "预定数据" in line or line == "预定":
            current_section = "预定"
        elif "流量数据" in line:
            current_section = "流量"
        elif "数据更新时间" in line:
            time_text = line.split("数据更新时间", 1)[1].strip()
            if time_text:
                section_at.append((current_section, time_text))
    if not section_at:
        return None
    today_text = datetime.now().strftime("%Y/%m/%d")
    update_times = [t for _sec, t in section_at]
    booking_inhouse_today = any(
        sec in {"在店", "预定"} and t.startswith(today_text) for sec, t in section_at
    )
    if not booking_inhouse_today:
        return None
    return {
        "today_confirmed": True,
        "section_update_times": update_times,
        "default_page_tab": "今日",
    }


def parse_fliggy_operating_text(text: str, *, current_url: str) -> dict[str, Any]:
    lines = _clean_fliggy_text_lines(text)
    in_house = _extract_section_label_values(
        lines,
        section_markers=["在店数据", "在店"],
        end_markers=["预定数据", "预订数据", "预定", "预订", "流量数据"],
        labels=["在店间夜", "房费收入", "间夜单价", "在店人数", "间夜", "人数", "入住率"],
        require_metric_value=True,
    )
    booking = _extract_section_label_values(
        lines,
        section_markers=["预定数据", "预订数据"],
        end_markers=["在店数据", "活动", "促销", "近期数据趋势", "流量数据"],
        labels=[
            "预定间夜",
            "预订间夜",
            "预订收入金额",
            "预订单数",
            "预订订单",
            "间夜",
            "订单数",
        ],
        require_metric_value=True,
        allow_zanwu=True,
    )
    if "预订间夜" in booking and "预定间夜" not in booking:
        booking["预定间夜"] = booking["预订间夜"]
    # 「暂无」= 当日暂无预订数据，按用户口径正常显示 0（2026-09-02 运营确认）。
    for _key in list(booking):
        if str(booking[_key]).strip() == "暂无":
            booking[_key] = "0"
    flow = _extract_section_label_values(
        lines,
        section_markers=["流量数据概览", "APP流量转化", "流量数据"],
        end_markers=["营销数据", "促销数据概览", "促销数据"],
        labels=[
            "列表页曝光人数",
            "详情页曝光人数",
            "线上预订人数",
            "浏览转化率",
            "预订转化率",
        ],
        require_metric_value=True,
        prefer_last_section=True,
        exact_section_markers=True,
    )
    section_found = (
        _find_section_index(lines, ["在店数据", "在店"]) is not None
        or _find_section_index(lines, ["预定数据", "预订数据"]) is not None
        or _find_section_index(lines, ["流量数据", "流量数据概览"]) is not None
    )
    today_evidence = _fliggy_operating_today_evidence(lines)
    if not in_house and not booking and not flow and not section_found:
        return {
            "status": "needs_review",
            "summary": "飞猪经营数据结构未识别，请人工复核是否已登录或页面已改版。",
            "platform": "fliggy",
            "dataset": "operating_data",
            "data": {
                "in_house_data": in_house,
                "booking_data": booking,
                "flow_data": flow,
                "visible_lines": _bounded_visible_lines(lines),
            },
            "evidence": _fliggy_dataset_evidence(
                parser=_FLIGGY_OPERATING_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    if not in_house and not booking and not flow:
        return {
            "status": "needs_review",
            "summary": "飞猪经营数据区块已出现但指标为空，请人工复核页面是否已加载完成。",
            "platform": "fliggy",
            "dataset": "operating_data",
            "data": {
                "in_house_data": in_house,
                "booking_data": booking,
                "flow_data": flow,
                "visible_lines": _bounded_visible_lines(lines),
            },
            "evidence": _fliggy_dataset_evidence(
                parser=_FLIGGY_OPERATING_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    return {
        "status": "success",
        "summary": "已读取飞猪经营数据（在店/预定/流量）。",
        "platform": "fliggy",
        "dataset": "operating_data",
        "data": {
            "in_house_data": in_house,
            "booking_data": booking,
            "flow_data": flow,
            "visible_lines": _bounded_visible_lines(lines),
            **({"today_evidence": today_evidence} if today_evidence else {}),
        },
        "evidence": _fliggy_dataset_evidence(
            parser=_FLIGGY_OPERATING_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def parse_fliggy_promotion_text(text: str, *, current_url: str) -> dict[str, Any]:
    lines = _clean_fliggy_text_lines(text)
    activities = _extract_joined_activities(lines)
    section_start = _find_section_index(lines, ["已参加活动", "已参加的活动"])
    section_found = section_start is not None
    empty_confirmed = False
    if section_found:
        nearby = lines[section_start : section_start + 12]
        empty_confirmed = any(token in nearby for token in ("暂无数据", "暂无活动", "没有数据", "暂无"))
    if section_found and (activities or empty_confirmed):
        return {
            "status": "success",
            "summary": (
                "已读取飞猪已参加活动。"
                if activities
                else "飞猪已参加活动为空（页面显示暂无数据）。"
            ),
            "platform": "fliggy",
            "dataset": "promotion_agent",
            "data": {
                "joined_activities": activities,
                "visible_lines": _bounded_visible_lines(lines),
            },
            "evidence": _fliggy_dataset_evidence(
                parser=_FLIGGY_PROMOTION_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    return {
        "status": "needs_review",
        "summary": "飞猪促销活动页结构未识别，请人工复核是否已登录或页面已改版。",
        "platform": "fliggy",
        "dataset": "promotion_agent",
        "data": {
            "joined_activities": [],
            "visible_lines": _bounded_visible_lines(lines),
        },
        "evidence": _fliggy_dataset_evidence(
            parser=_FLIGGY_PROMOTION_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


_FLIGGY_AD_CAMPAIGN_STATUS_VALUES = {
    "暂停",
    "已暂停",
    "投放中",
    "推广中",
    "开启中",
    "运行中",
    "已结束",
}
_FLIGGY_AD_TAB_LINES = {"今日", "昨日", "过去7日", "过去30日", "过去90日"}
_FLIGGY_AD_UNIT_LINES = {"/元", "元"}


def parse_fliggy_ad_text(text: str, *, current_url: str) -> dict[str, Any]:
    """解析付费推广-直通车首页（2026-09-11 实测结构）。

    - 数据总览 / 广告效果转化概况：默认「今日」页签（选中态证据由读取层
      从 DOM 校验后附加到 data.today_evidence）；
    - 智能投放托管卡：当前状态（计划名/状态/累计口径指标）；
    - 未开通商业化权限：页面重定向到申请页，属合法读取结果（activated=False），
      不能按 needs_review 处理，否则未开通店铺的推送会误报部分失败。
    """

    lines = _clean_fliggy_text_lines(text)
    not_activated = any(
        "还未开通商业化权限" in line or "未入驻商业化" in line for line in lines
    )
    overview = _extract_fliggy_ad_overview(lines)
    conversion = _extract_fliggy_ad_conversion(lines)
    campaign = _extract_fliggy_ad_campaign(lines)
    # 开通判定：有智能投放托管计划，或数据总览/转化概况存在非零消耗
    #（覆盖只投「小搜直通车」、无托管计划的店铺）。
    has_nonzero_spend = any(
        _fliggy_ad_positive_number(value)
        for value in (
            overview.get("总花费(元)"),
            overview.get("广告引导订单金额(元)"),
        )
    )
    activated = (bool(campaign.get("name")) or has_nonzero_spend) and not not_activated
    data: dict[str, Any] = {
        "overview": overview,
        "conversion": conversion,
        "campaign": campaign,
        "activated": activated,
        "visible_lines": _bounded_visible_lines(lines),
    }
    if not_activated:
        data["overview"] = {}
        data["conversion"] = {}
        data["campaign"] = {}
        data["activated"] = False
        return {
            "status": "success",
            "summary": "飞猪直通车未开通商业化权限（页面显示申请开通）。",
            "platform": "fliggy",
            "dataset": "ad_p4p",
            "data": data,
            "evidence": _fliggy_dataset_evidence(
                parser=_FLIGGY_AD_P4P_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    if not overview and not conversion and not campaign:
        return {
            "status": "needs_review",
            "summary": "飞猪直通车页面结构未识别，请人工复核是否已开通或页面已改版。",
            "platform": "fliggy",
            "dataset": "ad_p4p",
            "data": data,
            "evidence": _fliggy_dataset_evidence(
                parser=_FLIGGY_AD_P4P_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    return {
        "status": "success",
        "summary": "已读取飞猪直通车广告数据。",
        "platform": "fliggy",
        "dataset": "ad_p4p",
        "data": data,
        "evidence": _fliggy_dataset_evidence(
            parser=_FLIGGY_AD_P4P_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def _fliggy_ad_metric_value_after(
    lines: list[str], index: int, *, max_hops: int = 4
) -> str | None:
    """标签行后的数值：跳过「/元」单位行与日期页签行；非数值即终止。"""

    for candidate in lines[index + 1 : index + 1 + max_hops]:
        text = str(candidate or "").strip()
        if not text or text in _FLIGGY_AD_TAB_LINES or text in _FLIGGY_AD_UNIT_LINES:
            continue
        if _looks_like_metric_value(text):
            return text
        return None
    return None


def _fliggy_ad_raw_value_after(lines: list[str], index: int, *, max_hops: int = 2) -> str | None:
    """标签行后的原文值（如开始时间区间），只跳过空行。"""

    for candidate in lines[index + 1 : index + 1 + max_hops]:
        text = str(candidate or "").strip()
        if not text:
            continue
        return text
    return None


def _fliggy_ad_positive_number(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        return float(text.replace(",", "")) > 0
    except ValueError:
        return False


def _fliggy_ad_section(lines: list[str], start_markers: list[str], end_markers: list[str]):
    start = _find_section_index(lines, start_markers)
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if any(marker in lines[index] for marker in end_markers):
            end = index
            break
    return lines[start + 1 : end]


def _extract_fliggy_ad_overview(lines: list[str]) -> dict[str, str]:
    section = _fliggy_ad_section(
        lines, ["数据总览"], ["过去7日ROI趋势", "去充值", "广告效果转化概况"]
    )
    if section is None:
        return {}
    values: dict[str, str] = {}
    label_map = {
        "总花费": "总花费(元)",
        "广告引导浏览量": "广告引导浏览量",
        "广告引导订单金额": "广告引导订单金额(元)",
        "ROI": "ROI",
        "间夜量": "间夜量",
    }
    for index, line in enumerate(section[:-1]):
        label = line.strip()
        if label not in label_map:
            continue
        value = _fliggy_ad_metric_value_after(section, index)
        if value is not None:
            values[label_map[label]] = value
    return values


def _extract_fliggy_ad_conversion(lines: list[str]) -> dict[str, str]:
    start = next(
        (i for i, line in enumerate(lines) if "广告效果转化概况" in line), None
    )
    if start is None:
        return {}
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == "生意诊断"), len(lines))
    section = lines[start + 1 : end]
    labels = ("浏览量", "点击量", "购买量", "点击率", "转化率")
    values: dict[str, str] = {}
    for index, line in enumerate(section[:-1]):
        label = line.strip()
        if label not in labels:
            continue
        value = _fliggy_ad_metric_value_after(section, index)
        if value is not None:
            values[label] = value
    return values


def _extract_fliggy_ad_campaign(lines: list[str]) -> dict[str, str]:
    anchor = next((i for i, line in enumerate(lines) if line.strip() == "托管配置"), None)
    if anchor is None:
        return {}
    end = next(
        (
            i
            for i in range(anchor + 1, len(lines))
            if lines[i].strip() in {"商圈榜单排名", "今日待办"}
        ),
        len(lines),
    )
    section = lines[anchor + 1 : end]
    tab_index = next(
        (i for i, line in enumerate(section[:-1]) if line.strip() == "智能投放"), None
    )
    if tab_index is None:
        return {}
    name = section[tab_index + 1].strip()
    if not name or name in _FLIGGY_AD_CAMPAIGN_STATUS_VALUES or name in {"修改", "小搜直通车"}:
        return {}
    campaign: dict[str, str] = {"name": name}
    status = section[tab_index + 2].strip() if tab_index + 2 < len(section) else ""
    if status in _FLIGGY_AD_CAMPAIGN_STATUS_VALUES:
        campaign["status"] = status
    label_map = {
        "今日预估花费": "today_estimated_spend",
        "累计花费": "total_spend",
        "累计浏览量": "total_views",
        "累计引导间夜数": "total_room_nights",
    }
    for index, line in enumerate(section[:-1]):
        label = line.strip()
        key = next(
            (k for k in label_map if label == k or label.startswith(k)), None
        )
        if key is None or label_map[key] in campaign:
            continue
        if label == "开始时间":
            continue
        value = _fliggy_ad_metric_value_after(section, index)
        if value is not None:
            campaign[label_map[key]] = value
    start_time_index = next(
        (i for i, line in enumerate(section[:-1]) if line.strip() == "开始时间"), None
    )
    if start_time_index is not None:
        start_time = _fliggy_ad_raw_value_after(section, start_time_index)
        if start_time:
            campaign["start_time"] = start_time
    return campaign


async def _fliggy_ad_checked_tabs(page: Page) -> list[str]:
    with suppress(Exception):
        checked = await page.evaluate(
            """() => Array.from(
                document.querySelectorAll('.ant-radio-button-wrapper-checked')
            ).map(node => (node.textContent || '').trim()).filter(Boolean)"""
        )
        if isinstance(checked, list):
            return [str(item) for item in checked]
    return []


def _normalize_fliggy_dataset(dataset: str) -> str:
    value = str(dataset or "").strip().lower()
    aliases = {
        "home": "home_v1",
        "homev1": "home_v1",
        "diagnose": "diagnose_report",
        "operating": "operating_data",
        "promotion": "promotion_agent",
        "promotions": "promotion_agent",
    }
    return aliases.get(value, value)


def _fliggy_unknown_dataset_payload(
    *,
    dataset: str,
    platform_account_id: str,
    browser_session_id: str,
    current_url: str = "",
) -> dict[str, Any]:
    return {
        "status": "needs_review",
        "summary": "不支持的飞猪 EBK 数据集，请先在后端适配。",
        "platform": "fliggy",
        "dataset": dataset,
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {},
        "evidence": _fliggy_dataset_evidence(
            parser="fliggy_unknown",
            current_url=current_url,
            line_count=0,
        ),
    }


def _fliggy_human_required_dataset_payload(
    *,
    dataset: str,
    platform_account_id: str,
    browser_session_id: str,
    current_url: str,
    page_state: str,
) -> dict[str, Any]:
    return {
        "status": "human_required",
        "summary": "飞猪 EBK 需要人工登录或验证后才能读取。",
        "platform": "fliggy",
        "dataset": dataset,
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "manual_login_required": True,
        "data": {},
        "evidence": {
            **_fliggy_dataset_evidence(
                parser=_parser_name_for_fliggy_dataset(dataset),
                current_url=current_url,
                line_count=0,
            ),
            "page_state": page_state,
        },
        "next_action": "请先在飞猪 EBK 浏览器窗口完成人工登录或安全验证。",
    }


def _parser_name_for_fliggy_dataset(dataset: str) -> str:
    return {
        "home_v1": _FLIGGY_HOME_PARSER,
        "diagnose_report": _FLIGGY_DIAGNOSE_PARSER,
        "operating_data": _FLIGGY_OPERATING_PARSER,
        "promotion_agent": _FLIGGY_PROMOTION_PARSER,
        "ad_p4p": _FLIGGY_AD_P4P_PARSER,
    }.get(_normalize_fliggy_dataset(dataset), "fliggy_unknown")


def _fliggy_dataset_evidence(*, parser: str, current_url: str, line_count: int) -> dict[str, Any]:
    return {
        "parser": parser,
        "current_url": current_url,
        "line_count": line_count,
        "profile_path_returned": False,
        "password_returned": False,
        "password_stored": False,
        "automation_bypass": False,
    }


def _clean_fliggy_text_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = " ".join(raw.strip().split())
        if line:
            lines.append(line)
    return lines


def _bounded_visible_lines(lines: list[str], *, limit: int = 160) -> list[str]:
    return lines[:limit]


def _infer_fliggy_hotel_name(lines: list[str]) -> str | None:
    skip = {"飞猪酒店", "今日数据", "昨日数据", "诊断报告", "经营数据", "促销活动"}
    for line in lines[:20]:
        if line in skip:
            continue
        if "酒店" in line or "宾馆" in line or "客栈" in line:
            return line
    return None


def _extract_section_label_values(
    lines: list[str],
    *,
    section_markers: list[str],
    end_markers: list[str],
    labels: list[str],
    require_metric_value: bool = False,
    prefer_last_section: bool = False,
    exact_section_markers: bool = False,
    allow_zanwu: bool = False,
) -> dict[str, str]:
    start = _find_section_index(
        lines,
        section_markers,
        prefer_last=prefer_last_section,
        exact_only=exact_section_markers,
    )
    if start is None:
        return {}
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if any(marker == lines[index] or marker in lines[index] for marker in end_markers):
            if index == start:
                continue
            if lines[index] in section_markers:
                continue
            end = index
            break
    section = lines[start + 1 : end]
    values = _extract_known_label_values(section, labels)
    if require_metric_value:
        values = {
            key: value
            for key, value in values.items()
            if _looks_like_metric_value(value)
            or (allow_zanwu and str(value).strip() == "暂无")
        }
    return values


def _find_section_index(
    lines: list[str],
    markers: list[str],
    *,
    prefer_last: bool = False,
    exact_only: bool = False,
) -> int | None:
    matches: list[int] = []
    for index, line in enumerate(lines):
        if line in markers:
            matches.append(index)
            continue
        if exact_only:
            continue
        for marker in markers:
            if marker and marker in line and len(line) <= len(marker) + 8:
                matches.append(index)
                break
    if not matches:
        return None
    return matches[-1] if prefer_last else matches[0]

def _extract_known_label_values(lines: list[str], labels: list[str]) -> dict[str, str]:
    label_set = set(labels)
    values: dict[str, str] = {}
    for index, line in enumerate(lines[:-1]):
        if line not in label_set:
            continue
        value = _next_non_label_value(lines, index + 1, label_set)
        if value is not None and not _is_fliggy_shell_chrome(value):
            values[line] = value
    return values


def _next_non_label_value(
    lines: list[str],
    start_index: int,
    labels: set[str],
    *,
    max_hops: int = 2,
) -> str | None:
    for candidate in lines[start_index : start_index + max_hops]:
        if candidate in labels:
            return None
        if candidate.strip():
            return candidate
    return None


def _numeric_value_after_markers(
    lines: list[str],
    markers: list[str],
    *,
    secondary_labels: list[str] | None = None,
    exact_markers: bool = False,
) -> str | None:
    secondary = list(secondary_labels or [])
    match_indexes: list[int] = []
    for index, line in enumerate(lines):
        for marker in markers:
            if line == marker or (not exact_markers and line.startswith(marker) and len(line) <= len(marker) + 2):
                match_indexes.append(index)
                break
    # Prefer exact marker lines first.
    ordered = sorted(
        match_indexes,
        key=lambda index: (0 if lines[index] in markers else 1, index),
    )
    for index in ordered:
        window = lines[index : index + 10]
        for offset, candidate_line in enumerate(window):
            if candidate_line in secondary and offset + 1 < len(window):
                secondary_value = window[offset + 1]
                if _looks_like_metric_value(secondary_value):
                    return secondary_value
            candidate: str | None = None
            if "：" in candidate_line or ":" in candidate_line:
                parts = candidate_line.replace("：", ":").split(":", 1)
                if len(parts) == 2 and parts[1].strip():
                    candidate = parts[1].strip()
            elif offset > 0:
                candidate = candidate_line
            if candidate and _looks_like_metric_value(candidate):
                return candidate
    return None


def _extract_fliggy_review_score(lines: list[str]) -> str | None:
    # Prefer dedicated「点评得分」label under the real review section.
    for index, line in enumerate(lines):
        if line != "点评得分":
            continue
        if index + 1 >= len(lines):
            continue
        candidate = lines[index + 1]
        if _looks_like_review_score(candidate):
            return candidate
    # Fallback: exact section title only (ignore advice like「客人点评得分低于4.9分」).
    value = _numeric_value_after_markers(
        lines,
        ["客人点评得分"],
        secondary_labels=["点评得分"],
        exact_markers=True,
    )
    if value and _looks_like_review_score(value):
        return value
    return None


def _extract_fliggy_review_extras(lines: list[str]) -> dict[str, str]:
    extras: dict[str, str] = {}
    start = None
    for index, line in enumerate(lines):
        if line == "客人点评得分":
            start = index
            break
    if start is None:
        return extras
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index] in {"MCI酒店竞争力指数", "酒店竞争力指数", "交易数据概览", "APP流量转化"}:
            end = index
            break
    for line in lines[start:end]:
        normalized = line.replace("：", ":")
        if ":" not in normalized:
            continue
        key, value = normalized.split(":", 1)
        key = re.sub(r"\s+", "", key)
        value = value.strip()
        if not key or not value:
            continue
        if key.startswith("新增") or "评价" in key or "差评" in key:
            extras[key] = value
    return extras


def _looks_like_review_score(value: str) -> bool:
    text = str(value or "").strip()
    if not text or _is_fliggy_non_metric_value(text):
        return False
    if _is_datetime_like(text):
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?分?", text))


def _looks_like_metric_value(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _is_fliggy_shell_chrome(text):
        return False
    if _is_fliggy_non_metric_value(text):
        return False
    if _is_datetime_like(text):
        return False
    placeholders = {"无", "-", "--", "—", "N/A", "n/a", "null", "None", "-%"}
    if text in placeholders:
        return False
    return any(ch.isdigit() for ch in text)


def _is_datetime_like(value: str) -> bool:
    text = str(value or "").strip()
    if re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", text):
        return True
    if re.search(r"\d{1,2}:\d{2}", text) and ("/" in text or "-" in text or "更新" in text):
        return True
    return False


def _is_fliggy_non_metric_value(value: str) -> bool:
    text = str(value or "").strip()
    if text in {"差", "好", "优", "中", "更多详情", "实时数据", "日报", "周报"}:
        return True
    prefixes = (
        "上周同期",
        "周环比",
        "暂无同行",
        "暂无对比",
        "暂无同期",
        "更新时间",
        "新增",
        "数据更新时间",
    )
    return any(text.startswith(prefix) for prefix in prefixes)


def _merge_fliggy_diagnose_trade(
    realtime: dict[str, str],
    yesterday_trade: dict[str, str],
) -> dict[str, str]:
    trade: dict[str, str] = {}
    for key, value in realtime.items():
        if key == "间夜单价":
            trade["今日间夜单价"] = value
        else:
            trade[key] = value
    for key, value in yesterday_trade.items():
        if key == "间夜单价":
            trade["昨日间夜单价"] = value
        elif key == "在店间夜":
            trade["昨日在店间夜"] = value
        elif key == "预订订单":
            trade["昨日预订订单"] = value
        else:
            trade[f"昨日{key}"] = value
    return trade


def _is_fliggy_shell_chrome(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    chrome = {
        "通知",
        "商家学堂",
        "联系飞猪",
        "任务中心",
        "99+",
        "首页",
        "数据中心",
        "促销",
        "营销",
        "订单",
        "房态",
        "退出",
        "设置",
        "帮助",
        "飞猪酒店",
        "EBK",
        "工作台",
    }
    if text in chrome:
        return True
    if text.endswith("地铁站店）") or text.endswith("地铁站店)"):
        # Hotel name in chrome header is not a metric/activity value.
        return "酒店" in text or "公寓" in text
    return False


def _extract_joined_activities(lines: list[str]) -> list[dict[str, str]]:
    start = _find_section_index(lines, ["已参加活动", "已参加的活动"])
    if start is None:
        return []
    body = lines[start + 1 :]
    status_tokens = {"进行中", "已结束", "未开始", "已报名", "生效中", "已下线"}
    skip = {
        "已参加活动",
        "已参加的活动",
        "已参加",
        "促销活动",
        "活动列表",
        "活动报名",
        "全部",
        "进行中",
        "已结束",
        "查 询",
        "查询",
        "查看",
        "取消",
        "日志",
        "操作",
        "报名时间",
        "最后修改时间",
        "活动状态",
        "活动类型:",
        "活动状态:",
        "暂无数据",
        "暂无活动",
        *_FLIGGY_SHELL_CHROME_LINES,
    }
    activities: list[dict[str, str]] = []
    seen: set[str] = set()

    for index, line in enumerate(body):
        status = _status_token_from_line(line, status_tokens)
        if status is None:
            continue
        name_index: int | None = None
        if line.strip() in status_tokens:
            if index == 0:
                continue
            name = _activity_name_cell(body[index - 1])
            if _is_fliggy_activity_name(name, skip=skip, status_tokens=status_tokens):
                name_index = index - 1
            else:
                # 2026-09-01 实测：营销助手卡片版式里名称与状态之间隔着
                # 描述行（名称/描述/已报名），需向上回溯寻找活动名。
                name_index = _find_fliggy_activity_name_index_before(
                    body,
                    index,
                    skip=skip,
                    status_tokens=status_tokens,
                )
                if name_index is None:
                    continue
                name = _activity_name_cell(body[name_index])
        else:
            name_index = _find_fliggy_activity_name_index_before(
                body,
                index,
                skip=skip,
                status_tokens=status_tokens,
            )
            if name_index is None:
                continue
            name = _activity_name_cell(body[name_index])
        if name in seen:
            continue
        seen.add(name)
        activity = _build_fliggy_activity_row(
            body,
            name_index=name_index,
            name=name,
            status=status,
        )
        activities.append(activity)
        if len(activities) >= 20:
            break
    return activities


def _build_fliggy_activity_row(
    body: list[str],
    *,
    name_index: int,
    name: str,
    status: str,
) -> dict[str, str]:
    row = body[name_index]
    cells = [cell.strip() for cell in row.replace("\t", " ").split(" ") if cell.strip()]
    # Normalized whitespace rows look like: 分场景运营活动 39个 2026-05-30至2027-05-29
    room_product = cells[1] if len(cells) >= 2 else ""
    valid_period = cells[2] if len(cells) >= 3 else ""
    strength = ""
    description = ""
    for cursor in range(name_index + 1, min(name_index + 4, len(body))):
        candidate = body[cursor].strip()
        if not candidate or candidate in {"查看", "取消", "日志"}:
            continue
        if _status_token_from_line(candidate, {"进行中", "已结束", "未开始", "已报名", "生效中", "已下线"}):
            break
        if candidate.startswith("投放"):
            continue
        if not description and len(candidate) >= 8 and "%" not in candidate:
            # 2026-09-02 需求：营销助手卡片版式的描述行（面向...）作为活动内容
            description = candidate
            continue
        if "立减" in candidate or "活动" in candidate or "至" in candidate:
            strength = candidate
            break
    return {
        "name": name,
        "activity_type": name,
        "room_product": room_product,
        "valid_period": valid_period,
        "strength": strength,
        "description": description,
        "status": status,
    }


def _activity_name_cell(line: str) -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    if "\t" in text:
        text = text.split("\t")[0].strip()
    if text.startswith(("活动类型", "活动状态", "活动有效", "活动力度", "活动内容")):
        return ""
    return text.split(" ")[0].strip()


def _status_token_from_line(line: str, status_tokens: set[str]) -> str | None:
    text = str(line or "").strip()
    if text in status_tokens:
        return text
    if "\t" in text:
        last = text.split("\t")[-1].strip()
        if last in status_tokens:
            return last
    parts = text.split()
    if parts and parts[-1] in status_tokens:
        return parts[-1]
    for token in status_tokens:
        if text.endswith(token) and len(text) > len(token):
            prefix = text[: -len(token)].rstrip(" \t:：")
            if prefix:
                return token
    return None


def _find_fliggy_activity_name_index_before(
    body: list[str],
    status_index: int,
    *,
    skip: set[str],
    status_tokens: set[str],
) -> int | None:
    for cursor in range(status_index - 1, max(-1, status_index - 8), -1):
        candidate = _activity_name_cell(body[cursor])
        if _is_fliggy_activity_name(candidate, skip=skip, status_tokens=status_tokens):
            return cursor
    return None


def _find_fliggy_activity_name_before(
    body: list[str],
    status_index: int,
    *,
    skip: set[str],
    status_tokens: set[str],
) -> str | None:
    index = _find_fliggy_activity_name_index_before(
        body,
        status_index,
        skip=skip,
        status_tokens=status_tokens,
    )
    if index is None:
        return None
    return _activity_name_cell(body[index])


def _is_fliggy_activity_name(
    value: str,
    *,
    skip: set[str],
    status_tokens: set[str],
) -> bool:
    text = str(value or "").strip()
    if not text or text in skip or text in status_tokens:
        return False
    if text.endswith(":") or text.endswith("："):
        return False
    if _is_fliggy_shell_chrome(text):
        return False
    if text.startswith("投放") or text.startswith("立减"):
        return False
    if len(text) >= 4 and text[:4].isdigit() and "至" in text:
        return False
    if "活动类型" in text or "活动有效期" in text or "活动力度" in text:
        return False
    if "活动" in text and len(text) <= 40:
        return True
    return any(token in text for token in ("特惠", "促销", "优惠", "连住", "满减", "爆款"))


_FLIGGY_SHELL_CHROME_LINES = frozenset(
    {
        "通知",
        "商家学堂",
        "联系飞猪",
        "任务中心",
        "99+",
        "首页",
        "数据中心",
        "促销",
        "营销",
        "订单",
        "房态",
        "退出",
        "设置",
        "帮助",
        "飞猪酒店",
        "工作台",
    }
)


_FLIGGY_SERVICE: FliggyEbookingBrowserService | None = None


def get_fliggy_ebooking_service() -> FliggyEbookingBrowserService:
    global _FLIGGY_SERVICE
    if _FLIGGY_SERVICE is None:
        _FLIGGY_SERVICE = FliggyEbookingBrowserService()
    return _FLIGGY_SERVICE
